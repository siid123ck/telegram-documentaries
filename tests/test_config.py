"""Tests for the typed settings boundary.

The suite must never depend on the developer's real ``.env`` file, so every
test runs in an isolated temporary working directory with the relevant
environment variables cleared first.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import telegram_documentaries.config as config_module
from telegram_documentaries.config import MODELS, Settings, load_settings
from telegram_documentaries.errors import ConfigError

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
KEY_VAR = "GEMINI_API_KEY"
VALID_TOKEN = "test-telegram-token-value"
VALID_KEY = "test-gemini-key-value"
PLACEHOLDER_TOKEN = "your_telegram_bot_token_here"
PLACEHOLDER_KEY = "your_gemini_api_key_here"


@pytest.fixture(autouse=True)
def isolated_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep settings tests away from the real repo ``.env`` and environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.delenv(KEY_VAR, raising=False)


def _set_valid_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_VAR, VALID_TOKEN)
    monkeypatch.setenv(KEY_VAR, VALID_KEY)


def test_missing_token_raises_config_error_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VAR, VALID_KEY)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert TOKEN_VAR in message
    assert message.count(TOKEN_VAR) == 1
    assert "see .env.example" in message
    assert "\n" not in message


def test_placeholder_token_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VAR, PLACEHOLDER_TOKEN)
    monkeypatch.setenv(KEY_VAR, VALID_KEY)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert TOKEN_VAR in message
    assert "placeholder" in message.lower()
    assert "see .env.example" in message
    assert "\n" not in message


def test_empty_token_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_VAR, "")
    monkeypatch.setenv(KEY_VAR, VALID_KEY)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    assert TOKEN_VAR in str(excinfo.value)


def test_missing_key_raises_config_error_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VAR, VALID_TOKEN)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert KEY_VAR in message
    assert message.count(KEY_VAR) == 1
    assert "see .env.example" in message
    assert "\n" not in message


def test_placeholder_key_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VAR, VALID_TOKEN)
    monkeypatch.setenv(KEY_VAR, PLACEHOLDER_KEY)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert KEY_VAR in message
    assert "placeholder" in message.lower()
    assert "see .env.example" in message
    assert "\n" not in message


def test_valid_pair_constructs_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_pair(monkeypatch)

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.telegram_bot_token.get_secret_value() == VALID_TOKEN
    assert settings.gemini_api_key.get_secret_value() == VALID_KEY


def test_settings_repr_and_str_never_reveal_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_pair(monkeypatch)
    settings = load_settings()

    rendered = f"{settings!r} {settings!s}"

    assert VALID_TOKEN not in rendered
    assert VALID_KEY not in rendered


def test_error_message_never_contains_other_configured_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VAR, VALID_TOKEN)
    monkeypatch.setenv(KEY_VAR, PLACEHOLDER_KEY)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    assert VALID_TOKEN not in str(excinfo.value)


def test_multiple_problems_are_reported_in_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert TOKEN_VAR in message
    assert KEY_VAR in message
    assert "\n" not in message
    assert "see .env.example" in message


def test_load_settings_does_not_log_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _set_valid_pair(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        settings = load_settings()

    assert settings.telegram_bot_token.get_secret_value() == VALID_TOKEN
    assert VALID_TOKEN not in caplog.text
    assert VALID_KEY not in caplog.text


# --- Phase 4: the converter model is defined once -----------------------------

IMAGE_MODEL = "gemini-3.1-flash-image"


def test_the_converter_model_is_the_image_model() -> None:
    assert MODELS["converter"] == IMAGE_MODEL


def test_the_existing_model_entries_are_unchanged() -> None:
    assert MODELS["bouncer"] == "gemini-3.1-flash-lite"
    assert MODELS["interviewer"] == "gemini-3.1-flash-lite"


def test_the_image_model_id_is_written_exactly_once_in_the_source_tree() -> None:
    package_dir = Path(config_module.__file__).parent

    occurrences = sum(
        path.read_text(encoding="utf-8").count(IMAGE_MODEL)
        for path in package_dir.rglob("*.py")
    )

    assert occurrences == 1


# --- Phase 5: the Scripter model is defined once -------------------------------

FLASH_LITE_MODEL = "gemini-3.1-flash-lite"


def test_the_scripter_model_is_the_flash_lite_model() -> None:
    assert MODELS["scripter"] == FLASH_LITE_MODEL


def test_the_flash_lite_model_id_is_written_once_in_config_module() -> None:
    config_source = Path(config_module.__file__).read_text(encoding="utf-8")

    assert config_source.count(FLASH_LITE_MODEL) == 1


# --- Phase 6: the narrator model and voice are defined once -------------------

TTS_MODEL = "gemini-3.1-flash-tts-preview"


def test_the_narrator_model_is_the_tts_model() -> None:
    assert MODELS["narrator"] == TTS_MODEL


def test_the_bouncer_interviewer_and_scripter_entries_are_unchanged_by_phase_six() -> (
    None
):
    assert MODELS["bouncer"] == FLASH_LITE_MODEL
    assert MODELS["interviewer"] == FLASH_LITE_MODEL
    assert MODELS["scripter"] == FLASH_LITE_MODEL
    assert MODELS["converter"] == IMAGE_MODEL


def test_the_narrator_voice_is_a_non_empty_string_in_config() -> None:
    # getattr: the name does not exist yet, so mypy must not fail before pytest runs.
    voice = getattr(config_module, "NARRATOR_VOICE")  # noqa: B009

    assert isinstance(voice, str)
    assert voice.strip()


def test_the_tts_model_id_is_written_exactly_once_in_the_source_tree() -> None:
    package_dir = Path(config_module.__file__).parent

    occurrences = sum(
        path.read_text(encoding="utf-8").count(TTS_MODEL)
        for path in package_dir.rglob("*.py")
    )

    assert occurrences == 1
