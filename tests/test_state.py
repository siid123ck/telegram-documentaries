"""Tests for the versioned per-chat session store (schema v3).

:class:`SessionStore` is the only writer of session fields. Stages go through
its named methods and never mutate a :class:`SessionState`. These tests pin the
phase transitions, the in-flight flag, the invariants after each commit, the
reset semantics, two-chat isolation, and the version check on read.
"""

from __future__ import annotations

import pytest

from telegram_documentaries.errors import StaleResult
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


_DOSSIER_PHASES = {Phase.DOSSIER_READY, Phase.GENERATING, Phase.DELIVERING}


def _assert_invariants(store: SessionStore, chat_id: int) -> None:
    """The dossier is set exactly in the dossier phases; a question implies
    INTERVIEWING; a generation run is set exactly in GENERATING."""
    state = store.get(as_chat_id(chat_id))
    assert (state.dossier is not None) == (state.phase in _DOSSIER_PHASES)
    if state.outstanding_question is not None:
        assert state.phase is Phase.INTERVIEWING
    assert (state.generation_run is not None) == (state.phase is Phase.GENERATING)


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


def test_the_schema_version_is_three() -> None:
    assert STATE_VERSION == 3


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


# --- Phase 4: generation and delivery driver (schema v3) -----------------------

_QUESTION = "What keeps you up at night?"
_EXCHANGE = Answer(question=_QUESTION, answer="Owls.")


def _ready_for_generation(store: SessionStore, chat_id: ChatId = _CHAT_A) -> None:
    """Drive the store through a finished interview into DOSSIER_READY."""
    _start_interview(store, chat_id)
    store.begin_turn(chat_id)
    store.commit_answer(chat_id, _EXCHANGE)
    store.commit_dossier(chat_id, _dossier())


def test_a_stored_schema_version_two_is_discarded_on_read() -> None:
    stale = SessionState(
        schema_version=2,
        phase=Phase.DOSSIER_READY,
        photo_file_id="old-photo",
        answers=[_EXCHANGE],
        dossier=_dossier(),
    )
    store = SessionStore(initial={_CHAT_A: stale})

    state = store.get(_CHAT_A)

    assert state.schema_version == STATE_VERSION
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.dossier is None
    assert state.answers == []


def test_a_new_session_has_no_generation_run() -> None:
    assert SessionStore().get(_CHAT_A).generation_run is None


def test_begin_generation_from_dossier_ready_starts_a_run_in_flight() -> None:
    store = SessionStore()
    _ready_for_generation(store)

    run = store.begin_generation(_CHAT_A)

    assert run
    state = store.get(_CHAT_A)
    assert state.phase is Phase.GENERATING
    assert state.in_flight is True
    assert state.generation_run == run
    assert state.dossier == _dossier()
    _assert_invariants(store, 1001)


def test_each_generation_run_gets_a_fresh_token() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    first = store.begin_generation(_CHAT_A)
    assert first is not None
    store.abandon_generation(_CHAT_A, first)  # back in DOSSIER_READY

    second = store.begin_generation(_CHAT_A)

    assert second is not None
    assert second != first


def test_a_second_begin_generation_while_in_flight_returns_none() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    first = store.begin_generation(_CHAT_A)

    second = store.begin_generation(_CHAT_A)

    assert first is not None
    assert second is None
    assert store.get(_CHAT_A).generation_run == first


@pytest.mark.parametrize(
    "phase",
    [Phase.IDLE, Phase.AWAITING_PHOTO, Phase.INTERVIEWING],
    ids=lambda phase: phase.value,
)
def test_begin_generation_outside_dossier_ready_is_refused(phase: Phase) -> None:
    store = SessionStore(initial={_CHAT_A: SessionState(phase=phase)})

    with pytest.raises(StateError):
        store.begin_generation(_CHAT_A)

    assert store.get(_CHAT_A).phase is phase
    assert store.get(_CHAT_A).generation_run is None


def test_begin_generation_after_delivery_finished_is_refused() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None
    store.begin_delivery(_CHAT_A, run)
    store.finish_delivery(_CHAT_A)
    before = store.get(_CHAT_A)

    with pytest.raises(StateError):
        store.begin_generation(_CHAT_A)

    assert store.get(_CHAT_A) == before


def test_begin_generation_during_an_in_flight_delivery_returns_none() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None
    store.begin_delivery(_CHAT_A, run)
    before = store.get(_CHAT_A)

    assert store.begin_generation(_CHAT_A) is None

    assert store.get(_CHAT_A) == before


def test_begin_delivery_with_the_current_token_moves_to_delivering() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None

    accepted = store.begin_delivery(_CHAT_A, run)

    assert accepted is True
    state = store.get(_CHAT_A)
    assert state.phase is Phase.DELIVERING
    assert state.generation_run is None
    assert state.in_flight is True
    assert state.dossier == _dossier()
    _assert_invariants(store, 1001)


@pytest.mark.parametrize("token", ["not-the-current-run", ""])
def test_begin_delivery_with_a_wrong_token_changes_nothing(token: str) -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    before = store.get(_CHAT_A)

    accepted = store.begin_delivery(_CHAT_A, token)

    assert run is not None
    assert accepted is False
    assert store.get(_CHAT_A) == before


def test_begin_delivery_outside_generating_changes_nothing() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    before = store.get(_CHAT_A)

    accepted = store.begin_delivery(_CHAT_A, "any-token")

    assert accepted is False
    assert store.get(_CHAT_A) == before


def test_finish_delivery_clears_in_flight_and_rests_in_delivering() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None
    store.begin_delivery(_CHAT_A, run)

    finished = store.finish_delivery(_CHAT_A)

    assert finished is True
    state = store.get(_CHAT_A)
    assert state.phase is Phase.DELIVERING
    assert state.in_flight is False
    assert state.dossier == _dossier()


def test_finish_delivery_outside_delivering_changes_nothing() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    before = store.get(_CHAT_A)

    finished = store.finish_delivery(_CHAT_A)

    assert run is not None
    assert finished is False
    assert store.get(_CHAT_A) == before


def test_fail_delivery_rolls_back_to_dossier_ready_and_keeps_the_dossier() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None
    store.begin_delivery(_CHAT_A, run)

    failed = store.fail_delivery(_CHAT_A)

    assert failed is True
    state = store.get(_CHAT_A)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert state.generation_run is None
    assert state.dossier == _dossier()
    _assert_invariants(store, 1001)


def test_fail_delivery_outside_delivering_changes_nothing() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    store.begin_generation(_CHAT_A)
    before = store.get(_CHAT_A)

    failed = store.fail_delivery(_CHAT_A)

    assert failed is False
    assert store.get(_CHAT_A) == before


def test_abandon_generation_returns_to_dossier_ready_and_keeps_answers() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None

    abandoned = store.abandon_generation(_CHAT_A, run)

    assert abandoned is True
    state = store.get(_CHAT_A)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert state.generation_run is None
    assert state.dossier == _dossier()
    assert state.answers == [_EXCHANGE]
    _assert_invariants(store, 1001)


def test_abandon_generation_with_a_stale_token_changes_nothing() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    before = store.get(_CHAT_A)

    abandoned = store.abandon_generation(_CHAT_A, "stale-run")

    assert run is not None
    assert abandoned is False
    assert store.get(_CHAT_A) == before


def test_a_stale_run_token_cannot_change_a_newer_run() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    old_run = store.begin_generation(_CHAT_A)
    assert old_run is not None
    store.abandon_generation(_CHAT_A, old_run)
    new_run = store.begin_generation(_CHAT_A)
    assert new_run is not None
    before = store.get(_CHAT_A)

    assert store.abandon_generation(_CHAT_A, old_run) is False
    assert store.begin_delivery(_CHAT_A, old_run) is False

    assert store.get(_CHAT_A) == before
    assert store.get(_CHAT_A).generation_run == new_run


def test_a_run_started_before_a_reset_cannot_deliver_after_it() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    old_run = store.begin_generation(_CHAT_A)
    assert old_run is not None

    store.reset(_CHAT_A)

    assert store.begin_delivery(_CHAT_A, old_run) is False
    assert store.abandon_generation(_CHAT_A, old_run) is False
    assert store.get(_CHAT_A).phase is Phase.AWAITING_PHOTO


@pytest.mark.parametrize(
    "state",
    [
        SessionState(phase=Phase.INTERVIEWING, generation_run="run"),
        SessionState(phase=Phase.DOSSIER_READY, dossier=None, generation_run="run"),
        SessionState(phase=Phase.GENERATING, dossier=None, generation_run="run"),
        SessionState(phase=Phase.GENERATING, dossier=_dossier(), generation_run=None),
        SessionState(phase=Phase.INTERVIEWING, dossier=_dossier()),
        SessionState(phase=Phase.DELIVERING, dossier=None),
    ],
    ids=[
        "run-outside-generating",
        "run-missing-from-dossier-ready",
        "generating-without-dossier",
        "generating-without-run",
        "dossier-while-interviewing",
        "delivering-without-dossier",
    ],
)
def test_an_inconsistent_state_is_refused_at_the_next_commit(
    state: SessionState,
) -> None:
    store = SessionStore(initial={_CHAT_A: state})

    with pytest.raises(StateError):
        store.release_turn(_CHAT_A)


def test_dossier_and_run_are_kept_through_generation_and_delivery() -> None:
    store = SessionStore()
    _ready_for_generation(store)
    run = store.begin_generation(_CHAT_A)
    assert run is not None
    assert store.get(_CHAT_A).dossier == _dossier()
    store.begin_delivery(_CHAT_A, run)
    assert store.get(_CHAT_A).dossier == _dossier()
    assert store.get(_CHAT_A).generation_run is None


@pytest.mark.parametrize(
    "phase",
    list(Phase),
    ids=lambda phase: phase.value,
)
def test_reset_from_every_phase_clears_the_run_and_the_dossier(phase: Phase) -> None:
    messy = SessionState(
        phase=phase,
        photo_file_id="photo",
        answers=[_EXCHANGE],
        outstanding_question=_QUESTION if phase is Phase.INTERVIEWING else None,
        dossier=_dossier() if phase in _DOSSIER_PHASES else None,
        generation_run="run" if phase is Phase.GENERATING else None,
        in_flight=phase in {Phase.GENERATING, Phase.DELIVERING},
    )
    store = SessionStore(initial={_CHAT_A: messy})

    store.reset(_CHAT_A)

    state = store.get(_CHAT_A)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.generation_run is None
    assert state.dossier is None
    assert state.answers == []
    assert state.in_flight is False


def test_two_chats_keep_independent_generation_runs() -> None:
    store = SessionStore()
    _ready_for_generation(store, _CHAT_A)
    _ready_for_generation(store, _CHAT_B)

    run_a = store.begin_generation(_CHAT_A)
    run_b = store.begin_generation(_CHAT_B)

    assert run_a is not None
    assert run_b is not None
    assert run_a != run_b
    assert store.get(_CHAT_A).generation_run == run_a
    assert store.get(_CHAT_B).generation_run == run_b


def test_a_reset_in_one_chat_leaves_the_other_chats_token_valid() -> None:
    store = SessionStore()
    _ready_for_generation(store, _CHAT_A)
    _ready_for_generation(store, _CHAT_B)
    run_a = store.begin_generation(_CHAT_A)
    run_b = store.begin_generation(_CHAT_B)
    assert run_a is not None
    assert run_b is not None

    store.reset(_CHAT_A)

    assert store.begin_delivery(_CHAT_B, run_b) is True
    assert store.get(_CHAT_B).phase is Phase.DELIVERING


# --- Phase 4: reset epoch (stale model results after /restart) -----------------


def test_a_reset_advances_the_chat_epoch_and_leaves_the_other_chat_alone() -> None:
    store = SessionStore()
    before_a = store.epoch(_CHAT_A)
    before_b = store.epoch(_CHAT_B)

    store.reset(_CHAT_A)

    assert store.epoch(_CHAT_A) == before_a + 1
    assert store.epoch(_CHAT_B) == before_b


def test_a_result_from_before_a_reset_is_refused_as_stale() -> None:
    store = SessionStore()
    epoch = store.epoch(_CHAT_A)
    store.reset(_CHAT_A)

    with pytest.raises(StaleResult):
        store.require_current(_CHAT_A, epoch)


def test_a_result_with_no_intervening_reset_is_current() -> None:
    store = SessionStore()
    _start_interview(store, _CHAT_A)
    epoch = store.epoch(_CHAT_A)

    store.require_current(_CHAT_A, epoch)

    assert store.get(_CHAT_A).phase is Phase.INTERVIEWING
