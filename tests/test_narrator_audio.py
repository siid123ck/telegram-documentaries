"""Tests for the Narrator's PCM-to-MP3 encoder seam, :func:`encode_mp3`.

Every test except the two real-encoder tests injects a fake encoder, so the encoder
library is never run. The real-encoder tests run it on a small PCM tone, or on a
valid reply through the default seam, and check that the output is an MP3 that
:class:`VoiceNote` accepts. Encoder failures become
``bad_mp3`` errors that never carry the raw encoder message.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from telegram_documentaries.narrator import generate_narration
from telegram_documentaries.narrator_audio import encode_mp3
from telegram_documentaries.narrator_models import (
    EXPECTED_PCM_MIME,
    EXPECTED_SAMPLE_RATE_HZ,
    TtsReply,
    VoiceNote,
)

FIXTURE_PCM = Path(__file__).parent / "fixtures" / "narrator_tone.pcm"
AUDIO_MODULE = (
    Path(__file__).parents[1] / "src" / "telegram_documentaries" / "narrator_audio.py"
)

#: A valid MP3 header (an ID3 tag) that the fake encoder returns.
FAKE_MP3 = b"ID3" + b"\x00" * 128

#: A sentinel that must never surface in an error message.
SENTINEL_ENCODER_MESSAGE = "SENTINEL_ENCODER_DO_NOT_SHOW"


def _reply(pcm: bytes = b"\x00\x01" * 240) -> TtsReply:
    """Return a well-formed TTS reply carrying ``pcm``."""
    return TtsReply(
        pcm=pcm,
        mime_type=EXPECTED_PCM_MIME,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
    )


class FakeEncoder:
    """Records each call and returns a fixed output (or raises a fixed error)."""

    def __init__(
        self,
        output: bytes = FAKE_MP3,
        error: BaseException | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.calls: list[TtsReply] = []

    def __call__(self, reply: TtsReply) -> bytes:
        self.calls.append(reply)
        if self.error is not None:
            raise self.error
        return self.output


# --- Through the injected seam -----------------------------------------------


def test_a_valid_encoder_output_becomes_a_voice_note_with_those_bytes() -> None:
    fake = FakeEncoder()

    note = encode_mp3(_reply(), encoder=fake)

    assert isinstance(note, VoiceNote)
    assert note.mp3 == FAKE_MP3


def test_the_encoder_is_called_once_with_the_reply_it_was_given() -> None:
    fake = FakeEncoder()
    reply = _reply()

    encode_mp3(reply, encoder=fake)

    assert fake.calls == [reply]


def test_empty_encoder_output_is_rejected_as_empty_audio() -> None:
    fake = FakeEncoder(output=b"")

    with pytest.raises(ValueError, match="empty_audio"):
        encode_mp3(_reply(), encoder=fake)


def test_encoder_output_without_an_mp3_start_is_rejected_as_bad_mp3() -> None:
    fake = FakeEncoder(output=b"RIFF" + b"\x00" * 64)

    with pytest.raises(ValueError, match="bad_mp3"):
        encode_mp3(_reply(), encoder=fake)


def test_an_encoder_exception_becomes_bad_mp3_with_no_raw_message() -> None:
    fake = FakeEncoder(error=RuntimeError(SENTINEL_ENCODER_MESSAGE))

    with pytest.raises(ValueError) as excinfo:
        encode_mp3(_reply(), encoder=fake)

    assert "bad_mp3" in str(excinfo.value)
    assert SENTINEL_ENCODER_MESSAGE not in str(excinfo.value)


# --- The real encoder, on the small fixture -----------------------------------


def test_the_real_encoder_turns_the_fixture_tone_into_an_accepted_mp3() -> None:
    pcm = FIXTURE_PCM.read_bytes()
    assert len(pcm) == 1024

    note = encode_mp3(
        TtsReply(
            pcm=pcm,
            mime_type=EXPECTED_PCM_MIME,
            sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
        )
    )

    assert note.mp3[:3] == b"ID3" or note.mp3[:2] in (
        b"\xff\xfb",
        b"\xff\xf3",
        b"\xff\xf2",
    )
    assert len(note.mp3) > 0


class _ScriptedClient:
    """Minimal ``NarrationClient``: always returns one fixed reply."""

    def __init__(self, reply: TtsReply) -> None:
        self._reply = reply

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        return self._reply


async def test_the_default_encoder_turns_a_valid_reply_into_a_checked_mp3() -> None:
    """With no encoder seam, the real encoder runs and its output is checked."""
    pcm = b"\x00\x10" * 2400
    client = _ScriptedClient(_reply(pcm))

    note = await generate_narration(
        "A short narration about a meerkat.",
        client,
        still_current=lambda: True,
    )

    assert isinstance(note, VoiceNote)
    assert len(note.mp3) > 0


# --- Boundary discipline -------------------------------------------------------


def test_the_audio_module_imports_no_google_sdk_or_adk() -> None:
    tree = ast.parse(AUDIO_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.split(".")[0] == "google"]
