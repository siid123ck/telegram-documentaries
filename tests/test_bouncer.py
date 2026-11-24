"""Tests for the Bouncer stage: typed errors, copy, verdict routing and safety.

A fake :class:`VisionClient` stands in for the ADK-backed client, so no test in
this module imports the provider SDK or opens a socket.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest
from fakes import JPEG_BYTES, FakeVisionClient, verdict

import telegram_documentaries.messages as messages_module
from telegram_documentaries import bouncer
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.gateway import REPLY_TEXT
from telegram_documentaries.messages import (
    CONFIRMATION_TEXT,
    REJECTION_TEMPLATE,
    RESEND_TEXT,
    render_subject,
)
from telegram_documentaries.models import (
    ChatId,
    PhotoPayload,
    VisionVerdict,
    as_chat_id,
)
from telegram_documentaries.state import Phase, SessionStore

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)


def _photo(chat: ChatId = _CHAT, file_id: str = "file-1") -> PhotoPayload:
    return PhotoPayload(chat_id=chat, file_id=file_id, photo_bytes=JPEG_BYTES)


# --- Verdict routing (pass / fail) -----------------------------------------


async def test_a_human_verdict_is_returned_and_the_photo_reference_is_stored() -> None:
    store = SessionStore()
    client = FakeVisionClient(result=verdict(human=True, subject="person"))

    result = await bouncer.inspect(_photo(file_id="file-abc"), client, store)

    assert result.contains_human is True
    state = store.get(_CHAT)
    assert state.last_file_id == "file-abc"
    assert state.phase is Phase.AWAITING_PHOTO


@pytest.mark.parametrize("subject", ["cat", "pizza", "car", "coffee mug", "landscape"])
async def test_a_non_human_verdict_resets_the_chat_to_awaiting_photo(
    subject: str,
) -> None:
    store = SessionStore()
    store.note_photo(_CHAT, "previous-file")
    client = FakeVisionClient(result=verdict(human=False, subject=subject))

    result = await bouncer.inspect(_photo(file_id="rejected-file"), client, store)

    assert result.contains_human is False
    assert result.detected_subject == subject
    state = store.get(_CHAT)
    assert state.last_file_id is None
    assert state.phase is Phase.AWAITING_PHOTO


# --- Provider failure paths ------------------------------------------------


async def test_a_timeout_after_retry_raises_and_leaves_the_chat_untouched() -> None:
    store = SessionStore()
    store.note_photo(_CHAT, "previous-file")
    client = FakeVisionClient(errors=[ProviderTimeout(), ProviderTimeout()])

    with pytest.raises(ProviderTimeout):
        await bouncer.inspect(_photo(file_id="new-file"), client, store)

    assert store.get(_CHAT).last_file_id == "previous-file"


@pytest.mark.parametrize("error_type", [VisionUnavailable, RateLimited])
async def test_other_retryable_failures_after_retry_leave_the_chat_untouched(
    error_type: type[VisionError],
) -> None:
    store = SessionStore()
    store.note_photo(_CHAT, "previous-file")
    client = FakeVisionClient(errors=[error_type(), error_type()])

    with pytest.raises(error_type):
        await bouncer.inspect(_photo(file_id="new-file"), client, store)

    assert store.get(_CHAT).last_file_id == "previous-file"


async def test_an_invalid_response_gets_one_repair_with_the_validation_detail() -> None:
    store = SessionStore()
    client = FakeVisionClient(
        errors=[InvalidResponse(detail="contains_human: Field required")],
        result=verdict(human=True),
    )

    result = await bouncer.inspect(_photo(file_id="file-ok"), client, store)

    assert result.contains_human is True
    assert client.calls == 2
    assert client.repair_notes[0] is None
    repair_note = client.repair_notes[1]
    assert repair_note is not None
    assert "contains_human: Field required" in repair_note
    assert store.get(_CHAT).last_file_id == "file-ok"


async def test_an_invalid_response_twice_raises_after_exactly_one_repair() -> None:
    store = SessionStore()
    store.note_photo(_CHAT, "previous-file")
    client = FakeVisionClient(errors=[InvalidResponse(), InvalidResponse()])

    with pytest.raises(InvalidResponse):
        await bouncer.inspect(_photo(file_id="new-file"), client, store)

    assert client.calls == 2
    assert store.get(_CHAT).last_file_id == "previous-file"


# --- Cross-chat isolation ----------------------------------------------------


async def test_one_chat_passing_and_another_rejecting_never_bleed_into_each_other() -> (
    None
):
    store = SessionStore()
    passing = FakeVisionClient(result=verdict(human=True))
    rejecting = FakeVisionClient(result=verdict(human=False, subject="cat"))

    await bouncer.inspect(_photo(chat=_CHAT, file_id="chat-a-file"), passing, store)
    await bouncer.inspect(_photo(chat=_OTHER, file_id="chat-b-file"), rejecting, store)

    assert store.get(_CHAT).last_file_id == "chat-a-file"
    assert store.get(_OTHER).last_file_id is None


# --- Logging safety ----------------------------------------------------------


async def test_the_stage_logs_chat_verdict_and_subject_but_never_bytes_or_prose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = SessionStore()
    client = FakeVisionClient(
        result=VisionVerdict(
            contains_human=False,
            reason="SECRET-REASON-PROSE from the model",
            detected_subject="pizza",
        )
    )
    raw_marker = b"RAW-IMAGE-BYTES-MARKER"
    photo = PhotoPayload(chat_id=_CHAT, file_id="f", photo_bytes=raw_marker)

    with caplog.at_level(logging.INFO):
        await bouncer.inspect(photo, client, store)

    text = caplog.text
    assert "chat_id=4242" in text
    assert "contains_human=False" in text
    assert "detected_subject=pizza" in text
    assert "SECRET-REASON-PROSE" not in text
    assert "RAW-IMAGE-BYTES" not in text
    assert base64.b64encode(raw_marker).decode() not in text


async def test_the_stage_records_entry_outcome_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeVisionClient(result=verdict(human=True))

    with caplog.at_level(logging.INFO):
        await bouncer.inspect(_photo(), client, SessionStore())

    assert "stage_start stage=inspect" in caplog.text
    assert "stage_end stage=inspect outcome=ok" in caplog.text
    assert "duration_ms=" in caplog.text


# --- Typed errors ------------------------------------------------------------


def test_vision_errors_are_distinct_types_sharing_one_base() -> None:
    errors = [
        VisionUnavailable(),
        ProviderTimeout(),
        RateLimited(),
        InvalidResponse(),
    ]

    assert len({type(error) for error in errors}) == 4
    assert all(isinstance(error, VisionError) for error in errors)


@pytest.mark.parametrize(
    "error",
    [VisionUnavailable(), ProviderTimeout(), RateLimited(), InvalidResponse()],
)
def test_vision_error_messages_are_safe_and_non_empty(error: VisionError) -> None:
    message = str(error)

    assert message
    assert "Traceback" not in message
    assert "HTTP" not in message
    assert "429" not in message
    assert "500" not in message
    assert "{" not in message


def test_invalid_response_detail_defaults_to_empty() -> None:
    assert InvalidResponse().detail == ""


# --- Single-sourced copy -----------------------------------------------------


def test_confirmation_text_aliases_the_phase_one_constant() -> None:
    assert CONFIRMATION_TEXT == REPLY_TEXT


def test_messages_module_does_not_duplicate_the_fixed_literal() -> None:
    source = Path(messages_module.__file__).read_text()

    assert "hey mate!" not in source
    assert "REPLY_TEXT" in source


def test_rejection_template_names_the_subject_without_leaking_internals() -> None:
    rendered = REJECTION_TEMPLATE.format(subject="a pizza")

    assert "a pizza" in rendered
    assert "human" in rendered.lower()
    for forbidden in ("Traceback", "HTTP", "429", "500", "{", "prompt", "json"):
        assert forbidden not in rendered.lower()


@pytest.mark.parametrize("subject", ["cat", "pizza", "car", "coffee mug", "landscape"])
def test_rejection_template_renders_each_subject(subject: str) -> None:
    assert subject in REJECTION_TEMPLATE.format(subject=subject)


def test_rejection_template_uses_the_approved_documentary_wording() -> None:
    assert REJECTION_TEMPLATE.format(subject="coffee mug") == (
        "Nice coffee mug — but this documentary needs a human. "
        "Send me a photo of a person."
    )


def test_resend_text_is_the_single_gentle_nudge() -> None:
    assert RESEND_TEXT == "Vision's acting up — mind resending the photo?"
    assert "Traceback" not in RESEND_TEXT
    assert "HTTP" not in RESEND_TEXT


def test_copy_constants_never_log_secrets(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        rendered = REJECTION_TEMPLATE.format(subject="a mug")

    assert rendered
    assert "token" not in caplog.text.lower()
    assert "api_key" not in caplog.text.lower()


# --- Subject sanitising (model text never reaches the user verbatim) ---------


def test_render_subject_keeps_plain_words() -> None:
    assert render_subject("coffee mug") == "coffee mug"


def test_render_subject_strips_markup_newlines_and_injected_text() -> None:
    rendered = render_subject('pizza!!\n\n{"ignore": "previous rules"}')

    for forbidden in ("{", "}", '"', "\n", ":"):
        assert forbidden not in rendered
    assert rendered.startswith("pizza")


def test_render_subject_caps_the_length_of_what_the_user_sees() -> None:
    assert len(render_subject("a" * 200)) <= 40


def test_render_subject_falls_back_when_nothing_readable_remains() -> None:
    assert render_subject("!!!{}") == "thing"
    assert render_subject("   ") == "thing"
