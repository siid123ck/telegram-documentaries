"""Minimal, versioned, in-memory per-chat session state.

:class:`SessionStore` is the only writer of session fields. Stages call
``get`` / ``note_photo`` / ``reset`` and never mutate a ``SessionState``
directly (the state model is frozen to make that rule enforceable). Only the
``IDLE`` and ``AWAITING_PHOTO`` phases are used in this phase of the project.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from telegram_documentaries.models import ChatId

#: Bump when ``SessionState`` changes incompatibly; old states are discarded.
STATE_VERSION = 1


class Phase(StrEnum):
    """Pipeline phases. Later phases are declared but not driven yet."""

    IDLE = "idle"
    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"
    GENERATING = "generating"
    DELIVERING = "delivering"


class SessionState(BaseModel):
    """A frozen snapshot of one chat's state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = STATE_VERSION
    phase: Phase = Phase.AWAITING_PHOTO
    last_file_id: str | None = None


class SessionStore:
    """Single state driver keyed by the typed :class:`ChatId`."""

    def __init__(self, initial: Mapping[ChatId, SessionState] | None = None) -> None:
        self._states: dict[ChatId, SessionState] = dict(initial or {})

    def get(self, chat_id: ChatId) -> SessionState:
        """Return the chat's state, restarting cleanly if it is stale or absent."""
        state = self._states.get(chat_id)
        if state is None or state.version != STATE_VERSION:
            state = SessionState()
            self._states[chat_id] = state
        return state

    def note_photo(self, chat_id: ChatId, file_id: str) -> None:
        """Record an approved photo reference for the chat."""
        state = self.get(chat_id)
        self._states[chat_id] = state.model_copy(
            update={"last_file_id": file_id, "phase": Phase.AWAITING_PHOTO}
        )

    def reset(self, chat_id: ChatId) -> None:
        """Purge the chat's photo reference and return it to awaiting-photo."""
        self._states[chat_id] = SessionState()
