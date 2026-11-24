"""Tests for the Converter's typed output contract, :class:`Portrait`.

A portrait is accepted only when its bytes are a real PNG or JPEG of a sane
size. The MIME type comes from the bytes, never from the provider, so a declared
type that disagrees with the bytes cannot get through.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from telegram_documentaries.converter_models import Portrait
from telegram_documentaries.models import MAX_PHOTO_BYTES

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"png-body"
JPEG_BYTES = b"\xff\xd8\xff" + b"jpeg-body"


def test_a_png_is_accepted_and_typed_as_image_png() -> None:
    portrait = Portrait(image_bytes=PNG_BYTES)

    assert portrait.image_bytes == PNG_BYTES
    assert portrait.mime_type == "image/png"


def test_a_jpeg_is_accepted_and_typed_as_image_jpeg() -> None:
    portrait = Portrait(image_bytes=JPEG_BYTES)

    assert portrait.image_bytes == JPEG_BYTES
    assert portrait.mime_type == "image/jpeg"


def test_empty_bytes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Portrait(image_bytes=b"")


def test_bytes_over_the_photo_ceiling_are_rejected() -> None:
    oversize = PNG_BYTES + b"0" * (MAX_PHOTO_BYTES - len(PNG_BYTES) + 1)

    with pytest.raises(ValidationError):
        Portrait(image_bytes=oversize)


def test_bytes_exactly_at_the_photo_ceiling_are_accepted() -> None:
    at_limit = PNG_BYTES + b"0" * (MAX_PHOTO_BYTES - len(PNG_BYTES))

    portrait = Portrait(image_bytes=at_limit)

    assert len(portrait.image_bytes) == MAX_PHOTO_BYTES


@pytest.mark.parametrize(
    "payload",
    [
        b"hello",
        b"Sorry, I cannot draw that.",
        b"GIF89a-not-accepted",
        b"\x89PNG-without-the-full-signature",
    ],
    ids=["plain-text", "apology-text", "gif", "truncated-png-signature"],
)
def test_bytes_that_are_neither_png_nor_jpeg_are_rejected(payload: bytes) -> None:
    with pytest.raises(ValidationError):
        Portrait(image_bytes=payload)


def test_a_declared_mime_type_is_not_trusted_over_the_bytes() -> None:
    # A JPEG body that claims to be a PNG must not be accepted as one.
    with pytest.raises(ValidationError):
        Portrait.model_validate({"image_bytes": JPEG_BYTES, "mime_type": "image/png"})


def test_a_declared_mime_type_is_rejected_even_when_it_matches_the_bytes() -> None:
    # The type is derived, never supplied, so supplying one is always an error.
    with pytest.raises(ValidationError):
        Portrait.model_validate({"image_bytes": PNG_BYTES, "mime_type": "image/png"})


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Portrait.model_validate({"image_bytes": PNG_BYTES, "caption": "hi"})


def test_a_string_is_not_accepted_as_image_bytes() -> None:
    with pytest.raises(ValidationError):
        Portrait.model_validate({"image_bytes": "\x89PNG\r\n\x1a\nbody"})
