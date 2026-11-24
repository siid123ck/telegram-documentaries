"""Offline tests for the interview orchestrator (task group 5).

The orchestrator is driven with :class:`FakeInterviewClient`, so no test imports
the ADK, opens a socket, or reads ``.env``. Each test scripts the model's turns,
runs the public functions (``start_interview``, ``answer_question``,
``restart``), and asserts on the replies and on the resulting session state.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fakes import FakeInterviewClient, ask, dossier, finish

from telegram_documentaries.errors import InvalidResponse, ProviderTimeout, RateLimited
from telegram_documentaries.interview import (
    Reply,
    answer_question,
    restart,
    start_interview,
)
from telegram_documentaries.interview_models import MAX_ANSWER_CHARS
from telegram_documentaries.messages import (
    ANSWER_TOO_LONG_TEXT,
    EMPTY_ANSWER_TEXT,
    NO_QUESTION_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    render_dossier,
)
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.prompts import INTERVIEW_FINISH_NOTE
from telegram_documentaries.state import Phase, SessionState, SessionStore

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)
_PHOTO = "photo-file-1"


def _questions(count: int) -> list[str]:
    return [f"Question number {i}?" for i in range(1, count + 1)]


async def _start(
    client: FakeInterviewClient, store: SessionStore, chat: ChatId = _CHAT
) -> Reply:
    return await start_interview(chat, _PHOTO, client, store)


async def _answer(
    client: FakeInterviewClient,
    store: SessionStore,
    text: str,
    chat: ChatId = _CHAT,
) -> Reply:
    return await answer_question(chat, text, client, store)


def _outstanding(store: SessionStore, chat: ChatId = _CHAT) -> str | None:
    return store.get(chat).outstanding_question


async def _run_interview(
    store: SessionStore,
    client: FakeInterviewClient,
    answers: list[str],
    chat: ChatId = _CHAT,
) -> list[Reply]:
    """Answer every question in ``answers`` in order and collect the replies."""
    replies = [await _start(client, store, chat)]
    for text in answers:
        replies.append(await _answer(client, store, text, chat))
    return replies


# --- Full interview: 5 questions --------------------------------------------


async def test_a_passing_photo_sends_the_first_question_and_enters_interviewing() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient([ask("Where do you sleep?")])

    reply = await _start(client, store)

    assert reply.text == "Where do you sleep?"
    assert reply.phase is Phase.INTERVIEWING
    state = store.get(_CHAT)
    assert state.phase is Phase.INTERVIEWING
    assert state.photo_file_id == _PHOTO
    assert state.outstanding_question == "Where do you sleep?"
    assert state.answers == []


async def test_the_first_question_is_generated_from_an_empty_history() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Where do you sleep?")])

    await _start(client, store)

    assert client.histories == [[]]
    assert client.notes == [None]


async def test_a_five_question_interview_ends_with_a_dossier_after_five_answers() -> (
    None
):
    store = SessionStore()
    questions = _questions(5)
    client = FakeInterviewClient(
        [
            ask(questions[0]),
            ask(questions[1]),
            ask(questions[2]),
            ask(questions[3]),
            ask(questions[4]),
            finish(),
        ]
    )

    replies = await _run_interview(store, client, [f"answer {i}" for i in range(1, 6)])

    assert [reply.text for reply in replies[:5]] == questions
    assert replies[-1].phase is Phase.DOSSIER_READY
    assert replies[-1].text == render_dossier(dossier())
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert len(store.get(_CHAT).answers) == 5


async def test_each_outbound_message_carries_exactly_one_question() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("First?"), ask("Second?")])

    first = await _start(client, store)
    second = await _answer(client, store, "A reply.")

    assert first.text == "First?"
    assert second.text == "Second?"
    for reply in (first, second):
        assert reply.text.count("?") == 1


async def test_a_later_question_is_built_from_the_committed_answers_so_far() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?"), ask("Q3?")])

    await _start(client, store)
    await _answer(client, store, "Owls at night.")
    await _answer(client, store, "Under a hedge.")

    assert client.histories[0] == []
    assert [(a.question, a.answer) for a in client.histories[1]] == [
        ("Q1?", "Owls at night.")
    ]
    assert [(a.question, a.answer) for a in client.histories[2]] == [
        ("Q1?", "Owls at night."),
        ("Q2?", "Under a hedge."),
    ]


# --- Full interview: 7 questions and the range --------------------------------


async def test_a_seven_question_interview_forces_finish_at_seven_answers() -> None:
    store = SessionStore()
    client = FakeInterviewClient([*[ask(f"Q{i}?") for i in range(1, 8)], finish()])

    replies = await _run_interview(store, client, [f"a{i}" for i in range(1, 8)])

    assert replies[-1].phase is Phase.DOSSIER_READY
    assert client.calls == 8  # start + six asks + the forced finish
    assert len(client.histories[-1]) == 7
    assert client.notes[-1] == INTERVIEW_FINISH_NOTE


async def test_the_model_may_finish_at_six_answers() -> None:
    store = SessionStore()
    client = FakeInterviewClient([*[ask(f"Q{i}?") for i in range(1, 7)], finish()])

    replies = await _run_interview(store, client, [f"a{i}" for i in range(1, 7)])

    assert replies[-1].phase is Phase.DOSSIER_READY
    assert len(store.get(_CHAT).answers) == 6


async def test_a_finish_before_five_answers_gets_one_repair_then_is_refused() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), finish(), finish()])
    await _start(client, store)

    with pytest.raises(InvalidResponse):
        await _answer(client, store, "Only one answer.")

    assert client.calls == 3  # start, the early finish, and exactly one repair
    assert client.notes[1] is None
    assert client.notes[2] is not None


async def test_a_valid_finish_on_the_repair_is_accepted_as_the_next_turn() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), finish(), ask("Q2?")])
    await _start(client, store)

    reply = await _answer(client, store, "Only one answer.")

    assert reply.text == "Q2?"
    assert client.calls == 3


async def test_an_early_finish_repair_carries_the_validation_summary_to_the_model() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), finish(), ask("Q2?")])
    await _start(client, store)

    await _answer(client, store, "Only one answer.")

    repair = client.notes[2]
    assert repair is not None
    assert "failed validation" in repair


async def test_an_ask_after_the_seventh_answer_is_repaired_into_a_finish() -> None:
    store = SessionStore()
    client = FakeInterviewClient(
        [*[ask(f"Q{i}?") for i in range(1, 8)], ask("Q8?"), finish()]
    )
    await _run_interview(store, client, [f"a{i}" for i in range(1, 7)])

    reply = await _answer(client, store, "a7")

    assert reply.phase is Phase.DOSSIER_READY
    assert client.notes[-2] == INTERVIEW_FINISH_NOTE
    assert client.notes[-1] is not None
    assert INTERVIEW_FINISH_NOTE in client.notes[-1]


async def test_an_ask_after_seven_answers_twice_is_refused_with_no_state_change() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient(
        [*[ask(f"Q{i}?") for i in range(1, 8)], ask("Q8?"), ask("Q9?")]
    )
    await _run_interview(store, client, [f"a{i}" for i in range(1, 7)])

    with pytest.raises(InvalidResponse):
        await _answer(client, store, "a7")

    state = store.get(_CHAT)
    assert state.phase is Phase.INTERVIEWING
    assert len(state.answers) == 6
    assert state.outstanding_question == "Q7?"
    assert state.in_flight is False


async def test_a_dossier_that_is_invalid_twice_is_refused_with_no_state_change() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient(
        [
            ask("Q1?"),
            InvalidResponse(detail="bad dossier"),
            InvalidResponse(detail="bad again"),
        ]
    )
    await _start(client, store)

    with pytest.raises(InvalidResponse):
        await _answer(client, store, "Some answer.")

    state = store.get(_CHAT)
    assert state.answers == []
    assert state.outstanding_question == "Q1?"
    assert state.dossier is None


async def test_a_dossier_that_is_invalid_once_is_repaired_and_accepted() -> None:
    store = SessionStore()
    client = FakeInterviewClient(
        [*[ask(f"Q{i}?") for i in range(1, 6)], InvalidResponse(detail="bad"), finish()]
    )
    await _run_interview(store, client, [f"a{i}" for i in range(1, 5)])

    reply = await _answer(client, store, "a5")

    assert reply.phase is Phase.DOSSIER_READY
    assert client.calls == 7


# --- Provider errors: no commit, the answer can be resent --------------------


async def test_a_provider_timeout_after_retry_commits_nothing() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ProviderTimeout(), ProviderTimeout()])
    await _start(client, store)

    with pytest.raises(ProviderTimeout):
        await _answer(client, store, "My answer.")

    state = store.get(_CHAT)
    assert state.answers == []
    assert state.outstanding_question == "Q1?"
    assert state.in_flight is False


async def test_a_resent_answer_after_a_provider_failure_is_committed_once() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), RateLimited(), RateLimited(), ask("Q2?")])
    await _start(client, store)
    with pytest.raises(RateLimited):
        await _answer(client, store, "My answer.")

    reply = await _answer(client, store, "My answer.")

    assert reply.text == "Q2?"
    assert [a.answer for a in store.get(_CHAT).answers] == ["My answer."]


async def test_a_failed_first_question_stores_no_photo_and_stays_awaiting_photo() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient([ProviderTimeout(), ProviderTimeout()])

    with pytest.raises(ProviderTimeout):
        await _start(client, store)

    state = store.get(_CHAT)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.photo_file_id is None
    assert state.outstanding_question is None
    assert state.in_flight is False


async def test_a_failed_first_question_with_an_invalid_reply_stores_no_photo() -> None:
    store = SessionStore()
    client = FakeInterviewClient([finish(), finish()])

    with pytest.raises(InvalidResponse):
        await _start(client, store)

    assert store.get(_CHAT).photo_file_id is None
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


# --- Nudges: no model call, no state change ---------------------------------


async def test_an_empty_answer_is_nudged_without_a_model_call() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?")])
    await _start(client, store)

    reply = await _answer(client, store, "   \n\t ")

    assert reply.text == EMPTY_ANSWER_TEXT
    assert reply.phase is Phase.INTERVIEWING
    assert client.calls == 1
    assert store.get(_CHAT).answers == []
    assert store.get(_CHAT).outstanding_question == "Q1?"


async def test_an_answer_over_the_limit_is_nudged_and_not_stored() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?")])
    await _start(client, store)

    reply = await _answer(client, store, "x" * (MAX_ANSWER_CHARS + 1))

    assert reply.text == ANSWER_TOO_LONG_TEXT
    assert client.calls == 1
    assert store.get(_CHAT).answers == []
    assert store.get(_CHAT).outstanding_question == "Q1?"


async def test_an_answer_of_exactly_the_limit_is_accepted() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?")])
    await _start(client, store)

    reply = await _answer(client, store, "x" * MAX_ANSWER_CHARS)

    assert reply.text == "Q2?"
    assert len(store.get(_CHAT).answers[0].answer) == MAX_ANSWER_CHARS


async def test_a_reply_is_trimmed_before_it_is_stored() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?")])
    await _start(client, store)

    await _answer(client, store, "   Owls at night.   ")

    assert store.get(_CHAT).answers[0].answer == "Owls at night."
    assert client.histories[1][0].answer == "Owls at night."


async def test_text_with_no_outstanding_question_gets_a_nudge_and_no_model_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = SessionState(phase=Phase.INTERVIEWING)
    store = SessionStore(initial={_CHAT: broken})
    client = FakeInterviewClient([])

    with caplog.at_level(logging.WARNING):
        reply = await _answer(client, store, "Hello?")

    assert reply.text == NO_QUESTION_TEXT
    assert client.calls == 0
    assert store.get(_CHAT) == broken
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# --- In-flight guard ---------------------------------------------------------


async def test_an_answer_during_a_model_call_is_refused_without_a_second_call() -> None:
    store = SessionStore()
    release = asyncio.Event()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?")], gate=release)
    release.set()
    await _start(client, store)
    release.clear()

    first = asyncio.create_task(_answer(client, store, "First reply."))
    while client.calls < 2:
        await asyncio.sleep(0)

    second = await _answer(client, store, "Second reply.")
    release.set()
    reply = await first

    assert second.text == ONE_AT_A_TIME_TEXT
    assert client.calls == 2
    assert reply.text == "Q2?"
    assert [a.answer for a in store.get(_CHAT).answers] == ["First reply."]


async def test_a_start_while_a_turn_is_in_flight_is_refused() -> None:
    store = SessionStore()
    store.begin_turn(_CHAT)
    client = FakeInterviewClient([ask("Q1?")])

    reply = await _start(client, store)

    assert reply.text == ONE_AT_A_TIME_TEXT
    assert client.calls == 0
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


# --- Restart -----------------------------------------------------------------


async def test_restart_after_two_answers_clears_everything_and_sends_the_opening() -> (
    None
):
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?"), ask("Q3?")])
    await _run_interview(store, client, ["a1", "a2"])

    reply = restart(_CHAT, store)

    assert reply.text == OPENING_PROMPT
    assert reply.phase is Phase.AWAITING_PHOTO
    state = store.get(_CHAT)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.answers == []
    assert state.outstanding_question is None
    assert state.photo_file_id is None
    assert state.dossier is None


async def test_a_new_photo_after_restart_starts_with_an_empty_history() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?"), ask("Q2?"), ask("Fresh first?")])
    await _run_interview(store, client, ["a1"])
    restart(_CHAT, store)

    reply = await _start(client, store)

    assert reply.text == "Fresh first?"
    assert client.histories[-1] == []


async def test_restart_after_the_dossier_is_ready_discards_the_dossier() -> None:
    store = SessionStore()
    client = FakeInterviewClient([*[ask(f"Q{i}?") for i in range(1, 6)], finish()])
    await _run_interview(store, client, [f"a{i}" for i in range(1, 6)])
    assert store.get(_CHAT).dossier is not None

    restart(_CHAT, store)

    assert store.get(_CHAT).dossier is None
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_restart_leaves_the_other_chat_untouched() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("A1?"), ask("B1?"), ask("A2?"), ask("B2?")])
    await _start(client, store, _CHAT)
    await _start(client, store, _OTHER)

    restart(_CHAT, store)

    assert store.get(_OTHER).phase is Phase.INTERVIEWING
    assert store.get(_OTHER).outstanding_question == "B1?"


async def test_an_answer_after_restart_is_not_attributed_to_the_old_question() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("Q1?")])
    await _start(client, store)
    restart(_CHAT, store)

    reply = await _answer(client, store, "A stray reply.")

    assert reply.text == NO_QUESTION_TEXT
    assert store.get(_CHAT).answers == []


# --- Two chats: isolation -----------------------------------------------------


async def test_two_interleaved_chats_build_dossiers_without_bleed() -> None:
    store = SessionStore()
    script = [ask("A-Q1?"), ask("B-Q1?")]
    for index in range(1, 6):
        last = index == 5
        script.append(finish() if last else ask(f"A-Q{index + 1}?"))
        script.append(finish() if last else ask(f"B-Q{index + 1}?"))
    client = FakeInterviewClient(script)

    await _start(client, store, _CHAT)
    await _start(client, store, _OTHER)
    for index in range(1, 6):
        await _answer(client, store, f"alpha-{index}", _CHAT)
        await _answer(client, store, f"beta-{index}", _OTHER)

    alpha = [answer.answer for answer in store.get(_CHAT).answers]
    beta = [answer.answer for answer in store.get(_OTHER).answers]
    assert alpha == [f"alpha-{i}" for i in range(1, 6)]
    assert beta == [f"beta-{i}" for i in range(1, 6)]
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert store.get(_OTHER).phase is Phase.DOSSIER_READY


async def test_each_chat_model_history_contains_only_its_own_answers() -> None:
    store = SessionStore()
    client = FakeInterviewClient(
        [ask("A-Q1?"), ask("B-Q1?"), ask("A-Q2?"), ask("B-Q2?")]
    )
    await _start(client, store, _CHAT)
    await _start(client, store, _OTHER)
    await _answer(client, store, "alpha-only", _CHAT)
    await _answer(client, store, "beta-only", _OTHER)

    assert [a.answer for a in client.histories[2]] == ["alpha-only"]
    assert [a.answer for a in client.histories[3]] == ["beta-only"]


async def test_a_reset_in_one_chat_leaves_the_other_chats_question_intact() -> None:
    store = SessionStore()
    client = FakeInterviewClient([ask("A-Q1?"), ask("B-Q1?"), ask("B-Q2?")])
    await _start(client, store, _CHAT)
    await _start(client, store, _OTHER)

    restart(_CHAT, store)
    reply = await _answer(client, store, "beta reply", _OTHER)

    assert reply.text == "B-Q2?"
    assert [a.answer for a in store.get(_OTHER).answers] == ["beta reply"]
