"""Offline tests for the Scripter stage, :func:`generate_script`.

The stage takes a dossier and a :class:`ScriptClient`, validates each reply into
:class:`Script`, and makes at most two attempts in total, shared between
validation repairs and provider retries. A validation failure triggers a repair
whose note names the rule and the word count but never the rejected text. A
timeout, 429, or 5xx triggers a plain retry after a short backoff. Every other
provider error fails at once. The fake client is scripted, so nothing here touches
the network or the ADK.
"""

from __future__ import annotations

import asyncio

import pytest
from fakes import FakeScriptClient, dossier
from pydantic import ValidationError

from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionUnavailable,
)
from telegram_documentaries.scripter import generate_script
from telegram_documentaries.scripter_client import ScriptClient
from telegram_documentaries.vision_client import RETRY_BACKOFF_SECONDS

REJECTED_MARKER = "REJECTED-PROSE-SENTINEL"


def _words(count: int, prefix: str = "w") -> str:
    """Return ``count`` plain, markdown-free words on one line."""
    return " ".join(f"{prefix}{index}" for index in range(count))


def _always_current() -> bool:
    return True


async def _generate(client: FakeScriptClient) -> str:
    return await generate_script(dossier(), client, still_current=_always_current)


def test_the_fake_satisfies_the_script_client_protocol() -> None:
    assert isinstance(FakeScriptClient([_words(60)]), ScriptClient)


# --- Happy path ---------------------------------------------------------------


async def test_a_valid_first_reply_is_returned_after_one_call() -> None:
    text = _words(75)
    client = FakeScriptClient([text])

    result = await _generate(client)

    assert result == text
    assert client.repair_notes == [None]


async def test_the_returned_text_is_stripped_of_surrounding_whitespace() -> None:
    client = FakeScriptClient([f"  \n{_words(70)}  \n"])

    result = await _generate(client)

    assert result == _words(70)


@pytest.mark.parametrize("count", [60, 90])
async def test_the_inclusive_word_bounds_are_accepted_without_repair(
    count: int,
) -> None:
    client = FakeScriptClient([_words(count)])

    result = await _generate(client)

    assert len(result.split()) == count
    assert client.repair_notes == [None]


# --- Validation failure and the single repair ---------------------------------


@pytest.mark.parametrize(
    ("bad_text", "rule", "count"),
    [
        (_words(59), "word_count_low", 59),
        (_words(91), "word_count_high", 91),
        (f"{_words(70)}\n{_words(5)}", "multi_paragraph", 75),
        (f"**{_words(70)}**", "markdown", 70),
        (f"- {_words(70)}", "markdown", 71),
        ("   ", "empty", 0),
    ],
)
async def test_a_rejected_first_reply_gets_exactly_one_repair_naming_rule_and_count(
    bad_text: str, rule: str, count: int
) -> None:
    client = FakeScriptClient([bad_text, _words(70)])

    result = await _generate(client)

    assert result == _words(70)
    assert len(client.repair_notes) == 2
    assert client.repair_notes[0] is None
    repair_note = client.repair_notes[1]
    assert repair_note is not None
    assert rule in repair_note
    assert str(count) in repair_note


async def test_the_repair_note_never_contains_the_rejected_text() -> None:
    rejected = f"{REJECTED_MARKER} {_words(58)}"  # 59 words, too short
    client = FakeScriptClient([rejected, _words(70)])

    await _generate(client)

    repair_note = client.repair_notes[1]
    assert repair_note is not None
    assert REJECTED_MARKER not in repair_note
    assert "w0" not in repair_note


async def test_a_still_invalid_repair_raises_and_stops_at_two_calls() -> None:
    client = FakeScriptClient([_words(59), _words(61, prefix="x") + " *"])

    with pytest.raises(ValidationError):
        await _generate(client)

    assert client.calls == 2


async def test_the_budget_is_never_more_than_two_attempts_in_total() -> None:
    client = FakeScriptClient([_words(5), _words(5), _words(70)])

    with pytest.raises(ValidationError):
        await _generate(client)

    assert client.calls == 2


# --- Provider retry: timeout, 429, and 5xx -------------------------------------


@pytest.mark.parametrize(
    "retryable",
    [ProviderTimeout(), RateLimited(), VisionUnavailable()],
    ids=["timeout", "rate_limited", "server_5xx"],
)
async def test_a_retryable_first_failure_is_retried_once_and_succeeds(
    retryable: Exception,
) -> None:
    client = FakeScriptClient([retryable, _words(70)])

    result = await _generate(client)

    assert result == _words(70)
    assert client.calls == 2
    assert client.repair_notes == [None, None]


@pytest.mark.parametrize(
    "retryable",
    [ProviderTimeout(), RateLimited(), VisionUnavailable()],
    ids=["timeout", "rate_limited", "server_5xx"],
)
async def test_the_same_retryable_error_on_both_attempts_raises_after_two_calls(
    retryable: Exception,
) -> None:
    client = FakeScriptClient([retryable, type(retryable)()])

    with pytest.raises(type(retryable)):
        await _generate(client)

    assert client.calls == 2


async def test_a_plain_provider_retry_waits_the_backoff_before_the_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakeScriptClient([ProviderTimeout(), _words(70)])

    await _generate(client)

    assert waits == [RETRY_BACKOFF_SECONDS]


async def test_a_validation_repair_waits_no_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = FakeScriptClient([_words(59), _words(70)])

    await _generate(client)

    assert waits == []


# --- Shared budget between validation and provider failures -------------------


async def test_a_validation_failure_then_a_timeout_raises_the_timeout() -> None:
    client = FakeScriptClient([_words(59), ProviderTimeout()])

    with pytest.raises(ProviderTimeout):
        await _generate(client)

    assert client.calls == 2


async def test_a_timeout_then_a_validation_failure_raises_after_two_calls() -> None:
    client = FakeScriptClient([ProviderTimeout(), _words(59)])

    with pytest.raises(ValidationError):
        await _generate(client)

    assert client.calls == 2


# --- Fail fast: non-retryable provider errors ----------------------------------


async def test_a_rejected_request_is_raised_at_once_with_one_call() -> None:
    client = FakeScriptClient([ProviderRejected(), _words(70)])

    with pytest.raises(ProviderRejected):
        await _generate(client)

    assert client.calls == 1


async def test_an_invalid_reply_from_the_client_is_not_retried() -> None:
    client = FakeScriptClient([InvalidResponse(), _words(70)])

    with pytest.raises(InvalidResponse):
        await _generate(client)

    assert client.calls == 1


# --- Paid-call guard ----------------------------------------------------------


async def test_a_reset_before_the_retry_makes_no_second_call() -> None:
    client = FakeScriptClient([ProviderTimeout(), _words(70)])
    answers = iter([True, False])  # current for attempt 1, reset before attempt 2

    with pytest.raises(StaleResult):
        await generate_script(dossier(), client, still_current=lambda: next(answers))

    assert client.calls == 1


async def test_a_reset_before_the_repair_makes_no_second_call() -> None:
    client = FakeScriptClient([_words(59), _words(70)])
    answers = iter([True, False])

    with pytest.raises(StaleResult):
        await generate_script(dossier(), client, still_current=lambda: next(answers))

    assert client.calls == 1
