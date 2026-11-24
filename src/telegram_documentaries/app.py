"""Application entrypoint: validate config, wire the gateway, start polling.

Configuration is validated before any Telegram object is built, so a missing or
placeholder secret produces exactly one clear line on stderr and no traceback.
The thin seams (:func:`_build_application`, :func:`_run_polling`) let tests
verify startup without contacting Telegram.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeAlias

from telegram import Update
from telegram.error import InvalidToken
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from telegram_documentaries import __version__
from telegram_documentaries.config import describe_config_source, load_settings
from telegram_documentaries.errors import ConfigError
from telegram_documentaries.gateway import handle_message
from telegram_documentaries.logging_setup import configure_logging
from telegram_documentaries.photo_handler import PhotoGate, telegram_downloader
from telegram_documentaries.state import SessionStore
from telegram_documentaries.vision_client import AdkVisionClient

logger = logging.getLogger(__name__)

#: PTB's ``Application`` is generic over six parameters; none are specialised
#: by the Phase 1 gateway, so a single alias keeps the seam signatures readable.
TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]


PhotoCallback: TypeAlias = Callable[
    [Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]
]


def _photo_callback(gate: PhotoGate) -> PhotoCallback:
    """Adapt a :class:`PhotoGate` to PTB's handler signature."""

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await gate.handle(update)

    return on_photo


def _build_application(token: str, gemini_api_key: str) -> TelegramApplication:
    """Build the PTB application with the text (Phase 1) and photo (Bouncer) routes.

    The vision client is constructed lazily, so building the application never
    imports the ADK or contacts the network.
    """
    application = Application.builder().token(token).build()
    gate = PhotoGate(
        client=AdkVisionClient(api_key=gemini_api_key),
        store=SessionStore(),
        download=telegram_downloader(application.bot),
    )
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, _photo_callback(gate)))
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

    try:
        application = _build_application(
            settings.telegram_bot_token.get_secret_value(),
            settings.gemini_api_key.get_secret_value(),
        )
        logger.info(
            "polling started version=%s config=%s",
            __version__,
            describe_config_source(),
        )
        _run_polling(application)
    except InvalidToken:
        # PTB's message embeds the raw token, so the exception text is never printed.
        print(
            "Telegram rejected TELEGRAM_BOT_TOKEN. "
            "Check it in .env and see .env.example.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
