"""Tests for the Phase 1 gateway: text in, fixed reply out.

All Telegram objects here are constructed locally as plain data classes; no
``Bot`` is created and no network call is made. The async handler is exercised
through an injected sender seam so the send path is observable offline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TypeAlias

import pytest
from telegram import Chat, Message, PhotoSize, Sticker, Update, Voice

from telegram_documentaries.gateway import REPLY_TEXT, build_reply, handle_message

ReplySender: TypeAlias = Callable[[Update, str], Awaitable[None]]

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


async def test_handler_sends_the_reply_without_touching_the_network() -> None:
    calls: list[tuple[Update, str]] = []

    async def fake_sender(update: Update, text: str) -> None:
        calls.append((update, text))

    update = _text_update("Hello")
    await handle_message(update, None, sender=fake_sender)  # type: ignore[arg-type]

    assert calls == [(update, REPLY_TEXT)]


async def test_handler_does_not_send_for_non_text_updates() -> None:
    calls: list[tuple[Update, str]] = []

    async def fake_sender(update: Update, text: str) -> None:
        calls.append((update, text))

    for update in _non_text_updates():
        await handle_message(update, None, sender=fake_sender)  # type: ignore[arg-type]

    assert calls == []


async def test_handler_catches_send_error_logs_it_and_serves_next_update(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BoomSender:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def __call__(self, update: Update, text: str) -> None:
            self.calls.append(text)
            if len(self.calls) == 1:
                raise RuntimeError("send failed")

    boom = BoomSender()

    with caplog.at_level(logging.ERROR):
        await handle_message(_text_update("first"), None, sender=boom)  # type: ignore[arg-type]

    assert any(record.levelno >= logging.ERROR for record in caplog.records)

    async def good_sender(update: Update, text: str) -> None:
        boom.calls.append(text)

    await handle_message(_text_update("second"), None, sender=good_sender)  # type: ignore[arg-type]
    assert boom.calls == [REPLY_TEXT, REPLY_TEXT]
