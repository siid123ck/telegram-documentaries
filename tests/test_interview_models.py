"""Tests for the Phase 3 interview contracts.

Every malformed model reply or dossier must be rejected at construction. The
bounds are the ones named in ``interview_models``; these tests pin them at the
edges (exact limit accepted, one past the limit rejected).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from telegram_documentaries.interview_models import (
    MAX_ANIMAL_NAME_CHARS,
    MAX_ANIMAL_REASON_CHARS,
    MAX_ANSWER_CHARS,
    MAX_QUESTION_CHARS,
    MAX_QUIRK_OBSERVATION_CHARS,
    MAX_QUIRK_TOPIC_CHARS,
    MAX_QUIRKS,
    MIN_QUIRKS,
    Answer,
    Dossier,
    InterviewTurn,
    Quirk,
    SuggestedAnimal,
)

_QUIRK: dict[str, Any] = {"topic": "sleep", "observation": "Naps in short bursts."}
_ANIMAL: dict[str, Any] = {"name": "Meerkat", "reason": "Keeps a lookout all day."}


def _quirks(count: int) -> list[dict[str, Any]]:
    return [
        {"topic": f"topic-{i}", "observation": f"Observation {i}."}
        for i in range(count)
    ]


def _dossier(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"quirks": _quirks(3), "suggested_animal": _ANIMAL}
    return {**base, **overrides}


# --- Quirk -------------------------------------------------------------------


def test_quirk_accepts_a_valid_topic_and_observation() -> None:
    quirk = Quirk(**_QUIRK)

    assert quirk.topic == "sleep"
    assert quirk.observation == "Naps in short bursts."


@pytest.mark.parametrize("topic", ["", "   "])
def test_quirk_rejects_an_empty_or_blank_topic(topic: str) -> None:
    with pytest.raises(ValidationError):
        Quirk(topic=topic, observation="ok")


def test_quirk_topic_accepts_the_length_limit_and_rejects_one_past_it() -> None:
    Quirk(topic="t" * MAX_QUIRK_TOPIC_CHARS, observation="ok")

    with pytest.raises(ValidationError):
        Quirk(topic="t" * (MAX_QUIRK_TOPIC_CHARS + 1), observation="ok")


def test_quirk_rejects_an_empty_observation() -> None:
    with pytest.raises(ValidationError):
        Quirk(topic="sleep", observation="")


def test_quirk_observation_rejects_one_past_the_limit() -> None:
    Quirk(topic="sleep", observation="o" * MAX_QUIRK_OBSERVATION_CHARS)

    with pytest.raises(ValidationError):
        Quirk(topic="sleep", observation="o" * (MAX_QUIRK_OBSERVATION_CHARS + 1))


@pytest.mark.parametrize("observation", ["line one\nline two", "tab\tand\rcarriage"])
def test_quirk_observation_must_be_a_single_line(observation: str) -> None:
    with pytest.raises(ValidationError):
        Quirk(topic="sleep", observation=observation)


def test_quirk_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Quirk.model_validate({**_QUIRK, "confidence": 0.9})


# --- SuggestedAnimal ---------------------------------------------------------


def test_suggested_animal_accepts_a_name_and_a_short_reason() -> None:
    animal = SuggestedAnimal(**_ANIMAL)

    assert animal.name == "Meerkat"


def test_suggested_animal_rejects_an_empty_name() -> None:
    with pytest.raises(ValidationError):
        SuggestedAnimal(name="", reason="ok")


def test_suggested_animal_rejects_a_whitespace_only_name() -> None:
    with pytest.raises(ValidationError):
        SuggestedAnimal(name="   ", reason="ok")


def test_suggested_animal_name_accepts_the_limit_and_rejects_one_past_it() -> None:
    SuggestedAnimal(name="n" * MAX_ANIMAL_NAME_CHARS, reason="ok")

    with pytest.raises(ValidationError):
        SuggestedAnimal(name="n" * (MAX_ANIMAL_NAME_CHARS + 1), reason="ok")


def test_suggested_animal_reason_rejects_one_past_the_limit() -> None:
    SuggestedAnimal(name="Otter", reason="r" * MAX_ANIMAL_REASON_CHARS)

    with pytest.raises(ValidationError):
        SuggestedAnimal(name="Otter", reason="r" * (MAX_ANIMAL_REASON_CHARS + 1))


def test_suggested_animal_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SuggestedAnimal.model_validate(
            {**_ANIMAL, "species_latin": "Suricata suricatta"}
        )


# --- Dossier -----------------------------------------------------------------


@pytest.mark.parametrize("count", [MIN_QUIRKS, MAX_QUIRKS])
def test_dossier_accepts_three_and_seven_quirks(count: int) -> None:
    dossier = Dossier.model_validate(_dossier(quirks=_quirks(count)))

    assert len(dossier.quirks) == count


@pytest.mark.parametrize("count", [MIN_QUIRKS - 1, MAX_QUIRKS + 1, 0])
def test_dossier_rejects_quirk_counts_outside_three_to_seven(count: int) -> None:
    with pytest.raises(ValidationError):
        Dossier.model_validate(_dossier(quirks=_quirks(count)))


def test_dossier_rejects_extra_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        Dossier.model_validate(_dossier(summary="Too much"))


def test_dossier_rejects_a_missing_suggested_animal() -> None:
    with pytest.raises(ValidationError):
        Dossier.model_validate({"quirks": _quirks(3)})


def test_dossier_rejects_an_empty_animal_name() -> None:
    with pytest.raises(ValidationError):
        Dossier.model_validate(_dossier(suggested_animal={"name": "", "reason": "ok"}))


# --- Answer ------------------------------------------------------------------


def test_answer_accepts_a_question_and_a_reply() -> None:
    answer = Answer(question="Where do you nap?", answer="On the sofa.")

    assert answer.answer == "On the sofa."


@pytest.mark.parametrize("text", ["", "   \n\t  "])
def test_answer_rejects_empty_and_whitespace_only_text(text: str) -> None:
    with pytest.raises(ValidationError):
        Answer(question="Q?", answer=text)


def test_answer_accepts_the_length_limit_and_rejects_one_past_it() -> None:
    Answer(question="Q?", answer="a" * MAX_ANSWER_CHARS)

    with pytest.raises(ValidationError):
        Answer(question="Q?", answer="a" * (MAX_ANSWER_CHARS + 1))


def test_answer_rejects_an_empty_question() -> None:
    with pytest.raises(ValidationError):
        Answer(question="", answer="ok")


# --- InterviewTurn -----------------------------------------------------------


def test_an_ask_turn_with_a_question_is_valid() -> None:
    turn = InterviewTurn(action="ask", question="What do you do at dawn?")

    assert turn.action == "ask"
    assert turn.question == "What do you do at dawn?"
    assert turn.dossier is None


def test_a_finish_turn_with_a_dossier_is_valid() -> None:
    turn = InterviewTurn(action="finish", dossier=Dossier.model_validate(_dossier()))

    assert turn.dossier is not None
    assert turn.question is None


def test_an_ask_turn_carrying_a_dossier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(
            action="ask",
            question="Still asking?",
            dossier=Dossier.model_validate(_dossier()),
        )


def test_a_finish_turn_carrying_a_question_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(
            action="finish",
            question="Wait, one more?",
            dossier=Dossier.model_validate(_dossier()),
        )


def test_a_finish_turn_without_a_dossier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(action="finish")


def test_an_ask_turn_without_a_question_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(action="ask")


def test_a_turn_with_both_question_and_dossier_under_ask_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(
            action="ask",
            question="Q?",
            dossier=Dossier.model_validate(_dossier()),
        )


def test_an_unknown_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn.model_validate({"action": "summarise", "question": "Q?"})


def test_a_turn_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn.model_validate(
            {"action": "ask", "question": "Q?", "notes": "extra"}
        )


def test_a_question_accepts_the_limit_and_rejects_one_past_it() -> None:
    InterviewTurn(action="ask", question="q" * MAX_QUESTION_CHARS)

    with pytest.raises(ValidationError):
        InterviewTurn(action="ask", question="q" * (MAX_QUESTION_CHARS + 1))


def test_a_multi_line_question_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InterviewTurn(action="ask", question="First line\nSecond line")


def test_a_turn_parses_from_json_and_still_enforces_the_shape() -> None:
    valid = (
        '{"action": "ask", "question": "How do you spend your evenings?", '
        '"dossier": null}'
    )
    invalid = '{"action": "ask", "question": "Q?", "dossier": {"quirks": []}}'

    assert InterviewTurn.model_validate_json(valid).action == "ask"
    with pytest.raises(ValidationError):
        InterviewTurn.model_validate_json(invalid)
