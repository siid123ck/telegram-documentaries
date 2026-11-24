"""Telegram gateway: the fixed phase-one reply and the reply sender.

The pure :func:`build_reply` decides the fixed ``hey mate!`` reply for a text
message, and :func:`send_reply` delivers any reply back to the chat. Routing by
chat phase lives in :mod:`telegram_documentaries.routing`, which uses both.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from telegram import Update

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
