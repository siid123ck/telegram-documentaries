"""Bouncer stage: classify a photo and update minimal per-chat state.

This module has no Telegram or ADK dependency and emits no user-facing text: it
returns a typed :class:`VisionVerdict` for the routing layer to render. It talks
to the vision provider only through the :class:`VisionClient` protocol.
"""

from __future__ import annotations

import logging

from telegram_documentaries.errors import InvalidResponse
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.models import PhotoPayload, VisionVerdict
from telegram_documentaries.state import SessionStore
from telegram_documentaries.vision_client import VisionClient, classify_with_retry

logger = logging.getLogger(__name__)

#: Longest validation summary echoed back to the model in a repair request.
MAX_REPAIR_DETAIL_CHARS = 500


def _repair_note(error: InvalidResponse) -> str:
    """Build the one-shot repair request for an invalid model response."""
    detail = error.detail[:MAX_REPAIR_DETAIL_CHARS] or "the reply was not valid JSON"
    return (
        f"Your previous reply failed validation: {detail}. "
        "Reply again with only the single JSON object described in your instructions."
    )


@log_stage
async def inspect(
    photo: PhotoPayload, client: VisionClient, store: SessionStore
) -> VisionVerdict:
    """Classify ``photo``, then record the outcome in ``store``.

    An invalid model response gets exactly one repair attempt that carries the
    validation summary; a second invalid response raises :class:`InvalidResponse`.
    A passing verdict stores the photo reference; a failing one resets the chat.
    The verdict is returned unchanged and no message is emitted here. Provider
    errors propagate before any state mutation, so a failed call leaves the chat
    untouched.
    """
    try:
        verdict = await classify_with_retry(client, photo)
    except InvalidResponse as exc:
        verdict = await classify_with_retry(
            client, photo, repair_note=_repair_note(exc)
        )
    if verdict.contains_human:
        store.note_photo(photo.chat_id, photo.file_id)
    else:
        store.reset(photo.chat_id)
    logger.info(
        "bouncer_verdict chat_id=%s contains_human=%s detected_subject=%s",
        photo.chat_id,
        verdict.contains_human,
        verdict.detected_subject,
    )
    return verdict
