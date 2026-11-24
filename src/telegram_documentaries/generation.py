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
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.messages import (
    GENERATION_FAILED_TEXT,
    ONE_AT_A_TIME_TEXT,
    SCRIPT_FAILED_TEXT,
    render_caption,
)
from telegram_documentaries.models import ChatId
from telegram_documentaries.photo_handler import Downloader
from telegram_documentaries.scripter import generate_script
from telegram_documentaries.scripter_client import ScriptClient
from telegram_documentaries.state import Phase, SessionStore, StateError

logger = logging.getLogger(__name__)

#: Sends portrait bytes and a caption to a chat. Injected, so tests never touch
#: Telegram.
PhotoSender: TypeAlias = Callable[[ChatId, bytes, str], Awaitable[None]]
TextSender: TypeAlias = Callable[[ChatId, str], Awaitable[None]]


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


class _TextBot(Protocol):
    """The slice of ``telegram.Bot`` needed to send a text message."""

    async def send_message(self, chat_id: int, text: str) -> object: ...


def telegram_text_sender(bot: _TextBot) -> TextSender:
    """Build the production :data:`TextSender`, which sends the script as text.

    The text is never logged. Failures propagate to the script step, which decides
    whether to send the apology.
    """

    async def send(chat_id: ChatId, text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

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


async def _send_script_apology(chat_id: ChatId, send_text: TextSender) -> None:
    """Send the fixed apology after a script failure. It never carries the script."""
    try:
        await send_text(chat_id, SCRIPT_FAILED_TEXT)
    except Exception as exc:  # the apology itself is best effort
        logger.error(
            "script_apology_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )


async def _run_script_step(
    chat_id: ChatId,
    store: SessionStore,
    dossier: Dossier,
    scripts: ScriptClient,
    send_text: TextSender,
) -> None:
    """Write, store, and send the narration text after the portrait is delivered.

    Runs in ``DELIVERING`` with the portrait already sent. The reset epoch guards
    every step, so a ``/restart`` that lands during the model call drops the late
    result without a send. A failure sends the apology and keeps the portrait.
    """
    epoch = store.epoch(chat_id)
    if not store.begin_script(chat_id, epoch):
        logger.info("script_dropped reason=not_started chat_id=%s", chat_id)
        return

    def still_current() -> bool:
        # The paid-call guard: no model call is made once the chat has been reset.
        return store.epoch(chat_id) == epoch

    try:
        script = await generate_script(dossier, scripts, still_current=still_current)
    except Exception as exc:  # a Scripter failure is recoverable for the user
        if store.epoch(chat_id) != epoch:
            logger.info("script_dropped reason=stale_run chat_id=%s", chat_id)
            return
        logger.error(
            "script_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )
        store.abandon_script(chat_id, epoch)
        await _send_script_apology(chat_id, send_text)
        return

    # No await between the epoch check and the commit, so the commit itself is the
    # guard: a reset that landed during the model call makes it return False.
    if not store.commit_script(chat_id, epoch, script):
        logger.info("script_dropped reason=stale_run chat_id=%s", chat_id)
        return
    try:
        await send_text(chat_id, script)
    except Exception as exc:  # a Telegram failure is recoverable for the user
        logger.error(
            "script_send_failed error_type=%s chat_id=%s",
            type(exc).__name__,
            chat_id,
        )
        if store.epoch(chat_id) != epoch:
            logger.info("script_dropped reason=stale_run chat_id=%s", chat_id)
            return
        # The script stays stored for the Narrator. The send is not retried.
        await _send_script_apology(chat_id, send_text)


async def _generate_and_deliver(
    chat_id: ChatId,
    run: str,
    store: SessionStore,
    download: Downloader,
    portraits: PortraitClient,
    send_photo: PhotoSender,
    outcome: _RunOutcome,
    scripts: ScriptClient | None = None,
    send_text: TextSender | None = None,
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
    # The script step starts with no await after finish_delivery (S3), so the
    # in-flight guard covers the gap between the portrait and the script.
    if scripts is not None and send_text is not None:
        await _run_script_step(chat_id, store, dossier, scripts, send_text)
    return None


async def _run_once(
    chat_id: ChatId,
    store: SessionStore,
    download: Downloader,
    portraits: PortraitClient,
    send_photo: PhotoSender,
    outcome: _RunOutcome,
    scripts: ScriptClient | None = None,
    send_text: TextSender | None = None,
) -> str | None:
    """Start a run, refuse a duplicate, and always abandon the run at the end."""
    run = store.begin_generation(chat_id)
    if run is None:
        return ONE_AT_A_TIME_TEXT
    try:
        return await _generate_and_deliver(
            chat_id,
            run,
            store,
            download,
            portraits,
            send_photo,
            outcome,
            scripts,
            send_text,
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
    *,
    scripts: ScriptClient | None = None,
    send_text: TextSender | None = None,
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
            chat_id,
            store,
            download,
            portraits,
            send_photo,
            outcome,
            scripts,
            send_text,
        )
    except Exception as exc:
        outcome.error_type = type(exc).__name__
        _log_run_end(start, outcome)
        raise
    _log_run_end(start, outcome)
    return reply
