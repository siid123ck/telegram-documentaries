"""Telegram gateway for Phase 1: any text message gets the fixed reply.

No intelligence lives here yet. A text update produces ``hey mate!`` and any
other payload is silently ignored. The pure :func:`build_reply` keeps the reply
logic testable without a Telegram client; :func:`handle_message` is the async
PTB callback and never lets an error escape into the polling loop.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from telegram import Update
from telegram.ext import ContextTypes

from telegram_documentaries.logging_setup import log_stage

logger = logging.getLogger(__name__)

REPLY_TEXT = "hey mate!"

ReplySender: TypeAlias = Callable[[Update, str], Awaitable[None]]


def build_reply(update: Update) -> str | None:
    """Return the fixed reply for a text message, or ``None`` for anything else.

    Only a real, non-empty ``message.text`` qualifies. Photos, voice notes,
    stickers, edits, and channel posts all have no ``update.message.text`` and
    are therefore ignored without raising.
    """
    message = update.message
    if message is None or message.text is None or not message.text.strip():
        return None
    return REPLY_TEXT


async def send_reply(update: Update, text: str) -> None:
    """Send ``text`` back to the originating chat via the Telegram API."""
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(text)


@log_stage
async def _dispatch(update: Update, sender: ReplySender) -> None:
    """Stage boundary: decide the reply and send it when there is one."""
    reply = build_reply(update)
    if reply is not None:
        await sender(update, reply)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    sender: ReplySender = send_reply,
) -> None:
    """PTB callback: send the fixed reply, swallowing all errors.

    ``context`` is unused but required by PTB's callback signature. ``sender``
    is a thin seam that lets tests exercise the send path offline; production
    uses :func:`send_reply`. Errors are logged at error level and never
    propagated, so one bad update cannot kill the polling loop.
    """
    del context
    try:
        await _dispatch(update, sender)
    except Exception as exc:  # degraded gracefully; never propagate to PTB
        logger.error("handler_error error_type=%s", type(exc).__name__)
