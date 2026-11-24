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
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_documentaries import __version__
from telegram_documentaries.config import describe_config_source, load_settings
from telegram_documentaries.converter_client import AdkPortraitClient
from telegram_documentaries.errors import ConfigError
from telegram_documentaries.generation import (
    telegram_photo_sender,
    telegram_text_sender,
)
from telegram_documentaries.interview_client import AdkInterviewClient
from telegram_documentaries.logging_setup import configure_logging
from telegram_documentaries.photo_handler import telegram_downloader
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.scripter_client import AdkScriptClient
from telegram_documentaries.state import SessionStore
from telegram_documentaries.vision_client import AdkVisionClient

logger = logging.getLogger(__name__)

#: PTB's ``Application`` is generic over six parameters; none are specialised
#: by the Phase 1 gateway, so a single alias keeps the seam signatures readable.
TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]


UpdateCallback: TypeAlias = Callable[
    [Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]
]


def _update_callback(
    handler: Callable[[Update], Coroutine[Any, Any, None]],
) -> UpdateCallback:
    """Adapt a router entry point to PTB's handler signature."""

    async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await handler(update)

    return on_update


def _build_application(token: str, gemini_api_key: str) -> TelegramApplication:
    """Build the PTB application: ``/restart``, text, and photo routes.

    ``/restart`` is registered before the text route so PTB dispatches it to the
    reset handler rather than treating it as an answer. Both provider clients are
    constructed lazily, so building the application never imports the ADK or
    contacts the network.
    """
    # Concurrent updates: a /restart or a duplicate must be handled while a
    # generation is awaiting the model. PTB's default serialises updates.
    application = Application.builder().token(token).concurrent_updates(True).build()
    router = UpdateRouter(
        vision=AdkVisionClient(api_key=gemini_api_key),
        interview=AdkInterviewClient(api_key=gemini_api_key),
        store=SessionStore(),
        download=telegram_downloader(application.bot),
        portraits=AdkPortraitClient(api_key=gemini_api_key),
        send_photo=telegram_photo_sender(application.bot),
        scripts=AdkScriptClient(api_key=gemini_api_key),
        send_text=telegram_text_sender(application.bot),
    )
    restart_cb = _update_callback(router.handle_restart)
    text_cb = _update_callback(router.handle_text)
    photo_cb = _update_callback(router.handle_photo)
    application.add_handler(CommandHandler("restart", restart_cb))
    application.add_handler(MessageHandler(filters.TEXT, text_cb))
    application.add_handler(MessageHandler(filters.PHOTO, photo_cb))
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
