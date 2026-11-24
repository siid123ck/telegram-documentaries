"""Typed contracts for the Narrator's audio: the raw TTS reply and the encoded MP3.

The provider's reply is untrusted. A :class:`TtsReply` exists only when its audio
bytes are present, non-empty, within the size cap, and in the expected format. A
:class:`VoiceNote` exists only when its MP3 bytes are present, within the cap, and
start with a valid MP3 header. Failures carry a reason code and the byte count only,
never the audio. Nothing here imports the SDK, the encoder, or the network.
"""

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

#: The MIME type the TTS reply must carry (N4). Set from the Phase 6 spec; not yet
#: confirmed against a live reply (see the Phase 6 group 0 spike).
EXPECTED_PCM_MIME: Final = "audio/L16"

#: The sample rate the TTS reply must carry, in hertz (N4). Set from the Phase 6
#: spec; not yet confirmed against a live reply.
EXPECTED_SAMPLE_RATE_HZ: Final = 24000

#: Largest raw reply accepted, in bytes (N5). Matches the Converter's image cap.
MAX_PCM_BYTES: Final = 10 * 1024 * 1024

#: Largest encoded MP3 accepted, in bytes (N5).
MAX_MP3_BYTES: Final = 10 * 1024 * 1024

#: Reason codes, one per rule. They are the only detail an error carries.
NarrationRejection = Literal[
    "empty_audio",
    "oversize_audio",
    "unexpected_format",
    "bad_mp3",
]

#: Leading bytes that mark an MP3 as valid (N6): an ID3 tag, or an MPEG Layer III
#: frame sync (0xFF then 0xFB for MPEG-1, 0xF3 for MPEG-2, 0xF2 for MPEG-2.5).
_MP3_ID3_TAG: Final = b"ID3"
_MP3_FRAME_SYNC_SECOND_BYTES: Final = frozenset({0xFB, 0xF3, 0xF2})


def _reject(reason: NarrationRejection, size: int) -> ValueError:
    """Build the error for a rule failure. It names the rule and the count only."""
    return ValueError(f"{reason} (bytes={size})")


class TtsReply(BaseModel):
    """The raw audio the TTS call returns, checked at construction.

    The checks run in a fixed order: presence and size first, then format. The
    first failure is reported with its reason code and the byte count. Supplying
    any field beyond the three declared ones is an error.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    pcm: bytes
    mime_type: str
    sample_rate_hz: int

    @model_validator(mode="after")
    def _audio_is_present_and_expected(self) -> Self:
        size = len(self.pcm)
        if size == 0:
            raise _reject("empty_audio", size)
        if size > MAX_PCM_BYTES:
            raise _reject("oversize_audio", size)
        if (
            self.mime_type != EXPECTED_PCM_MIME
            or self.sample_rate_hz != EXPECTED_SAMPLE_RATE_HZ
        ):
            raise _reject("unexpected_format", size)
        return self


class VoiceNote(BaseModel):
    """The encoded MP3 for the voice note, checked at construction (N5, N6).

    The checks run in a fixed order: presence and size first, then the MP3 header.
    The first failure is reported with its reason code and the byte count. Supplying
    any field beyond ``mp3`` is an error.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    mp3: bytes

    @model_validator(mode="after")
    def _is_present_and_a_valid_mp3(self) -> Self:
        size = len(self.mp3)
        if size == 0:
            raise _reject("empty_audio", size)
        if size > MAX_MP3_BYTES:
            raise _reject("oversize_audio", size)
        if not _starts_like_mp3(self.mp3):
            raise _reject("bad_mp3", size)
        return self


def _starts_like_mp3(data: bytes) -> bool:
    """Return True when ``data`` begins with an ID3 tag or an MPEG frame sync."""
    if data[:3] == _MP3_ID3_TAG:
        return True
    return (
        len(data) >= 2 and data[0] == 0xFF and data[1] in _MP3_FRAME_SYNC_SECOND_BYTES
    )
