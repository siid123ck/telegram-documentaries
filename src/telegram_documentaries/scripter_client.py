"""Scripter-client seam and the ADK-backed implementation (Phase 5).

Importing this module is cheap and provider-free. It exposes the
:class:`ScriptClient` protocol, the pure request builder, the reply parser, and
the one-field response schema, without importing the Google ADK. Only
:class:`AdkScriptTransport` imports the ADK, and only when a call is made, so tests
run offline with an injected transport.

The client makes exactly one attempt per :meth:`AdkScriptClient.write` call. The
attempt budget, the repair note, and the retry backoff belong to the Scripter
stage, which owns the paid-call guard. The provider path reuses the typed errors
and the status mapping from the Bouncer, so every text model degrades the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from telegram_documentaries.config import MODELS
from telegram_documentaries.errors import InvalidResponse, ProviderTimeout, VisionError
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.prompts import SCRIPTER_INSTRUCTION
from telegram_documentaries.vision_client import APP_NAME, map_provider_error
from telegram_documentaries.wire_schema import flatten_wire_schema

logger = logging.getLogger(__name__)

#: Deadline per attempt. Text generation is short, so it shares the 10-second
#: limit of the Bouncer and Interviewer (requirements S11).
SCRIPT_TIMEOUT_SECONDS: Final = 10.0

#: Stable ADK user id for the scripter. It carries no subject data.
SCRIPT_USER_ID: Final = "scripter"


class ScriptReply(BaseModel):
    """The wire shape of the model's reply: one string field, nothing else.

    This checks only the shape. The word count, paragraph, and markdown rules are
    applied later by the Scripter stage (``Script``), so the reason codes stay in
    one place.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    script: str


@runtime_checkable
class ScriptClient(Protocol):
    """Structural seam for anything that can write the narration paragraph.

    ``repair_note`` is supplied only on the repair attempt after a validation
    failure. It carries the rule name and word count, never the failed text.
    The client returns the raw ``script`` string, unvalidated.
    """

    async def write(self, dossier: Dossier, *, repair_note: str | None = None) -> str:
        """Return the raw script string, or raise a typed error."""
        ...


class ScriptTransport(Protocol):
    """The single provider call: one structured request, returning the raw reply.

    Production uses :class:`AdkScriptTransport`. Tests inject a fake, so no socket
    is opened and the ADK is never imported.
    """

    async def __call__(
        self,
        *,
        model: str,
        instruction: str,
        request_text: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Return the model's final text reply, unvalidated."""
        ...


def script_response_schema() -> dict[str, Any]:
    """Return the Gemini response schema for the one-field script reply."""
    return flatten_wire_schema(ScriptReply)


def build_script_request(dossier: Dossier, repair_note: str | None) -> str:
    """Serialise the dossier and the optional note as one JSON document.

    The dossier is user-derived data. It is JSON-encoded, so quotes, newlines, or
    text that imitates instructions stay inside a string value and cannot change
    the shape of the request. No photo and no raw answers are sent (requirements
    S16).
    """
    payload = {
        "suggested_animal": {
            "name": dossier.suggested_animal.name,
            "reason": dossier.suggested_animal.reason,
        },
        "quirks": [
            {"topic": quirk.topic, "observation": quirk.observation}
            for quirk in dossier.quirks
        ],
        "note": repair_note,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_script_reply(text: str) -> str:
    """Return the ``script`` string from a raw reply, or raise :class:`InvalidResponse`.

    A reply that is not JSON, is not an object, has a non-string ``script``, or has
    any extra key is invalid. The error carries only the number of schema errors.
    Pydantic's own message is deliberately not kept, because it echoes the input
    and so would copy the model's prose into the error.
    """
    try:
        return ScriptReply.model_validate_json(text).script
    except ValidationError as exc:
        raise InvalidResponse(detail=f"{exc.error_count()} schema error(s)") from exc


class AdkScriptTransport:
    """:class:`ScriptTransport` backed by a single-turn Google ADK agent.

    The ADK objects are built inside the call, from the instruction and schema the
    caller passes, so constructing the transport never imports the ADK or touches
    the network. Each call runs in a fresh session, so no state accumulates in the
    provider.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def __call__(
        self,
        *,
        model: str,
        instruction: str,
        request_text: str,
        response_schema: dict[str, Any],
    ) -> str:
        from google.adk import runners as adk_runners
        from google.adk import sessions as adk_sessions
        from google.adk.agents import Agent
        from google.adk.models import Gemini
        from google.genai import Client, types

        llm = Gemini(model=model, client=Client(api_key=self._api_key))
        agent = Agent(
            name="scripter",
            model=llm,
            instruction=instruction,
            output_schema=response_schema,
        )
        session_service = adk_sessions.InMemorySessionService()
        runner = adk_runners.Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=session_service,
        )
        session_id = f"scripter-{uuid.uuid4().hex}"
        await session_service.create_session(
            app_name=APP_NAME, user_id=SCRIPT_USER_ID, session_id=session_id
        )
        content = types.Content(role="user", parts=[types.Part(text=request_text)])
        async for event in runner.run_async(
            user_id=SCRIPT_USER_ID,
            session_id=session_id,
            new_message=content,
        ):
            if not event.is_final_response() or event.content is None:
                continue
            for part in event.content.parts or []:
                if part.text:
                    return part.text
        raise InvalidResponse(detail="no final text response")


class AdkScriptClient:
    """:class:`ScriptClient` backed by the text model through ADK.

    Pass a ``transport`` in tests to avoid the network. Without one, the real
    :class:`AdkScriptTransport` is used.
    """

    def __init__(self, api_key: str, transport: ScriptTransport | None = None) -> None:
        self._transport: ScriptTransport = (
            transport if transport is not None else AdkScriptTransport(api_key)
        )

    async def write(self, dossier: Dossier, *, repair_note: str | None = None) -> str:
        """Make one attempt under the 10-second deadline and map every failure."""
        request_text = build_script_request(dossier, repair_note)
        try:
            reply = await asyncio.wait_for(
                self._transport(
                    model=MODELS["scripter"],
                    instruction=SCRIPTER_INSTRUCTION,
                    request_text=request_text,
                    response_schema=script_response_schema(),
                ),
                timeout=SCRIPT_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except VisionError:
            raise
        except Exception as exc:  # provider SDK failure; mapped, never leaked
            # Only the exception class and HTTP status are logged. No message,
            # prompt, dossier, or reply reaches the log.
            status = getattr(exc, "code", None)
            logger.error(
                "script_provider_error error_type=%s provider_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            raise map_provider_error(exc) from exc
        return parse_script_reply(reply)
