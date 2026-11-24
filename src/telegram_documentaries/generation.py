"""Generation orchestrator: from a finished dossier to a delivered portrait.

:func:`run_generation` drives one portrait run for a chat in ``DOSSIER_READY``:
it starts the run, downloads the original photo, asks the converter for the
portrait, and then sends it. Every session change goes through the
:class:`SessionStore` driver, and the run token guards each step, so a result
that arrives after ``/restart`` can never reach the chat.

The function returns the text to reply with (an apology), or ``None`` when the
photo itself is the reply or when the run was dropped. Routing sends the text.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol, TypeAlias

from telegram_documentaries.converter import generate_portrait
from telegram_documentaries.converter_client import PortraitClient
from telegram_documentaries.errors import StaleResult, VisionError
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.messages import (
    GENERATION_FAILED_TEXT,
    ONE_AT_A_TIME_TEXT,
    render_caption,
)
from telegram_documentaries.models import ChatId
from telegram_documentaries.photo_handler import Downloader
from telegram_documentaries.state import Phase, SessionStore, StateError

logger = logging.getLogger(__name__)

#: Sends portrait bytes and a caption to a chat. Injected, so tests never touch
#: Telegram.
PhotoSender: TypeAlias = Callable[[ChatId, bytes, str], Awaitable[None]]


class _PhotoBot(Protocol):
    """The slice of ``telegram.Bot`` needed to send a photo."""

    async def send_photo(
        self, chat_id: int, photo: BytesIO, caption: str
    ) -> object: ...


@log_stage
async def _send_portrait(
    bot: _PhotoBot, chat_id: ChatId, image_bytes: bytes, caption: str
) -> None:
    """Post the portrait to Telegram. The bytes and caption are never logged."""
    await bot.send_photo(chat_id=chat_id, photo=BytesIO(image_bytes), caption=caption)


def telegram_photo_sender(bot: _PhotoBot) -> PhotoSender:
    """Build the production :data:`PhotoSender`, which sends from memory.

    The bytes are wrapped in a :class:`BytesIO`, so no temporary file is written.
    """

    async def send(chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        await _send_portrait(bot, chat_id, image_bytes, caption)

    return send


@dataclass
class _RunOutcome:
    """How one run ended, recorded by the step that ends it.

    The outer stage reads it to log the outcome: ``error`` with the error type for
    a failed run, ``dropped`` for a run that a ``/restart`` abandoned, and ``ok``
    otherwise.
    """

    error_type: str | None = None
    dropped: bool = False


def _fail_run(
    store: SessionStore,
    chat_id: ChatId,
    run: str,
    outcome: _RunOutcome,
    exc: Exception,
) -> str | None:
    """Record a failed step and return the apology, or drop it if the run is stale.

    A run that a ``/restart`` has already abandoned must not send anything, so a
    late failure is dropped the same way a late success is.
    """
    if store.get(chat_id).generation_run != run:
        logger.info("portrait_dropped reason=stale_run chat_id=%s", chat_id)
        outcome.dropped = True
        return None
    outcome.error_type = type(exc).__name__
    return GENERATION_FAILED_TEXT


async def _generate_and_deliver(
    chat_id: ChatId,
    run: str,
    store: SessionStore,
    download: Downloader,
    portraits: PortraitClient,
    send_photo: PhotoSender,
    outcome: _RunOutcome,
) -> str | None:
    """Run the steps of one generation, in order, for a run that has started."""
    state = store.get(chat_id)
    dossier = state.dossier
    file_id = state.photo_file_id
    if dossier is None or file_id is None:
        raise StateError("a portrait run needs a dossier and an original photo")

    try:
        photo_bytes = await download(file_id)
    except Exception as exc:  # a download failure is recoverable for the user
        logger.error(
            "portrait_download_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )
        return _fail_run(store, chat_id, run, outcome, exc)

    def still_current() -> bool:
        # The converter checks this before every model call, so a /restart during
        # the download or a retry stops the run before it spends another call.
        return store.get(chat_id).generation_run == run

    try:
        portrait = await generate_portrait(
            chat_id, photo_bytes, dossier, portraits, still_current=still_current
        )
    except StaleResult:
        # A /restart abandoned the run before or between model calls. No call was
        # made after it, and nothing is sent.
        logger.info("portrait_dropped reason=stale_run chat_id=%s", chat_id)
        outcome.dropped = True
        return None
    except VisionError as exc:
        # The converter stage already logged the attempt; this line records the
        # outcome of the run.
        logger.error(
            "portrait_run_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )
        return _fail_run(store, chat_id, run, outcome, exc)

    # Commit the delivery before the send (D18). A reset after this point cannot
    # recall a photo already sent; that single race is accepted and logged below.
    if not store.begin_delivery(chat_id, run):
        logger.info("portrait_dropped reason=stale_run chat_id=%s", chat_id)
        outcome.dropped = True
        return None

    # The delivery methods take no run token, because DELIVERING carries none. The
    # reset epoch stands in for it: if a /restart lands during the send, this run
    # is superseded and must not move the chat, even if a newer run is now in
    # DELIVERING.
    epoch = store.epoch(chat_id)
    caption = render_caption(dossier.suggested_animal.name)
    try:
        await send_photo(chat_id, portrait.image_bytes, caption)
    except Exception as exc:  # a Telegram failure is recoverable for the user
        logger.error(
            "portrait_send_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )
        if store.epoch(chat_id) != epoch or not store.fail_delivery(chat_id):
            logger.info("portrait_dropped reason=stale_run chat_id=%s", chat_id)
            outcome.dropped = True
            return None  # a reset landed during the send
        outcome.error_type = type(exc).__name__
        return GENERATION_FAILED_TEXT

    if store.epoch(chat_id) != epoch or not store.finish_delivery(chat_id):
        logger.info(
            "portrait_sent_after_restart chat_id=%s phase_after=%s",
            chat_id,
            store.get(chat_id).phase,
        )
        return None
    logger.info(
        "portrait_delivered chat_id=%s phase_before=%s phase_after=%s animal=%s",
        chat_id,
        Phase.GENERATING,
        Phase.DELIVERING,
        dossier.suggested_animal.name,
    )
    return None


async def _run_once(
    chat_id: ChatId,
    store: SessionStore,
    download: Downloader,
    portraits: PortraitClient,
    send_photo: PhotoSender,
    outcome: _RunOutcome,
) -> str | None:
    """Start a run, refuse a duplicate, and always abandon the run at the end."""
    run = store.begin_generation(chat_id)
    if run is None:
        return ONE_AT_A_TIME_TEXT
    try:
        return await _generate_and_deliver(
            chat_id, run, store, download, portraits, send_photo, outcome
        )
    finally:
        # A no-op once the run has moved on (delivered, or reset by /restart).
        store.abandon_generation(chat_id, run)


def _log_run_end(start: float, outcome: _RunOutcome) -> None:
    """Log the outer stage's outcome. A failed run is logged at error, with its type."""
    duration_ms = (time.monotonic() - start) * 1000
    if outcome.error_type is not None:
        logger.error(
            "stage_end stage=run_generation outcome=error "
            "error_type=%s duration_ms=%.2f",
            outcome.error_type,
            duration_ms,
        )
    elif outcome.dropped:
        logger.info(
            "stage_dropped stage=run_generation reason=stale_run duration_ms=%.2f",
            duration_ms,
        )
    else:
        logger.info(
            "stage_end stage=run_generation outcome=ok duration_ms=%.2f", duration_ms
        )


async def run_generation(
    chat_id: ChatId,
    store: SessionStore,
    download: Downloader,
    portraits: PortraitClient,
    send_photo: PhotoSender,
) -> str | None:
    """Generate and deliver the portrait for a chat that has finished its dossier.

    A second run while one is in flight is refused with the one-at-a-time nudge,
    so no second paid generation starts. Whatever happens, the run is abandoned
    in ``finally``, so the chat is never left in ``GENERATING``.

    The outcome is logged here rather than by ``@log_stage``, because a failed run
    returns the apology text instead of raising. The decorator would log that as
    ``outcome=ok``.
    """
    start = time.monotonic()
    outcome = _RunOutcome()
    logger.info("stage_start stage=run_generation")
    try:
        reply = await _run_once(
            chat_id, store, download, portraits, send_photo, outcome
        )
    except Exception as exc:
        outcome.error_type = type(exc).__name__
        _log_run_end(start, outcome)
        raise
    _log_run_end(start, outcome)
    return reply
