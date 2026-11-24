"""Tests for the script step: the narration text sent after the portrait.

The store is real. The script client, the photo download, and both Telegram
senders are offline fakes. Every send lands in one ordered log, so a test can
assert that the portrait goes out before the script. Nothing here touches the
network, the ADK, or Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest
from fakes import (
    FakeDownloader,
    FakePortraitClient,
    FakeScriptClient,
    dossier,
)

from telegram_documentaries.errors import (
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    VisionUnavailable,
)
from telegram_documentaries.generation import run_generation
from telegram_documentaries.interview import restart
from telegram_documentaries.interview_models import Answer
from telegram_documentaries.messages import (
    GENERATION_FAILED_TEXT,
    OPENING_PROMPT,
    SCRIPT_FAILED_TEXT,
)
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.state import Phase, SessionStore

_CHAT = as_chat_id(4242)
_PHOTO_FILE = "photo-file-1"
_EXCHANGE = Answer(question="What keeps you up at night?", answer="Owls.")

#: A 75-word, single-paragraph, markdown-free script. The sentinel must never
#: appear in any log record.
SENTINEL = "SCRIPT-TEXT-SENTINEL"
SCRIPT = " ".join([SENTINEL, *(f"w{index}" for index in range(74))])
_TOO_SHORT = " ".join(f"s{index}" for index in range(59))

#: Upper bound for any wait in this file, so a hang fails fast instead of blocking.
_WAIT_SECONDS = 5.0


class _RecordingSends:
    """One ordered log of every Telegram send, for portraits and texts alike.

    ``photo_error`` makes the portrait send fail. ``text_errors`` are raised by the
    first text sends, in order, before any later send succeeds.
    """

    def __init__(
        self,
        *,
        photo_error: Exception | None = None,
        text_errors: Sequence[Exception] = (),
    ) -> None:
        self._photo_error = photo_error
        self._text_errors = list(text_errors)
        self.log: list[str] = []
        self.photos: list[ChatId] = []
        self.texts: list[tuple[ChatId, str]] = []

    async def photo(self, chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        if self._photo_error is not None:
            raise self._photo_error
        self.photos.append(chat_id)
        self.log.append("photo")

    async def text(self, chat_id: ChatId, text: str) -> None:
        if self._text_errors:
            raise self._text_errors.pop(0)
        self.texts.append((chat_id, text))
        self.log.append("text")


def _ready(store: SessionStore, chat_id: ChatId = _CHAT) -> None:
    """Drive a chat through a finished interview into ``DOSSIER_READY``."""
    store.commit_first_question(chat_id, _PHOTO_FILE, "What keeps you up at night?")
    store.commit_answer(chat_id, _EXCHANGE)
    store.commit_dossier(chat_id, dossier())


async def _run(
    store: SessionStore,
    sends: _RecordingSends,
    scripts: FakeScriptClient,
    *,
    chat_id: ChatId = _CHAT,
    portraits: FakePortraitClient | None = None,
) -> str | None:
    return await run_generation(
        chat_id,
        store,
        FakeDownloader(),
        portraits if portraits is not None else FakePortraitClient(),
        sends.photo,
        scripts=scripts,
        send_text=sends.text,
    )


# --- Happy path ---------------------------------------------------------------


async def test_the_script_is_sent_as_text_after_the_portrait_and_stored() -> None:
    store = SessionStore()
    _ready(store)
    sends = _RecordingSends()
    scripts = FakeScriptClient([SCRIPT])

    reply = await _run(store, sends, scripts)

    assert reply is None
    assert sends.log == ["photo", "text"]
    assert sends.texts == [(_CHAT, SCRIPT)]
    assert scripts.calls == 1


async def test_a_successful_script_step_leaves_the_chat_resting_in_delivering() -> None:
    store = SessionStore()
    _ready(store)

    await _run(store, _RecordingSends(), FakeScriptClient([SCRIPT]))

    state = store.get(_CHAT)
    assert state.phase is Phase.DELIVERING
    assert state.script == SCRIPT
    assert state.in_flight is False


# --- Failure fallback ---------------------------------------------------------


@pytest.mark.parametrize(
    ("outcomes", "calls"),
    [
        pytest.param([_TOO_SHORT, _TOO_SHORT], 2, id="validation_fails_twice"),
        pytest.param([ProviderTimeout(), ProviderTimeout()], 2, id="timeout_twice"),
        pytest.param([RateLimited(), RateLimited()], 2, id="rate_limited_twice"),
        pytest.param([VisionUnavailable(), VisionUnavailable()], 2, id="5xx_twice"),
        pytest.param([ProviderRejected()], 1, id="rejected_once"),
    ],
)
async def test_a_failed_script_step_sends_the_apology_and_keeps_the_portrait(
    outcomes: list[str | Exception],
    calls: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    store = SessionStore()
    _ready(store)
    sends = _RecordingSends()
    scripts = FakeScriptClient(outcomes)

    reply = await _run(store, sends, scripts)

    assert reply is None
    assert scripts.calls == calls
    assert sends.photos == [_CHAT]  # the portrait is kept and not re-sent
    assert sends.texts == [(_CHAT, SCRIPT_FAILED_TEXT)]
    state = store.get(_CHAT)
    assert state.script is None
    assert state.phase is Phase.DELIVERING
    assert state.in_flight is False
    assert SENTINEL not in caplog.text


async def test_no_script_call_is_made_when_the_portrait_send_fails() -> None:
    store = SessionStore()
    _ready(store)
    sends = _RecordingSends(photo_error=RuntimeError("telegram down"))
    scripts = FakeScriptClient([SCRIPT])

    reply = await _run(store, sends, scripts)

    assert reply == GENERATION_FAILED_TEXT
    assert scripts.calls == 0
    assert sends.texts == []
    assert store.get(_CHAT).script is None


async def test_a_failed_script_send_is_retried_once_as_the_apology_only() -> None:
    store = SessionStore()
    _ready(store)
    sends = _RecordingSends(text_errors=[RuntimeError("telegram down")])
    scripts = FakeScriptClient([SCRIPT])

    await _run(store, sends, scripts)

    # The script send failed once and the apology went out. The script is not
    # generated or sent again, but it stays stored for the Narrator.
    assert sends.texts == [(_CHAT, SCRIPT_FAILED_TEXT)]
    assert scripts.calls == 1
    assert store.get(_CHAT).script == SCRIPT
    assert store.get(_CHAT).phase is Phase.DELIVERING


# --- /restart during the script step ------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [SCRIPT, ProviderRejected()],
    ids=["late_success", "late_failure"],
)
async def test_a_restart_during_the_script_call_drops_the_late_result(
    outcome: str | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    store = SessionStore()
    _ready(store)
    sends = _RecordingSends()
    gate = asyncio.Event()
    scripts = FakeScriptClient([outcome], gate=gate)

    task = asyncio.create_task(_run(store, sends, scripts))
    try:
        # Wait until the script call is blocked. Bounded, so a script call that
        # never starts fails the test instead of spinning forever.
        async with asyncio.timeout(_WAIT_SECONDS):
            while scripts.calls == 0:
                await asyncio.sleep(0)

        restart_reply = restart(_CHAT, store)
        gate.set()
        reply = await asyncio.wait_for(task, timeout=_WAIT_SECONDS)
    finally:
        task.cancel()

    assert restart_reply.text == OPENING_PROMPT
    assert reply is None
    assert sends.photos == [_CHAT]  # only the portrait, sent before the restart
    assert sends.texts == []  # nothing sent after the reset, not even an apology
    state = store.get(_CHAT)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.script is None
    assert state.in_flight is False
    assert SENTINEL not in caplog.text
    assert "script_dropped" in caplog.text


# --- Isolation ----------------------------------------------------------------


async def test_a_script_for_one_chat_never_reaches_another_chat() -> None:
    store = SessionStore()
    other = as_chat_id(7777)
    _ready(store)
    store.commit_first_question(other, "photo-2", "What keeps you up at night?")
    sends = _RecordingSends()

    await _run(store, sends, FakeScriptClient([SCRIPT]))

    assert sends.texts == [(_CHAT, SCRIPT)]
    assert store.get(other).phase is Phase.INTERVIEWING
    assert store.get(other).script is None
