"""Versioned, in-memory per-chat session state (schema v2).

:class:`SessionStore` is the single writer of session fields. Stages call its
named methods (``begin_turn``, ``commit_*``, ``release_turn``, ``reset``) and
never mutate a :class:`SessionState`, which is frozen to make that rule
enforceable. Each commit checks the transition it performs and the invariants
the state must hold afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from telegram_documentaries.interview_models import Answer, Dossier
from telegram_documentaries.models import ChatId

#: Bump when ``SessionState`` changes incompatibly; old states are discarded.
#: Version 1 was the Phase 2 Bouncer state (``last_file_id``).
STATE_VERSION = 2


class StateError(RuntimeError):
    """A commit was requested that the current state does not allow.

    This is a programming error in a stage, never a user-facing failure.
    """


class Phase(StrEnum):
    """Pipeline phases. ``GENERATING`` and ``DELIVERING`` are declared only."""

    IDLE = "idle"
    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"
    DOSSIER_READY = "dossier_ready"
    GENERATING = "generating"
    DELIVERING = "delivering"


class SessionState(BaseModel):
    """A frozen snapshot of one chat's state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = STATE_VERSION
    phase: Phase = Phase.AWAITING_PHOTO
    photo_file_id: str | None = None
    answers: list[Answer] = Field(default_factory=list)
    outstanding_question: str | None = None
    dossier: Dossier | None = None
    in_flight: bool = False


def _check_invariants(state: SessionState) -> None:
    """Refuse any state that breaks the cross-field rules of the phase model."""
    if (state.dossier is not None) != (state.phase is Phase.DOSSIER_READY):
        raise StateError("dossier is set exactly when the phase is dossier_ready")
    if state.outstanding_question is not None and state.phase is not Phase.INTERVIEWING:
        raise StateError("a question is outstanding only while interviewing")


class SessionStore:
    """Single state driver keyed by the typed :class:`ChatId`."""

    def __init__(self, initial: Mapping[ChatId, SessionState] | None = None) -> None:
        self._states: dict[ChatId, SessionState] = dict(initial or {})

    def get(self, chat_id: ChatId) -> SessionState:
        """Return the chat's state, restarting cleanly if it is stale or absent."""
        state = self._states.get(chat_id)
        if state is None or state.schema_version != STATE_VERSION:
            state = SessionState()
            self._states[chat_id] = state
        return state

    def _replace(self, chat_id: ChatId, **updates: object) -> None:
        """Store a copy with ``updates`` applied, after checking the invariants."""
        new_state = self.get(chat_id).model_copy(update=updates)
        _check_invariants(new_state)
        self._states[chat_id] = new_state

    def begin_turn(self, chat_id: ChatId) -> bool:
        """Mark a model call as in flight. Returns ``False`` if one already is."""
        if self.get(chat_id).in_flight:
            return False
        self._replace(chat_id, in_flight=True)
        return True

    def release_turn(self, chat_id: ChatId) -> None:
        """Clear the in-flight flag after a failed call. Nothing else changes."""
        self._replace(chat_id, in_flight=False)

    def commit_first_question(
        self, chat_id: ChatId, photo_file_id: str, question: str
    ) -> None:
        """Record the approved photo and its first question; start the interview."""
        self._require_phase(chat_id, {Phase.AWAITING_PHOTO})
        self._replace(
            chat_id,
            phase=Phase.INTERVIEWING,
            photo_file_id=photo_file_id,
            answers=[],
            outstanding_question=question,
            dossier=None,
            in_flight=False,
        )

    def commit_answer(self, chat_id: ChatId, answer: Answer) -> None:
        """Append the answer to the outstanding question and clear that question."""
        state = self.get(chat_id)
        self._require_phase(chat_id, {Phase.INTERVIEWING})
        if state.outstanding_question != answer.question:
            raise StateError("answer does not match the outstanding question")
        self._replace(
            chat_id,
            answers=[*state.answers, answer],
            outstanding_question=None,
            in_flight=False,
        )

    def commit_question(self, chat_id: ChatId, question: str) -> None:
        """Set the next question. Only valid between answers while interviewing."""
        state = self.get(chat_id)
        self._require_phase(chat_id, {Phase.INTERVIEWING})
        if state.outstanding_question is not None:
            raise StateError("a question is already outstanding")
        self._replace(chat_id, outstanding_question=question, in_flight=False)

    def commit_dossier(self, chat_id: ChatId, dossier: Dossier) -> None:
        """Store the finished dossier and move the chat to ``DOSSIER_READY``."""
        state = self.get(chat_id)
        self._require_phase(chat_id, {Phase.INTERVIEWING})
        if state.outstanding_question is not None:
            raise StateError("cannot finish while a question is outstanding")
        self._replace(
            chat_id,
            phase=Phase.DOSSIER_READY,
            dossier=dossier,
            in_flight=False,
        )

    def reset(self, chat_id: ChatId) -> None:
        """Purge everything for the chat and return it to awaiting-photo."""
        self._states[chat_id] = SessionState()

    def _require_phase(self, chat_id: ChatId, allowed: set[Phase]) -> None:
        if self.get(chat_id).phase not in allowed:
            raise StateError("commit not allowed in the current phase")
