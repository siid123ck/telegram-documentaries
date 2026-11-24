"""Shared offline doubles for the Bouncer and Interviewer test suites.

Nothing in this module imports the Google ADK, opens a socket, or reads ``.env``.
The fakes stand in for the providers at the :class:`VisionClient` and
:class:`InterviewClient` seams.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from telegram import Chat, Message, PhotoSize, Update

from telegram_documentaries.converter_models import PNG_MAGIC
from telegram_documentaries.interview_models import Answer, Dossier, InterviewTurn
from telegram_documentaries.models import ChatId, PhotoPayload, VisionVerdict

#: Minimal JPEG-like payload. Tests assert it is forwarded unchanged.
JPEG_BYTES = b"\xff\xd8\xff\xd9"

#: A minimal, valid-magic PNG standing in for a generated portrait.
PORTRAIT_BYTES = PNG_MAGIC + b"portrait-bytes"

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def text_update(text: str, *, chat_id: int = 4242, update_id: int = 1) -> Update:
    """A plain text message update, built locally (no Bot, no network)."""
    message = Message(
        message_id=update_id,
        date=_NOW,
        chat=Chat(id=chat_id, type="private"),
        text=text,
    )
    return Update(update_id=update_id, message=message)


def photo_update(
    file_id: str = "file-1",
    *,
    chat_id: int = 4242,
    update_id: int = 1,
    file_size: int | None = 1000,
) -> Update:
    """A single-photo message update, built locally."""
    size = PhotoSize(
        file_id=file_id,
        file_unique_id=f"unique-{file_id}",
        width=640,
        height=480,
        file_size=file_size,
    )
    message = Message(
        message_id=update_id,
        date=_NOW,
        chat=Chat(id=chat_id, type="private"),
        photo=(size,),
    )
    return Update(update_id=update_id, message=message)


class FakeDownloader:
    """Returns fixed bytes (or raises) and records every requested file id."""

    def __init__(
        self, data: bytes = JPEG_BYTES, error: Exception | None = None
    ) -> None:
        self._data = data
        self._error = error
        self.requested: list[str] = []

    async def __call__(self, file_id: str) -> bytes:
        self.requested.append(file_id)
        if self._error is not None:
            raise self._error
        return self._data


class GatedDownloader:
    """Holds the download open until released, so a photo can be in flight."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.requested: list[str] = []

    async def __call__(self, file_id: str) -> bytes:
        self.requested.append(file_id)
        await self.release.wait()
        return JPEG_BYTES


class RecordingSender:
    """Captures every reply the router would send to Telegram."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def __call__(self, update: Update, text: str) -> None:
        self.sent.append(text)


def verdict(*, human: bool = True, subject: str = "person") -> VisionVerdict:
    """Build a canned, schema-valid verdict."""
    return VisionVerdict(
        contains_human=human,
        reason="Canned reason for the test.",
        detected_subject=subject,
    )


class FakeVisionClient:
    """Scripted ``VisionClient``.

    Raises the queued ``errors`` first (one per call), then returns ``result``.
    When ``gate`` is supplied, each call waits on it before answering, which lets
    tests hold a classification in flight. Every call is recorded.
    """

    def __init__(
        self,
        *,
        result: VisionVerdict | None = None,
        errors: Sequence[Exception] = (),
        gate: asyncio.Event | None = None,
    ) -> None:
        self._result = result
        self._errors = list(errors)
        self._gate = gate
        self.photos: list[PhotoPayload] = []
        self.repair_notes: list[str | None] = []

    @property
    def calls(self) -> int:
        """Number of times the provider was asked to classify a photo."""
        return len(self.photos)

    async def classify(
        self, photo: PhotoPayload, *, repair_note: str | None = None
    ) -> VisionVerdict:
        self.photos.append(photo)
        self.repair_notes.append(repair_note)
        if self._gate is not None:
            await self._gate.wait()
        if self._errors:
            raise self._errors.pop(0)
        assert self._result is not None, "FakeVisionClient needs a result or an error"
        return self._result


def dossier(*, quirk_count: int = 3, animal: str = "Meerkat") -> Dossier:
    """Build a schema-valid dossier with ``quirk_count`` quirks."""
    return Dossier.model_validate(
        {
            "quirks": [
                {"topic": f"topic-{i}", "observation": f"Observed habit {i}."}
                for i in range(quirk_count)
            ],
            "suggested_animal": {
                "name": animal,
                "reason": "Keeps a lookout while the others eat.",
            },
        }
    )


def ask(question: str) -> InterviewTurn:
    """A valid ``ask`` turn."""
    return InterviewTurn(action="ask", question=question)


def finish(quirk_count: int = 3) -> InterviewTurn:
    """A valid ``finish`` turn carrying a dossier."""
    return InterviewTurn(action="finish", dossier=dossier(quirk_count=quirk_count))


class FakeInterviewClient:
    """Scripted ``InterviewClient``.

    Each call pops the next scripted item: an :class:`InterviewTurn` is returned,
    an exception is raised. Every call records a copy of the history it received
    and the repair or finish note, so tests can assert what the model was told.
    """

    def __init__(
        self,
        script: Sequence[InterviewTurn | Exception],
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._script = list(script)
        self._gate = gate
        self.histories: list[list[Answer]] = []
        self.notes: list[str | None] = []

    @property
    def calls(self) -> int:
        """Number of times the provider was asked for a turn."""
        return len(self.histories)

    async def next_turn(
        self, history: Sequence[Answer], *, note: str | None = None
    ) -> InterviewTurn:
        self.histories.append(list(history))
        self.notes.append(note)
        if self._gate is not None:
            await self._gate.wait()
        if not self._script:
            raise AssertionError("FakeInterviewClient ran out of scripted turns")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakePortraitClient:
    """Scripted ``PortraitClient``.

    Each call pops the next scripted item: bytes are returned, an exception is
    raised. When ``gate`` is supplied, each call waits on it first, so a test can
    hold a generation in flight. Every call records the photo bytes it received
    and the repair note it was given (the stage never sends one in Phase 4).
    """

    def __init__(
        self,
        script: Sequence[bytes | Exception] = (PORTRAIT_BYTES,),
        *,
        gate: asyncio.Event | None = None,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self._script = list(script)
        self._gate = gate
        self._on_call = on_call
        self.photos: list[bytes] = []
        self.repair_notes: list[str | None] = []

    @property
    def calls(self) -> int:
        """Number of times the provider was asked for a portrait."""
        return len(self.photos)

    async def render(
        self,
        photo_bytes: bytes,
        dossier: Dossier,
        /,
        *,
        repair_note: str | None = None,
    ) -> bytes:
        self.photos.append(photo_bytes)
        self.repair_notes.append(repair_note)
        if self._on_call is not None:
            # Lets a test act while the model call is in flight, e.g. a /restart.
            self._on_call()
        if self._gate is not None:
            await self._gate.wait()
        if not self._script:
            raise AssertionError("FakePortraitClient ran out of scripted replies")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingPhotoSender:
    """Captures every portrait the delivery step would send to Telegram.

    Records the chat id, the image bytes, and the caption. When ``error`` is
    supplied, every send raises it instead, to model a Telegram failure.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[tuple[ChatId, bytes, str]] = []

    async def __call__(self, chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((chat_id, image_bytes, caption))


class FakeScriptClient:
    """Scripted ``ScriptClient``: each call pops the next reply or raises the error.

    ``gate``, when supplied, holds every call open until it is set, so a test can
    reset the chat while the model is working. Every call records the repair note
    it was given; the stage never sends the rejected text back.
    """

    def __init__(
        self,
        script: Sequence[str | Exception],
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._script = list(script)
        self._gate = gate
        self.repair_notes: list[str | None] = []

    @property
    def calls(self) -> int:
        """Number of times the provider was asked for a script."""
        return len(self.repair_notes)

    async def write(self, dossier: Dossier, *, repair_note: str | None = None) -> str:
        self.repair_notes.append(repair_note)
        if self._gate is not None:
            await self._gate.wait()
        if not self._script:
            raise AssertionError("FakeScriptClient ran out of scripted replies")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
