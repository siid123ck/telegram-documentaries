"""Structured logging setup and the ``@log_stage`` decorator.

Logging is configured once at startup. Every record is rendered as
space-separated ``key=value`` pairs so it can be consumed by structured log
collectors. Secret values (``SecretStr`` contents, tokens, API keys) must never
be interpolated into a log record; the decorator logs only function name,
outcome, duration, and error type — never arguments or return values.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar, cast

P = ParamSpec("P")
T = TypeVar("T")

#: Attribute set on the handler installed by :func:`configure_logging` so the
#: setup can be called idempotently without duplicating handlers.
_HANDLER_MARKER = "_telegram_documentaries_handler"

#: Library loggers pinned to a minimum level, so they can never write the bot
#: token into the logs. The Telegram API URL is ``/bot<TOKEN>/method``:
#: - httpx/httpcore record every request URL at INFO.
#: - python-telegram-bot logs the base URL (with the token) at DEBUG, so it is
#: pinned to INFO to silence all of its DEBUG output, including endpoint payloads.
_PINNED_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "telegram": logging.INFO,
}

#: LogRecord attribute keys that are part of the standard logging machinery and
#: should not be re-emitted as custom key=value fields.
_RESERVED_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "getMessage",
        "message",
        "asctime",
        "taskName",
    }
)


class _StructuredFormatter(logging.Formatter):
    """Render a LogRecord as ``key=value`` pairs.

    Includes the standard ``level``, ``logger``, and ``message`` fields, plus
    any ``extra=`` keywords passed to the logging call (e.g. ``stage=``,
    ``outcome=``, ``duration_ms=``, ``error_type=``).
    """

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            f"level={record.levelname}",
            f"logger={record.name}",
            f"message={record.getMessage()}",
        ]
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS:
                continue
            parts.append(f"{key}={value}")
        if record.exc_info:
            parts.append("exc_info=1")
        return " ".join(parts)


def configure_logging() -> None:
    """Configure structured logging once.

    Idempotent: calling it multiple times installs only a single handler on the
    root logger. The handler is marked so subsequent calls detect it and return
    early.
    """
    # Applied on every call (not just the first) so the token guard can never be
    # skipped; setting a level is itself idempotent.
    for name, level in _PINNED_LOGGER_LEVELS.items():
        logging.getLogger(name).setLevel(level)

    root = logging.getLogger()
    already_installed = any(
        getattr(handler, _HANDLER_MARKER, False) for handler in root.handlers
    )
    if already_installed:
        return

    handler = logging.StreamHandler()
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(_StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_stage(func: Callable[P, T]) -> Callable[P, T]:
    """Decorate a function to log stage entry, outcome, duration, and errors.

    The decorated function's module logger is used. On entry a ``stage_start``
    record is emitted; on success a ``stage_end`` record with ``outcome=ok`` and
    ``duration_ms=``; on failure a ``stage_error`` record with ``error_type=``.
    The exception is re-raised after logging. Arguments and return values are
    never logged, so secrets passed as arguments never appear in log output.
    The structured fields are embedded in the message string so that
    ``record.getMessage()`` contains them.
    """

    stage_name = getattr(func, "__qualname__", func.__name__)
    module_logger = logging.getLogger(func.__module__ or __name__)

    if asyncio.iscoroutinefunction(func):
        async_func = cast(Callable[P, Coroutine[Any, Any, T]], func)

        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            start = time.monotonic()
            module_logger.info("stage_start stage=%s", stage_name)
            try:
                result = await async_func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — log and re-raise
                duration_ms = (time.monotonic() - start) * 1000
                module_logger.error(
                    "stage_error stage=%s error_type=%s duration_ms=%.2f",
                    stage_name,
                    type(exc).__name__,
                    duration_ms,
                )
                raise
            duration_ms = (time.monotonic() - start) * 1000
            module_logger.info(
                "stage_end stage=%s outcome=ok duration_ms=%.2f",
                stage_name,
                duration_ms,
            )
            return result

        functools.update_wrapper(async_wrapper, func)
        return cast(Callable[P, T], async_wrapper)

    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        start = time.monotonic()
        module_logger.info("stage_start stage=%s", stage_name)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — log and re-raise
            duration_ms = (time.monotonic() - start) * 1000
            module_logger.error(
                "stage_error stage=%s error_type=%s duration_ms=%.2f",
                stage_name,
                type(exc).__name__,
                duration_ms,
            )
            raise
        duration_ms = (time.monotonic() - start) * 1000
        module_logger.info(
            "stage_end stage=%s outcome=ok duration_ms=%.2f",
            stage_name,
            duration_ms,
        )
        return result

    functools.update_wrapper(sync_wrapper, func)
    return sync_wrapper
