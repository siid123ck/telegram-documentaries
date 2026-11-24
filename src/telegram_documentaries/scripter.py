"""The Scripter stage: turn a dossier into one validated narration paragraph.

:func:`generate_script` asks the :class:`ScriptClient` for a script, validates the
reply into :class:`Script`, and makes at most two attempts in total. A validation
failure on attempt 1 triggers exactly one repair call. The repair note names the
rule that failed and the measured word count, and never the rejected text, so no
generated prose is sent back to the model or written to a log.

Provider errors are not repaired here. They propagate to the caller unchanged.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Final

from pydantic import ValidationError

from telegram_documentaries.errors import (
    ProviderTimeout,
    RateLimited,
    StaleResult,
    VisionUnavailable,
)
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.scripter_client import ScriptClient
from telegram_documentaries.scripter_models import Script
from telegram_documentaries.vision_client import RETRY_BACKOFF_SECONDS

#: Total model attempts across validation and provider failures (requirements S1).
MAX_ATTEMPTS: Final = 2

#: Provider failures that get one plain retry after the backoff (S12). Every other
#: provider error, such as ``ProviderRejected``, fails at once.
_RETRYABLE: Final = (ProviderTimeout, RateLimited, VisionUnavailable)

#: Matches the reason code and count that ``scripter_models`` puts in each error,
#: for example ``"Value error, word_count_low (words=59)"``.
_REJECTION_PATTERN: Final = re.compile(r"(\w+) \(words=(\d+)\)")


def _repair_note(error: ValidationError) -> str | None:
    """Build the S2 note from a validation error: the rule and the count only.

    Returns ``None`` when the error does not carry a recognised rejection, so the
    caller re-raises it rather than sending an unhelpful note.
    """
    for detail in error.errors():
        match = _REJECTION_PATTERN.search(str(detail.get("msg", "")))
        if match is not None:
            rule, count = match.group(1), match.group(2)
            return (
                f"The previous script was rejected by rule {rule} "
                f"with {count} words. Rewrite it to satisfy the rules."
            )
    return None


async def generate_script(
    dossier: Dossier, client: ScriptClient, *, still_current: Callable[[], bool]
) -> str:
    """Return a validated script for ``dossier``, using at most two attempts.

    Before every attempt, ``still_current`` is checked, and a stale chat raises
    :class:`StaleResult` with no model call (the paid-call guard). A validation
    failure on attempt 1 triggers one repair call with no backoff. A retryable
    provider failure (timeout, 429, 5xx) triggers one plain retry after
    ``RETRY_BACKOFF_SECONDS``, with no repair note. Any failure on the last attempt,
    and any other provider error, is raised to the caller.
    """
    repair_note: str | None = None
    backoff = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not still_current():
            raise StaleResult()
        if backoff:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            backoff = False
        try:
            raw = await client.write(dossier, repair_note=repair_note)
        except _RETRYABLE:
            if attempt == MAX_ATTEMPTS:
                raise
            repair_note = None
            backoff = True
            continue
        try:
            return Script.model_validate({"script": raw}).script
        except ValidationError as error:
            note = _repair_note(error)
            if attempt == MAX_ATTEMPTS or note is None:
                raise
            repair_note = note
    raise AssertionError("unreachable: the loop returns or raises")
