"""Tests for the versioned per-chat session store (schema v2).

:class:`SessionStore` is the only writer of session fields. Stages go through
its named methods and never mutate a :class:`SessionState`. These tests pin the
phase transitions, the in-flight flag, the invariants after each commit, the
reset semantics, two-chat isolation, and the version check on read.
"""

from __future__ import annotations

import pytest

from telegram_documentaries.interview_models import Answer, Dossier
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.state import (
    STATE_VERSION,
    Phase,
    SessionState,
    SessionStore,
    StateError,
)

_CHAT_A = as_chat_id(1001)
_CHAT_B = as_chat_id(2002)


def _dossier() -> Dossier:
    return Dossier.model_validate(
        {
            "quirks": [
                {"topic": "sleep", "observation": "Dozes in the afternoon."},
                {"topic": "forage", "observation": "Digs for grubs."},
                {"topic": "territory", "observation": "Patrols the fence."},
            ],
            "suggested_animal": {"name": "Badger", "reason": "Keeps odd hours."},
        }
    )


def _assert_invariants(store: SessionStore, chat_id: int) -> None:
    """The dossier is set exactly in DOSSIER_READY; a question implies INTERVIEWING."""
    state = store.get(as_chat_id(chat_id))
    assert (state.dossier is not None) == (state.phase is Phase.DOSSIER_READY)
    if state.outstanding_question is not None:
        assert state.phase is Phase.INTERVIEWING


def _start_interview(store: SessionStore, chat_id: ChatId) -> None:
    """Drive the store through a passing photo and its first question."""
    assert store.begin_turn(chat_id) is True
    store.commit_first_question(chat_id, "photo-1", "What keeps you up at night?")


# --- Fresh session -------------------------------------------------------------


def test_a_fresh_chat_starts_awaiting_a_photo_with_nothing_recorded() -> None:
    store = SessionStore()

    state = store.get(_CHAT_A)

    assert state.phase is Phase.AWAITING_PHOTO
    assert state.schema_version == STATE_VERSION
    assert state.photo_file_id is None
    assert state.answers == []
    assert state.outstanding_question is None
    assert state.dossier is None
    assert state.in_flight is False


def test_the_schema_version_is_two() -> None:
    assert STATE_VERSION == 2


def test_every_declared_phase_keeps_its_wire_value() -> None:
    assert {phase.value for phase in Phase} == {
        "idle",
        "awaiting_photo",
        "interviewing",
        "dossier_ready",
        "generating",
        "delivering",
    }


# --- In-flight flag ------------------------------------------------------------


def test_begin_turn_marks_the_chat_in_flight() -> None:
    store = SessionStore()

    assert store.begin_turn(_CHAT_A) is True

    assert store.get(_CHAT_A).in_flight is True


def test_a_second_begin_turn_while_in_flight_is_refused() -> None:
    store = SessionStore()
    store.begin_turn(_CHAT_A)

    assert store.begin_turn(_CHAT_A) is False
    assert store.get(_CHAT_A).in_flight is True


def test_release_turn_clears_the_in_flight_flag_without_changing_anything_else() -> (
    None
):
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)

    store.release_turn(_CHAT_A)

    state = store.get(_CHAT_A)
    assert state.in_flight is False
    assert state.outstanding_question == "What keeps you up at night?"
    assert state.answers == []


def test_a_released_turn_can_be_begun_again() -> None:
    store = SessionStore()
    store.begin_turn(_CHAT_A)
    store.release_turn(_CHAT_A)

    assert store.begin_turn(_CHAT_A) is True


# --- Commits and transitions ---------------------------------------------------


def test_commit_first_question_stores_the_photo_and_enters_interviewing() -> None:
    store = SessionStore()
    store.begin_turn(_CHAT_A)

    store.commit_first_question(_CHAT_A, "photo-1", "Where do you sleep?")

    state = store.get(_CHAT_A)
    assert state.phase is Phase.INTERVIEWING
    assert state.photo_file_id == "photo-1"
    assert state.outstanding_question == "Where do you sleep?"
    assert state.answers == []
    assert state.in_flight is False
    _assert_invariants(store, 1001)


def test_commit_answer_appends_one_exchange_and_clears_the_question() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)

    store.commit_answer(
        _CHAT_A,
        Answer(question="What keeps you up at night?", answer="Loud neighbours."),
    )

    state = store.get(_CHAT_A)
    assert state.answers == [
        Answer(question="What keeps you up at night?", answer="Loud neighbours.")
    ]
    assert state.outstanding_question is None
    assert state.in_flight is False


def test_commit_question_sets_the_next_question_after_an_answer() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)
    store.commit_answer(
        _CHAT_A, Answer(question="What keeps you up at night?", answer="Owls.")
    )

    store.commit_question(_CHAT_A, "Which tree do you favour?")

    state = store.get(_CHAT_A)
    assert state.outstanding_question == "Which tree do you favour?"
    assert state.phase is Phase.INTERVIEWING
    assert len(state.answers) == 1
    _assert_invariants(store, 1001)


def test_commit_dossier_stores_it_clears_the_question_and_finishes() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)
    store.commit_answer(
        _CHAT_A, Answer(question="What keeps you up at night?", answer="Owls.")
    )

    store.commit_dossier(_CHAT_A, _dossier())

    state = store.get(_CHAT_A)
    assert state.phase is Phase.DOSSIER_READY
    assert state.dossier == _dossier()
    assert state.outstanding_question is None
    assert state.in_flight is False
    _assert_invariants(store, 1001)


def test_commit_answer_for_a_question_that_is_not_outstanding_is_refused() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)

    with pytest.raises(StateError):
        store.commit_answer(_CHAT_A, Answer(question="A stale question?", answer="Hi"))

    assert store.get(_CHAT_A).answers == []
    assert store.get(_CHAT_A).outstanding_question == ("What keeps you up at night?")


def test_commit_question_outside_an_interview_is_refused() -> None:
    store = SessionStore()

    with pytest.raises(StateError):
        store.commit_question(_CHAT_A, "Out of nowhere?")

    assert store.get(_CHAT_A).phase is Phase.AWAITING_PHOTO


def test_commit_dossier_while_a_question_is_outstanding_is_refused() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)

    with pytest.raises(StateError):
        store.commit_dossier(_CHAT_A, _dossier())

    assert store.get(_CHAT_A).dossier is None
    assert store.get(_CHAT_A).phase is Phase.INTERVIEWING


# --- Reset ---------------------------------------------------------------------


def test_reset_clears_everything_and_returns_to_awaiting_photo() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)
    store.commit_answer(
        _CHAT_A, Answer(question="What keeps you up at night?", answer="Owls.")
    )
    store.commit_question(_CHAT_A, "Which tree do you favour?")

    store.reset(_CHAT_A)

    state = store.get(_CHAT_A)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.photo_file_id is None
    assert state.answers == []
    assert state.outstanding_question is None
    assert state.dossier is None
    assert state.in_flight is False


def test_reset_discards_a_completed_dossier() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    store.begin_turn(_CHAT_A)
    store.commit_answer(
        _CHAT_A, Answer(question="What keeps you up at night?", answer="Owls.")
    )
    store.commit_dossier(_CHAT_A, _dossier())

    store.reset(_CHAT_A)

    assert store.get(_CHAT_A).dossier is None
    assert store.get(_CHAT_A).phase is Phase.AWAITING_PHOTO


def test_reset_on_an_unknown_chat_is_harmless() -> None:
    store = SessionStore()

    store.reset(_CHAT_A)

    assert store.get(_CHAT_A).phase is Phase.AWAITING_PHOTO


# --- Versioning ----------------------------------------------------------------


def test_a_stored_schema_version_one_is_discarded_on_read() -> None:
    stale = SessionState(
        schema_version=1,
        phase=Phase.INTERVIEWING,
        photo_file_id="old-photo",
        outstanding_question="Stale question?",
    )
    store = SessionStore(initial={_CHAT_A: stale})

    state = store.get(_CHAT_A)

    assert state.schema_version == STATE_VERSION
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.photo_file_id is None
    assert state.outstanding_question is None


def test_writes_after_a_stale_state_is_discarded_start_from_a_clean_session() -> None:
    stale = SessionState(schema_version=1, phase=Phase.INTERVIEWING)
    store = SessionStore(initial={_CHAT_A: stale})

    store.get(_CHAT_A)
    store.begin_turn(_CHAT_A)
    store.commit_first_question(_CHAT_A, "fresh-photo", "Fresh question?")

    state = store.get(_CHAT_A)
    assert state.photo_file_id == "fresh-photo"
    assert state.answers == []


# --- Isolation -----------------------------------------------------------------


def test_two_chats_keep_independent_state() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)

    assert store.get(_CHAT_A).outstanding_question == ("What keeps you up at night?")
    assert store.get(_CHAT_B).phase is Phase.AWAITING_PHOTO
    assert store.get(_CHAT_B).outstanding_question is None


def test_an_in_flight_turn_in_one_chat_does_not_block_another() -> None:
    store = SessionStore()
    store.begin_turn(_CHAT_A)

    assert store.begin_turn(_CHAT_B) is True


def test_resetting_one_chat_leaves_the_other_intact() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    _start_interview(store, _CHAT_B)

    store.reset(_CHAT_A)

    assert store.get(_CHAT_A).phase is Phase.AWAITING_PHOTO
    assert store.get(_CHAT_B).phase is Phase.INTERVIEWING
    assert store.get(_CHAT_B).outstanding_question == ("What keeps you up at night?")


def test_string_and_integer_chat_ids_address_the_same_session() -> None:
    store = SessionStore()

    store.begin_turn(as_chat_id("1001"))
    store.commit_first_question(as_chat_id("1001"), "photo-x", "Q?")

    assert store.get(_CHAT_A).photo_file_id == "photo-x"
