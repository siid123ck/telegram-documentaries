"""PCM-to-MP3 encoding for the Narrator's voice note (N3).

The TTS reply is raw PCM, which Telegram will not play as a voice note, so it is
encoded locally to MP3 with ``lameenc`` (a wheel with no system binary). This is the
only module that imports the encoder library. The encoder is injected as a seam, so
tests run a fake and never the real encoder, except for the one fixture test.

Encoder output is untrusted. It is checked by :class:`VoiceNote` before it can reach
Telegram. Encoder failures become ``bad_mp3`` errors that carry no raw message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

# lameenc is a compiled module that ships no type stubs; this is its only import.
import lameenc  # type: ignore[import-not-found]

from telegram_documentaries.narrator_models import TtsReply, VoiceNote

#: Encoder seam: a function from a checked PCM reply to MP3 bytes.
Encoder = Callable[[TtsReply], bytes]

#: Target bitrate for the voice note, in kbit/s. Speech is clear at this rate.
_BIT_RATE_KBPS: Final = 64

#: LAME quality setting: 2 is high quality, the encoder's standard for speech.
_QUALITY: Final = 2


def _encode_with_lameenc(reply: TtsReply) -> bytes:
    """Encode mono 16-bit PCM at the reply's sample rate to MP3 with LAME."""
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(_BIT_RATE_KBPS)
    encoder.set_in_sample_rate(reply.sample_rate_hz)
    encoder.set_channels(1)
    encoder.set_quality(_QUALITY)
    # lameenc returns a bytearray; the contract is immutable bytes.
    mp3: bytes = bytes(encoder.encode(reply.pcm) + encoder.flush())
    return mp3


def encode_mp3(
    reply: TtsReply,
    *,
    encoder: Encoder = _encode_with_lameenc,
) -> VoiceNote:
    """Encode a checked PCM reply to a checked MP3 voice note.

    A failure inside the encoder is reported as ``bad_mp3`` with the input byte
    count. The encoder's own message is dropped, so it never reaches a log or a
    reply. An empty, oversize, or malformed output is rejected by :class:`VoiceNote`.
    """
    try:
        mp3 = encoder(reply)
    except Exception:
        # The encoder's message is not copied. Only the reason and count are kept.
        raise ValueError(f"bad_mp3 (bytes={len(reply.pcm)})") from None
    return VoiceNote.model_validate({"mp3": mp3})
