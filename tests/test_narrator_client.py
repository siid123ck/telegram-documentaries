"""Offline tests for the Narrator's TTS seam (Phase 6, group 4).

The provider is replaced by an injected fake transport, so nothing here imports
``google.genai`` or the ADK, opens a socket, or reads ``.env``. The one subprocess
test proves that constructing the real client does not import either SDK.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from telegram_documentaries import narrator_client
from telegram_documentaries.config import MODELS, NARRATOR_VOICE
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.narrator_client import (
    NARRATION_TIMEOUT_SECONDS,
    GeminiNarrationClient,
    NarrationClient,
    RawAudio,
    first_audio_part,
    parse_mime_and_rate,
)
from telegram_documentaries.narrator_models import (
    EXPECTED_PCM_MIME,
    EXPECTED_SAMPLE_RATE_HZ,
    TtsReply,
)
from telegram_documentaries.prompts import NARRATOR_INSTRUCTION

#: Sentinel carried by the script text and the audio. None may reach an error or a log.
SCRIPT_SENTINEL = "SENTINEL-SCRIPT-PROSE-5d3a"
AUDIO_SENTINEL = b"SENTINEL-AUDIO-9e1c"
PROVIDER_SENTINEL = "SENTINEL-PROVIDER-PAYLOAD-2b8f"

EXPECTED_MIME_WITH_PARAMS = (
    f"{EXPECTED_PCM_MIME};codec=pcm;rate={EXPECTED_SAMPLE_RATE_HZ}"
)


def _pcm() -> bytes:
    return AUDIO_SENTINEL + b"\x00\x01" * 64


def _valid_audio(data: bytes | None = None) -> RawAudio:
    return RawAudio(
        data=data if data is not None else _pcm(), mime_type=EXPECTED_MIME_WITH_PARAMS
    )


class ProviderFailure(Exception):
    """A stand-in for an SDK error that carries an HTTP status code."""

    def __init__(self, code: int | None, message: str = PROVIDER_SENTINEL) -> None:
        super().__init__(message)
        self.code = code


class FakeNarrationTransport:
    """Records every request and replies with audio, nothing, a delay, or an error."""

    def __init__(
        self,
        *,
        reply: RawAudio | None = None,
        no_audio: bool = False,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self._reply = None if no_audio else (reply or _valid_audio())
        self._error = error
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        model: str,
        prompt: str,
        voice: str,
        response_modalities: tuple[str, ...],
    ) -> RawAudio | None:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "voice": voice,
                "response_modalities": response_modalities,
            }
        )
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._reply


class _FakeNarrationClient:
    """An offline ``NarrationClient`` that returns a fixed, checked reply."""

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        return TtsReply(
            pcm=_pcm(),
            mime_type=EXPECTED_PCM_MIME,
            sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
        )


def _client(transport: FakeNarrationTransport) -> GeminiNarrationClient:
    return GeminiNarrationClient(api_key="test-key", transport=transport)


async def _synthesise(
    client: GeminiNarrationClient, text: str = SCRIPT_SENTINEL
) -> TtsReply:
    return await client.synthesise(text, voice=NARRATOR_VOICE)


# --- The seam ----------------------------------------------------------------


async def test_a_fake_returning_a_tts_reply_satisfies_the_narration_protocol() -> None:
    client: NarrationClient = _FakeNarrationClient()

    assert isinstance(client, NarrationClient)
    reply = await client.synthesise(SCRIPT_SENTINEL, voice=NARRATOR_VOICE)
    assert isinstance(reply, TtsReply)


def test_the_gemini_client_satisfies_the_protocol() -> None:
    assert isinstance(_client(FakeNarrationTransport()), NarrationClient)


def test_constructing_the_real_client_does_not_import_genai_or_the_adk() -> None:
    script = (
        "import sys\n"
        "from telegram_documentaries.narrator_client import GeminiNarrationClient\n"
        "GeminiNarrationClient(api_key='test-key')\n"
        "leaked = [m for m in sys.modules "
        "if m.startswith('google.genai') or m.startswith('google.adk')]\n"
        "print('LEAKED=' + ','.join(leaked))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LEAKED="


def test_the_synthesise_signature_takes_only_text_and_voice() -> None:
    parameters = inspect.signature(GeminiNarrationClient.synthesise).parameters

    assert set(parameters) == {"self", "text", "voice"}


# --- Timeout -----------------------------------------------------------------


def test_the_narration_timeout_is_thirty_seconds_per_attempt() -> None:
    assert NARRATION_TIMEOUT_SECONDS == 30.0


async def test_a_transport_that_outlasts_the_deadline_maps_to_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(narrator_client, "NARRATION_TIMEOUT_SECONDS", 0.01)
    client = _client(FakeNarrationTransport(delay=5.0))

    with pytest.raises(ProviderTimeout):
        await _synthesise(client)


async def test_a_timeout_raised_by_the_transport_maps_to_provider_timeout() -> None:
    client = _client(FakeNarrationTransport(error=TimeoutError()))

    with pytest.raises(ProviderTimeout):
        await _synthesise(client)


async def test_one_call_makes_exactly_one_provider_attempt_even_on_timeout() -> None:
    transport = FakeNarrationTransport(error=TimeoutError())

    with pytest.raises(ProviderTimeout):
        await _synthesise(_client(transport))

    assert len(transport.calls) == 1


# --- Status mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, RateLimited),
        (500, VisionUnavailable),
        (502, VisionUnavailable),
        (503, VisionUnavailable),
        (400, ProviderRejected),
        (401, ProviderRejected),
        (403, ProviderRejected),
        (404, ProviderRejected),
        (418, VisionUnavailable),
        (None, VisionUnavailable),
    ],
    ids=lambda value: str(value),
)
async def test_a_provider_status_maps_to_its_typed_error(
    status: int | None, expected: type[VisionError]
) -> None:
    client = _client(FakeNarrationTransport(error=ProviderFailure(status)))

    with pytest.raises(VisionError) as excinfo:
        await _synthesise(client)

    assert type(excinfo.value) is expected


async def test_the_provider_message_never_reaches_the_typed_error() -> None:
    client = _client(FakeNarrationTransport(error=ProviderFailure(500)))

    with pytest.raises(VisionError) as excinfo:
        await _synthesise(client)

    assert PROVIDER_SENTINEL not in str(excinfo.value)
    assert PROVIDER_SENTINEL not in repr(excinfo.value)


async def test_a_provider_failure_logs_the_error_type_and_status_not_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client(FakeNarrationTransport(error=ProviderFailure(503)))

    with caplog.at_level(logging.ERROR), pytest.raises(VisionError):
        await _synthesise(client)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("error_type=ProviderFailure" in r.getMessage() for r in errors)
    assert any("provider_status=503" in r.getMessage() for r in errors)
    assert PROVIDER_SENTINEL not in caplog.text
    assert SCRIPT_SENTINEL not in caplog.text


# --- Reply validation --------------------------------------------------------


async def test_a_valid_audio_reply_becomes_a_checked_tts_reply() -> None:
    reply = await _synthesise(_client(FakeNarrationTransport(reply=_valid_audio())))

    assert reply.pcm == _pcm()
    assert reply.mime_type == EXPECTED_PCM_MIME
    assert reply.sample_rate_hz == EXPECTED_SAMPLE_RATE_HZ


async def test_a_reply_with_no_audio_part_is_an_invalid_response() -> None:
    client = _client(FakeNarrationTransport(no_audio=True))

    with pytest.raises(InvalidResponse):
        await _synthesise(client)


@pytest.mark.parametrize(
    ("audio", "reason"),
    [
        (RawAudio(data=b"", mime_type=EXPECTED_MIME_WITH_PARAMS), "empty_audio"),
        (
            RawAudio(data=_pcm(), mime_type="audio/mpeg;rate=24000"),
            "unexpected_format",
        ),
        (
            RawAudio(data=_pcm(), mime_type=f"{EXPECTED_PCM_MIME};rate=16000"),
            "unexpected_format",
        ),
        (RawAudio(data=_pcm(), mime_type=EXPECTED_PCM_MIME), "unexpected_format"),
    ],
    ids=["empty", "wrong-mime", "wrong-rate", "no-rate-parameter"],
)
async def test_a_malformed_audio_reply_is_an_invalid_response_with_its_reason(
    audio: RawAudio, reason: str
) -> None:
    client = _client(FakeNarrationTransport(reply=audio))

    with pytest.raises(InvalidResponse) as excinfo:
        await _synthesise(client)

    assert excinfo.value.detail == reason


async def test_an_invalid_reply_never_carries_the_audio_bytes_into_the_error() -> None:
    client = _client(
        FakeNarrationTransport(
            reply=RawAudio(data=AUDIO_SENTINEL, mime_type="audio/mpeg;rate=24000")
        )
    )

    with pytest.raises(InvalidResponse) as excinfo:
        await _synthesise(client)

    assert AUDIO_SENTINEL.decode() not in str(excinfo.value)
    assert AUDIO_SENTINEL.decode() not in excinfo.value.detail


def test_the_audio_part_is_found_in_a_genai_shaped_response() -> None:
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text=None, inline_data=None),
                        SimpleNamespace(
                            text=None,
                            inline_data=SimpleNamespace(
                                data=_pcm(), mime_type=EXPECTED_MIME_WITH_PARAMS
                            ),
                        ),
                    ]
                )
            )
        ]
    )

    audio = first_audio_part(response)

    assert audio == RawAudio(data=_pcm(), mime_type=EXPECTED_MIME_WITH_PARAMS)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(candidates=[]),
        SimpleNamespace(candidates=None),
        SimpleNamespace(candidates=[SimpleNamespace(content=None)]),
        SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="words only", inline_data=None)]
                    )
                )
            ]
        ),
    ],
    ids=["no-candidates", "none-candidates", "no-content", "text-only"],
)
def test_a_response_without_audio_yields_no_audio_part(response: Any) -> None:
    assert first_audio_part(response) is None


def test_the_mime_type_and_rate_are_split_from_the_content_type_parameters() -> None:
    assert parse_mime_and_rate(EXPECTED_MIME_WITH_PARAMS) == (
        EXPECTED_PCM_MIME,
        EXPECTED_SAMPLE_RATE_HZ,
    )


def test_a_content_type_without_a_rate_parameter_reports_no_rate() -> None:
    assert parse_mime_and_rate(EXPECTED_PCM_MIME) == (EXPECTED_PCM_MIME, 0)


# --- The request -------------------------------------------------------------


async def test_the_request_names_the_narrator_model_from_config() -> None:
    transport = FakeNarrationTransport()

    await _synthesise(_client(transport))

    assert transport.calls[0]["model"] == MODELS["narrator"]
    assert transport.calls[0]["model"] == "gemini-3.1-flash-tts-preview"


async def test_the_prompt_is_the_narrator_instruction_followed_by_the_script() -> None:
    transport = FakeNarrationTransport()

    await _synthesise(_client(transport))

    prompt = transport.calls[0]["prompt"]
    assert prompt.startswith(NARRATOR_INSTRUCTION)
    assert prompt.endswith(SCRIPT_SENTINEL)


async def test_the_voice_name_passed_in_reaches_the_transport() -> None:
    transport = FakeNarrationTransport()

    await _client(transport).synthesise(SCRIPT_SENTINEL, voice="Kore")

    assert transport.calls[0]["voice"] == "Kore"


async def test_the_request_asks_for_audio_output_only() -> None:
    transport = FakeNarrationTransport()

    await _synthesise(_client(transport))

    assert transport.calls[0]["response_modalities"] == ("AUDIO",)


async def test_the_request_carries_only_model_prompt_voice_and_modality() -> None:
    transport = FakeNarrationTransport()

    await _synthesise(_client(transport))

    assert set(transport.calls[0]) == {
        "model",
        "prompt",
        "voice",
        "response_modalities",
    }


async def test_the_request_never_carries_a_dossier_or_photo_bytes() -> None:
    transport = FakeNarrationTransport()

    await _synthesise(_client(transport))

    rendered = repr(transport.calls[0])
    assert "suggested_animal" not in rendered
    assert "quirks" not in rendered
    assert "photo" not in rendered.lower()


async def test_a_failing_call_is_not_retried_inside_the_client() -> None:
    transport = FakeNarrationTransport(error=ProviderFailure(503))

    with pytest.raises(VisionUnavailable):
        await _synthesise(_client(transport))

    assert len(transport.calls) == 1
