"""Offline tests for routing every update by ``(phase, payload type)``.

The router is wired to fakes for the vision provider, the interview provider,
the file download, and the reply sender, so no test touches Telegram or the
network. Each test builds a local update, sends it through the router, and
asserts on what would be sent and on the resulting session state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from fakes import (
    FakeDownloader,
    FakeInterviewClient,
    FakeVisionClient,
    GatedDownloader,
    RecordingSender,
    ask,
    dossier,
    finish,
    photo_update,
    text_update,
    verdict,
)
from telegram import Chat, Message, Sticker, Update, Voice

from telegram_documentaries.errors import ProviderTimeout
from telegram_documentaries.gateway import REPLY_TEXT
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    PHOTO_MID_INTERVIEW_TEXT,
    REJECTION_TEMPLATE,
    RESEND_ANSWER_TEXT,
    RESEND_TEXT,
    render_dossier,
)
from telegram_documentaries.models import as_chat_id
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.state import Phase, SessionStore

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)


def _router(
    *,
    vision: FakeVisionClient | None = None,
    interview: FakeInterviewClient | None = None,
    store: SessionStore | None = None,
    download: FakeDownloader | GatedDownloader | None = None,
    sender: RecordingSender | None = None,
) -> tuple[
    UpdateRouter, RecordingSender, SessionStore, FakeVisionClient, FakeInterviewClient
]:
    vision = vision if vision is not None else FakeVisionClient(result=verdict())
    interview = (
        interview if interview is not None else FakeInterviewClient([ask("First?")])
    )
    store = store if store is not None else SessionStore()
    sender = sender if sender is not None else RecordingSender()
    router = UpdateRouter(
        vision=vision,
        interview=interview,
        store=store,
        download=download if download is not None else FakeDownloader(),
        reply=sender,
    )
    return router, sender, store, vision, interview


async def _send_photo(router: UpdateRouter, file_id: str = "p1") -> None:
    await router.handle_photo(photo_update(file_id))


async def _send_text(router: UpdateRouter, text: str, update_id: int = 1) -> None:
    await router.handle_text(text_update(text, update_id=update_id))


async def _start_interview(
    router: UpdateRouter, store: SessionStore, sender: RecordingSender
) -> None:
    await _send_photo(router)
    assert store.get(_CHAT).phase is Phase.INTERVIEWING, sender.sent


# --- Photo routing by phase --------------------------------------------------


async def test_a_passing_photo_sends_the_first_question_not_hey_mate() -> None:
    router, sender, store, _, _ = _router()

    await _send_photo(router)

    assert sender.sent == ["First?"]
    assert REPLY_TEXT not in sender.sent
    assert store.get(_CHAT).phase is Phase.INTERVIEWING


async def test_a_rejected_photo_sends_the_rejection_and_stays_awaiting_photo() -> None:
    vision = FakeVisionClient(result=verdict(human=False, subject="cat"))
    router, sender, store, _, interview = _router(vision=vision)

    await _send_photo(router)

    assert sender.sent == [REJECTION_TEMPLATE.format(subject="cat")]
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert interview.calls == 0


async def test_a_photo_mid_interview_gets_the_nudge_without_the_bouncer() -> None:
    router, sender, store, vision, _ = _router()
    await _start_interview(router, store, sender)
    sent_before = len(sender.sent)

    await _send_photo(router, "second-photo")

    assert sender.sent[sent_before:] == [PHOTO_MID_INTERVIEW_TEXT]
    assert vision.calls == 1
    assert store.get(_CHAT).phase is Phase.INTERVIEWING
    assert store.get(_CHAT).outstanding_question == "First?"


async def test_a_photo_in_dossier_ready_gets_the_already_done_nudge() -> None:
    interview = FakeInterviewClient(
        [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?"), finish()]
    )
    router, sender, store, vision, _ = _router(interview=interview)
    await _start_interview(router, store, sender)
    for index in range(1, 6):
        await _send_text(router, f"answer {index}", update_id=index + 1)
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    sent_before = len(sender.sent)

    await _send_photo(router, "late-photo")

    assert sender.sent[sent_before:] == [ALREADY_DONE_TEXT]
    assert vision.calls == 1


# --- Text routing by phase ---------------------------------------------------


async def test_text_while_awaiting_a_photo_gets_the_phase_one_reply() -> None:
    router, sender, store, _, interview = _router()

    await _send_text(router, "hello there")

    assert sender.sent == [REPLY_TEXT] == ["hey mate!"]
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert interview.calls == 0


async def test_whitespace_only_text_while_awaiting_a_photo_gets_no_reply() -> None:
    router, sender, _, _, _ = _router()

    await _send_text(router, "   ")

    assert sender.sent == []


async def test_text_while_interviewing_is_the_next_answer() -> None:
    router, sender, store, _, interview = _router(
        interview=FakeInterviewClient([ask("Where do you nap?"), ask("And then?")])
    )
    await _start_interview(router, store, sender)

    await _send_text(router, "On a sofa.")

    assert sender.sent == ["Where do you nap?", "And then?"]
    assert store.get(_CHAT).answers[0].answer == "On a sofa."
    assert interview.calls == 2


async def test_empty_text_while_interviewing_is_nudged_and_not_stored() -> None:
    router, sender, store, _, interview = _router()
    await _start_interview(router, store, sender)

    await _send_text(router, "  ")

    assert sender.sent[-1] == "I didn't catch anything there — tell me a little more."
    assert store.get(_CHAT).answers == []
    assert interview.calls == 1


async def test_text_in_dossier_ready_gets_the_already_done_nudge() -> None:
    interview = FakeInterviewClient(
        [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?"), finish()]
    )
    router, sender, store, _, _ = _router(interview=interview)
    await _start_interview(router, store, sender)
    for index in range(1, 6):
        await _send_text(router, f"answer {index}", update_id=index + 1)
    sent_before = len(sender.sent)

    await _send_text(router, "one more thing")

    assert sender.sent[sent_before:] == [ALREADY_DONE_TEXT]
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert store.get(_CHAT).dossier is not None


async def test_a_completed_interview_sends_one_rendered_dossier() -> None:
    interview = FakeInterviewClient(
        [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?"), finish()]
    )
    router, sender, store, _, _ = _router(interview=interview)
    await _start_interview(router, store, sender)
    for index in range(1, 6):
        await _send_text(router, f"answer {index}", update_id=index + 1)

    assert sender.sent[-1] == render_dossier(dossier())


async def test_a_provider_failure_while_answering_sends_the_resend_nudge() -> None:
    interview = FakeInterviewClient([ask("Q1?"), ProviderTimeout(), ProviderTimeout()])
    router, sender, store, _, _ = _router(interview=interview)
    await _start_interview(router, store, sender)

    await _send_text(router, "My answer.")

    assert sender.sent[-1] == RESEND_ANSWER_TEXT
    assert store.get(_CHAT).answers == []
    assert store.get(_CHAT).outstanding_question == "Q1?"


async def test_a_failed_first_question_sends_the_resend_photo_nudge() -> None:
    interview = FakeInterviewClient([ProviderTimeout(), ProviderTimeout()])
    router, sender, store, _, _ = _router(interview=interview)

    await _send_photo(router)

    assert sender.sent == [RESEND_TEXT]
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert store.get(_CHAT).photo_file_id is None


async def test_a_bouncer_failure_sends_the_resend_photo_nudge() -> None:
    vision = FakeVisionClient(errors=[ProviderTimeout(), ProviderTimeout()])
    router, sender, store, _, interview = _router(vision=vision)

    await _send_photo(router)

    assert sender.sent == [RESEND_TEXT]
    assert interview.calls == 0
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


# --- Restart in every driven phase -------------------------------------------


async def test_restart_while_awaiting_a_photo_sends_the_opening_prompt() -> None:
    router, sender, store, _, _ = _router()

    await router.handle_restart(text_update("/restart"))

    assert sender.sent == [OPENING_PROMPT]
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_restart_mid_interview_clears_the_session_and_sends_the_opening() -> None:
    router, sender, store, _, _ = _router(
        interview=FakeInterviewClient([ask("Q1?"), ask("Q2?"), ask("Q3?")])
    )
    await _start_interview(router, store, sender)
    await _send_text(router, "a1", update_id=2)

    await router.handle_restart(text_update("/restart", update_id=3))

    assert sender.sent[-1] == OPENING_PROMPT
    state = store.get(_CHAT)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.answers == []
    assert state.outstanding_question is None
    assert state.photo_file_id is None


async def test_restart_after_the_dossier_is_ready_discards_it() -> None:
    interview = FakeInterviewClient(
        [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?"), finish()]
    )
    router, sender, store, _, _ = _router(interview=interview)
    await _start_interview(router, store, sender)
    for index in range(1, 6):
        await _send_text(router, f"answer {index}", update_id=index + 1)

    await router.handle_restart(text_update("/restart", update_id=99))

    assert sender.sent[-1] == OPENING_PROMPT
    assert store.get(_CHAT).dossier is None
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_restart_during_a_model_call_is_nudged_and_changes_nothing() -> None:
    router, sender, store, _, _ = _router()
    store.begin_turn(_CHAT)

    await router.handle_restart(text_update("/restart"))

    assert sender.sent == [ONE_AT_A_TIME_TEXT]
    assert store.get(_CHAT).in_flight is True


# --- The in-flight guard covers every update kind -----------------------------


async def test_text_during_a_photo_download_gets_the_one_at_a_time_nudge() -> None:
    download = GatedDownloader()
    router, sender, store, vision, _ = _router(download=download)
    first = asyncio.create_task(router.handle_photo(photo_update("p1", update_id=1)))
    while download.requested != ["p1"]:
        await asyncio.sleep(0)

    await _send_text(router, "impatient text")
    download.release.set()
    await first

    assert sender.sent[0] == ONE_AT_A_TIME_TEXT
    assert store.get(_CHAT).phase is Phase.INTERVIEWING
    assert vision.calls == 1


async def test_a_photo_during_an_answer_call_gets_the_one_at_a_time_nudge() -> None:
    release = asyncio.Event()
    interview = FakeInterviewClient([ask("Q1?"), ask("Q2?")], gate=release)
    router, sender, store, vision, _ = _router(interview=interview)
    release.set()
    await _start_interview(router, store, sender)
    release.clear()

    answer = asyncio.create_task(_send_text(router, "a reply"))
    while interview.calls < 2:
        await asyncio.sleep(0)

    await router.handle_photo(photo_update("late", update_id=5))
    release.set()
    await answer

    assert sender.sent[-2:] == [ONE_AT_A_TIME_TEXT, "Q2?"]
    assert vision.calls == 1


# --- Wrong-payload guards and malformed updates ------------------------------


def _sticker_update() -> Update:
    sticker = Sticker(
        file_id="s",
        file_unique_id="su",
        width=1,
        height=1,
        is_animated=False,
        is_video=False,
        type="regular",
    )
    message = Message(
        message_id=3,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        chat=Chat(id=4242, type="private"),
        sticker=sticker,
    )
    return Update(update_id=3, message=message)


def _voice_update() -> Update:
    voice = Voice(file_id="v", file_unique_id="vu", duration=1)
    message = Message(
        message_id=4,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        chat=Chat(id=4242, type="private"),
        voice=voice,
    )
    return Update(update_id=4, message=message)


@pytest.mark.parametrize(
    "update",
    [
        Update(update_id=1),
        _sticker_update(),
        _voice_update(),
        text_update("edited body", chat_id=-5),
    ],
    ids=["no-message", "sticker", "voice-note", "negative-chat"],
)
async def test_noise_and_malformed_updates_are_ignored_without_an_exception(
    update: Update,
) -> None:
    router, sender, store, vision, interview = _router()

    await router.handle_text(update)
    await router.handle_photo(update)

    assert sender.sent == []
    assert vision.calls == 0
    assert interview.calls == 0
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_a_restart_with_no_routable_chat_is_ignored() -> None:
    router, sender, _, _, _ = _router()

    await router.handle_restart(Update(update_id=1))
    await router.handle_restart(text_update("/restart", chat_id=-5))

    assert sender.sent == []


async def test_an_unexpected_error_is_logged_and_the_next_update_is_still_served(
    caplog: pytest.LogCaptureFixture,
) -> None:
    vision = FakeVisionClient(result=verdict(human=True), errors=[RuntimeError("boom")])
    router, sender, store, _, _ = _router(
        vision=vision,
        interview=FakeInterviewClient([ask("Still here?")]),
    )

    with caplog.at_level(logging.ERROR):
        await router.handle_photo(photo_update("first", update_id=1))
        await router.handle_photo(photo_update("second", update_id=2))

    assert sender.sent == ["Still here?"]
    assert store.get(_CHAT).phase is Phase.INTERVIEWING
    assert any(
        record.levelno == logging.ERROR
        and "error_type=RuntimeError" in record.getMessage()
        for record in caplog.records
    )


async def test_a_handler_crash_during_text_never_escapes_the_router() -> None:
    interview = FakeInterviewClient([ask("Q1?"), RuntimeError("interview exploded")])
    router, sender, store, _, _ = _router(interview=interview)
    await _start_interview(router, store, sender)

    await _send_text(router, "a reply")

    assert store.get(_CHAT).answers == []
    assert store.get(_CHAT).in_flight is False
    assert store.get(_CHAT).outstanding_question == "Q1?"


async def test_two_chats_route_independently_through_the_same_router() -> None:
    interview = FakeInterviewClient([ask("A-first?"), ask("B-first?"), ask("A-next?")])
    router, sender, store, _, _ = _router(interview=interview)

    await router.handle_photo(photo_update("a", chat_id=4242, update_id=1))
    await router.handle_photo(photo_update("b", chat_id=7777, update_id=2))
    await router.handle_text(text_update("alpha reply", chat_id=4242, update_id=3))

    assert store.get(_CHAT).answers[0].answer == "alpha reply"
    assert store.get(_OTHER).answers == []
    assert store.get(_OTHER).outstanding_question == "B-first?"
    assert sender.sent == ["A-first?", "B-first?", "A-next?"]


# --- Phase 1 text reply constant ---------------------------------------------


def test_the_awaiting_photo_text_reply_is_the_imported_phase_one_constant() -> None:
    assert REPLY_TEXT == "hey mate!"
