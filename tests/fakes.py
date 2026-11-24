"""Shared offline doubles for the Bouncer test suite.

Nothing in this module imports the Google ADK, opens a socket, or reads ``.env``.
The fakes stand in for the vision provider at the :class:`VisionClient` seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from telegram_documentaries.models import PhotoPayload, VisionVerdict

#: Minimal JPEG-like payload. Tests assert it is forwarded unchanged.
JPEG_BYTES = b"\xff\xd8\xff\xd9"


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
