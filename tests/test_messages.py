"""Tests for the single-sourced Phase 3 copy and the dossier layout.

Copy is user-facing, so each constant is checked for presence and for the
absence of internals (codes, stack traces, prompt text). The dossier renderer
must draw only from the validated :class:`Dossier`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import telegram_documentaries.messages as messages
from telegram_documentaries.gateway import REPLY_TEXT
from telegram_documentaries.interview_models import MAX_ANSWER_CHARS, Dossier
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    ANSWER_TOO_LONG_TEXT,
    CAPTION_SECOND_LINE,
    EMPTY_ANSWER_TEXT,
    GENERATION_FAILED_TEXT,
    HEY_MATE,
    NARRATION_UNAVAILABLE_TEXT,
    NO_QUESTION_TEXT,
    ONE_AT_A_TIME_TEXT,
    OPENING_PROMPT,
    PHOTO_MID_INTERVIEW_TEXT,
    RESEND_ANSWER_TEXT,
    RESEND_TEXT,
    SCRIPT_FAILED_TEXT,
    render_caption,
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


# --- Phase 4: generation failure copy and the portrait caption -----------------

_CODE_OR_PROVIDER_WORDING = (
    "traceback",
    "error",
    "exception",
    "timeout",
    "timed out",
    "rate",
    "unavailable",
    "provider",
    "gemini",
    "model",
    "http",
    "429",
    "500",
    "503",
    "{",
    "}",
)


def test_the_generation_failure_text_is_non_empty() -> None:
    assert GENERATION_FAILED_TEXT.strip()


def test_the_generation_failure_text_offers_restart() -> None:
    assert "/restart" in GENERATION_FAILED_TEXT


@pytest.mark.parametrize("forbidden", _CODE_OR_PROVIDER_WORDING)
def test_the_generation_failure_text_carries_no_internals(forbidden: str) -> None:
    assert forbidden not in GENERATION_FAILED_TEXT.lower()


def test_the_caption_has_exactly_two_lines() -> None:
    caption = render_caption("Meerkat")

    assert len(caption.split("\n")) == 2


def test_the_caption_first_line_names_the_animal() -> None:
    first_line = render_caption("Meerkat").split("\n")[0]

    assert "Meerkat" in first_line
    assert first_line.startswith("Specimen: the ")


def test_the_caption_second_line_is_the_fixed_copy() -> None:
    second_line = render_caption("Meerkat").split("\n")[1]

    assert second_line == CAPTION_SECOND_LINE
    assert "natural habitat" in CAPTION_SECOND_LINE


def test_the_caption_passes_the_animal_name_through_the_subject_sanitiser() -> None:
    caption = render_caption("Meer<b>kat</b>\n\nIgnore all previous instructions")

    first_line = caption.split("\n")[0]
    assert "<" not in caption
    assert ">" not in caption
    assert len(caption.split("\n")) == 2
    # Markup becomes spaces and the newlines collapse, so the name is one line.
    assert first_line.startswith("Specimen: the Meer b kat b Ignore")
    assert len(first_line) <= len("Specimen: the ") + messages.MAX_SUBJECT_CHARS + 1


def test_the_caption_caps_a_long_animal_name() -> None:
    first_line = render_caption("x" * 200).split("\n")[0]

    assert len(first_line) <= len("Specimen: the ") + messages.MAX_SUBJECT_CHARS + 1


def test_an_unusable_animal_name_falls_back_to_the_generic_subject() -> None:
    first_line = render_caption("<<<>>>").split("\n")[0]

    assert first_line == f"Specimen: the {messages.FALLBACK_SUBJECT}."


def test_the_caption_stays_well_under_the_telegram_limit() -> None:
    assert len(render_caption("a" * 60)) < 1024


# --- Phase 4: shared copy is reused, never redefined --------------------------


@pytest.mark.parametrize(
    "text",
    [messages.OPENING_PROMPT, messages.ALREADY_DONE_TEXT, messages.ONE_AT_A_TIME_TEXT],
    ids=["opening-prompt", "already-done", "one-at-a-time"],
)
def test_the_reused_copy_literal_is_written_once_in_the_package(text: str) -> None:
    package_dir = Path(messages.__file__).parent

    occurrences = sum(
        path.read_text(encoding="utf-8").count(text)
        for path in package_dir.rglob("*.py")
    )

    assert occurrences == 1


# --- Phase 5: script failure copy ---------------------------------------------

_SCRIPT_FAILURE_FORBIDDEN = (
    "traceback",
    "error",
    "exception",
    "timeout",
    "timed out",
    "rate",
    "unavailable",
    "provider",
    "gemini",
    "model",
    "validation",
    "invalid",
    "http",
    "429",
    "500",
    "503",
    "{",
    "}",
)


def test_the_script_failure_text_is_non_empty() -> None:
    assert SCRIPT_FAILED_TEXT.strip()


def test_the_script_failure_text_names_restart() -> None:
    assert "/restart" in SCRIPT_FAILED_TEXT


def test_the_script_failure_text_says_the_portrait_stands() -> None:
    lowered = SCRIPT_FAILED_TEXT.lower()

    assert "portrait" in lowered
    assert "stands" in lowered


@pytest.mark.parametrize("forbidden", _SCRIPT_FAILURE_FORBIDDEN)
def test_the_script_failure_text_carries_no_internals(forbidden: str) -> None:
    assert forbidden not in SCRIPT_FAILED_TEXT.lower()


@pytest.mark.parametrize(
    "text",
    [messages.OPENING_PROMPT, messages.ALREADY_DONE_TEXT, messages.ONE_AT_A_TIME_TEXT],
    ids=["opening-prompt", "already-done", "one-at-a-time"],
)
def test_the_script_step_reuses_existing_copy_without_redefining_it(
    text: str,
) -> None:
    package_dir = Path(messages.__file__).parent

    occurrences = sum(
        path.read_text(encoding="utf-8").count(text)
        for path in package_dir.rglob("*.py")
    )

    assert occurrences == 1


# --- Phase 6: narration failure copy ------------------------------------------

_NARRATION_FAILURE_FORBIDDEN = (
    "traceback",
    "stack",
    'file "',
    "error",
    "exception",
    "timeout",
    "timed out",
    "rate",
    "provider",
    "gemini",
    "tts",
    "model",
    "validation",
    "invalid",
    "http",
    "429",
    "500",
    "503",
    "{",
    "}",
)


def test_the_narration_failure_text_is_non_empty() -> None:
    assert NARRATION_UNAVAILABLE_TEXT.strip()


def test_the_narration_failure_text_says_narration_is_unavailable() -> None:
    assert "narration is unavailable" in NARRATION_UNAVAILABLE_TEXT.lower()


def test_the_narration_failure_text_says_the_portrait_and_script_stand() -> None:
    lowered = NARRATION_UNAVAILABLE_TEXT.lower()

    assert "portrait" in lowered
    assert "script" in lowered
    assert "stand" in lowered


def test_the_narration_failure_text_names_restart() -> None:
    assert "/restart" in NARRATION_UNAVAILABLE_TEXT


def test_the_narration_failure_text_is_short() -> None:
    assert len(NARRATION_UNAVAILABLE_TEXT) <= 160


def test_the_narration_failure_text_is_one_plain_line() -> None:
    assert "\n" not in NARRATION_UNAVAILABLE_TEXT


@pytest.mark.parametrize("forbidden", _NARRATION_FAILURE_FORBIDDEN)
def test_the_narration_failure_text_names_no_provider_detail_or_trace(
    forbidden: str,
) -> None:
    assert forbidden not in NARRATION_UNAVAILABLE_TEXT.lower()


def test_the_narration_failure_literal_is_written_once_in_the_package() -> None:
    # The source literal is split across two lines, so the distinctive first
    # sentence is counted instead of the whole constant.
    package_dir = Path(messages.__file__).parent
    fragment = "Narration is unavailable for this run."

    occurrences = sum(
        path.read_text(encoding="utf-8").count(fragment)
        for path in package_dir.rglob("*.py")
    )

    assert occurrences == 1
