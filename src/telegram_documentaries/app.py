"""Application entrypoint: validate config, wire the gateway, start polling.

Configuration is validated before any Telegram object is built, so a missing or
placeholder secret produces exactly one clear line on stderr and no traceback.
The thin seams (:func:`_build_application`, :func:`_run_polling`) let tests
verify startup without contacting Telegram.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, TypeAlias

from telegram.ext import Application, MessageHandler, filters

from telegram_documentaries import __version__
from telegram_documentaries.config import describe_config_source, load_settings
from telegram_documentaries.errors import ConfigError
from telegram_documentaries.gateway import handle_message
from telegram_documentaries.logging_setup import configure_logging

logger = logging.getLogger(__name__)

#: PTB's ``Application`` is generic over six parameters; none are specialised
#: by the Phase 1 gateway, so a single alias keeps the seam signatures readable.
TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]


def _build_application(token: str) -> TelegramApplication:
    """Build the PTB application and register the Phase 1 text handler."""
    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    return application


def _run_polling(application: TelegramApplication) -> None:
    """Start long polling (the single seam that touches the network)."""
    application.run_polling()


def main() -> None:
    """Run the bot from a cold start, failing fast on bad configuration."""
    configure_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        # The ConfigError message is already a single, secret-free line.
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None

    application = _build_application(settings.telegram_bot_token.get_secret_value())
    logger.info(
        "polling started version=%s config=%s",
        __version__,
        describe_config_source(),
    )
    _run_polling(application)
