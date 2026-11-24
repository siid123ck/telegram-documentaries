"""Bouncer stage: classify a photo and return a typed verdict.

This module has no Telegram or ADK dependency, writes no session state, and emits
no user-facing text: it returns a :class:`VisionVerdict` for the routing layer to
act on. It talks to the vision provider only through the :class:`VisionClient`
protocol.
"""

from __future__ import annotations

import logging

from telegram_documentaries.errors import InvalidResponse
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.models import PhotoPayload, VisionVerdict
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
async def inspect(photo: PhotoPayload, client: VisionClient) -> VisionVerdict:
    """Classify ``photo`` and return the verdict. The Bouncer writes no state.

    An invalid model response gets exactly one repair attempt that carries the
    validation summary; a second invalid response raises :class:`InvalidResponse`.
    Recording the photo is the interview's job: it happens only when the first
    question is committed, so a rejected or failed photo never reaches the session.
    The verdict is returned unchanged and no message is emitted here.
    """
    try:
        verdict = await classify_with_retry(client, photo)
    except InvalidResponse as exc:
        verdict = await classify_with_retry(
            client, photo, repair_note=_repair_note(exc)
        )
    logger.info(
        "bouncer_verdict chat_id=%s contains_human=%s detected_subject=%s",
        photo.chat_id,
        verdict.contains_human,
        verdict.detected_subject,
    )
    return verdict
