"""Tests for the converter stage: one validated portrait, bounded retries.

The portrait client is the offline fake from ``fakes``, so nothing here touches
the network. The stage's contract is the returned :class:`Portrait`, the number
of provider attempts, and the typed error that escapes when the run fails.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import pytest
from fakes import PORTRAIT_BYTES, FakePortraitClient, dossier

from telegram_documentaries.converter import generate_portrait
from telegram_documentaries.converter_client import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
)
from telegram_documentaries.converter_models import PNG_MAGIC, Portrait
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.models import MAX_PHOTO_BYTES, as_chat_id

_CHAT = as_chat_id(4242)
_PHOTO = b"\xff\xd8\xff" + b"user-photo-bytes"
_SENTINEL_REPLY = "SENTINEL-REPLY-TEXT-41c9"
_SENTINEL_PHOTO = b"SENTINEL-PHOTO-BYTES-9e27"


def _always_current() -> bool:
    """The run is never abandoned: the default for tests that are not about /restart."""
    return True


#: The three failures the stage is allowed to retry (timeout, 429, 5xx).
_RETRYABLE: list[type[VisionError]] = [ProviderTimeout, RateLimited, VisionUnavailable]

#: Payloads that are not a usable image. Each must fail with no retry.
_BAD_PAYLOADS: list[bytes] = [
    b"Sorry, I cannot draw that.",
    b"",
    b"GIF89a-wrong-magic",
    PNG_MAGIC[:4],
    PNG_MAGIC + b"0" * MAX_PHOTO_BYTES,
]


async def test_one_valid_png_becomes_one_portrait_after_one_attempt() -> None:
    client = FakePortraitClient([PORTRAIT_BYTES])

    portrait = await generate_portrait(
        _CHAT, _PHOTO, dossier(), client, still_current=_always_current
    )

    assert isinstance(portrait, Portrait)
    assert portrait.image_bytes == PORTRAIT_BYTES
    assert portrait.mime_type == "image/png"
    assert client.calls == 1


async def test_the_original_photo_bytes_reach_the_provider_unchanged() -> None:
    client = FakePortraitClient([PORTRAIT_BYTES])

    await generate_portrait(
        _CHAT, _PHOTO, dossier(), client, still_current=_always_current
    )

    assert client.photos == [_PHOTO]


async def test_no_repair_note_is_ever_sent_for_a_portrait() -> None:
    client = FakePortraitClient([PORTRAIT_BYTES])

    await generate_portrait(
        _CHAT, _PHOTO, dossier(), client, still_current=_always_current
    )

    assert client.repair_notes == [None]


@pytest.mark.parametrize("error_type", _RETRYABLE, ids=lambda t: t.__name__)
async def test_a_retryable_failure_then_success_returns_the_portrait(
    error_type: type[VisionError],
) -> None:
    client = FakePortraitClient([error_type(), PORTRAIT_BYTES])

    portrait = await generate_portrait(
        _CHAT, _PHOTO, dossier(), client, still_current=_always_current
    )

    assert portrait.image_bytes == PORTRAIT_BYTES
    assert client.calls == 2


@pytest.mark.parametrize("error_type", _RETRYABLE, ids=lambda t: t.__name__)
async def test_the_same_retryable_failure_twice_raises_after_exactly_two_attempts(
    error_type: type[VisionError],
) -> None:
    client = FakePortraitClient([error_type(), error_type()])

    with pytest.raises(error_type):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert client.calls == MAX_ATTEMPTS == 2


async def test_a_rejected_request_fails_fast_after_one_attempt() -> None:
    client = FakePortraitClient([ProviderRejected(), PORTRAIT_BYTES])

    with pytest.raises(ProviderRejected):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert client.calls == 1


@pytest.mark.parametrize("payload", _BAD_PAYLOADS, ids=lambda p: f"len-{len(p)}")
async def test_an_invalid_payload_raises_invalid_response_with_no_retry(
    payload: bytes,
) -> None:
    client = FakePortraitClient([payload, PORTRAIT_BYTES])

    with pytest.raises(InvalidResponse):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert client.calls == 1


async def test_a_text_only_reply_is_rejected_and_never_retried() -> None:
    client = FakePortraitClient([b"Sorry, I cannot draw that.", PORTRAIT_BYTES])

    with pytest.raises(InvalidResponse):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert client.calls == 1


async def test_the_retry_waits_the_configured_backoff_before_the_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakePortraitClient([ProviderTimeout(), PORTRAIT_BYTES])

    await generate_portrait(
        _CHAT, _PHOTO, dossier(), client, still_current=_always_current
    )

    assert waits == [RETRY_BACKOFF_SECONDS]


async def test_a_fail_fast_error_does_not_wait_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakePortraitClient([ProviderRejected()])

    with pytest.raises(ProviderRejected):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert waits == []


async def test_a_failure_logs_the_error_type_and_attempt_count_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakePortraitClient([ProviderTimeout(), ProviderTimeout()])

    with caplog.at_level(logging.ERROR), pytest.raises(ProviderTimeout):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("attempts=2" in message for message in errors)
    assert any("error_type=ProviderTimeout" in message for message in errors)


async def test_an_invalid_payload_logs_at_error_without_the_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = _SENTINEL_REPLY.encode()
    client = FakePortraitClient([reply])

    with caplog.at_level(logging.DEBUG), pytest.raises(InvalidResponse):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors
    assert _SENTINEL_REPLY not in caplog.text
    assert base64.b64encode(reply).decode() not in caplog.text


async def test_the_photo_and_the_reply_bytes_never_reach_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    photo = _SENTINEL_PHOTO
    client = FakePortraitClient([ProviderRejected()])

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderRejected):
        await generate_portrait(
            _CHAT, photo, dossier(), client, still_current=_always_current
        )

    assert _SENTINEL_PHOTO.decode() not in caplog.text
    assert base64.b64encode(photo).decode() not in caplog.text


async def test_a_successful_run_logs_no_error_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakePortraitClient([PORTRAIT_BYTES])

    with caplog.at_level(logging.INFO):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=_always_current
        )

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- /restart while the run is in flight --------------------------------------


async def test_a_run_abandoned_before_the_first_call_makes_no_model_call() -> None:
    client = FakePortraitClient([PORTRAIT_BYTES])

    with pytest.raises(StaleResult):
        await generate_portrait(
            _CHAT, _PHOTO, dossier(), client, still_current=lambda: False
        )

    assert client.calls == 0


async def test_a_run_abandoned_during_a_retryable_failure_makes_no_retry() -> None:
    alive = {"current": True}

    def restart_during_the_call() -> None:
        alive["current"] = False  # /restart lands while the first call is in flight

    client = FakePortraitClient(
        [ProviderTimeout(), PORTRAIT_BYTES], on_call=restart_during_the_call
    )

    with pytest.raises(StaleResult):
        await generate_portrait(
            _CHAT,
            _PHOTO,
            dossier(),
            client,
            still_current=lambda: alive["current"],
        )

    assert client.calls == 1


def test_the_generation_stage_is_a_coroutine_function() -> None:
    assert asyncio.iscoroutinefunction(generate_portrait)
