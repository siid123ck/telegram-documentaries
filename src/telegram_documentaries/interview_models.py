"""Typed contracts for the Interviewer: answers, dossier, and model turns.

Every model reply from the interview provider is parsed into
:class:`InterviewTurn` at the boundary, and the dossier is only ever built from
these validated models. All bounds are named here, in one place, so no
validator carries a magic number. Nothing in this module imports the ADK.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Interview length, enforced by the orchestrator (not by the model).
MIN_ANSWERS = 5
MAX_ANSWERS = 7

#: Quirks per dossier.
MIN_QUIRKS = 3
MAX_QUIRKS = 7

#: Field bounds (working defaults; see requirements.md, Open decisions 4).
MAX_QUESTION_CHARS = 300
MAX_ANSWER_CHARS = 1000
MAX_QUIRK_TOPIC_CHARS = 40
MAX_QUIRK_OBSERVATION_CHARS = 300
MAX_ANIMAL_NAME_CHARS = 60
MAX_ANIMAL_REASON_CHARS = 300


def _single_line(value: str) -> str:
    """Reject values that would break a one-line field or a chat message layout."""
    if "\n" in value or "\r" in value or "\t" in value:
        raise ValueError("must be a single line")
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class Answer(BaseModel):
    """One committed exchange: the question asked and the user's reply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)

    @field_validator("question", "answer")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class Quirk(BaseModel):
    """One observed behaviour. ``topic`` is model-chosen free text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    topic: str = Field(min_length=1, max_length=MAX_QUIRK_TOPIC_CHARS)
    observation: str = Field(min_length=1, max_length=MAX_QUIRK_OBSERVATION_CHARS)

    @field_validator("topic", "observation")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        return _single_line(_not_blank(value))


class SuggestedAnimal(BaseModel):
    """The animal the dossier proposes, with a short playful reason."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=MAX_ANIMAL_NAME_CHARS)
    reason: str = Field(min_length=1, max_length=MAX_ANIMAL_REASON_CHARS)

    @field_validator("name", "reason")
    @classmethod
    def _valid_text(cls, value: str) -> str:
        return _single_line(_not_blank(value))


class Dossier(BaseModel):
    """The interview's output: quirks and one suggested animal, nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    quirks: list[Quirk] = Field(min_length=MIN_QUIRKS, max_length=MAX_QUIRKS)
    suggested_animal: SuggestedAnimal


class InterviewTurn(BaseModel):
    """One model turn: either the next question, or the finished dossier.

    ``action="ask"`` requires ``question`` and forbids ``dossier``;
    ``action="finish"`` requires ``dossier`` and forbids ``question``.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["ask", "finish"]
    question: str | None = Field(
        default=None, min_length=1, max_length=MAX_QUESTION_CHARS
    )
    dossier: Dossier | None = None

    @field_validator("question")
    @classmethod
    def _question_is_one_line(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _single_line(_not_blank(value))

    @model_validator(mode="after")
    def _shape_matches_action(self) -> Self:
        if self.action == "ask":
            if self.question is None or self.dossier is not None:
                raise ValueError("ask needs a question and no dossier")
        elif self.question is not None or self.dossier is None:
            raise ValueError("finish needs a dossier and no question")
        return self
