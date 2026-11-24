"""Tests for the application builder's update-processing configuration.

The builder is exercised offline: building a PTB ``Application`` with a
fake-shaped token makes no API call, and polling is never started. The test pins
the concurrency setting so a ``/restart`` or duplicate update is processed while
a generation is awaiting the model, rather than queued behind it.
"""

from __future__ import annotations

from typing import Any

import pytest

import telegram_documentaries.app as app_module
from telegram_documentaries.generation import telegram_text_sender
from telegram_documentaries.models import as_chat_id
from telegram_documentaries.narrator_client import GeminiNarrationClient
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.scripter_client import AdkScriptClient

TEST_TOKEN = "123456:TEST-token-value"
TEST_KEY = "test-gemini-key-value"


def test_built_application_processes_updates_concurrently() -> None:
    application = app_module._build_application(TEST_TOKEN, TEST_KEY)

    # PTB's default is 1 (serial). Anything above 1 lets /restart and duplicate
    # messages run while a generation is blocked on the model.
    assert application.update_processor.max_concurrent_updates > 1


def test_the_router_is_wired_with_the_script_client_and_a_text_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def spy(**kwargs: Any) -> UpdateRouter:
        captured.update(kwargs)
        return UpdateRouter(**kwargs)

    monkeypatch.setattr(app_module, "UpdateRouter", spy)

    app_module._build_application(TEST_TOKEN, TEST_KEY)

    assert isinstance(captured["scripts"], AdkScriptClient)
    assert callable(captured["send_text"])


def test_the_router_is_wired_with_the_narration_client_and_a_voice_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def spy(**kwargs: Any) -> UpdateRouter:
        captured.update(kwargs)
        return UpdateRouter(**kwargs)

    monkeypatch.setattr(app_module, "UpdateRouter", spy)

    app_module._build_application(TEST_TOKEN, TEST_KEY)

    assert isinstance(captured["narrator"], GeminiNarrationClient)
    assert callable(captured["send_voice"])


class _FakeTextBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> object:
        self.sent.append((chat_id, text))
        return None


async def test_the_text_sender_posts_the_script_to_the_chat_as_a_message() -> None:
    bot = _FakeTextBot()

    await telegram_text_sender(bot)(as_chat_id(4242), "Once upon a meerkat.")

    assert bot.sent == [(4242, "Once upon a meerkat.")]
