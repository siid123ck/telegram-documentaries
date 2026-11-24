"""Tests for the single-sourced Phase 3 copy and the dossier layout.

Copy is user-facing, so each constant is checked for presence and for the
absence of internals (codes, stack traces, prompt text). The dossier renderer
must draw only from the validated :class:`Dossier`.
"""

from __future__ import annotations

import pytest

import telegram_documentaries.messages as messages
from telegram_documentaries.gateway import REPLY_TEXT
from telegram_documentaries.interview_models import MAX_ANSWER_CHARS, Dossier
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    ANSWER_TOO_LONG_TEXT,
    EMPTY_ANSWER_TEXT,
    HEY_MATE,
    NO_QUESTION_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    PHOTO_MID_INTERVIEW_TEXT,
    RESEND_ANSWER_TEXT,
    RESEND_TEXT,
    render_dossier,
)

_FORBIDDEN_IN_USER_COPY = (
    "traceback",
    "http",
    "429",
    "500",
    "{",
    "}",
    "exception",
    "prompt",
    "json",
    "token",
    "api key",
)

_ALL_USER_COPY = (
    OPENING_PROMPT,
    HEY_MATE,
    PHOTO_MID_INTERVIEW_TEXT,
    ONE_AT_A_TIME_TEXT,
    RESEND_ANSWER_TEXT,
    RESEND_TEXT,
    EMPTY_ANSWER_TEXT,
    ANSWER_TOO_LONG_TEXT,
    ALREADY_DONE_TEXT,
    NO_QUESTION_TEXT,
)


def _dossier() -> Dossier:
    return Dossier.model_validate(
        {
            "quirks": [
                {"topic": "sleep", "observation": "Naps in short, suspicious bursts."},
                {"topic": "territory", "observation": "Guards the biscuit tin."},
                {"topic": "forage", "observation": "Raids the fridge after midnight."},
            ],
            "suggested_animal": {
                "name": "Meerkat",
                "reason": "Keeps a lookout while everyone else eats.",
            },
        }
    )


@pytest.mark.parametrize("text", _ALL_USER_COPY, ids=lambda t: t[:24])
def test_every_copy_constant_is_non_empty(text: str) -> None:
    assert text.strip()


@pytest.mark.parametrize("text", _ALL_USER_COPY, ids=lambda t: t[:24])
def test_user_copy_carries_no_internals(text: str) -> None:
    lowered = text.lower()

    for forbidden in _FORBIDDEN_IN_USER_COPY:
        assert forbidden not in lowered


def test_hey_mate_is_the_phase_one_constant_not_a_copy() -> None:
    assert HEY_MATE is REPLY_TEXT
    assert HEY_MATE == "hey mate!"


def test_messages_module_does_not_repeat_the_fixed_reply_literal() -> None:
    source = messages.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "hey mate!" not in text


def test_the_one_at_a_time_nudge_is_reused_from_the_bouncer() -> None:
    assert ONE_AT_A_TIME_TEXT == messages.ONE_AT_A_TIME_TEXT
    assert "one at a time" in ONE_AT_A_TIME_TEXT.lower()


def test_the_resend_photo_text_is_reused_from_the_bouncer() -> None:
    assert RESEND_TEXT == "Vision's acting up — mind resending the photo?"


def test_the_opening_prompt_asks_for_a_portrait_photo() -> None:
    assert OPENING_PROMPT == "Send me a portrait photo to begin your documentary."


def test_the_resend_answer_text_matches_the_spec_wording() -> None:
    assert RESEND_ANSWER_TEXT == "Signal's fuzzy — mind resending your last answer?"


def test_the_empty_answer_nudge_matches_the_spec_wording() -> None:
    assert EMPTY_ANSWER_TEXT == "I didn't catch anything there — tell me a little more."


def test_the_too_long_nudge_states_the_real_answer_limit() -> None:
    assert str(MAX_ANSWER_CHARS) in ANSWER_TOO_LONG_TEXT
    assert "keep it under" in ANSWER_TOO_LONG_TEXT


def test_the_already_done_nudge_points_to_restart() -> None:
    assert "/restart" in ALREADY_DONE_TEXT


def test_the_mid_interview_photo_nudge_matches_the_spec_wording() -> None:
    assert PHOTO_MID_INTERVIEW_TEXT == (
        "I'm still mid-expedition with you — please answer the question above."
    )


def test_the_dossier_renders_every_quirk_and_the_animal_with_its_reason() -> None:
    rendered = render_dossier(_dossier())

    assert "sleep" in rendered.lower()
    assert "Naps in short, suspicious bursts." in rendered
    assert "territory" in rendered.lower()
    assert "Guards the biscuit tin." in rendered
    assert "forage" in rendered.lower()
    assert "Raids the fridge after midnight." in rendered
    assert "Meerkat" in rendered
    assert "Keeps a lookout while everyone else eats." in rendered


def test_the_dossier_renders_one_line_per_quirk_plus_animal_lines() -> None:
    rendered = render_dossier(_dossier())
    lines = [line for line in rendered.splitlines() if line.strip()]

    quirk_lines = [line for line in lines if line.startswith("- ")]
    assert len(quirk_lines) == 3
    assert any(line.startswith("Suggested animal:") for line in lines)
    assert any(line.startswith("Why:") for line in lines)


def test_the_dossier_renders_nothing_beyond_its_validated_fields() -> None:
    rendered = render_dossier(_dossier())
    lines = [line for line in rendered.splitlines() if line.strip()]

    # header + 3 quirk lines + animal line + reason line
    assert len(lines) == 6


def test_the_dossier_layout_is_a_fixed_template_for_any_valid_dossier() -> None:
    first = render_dossier(_dossier())
    second = render_dossier(_dossier())

    assert first == second
    assert first.startswith(messages.DOSSIER_HEADER)


def test_the_longest_valid_dossier_fits_in_one_telegram_message() -> None:
    longest = Dossier.model_validate(
        {
            "quirks": [{"topic": "t" * 40, "observation": "o" * 300} for _ in range(7)],
            "suggested_animal": {"name": "n" * 60, "reason": "r" * 300},
        }
    )

    assert len(render_dossier(longest)) < 4096
