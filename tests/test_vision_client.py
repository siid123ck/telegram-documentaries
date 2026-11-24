"""Offline tests for the vision-client seam (task group 4).

These tests use fakes only: they never open a socket or read ``.env``. The
protocol and retry policy are exercised purely through injected doubles. The one
exception is the wiring test at the bottom, which builds (but never calls) the
ADK agent to check the schema it would send to Gemini.
"""

from __future__ import annotations

import logging

import pytest

from telegram_documentaries import bouncer
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.models import PhotoPayload, VisionVerdict, as_chat_id
from telegram_documentaries.state import SessionStore
from telegram_documentaries.vision_client import (
    VisionClient,
    classify_with_retry,
    map_provider_error,
    new_session_id,
)


def _photo() -> PhotoPayload:
    return PhotoPayload(
        chat_id=as_chat_id(4242),
        file_id="telegram-file-id",
        photo_bytes=b"\xff\xd8\xff\xd9",
        caption=None,
    )


def _verdict() -> VisionVerdict:
    return VisionVerdict(
        contains_human=True,
        reason="A discernible person is present.",
        detected_subject="person",
    )


class _CountingVisionClient:
    """Offline stand-in for the ``VisionClient`` protocol.

    Records every photo it is handed, returns a canned verdict, and can be told
    to raise a scripted sequence of errors so the retry policy is observable.
    """

    def __init__(
        self,
        verdict: VisionVerdict | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self._verdict = verdict
        self._errors = list(errors or [])
        self.calls: list[PhotoPayload] = []
        self.attempts = 0

    async def classify(
        self, photo: PhotoPayload, *, repair_note: str | None = None
    ) -> VisionVerdict:
        self.calls.append(photo)
        self.attempts += 1
        if self._errors:
            raise self._errors.pop(0)
        assert self._verdict is not None, "fake needs a verdict or a scripted error"
        return self._verdict


async def test_offline_fake_satisfies_protocol_and_returns_a_canned_verdict() -> None:
    verdict = _verdict()
    client: VisionClient = _CountingVisionClient(verdict=verdict)

    result = await client.classify(_photo())

    assert result is verdict


async def test_inspect_forwards_the_exact_photo_and_returns_the_verdict() -> None:
    photo = _photo()
    verdict = _verdict()
    client = _CountingVisionClient(verdict=verdict)
    store = SessionStore()

    result = await bouncer.inspect(photo, client, store)

    assert len(client.calls) == 1
    assert client.calls[0] is photo
    assert result is verdict


async def test_retryable_timeout_is_bounded_to_two_attempts_then_raised() -> None:
    photo = _photo()
    # More errors than the policy may ever consume: an unbounded loop would be
    # caught by the attempt-count assertion below.
    client = _CountingVisionClient(errors=[ProviderTimeout() for _ in range(5)])

    with pytest.raises(ProviderTimeout):
        await classify_with_retry(client, photo)

    assert client.attempts == 2
    assert client.calls[0] is photo


async def test_non_retryable_invalid_response_fails_fast_after_one_attempt() -> None:
    photo = _photo()
    client = _CountingVisionClient(errors=[InvalidResponse()])

    with pytest.raises(InvalidResponse):
        await classify_with_retry(client, photo)

    assert client.attempts == 1


async def test_one_retryable_failure_then_success_returns_the_verdict() -> None:
    photo = _photo()
    verdict = _verdict()
    client = _CountingVisionClient(verdict=verdict, errors=[RateLimited()])

    result = await classify_with_retry(client, photo)

    assert client.attempts == 2
    assert result is verdict


# --- Provider error mapping (offline; the ADK is never imported here) --------


class DeadlineExceeded(Exception):
    """Stand-in for a provider deadline error, matched by type name."""


class ResourceExhausted(Exception):
    """Stand-in for a provider quota error, matched by type name."""


class ServiceUnavailable(Exception):
    """Stand-in for a provider outage, matched by type name."""


class GenerateContentError(Exception):
    """Stand-in whose name contains the substring 'rate' but is not a quota error."""


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("slow"), ProviderTimeout),
        (DeadlineExceeded("deadline"), ProviderTimeout),
        (ResourceExhausted("quota"), RateLimited),
        (RuntimeError("HTTP 429 too many requests"), RateLimited),
        (ServiceUnavailable("down"), VisionUnavailable),
        (RuntimeError("unexpected"), VisionUnavailable),
        # "generate" contains "rate"; it must not be read as a quota error.
        (GenerateContentError("model call failed"), VisionUnavailable),
    ],
)
def test_provider_failures_map_to_the_typed_vision_errors(
    error: Exception, expected: type[VisionError]
) -> None:
    mapped = map_provider_error(error)

    assert type(mapped) is expected


def test_mapped_errors_never_carry_provider_text_or_keys() -> None:
    leaked = RuntimeError("API key AIza-SECRET-VALUE rejected at /bot123:TOKEN")

    mapped = map_provider_error(leaked)

    assert "AIza" not in str(mapped)
    assert "SECRET" not in str(mapped)
    assert "TOKEN" not in str(mapped)
    assert mapped.__cause__ is None


# --- Session identity ----------------------------------------------------------


def test_each_classification_gets_a_fresh_session_id_for_the_same_chat() -> None:
    chat = as_chat_id(4242)

    first = new_session_id(chat)
    second = new_session_id(chat)

    # The ADK in-memory store rejects a reused session id, so a second photo from
    # the same chat (or a repair attempt) must never collide with an earlier one.
    assert first != second
    assert first.startswith("bouncer-4242-")
    assert second.startswith("bouncer-4242-")


# --- Gemini response schema (regression: 400 INVALID_ARGUMENT) ---------------


def test_agent_output_schema_has_no_additional_properties_for_gemini() -> None:
    """Gemini rejects ``additionalProperties`` in ``response_schema`` with a 400.

    ``VisionVerdict`` forbids extra keys, which pydantic emits as
    ``additionalProperties: false``. That key must not reach the provider; the
    strict check still runs locally on the parsed reply.
    """
    from telegram_documentaries.vision_client import AdkVisionClient

    client = AdkVisionClient(api_key="test-key-not-used-offline")
    runner, _ = client._ensure_runner()
    output_schema = runner.agent.output_schema

    assert isinstance(output_schema, dict)
    assert "additionalProperties" not in output_schema
    assert set(output_schema["required"]) == {
        "contains_human",
        "reason",
        "detected_subject",
    }


def test_local_validation_still_rejects_extra_keys_after_schema_relaxation() -> None:
    from pydantic import ValidationError

    reply = (
        '{"contains_human": true, "reason": "Welcome in.", '
        '"detected_subject": "person", "extra": "nope"}'
    )

    with pytest.raises(ValidationError):
        VisionVerdict.model_validate_json(reply)


# --- Provider failure diagnostics ---------------------------------------------


class ClientError(Exception):
    """Stand-in for ``google.genai.errors.ClientError`` (same name, has ``code``)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


async def test_provider_failure_logs_underlying_class_and_status_without_secrets(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram_documentaries.vision_client import AdkVisionClient

    secret = "AIza-SECRET-KEY-VALUE"
    client = AdkVisionClient(api_key=secret)

    async def failing_run(*_args: object) -> VisionVerdict:
        raise ClientError(400, f"INVALID_ARGUMENT key={secret}")

    monkeypatch.setattr(client, "_run_agent", failing_run)

    with (
        caplog.at_level(logging.ERROR, logger="telegram_documentaries"),
        pytest.raises(VisionUnavailable),
    ):
        await client.classify(_photo())

    messages = [record.getMessage() for record in caplog.records]
    provider_lines = [m for m in messages if "vision_provider_error" in m]
    assert len(provider_lines) == 1
    assert "error_type=ClientError" in provider_lines[0]
    assert "provider_status=400" in provider_lines[0]
    assert all(secret not in m for m in messages)
    assert all(b"\xff\xd8" not in m.encode() for m in messages)
