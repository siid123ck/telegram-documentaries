"""Typed configuration loaded from the environment or a local ``.env`` file."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telegram_documentaries.errors import ConfigError

ENV_FILE = ".env"
REMEDIATION = "Set real values in .env and see .env.example."

#: Single source of truth for model IDs (TECH.md). Changing a model is a one-line
#: change here; no other module may hard-code a provider model string.
_FLASH_LITE = "gemini-3.1-flash-lite"
_IMAGE = "gemini-3.1-flash-image"
_TTS = "gemini-3.1-flash-tts-preview"
MODELS: dict[str, str] = {
    "bouncer": _FLASH_LITE,
    "interviewer": _FLASH_LITE,
    "scripter": _FLASH_LITE,
    "converter": _IMAGE,
    "narrator": _TTS,
}

#: The Gemini prebuilt speech voice for the Narrator (requirements N7). It is set
#: here only; logic reads this constant. Provisional: the Tech Lead confirms it
#: after a live audition (Open decision 3).
NARRATOR_VOICE = "Charon"

#: Maps the pydantic field name to the environment variable it reads.
FIELD_TO_ENV = {
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "gemini_api_key": "GEMINI_API_KEY",
}

#: Known placeholder markers that must never reach a running process.
PLACEHOLDER_MARKERS = ("your_", "_here")


class Settings(BaseSettings):
    """Validated settings at the application boundary.

    Both secrets are wrapped in :class:`~pydantic.SecretStr` so they cannot be
    interpolated into logs or repr output by accident. The Gemini key is not
    used in Phase 1, but it is validated now so later phases cannot start
    half-configured.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr

    @field_validator("telegram_bot_token", "gemini_api_key")
    @classmethod
    def reject_placeholder(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        raw_value = value.get_secret_value().strip()
        if not raw_value:
            raise ValueError("must not be empty")
        if any(marker in raw_value.lower() for marker in PLACEHOLDER_MARKERS):
            raise ValueError("placeholder value detected")
        return value


def _problem_reason(error_type: str, message: str) -> str:
    if error_type == "missing":
        return "missing"
    lowered = message.lower()
    if "placeholder" in lowered:
        return "placeholder"
    if "empty" in lowered:
        return "empty"
    return "invalid"


def load_settings() -> Settings:
    """Load and validate settings, raising a single-line :class:`ConfigError`.

    Pydantic's detailed validation error is reduced to one line that names the
    offending environment variables and points at ``.env.example``. No secret
    value is ever included in the message.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems: list[str] = []
        for error in exc.errors():
            location = error.get("loc", ())
            field = str(location[0]) if location else "settings"
            env_name = FIELD_TO_ENV.get(field, field.upper())
            reason = _problem_reason(
                str(error.get("type", "")), str(error.get("msg", ""))
            )
            problem = f"{env_name} ({reason})"
            if problem not in problems:
                problems.append(problem)

        detail = ", ".join(problems) if problems else "invalid configuration"
        raise ConfigError(f"{detail}. {REMEDIATION}") from None


def describe_config_source() -> str:
    """Return a secret-free description of where configuration comes from."""
    return f"file:{ENV_FILE}" if Path(ENV_FILE).is_file() else "environment"
