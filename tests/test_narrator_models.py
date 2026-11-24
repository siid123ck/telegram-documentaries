"""Tests for the Narrator's typed TTS reply contract, :class:`TtsReply`.

The TTS reply is untrusted. A :class:`TtsReply` exists only when its audio bytes
are present, non-empty, within the size cap, and in the expected format. Each
failure is reported with a reason code and the byte count only. The audio bytes
never appear in an error, because a ValidationError would otherwise repeat them
into logs. Extra fields are forbidden. Nothing here imports the SDK or the network.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from telegram_documentaries.narrator_models import (
    EXPECTED_PCM_MIME,
    EXPECTED_SAMPLE_RATE_HZ,
    MAX_MP3_BYTES,
    MAX_PCM_BYTES,
    TtsReply,
    VoiceNote,
)

#: A sentinel that must never surface in an error message or a log line.
SENTINEL_BYTES = b"SENTINEL_PCM_DO_NOT_SHOW"


def _valid_fields(**overrides: Any) -> dict[str, Any]:
    """Return the fields of a well-formed TTS reply, with any overrides applied."""
    fields: dict[str, Any] = {
        "pcm": b"\x00\x01" * 240,
        "mime_type": EXPECTED_PCM_MIME,
        "sample_rate_hz": EXPECTED_SAMPLE_RATE_HZ,
    }
    fields.update(overrides)
    return fields


def _error_text(fields: dict[str, Any]) -> str:
    """Build a reply from ``fields`` and return the ValidationError message."""
    with pytest.raises(ValidationError) as excinfo:
        TtsReply.model_validate(fields)
    return str(excinfo.value)


# --- Constants ----------------------------------------------------------------


def test_the_pcm_cap_is_ten_mebibytes() -> None:
    assert MAX_PCM_BYTES == 10 * 1024 * 1024


def test_the_expected_pcm_format_is_mono_linear_pcm_at_24_khz() -> None:
    assert EXPECTED_PCM_MIME == "audio/L16"
    assert EXPECTED_SAMPLE_RATE_HZ == 24000


# --- Happy path ---------------------------------------------------------------


def test_a_well_formed_reply_is_accepted_and_keeps_its_audio() -> None:
    reply = TtsReply.model_validate(_valid_fields())

    assert reply.pcm == b"\x00\x01" * 240
    assert reply.mime_type == EXPECTED_PCM_MIME
    assert reply.sample_rate_hz == EXPECTED_SAMPLE_RATE_HZ


def test_a_reply_of_exactly_the_cap_is_accepted() -> None:
    reply = TtsReply.model_validate(_valid_fields(pcm=b"\x00" * MAX_PCM_BYTES))

    assert len(reply.pcm) == MAX_PCM_BYTES


def test_a_single_byte_of_audio_is_accepted() -> None:
    reply = TtsReply.model_validate(_valid_fields(pcm=b"\x7f"))

    assert reply.pcm == b"\x7f"


# --- Audio bytes present and non-empty ----------------------------------------


def test_empty_audio_is_rejected_with_its_reason_code() -> None:
    message = _error_text(_valid_fields(pcm=b""))

    assert "empty_audio" in message
    assert "bytes=0" in message


def test_a_missing_audio_part_is_rejected() -> None:
    fields = _valid_fields()
    del fields["pcm"]

    with pytest.raises(ValidationError):
        TtsReply.model_validate(fields)


def test_none_for_the_audio_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TtsReply.model_validate(_valid_fields(pcm=None))


def test_text_in_place_of_audio_bytes_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TtsReply.model_validate(_valid_fields(pcm="not audio"))


# --- Size cap -----------------------------------------------------------------


def test_audio_one_byte_over_the_cap_is_rejected_as_oversize() -> None:
    too_big = MAX_PCM_BYTES + 1

    message = _error_text(_valid_fields(pcm=b"\x00" * too_big))

    assert "oversize_audio" in message
    assert f"bytes={too_big}" in message


# --- Expected format ----------------------------------------------------------


@pytest.mark.parametrize(
    "mime_type",
    ["audio/wav", "audio/mpeg", "audio/L16;rate=16000", ""],
    ids=["wav", "mpeg", "rate-suffixed", "empty"],
)
def test_a_wrong_mime_type_is_rejected_as_unexpected_format(mime_type: str) -> None:
    message = _error_text(_valid_fields(mime_type=mime_type))

    assert "unexpected_format" in message


@pytest.mark.parametrize(
    "sample_rate_hz", [16000, 48000, 0], ids=["16k", "48k", "zero"]
)
def test_a_wrong_sample_rate_is_rejected_as_unexpected_format(
    sample_rate_hz: int,
) -> None:
    message = _error_text(_valid_fields(sample_rate_hz=sample_rate_hz))

    assert "unexpected_format" in message


def test_a_sample_rate_given_as_text_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TtsReply.model_validate(_valid_fields(sample_rate_hz="24000"))


# --- Extra fields -------------------------------------------------------------


@pytest.mark.parametrize("extra", ["caption", "parse_mode", "codec"])
def test_an_extra_field_is_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        TtsReply.model_validate(_valid_fields(**{extra: "anything"}))


# --- Errors carry the reason and count, never the audio -----------------------


def test_the_error_names_the_reason_and_count_but_never_the_audio() -> None:
    message = _error_text(_valid_fields(mime_type="audio/wav", pcm=SENTINEL_BYTES))

    assert "unexpected_format" in message
    assert f"bytes={len(SENTINEL_BYTES)}" in message
    assert SENTINEL_BYTES.decode() not in message
    assert repr(SENTINEL_BYTES) not in message


# --- VoiceNote: the encoded MP3 contract --------------------------------------

#: A sentinel MP3 payload that must never surface in an error message or a log line.
SENTINEL_MP3 = b"SENTINEL_MP3_DO_NOT_SHOW"


def _mp3(start: bytes, size: int = 512) -> bytes:
    """Return MP3-like bytes that begin with ``start`` and pad to ``size``."""
    return start + b"\x00" * (size - len(start))


def _voice_error_text(mp3: bytes) -> str:
    """Build a VoiceNote from ``mp3`` and return the ValidationError message."""
    with pytest.raises(ValidationError) as excinfo:
        VoiceNote.model_validate({"mp3": mp3})
    return str(excinfo.value)


@pytest.mark.parametrize(
    "start",
    [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    ids=["id3-tag", "mpeg1-l3", "mpeg2-l3", "mpeg2.5-l3"],
)
def test_a_voice_note_with_a_valid_mp3_start_is_accepted(start: bytes) -> None:
    note = VoiceNote.model_validate({"mp3": _mp3(start)})

    assert note.mp3 == _mp3(start)


def test_a_voice_note_of_exactly_the_cap_is_accepted() -> None:
    note = VoiceNote.model_validate({"mp3": _mp3(b"ID3", MAX_MP3_BYTES)})

    assert len(note.mp3) == MAX_MP3_BYTES


@pytest.mark.parametrize(
    "start",
    [b"RIFF", b"\xff\xfa", b"\xff\xe0", b"\x00\xff\xfb", b"\xff", b"I"],
    ids=["wav", "wrong-layer", "bad-sync", "leading-zero", "lone-sync", "short-id"],
)
def test_bytes_without_a_valid_mp3_start_are_rejected_as_bad_mp3(start: bytes) -> None:
    message = _voice_error_text(_mp3(start))

    assert "bad_mp3" in message
    assert f"bytes={len(_mp3(start))}" in message


def test_empty_mp3_is_rejected_as_empty_audio() -> None:
    message = _voice_error_text(b"")

    assert "empty_audio" in message
    assert "bytes=0" in message


def test_mp3_one_byte_over_the_cap_is_rejected_as_oversize() -> None:
    too_big = MAX_MP3_BYTES + 1

    message = _voice_error_text(_mp3(b"ID3", too_big))

    assert "oversize_audio" in message
    assert f"bytes={too_big}" in message


@pytest.mark.parametrize("extra", ["caption", "codec"])
def test_an_extra_field_on_a_voice_note_is_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        VoiceNote.model_validate({"mp3": _mp3(b"ID3"), extra: "anything"})


def test_a_voice_note_error_names_the_reason_but_never_the_mp3_bytes() -> None:
    message = _voice_error_text(SENTINEL_MP3)

    assert "bad_mp3" in message
    assert SENTINEL_MP3.decode() not in message
    assert repr(SENTINEL_MP3) not in message
