"""Typed contract for the Scripter's output: one plain-text narration paragraph.

The model's reply is untrusted. A :class:`Script` exists only when its text is a
single plain paragraph of 60 to 90 words with no markdown. The checks run in a
fixed order, and the first failure is reported with its reason code and the
measured word count. The text itself never appears in an error, because a
ValidationError would otherwise repeat the rejected prose into logs.
Nothing here imports the ADK or touches the network.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator

#: Word bounds for the narration, inclusive (S8). A word is one ``str.split`` token.
MIN_WORDS: Final = 60
MAX_WORDS: Final = 90

#: Characters that mark markdown (S10). This is a conservative deny-list.
#: Parentheses and apostrophes are deliberately allowed.
MARKDOWN_CHARS: Final[tuple[str, ...]] = (
    "*",
    "_",
    "#",
    "`",
    "~",
    "[",
    "]",
    "<",
    ">",
    "|",
    "\\",
)

#: A hyphen bullet at the start of a line (S10). Only the line start counts, so a
#: hyphen inside a sentence, or glued to a word, stays allowed. Because S9 has
#: already rejected line breaks, the start of the text is the only line start here.
_BULLET_PREFIX: Final = "- "

#: Line breaks that would split the text into more than one paragraph (S9).
_LINE_BREAKS: Final[tuple[str, ...]] = ("\n", "\r")

#: Reason codes, one per rule. They are the only detail an error carries.
ScriptRejection = Literal[
    "empty",
    "multi_paragraph",
    "word_count_low",
    "word_count_high",
    "markdown",
]


def _reject(reason: ScriptRejection, word_count: int) -> ValueError:
    """Build the error for a rule failure. It names the rule and the count only."""
    return ValueError(f"{reason} (words={word_count})")


def _check_paragraph(text: str) -> str:
    """Apply the S8 to S10 rules, in order, to already-stripped text."""
    if not text:
        raise _reject("empty", 0)
    if any(line_break in text for line_break in _LINE_BREAKS):
        raise _reject("multi_paragraph", len(text.split()))

    word_count = len(text.split())
    if word_count < MIN_WORDS:
        raise _reject("word_count_low", word_count)
    if word_count > MAX_WORDS:
        raise _reject("word_count_high", word_count)

    if text.startswith(_BULLET_PREFIX) or any(char in text for char in MARKDOWN_CHARS):
        raise _reject("markdown", word_count)
    return text


class Script(BaseModel):
    """The narration paragraph the Scripter returns, checked at construction.

    Leading and trailing whitespace is stripped before any rule is applied, so
    the stored text is the stripped text. ``hide_input_in_errors`` keeps the
    script out of the ValidationError message, which otherwise echoes the input.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    script: str

    @field_validator("script", mode="after")
    @classmethod
    def _one_plain_paragraph(cls, value: str) -> str:
        return _check_paragraph(value.strip())
