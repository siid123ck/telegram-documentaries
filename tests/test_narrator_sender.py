"""Tests for the production voice-note sender (Phase 6, N14, N16, N17).

The sender takes a checked :class:`VoiceNote`, writes its MP3 bytes to a file in the
system temp directory, and sends that file by path as a Telegram voice message. The
file is deleted in a ``finally`` block, on success or failure. The bot is a fake that
records each ``send_voice`` call and reads the file while the call is in progress, so
nothing here touches Telegram or the network. The sender makes exactly one attempt:
a failure propagates to the caller and is never retried (N14). The voice note carries
no caption and no ``parse_mode`` (N17).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from telegram_documentaries.models import as_chat_id
from telegram_documentaries.narrator_models import VoiceNote
from telegram_documentaries.narrator_sender import telegram_voice_sender

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)
_REPO_ROOT = Path(__file__).resolve().parents[1]

#: A small MP3 that starts with an ID3 tag, so it passes the VoiceNote header check.
_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"narration-audio-bytes"
_NOTE = VoiceNote(mp3=_MP3)


class _FakeBot:
    """Records ``send_voice`` calls and, during each call, what the file held.

    ``error``, when set, is raised on every call after the file has been read.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, object]] = []
        #: The file path the sender passed, and what the file held while in use.
        self.paths: list[Path] = []
        self.contents: list[bytes] = []
        self.existed: list[bool] = []

    async def send_voice(
        self, chat_id: int, voice: Path, caption: str | None = None
    ) -> object:
        path = Path(voice)
        self.calls.append({"chat_id": chat_id, "voice": voice, "caption": caption})
        self.paths.append(path)
        self.existed.append(path.exists())
        self.contents.append(path.read_bytes() if path.exists() else b"")
        if self._error is not None:
            raise self._error
        return object()


async def test_the_voice_note_is_sent_by_path_with_the_mp3_bytes_in_the_file() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 4242
    assert bot.existed == [True]
    assert bot.contents == [_MP3]


async def test_the_temp_file_is_in_the_system_temp_directory_not_the_repo() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    path = bot.paths[0].resolve()
    assert path.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not path.is_relative_to(_REPO_ROOT)


async def test_the_temp_file_is_removed_after_a_successful_send() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    assert not bot.paths[0].exists()


async def test_the_temp_file_is_removed_after_a_failed_send() -> None:
    bot = _FakeBot(error=RuntimeError("telegram unavailable"))
    send = telegram_voice_sender(bot)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        await send(_CHAT, _NOTE)

    assert bot.existed == [True]
    assert not bot.paths[0].exists()


async def test_the_temp_file_is_named_as_an_mp3_for_format_detection() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    assert bot.paths[0].suffix == ".mp3"


async def test_the_voice_note_is_sent_with_no_caption() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    # N17: the script text is already in the chat, so the note carries no caption.
    assert bot.calls[0]["caption"] is None


async def test_the_voice_note_goes_only_to_the_chat_it_was_sent_to() -> None:
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_OTHER, _NOTE)

    assert [call["chat_id"] for call in bot.calls] == [7777]


async def test_a_failed_send_is_raised_after_exactly_one_attempt() -> None:
    # N14: no retry. A failed voice send could duplicate a note, so the sender
    # makes one call and lets the error reach the caller.
    bot = _FakeBot(error=RuntimeError("telegram unavailable"))
    send = telegram_voice_sender(bot)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        await send(_CHAT, _NOTE)

    assert len(bot.calls) == 1


async def test_a_failed_temp_file_removal_is_logged_and_never_raised(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N16: a removal failure is logged at error and does not fail the send.
    def refuse_to_unlink(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("no delete for you")

    monkeypatch.setattr(Path, "unlink", refuse_to_unlink)
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    with caplog.at_level(logging.ERROR):
        await send(_CHAT, _NOTE)

    assert len(bot.calls) == 1
    assert any(
        record.levelno == logging.ERROR and "PermissionError" in record.getMessage()
        for record in caplog.records
    )
    monkeypatch.undo()
    bot.paths[0].unlink(missing_ok=True)


async def test_the_sender_logs_nothing_about_the_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bot = _FakeBot()
    send = telegram_voice_sender(bot)

    await send(_CHAT, _NOTE)

    # The audio bytes must never reach a log line.
    for record in caplog.records:
        assert "narration-audio-bytes" not in record.getMessage()
        assert _MP3 not in record.getMessage().encode()
