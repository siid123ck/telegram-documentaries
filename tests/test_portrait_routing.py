"""Routing tests for the Phase 4 generation path: text, photos, and ``/restart``.

Everything runs through :class:`UpdateRouter` with offline fakes for the vision,
interview, portrait, download, and photo-send seams. Each test asserts what the
user sees (reply text, portrait sends, and their order) and the resulting session
phase. Gates hold a model call or a send open, so a ``/restart`` or a duplicate
can land while work is in flight.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from fakes import (
    FakeDownloader,
    FakeInterviewClient,
    FakePortraitClient,
    FakeScriptClient,
    FakeVisionClient,
    RecordingPhotoSender,
    ask,
    dossier,
    finish,
    photo_update,
    text_update,
    verdict,
)
from telegram import Update

from telegram_documentaries.errors import ProviderTimeout
from telegram_documentaries.interview_models import InterviewTurn
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    GENERATION_FAILED_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    render_caption,
    render_dossier,
)
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.state import Phase, SessionStore

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)
#: Upper bound on loop yields while waiting for a flow step, so a bug fails fast.
_MAX_YIELDS = 10_000
_FIVE_QUESTIONS = [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?")]


@dataclass
class _Harness:
    """A router wired to offline fakes, plus one ordered record of what was sent."""

    router: UpdateRouter
    store: SessionStore
    vision: FakeVisionClient
    interview: FakeInterviewClient
    portraits: FakePortraitClient
    photos: RecordingPhotoSender
    download: FakeDownloader
    #: Every reply text and portrait send, in the order it happened.
    log: list[str] = field(default_factory=list)
    #: Reply texts only, for assertions that do not care about photo order.
    texts: list[str] = field(default_factory=list)


def _harness(
    *,
    script: list[InterviewTurn] | None = None,
    interview_gate: asyncio.Event | None = None,
    vision_gate: asyncio.Event | None = None,
    portraits: FakePortraitClient | None = None,
    photos: RecordingPhotoSender | None = None,
    scripts: FakeScriptClient | None = None,
) -> _Harness:
    harness: _Harness

    async def reply(update: Update, text: str) -> None:
        harness.texts.append(text)
        harness.log.append(text)

    interview = FakeInterviewClient(
        script if script is not None else [*_FIVE_QUESTIONS, finish()],
        gate=interview_gate,
    )
    portrait_client = portraits if portraits is not None else FakePortraitClient()
    photo_sender = photos if photos is not None else RecordingPhotoSender()
    store = SessionStore()
    download = FakeDownloader()
    vision = FakeVisionClient(result=verdict(human=True), gate=vision_gate)

    async def send_photo(chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        await photo_sender(chat_id, image_bytes, caption)
        harness.log.append(f"<portrait chat={int(chat_id)}>")

    async def send_text(chat_id: ChatId, text: str) -> None:
        harness.log.append(f"<script chat={int(chat_id)}>")
        harness.log.append(text)

    router = UpdateRouter(
        vision=vision,
        interview=interview,
        store=store,
        download=download,
        portraits=portrait_client,
        send_photo=send_photo,
        reply=reply,
        scripts=scripts,
        send_text=send_text,
    )
    harness = _Harness(
        router=router,
        store=store,
        vision=vision,
        interview=interview,
        portraits=portrait_client,
        photos=photo_sender,
        download=download,
    )
    return harness


async def _send_photo(
    h: _Harness, chat_id: ChatId = _CHAT, file_id: str = "p1"
) -> None:
    await h.router.handle_photo(photo_update(file_id, chat_id=int(chat_id)))


async def _send_text(
    h: _Harness, text: str, *, chat_id: ChatId = _CHAT, update_id: int = 1
) -> None:
    update = text_update(text, chat_id=int(chat_id), update_id=update_id)
    await h.router.handle_text(update)


async def _answer_all(h: _Harness, count: int = 5) -> None:
    """Start the interview and give ``count`` answers (the fifth finishes it)."""
    await _send_photo(h)
    for index in range(1, count + 1):
        await _send_text(h, f"answer {index}", update_id=index + 1)


async def _until(condition: Callable[[], bool]) -> None:
    """Yield to the loop until ``condition`` holds, or fail rather than hang."""
    for _ in range(_MAX_YIELDS):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the expected step in the flow never happened")


async def _wait_until_called(portraits: FakePortraitClient) -> None:
    """Wait until a generation has reached the (gated) portrait client."""
    await _until(lambda: portraits.calls > 0)


# --- The happy path through the router ----------------------------------------

_SCRIPT = " ".join(f"word{index}" for index in range(75))


async def test_the_script_is_sent_after_the_portrait_through_the_router() -> None:
    # The real router wiring: the script step must be reachable from handle_text.
    scripts = FakeScriptClient([_SCRIPT])
    h = _harness(scripts=scripts)

    await _answer_all(h)

    assert scripts.calls == 1
    assert h.log[-3:] == [
        f"<portrait chat={int(_CHAT)}>",
        f"<script chat={int(_CHAT)}>",
        _SCRIPT,
    ]
    assert h.store.get(_CHAT).phase is Phase.DELIVERING


async def test_the_dossier_text_is_sent_before_the_portrait() -> None:
    h = _harness()

    await _answer_all(h)

    assert h.log[-2] == render_dossier(dossier())
    assert h.log[-1] == f"<portrait chat={int(_CHAT)}>"


async def test_a_completed_interview_delivers_one_portrait_to_its_chat() -> None:
    h = _harness()

    await _answer_all(h)

    assert h.portraits.calls == 1
    assert [chat for chat, _, _ in h.photos.sent] == [_CHAT]
    _, _, caption = h.photos.sent[0]
    assert caption == render_caption("Meerkat")
    assert h.store.get(_CHAT).phase is Phase.DELIVERING
    assert h.store.get(_CHAT).in_flight is False


async def test_the_chat_rests_in_delivering_and_text_gets_the_already_done_nudge() -> (
    None
):
    h = _harness()
    await _answer_all(h)

    await _send_text(h, "one more thing", update_id=50)

    assert h.texts[-1] == ALREADY_DONE_TEXT
    assert h.portraits.calls == 1
    assert h.store.get(_CHAT).phase is Phase.DELIVERING


async def test_a_photo_in_delivering_gets_the_nudge_and_no_bouncer_call() -> None:
    h = _harness()
    await _answer_all(h)
    vision_calls = h.vision.calls

    await _send_photo(h, file_id="late-photo")

    assert h.texts[-1] == ALREADY_DONE_TEXT
    assert h.vision.calls == vision_calls
    assert h.portraits.calls == 1


async def test_restart_after_delivery_resets_the_chat_normally() -> None:
    h = _harness()
    await _answer_all(h)

    await h.router.handle_restart(text_update("/restart", update_id=99))

    assert h.texts[-1] == OPENING_PROMPT
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert h.store.get(_CHAT).dossier is None


# --- Failure path --------------------------------------------------------------


async def test_a_failed_generation_sends_the_apology_and_keeps_the_answers() -> None:
    h = _harness(portraits=FakePortraitClient([ProviderTimeout(), ProviderTimeout()]))

    await _answer_all(h)

    assert h.texts[-1] == GENERATION_FAILED_TEXT
    assert h.photos.sent == []
    state = h.store.get(_CHAT)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert len(state.answers) == 5
    assert state.dossier == dossier()


async def test_a_telegram_send_failure_apologises_and_returns_to_dossier_ready() -> (
    None
):
    h = _harness(photos=RecordingPhotoSender(error=RuntimeError("no")))

    await _answer_all(h)

    assert h.texts[-1] == GENERATION_FAILED_TEXT
    assert h.store.get(_CHAT).phase is Phase.DOSSIER_READY


async def test_an_unexpected_error_is_logged_and_never_escapes_the_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = _harness(portraits=FakePortraitClient([RuntimeError("a bug")]))

    with caplog.at_level(logging.ERROR):
        await _answer_all(h)

    assert h.store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert h.store.get(_CHAT).in_flight is False
    assert any(
        "router_error" in r.getMessage() and "RuntimeError" in r.getMessage()
        for r in caplog.records
    )


# --- /restart and duplicates during generation --------------------------------


async def test_restart_mid_generation_resets_at_once_and_drops_the_portrait() -> None:
    gate = asyncio.Event()
    h = _harness(portraits=FakePortraitClient(gate=gate))
    await _send_photo(h)
    for index in range(1, 5):
        await _send_text(h, f"answer {index}", update_id=index + 1)
    run = asyncio.create_task(_send_text(h, "answer 5", update_id=6))
    await _wait_until_called(h.portraits)

    await h.router.handle_restart(text_update("/restart", update_id=7))

    assert h.texts[-1] == OPENING_PROMPT
    assert ONE_AT_A_TIME_TEXT not in h.texts
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO

    gate.set()
    await run

    assert h.photos.sent == []
    assert h.texts[-1] == OPENING_PROMPT
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_a_photo_after_a_mid_generation_restart_starts_a_fresh_interview() -> (
    None
):
    gate = asyncio.Event()
    fresh = [*_FIVE_QUESTIONS, finish(), ask("Fresh?")]
    h = _harness(script=fresh, portraits=FakePortraitClient(gate=gate))
    await _send_photo(h)
    for index in range(1, 5):
        await _send_text(h, f"answer {index}", update_id=index + 1)
    run = asyncio.create_task(_send_text(h, "answer 5", update_id=6))
    await _wait_until_called(h.portraits)
    await h.router.handle_restart(text_update("/restart", update_id=7))

    await _send_photo(h, file_id="second-photo")

    state = h.store.get(_CHAT)
    assert state.phase is Phase.INTERVIEWING
    assert state.outstanding_question == "Fresh?"
    assert state.answers == []
    gate.set()
    await run
    assert h.photos.sent == []


async def test_a_duplicate_text_during_generation_is_nudged_and_not_regenerated() -> (
    None
):
    gate = asyncio.Event()
    h = _harness(portraits=FakePortraitClient(gate=gate))
    await _send_photo(h)
    for index in range(1, 5):
        await _send_text(h, f"answer {index}", update_id=index + 1)
    run = asyncio.create_task(_send_text(h, "answer 5", update_id=6))
    await _wait_until_called(h.portraits)

    await _send_text(h, "are you still there?", update_id=8)

    assert h.texts[-1] == ONE_AT_A_TIME_TEXT
    assert h.portraits.calls == 1
    gate.set()
    await run
    assert len(h.photos.sent) == 1


async def test_a_duplicate_photo_during_generation_is_nudged_without_a_download() -> (
    None
):
    gate = asyncio.Event()
    h = _harness(portraits=FakePortraitClient(gate=gate))
    await _send_photo(h)
    for index in range(1, 5):
        await _send_text(h, f"answer {index}", update_id=index + 1)
    run = asyncio.create_task(_send_text(h, "answer 5", update_id=6))
    await _wait_until_called(h.portraits)
    downloads = len(h.download.requested)

    await _send_photo(h, file_id="duplicate-photo")

    assert h.texts[-1] == ONE_AT_A_TIME_TEXT
    assert len(h.download.requested) == downloads
    assert h.portraits.calls == 1
    gate.set()
    await run


# --- Interview turns that land after a /restart --------------------------------


async def test_an_answer_whose_model_call_outlives_a_restart_is_dropped_silently() -> (
    None
):
    gate = asyncio.Event()
    gate.set()
    h = _harness(interview_gate=gate)
    await _send_photo(h)
    gate.clear()
    answer = asyncio.create_task(_send_text(h, "a late answer", update_id=2))
    await _until(lambda: h.interview.calls >= 2)

    await h.router.handle_restart(text_update("/restart", update_id=3))
    gate.set()
    await answer

    assert h.texts[-1] == OPENING_PROMPT
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert h.store.get(_CHAT).answers == []
    assert h.texts.count(OPENING_PROMPT) == 1


async def test_a_photo_classified_after_a_restart_starts_no_interview() -> None:
    gate = asyncio.Event()
    # The classification is the call that outlives the reset.
    h = _harness(vision_gate=gate)
    photo = asyncio.create_task(_send_photo(h))
    await _until(lambda: h.vision.calls > 0)

    await h.router.handle_restart(text_update("/restart", update_id=3))
    gate.set()
    await photo

    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert h.interview.calls == 0
    assert h.texts == [OPENING_PROMPT]


# --- Isolation between chats ---------------------------------------------------


async def test_a_portrait_completes_for_one_chat_while_another_is_mid_interview() -> (
    None
):
    gate = asyncio.Event()
    # Turn order: A's photo and four answers, B's photo, A's fifth answer (which
    # finishes), then B's answer. Each model call consumes the next scripted turn.
    script = [*_FIVE_QUESTIONS, ask("B1?"), finish(), ask("B2?")]
    h = _harness(script=script, portraits=FakePortraitClient(gate=gate))
    await _send_photo(h, _CHAT)
    for index in range(1, 5):
        await _send_text(h, f"a{index}", chat_id=_CHAT, update_id=index + 1)
    await _send_photo(h, _OTHER, file_id="b-photo")
    run = asyncio.create_task(_send_text(h, "a5", chat_id=_CHAT, update_id=20))
    await _wait_until_called(h.portraits)
    await _send_text(h, "b1", chat_id=_OTHER, update_id=21)

    gate.set()
    await run

    assert [chat for chat, _, _ in h.photos.sent] == [_CHAT]
    assert h.store.get(_CHAT).phase is Phase.DELIVERING
    assert h.store.get(_OTHER).phase is Phase.INTERVIEWING
    assert h.store.get(_OTHER).outstanding_question == "B2?"


async def test_a_restart_in_one_chat_leaves_the_other_chats_interview_intact() -> None:
    gate = asyncio.Event()
    # Turn order: A's photo and four answers, B's photo and one answer, A's fifth
    # answer (which finishes), then B's second answer.
    script = [*_FIVE_QUESTIONS, ask("B1?"), ask("B2?"), finish(), ask("B3?")]
    h = _harness(script=script, portraits=FakePortraitClient(gate=gate))
    await _send_photo(h, _CHAT)
    for index in range(1, 5):
        await _send_text(h, f"a{index}", chat_id=_CHAT, update_id=index + 1)
    await _send_photo(h, _OTHER, file_id="b-photo")
    await _send_text(h, "b1", chat_id=_OTHER, update_id=30)
    run = asyncio.create_task(_send_text(h, "a5", chat_id=_CHAT, update_id=31))
    await _wait_until_called(h.portraits)

    restart = text_update("/restart", chat_id=int(_CHAT), update_id=32)
    await h.router.handle_restart(restart)
    await _send_text(h, "b2", chat_id=_OTHER, update_id=33)
    gate.set()
    await run

    assert h.photos.sent == []
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    other = h.store.get(_OTHER)
    assert other.phase is Phase.INTERVIEWING
    assert other.outstanding_question == "B3?"
    assert len(other.answers) == 2


async def test_a_late_provider_failure_after_a_restart_sends_no_apology() -> None:
    gate = asyncio.Event()
    timeouts = [ProviderTimeout(), ProviderTimeout()]
    h = _harness(portraits=FakePortraitClient(timeouts, gate=gate))
    await _answer_all(h, count=4)
    run = asyncio.create_task(_send_text(h, "answer 5", update_id=6))
    await _wait_until_called(h.portraits)

    await h.router.handle_restart(text_update("/restart", update_id=7))
    gate.set()
    await run

    assert GENERATION_FAILED_TEXT not in h.texts
    assert h.texts[-1] == OPENING_PROMPT
    assert h.photos.sent == []
    assert h.store.get(_CHAT).phase is Phase.AWAITING_PHOTO
