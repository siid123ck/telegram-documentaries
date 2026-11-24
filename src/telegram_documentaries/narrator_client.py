"""Narration-client seam and the ``google.genai`` TTS implementation (Phase 6).

Importing this module is cheap and provider-free. It exposes the
:class:`NarrationClient` protocol, the pure request and reply helpers, and the
timeout constant, without importing ``google.genai`` or the ADK. Only
:class:`GenAiNarrationTransport` imports ``google.genai``, and only when a call is
made, so tests run offline with an injected transport. This is the only Phase 6
module that imports ``google.genai``.

The client makes exactly one attempt per :meth:`GeminiNarrationClient.synthesise`
call. The attempt budget and the retry backoff belong to the Narrator tool, which
owns the paid-call guard. Provider failures are mapped with the shared
:func:`~telegram_documentaries.vision_client.map_provider_error`, so every model
degrades the same way.

The wire-schema helper does not apply here. TTS returns audio, not JSON, so there is
no response schema to flatten. The reply is checked by :class:`TtsReply` instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, NamedTuple, Protocol, get_args, runtime_checkable

from pydantic import ValidationError

from telegram_documentaries.config import MODELS
from telegram_documentaries.errors import InvalidResponse, ProviderTimeout, VisionError
from telegram_documentaries.narrator_models import NarrationRejection, TtsReply
from telegram_documentaries.prompts import NARRATOR_INSTRUCTION
from telegram_documentaries.vision_client import map_provider_error

logger = logging.getLogger(__name__)

#: Deadline per attempt. Speech synthesis is slower than text (10 s) and faster
#: than images (60 s), so it gets its own limit (requirements N2).
NARRATION_TIMEOUT_SECONDS: Final = 30.0

#: The only response modality requested: speech, with no text (requirements N8).
AUDIO_MODALITY: Final = "AUDIO"

#: Reason-code names, used to pull a safe reason out of a validation error.
_REJECTION_REASONS: Final[frozenset[str]] = frozenset(get_args(NarrationRejection))


class RawAudio(NamedTuple):
    """The audio part of a TTS reply, before it is checked into a :class:`TtsReply`.

    ``data`` is the raw bytes and ``mime_type`` is the content type as the provider
    sent it, including any parameters such as ``rate=24000``.
    """

    data: bytes
    mime_type: str


@runtime_checkable
class NarrationClient(Protocol):
    """Structural seam for anything that can speak the script text.

    ``text`` is the script. ``voice`` is the prebuilt voice name from config, passed
    in and never hard-coded here. The client returns a checked :class:`TtsReply`, or
    raises a typed error.
    """

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        """Return the checked audio reply, or raise a typed error."""
        ...


class NarrationTransport(Protocol):
    """The single provider call: one TTS request, returning the audio part.

    Production uses :class:`GenAiNarrationTransport`. Tests inject a fake, so no
    socket is opened and ``google.genai`` is never imported.
    """

    async def __call__(
        self,
        *,
        model: str,
        prompt: str,
        voice: str,
        response_modalities: tuple[str, ...],
    ) -> RawAudio | None:
        """Return the first audio part, or ``None`` if the reply has none."""
        ...


def narration_prompt(text: str) -> str:
    """Return the TTS request text: the Narrator instruction, then the script.

    Only the script text is added after the instruction. No dossier, photo, or answer
    reaches the provider (requirements N8).
    """
    return f"{NARRATOR_INSTRUCTION}\n{text}"


def parse_mime_and_rate(content_type: str) -> tuple[str, int]:
    """Split a content type such as ``audio/L16;codec=pcm;rate=24000``.

    Returns the base MIME type and the ``rate`` parameter in hertz. A missing or
    non-numeric rate returns ``0``, which :class:`TtsReply` then rejects as an
    unexpected format.
    """
    base, *params = (part.strip() for part in content_type.split(";"))
    rate = 0
    for param in params:
        key, _, value = param.partition("=")
        if key.strip() == "rate" and value.strip().isdigit():
            rate = int(value.strip())
    return base, rate


def first_audio_part(response: Any) -> RawAudio | None:
    """Return the first inline audio part in a genai-shaped response, or ``None``.

    An absent or empty audio part returns ``None``. The client turns that into an
    :class:`InvalidResponse`, so a text-only reply never reaches Telegram.
    """
    for candidate in response.candidates or []:
        content = candidate.content
        for part in (content.parts or []) if content is not None else []:
            inline = part.inline_data
            if inline is not None and inline.data:
                return RawAudio(
                    data=bytes(inline.data), mime_type=inline.mime_type or ""
                )
    return None


def _to_tts_reply(audio: RawAudio) -> TtsReply:
    """Check a provider audio part into a :class:`TtsReply`, or raise InvalidResponse.

    The detail is the reason code only. The validation message is not kept, because
    it could carry the audio's size or contents.
    """
    base, rate = parse_mime_and_rate(audio.mime_type)
    try:
        return TtsReply(pcm=audio.data, mime_type=base, sample_rate_hz=rate)
    except ValidationError as exc:
        raise InvalidResponse(detail=_rejection_reason(exc)) from exc


def _rejection_reason(exc: ValidationError) -> str:
    """Return the reason code named in a :class:`TtsReply` validation error."""
    for error in exc.errors():
        for reason in _REJECTION_REASONS:
            if reason in str(error.get("msg", "")):
                return reason
    return "invalid_audio"


class GenAiNarrationTransport:
    """:class:`NarrationTransport` that calls ``google.genai`` directly.

    The genai client is built on the first call, from the explicit key the caller
    supplies, so constructing the transport never imports genai or touches the
    network.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google.genai import Client

            self._client = Client(api_key=self._api_key)
        return self._client

    async def __call__(
        self,
        *,
        model: str,
        prompt: str,
        voice: str,
        response_modalities: tuple[str, ...],
    ) -> RawAudio | None:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_modalities=list(response_modalities),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        )
        response = await self._ensure_client().aio.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return first_audio_part(response)


class GeminiNarrationClient:
    """:class:`NarrationClient` backed by the Gemini TTS model (one attempt per call).

    Pass a ``transport`` in tests to avoid the network. Without one, the real
    :class:`GenAiNarrationTransport` is used.
    """

    def __init__(
        self, api_key: str, transport: NarrationTransport | None = None
    ) -> None:
        self._transport: NarrationTransport = (
            transport if transport is not None else GenAiNarrationTransport(api_key)
        )

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        """Make one attempt under the 30-second deadline and map every failure."""
        try:
            audio = await asyncio.wait_for(
                self._transport(
                    model=MODELS["narrator"],
                    prompt=narration_prompt(text),
                    voice=voice,
                    response_modalities=(AUDIO_MODALITY,),
                ),
                timeout=NARRATION_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except VisionError:
            raise
        except Exception as exc:  # provider SDK failure; mapped, never leaked
            # Only the exception class and HTTP status are logged. No message,
            # prompt, script, or audio reaches the log.
            status = getattr(exc, "code", None)
            logger.error(
                "narration_provider_error error_type=%s provider_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            raise map_provider_error(exc) from exc
        if audio is None:
            raise InvalidResponse(detail="no audio part")
        return _to_tts_reply(audio)
