"""The Narrator stage: turn the script text into a checked :class:`VoiceNote`.

:func:`generate_narration` asks the :class:`NarrationClient` for audio, checks the
encoded MP3 into :class:`VoiceNote`, and makes at most two attempts in total. The
client already checks the raw reply into :class:`TtsReply`, so a malformed reply
arrives here as an :class:`InvalidResponse`, and an unencodable one as a ``bad_mp3``
``ValueError``. Neither is retried: the same input would fail again.

Only a timeout, a 429, or a 5xx gets one plain retry, after ``RETRY_BACKOFF_SECONDS``.
Every other provider error fails at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Final

from telegram_documentaries.config import NARRATOR_VOICE
from telegram_documentaries.errors import (
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionUnavailable,
)
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.narrator_audio import encode_mp3
from telegram_documentaries.narrator_client import NarrationClient
from telegram_documentaries.narrator_models import TtsReply, VoiceNote
from telegram_documentaries.vision_client import RETRY_BACKOFF_SECONDS

#: Total synthesis attempts per run (requirements N1). There is no repair attempt.
MAX_ATTEMPTS: Final = 2

#: Provider failures that get one plain retry after the backoff. Every other
#: provider error, such as ``ProviderRejected``, fails at once.
_RETRYABLE: Final = (ProviderTimeout, RateLimited, VisionUnavailable)


def _attempt_stage(
    attempt: int,
) -> Callable[[str, NarrationClient], Coroutine[Any, Any, TtsReply]]:
    """Return one provider call for ``attempt``, wrapped by ``@log_stage``.

    ``log_stage`` names each record after the function, so each attempt gets its
    own stage name (``synthesise_attempt_1``, ``synthesise_attempt_2``). That is
    how the attempt number reaches the log. Arguments are never logged, so the
    script text and the client never appear in these records.
    """

    async def synthesise_attempt(text: str, client: NarrationClient) -> TtsReply:
        return await client.synthesise(text, voice=NARRATOR_VOICE)

    synthesise_attempt.__qualname__ = f"synthesise_attempt_{attempt}"
    return log_stage(synthesise_attempt)


@log_stage
async def generate_narration(
    text: str,
    client: NarrationClient,
    *,
    still_current: Callable[[], bool],
    encode: Callable[[TtsReply], VoiceNote] = encode_mp3,
) -> VoiceNote:
    """Return a checked voice note for ``text``, using at most two attempts.

    ``still_current`` is checked immediately before every provider call. A stale chat
    raises :class:`StaleResult` with no model call (the paid-call guard). A retryable
    provider failure triggers one plain retry after ``RETRY_BACKOFF_SECONDS``. Any
    failure on the last attempt, any other provider error, and any malformed audio or
    encoder output is raised to the caller without a further attempt.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        if not still_current():
            raise StaleResult()
        try:
            reply = await _attempt_stage(attempt)(text, client)
        except _RETRYABLE:
            if attempt == MAX_ATTEMPTS:
                raise
            continue
        # Malformed audio is never retried: the encoder's rejection propagates.
        return encode(reply)
    raise AssertionError("unreachable: the loop returns or raises")
