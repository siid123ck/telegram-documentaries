"""Single-sourced, user-facing copy.

Nothing here contains a provider code, stack trace, prompt fragment, or model
payload. The confirmation deliberately re-exports the Phase 1 constant so the
fixed reply literal exists in exactly one module.
"""

from __future__ import annotations

import re

from telegram_documentaries.gateway import REPLY_TEXT
from telegram_documentaries.interview_models import MAX_ANSWER_CHARS, Dossier

#: Text sent while the chat is awaiting a photo (Phase 1 reply, never duplicated).
HEY_MATE = REPLY_TEXT

#: Opening prompt sent after ``/restart``.
OPENING_PROMPT = "Send me a portrait photo to begin your documentary."

#: Nudge for a photo sent mid-interview; the Bouncer is not called.
PHOTO_MID_INTERVIEW_TEXT = (
    "I'm still mid-expedition with you — please answer the question above."
)

#: Sent after a provider failure on an answer; the answer is not committed.
RESEND_ANSWER_TEXT = "Signal's fuzzy — mind resending your last answer?"

#: Nudges for answers that are blank or over the length limit.
EMPTY_ANSWER_TEXT = "I didn't catch anything there — tell me a little more."
ANSWER_TOO_LONG_TEXT = (
    f"That's a lot to observe in one go — keep it under {MAX_ANSWER_CHARS} characters."
)

#: Sent for any text or photo after the dossier is complete.
ALREADY_DONE_TEXT = "Your dossier is complete. Send /restart for a new subject."

#: Defensive nudge for text that arrives with no question outstanding. Should
#: not happen in normal flow; it is logged at warning level.
NO_QUESTION_TEXT = "I'm not expecting an answer just now — send /restart to start over."

#: First line of every dossier message.
DOSSIER_HEADER = "Field notes complete. Here is your dossier:"

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


#: Sent when a portrait run fails. It offers ``/restart`` and carries no detail.
GENERATION_FAILED_TEXT = (
    "Sorry — the portrait studio jammed on that one. Send /restart to try again."
)

#: Second caption line. Fixed copy, not per-animal and never model prose (D16).
CAPTION_SECOND_LINE = "Filmed in its natural habitat, unaware it is being watched."


def render_caption(animal_name: str) -> str:
    """Return the two-line portrait caption.

    Line one names the animal, after :func:`render_subject` has removed markup,
    newlines, and any injected text and capped the length. Line two is fixed copy.
    """
    return f"Specimen: the {render_subject(animal_name)}.\n{CAPTION_SECOND_LINE}"


def render_dossier(dossier: Dossier) -> str:
    """Render the finished dossier as one plain-text message.

    Only the validated :class:`Dossier` fields are drawn on: one line per quirk,
    then the suggested animal and its reason. No model prose is passed through
    unvalidated, and no other field exists to render.
    """
    lines = [DOSSIER_HEADER, ""]
    lines.extend(f"- {quirk.topic}: {quirk.observation}" for quirk in dossier.quirks)
    lines.append("")
    lines.append(f"Suggested animal: {dossier.suggested_animal.name}")
    lines.append(f"Why: {dossier.suggested_animal.reason}")
    return "\n".join(lines)
