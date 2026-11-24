"""Production sender for the narrator's MP3 voice note.

:func:`telegram_voice_sender` writes the checked MP3 to a ``mkstemp`` file in the
system temp directory and posts that file by path as a Telegram voice message. The
file is removed in ``finally``. The audio is never logged.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol, TypeAlias

from telegram_documentaries.models import ChatId
from telegram_documentaries.narrator_models import VoiceNote

logger = logging.getLogger(__name__)

#: Posts the checked MP3 voice note to a chat. Injected, so tests never touch Telegram.
VoiceSender: TypeAlias = Callable[[ChatId, VoiceNote], Awaitable[None]]


class _VoiceBot(Protocol):
    """The slice of ``telegram.Bot`` needed to send a voice message."""

    async def send_voice(
        self, chat_id: int, voice: Path, *, caption: str | None = None
    ) -> object: ...


def _remove_temp_file(path: Path) -> None:
    """Delete the temp file. A failed removal is logged at error, never raised (N16)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("voice_temp_remove_failed error_type=%s", type(exc).__name__)


def telegram_voice_sender(bot: _VoiceBot) -> VoiceSender:
    """Build the production :data:`VoiceSender`, which posts the MP3 as a voice note.

    The MP3 is written once to a ``mkstemp`` file in the system temp directory, never
    inside the repo, and sent by path. The ``.mp3`` suffix lets Telegram detect the
    format. The file is removed in ``finally``, on success or failure. The note
    carries no caption (N17). Exactly one ``send_voice`` call is made per use, and a
    failure propagates without retry (N14). The audio is never logged.
    """

    async def send(chat_id: ChatId, note: VoiceNote) -> None:
        fd, name = tempfile.mkstemp(suffix=".mp3")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(note.mp3)
            await bot.send_voice(chat_id=chat_id, voice=path)
        finally:
            _remove_temp_file(path)

    return send
