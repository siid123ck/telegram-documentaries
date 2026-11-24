"""Tests for structured logging, idempotent setup, and the stage decorator."""

from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr
from telegram.ext import Application

from telegram_documentaries.config import Settings
from telegram_documentaries.logging_setup import configure_logging, log_stage

SECRET_TOKEN = "super-secret-telegram-token-value"
SECRET_KEY = "super-secret-gemini-key-value"


def _managed_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [
        handler
        for handler in root.handlers
        if getattr(handler, "_telegram_documentaries_handler", False)
    ]


def test_configure_logging_twice_does_not_duplicate_handlers() -> None:
    configure_logging()
    first = _managed_handlers()
    configure_logging()
    second = _managed_handlers()

    assert len(first) == 1
    assert second == first


def test_configure_logging_emits_structured_key_value_records() -> None:
    configure_logging()
    handler = _managed_handlers()[0]
    record = logging.LogRecord(
        name="telegram_documentaries.example",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    rendered = handler.format(record)

    assert "level=INFO" in rendered
    assert "logger=telegram_documentaries.example" in rendered
    assert "message=hello" in rendered


def test_log_stage_logs_entry_and_successful_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @log_stage
    def stage(value: int) -> int:
        return value * 2

    with caplog.at_level(logging.INFO):
        assert stage(2) == 4

    messages = [record.getMessage() for record in caplog.records]
    assert any("stage_start" in message and "stage=" in message for message in messages)
    assert any(
        "stage_end" in message and "outcome=ok" in message for message in messages
    )
    assert any("duration_ms=" in message for message in messages)


async def test_log_stage_wraps_async_functions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @log_stage
    async def async_stage() -> str:
        return "done"

    with caplog.at_level(logging.INFO):
        assert await async_stage() == "done"

    messages = [record.getMessage() for record in caplog.records]
    assert any("stage_start" in message for message in messages)
    assert any(
        "stage_end" in message and "outcome=ok" in message for message in messages
    )


def test_log_stage_never_renders_secret_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = SecretStr(SECRET_TOKEN)

    @log_stage
    def stage(value: SecretStr) -> str:
        return value.get_secret_value()[:4]

    with caplog.at_level(logging.DEBUG):
        assert stage(secret) == "supe"

    assert SECRET_TOKEN not in caplog.text


def test_logging_a_settings_object_never_renders_secret_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        telegram_bot_token=SecretStr(SECRET_TOKEN),
        gemini_api_key=SecretStr(SECRET_KEY),
    )
    logger = logging.getLogger("telegram_documentaries.tests")

    @log_stage
    def stage() -> None:
        logger.info("settings=%s", settings)

    with caplog.at_level(logging.INFO):
        stage()

    assert SECRET_TOKEN not in caplog.text
    assert SECRET_KEY not in caplog.text


def test_log_stage_logs_exception_type_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @log_stage
    def failing_stage() -> None:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="boom"):
        failing_stage()

    error_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ]
    assert any(
        "stage_error" in message and "error_type=ValueError" in message
        for message in error_messages
    )
    assert "Traceback" not in caplog.text


# --- Bot token never reaches the logs ------------------------------------------

#: A well-formed but fake Telegram-style token. It is never a real credential.
FAKE_BOT_TOKEN = "123456:FAKE-TOKEN-FOR-TESTS-ONLY"


def test_httpx_request_urls_carrying_the_bot_token_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True, "result": {}})
    )

    with (
        caplog.at_level(logging.INFO),
        httpx.Client(transport=transport) as client,
    ):
        client.get(f"https://api.telegram.org/bot{FAKE_BOT_TOKEN}/getMe")

    assert FAKE_BOT_TOKEN not in caplog.text
    assert "HTTP Request" not in caplog.text


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore"])
def test_http_client_loggers_are_pinned_to_warning(logger_name: str) -> None:
    configure_logging()

    logger = logging.getLogger(logger_name)

    assert not logger.isEnabledFor(logging.INFO)
    assert logger.isEnabledFor(logging.WARNING)


def test_ptb_debug_logs_never_show_the_bot_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # PTB logs "Set Bot API URL: https://api.telegram.org/bot<TOKEN>" at DEBUG.
    # Even when debugging is turned up, that token must not reach the log.
    configure_logging()

    with caplog.at_level(logging.DEBUG):
        Application.builder().token(FAKE_BOT_TOKEN).build()

    assert FAKE_BOT_TOKEN not in caplog.text


@pytest.mark.parametrize("logger_name", ["telegram"])
def test_python_telegram_bot_loggers_are_pinned_to_info(logger_name: str) -> None:
    configure_logging()

    assert not logging.getLogger(logger_name).isEnabledFor(logging.DEBUG)
    assert logging.getLogger(logger_name).isEnabledFor(logging.INFO)
