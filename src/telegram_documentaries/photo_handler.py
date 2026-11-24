"""Photo intake and routing: a Telegram photo update in, one reply out.

The pure :func:`parse_photo_update` turns a raw update into a :class:`PhotoRef`
(largest photo size, typed ``ChatId``) without touching the network. The
:class:`PhotoGate` handles a photo according to the chat's phase:

- ``AWAITING_PHOTO``: the Bouncer classifies it. A pass starts the interview and
  sends the first question; a fail sends the in-character rejection.
- ``INTERVIEWING`` or ``DOSSIER_READY``: a nudge, with no download and no
  Bouncer call.

It never lets an exception escape to the polling loop. Downloading and replying
are injected seams, so tests stay offline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from pydantic import ValidationError
from telegram import PhotoSize, Update

from telegram_documentaries.bouncer import inspect as bouncer_inspect
from telegram_documentaries.errors import StaleResult, VisionError
from telegram_documentaries.gateway import ReplySender, send_reply
from telegram_documentaries.interview import start_interview
from telegram_documentaries.interview_client import InterviewClient
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    EMPTY_PHOTO_TEXT,
    ONE_AT_A_TIME_TEXT,
    OVERSIZE_PHOTO_TEXT,
    PHOTO_MID_INTERVIEW_TEXT,
    REJECTION_TEMPLATE,
    RESEND_TEXT,
    render_subject,
)
from telegram_documentaries.models import (
    MAX_PHOTO_BYTES,
    ChatId,
    PhotoPayload,
    as_chat_id,
)
from telegram_documentaries.state import Phase, SessionStore
from telegram_documentaries.vision_client import VisionClient

logger = logging.getLogger(__name__)

#: Fetches the raw bytes for a Telegram file id. Injected so tests never download.
Downloader: TypeAlias = Callable[[str], Awaitable[bytes]]


class _DownloadableFile(Protocol):
    async def download_as_bytearray(self) -> bytearray: ...


class TelegramFileSource(Protocol):
    """The slice of ``telegram.Bot`` needed to fetch a photo's bytes."""

    async def get_file(self, file_id: str) -> _DownloadableFile: ...


@dataclass(frozen=True)
class PhotoRef:
    """A routable photo: where it came from and which size to fetch."""

    chat_id: ChatId
    file_id: str
    file_size: int | None
    caption: str | None


def select_largest_photo(photos: Sequence[PhotoSize]) -> PhotoSize:
    """Pick the largest available size; the last listed size wins a tie.

    Telegram lists sizes smallest to largest, so reversing before ``max`` makes
    the last-listed entry win when two sizes report the same ``file_size``.
    """
    return max(reversed(photos), key=lambda photo: photo.file_size or 0)


def parse_photo_update(update: Update) -> PhotoRef | None:
    """Return the routable photo in ``update``, or ``None`` if there is none.

    Updates without a message, without photos, without a chat, or with a
    non-positive chat id are not routable and are ignored without raising.
    """
    message = update.message
    chat = update.effective_chat
    if message is None or not message.photo or chat is None:
        return None
    try:
        chat_id = as_chat_id(chat.id)
    except ValueError:
        return None
    largest = select_largest_photo(message.photo)
    return PhotoRef(
        chat_id=chat_id,
        file_id=largest.file_id,
        file_size=largest.file_size,
        caption=message.caption,
    )


def telegram_downloader(bot: TelegramFileSource) -> Downloader:
    """Build the production :data:`Downloader` backed by the Telegram Bot API."""

    async def download(file_id: str) -> bytes:
        telegram_file = await bot.get_file(file_id)
        return bytes(await telegram_file.download_as_bytearray())

    return download


#: Nudge for a photo in a phase that already has a photo or a dossier.
_PHOTO_NUDGE_BY_PHASE = {
    Phase.INTERVIEWING: (PHOTO_MID_INTERVIEW_TEXT, "mid_interview"),
}


class PhotoGate:
    """Route one photo update by phase and send exactly one reply.

    A chat is busy while a photo is being handled or a model call is in flight.
    Any photo that arrives then gets a "one at a time" nudge; it is never
    downloaded or classified, so it cannot corrupt that chat's state.
    """

    def __init__(
        self,
        *,
        client: VisionClient,
        interview: InterviewClient,
        store: SessionStore,
        download: Downloader,
        reply: ReplySender = send_reply,
    ) -> None:
        self._client = client
        self._interview = interview
        self._store = store
        self._download = download
        self._reply = reply
        self._in_flight: set[ChatId] = set()

    def is_busy(self, chat_id: ChatId) -> bool:
        """Whether a photo or a model call is already in progress for ``chat_id``."""
        return chat_id in self._in_flight or self._store.get(chat_id).in_flight

    @log_stage
    async def handle(self, update: Update) -> None:
        """Route one update. Errors are logged and never propagate."""
        ref = parse_photo_update(update)
        if ref is None:
            logger.debug("photo_update_ignored reason=not_a_routable_photo")
            return
        try:
            if self.is_busy(ref.chat_id):
                # Answered without a download or a model call, so whatever is
                # already in flight for this chat is unaffected.
                await self._nudge(update, ref, ONE_AT_A_TIME_TEXT, "in_flight")
                return
            self._in_flight.add(ref.chat_id)
            try:
                await self._process(update, ref)
            finally:
                self._in_flight.discard(ref.chat_id)
        except StaleResult:
            # A /restart reset the chat while the photo was being checked. The reset
            # has already replied, so the late result is dropped without a reply.
            logger.info("photo_result_dropped reason=stale_run chat_id=%s", ref.chat_id)
        except Exception as exc:  # one bad update must never stop the poller
            logger.error(
                "photo_handler_error error_type=%s chat_id=%s",
                type(exc).__name__,
                ref.chat_id,
            )

    async def _process(self, update: Update, ref: PhotoRef) -> None:
        """Route one routable photo by the chat's phase, then reply once."""
        phase = self._store.get(ref.chat_id).phase
        if phase is not Phase.AWAITING_PHOTO:
            text, reason = _PHOTO_NUDGE_BY_PHASE.get(
                phase, (ALREADY_DONE_TEXT, "already_done")
            )
            await self._nudge(update, ref, text, reason)
            return

        if ref.file_size is not None and ref.file_size > MAX_PHOTO_BYTES:
            await self._nudge(update, ref, OVERSIZE_PHOTO_TEXT, "oversize")
            return

        try:
            data = await self._download(ref.file_id)
        except Exception as exc:  # download failure is recoverable for the user
            logger.error(
                "photo_download_failed error_type=%s chat_id=%s",
                type(exc).__name__,
                ref.chat_id,
            )
            await self._nudge(update, ref, RESEND_TEXT, "download_failed")
            return

        try:
            photo = PhotoPayload(
                chat_id=ref.chat_id,
                file_id=ref.file_id,
                photo_bytes=data,
                caption=ref.caption,
            )
        except ValidationError:
            if len(data) > MAX_PHOTO_BYTES:
                await self._nudge(update, ref, OVERSIZE_PHOTO_TEXT, "oversize")
            else:
                await self._nudge(update, ref, EMPTY_PHOTO_TEXT, "malformed")
            return

        epoch = self._store.epoch(ref.chat_id)
        try:
            verdict = await bouncer_inspect(photo, self._client)
            self._store.require_current(ref.chat_id, epoch)  # /restart may have landed
            if not verdict.contains_human:
                text = REJECTION_TEMPLATE.format(
                    subject=render_subject(verdict.detected_subject)
                )
                await self._reply(update, text)
                return
            # The photo is recorded only when its first question is committed.
            started = await start_interview(
                ref.chat_id, ref.file_id, self._interview, self._store
            )
        except VisionError:
            # The failing stage already logged the typed error; state is untouched.
            if self._store.epoch(ref.chat_id) != epoch:
                return  # a /restart already replied; this failure is stale
            await self._nudge(update, ref, RESEND_TEXT, "vision_error")
            return

        await self._reply(update, started.text)

    async def _nudge(
        self, update: Update, ref: PhotoRef, text: str, reason: str
    ) -> None:
        """Send a gentle nudge; no state is changed on this path."""
        logger.info("photo_nudged reason=%s chat_id=%s", reason, ref.chat_id)
        await self._reply(update, text)
