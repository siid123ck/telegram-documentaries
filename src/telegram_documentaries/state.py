"""Versioned, in-memory per-chat session state (schema v4).

:class:`SessionStore` is the single writer of session fields. Stages call its
named methods (``begin_turn``, ``commit_*``, ``release_turn``, ``begin_generation``,
``begin_delivery``, ``finish_delivery``, ``fail_delivery``, ``abandon_generation``,
``begin_script``, ``commit_script``, ``abandon_script``, ``reset``) and never
mutate a :class:`SessionState`, which is frozen to make that rule enforceable.
Each commit checks the transition it performs and the invariants the state must
hold afterwards.

Generation runs are guarded by an opaque run token. A driver method that takes a
token changes nothing unless the token is the chat's current one, so a result that
arrives after a reset cannot change a newer state. Each reset also advances a
per-chat epoch, which lets the interview stages drop a late model reply. The script
step has no run token (``begin_delivery`` clears it), so its driver methods are
guarded by the epoch alone.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from telegram_documentaries.errors import StaleResult
from telegram_documentaries.interview_models import Answer, Dossier
from telegram_documentaries.models import ChatId

#: Bump when ``SessionState`` changes incompatibly; old states are discarded.
#: Version 1 was the Phase 2 Bouncer state (``last_file_id``). Version 2 had no
#: generation run token (Phase 4 added it). Version 3 had no stored script (Phase 5
#: added it).
STATE_VERSION = 4


class StateError(RuntimeError):
    """A commit was requested that the current state does not allow.

    This is a programming error in a stage, never a user-facing failure.
    """


class Phase(StrEnum):
    """Pipeline phases. ``GENERATING`` and ``DELIVERING`` are driven in Phase 4."""

    IDLE = "idle"
    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"
    DOSSIER_READY = "dossier_ready"
    GENERATING = "generating"
    DELIVERING = "delivering"


#: Phases in which a dossier is held. It is set exactly in these phases.
_DOSSIER_PHASES = frozenset({Phase.DOSSIER_READY, Phase.GENERATING, Phase.DELIVERING})


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
    generation_run: str | None = None
    script: str | None = None


def _check_invariants(state: SessionState) -> None:
    """Refuse any state that breaks the cross-field rules of the phase model."""
    if (state.dossier is not None) != (state.phase in _DOSSIER_PHASES):
        raise StateError(
            "dossier is set exactly in dossier_ready, generating, and delivering"
        )
    if state.outstanding_question is not None and state.phase is not Phase.INTERVIEWING:
        raise StateError("a question is outstanding only while interviewing")
    if (state.generation_run is not None) != (state.phase is Phase.GENERATING):
        raise StateError("a generation run is set exactly while generating")
    if state.script is not None and state.phase is not Phase.DELIVERING:
        raise StateError("a script is set only while delivering")


class SessionStore:
    """Single state driver keyed by the typed :class:`ChatId`."""

    def __init__(self, initial: Mapping[ChatId, SessionState] | None = None) -> None:
        self._states: dict[ChatId, SessionState] = dict(initial or {})
        self._epochs: dict[ChatId, int] = {}

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

    def begin_generation(self, chat_id: ChatId) -> str | None:
        """Start a portrait run from ``DOSSIER_READY`` and return its token.

        Returns ``None`` if a run is already in flight for the chat, so a duplicate
        update cannot start a second generation. Any other phase is a programming
        error and raises :class:`StateError`.
        """
        if self.get(chat_id).in_flight:
            return None
        self._require_phase(chat_id, {Phase.DOSSIER_READY})
        run = uuid.uuid4().hex
        self._replace(
            chat_id, phase=Phase.GENERATING, in_flight=True, generation_run=run
        )
        return run

    def begin_delivery(self, chat_id: ChatId, run: str) -> bool:
        """Move a finished run to ``DELIVERING``, before the photo is sent.

        Returns ``False`` and changes nothing if the chat is no longer in
        ``GENERATING`` for this ``run``, which means a reset happened while the
        model was working. ``in_flight`` stays set during the send.
        """
        state = self.get(chat_id)
        if state.phase is not Phase.GENERATING or state.generation_run != run:
            return False
        self._replace(chat_id, phase=Phase.DELIVERING, generation_run=None)
        return True

    def finish_delivery(self, chat_id: ChatId) -> bool:
        """Clear ``in_flight`` once the photo is sent. The chat rests in ``DELIVERING``.

        Returns ``False`` and changes nothing if the chat is not in ``DELIVERING``.
        That happens only when a reset landed during the send (the accepted race).
        """
        if self.get(chat_id).phase is not Phase.DELIVERING:
            return False
        self._replace(chat_id, in_flight=False)
        return True

    def fail_delivery(self, chat_id: ChatId) -> bool:
        """Roll a failed send back to ``DOSSIER_READY`` with the dossier kept.

        Returns ``False`` and changes nothing if the chat is not in ``DELIVERING``.
        """
        if self.get(chat_id).phase is not Phase.DELIVERING:
            return False
        self._replace(chat_id, phase=Phase.DOSSIER_READY, in_flight=False)
        return True

    def abandon_generation(self, chat_id: ChatId, run: str) -> bool:
        """Return a failed or dropped run to ``DOSSIER_READY``, keeping the interview.

        Returns ``False`` and changes nothing if ``run`` is not the chat's current
        generation run.
        """
        state = self.get(chat_id)
        if state.phase is not Phase.GENERATING or state.generation_run != run:
            return False
        self._replace(
            chat_id,
            phase=Phase.DOSSIER_READY,
            in_flight=False,
            generation_run=None,
        )
        return True

    def begin_script(self, chat_id: ChatId, epoch: int) -> bool:
        """Start the script step in ``DELIVERING``, after the portrait is sent.

        Returns ``False`` and changes nothing if the chat was reset since ``epoch``,
        is not in ``DELIVERING``, already has a script, or has a turn in flight.
        """
        state = self.get(chat_id)
        if (
            self.epoch(chat_id) != epoch
            or state.phase is not Phase.DELIVERING
            or state.script is not None
            or state.in_flight
        ):
            return False
        self._replace(chat_id, in_flight=True)
        return True

    def commit_script(self, chat_id: ChatId, epoch: int, text: str) -> bool:
        """Store a validated script and clear ``in_flight``.

        Returns ``False`` and changes nothing if the chat was reset since ``epoch``
        or no script step is in flight in ``DELIVERING``. A late result therefore
        cannot write into a newer session.
        """
        state = self.get(chat_id)
        if (
            self.epoch(chat_id) != epoch
            or state.phase is not Phase.DELIVERING
            or not state.in_flight
        ):
            return False
        self._replace(chat_id, script=text, in_flight=False)
        return True

    def abandon_script(self, chat_id: ChatId, epoch: int) -> bool:
        """Clear ``in_flight`` after a failed script step. ``script`` stays ``None``.

        Returns ``False`` and changes nothing if the chat was reset since ``epoch``
        or is not in ``DELIVERING``.
        """
        if (
            self.epoch(chat_id) != epoch
            or self.get(chat_id).phase is not Phase.DELIVERING
        ):
            return False
        self._replace(chat_id, in_flight=False)
        return True

    def epoch(self, chat_id: ChatId) -> int:
        """Return the chat's reset counter. It changes on every ``/restart``."""
        return self._epochs.get(chat_id, 0)

    def require_current(self, chat_id: ChatId, epoch: int) -> None:
        """Raise :class:`StaleResult` if the chat was reset since ``epoch``."""
        if self.epoch(chat_id) != epoch:
            raise StaleResult("the chat was reset while a result was in flight")

    def reset(self, chat_id: ChatId) -> None:
        """Purge everything for the chat and return it to awaiting-photo."""
        self._states[chat_id] = SessionState()
        self._epochs[chat_id] = self.epoch(chat_id) + 1

    def _require_phase(self, chat_id: ChatId, allowed: set[Phase]) -> None:
        if self.get(chat_id).phase not in allowed:
            raise StateError("commit not allowed in the current phase")
