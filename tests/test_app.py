"""Tests for the application builder's update-processing configuration.

The builder is exercised offline: building a PTB ``Application`` with a
fake-shaped token makes no API call, and polling is never started. The test pins
the concurrency setting so a ``/restart`` or duplicate update is processed while
a generation is awaiting the model, rather than queued behind it.
"""

from __future__ import annotations

import telegram_documentaries.app as app_module

TEST_TOKEN = "123456:TEST-token-value"
TEST_KEY = "test-gemini-key-value"


def test_built_application_processes_updates_concurrently() -> None:
    application = app_module._build_application(TEST_TOKEN, TEST_KEY)

    # PTB's default is 1 (serial). Anything above 1 lets /restart and duplicate
    # messages run while a generation is blocked on the model.
    assert application.update_processor.max_concurrent_updates > 1
