"""Tests for the minimal per-chat session store.

The store is the only writer of session fields; stages go through ``get``,
``note_photo`` and ``reset``. Two chats must never bleed into one another, and a
stored state from an incompatible version is discarded on read.
"""

from __future__ import annotations

from telegram_documentaries.models import as_chat_id
from telegram_documentaries.state import (
    STATE_VERSION,
    Phase,
    SessionState,
    SessionStore,
)

_CHAT_A = as_chat_id(1001)
_CHAT_B = as_chat_id(2002)


def test_a_fresh_chat_starts_awaiting_a_photo() -> None:
    store = SessionStore()

    state = store.get(_CHAT_A)

    assert state.phase is Phase.AWAITING_PHOTO
    assert state.last_file_id is None
    assert state.version == STATE_VERSION


def test_note_photo_records_the_file_reference() -> None:
    store = SessionStore()

    store.note_photo(_CHAT_A, "file-abc")

    state = store.get(_CHAT_A)
    assert state.last_file_id == "file-abc"
    assert state.phase is Phase.AWAITING_PHOTO


def test_note_photo_is_idempotent_for_the_same_reference() -> None:
    store = SessionStore()

    store.note_photo(_CHAT_A, "file-abc")
    store.note_photo(_CHAT_A, "file-abc")

    assert store.get(_CHAT_A).last_file_id == "file-abc"


def test_reset_clears_the_reference_and_returns_to_awaiting_photo() -> None:
    store = SessionStore()
    store.note_photo(_CHAT_A, "file-abc")

    store.reset(_CHAT_A)

    state = store.get(_CHAT_A)
    assert state.last_file_id is None
    assert state.phase is Phase.AWAITING_PHOTO


def test_reset_on_an_unknown_chat_is_harmless() -> None:
    store = SessionStore()

    store.reset(_CHAT_A)

    assert store.get(_CHAT_A).last_file_id is None


def test_a_stale_versioned_state_is_discarded_on_read() -> None:
    stale = SessionState(version=STATE_VERSION - 1, last_file_id="old-file")
    store = SessionStore(initial={_CHAT_A: stale})

    state = store.get(_CHAT_A)

    assert state.version == STATE_VERSION
    assert state.last_file_id is None
    assert state.phase is Phase.AWAITING_PHOTO


def test_a_writes_after_a_stale_state_is_discarded_are_clean() -> None:
    stale = SessionState(version=STATE_VERSION - 1, last_file_id="old-file")
    store = SessionStore(initial={_CHAT_A: stale})

    store.get(_CHAT_A)
    store.note_photo(_CHAT_A, "fresh-file")

    assert store.get(_CHAT_A).last_file_id == "fresh-file"


def test_two_chats_keep_independent_state() -> None:
    store = SessionStore()
    store.note_photo(_CHAT_A, "file-a")

    assert store.get(_CHAT_A).last_file_id == "file-a"
    assert store.get(_CHAT_B).last_file_id is None


def test_resetting_one_chat_leaves_the_other_intact() -> None:
    store = SessionStore()
    store.note_photo(_CHAT_A, "file-a")
    store.note_photo(_CHAT_B, "file-b")

    store.reset(_CHAT_A)

    assert store.get(_CHAT_A).last_file_id is None
    assert store.get(_CHAT_B).last_file_id == "file-b"


def test_string_and_integer_chat_ids_address_the_same_session() -> None:
    store = SessionStore()

    store.note_photo(as_chat_id("1001"), "file-a")

    assert store.get(_CHAT_A).last_file_id == "file-a"


def test_only_idling_phases_are_declared_as_drivable_in_this_phase() -> None:
    assert Phase.IDLE.value == "idle"
    assert Phase.AWAITING_PHOTO.value == "awaiting_photo"
