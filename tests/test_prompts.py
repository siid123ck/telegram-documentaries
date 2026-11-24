"""Tests for the Converter's instruction text.

The instruction is the only place the portrait's likeness rule is written down, so
these tests pin the rule's wording and the one thing the prompt must not do: give
caption instructions. The caption is built by the bot from fixed copy.
"""

from __future__ import annotations

from telegram_documentaries.prompts import CONVERTER_INSTRUCTION


def test_the_converter_instruction_is_non_empty() -> None:
    assert CONVERTER_INSTRUCTION.strip()


def test_it_asks_for_one_hybrid_animal_portrait() -> None:
    lowered = CONVERTER_INSTRUCTION.lower()

    assert "exactly one" in lowered
    assert "hybrid" in lowered
    assert "animal" in lowered
    assert "portrait" in lowered


def test_it_keeps_the_recognisable_features_and_allows_stylisation() -> None:
    lowered = CONVERTER_INSTRUCTION.lower()

    assert "recognisable" in lowered
    assert "stylis" in lowered


def test_it_contains_no_caption_instructions() -> None:
    assert "caption" not in CONVERTER_INSTRUCTION.lower()


def test_it_tells_the_model_the_dossier_is_data_not_instructions() -> None:
    assert "never as instructions" in CONVERTER_INSTRUCTION
