"""Typed contract for the Converter's output: one validated hybrid portrait.

The provider's reply is untrusted. A :class:`Portrait` exists only when its bytes
are a real PNG or JPEG of a sane size. The MIME type is read from the magic bytes,
never accepted from the provider, so a declared type cannot change what is sent.
Nothing here imports the ADK or touches the network.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from telegram_documentaries.models import MAX_PHOTO_BYTES

#: File signature of a PNG (the eight-byte header, in full).
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: File signature of a JPEG (start-of-image marker plus the first marker byte).
JPEG_MAGIC = b"\xff\xd8\xff"

PortraitMimeType = Literal["image/png", "image/jpeg"]


def _mime_of(image_bytes: bytes) -> PortraitMimeType:
    """Return the MIME type the bytes actually carry, or refuse them."""
    if image_bytes.startswith(PNG_MAGIC):
        return "image/png"
    if image_bytes.startswith(JPEG_MAGIC):
        return "image/jpeg"
    raise ValueError("image bytes are neither PNG nor JPEG")


class Portrait(BaseModel):
    """A generated portrait that is safe to hand to Telegram.

    ``mime_type`` is derived from the bytes. It is not a field, so supplying one
    is an extra-field error, and it can never disagree with the payload.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    image_bytes: bytes = Field(min_length=1, max_length=MAX_PHOTO_BYTES)

    @model_validator(mode="after")
    def _bytes_are_a_known_image(self) -> Self:
        _mime_of(self.image_bytes)
        return self

    @property
    def mime_type(self) -> PortraitMimeType:
        """The MIME type read from the image bytes."""
        return _mime_of(self.image_bytes)
