"""Tests for the phase-one reply rules: text in, fixed reply out.

All Telegram objects here are constructed locally as plain data classes; no
``Bot`` is created and no network call is made. Routing by phase is tested in
``test_interview_routing.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from telegram import Chat, Message, PhotoSize, Sticker, Update, Voice

from telegram_documentaries.gateway import REPLY_TEXT, build_reply

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _message(**kwargs: object) -> Message:
    return Message(message_id=1, date=_NOW, chat=Chat(id=42, type="private"), **kwargs)  # type: ignore[arg-type]


def _text_update(text: str, update_id: int = 1) -> Update:
    return Update(update_id=update_id, message=_message(text=text))


def _non_text_updates() -> Sequence[Update]:
    photo = PhotoSize(file_id="p", file_unique_id="pu", width=1, height=1)
    voice = Voice(file_id="v", file_unique_id="vu", duration=1)
    sticker = Sticker(
        file_id="s",
        file_unique_id="su",
        width=1,
        height=1,
        is_animated=False,
        is_video=False,
        type="regular",
    )
    return [
        Update(update_id=1, message=_message(photo=(photo,))),
        Update(update_id=2, message=_message(voice=voice)),
        Update(update_id=3, message=_message(sticker=sticker)),
    ]


def test_reply_constant_is_the_single_source_of_the_fixed_text() -> None:
    assert REPLY_TEXT == "hey mate!"


def test_text_message_produces_the_fixed_reply() -> None:
    assert build_reply(_text_update("Hello")) == REPLY_TEXT


def test_two_sequential_text_messages_produce_the_same_reply_twice() -> None:
    first = build_reply(_text_update("Hello", update_id=1))
    second = build_reply(_text_update("Different text", update_id=2))

    assert first == second == REPLY_TEXT


def test_empty_text_is_ignored() -> None:
    assert build_reply(_text_update("   ")) is None


@pytest.mark.parametrize(
    "update", _non_text_updates(), ids=["photo", "voice", "sticker"]
)
def test_non_text_message_produces_no_reply(update: Update) -> None:
    assert build_reply(update) is None


def test_edited_message_and_channel_post_produce_no_exception() -> None:
    edited = Update(update_id=1, edited_message=_message(text="edited"))
    channel = Update(update_id=2, channel_post=_message(text="channel post"))

    assert build_reply(edited) is None
    assert build_reply(channel) is None


def test_update_with_no_message_produces_no_reply() -> None:
    assert build_reply(Update(update_id=1)) is None
