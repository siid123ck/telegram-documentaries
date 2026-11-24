"""Wiring tests for the narration step in the live flow (Phase 6).

Everything runs through :class:`UpdateRouter`, so the test proves the narration
step is reachable from a real text update. The session store is real. The portrait,
the interview, the script, the narration client, the MP3 encoder, and the Telegram
senders are all offline fakes. Every send lands in one ordered log, so a test can
assert the chat order: portrait, script text, voice note.

Nothing here touches the network, the ADK, ``google.genai``, the real encoder, or
Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest
from fakes import (
    PORTRAIT_BYTES,
    FakeDownloader,
    FakeInterviewClient,
    FakePortraitClient,
    FakeScriptClient,
    FakeVisionClient,
    ask,
    finish,
    photo_update,
    text_update,
    verdict,
)
from telegram import Update

from telegram_documentaries import generation
from telegram_documentaries.config import NARRATOR_VOICE
from telegram_documentaries.errors import ProviderRejected
from telegram_documentaries.interview import restart
from telegram_documentaries.interview_models import InterviewTurn
from telegram_documentaries.messages import (
    ALREADY_DONE_TEXT,
    NARRATION_UNAVAILABLE_TEXT,
    ONE_AT_A_TIME_TEXT,
)
from telegram_documentaries.models import ChatId, as_chat_id
from telegram_documentaries.narrator_models import (
    EXPECTED_PCM_MIME,
    EXPECTED_SAMPLE_RATE_HZ,
    TtsReply,
    VoiceNote,
)
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.state import Phase, SessionStore

_CHAT = as_chat_id(4242)

#: A 75-word, markdown-free script, the same shape the Scripter accepts.
SCRIPT = " ".join(f"word{index}" for index in range(75))

#: A small MP3 that starts with an ID3 tag, so it would pass the VoiceNote check.
FAKE_MP3 = b"ID3" + b"\x00" * 64

_FIVE_QUESTIONS = [ask("Q1?"), ask("Q2?"), ask("Q3?"), ask("Q4?"), ask("Q5?")]

#: Upper bound on loop yields while waiting for a flow step, so a bug fails fast.
_MAX_YIELDS = 10_000


def _tts_reply() -> TtsReply:
    """A well-formed raw TTS reply. The fake encoder turns it into FAKE_MP3."""
    return TtsReply(
        pcm=b"\x00\x01" * 240,
        mime_type=EXPECTED_PCM_MIME,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
    )


class FakeNarrationClient:
    """Scripted ``NarrationClient``: each call pops a reply or raises the error.

    ``gate``, when supplied, holds every call open until it is set, so a test can
    send a duplicate while the synthesis is in flight. Every call is recorded with
    the text and voice it was given.
    """

    def __init__(
        self,
        outcomes: Sequence[TtsReply | Exception],
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._outcomes = list(outcomes)
        self._gate = gate
        self.calls: list[tuple[str, str]] = []

    async def synthesise(self, text: str, *, voice: str) -> TtsReply:
        self.calls.append((text, voice))
        if self._gate is not None:
            await self._gate.wait()
        if not self._outcomes:
            raise AssertionError("FakeNarrationClient ran out of scripted replies")
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeVoiceSender:
    """Records each voice note sent, in the shared order log. Can raise on send.

    ``gate``, when supplied, holds the send open until it is set, so a test can
    send a duplicate while the voice note is in flight. ``started`` counts entries.
    """

    def __init__(
        self,
        log: list[str],
        *,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._log = log
        self._error = error
        self._gate = gate
        self.started = 0
        self.notes: list[tuple[ChatId, VoiceNote]] = []

    async def __call__(self, chat_id: ChatId, note: VoiceNote) -> None:
        self.started += 1
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        self.notes.append((chat_id, note))
        self._log.append("voice")


@pytest.fixture(autouse=True)
def _fake_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real MP3 encoder, so no audio is encoded in these tests."""

    def encode(reply: TtsReply) -> VoiceNote:
        del reply
        return VoiceNote(mp3=FAKE_MP3)

    monkeypatch.setattr(generation, "encode_mp3", encode)


@dataclass
class _Harness:
    router: UpdateRouter
    store: SessionStore
    narrator: FakeNarrationClient
    voice: FakeVoiceSender
    #: Reply texts only, in the order they were sent.
    texts: list[str] = field(default_factory=list)
    #: Every chat send, in order: "portrait", "script", "voice", or a reply text.
    log: list[str] = field(default_factory=list)
    #: Every text, photo, and script send as (chat id, label), in order.
    sent_to: list[tuple[int, str]] = field(default_factory=list)


def _harness(
    *,
    narrations: Sequence[TtsReply | Exception] = (_tts_reply(),),
    narration_gate: asyncio.Event | None = None,
    voice_error: Exception | None = None,
    script_error: Exception | None = None,
    voice_gate: asyncio.Event | None = None,
    interview_turns: Sequence[InterviewTurn | Exception] = (
        *_FIVE_QUESTIONS,
        finish(),
    ),
    scripts_replies: Sequence[str | Exception] = (SCRIPT,),
    portrait_script: Sequence[bytes | Exception] = (PORTRAIT_BYTES,),
) -> _Harness:
    log: list[str] = []
    texts: list[str] = []
    sent_to: list[tuple[int, str]] = []

    async def reply(update: Update, text: str) -> None:
        texts.append(text)
        log.append(text)
        chat = update.effective_chat
        sent_to.append((chat.id if chat is not None else -1, text))

    async def send_photo(chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        log.append("portrait")
        sent_to.append((int(chat_id), "portrait"))

    async def send_text(chat_id: ChatId, text: str) -> None:
        # The script text is logged as a marker. The narration-unavailable note is
        # also sent through this seam, so its copy is recorded as a reply text.
        if text == SCRIPT:
            if script_error is not None:
                raise script_error
            log.append("script")
            sent_to.append((int(chat_id), "script"))
        else:
            texts.append(text)
            log.append(text)
            sent_to.append((int(chat_id), text))

    narrator = FakeNarrationClient(narrations, gate=narration_gate)
    voice = FakeVoiceSender(log, error=voice_error, gate=voice_gate)
    store = SessionStore()
    router = UpdateRouter(
        vision=FakeVisionClient(result=verdict(human=True)),
        interview=FakeInterviewClient(interview_turns),
        store=store,
        download=FakeDownloader(),
        portraits=FakePortraitClient(portrait_script),
        send_photo=send_photo,
        reply=reply,
        scripts=FakeScriptClient(scripts_replies),
        send_text=send_text,
        narrator=narrator,
        send_voice=voice,
    )
    return _Harness(
        router=router,
        store=store,
        narrator=narrator,
        voice=voice,
        texts=texts,
        log=log,
        sent_to=sent_to,
    )


async def _send_photo(h: _Harness) -> None:
    await h.router.handle_photo(photo_update("p1", chat_id=int(_CHAT)))


async def _send_text(h: _Harness, text: str, *, update_id: int) -> None:
    await h.router.handle_text(
        text_update(text, chat_id=int(_CHAT), update_id=update_id)
    )


def _restart_update() -> Update:
    """A ``/restart`` command update for the test chat."""
    return text_update("/restart", chat_id=int(_CHAT), update_id=99)


async def _finish_interview(h: _Harness) -> None:
    """Start the interview and give five answers. The fifth runs the whole flow."""
    await _send_photo(h)
    for index in range(1, 6):
        await _send_text(h, f"answer {index}", update_id=index + 1)


async def _until(condition: Callable[[], bool]) -> None:
    """Yield to the loop until ``condition`` holds, or fail rather than hang."""
    for _ in range(_MAX_YIELDS):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the expected step in the flow never happened")


# --- Success path -------------------------------------------------------------


async def test_the_voice_note_follows_the_script_text_after_the_portrait() -> None:
    h = _harness()

    await _finish_interview(h)

    assert h.log[-3:] == ["portrait", "script", "voice"]
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]


async def test_narration_is_one_synthesis_of_the_script_with_the_narrator_voice() -> (
    None
):
    h = _harness()

    await _finish_interview(h)

    # One paid call, given the script text and the configured voice only.
    assert h.narrator.calls == [(SCRIPT, NARRATOR_VOICE)]


async def test_a_successful_narration_leaves_the_chat_resting_in_delivering() -> None:
    h = _harness()

    await _finish_interview(h)

    state = h.store.get(_CHAT)
    assert state.phase is Phase.DELIVERING
    assert state.script == SCRIPT
    assert state.narration_started is True
    assert state.in_flight is False
    assert NARRATION_UNAVAILABLE_TEXT not in h.texts  # no apology on success


# --- Failure keeps the script -------------------------------------------------


async def test_a_narration_failure_sends_the_copy_and_keeps_the_script() -> None:
    h = _harness(narrations=[ProviderRejected()])

    await _finish_interview(h)

    assert h.log[-3:] == ["portrait", "script", NARRATION_UNAVAILABLE_TEXT]
    assert h.voice.notes == []
    assert h.narrator.calls == [(SCRIPT, NARRATOR_VOICE)]
    state = h.store.get(_CHAT)
    assert state.script == SCRIPT
    assert state.phase is Phase.DELIVERING
    assert state.narration_started is True
    assert state.in_flight is False


async def test_an_encoder_failure_sends_the_copy_and_keeps_the_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_encode(reply: TtsReply) -> VoiceNote:
        del reply
        raise ValueError("bad_mp3 (bytes=480)")

    monkeypatch.setattr(generation, "encode_mp3", failing_encode)
    h = _harness()

    await _finish_interview(h)

    assert h.log[-1] == NARRATION_UNAVAILABLE_TEXT
    assert h.voice.notes == []
    assert h.store.get(_CHAT).script == SCRIPT


async def test_a_voice_send_failure_sends_the_copy_once_and_keeps_the_script(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    h = _harness(voice_error=RuntimeError("telegram voice rejected"))

    await _finish_interview(h)

    assert h.texts[-1] == NARRATION_UNAVAILABLE_TEXT
    assert h.texts.count(NARRATION_UNAVAILABLE_TEXT) == 1
    assert h.narrator.calls == [(SCRIPT, NARRATOR_VOICE)]
    assert h.store.get(_CHAT).script == SCRIPT
    assert h.store.get(_CHAT).in_flight is False
    assert "narration_failed" in caplog.text


# --- A duplicate update does not re-run narration ------------------------------


async def test_a_duplicate_text_during_synthesis_is_nudged_and_not_resynthesised() -> (
    None
):
    gate = asyncio.Event()
    h = _harness(narration_gate=gate)
    run = asyncio.create_task(_finish_interview(h))
    await _until(lambda: h.narrator.calls != [])

    await _send_text(h, "are you still there?", update_id=50)

    assert h.texts[-1] == ONE_AT_A_TIME_TEXT
    assert len(h.narrator.calls) == 1
    gate.set()
    await asyncio.wait_for(run, timeout=5.0)
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]


async def test_a_text_after_a_failed_narration_is_not_a_second_synthesis() -> None:
    h = _harness(narrations=[ProviderRejected()])
    await _finish_interview(h)

    await _send_text(h, "one more try?", update_id=50)

    assert h.texts[-2:] == [NARRATION_UNAVAILABLE_TEXT, ALREADY_DONE_TEXT]
    assert len(h.narrator.calls) == 1
    assert h.voice.notes == []


async def test_a_text_after_a_successful_narration_is_not_a_second_synthesis() -> None:
    h = _harness()
    await _finish_interview(h)

    await _send_text(h, "again please", update_id=50)

    assert h.texts[-1] == ALREADY_DONE_TEXT
    assert len(h.narrator.calls) == 1
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]


# --- /restart and the late narration ------------------------------------------


async def test_restart_during_narration_drops_the_late_voice_note() -> None:
    gate = asyncio.Event()
    h = _harness(narration_gate=gate)
    run = asyncio.create_task(_finish_interview(h))
    await _until(lambda: h.narrator.calls != [])
    sent_before_restart = len(h.log)

    await h.router.handle_restart(_restart_update())
    gate.set()
    await asyncio.wait_for(run, timeout=5.0)

    # Only the restart's own reply reaches the chat after the reset: no voice note,
    # no narration-unavailable note.
    late_sends = h.log[sent_before_restart:]
    assert len(late_sends) == 1
    assert "voice" not in late_sends
    assert h.voice.notes == []
    assert h.store.get(_CHAT).phase is not Phase.DELIVERING


async def test_a_failed_script_send_still_narrates_and_sends_the_voice_note() -> None:
    h = _harness(script_error=RuntimeError("telegram script rejected"))

    await _finish_interview(h)

    assert h.narrator.calls == [(SCRIPT, NARRATOR_VOICE)]
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]
    assert h.log[-1] == "voice"
    assert h.store.get(_CHAT).in_flight is False


# --- V-3: routing cases the narration step must hold -------------------------


async def test_a_duplicate_during_the_voice_send_is_nudged_and_sends_one_note() -> None:
    gate = asyncio.Event()
    h = _harness(voice_gate=gate)
    run = asyncio.create_task(_finish_interview(h))
    await _until(lambda: h.voice.started == 1)

    await _send_text(h, "is it ready yet?", update_id=50)
    await h.router.handle_photo(photo_update("p2", chat_id=int(_CHAT)))

    # Both duplicates are nudged, and neither starts a second synthesis or send.
    assert h.log[-2:] == [ONE_AT_A_TIME_TEXT, ONE_AT_A_TIME_TEXT]
    assert len(h.narrator.calls) == 1
    assert h.log.count("portrait") == 1
    gate.set()
    await asyncio.wait_for(run, timeout=5.0)
    assert h.voice.started == 1
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]
    assert h.log.count("voice") == 1


async def test_restart_between_encode_and_send_drops_the_voice_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness()

    def encode_then_restart(reply: TtsReply) -> VoiceNote:
        # No await separates the encoder from the send check, so the reset that a
        # /restart would make is applied here, at the same point in the flow.
        del reply
        restart(_CHAT, h.store)
        return VoiceNote(mp3=FAKE_MP3)

    monkeypatch.setattr(generation, "encode_mp3", encode_then_restart)

    await _finish_interview(h)

    assert h.voice.notes == []
    assert "voice" not in h.log
    assert NARRATION_UNAVAILABLE_TEXT not in h.log  # a dropped run sends no apology
    state = h.store.get(_CHAT)
    assert state.phase is Phase.AWAITING_PHOTO
    assert state.script is None
    assert state.in_flight is False


async def test_narration_in_one_chat_does_not_reach_another_chat() -> None:
    other = 7777
    # Chat B asks for its photo and one answer, so it is mid-interview. Chat A then
    # runs the whole interview. The script order is: B's two asks, A's five asks,
    # then A's finish.
    turns = [*(ask(f"Q{i}?") for i in range(7)), finish()]
    h = _harness(interview_turns=turns)
    await h.router.handle_photo(photo_update("pB", chat_id=other))
    await h.router.handle_text(text_update("B answer 1", chat_id=other, update_id=200))
    before = len(h.sent_to)

    await _finish_interview(h)

    a_run = h.sent_to[before:]
    # Nothing from A's run reaches B, and A's portrait and script reach A.
    assert [label for chat, label in a_run if chat == other] == []
    a_labels = [label for chat, label in a_run if chat == int(_CHAT)]
    assert a_labels[-2:] == ["portrait", "script"]
    assert [chat_id for chat_id, _ in h.voice.notes] == [_CHAT]
    other_state = h.store.get(as_chat_id(other))
    assert other_state.phase is Phase.INTERVIEWING
    assert len(other_state.answers) == 1
    assert other_state.script is None
    assert other_state.narration_started is False


async def test_a_text_or_photo_after_narration_gets_already_done_and_no_payload() -> (
    None
):
    h = _harness()
    await _finish_interview(h)
    before = len(h.log)
    calls_before = len(h.narrator.calls)

    await _send_text(h, "one more voice please", update_id=50)
    await h.router.handle_photo(photo_update("p2", chat_id=int(_CHAT)))

    assert h.log[before:] == [ALREADY_DONE_TEXT, ALREADY_DONE_TEXT]
    assert len(h.narrator.calls) == calls_before
    assert h.voice.notes == [(_CHAT, VoiceNote(mp3=FAKE_MP3))]
    assert h.store.get(_CHAT).phase is Phase.DELIVERING


async def test_after_a_restart_a_fresh_interview_runs_to_narration_again() -> None:
    h = _harness(
        narrations=[_tts_reply(), _tts_reply()],
        interview_turns=[*_FIVE_QUESTIONS, finish(), *_FIVE_QUESTIONS, finish()],
        scripts_replies=[SCRIPT, SCRIPT],
        portrait_script=[PORTRAIT_BYTES, PORTRAIT_BYTES],
    )
    await _finish_interview(h)
    await h.router.handle_restart(_restart_update())

    await _finish_interview(h)

    assert h.log.count("portrait") == 2
    assert h.log.count("script") == 2
    assert h.log.count("voice") == 2
    assert h.narrator.calls == [(SCRIPT, NARRATOR_VOICE), (SCRIPT, NARRATOR_VOICE)]
    state = h.store.get(_CHAT)
    assert state.phase is Phase.DELIVERING
    assert state.narration_started is True
    assert state.in_flight is False
