"""Update routing: one entry point per payload type, dispatched by phase.

:class:`UpdateRouter` is the only place that maps the interview's typed errors to
user-facing copy. Every entry point is wrapped so that no exception escapes to the
polling loop; an unexpected failure is logged at ``error`` and the next update is
still served. Text and photos check the in-flight guard, so any update for a chat
with work in progress gets the one-at-a-time nudge and changes nothing. ``/restart``
is the one exception: it always resets at once (D3), and any model result that
lands afterwards is dropped.
"""

from __future__ import annotations

import logging

from telegram import Update

from telegram_documentaries.converter_client import PortraitClient
from telegram_documentaries.errors import StaleResult, VisionError
from telegram_documentaries.gateway import ReplySender, build_reply, send_reply
from telegram_documentaries.generation import PhotoSender, run_generation
from telegram_documentaries.interview import answer_question, restart
from telegram_documentaries.interview_client import InterviewClient
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    ONE_AT_A_TIME_TEXT,
    RESEND_ANSWER_TEXT,
)
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.photo_handler import Downloader, PhotoGate
from telegram_documentaries.state import Phase, SessionStore
from telegram_documentaries.vision_client import VisionClient

logger = logging.getLogger(__name__)


def _chat_of(update: Update) -> ChatId | None:
    """Return the typed chat id of ``update``, or ``None`` if it has none."""
    chat = update.effective_chat
    if chat is None:
        return None
    try:
        return as_chat_id(chat.id)
    except ValueError:
        return None


class UpdateRouter:
    """Route text, photo, and ``/restart`` updates by the chat's current phase."""

    def __init__(
        self,
        *,
        vision: VisionClient,
        interview: InterviewClient,
        store: SessionStore,
        download: Downloader,
        portraits: PortraitClient,
        send_photo: PhotoSender,
        reply: ReplySender = send_reply,
    ) -> None:
        self._interview = interview
        self._store = store
        self._download = download
        self._reply = reply
        self._portraits = portraits
        self._send_photo = send_photo
        self._photos = PhotoGate(
            client=vision,
            interview=interview,
            store=store,
            download=download,
            reply=reply,
        )

    def _is_busy(self, chat_id: ChatId) -> bool:
        """A chat is busy while a photo or any model call is in progress."""
        return self._photos.is_busy(chat_id) or self._store.get(chat_id).in_flight

    async def _generate(self, update: Update, chat_id: ChatId) -> None:
        """Run the portrait for a chat that has just finished its dossier.

        The dossier text has already been sent. Only a failure produces a reply
        here: the portrait itself is the reply when it is delivered.
        """
        text = await run_generation(
            chat_id, self._store, self._download, self._portraits, self._send_photo
        )
        if text is not None:
            await self._reply(update, text)

    async def handle_photo(self, update: Update) -> None:
        """Route a photo update. The photo gate handles its own errors."""
        await self._photos.handle(update)

    async def handle_text(self, update: Update) -> None:
        """Route a text update: the phase-one reply, an answer, or a nudge."""
        chat_id = _chat_of(update)
        message = update.message
        if chat_id is None or message is None or message.text is None:
            logger.debug("text_update_ignored reason=no_routable_text")
            return
        epoch = self._store.epoch(chat_id)
        try:
            if self._is_busy(chat_id):
                await self._reply(update, ONE_AT_A_TIME_TEXT)
                return
            phase = self._store.get(chat_id).phase
            if phase is Phase.INTERVIEWING:
                reply = await answer_question(
                    chat_id, message.text, self._interview, self._store
                )
                await self._reply(update, reply.text)
                if reply.phase is Phase.DOSSIER_READY:
                    # D12: the dossier text is sent first; the portrait follows.
                    await self._generate(update, chat_id)
            elif phase is Phase.AWAITING_PHOTO:
                phase_one_reply = build_reply(update)
                if phase_one_reply is not None:
                    await self._reply(update, phase_one_reply)
            else:
                await self._reply(update, ALREADY_DONE_TEXT)
        except StaleResult:
            # A /restart reset the chat while the answer was with the model. The
            # reset has already replied, so nothing more is sent.
            return
        except VisionError as exc:
            # The interview stage already logged the typed error; the answer is
            # not committed, so the user can resend it.
            if self._store.epoch(chat_id) != epoch:
                return  # a /restart already replied; this failure is stale
            logger.error(
                "interview_turn_failed error_type=%s chat_id=%s",
                type(exc).__name__,
                chat_id,
            )
            await self._reply(update, RESEND_ANSWER_TEXT)
        except Exception as exc:  # one bad update must never stop the poller
            logger.error(
                "router_error handler=text error_type=%s chat_id=%s",
                type(exc).__name__,
                chat_id,
            )

    async def handle_restart(self, update: Update) -> None:
        """Handle ``/restart``: clear the chat and send the opening prompt."""
        chat_id = _chat_of(update)
        if chat_id is None:
            logger.debug("restart_ignored reason=no_routable_chat")
            return
        try:
            # No in-flight check: /restart always resets at once (D3). Any model
            # result that lands later is dropped by its own stage.
            reply = restart(chat_id, self._store)
            await self._reply(update, reply.text)
        except Exception as exc:  # one bad update must never stop the poller
            logger.error(
                "router_error handler=restart error_type=%s chat_id=%s",
                type(exc).__name__,
                chat_id,
            )
