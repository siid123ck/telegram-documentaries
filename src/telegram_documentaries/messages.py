"""Single-sourced, user-facing copy.

Nothing here contains a provider code, stack trace, prompt fragment, or model
payload. The confirmation deliberately re-exports the Phase 1 constant so the
fixed reply literal exists in exactly one module.
"""

from __future__ import annotations

import re

from telegram_documentaries.gateway import REPLY_TEXT

#: Pass-path confirmation (aliased, never duplicated).
CONFIRMATION_TEXT = REPLY_TEXT

#: Cheeky in-character rejection; ``subject`` is the model's plain object name.
REJECTION_TEMPLATE = (
    "Nice {subject} — but this documentary needs a human. Send me a photo of a person."
)

#: Gentle, in-character nudge for any vision failure.
RESEND_TEXT = "Vision's acting up — mind resending the photo?"

#: Sent when a second photo arrives while the chat's first photo is still checked.
ONE_AT_A_TIME_TEXT = "One at a time, please — I'm still checking your last photo."

#: Nudges for malformed uploads at the photo gate.
EMPTY_PHOTO_TEXT = "I couldn't read that photo — mind sending another one?"
OVERSIZE_PHOTO_TEXT = "That photo's too big — send me a smaller one, would you?"

#: Longest subject a user will ever see in the rejection template.
MAX_SUBJECT_CHARS = 40

#: Used when the model's subject has nothing readable left after sanitising.
FALLBACK_SUBJECT = "thing"

#: Anything that is not a letter, digit, underscore, apostrophe, hyphen, or space.
_UNSAFE_SUBJECT_CHARS = re.compile(r"[^\w '\-]+")


def render_subject(raw: str) -> str:
    """Reduce model-supplied ``detected_subject`` text to plain, bounded words.

    The subject comes from a model and may contain markup, newlines, or injected
    instructions. Only word characters survive, whitespace is collapsed, and the
    result is capped so the rejection can never echo arbitrary model prose.
    """
    plain = " ".join(_UNSAFE_SUBJECT_CHARS.sub(" ", raw).split())
    capped = plain[:MAX_SUBJECT_CHARS].rstrip()
    return capped or FALLBACK_SUBJECT
