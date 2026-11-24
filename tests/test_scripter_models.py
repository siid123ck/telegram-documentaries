"""Tests for the Scripter's typed output contract, :class:`Script`.

A script is one plain-text paragraph of 60 to 90 words. The word count is
``len(text.split())`` on the stripped text, and both bounds are inclusive. The
checks run in a fixed order (empty, multi-paragraph, word count, markdown), and
the first failure is reported with its reason code and measured count only. The
script text itself never appears in an error.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from telegram_documentaries.scripter_models import (
    MARKDOWN_CHARS,
    MAX_WORDS,
    MIN_WORDS,
    Script,
)

#: A sentinel that must never surface in an error message or a log line.
SENTINEL = "SENTINEL_PARAGRAPH_DO_NOT_SHOW"


def _paragraph(word_count: int) -> str:
    """Return a plain paragraph of exactly ``word_count`` whitespace-separated words."""
    return " ".join(f"word{index}" for index in range(word_count))


def _error_text(text: str) -> str:
    """Build a script from ``text`` and return the ValidationError message."""
    with pytest.raises(ValidationError) as excinfo:
        Script(script=text)
    return str(excinfo.value)


def test_the_word_bounds_are_sixty_and_ninety() -> None:
    assert MIN_WORDS == 60
    assert MAX_WORDS == 90


def test_a_sixty_word_paragraph_is_accepted() -> None:
    script = Script(script=_paragraph(60))

    assert script.script == _paragraph(60)


def test_a_ninety_word_paragraph_is_accepted() -> None:
    script = Script(script=_paragraph(90))

    assert script.script == _paragraph(90)


def test_a_seventy_five_word_paragraph_is_accepted() -> None:
    script = Script(script=_paragraph(75))

    assert len(script.script.split()) == 75


def test_a_fifty_nine_word_paragraph_fails_as_word_count_low() -> None:
    message = _error_text(_paragraph(59))

    assert "word_count_low" in message
    assert "59" in message


def test_a_ninety_one_word_paragraph_fails_as_word_count_high() -> None:
    message = _error_text(_paragraph(91))

    assert "word_count_high" in message
    assert "91" in message


def test_a_hyphenated_word_counts_as_one_word() -> None:
    # Fifty-nine plain words plus one hyphenated word is sixty whitespace tokens.
    text = _paragraph(59) + " one-eyed"

    assert len(Script(script=text).script.split()) == 60


def test_a_numeral_counts_as_one_word() -> None:
    text = _paragraph(59) + " 1966"

    assert len(Script(script=text).script.split()) == 60


def test_leading_and_trailing_whitespace_is_stripped_before_counting() -> None:
    padded = "   \t" + _paragraph(60) + "  \n  "
    # The newline here is trailing whitespace, so it is stripped before the
    # single-paragraph check runs and the script is accepted.

    script = Script(script=padded)

    assert script.script == _paragraph(60)


def test_padding_does_not_lift_a_short_paragraph_over_the_floor() -> None:
    padded = "     " + _paragraph(59) + "     "

    message = _error_text(padded)

    assert "word_count_low" in message


@pytest.mark.parametrize("blank", ["", "   ", "\t\t", " \n "])
def test_empty_or_whitespace_only_text_fails_as_empty(blank: str) -> None:
    message = _error_text(blank)

    assert "empty" in message


def test_a_newline_inside_the_text_fails_as_multi_paragraph() -> None:
    text = _paragraph(30) + "\n" + _paragraph(30)

    message = _error_text(text)

    assert "multi_paragraph" in message


def test_a_carriage_return_inside_the_text_fails_as_multi_paragraph() -> None:
    text = _paragraph(30) + "\r" + _paragraph(30)

    message = _error_text(text)

    assert "multi_paragraph" in message


@pytest.mark.parametrize("char", MARKDOWN_CHARS)
def test_each_markdown_character_fails_as_markdown(char: str) -> None:
    text = _paragraph(30) + f" marked{char}up " + _paragraph(30)

    message = _error_text(text)

    assert "markdown" in message


def test_the_markdown_deny_list_is_the_documented_set() -> None:
    assert set(MARKDOWN_CHARS) == set("*_#`~[]<>|\\")


@pytest.mark.parametrize(
    "example",
    [
        "**bold** words",
        "_italic_ words",
        "# a heading",
        "* a bullet item",
        "[a link](https://example.com)",
    ],
)
def test_common_markdown_constructs_fail_as_markdown(example: str) -> None:
    text = _paragraph(60) + " " + example

    message = _error_text(text)

    assert "markdown" in message


def test_parentheses_and_apostrophes_are_accepted() -> None:
    text = "The narrator (quietly) notes Dave's habit. " + _paragraph(54)

    script = Script(script=text)

    assert "(quietly)" in script.script
    assert "Dave's" in script.script


def test_a_text_with_extra_fields_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Script.model_validate({"script": _paragraph(60), "title": "extra"})


def test_a_non_string_script_is_rejected_under_strict_typing() -> None:
    with pytest.raises(ValidationError):
        Script.model_validate({"script": 60})


def test_a_missing_script_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Script.model_validate({})


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (SENTINEL, "word_count_low"),
        (SENTINEL + "\n" + _paragraph(60), "multi_paragraph"),
        (SENTINEL + " " + _paragraph(91), "word_count_high"),
        (SENTINEL + " " + _paragraph(60) + " *bold*", "markdown"),
    ],
)
def test_the_error_names_the_reason_and_never_shows_the_text(
    text: str, reason: str
) -> None:
    message = _error_text(text)

    assert reason in message
    assert SENTINEL not in message
    assert "word0" not in message


def test_a_bullet_line_at_the_start_fails_as_markdown() -> None:
    # A hyphen bullet is markdown even though "-" is not on the deny-list.
    text = "- " + _paragraph(60)

    message = _error_text(text)

    assert "markdown" in message


def test_a_hyphen_without_a_following_space_is_not_a_bullet() -> None:
    # "-" glued to a word is a hyphenated word, not a bullet, and stays accepted.
    text = "-" + _paragraph(60)

    assert Script(script=text).script == text


def test_a_hyphen_inside_a_sentence_is_accepted() -> None:
    # Only a bullet at the line start is rejected; a mid-sentence hyphen is prose.
    text = _paragraph(60) + " one - eyed"

    assert len(Script(script=text).script.split()) == 63
