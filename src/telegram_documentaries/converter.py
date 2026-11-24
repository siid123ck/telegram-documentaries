"""Converter stage: the original photo and the dossier in, one validated portrait out.

The stage calls the image model through the :class:`PortraitClient` seam and
turns the raw reply into a :class:`Portrait`. It applies the bounded retry policy
and nothing else. It never calls Telegram, never writes the session, and never
makes a repair call: a payload that is not an image is a final failure (D8).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import ValidationError

from telegram_documentaries.converter_client import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    PortraitClient,
)
from telegram_documentaries.converter_models import Portrait
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.models import ChatId

logger = logging.getLogger(__name__)

#: Errors that are safe to retry once. A rejected request and an invalid reply
#: are not among them: the same request would fail again, or a repair cannot
#: fix a missing image.
RETRYABLE_ERRORS: tuple[type[VisionError], ...] = (
    ProviderTimeout,
    RateLimited,
    VisionUnavailable,
)


@log_stage
async def generate_portrait(
    chat_id: ChatId,
    photo_bytes: bytes,
    dossier: Dossier,
    client: PortraitClient,
    *,
    still_current: Callable[[], bool],
) -> Portrait:
    """Render the hybrid portrait, with bounded retry and a validated result.

    Timeouts, rate limits, and 5xx errors are retried once after a short backoff.
    A rejected request fails at once. Bytes that are not a PNG or JPEG of a sane
    size raise :class:`InvalidResponse` with no retry. Only the error type and
    attempt count are logged, never the bytes or the dossier.

    ``still_current`` reports whether the run that owns this call is still live.
    It is checked before every model call, including each retry, so a run that a
    ``/restart`` abandoned makes no further paid call. It raises
    :class:`StaleResult` instead.
    """
    attempt = 0
    while True:
        if not still_current():
            raise StaleResult("the run was abandoned before the model call")
        attempt += 1
        try:
            raw = await client.render(photo_bytes, dossier)
        except RETRYABLE_ERRORS as exc:
            if attempt >= MAX_ATTEMPTS:
                logger.error(
                    "portrait_call_failed attempts=%d error_type=%s chat_id=%s",
                    attempt,
                    type(exc).__name__,
                    chat_id,
                )
                raise
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            continue
        try:
            return Portrait(image_bytes=raw)
        except ValidationError:
            # Raised with ``from None`` so the validation detail, which can echo
            # the raw bytes, is not kept as the exception context.
            logger.error(
                "portrait_response_invalid error_type=InvalidResponse chat_id=%s",
                chat_id,
            )
            raise InvalidResponse() from None
