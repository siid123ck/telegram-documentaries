"""Typed boundary contracts shared by the gateway, Bouncer, and state store.

Untrusted input (Telegram updates, model responses) is converted to these models
at the edge. Every model forbids extra fields and is strict about types, so a
wrongly shaped payload is rejected at construction rather than drifting through
the pipeline as a raw dict or an ambiguous string.
"""

from __future__ import annotations

from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Defensive ceiling on an uploaded image (TECH.md names 5 MB inputs). An
#: oversized photo is nudged back to the user, never handed to the model.
MAX_PHOTO_BYTES = 10 * 1024 * 1024

#: Typed session-store key. Use :func:`as_chat_id` to build one from an edge
#: value so a raw ``str`` and ``int`` cannot produce two different keys.
ChatId = NewType("ChatId", int)


def as_chat_id(value: int | str) -> ChatId:
    """Normalise an edge ``chat_id`` into a single, positive, typed key."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("chat_id must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError("chat_id must be a positive integer")
    return ChatId(parsed)


class PhotoPayload(BaseModel):
    """A validated photo ready for the vision gate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: ChatId
    file_id: str = Field(min_length=1)
    photo_bytes: bytes = Field(min_length=1, max_length=MAX_PHOTO_BYTES)
    caption: str | None = None

    @field_validator("chat_id", mode="before")
    @classmethod
    def _normalise_chat_id(cls, value: object) -> ChatId:
        return as_chat_id(value)  # type: ignore[arg-type]

    @field_validator("file_id")
    @classmethod
    def _file_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("file_id must not be blank")
        return value


class VisionVerdict(BaseModel):
    """Structured Bouncer output; ``reason`` is logged but never shown."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contains_human: bool
    reason: str = Field(min_length=1, max_length=300)
    detected_subject: str = Field(min_length=1, max_length=80)

    @field_validator("reason", "detected_subject")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value
