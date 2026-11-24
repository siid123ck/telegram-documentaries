"""Tests for the startup entrypoint and its fail-fast behaviour.

The real Telegram network is never contacted: ``_run_polling`` is replaced with
a recorder. The real :func:`_build_application` is exercised offline (building
an ``Application`` with a fake-shaped token makes no API call), so the handler
registration is covered too. One black-box subprocess test proves ``python -m``
wires up correctly and fails cleanly on placeholder configuration.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from telegram.error import InvalidToken
from telegram.ext import Application, CommandHandler, MessageHandler

import telegram_documentaries.app as app_module
from telegram_documentaries import __version__, logging_setup
from telegram_documentaries.config import Settings
from telegram_documentaries.errors import ConfigError

TEST_TOKEN = "123456:TEST-token-value"
TEST_KEY = "test-gemini-key-value"
PLACEHOLDER_TOKEN = "your_telegram_bot_token_here"
PLACEHOLDER_KEY = "your_gemini_api_key_here"


class _Recorder:
    """Records calls to the polling seam without polling."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def build_application(self, token: str) -> object:
        self.calls.append(("build", token))
        return object()

    def run_polling(self, application: object) -> None:
        self.calls.append(("poll", application))


def _valid_settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr(TEST_TOKEN),
        gemini_api_key=SecretStr(TEST_KEY),
    )


def _patch_happy_path(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace load_settings and polling; leave real application building on."""
    polled: list[object] = []
    monkeypatch.setattr(app_module, "configure_logging", lambda: None)
    monkeypatch.setattr(app_module, "load_settings", _valid_settings)
    monkeypatch.setattr(app_module, "describe_config_source", lambda: "file:.env")

    def record_polling(application: object) -> None:
        polled.append(application)

    monkeypatch.setattr(app_module, "_run_polling", record_polling)
    return polled


def test_config_error_exits_2_with_one_stderr_line_and_no_polling(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(app_module, "configure_logging", lambda: None)

    def fail_settings() -> Settings:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN (missing). "
            "Set real values in .env and see .env.example."
        )

    monkeypatch.setattr(app_module, "load_settings", fail_settings)
    monkeypatch.setattr(app_module, "_build_application", recorder.build_application)
    monkeypatch.setattr(app_module, "_run_polling", recorder.run_polling)

    with pytest.raises(SystemExit) as excinfo:
        app_module.main()

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert stderr.count("\n") == 1
    assert "TELEGRAM_BOT_TOKEN" in stderr
    assert "Traceback" not in stderr
    assert recorder.calls == []


def test_valid_settings_build_the_app_and_start_polling_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polled = _patch_happy_path(monkeypatch)

    app_module.main()

    assert len(polled) == 1
    application = polled[0]
    assert isinstance(application, Application)
    # One /restart command, one text route, and one photo route.
    assert sum(len(handlers) for handlers in application.handlers.values()) == 3
    assert application.bot.token == TEST_TOKEN


def test_the_gemini_key_is_handed_to_the_vision_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_happy_path(monkeypatch)
    received_keys: list[str] = []

    class _RecordingVisionClient:
        def __init__(self, api_key: str) -> None:
            received_keys.append(api_key)

    monkeypatch.setattr(app_module, "AdkVisionClient", _RecordingVisionClient)

    app_module.main()

    assert received_keys == [TEST_KEY]


def test_a_rejected_bot_token_exits_2_with_one_line_and_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_happy_path(monkeypatch)
    # Use the real logging setup so the library-level guards are in effect.
    monkeypatch.setattr(
        app_module, "configure_logging", logging_setup.configure_logging
    )

    def reject_token(application: object) -> None:
        # PTB embeds the raw token in this message; it must never reach output.
        raise InvalidToken(f"The token `{TEST_TOKEN}` was rejected by the server.")

    monkeypatch.setattr(app_module, "_run_polling", reject_token)

    with caplog.at_level(logging.DEBUG), pytest.raises(SystemExit) as excinfo:
        app_module.main()

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert TEST_TOKEN not in captured.out
    assert TEST_TOKEN not in captured.err
    assert TEST_TOKEN not in caplog.text
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "TELEGRAM_BOT_TOKEN" in captured.err


def test_startup_logs_polling_started_version_and_source_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_happy_path(monkeypatch)

    with caplog.at_level(logging.INFO):
        app_module.main()

    assert "polling started" in caplog.text
    assert __version__ in caplog.text
    assert "file:.env" in caplog.text
    assert TEST_TOKEN not in caplog.text
    assert TEST_KEY not in caplog.text
    assert TEST_TOKEN not in capsys.readouterr().err


def test_module_entrypoint_with_placeholders_exits_2_without_traceback(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["TELEGRAM_BOT_TOKEN"] = PLACEHOLDER_TOKEN
    env["GEMINI_API_KEY"] = PLACEHOLDER_KEY

    result = subprocess.run(
        [sys.executable, "-m", "telegram_documentaries"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Traceback" not in combined
    assert result.stderr.count("\n") == 1
    assert "placeholder" in combined.lower()
    assert "see .env.example" in combined
    assert PLACEHOLDER_TOKEN not in combined
    assert PLACEHOLDER_KEY not in combined


def test_the_restart_command_is_routed_before_the_text_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polled = _patch_happy_path(monkeypatch)

    app_module.main()

    first_group = polled[0].handlers[0]  # type: ignore[attr-defined]
    assert isinstance(first_group[0], CommandHandler)
    assert "restart" in first_group[0].commands
    assert isinstance(first_group[1], MessageHandler)
