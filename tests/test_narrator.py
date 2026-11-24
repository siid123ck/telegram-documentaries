"""Offline tests for the Narrator stage, :func:`generate_narration`.

The stage takes the script text and a :class:`NarrationClient`, asks for audio, and
encodes it to a checked :class:`VoiceNote`. It makes at most two attempts in total.
A timeout, 429, or 5xx gets one plain retry after a short backoff. Every other
error fails at once, and malformed audio is never retried. The fake client is
scripted, and the encoder is either a fake or the real :func:`encode_mp3` with a
fake inner encoder, so nothing here touches the network or the encoder library.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import pytest

from telegram_documentaries.config import NARRATOR_VOICE
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionUnavailable,
)
from telegram_documentaries.narrator import generate_narration
from telegram_documentaries.narrator_audio import encode_mp3
from telegram_documentaries.narrator_client import NarrationClient
from telegram_documentaries.narrator_models import (
    EXPECTED_PCM_MIME,
    EXPECTED_SAMPLE_RATE_HZ,
    TtsReply,
    VoiceNote,
)
from telegram_documentaries.vision_client import RETRY_BACKOFF_SECONDS

#: A valid MP3 header that the fake encoder returns.
FAKE_MP3 = b"ID3" + b"\x00" * 128

#: Script text with a marker, so tests can check what the client was given.
SCRIPT_TEXT = "A short narration about a meerkat."


def _reply(pcm: bytes = b"\x00\x01" * 240) -> TtsReply:
    """Return a well-formed TTS reply carrying ``pcm``."""
    return TtsReply(
        pcm=pcm,
        mime_type=EXPECTED_PCM_MIME,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
    )


def _voice_note(reply: TtsReply) -> VoiceNote:
    """Fake encoder seam: always returns a valid MP3 voice note."""
    return VoiceNote(mp3=FAKE_MP3)


def _always_current() -> bool:
    return True


class FakeNarrationClient:
    """Scripted ``NarrationClient``: each call pops the next reply or raises."""

    def __init__(self, script: list[TtsReply | Exception]) -> None:
        self._script = list(script)
        self.texts: list[str] = []
        self.voices: list[str] = []

    @property
    def calls(self) -> int:
        """Number of times the provider was asked for audio."""
        return len(self.texts)

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        self.texts.append(text)
        self.voices.append(voice)
        if not self._script:
            raise AssertionError("FakeNarrationClient ran out of scripted replies")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def _narrate(client: FakeNarrationClient) -> VoiceNote:
    return await generate_narration(
        SCRIPT_TEXT, client, still_current=_always_current, encode=_voice_note
    )


def test_the_fake_satisfies_the_narration_client_protocol() -> None:
    assert isinstance(FakeNarrationClient([_reply()]), NarrationClient)


# --- Happy path ---------------------------------------------------------------


async def test_a_valid_reply_becomes_a_voice_note_after_one_call() -> None:
    client = FakeNarrationClient([_reply()])

    note = await _narrate(client)

    assert isinstance(note, VoiceNote)
    assert note.mp3 == FAKE_MP3
    assert client.calls == 1


async def test_the_client_receives_the_script_text_and_the_narrator_voice_only() -> (
    None
):
    client = FakeNarrationClient([_reply()])

    await _narrate(client)

    assert client.texts == [SCRIPT_TEXT]
    assert client.voices == [NARRATOR_VOICE]


# --- Malformed audio: rejected, never retried ----------------------------------


@pytest.mark.parametrize(
    "reason", ["empty_audio", "oversize_audio", "unexpected_format", "no audio part"]
)
async def test_malformed_audio_from_the_client_is_rejected_after_one_call(
    reason: str,
) -> None:
    client = FakeNarrationClient([InvalidResponse(detail=reason), _reply()])

    with pytest.raises(InvalidResponse):
        await _narrate(client)

    assert client.calls == 1


async def test_malformed_audio_is_not_retried_and_waits_no_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakeNarrationClient([InvalidResponse(detail="empty_audio"), _reply()])

    with pytest.raises(InvalidResponse):
        await _narrate(client)

    assert client.calls == 1
    assert sleeps == []


async def test_an_encoder_rejection_of_the_output_is_raised_after_one_call() -> None:
    """A malformed MP3 from the encoder is rejected by VoiceNote and not retried."""
    client = FakeNarrationClient([_reply(), _reply()])

    def bad_encoder(reply: TtsReply) -> VoiceNote:
        return encode_mp3(reply, encoder=lambda _reply: b"NOT-AN-MP3-HEADER")

    with pytest.raises(ValueError, match="bad_mp3"):
        await generate_narration(
            SCRIPT_TEXT, client, still_current=_always_current, encode=bad_encoder
        )

    assert client.calls == 1


# --- Retry: timeout, 429, and 5xx ----------------------------------------------

RETRYABLE_ERRORS = [ProviderTimeout(), RateLimited(), VisionUnavailable()]


@pytest.mark.parametrize("retryable", RETRYABLE_ERRORS, ids=lambda e: type(e).__name__)
async def test_a_retryable_first_failure_is_retried_once_and_succeeds(
    retryable: Exception,
) -> None:
    client = FakeNarrationClient([retryable, _reply()])

    note = await _narrate(client)

    assert note.mp3 == FAKE_MP3
    assert client.calls == 2


@pytest.mark.parametrize("retryable", RETRYABLE_ERRORS, ids=lambda e: type(e).__name__)
async def test_the_same_retryable_error_on_both_attempts_raises_after_two_calls(
    retryable: Exception,
) -> None:
    client = FakeNarrationClient([retryable, type(retryable)()])

    with pytest.raises(type(retryable)):
        await _narrate(client)

    assert client.calls == 2


async def test_a_retry_waits_the_backoff_before_the_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakeNarrationClient([ProviderTimeout(), _reply()])

    await _narrate(client)

    assert sleeps == [RETRY_BACKOFF_SECONDS]


async def test_a_first_attempt_success_waits_no_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    await _narrate(FakeNarrationClient([_reply()]))

    assert sleeps == []


# --- Fail fast: non-retryable provider errors ----------------------------------


async def test_a_rejected_request_fails_at_once_and_is_not_retried() -> None:
    client = FakeNarrationClient([ProviderRejected(), _reply()])

    with pytest.raises(ProviderRejected):
        await _narrate(client)

    assert client.calls == 1


# --- Paid-call guard ----------------------------------------------------------


async def test_a_stale_chat_before_the_first_attempt_makes_no_call() -> None:
    client = FakeNarrationClient([_reply()])

    with pytest.raises(StaleResult):
        await generate_narration(
            SCRIPT_TEXT, client, still_current=lambda: False, encode=_voice_note
        )

    assert client.calls == 0


async def test_a_reset_during_the_backoff_makes_no_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    # Current for the first attempt, stale before the retry.
    answers = iter([True, False])
    client = FakeNarrationClient([ProviderTimeout(), _reply()])

    with pytest.raises(StaleResult):
        await generate_narration(
            SCRIPT_TEXT,
            client,
            still_current=lambda: next(answers),
            encode=_voice_note,
        )

    assert client.calls == 1


# --- Logging: attempts, outcome, duration, error type; never secrets -----------

#: Sentinels that must never reach a log record: the script text, the audio bytes
#: (raw and base64), and an API-key-shaped secret carried by the fake client.
SENTINEL_AUDIO = b"SENTINEL-AUDIO-BYTES-7f3a"
CREDENTIAL_MARKER = "credential-marker-not-a-secret"


def _sentinel_reply() -> TtsReply:
    return _reply(pcm=SENTINEL_AUDIO)


async def test_a_retried_run_logs_each_attempt_number_and_its_outcome(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = FakeNarrationClient([ProviderTimeout(), _reply()])

    with caplog.at_level(logging.INFO, logger="telegram_documentaries.narrator"):
        await _narrate(client)

    text = caplog.text
    assert "synthesise_attempt_1" in text
    assert "synthesise_attempt_2" in text
    assert "error_type=ProviderTimeout" in text
    assert "outcome=ok" in text
    assert "duration_ms=" in text


async def test_a_failed_run_logs_the_error_type_and_duration_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeNarrationClient([ProviderRejected()])

    with (
        caplog.at_level(logging.INFO, logger="telegram_documentaries.narrator"),
        pytest.raises(ProviderRejected),
    ):
        await _narrate(client)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "synthesise_attempt_1" in r.getMessage()
        and "error_type=ProviderRejected" in r.getMessage()
        and "duration_ms=" in r.getMessage()
        for r in errors
    )


async def test_a_run_never_logs_script_text_audio_bytes_or_keys(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    secret_text = f"{SCRIPT_TEXT} {CREDENTIAL_MARKER}"
    client = FakeNarrationClient([ProviderTimeout(), _sentinel_reply()])

    with caplog.at_level(logging.DEBUG):
        await generate_narration(
            secret_text, client, still_current=_always_current, encode=_voice_note
        )

    text = caplog.text
    assert caplog.records  # the run did log something, so the check is meaningful
    assert SCRIPT_TEXT not in text
    assert CREDENTIAL_MARKER not in text
    assert SENTINEL_AUDIO.decode() not in text
    assert base64.b64encode(SENTINEL_AUDIO).decode() not in text
