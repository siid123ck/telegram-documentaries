"""Interview-client seam and the ADK-backed implementation.

Importing this module is cheap and provider-free: it exposes the
:class:`InterviewClient` protocol, the bounded :func:`next_turn_with_retry`
policy, the pure request builder, and the wire schema, without importing the
Google ADK. Only :class:`AdkInterviewClient` imports provider SDKs, lazily on
first use, so tests run offline with a fake client.

The provider path reuses the Bouncer's typed errors (``ProviderTimeout``,
``RateLimited``, ``VisionUnavailable``, ``InvalidResponse``) and its timeout,
retry, and error-mapping policy, so both providers degrade the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
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
from telegram_documentaries.interview_models import Answer, InterviewTurn
from telegram_documentaries.logging_setup import log_stage
from telegram_documentaries.prompts import INTERVIEW_INSTRUCTION
from telegram_documentaries.vision_client import (
    APP_NAME,
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    map_provider_error,
)

if TYPE_CHECKING:  # annotations only; never imported at runtime
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

logger = logging.getLogger(__name__)

#: Stable ADK user id for the interviewer. It carries no subject data.
INTERVIEW_USER_ID = "interviewer"

#: Errors that are safe to retry; an invalid response is not among them.
RETRYABLE_ERRORS: tuple[type[VisionError], ...] = (
    ProviderTimeout,
    RateLimited,
    VisionUnavailable,
)


@runtime_checkable
class InterviewClient(Protocol):
    """Structural seam for anything that can produce the next interview turn.

    ``history`` is the list of committed exchanges, oldest first. ``note`` is an
    optional instruction from the orchestrator (a forced finish, or a repair
    request after an invalid reply).
    """

    async def next_turn(
        self, history: Sequence[Answer], *, note: str | None = None
    ) -> InterviewTurn:
        """Return a validated turn, or raise a typed error."""
        ...


def parse_turn_reply(text: str) -> InterviewTurn:
    """Validate a raw model reply into an :class:`InterviewTurn`.

    Any malformed reply, including invalid JSON, an extra key, a wrong
    ``action``/field combination, or a dossier outside its bounds, becomes
    :class:`InvalidResponse`. The validation summary is kept only as ``detail``
    for the repair request.
    """
    try:
        return InterviewTurn.model_validate_json(text)
    except ValidationError as exc:
        raise InvalidResponse(detail=str(exc)) from exc


def build_turn_request(history: Sequence[Answer], note: str | None) -> str:
    """Serialise the exchanges and the optional note as one JSON document.

    Answers are user data. They are JSON-encoded, so quotes, newlines, or text
    that imitates instructions stay inside a string value and cannot change the
    shape of the request.
    """
    payload = {
        "exchanges": [
            {"question": exchange.question, "answer": exchange.answer}
            for exchange in history
        ],
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False)


def _flatten_schema(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref`` and convert nullable unions into Gemini-compatible form.

    Pydantic emits ``$defs`` references, ``additionalProperties``, and
    ``{"type": "null"}`` branches. The Gemini response schema accepts none of
    them, so they are rewritten here for the wire only. The strict local check
    (:class:`InterviewTurn`) still runs on every reply.
    """
    if isinstance(node, list):
        return [_flatten_schema(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _flatten_schema(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    branches = node.get("anyOf")
    if isinstance(branches, list) and {"type": "null"} in branches:
        non_null = [branch for branch in branches if branch != {"type": "null"}]
        if len(non_null) == 1:
            merged = {key: value for key, value in node.items() if key != "anyOf"}
            merged.update(_flatten_schema(non_null[0], defs))
            merged["nullable"] = True
            return _drop_wire_noise(merged)
    return _flatten_schema_object(node, defs)


def _drop_wire_noise(node: dict[str, Any]) -> dict[str, Any]:
    """Remove keys Gemini does not accept, such as defaults and the null type."""
    return {key: value for key, value in node.items() if key != "default"}


def _flatten_schema_object(
    node: dict[str, Any], defs: dict[str, Any]
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in _drop_wire_noise(node).items():
        if key == "additionalProperties":
            continue
        flattened[key] = _flatten_schema(value, defs)
    return flattened


def interview_response_schema() -> dict[str, Any]:
    """Return the JSON schema sent to Gemini as the interviewer's response schema.

    Every top-level key is marked required (optional values are nullable), so the
    model always emits the full ``action`` / ``question`` / ``dossier`` shape.
    """
    raw = InterviewTurn.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})
    schema: dict[str, Any] = _flatten_schema(raw, defs)
    schema["required"] = list(schema["properties"])
    return schema


@log_stage
async def next_turn_with_retry(
    client: InterviewClient,
    history: Sequence[Answer],
    *,
    note: str | None = None,
) -> InterviewTurn:
    """Request a turn with bounded retry and safe error logging.

    Retryable provider failures (timeout, rate limit, temporary unavailability)
    are retried once after a short backoff. :class:`InvalidResponse` fails fast
    here; the orchestrator owns the single repair attempt. Only the error type is
    logged, never the history, the note, or the model's reply.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await client.next_turn(history, note=note)
        except InvalidResponse:
            # Not retried here; the orchestrator logs it, since it decides
            # whether the single repair attempt recovers the turn.
            raise
        except RETRYABLE_ERRORS as exc:
            if attempt >= MAX_ATTEMPTS:
                logger.error(
                    "interview_call_failed attempts=%d error_type=%s",
                    attempt,
                    type(exc).__name__,
                )
                raise
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)


class AdkInterviewClient:
    """``InterviewClient`` backed by a Google ADK agent.

    The Gemini client is built from the explicit API key the caller supplies. The
    agent is created lazily on the first turn, so constructing the client never
    imports the ADK or touches the network. Each call runs in a fresh ADK session,
    so no conversation state accumulates inside the provider; the history travels
    in every request instead.
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
                model=MODELS["interviewer"],
                client=Client(api_key=self._api_key),
            )
            agent = Agent(
                name="interviewer",
                model=model,
                instruction=INTERVIEW_INSTRUCTION,
                output_schema=interview_response_schema(),
            )
            self._session_service = adk_sessions.InMemorySessionService()
            self._runner = adk_runners.Runner(
                agent=agent,
                app_name=APP_NAME,
                session_service=self._session_service,
            )
        return self._runner, self._session_service

    async def _run_agent(
        self, session_id: str, content: genai_types.Content
    ) -> InterviewTurn:
        """Run the agent once for a fresh session and validate its final reply."""
        runner, session_service = self._ensure_runner()
        await session_service.create_session(
            app_name=APP_NAME, user_id=INTERVIEW_USER_ID, session_id=session_id
        )
        async for event in runner.run_async(
            user_id=INTERVIEW_USER_ID,
            session_id=session_id,
            new_message=content,
        ):
            if not event.is_final_response() or event.content is None:
                continue
            for part in event.content.parts or []:
                if part.text:
                    return parse_turn_reply(part.text)
        raise InvalidResponse(detail="no final text response")

    async def next_turn(
        self, history: Sequence[Answer], *, note: str | None = None
    ) -> InterviewTurn:
        """Request one turn through ADK under a hard deadline; map every failure."""
        from google.genai import types

        content = types.Content(
            role="user",
            parts=[types.Part(text=build_turn_request(history, note))],
        )
        session_id = f"interview-{uuid.uuid4().hex}"
        try:
            return await asyncio.wait_for(
                self._run_agent(session_id, content),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except VisionError:
            raise
        except Exception as exc:  # provider SDK failure; mapped, never leaked
            # Only the exception class and HTTP status are logged. No message,
            # key, prompt, answer, or payload reaches the log.
            status = getattr(exc, "code", None)
            logger.error(
                "interview_provider_error error_type=%s provider_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            raise map_provider_error(exc) from exc
