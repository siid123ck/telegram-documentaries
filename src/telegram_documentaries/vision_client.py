"""Vision-client seam and the ADK-backed implementation.

Importing this module is cheap and provider-free: it exposes the
:class:`VisionClient` protocol, the bounded :func:`classify_with_retry` policy,
and the pure error-mapping helpers without importing the Google ADK. Only
:class:`AdkVisionClient` imports provider SDKs, and it does so lazily on first
use, so tests can use fakes without loading ADK or opening a socket.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import ValidationError

from telegram_documentaries.config import MODELS
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.models import ChatId, PhotoPayload, VisionVerdict
from telegram_documentaries.prompts import BOUNCER_INSTRUCTION

if TYPE_CHECKING:  # annotations only; never imported at runtime
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

logger = logging.getLogger(__name__)

#: Hard ceiling on provider calls: one attempt plus exactly one retry.
MAX_ATTEMPTS = 2

#: Small, fixed backoff before the single retry. Bounded so a graceful failure
#: never leaves the user waiting on an exponential schedule.
RETRY_BACKOFF_SECONDS = 0.05

#: Provider call deadline, including session setup and streaming the response.
REQUEST_TIMEOUT_SECONDS = 10.0

#: Stable ADK application/session namespace (no user data).
APP_NAME = "telegram-documentaries"

#: Telegram photos arrive as JPEG unless a MIME type is supplied by the edge.
DEFAULT_MIME_TYPE = "image/jpeg"

#: Errors that are safe to retry once; an invalid response is not among them.
RETRYABLE_ERRORS: tuple[type[VisionError], ...] = (
    ProviderTimeout,
    RateLimited,
    VisionUnavailable,
)


@runtime_checkable
class VisionClient(Protocol):
    """Structural seam for anything that can classify a validated photo.

    ``repair_note`` is only supplied on the single repair attempt after an
    invalid response; it carries a short validation summary for the model.
    """

    async def classify(
        self, photo: PhotoPayload, *, repair_note: str | None = None
    ) -> VisionVerdict:
        """Return a validated verdict, or raise a typed vision error."""
        ...


def map_provider_error(exc: BaseException) -> VisionError:
    """Translate an unexpected provider exception into a typed, safe error.

    Matching uses the exception *type name* and quota phrasing only. The returned
    error carries a fixed, provider-free message, so no key, token, or payload
    from the provider can leak into user-facing or logged text.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "deadline" in name:
        return ProviderTimeout()
    if "ratelimit" in name or "resourceexhausted" in name:
        return RateLimited()
    if "429" in text or "rate limit" in text:
        return RateLimited()
    return VisionUnavailable()


def gemini_response_schema() -> dict[str, Any]:
    """Return the JSON schema sent to Gemini as the Bouncer's ``response_schema``.

    Pydantic emits ``additionalProperties: false`` for ``VisionVerdict``
    (``extra="forbid"``). Gemini's ``response_schema`` rejects that keyword with
    HTTP 400 INVALID_ARGUMENT, so it is dropped here for the wire only. The strict
    check still runs locally: :meth:`VisionVerdict.model_validate_json` rejects
    any extra key in the reply.
    """
    schema = VisionVerdict.model_json_schema()
    schema.pop("additionalProperties", None)
    return schema


def new_session_id(chat_id: ChatId) -> str:
    """Return a fresh ADK session id for one classification attempt.

    The ADK in-memory store rejects a reused session id, so every attempt (a
    second photo from the same chat, or a repair call) gets its own id. The chat
    id is included only to make logs and traces easier to follow.
    """
    return f"bouncer-{int(chat_id)}-{uuid.uuid4().hex}"


@log_stage
async def classify_with_retry(
    client: VisionClient,
    photo: PhotoPayload,
    *,
    repair_note: str | None = None,
) -> VisionVerdict:
    """Classify ``photo`` with bounded retry and safe error logging.

    Retryable provider failures (timeout, rate limit, temporary unavailability)
    are retried once after a short backoff. :class:`InvalidResponse` is
    non-retryable here and fails fast; the Bouncer stage owns the single repair
    attempt. The final error is re-raised for the routing layer to render; only
    safe metadata (attempt count, error type, chat id) is ever logged, never image
    bytes or model prose.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await client.classify(photo, repair_note=repair_note)
        except InvalidResponse:
            logger.error(
                "vision_response_invalid error_type=InvalidResponse chat_id=%s",
                photo.chat_id,
            )
            raise
        except RETRYABLE_ERRORS as exc:
            if attempt >= MAX_ATTEMPTS:
                logger.error(
                    "vision_call_failed attempts=%d error_type=%s chat_id=%s",
                    attempt,
                    type(exc).__name__,
                    photo.chat_id,
                )
                raise
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)


class AdkVisionClient:
    """``VisionClient`` backed by a Google ADK agent.

    The Gemini client is built from the explicit API key the caller supplies,
    not from the process environment (pydantic-settings does not export ``.env``
    values). The ADK agent is created lazily on the first classification so that
    constructing the client never imports the ADK or touches the network.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._runner: Runner | None = None
        self._session_service: InMemorySessionService | None = None

    def _ensure_runner(self) -> tuple[Runner, InMemorySessionService]:
        """Build the ADK agent, runner, and session store once, on first use."""
        if self._runner is None or self._session_service is None:
            from google.adk import runners as adk_runners
            from google.adk import sessions as adk_sessions
            from google.adk.agents import Agent
            from google.adk.models import Gemini
            from google.genai import Client

            model = Gemini(
                model=MODELS["bouncer"],
                client=Client(api_key=self._api_key),
            )
            agent = Agent(
                name="bouncer",
                model=model,
                instruction=BOUNCER_INSTRUCTION,
                output_schema=gemini_response_schema(),
            )
            self._session_service = adk_sessions.InMemorySessionService()
            self._runner = adk_runners.Runner(
                agent=agent,
                app_name=APP_NAME,
                session_service=self._session_service,
            )
        return self._runner, self._session_service

    async def _run_agent(
        self, user_id: str, session_id: str, content: genai_types.Content
    ) -> VisionVerdict:
        """Run the agent once for a fresh session and parse its final response."""
        runner, session_service = self._ensure_runner()
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            if not event.is_final_response() or event.content is None:
                continue
            for part in event.content.parts or []:
                if not part.text:
                    continue
                try:
                    return VisionVerdict.model_validate_json(part.text)
                except ValidationError as exc:
                    raise InvalidResponse(detail=str(exc)) from exc
        raise InvalidResponse(detail="no final text response")

    async def classify(
        self, photo: PhotoPayload, *, repair_note: str | None = None
    ) -> VisionVerdict:
        """Classify ``photo`` through ADK under a hard 10-second deadline."""
        from google.genai import types

        parts = [
            types.Part.from_bytes(data=photo.photo_bytes, mime_type=DEFAULT_MIME_TYPE)
        ]
        if repair_note is not None:
            parts.append(types.Part(text=repair_note))
        content = types.Content(role="user", parts=parts)
        user_id = str(int(photo.chat_id))
        session_id = new_session_id(photo.chat_id)
        try:
            return await asyncio.wait_for(
                self._run_agent(user_id, session_id, content),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except VisionError:
            raise
        except Exception as exc:  # provider SDK failure; mapped, never leaked
            # Log only the exception class and HTTP status so the next failure is
            # diagnosable from the terminal. No message, key, or payload is logged.
            status = getattr(exc, "code", None)
            logger.error(
                "vision_provider_error error_type=%s provider_status=%s chat_id=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
                photo.chat_id,
            )
            raise map_provider_error(exc) from exc
