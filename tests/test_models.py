"""Tests for the typed boundary contracts.

Every malformed payload must be rejected at construction, and the typed chat key
must collapse a raw ``str`` and ``int`` onto the same value so session lookups
cannot miss because of a string-vs-int mismatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import telegram_documentaries.config as config_module
from telegram_documentaries.config import MODELS
from telegram_documentaries.models import (
    MAX_PHOTO_BYTES,
    ChatId,
    PhotoPayload,
    VisionVerdict,
    as_chat_id,
)

_VALID_VERDICT = {
    "contains_human": True,
    "reason": "A clear, discernible person is present.",
    "detected_subject": "person",
}


def _verdict(**overrides: object) -> VisionVerdict:
    return VisionVerdict(**{**_VALID_VERDICT, **overrides})  # type: ignore[arg-type]


# --- VisionVerdict -----------------------------------------------------------


def test_vision_verdict_accepts_a_valid_response() -> None:
    verdict = _verdict()

    assert verdict.contains_human is True
    assert verdict.detected_subject == "person"


@pytest.mark.parametrize("field", ["reason", "detected_subject"])
def test_vision_verdict_rejects_empty_text_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: ""})


@pytest.mark.parametrize("field", ["reason", "detected_subject"])
def test_vision_verdict_rejects_whitespace_only_text_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: "   "})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contains_human", "yes"),
        ("contains_human", 1),
        ("reason", 42),
        ("detected_subject", ["cat"]),
    ],
)
def test_vision_verdict_rejects_wrong_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: value})


def test_vision_verdict_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _verdict(unexpected="leak")


@pytest.mark.parametrize(
    ("field", "length"),
    [("reason", 301), ("detected_subject", 81)],
)
def test_vision_verdict_rejects_overlength_fields(field: str, length: int) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: "x" * length})


def test_vision_verdict_accepts_fields_at_the_length_limit() -> None:
    assert _verdict(reason="x" * 300, detected_subject="y" * 80).contains_human


# --- PhotoPayload ------------------------------------------------------------


def _payload(**overrides: object) -> PhotoPayload:
    data: dict[str, object] = {
        "chat_id": 42,
        "file_id": "telegram-file-id",
        "photo_bytes": b"\xff\xd8\xff\xd9",
    }
    data.update(overrides)
    return PhotoPayload(**data)  # type: ignore[arg-type]


def test_photo_payload_accepts_a_valid_image() -> None:
    payload = _payload(caption="a person")

    assert int(payload.chat_id) == 42
    assert payload.caption == "a person"
    assert payload.photo_bytes == b"\xff\xd8\xff\xd9"


def test_photo_payload_accepts_caption_none() -> None:
    assert _payload().caption is None


def test_photo_payload_rejects_empty_bytes() -> None:
    with pytest.raises(ValidationError):
        _payload(photo_bytes=b"")


def test_photo_payload_rejects_oversize_bytes() -> None:
    with pytest.raises(ValidationError):
        _payload(photo_bytes=b"x" * (MAX_PHOTO_BYTES + 1))


def test_photo_payload_accepts_bytes_at_the_size_limit() -> None:
    at_limit = _payload(photo_bytes=b"x" * MAX_PHOTO_BYTES)
    assert len(at_limit.photo_bytes) == MAX_PHOTO_BYTES


def test_photo_payload_requires_a_non_empty_file_id() -> None:
    with pytest.raises(ValidationError):
        _payload(file_id="")


def test_photo_payload_requires_chat_id() -> None:
    with pytest.raises(ValidationError):
        PhotoPayload(file_id="f", photo_bytes=b"x")  # type: ignore[call-arg]


def test_photo_payload_rejects_non_positive_chat_id() -> None:
    with pytest.raises(ValidationError):
        _payload(chat_id=0)


def test_photo_payload_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _payload(surprise=True)


# --- ChatId ------------------------------------------------------------------


def test_raw_string_and_integer_convert_to_the_same_typed_key() -> None:
    assert as_chat_id("42") == as_chat_id(42)


def test_as_chat_id_returns_a_chat_id_value() -> None:
    chat_id: ChatId = as_chat_id("42")

    assert chat_id == 42
    assert isinstance(chat_id, int)


def test_chat_id_is_hashable_as_a_store_key() -> None:
    assert {as_chat_id("42"): "yes"}[as_chat_id(42)] == "yes"


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-3", "", 0, -1])
def test_as_chat_id_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(ValueError):
        as_chat_id(bad)  # type: ignore[arg-type]


def test_photo_payload_normalises_a_string_chat_id() -> None:
    assert int(_payload(chat_id="42").chat_id) == 42


# --- Model id single-sourcing ------------------------------------------------


def test_bouncer_model_id_is_single_sourced_in_config() -> None:
    assert MODELS["bouncer"] == "gemini-3.1-flash-lite"


def test_interviewer_reads_its_model_from_the_same_mapping_as_the_bouncer() -> None:
    assert MODELS["interviewer"] == "gemini-3.1-flash-lite"


def test_the_flash_lite_model_literal_appears_exactly_once_in_config() -> None:
    config_source = Path(config_module.__file__).read_text(encoding="utf-8")

    assert config_source.count("gemini-3.1-flash-lite") == 1
