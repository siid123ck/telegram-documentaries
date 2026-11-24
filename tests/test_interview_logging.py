"""Logging and safety tests for the interview path (task group 7).

Each test plants sentinel strings in the fakes (answers, question text, quirk
text, the animal's reason, image bytes, and a fake key) and runs the real router
end to end. The captured log text must carry the operational fields (chat id,
phases, question index, animal name) and must never carry the sentinels.
"""

from __future__ import annotations

import base64
import logging

import pytest
from fakes import (
    FakeInterviewClient,
    FakePortraitClient,
    FakeVisionClient,
    RecordingPhotoSender,
    RecordingSender,
    ask,
    finish,
    photo_update,
    text_update,
    verdict,
)

from telegram_documentaries.errors import InvalidResponse
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.models import as_chat_id
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.state import Phase, SessionState, SessionStore

SENTINEL_ANSWER = "SENTINEL-ANSWER-7f3a"
SENTINEL_QUIRK = "SENTINEL-QUIRK-9c1d"
SENTINEL_REASON = "SENTINEL-REASON-2b8e"
SENTINEL_QUESTION = "SENTINEL-QUESTION-4e6f"
SENTINEL_IMAGE = b"SENTINEL-IMAGE-BYTES-8d2c"
ANIMAL = "Meerkat"


def _dossier() -> Dossier:
    return Dossier.model_validate(
        {
            "quirks": [
                {"topic": "sleep", "observation": SENTINEL_QUIRK},
                {"topic": "forage", "observation": "Digs for grubs."},
                {"topic": "territory", "observation": "Patrols the fence."},
            ],
            "suggested_animal": {"name": ANIMAL, "reason": SENTINEL_REASON},
        }
    )


def _router(
    interview: FakeInterviewClient,
    store: SessionStore | None = None,
) -> tuple[UpdateRouter, RecordingSender, SessionStore]:
    store = store if store is not None else SessionStore()
    sender = RecordingSender()

    async def download(file_id: str) -> bytes:
        return SENTINEL_IMAGE

    router = UpdateRouter(
        vision=FakeVisionClient(result=verdict(human=True)),
        interview=interview,
        store=store,
        download=download,
        portraits=FakePortraitClient(),
        send_photo=RecordingPhotoSender(),
        reply=sender,
    )
    return router, sender, store


def _full_script() -> FakeInterviewClient:
    """Five questions, then a finish on the fifth answer, all carrying sentinels."""
    asks = [ask(f"{SENTINEL_QUESTION} {index}?") for index in range(1, 6)]
    return FakeInterviewClient([*asks, finish()])


async def _run_full_interview(router: UpdateRouter) -> None:
    await router.handle_photo(photo_update("p-1", update_id=1))
    for index in range(1, 6):
        await router.handle_text(
            text_update(f"{SENTINEL_ANSWER} {index}", update_id=index + 1)
        )


async def test_a_full_run_logs_chat_phases_question_index_and_animal_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _ = _router(_full_script())

    with caplog.at_level(logging.INFO):
        await _run_full_interview(router)

    text = caplog.text
    assert "chat_id=4242" in text
    assert "phase_before=awaiting_photo" in text
    assert "phase_after=interviewing" in text
    assert "phase_before=interviewing" in text
    assert "phase_after=dossier_ready" in text
    assert "question_index=" in text
    assert f"animal={ANIMAL}" in text


async def test_a_full_run_never_logs_answers_questions_quirks_reasons_or_images(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _ = _router(_full_script())

    with caplog.at_level(logging.DEBUG):
        await _run_full_interview(router)

    text = caplog.text
    for sentinel in (
        SENTINEL_ANSWER,
        SENTINEL_QUIRK,
        SENTINEL_REASON,
        SENTINEL_QUESTION,
    ):
        assert sentinel not in text
    assert SENTINEL_IMAGE.decode() not in text
    assert base64.b64encode(SENTINEL_IMAGE).decode() not in text


async def test_each_interview_boundary_records_entry_outcome_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _ = _router(_full_script())

    with caplog.at_level(logging.INFO):
        await _run_full_interview(router)

    text = caplog.text
    assert "stage_start stage=start_interview" in text
    assert "stage_end stage=start_interview outcome=ok" in text
    assert "stage_start stage=answer_question" in text
    assert "stage_end stage=answer_question outcome=ok" in text
    assert "duration_ms=" in text


async def test_a_recovered_invalid_reply_is_logged_at_error_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    interview = FakeInterviewClient(
        [ask("Q1?"), InvalidResponse(detail="bad shape"), ask("Q2?")]
    )
    router, _, _ = _router(interview)
    await router.handle_photo(photo_update("p-1", update_id=1))

    with caplog.at_level(logging.INFO):
        await router.handle_text(text_update(f"{SENTINEL_ANSWER} 1", update_id=2))

    invalid = [
        r for r in caplog.records if "interview_response_invalid" in r.getMessage()
    ]
    assert invalid, "the recovered invalid reply must be logged"
    assert all(record.levelno == logging.ERROR for record in invalid)
    assert "bad shape" not in caplog.text
    assert SENTINEL_ANSWER not in caplog.text


async def test_a_text_with_no_question_is_logged_at_warning_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = SessionStore(
        initial={as_chat_id(4242): SessionState(phase=Phase.INTERVIEWING)}
    )
    router, _, _ = _router(FakeInterviewClient([]), store=store)

    with caplog.at_level(logging.WARNING):
        await router.handle_text(text_update(f"{SENTINEL_ANSWER} stray"))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("interview_text_without_question" in r.getMessage() for r in warnings)
    assert SENTINEL_ANSWER not in caplog.text
