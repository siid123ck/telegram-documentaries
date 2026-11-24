"""Tests for the generation orchestrator: from a finished dossier to a portrait.

The store is real. The portrait client, the photo download, and the Telegram
photo sender are offline fakes, so nothing here touches the network. Each test
checks a user-visible outcome: what is sent, to which chat, and the resulting
session phase.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import pytest
from fakes import (
    JPEG_BYTES,
    PORTRAIT_BYTES,
    FakeDownloader,
    FakePortraitClient,
    RecordingPhotoSender,
    dossier,
)

from telegram_documentaries.converter_client import MAX_ATTEMPTS
from telegram_documentaries.converter_models import PNG_MAGIC
from telegram_documentaries.errors import (
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.generation import (
    run_generation,
    telegram_photo_sender,
)
from telegram_documentaries.interview_models import Answer
from telegram_documentaries.messages import (
    GENERATION_FAILED_TEXT,
    ONE_AT_A_TIME_TEXT,
    render_caption,
)
from telegram_documentaries.models import MAX_PHOTO_BYTES, ChatId, as_chat_id
from telegram_documentaries.state import Phase, SessionStore, StateError

_CHAT = as_chat_id(4242)
_OTHER = as_chat_id(7777)
_PHOTO_FILE = "photo-file-1"
_EXCHANGE = Answer(question="What keeps you up at night?", answer="Owls.")

_BAD_PAYLOADS: list[bytes] = [
    b"Sorry, I cannot draw that.",
    b"",
    b"GIF89a-wrong-magic",
    PNG_MAGIC + b"0" * MAX_PHOTO_BYTES,
]


def _ready(store: SessionStore, chat_id: ChatId = _CHAT) -> None:
    """Drive a chat through a finished interview into ``DOSSIER_READY``."""
    store.commit_first_question(chat_id, _PHOTO_FILE, "What keeps you up at night?")
    store.commit_answer(chat_id, _EXCHANGE)
    store.commit_dossier(chat_id, dossier())


class _GatedPhotoSender:
    """Holds the photo send open, so a test can reset the chat mid-send."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.sent: list[tuple[ChatId, bytes, str]] = []

    async def __call__(self, chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        self.entered.set()
        await self.release.wait()
        self.sent.append((chat_id, image_bytes, caption))


# --- Happy path ---------------------------------------------------------------


async def test_a_finished_dossier_is_delivered_as_one_portrait_to_its_chat() -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([PORTRAIT_BYTES])
    photos = RecordingPhotoSender()

    reply = await run_generation(_CHAT, store, FakeDownloader(), portraits, photos)

    assert reply is None
    assert photos.sent == [(_CHAT, PORTRAIT_BYTES, render_caption("Meerkat"))]
    assert portraits.calls == 1


async def test_the_chat_rests_in_delivering_with_the_dossier_kept() -> None:
    store = SessionStore()
    _ready(store)

    await run_generation(
        _CHAT, store, FakeDownloader(), FakePortraitClient(), RecordingPhotoSender()
    )

    state = store.get(_CHAT)
    assert state.phase is Phase.DELIVERING
    assert state.in_flight is False
    assert state.generation_run is None
    assert state.dossier == dossier()
    assert state.answers == [_EXCHANGE]


async def test_the_original_photo_is_downloaded_by_file_id_and_passed_on() -> None:
    store = SessionStore()
    _ready(store)
    download = FakeDownloader(JPEG_BYTES)
    portraits = FakePortraitClient([PORTRAIT_BYTES])

    await run_generation(_CHAT, store, download, portraits, RecordingPhotoSender())

    assert download.requested == [_PHOTO_FILE]
    assert portraits.photos == [JPEG_BYTES]


async def test_the_caption_has_two_lines_and_names_the_animal() -> None:
    store = SessionStore()
    _ready(store)
    photos = RecordingPhotoSender()

    await run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)

    _, _, caption = photos.sent[0]
    lines = caption.split("\n")
    assert len(lines) == 2
    assert "Meerkat" in lines[0]


async def test_a_successful_run_returns_no_text_so_the_portrait_is_the_reply() -> None:
    store = SessionStore()
    _ready(store)

    reply = await run_generation(
        _CHAT, store, FakeDownloader(), FakePortraitClient(), RecordingPhotoSender()
    )

    assert reply is None


# --- Wrong payload during generation -----------------------------------------


@pytest.mark.parametrize("payload", _BAD_PAYLOADS, ids=lambda p: f"len-{len(p)}")
async def test_a_wrong_payload_sends_the_apology_and_no_portrait(
    payload: bytes,
) -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([payload, PORTRAIT_BYTES])
    photos = RecordingPhotoSender()

    reply = await run_generation(_CHAT, store, FakeDownloader(), portraits, photos)

    assert reply == GENERATION_FAILED_TEXT
    assert photos.sent == []
    assert portraits.calls == 1
    state = store.get(_CHAT)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert state.generation_run is None
    assert state.answers == [_EXCHANGE]
    assert state.dossier == dossier()


# --- Provider errors ----------------------------------------------------------


_RETRYABLE: list[type[VisionError]] = [
    ProviderTimeout,
    RateLimited,
    VisionUnavailable,
]


@pytest.mark.parametrize("error_type", _RETRYABLE, ids=lambda t: t.__name__)
async def test_a_retryable_provider_error_is_attempted_twice_then_apologised(
    error_type: type[VisionError],
) -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([error_type(), error_type()])
    photos = RecordingPhotoSender()

    reply = await run_generation(_CHAT, store, FakeDownloader(), portraits, photos)

    assert portraits.calls == MAX_ATTEMPTS == 2
    assert reply == GENERATION_FAILED_TEXT
    assert photos.sent == []
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert store.get(_CHAT).answers == [_EXCHANGE]


async def test_a_rejected_request_is_attempted_once_then_apologised() -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([ProviderRejected(), PORTRAIT_BYTES])

    reply = await run_generation(
        _CHAT, store, FakeDownloader(), portraits, RecordingPhotoSender()
    )

    assert portraits.calls == 1
    assert reply == GENERATION_FAILED_TEXT
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert store.get(_CHAT).dossier == dossier()


async def test_a_download_failure_apologises_and_makes_no_generation_call() -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([PORTRAIT_BYTES])

    reply = await run_generation(
        _CHAT,
        store,
        FakeDownloader(error=RuntimeError("download broke")),
        portraits,
        RecordingPhotoSender(),
    )

    assert reply == GENERATION_FAILED_TEXT
    assert portraits.calls == 0
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY
    assert store.get(_CHAT).in_flight is False


async def test_a_telegram_send_failure_rolls_back_and_apologises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = SessionStore()
    _ready(store)
    photos = RecordingPhotoSender(error=RuntimeError("telegram said no"))

    with caplog.at_level(logging.ERROR):
        reply = await run_generation(
            _CHAT, store, FakeDownloader(), FakePortraitClient(), photos
        )

    assert reply == GENERATION_FAILED_TEXT
    state = store.get(_CHAT)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert state.dossier == dossier()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("error_type=RuntimeError" in message for message in errors)


async def test_an_unexpected_error_is_not_swallowed_and_the_chat_is_not_stranded() -> (
    None
):
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([RuntimeError("a bug in the model client")])

    with pytest.raises(RuntimeError):
        await run_generation(
            _CHAT, store, FakeDownloader(), portraits, RecordingPhotoSender()
        )

    state = store.get(_CHAT)
    assert state.phase is Phase.DOSSIER_READY
    assert state.in_flight is False
    assert state.generation_run is None


# --- Guards and stale runs ----------------------------------------------------


async def test_a_second_run_while_one_is_in_flight_is_nudged_not_started() -> None:
    store = SessionStore()
    _ready(store)
    assert store.begin_generation(_CHAT) is not None
    portraits = FakePortraitClient([PORTRAIT_BYTES])

    reply = await run_generation(
        _CHAT, store, FakeDownloader(), portraits, RecordingPhotoSender()
    )

    assert reply == ONE_AT_A_TIME_TEXT
    assert portraits.calls == 0
    assert store.get(_CHAT).phase is Phase.GENERATING


async def test_a_run_requested_outside_dossier_ready_is_refused() -> None:
    store = SessionStore()
    store.commit_first_question(_CHAT, _PHOTO_FILE, "Q?")
    portraits = FakePortraitClient([PORTRAIT_BYTES])

    with pytest.raises(StateError):
        await run_generation(
            _CHAT, store, FakeDownloader(), portraits, RecordingPhotoSender()
        )

    assert portraits.calls == 0
    assert store.get(_CHAT).phase is Phase.INTERVIEWING


async def test_a_reset_during_generation_drops_the_late_portrait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = SessionStore()
    _ready(store)
    gate = asyncio.Event()
    portraits = FakePortraitClient([PORTRAIT_BYTES], gate=gate)
    photos = RecordingPhotoSender()

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(
            run_generation(_CHAT, store, FakeDownloader(), portraits, photos)
        )
        await asyncio.sleep(0)  # let the run reach the blocked model call
        store.reset(_CHAT)
        gate.set()
        reply = await task

    assert reply is None
    assert photos.sent == []
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert store.get(_CHAT).generation_run is None
    dropped = [r for r in caplog.records if "stale_run" in r.getMessage()]
    assert dropped
    assert all(r.levelno == logging.INFO for r in dropped)


async def test_a_reset_during_the_send_is_accepted_without_a_crash() -> None:
    store = SessionStore()
    _ready(store)
    photos = _GatedPhotoSender()

    task = asyncio.create_task(
        run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)
    )
    await asyncio.wait_for(photos.entered.wait(), timeout=5)
    assert store.get(_CHAT).phase is Phase.DELIVERING
    assert store.get(_CHAT).in_flight is True

    store.reset(_CHAT)
    photos.release.set()
    reply = await task

    assert reply is None
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO
    assert store.get(_CHAT).in_flight is False


async def test_the_photo_bytes_are_handed_over_in_memory_without_a_file() -> None:
    store = SessionStore()
    _ready(store)
    photos = RecordingPhotoSender()

    await run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)

    _, sent_bytes, _ = photos.sent[0]
    assert isinstance(sent_bytes, bytes)
    assert sent_bytes == PORTRAIT_BYTES


async def test_two_chats_each_get_only_their_own_portrait_and_caption() -> None:
    store = SessionStore()
    _ready(store, _CHAT)
    _ready(store, _OTHER)
    photos = RecordingPhotoSender()

    await run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)

    assert [chat for chat, _, _ in photos.sent] == [_CHAT]
    assert store.get(_OTHER).phase is Phase.DOSSIER_READY


# --- /restart while a paid call is still pending -----------------------------


async def test_a_restart_during_a_retry_sends_no_further_model_call() -> None:
    store = SessionStore()
    _ready(store)
    # The first model call fails retryably, and /restart lands while it is in flight.
    portraits = FakePortraitClient(
        [ProviderTimeout(), PORTRAIT_BYTES],
        on_call=lambda: store.reset(_CHAT),
    )
    photos = RecordingPhotoSender()

    reply = await run_generation(_CHAT, store, FakeDownloader(), portraits, photos)

    assert reply is None
    assert portraits.calls == 1
    assert photos.sent == []
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


async def test_a_restart_during_the_photo_download_sends_no_model_call() -> None:
    store = SessionStore()
    _ready(store)
    portraits = FakePortraitClient([PORTRAIT_BYTES])
    photos = RecordingPhotoSender()

    async def download_during_restart(file_id: str) -> bytes:
        store.reset(_CHAT)  # /restart lands while the photo is downloading
        return JPEG_BYTES

    reply = await run_generation(
        _CHAT, store, download_during_restart, portraits, photos
    )

    assert reply is None
    assert portraits.calls == 0
    assert photos.sent == []
    assert store.get(_CHAT).phase is Phase.AWAITING_PHOTO


# --- Outcome logging for a failed run ----------------------------------------


async def _failing_download(file_id: str) -> bytes:
    raise OSError("download broke")


class _FailingPhotoSender:
    """Every send fails, to model a Telegram error during delivery."""

    async def __call__(self, chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        raise ConnectionError("telegram said no")


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("provider_rejected", "ProviderRejected"),
        ("download", "OSError"),
        ("send", "ConnectionError"),
    ],
)
async def test_a_failed_run_logs_outcome_error_with_the_error_type(
    caplog: pytest.LogCaptureFixture, failure: str, error_type: str
) -> None:
    store = SessionStore()
    _ready(store)
    download = _failing_download if failure == "download" else FakeDownloader()
    portraits = FakePortraitClient(
        [ProviderRejected()] if failure == "provider_rejected" else [PORTRAIT_BYTES]
    )
    photos: RecordingPhotoSender | _FailingPhotoSender = (
        _FailingPhotoSender() if failure == "send" else RecordingPhotoSender()
    )

    with caplog.at_level(logging.INFO):
        reply = await run_generation(_CHAT, store, download, portraits, photos)

    assert reply == GENERATION_FAILED_TEXT
    run_ends = [
        r.getMessage()
        for r in caplog.records
        if "stage_end stage=run_generation" in r.getMessage()
    ]
    assert len(run_ends) == 1
    assert "outcome=error" in run_ends[0]
    assert f"error_type={error_type}" in run_ends[0]
    assert "outcome=ok" not in run_ends[0]


# --- Production photo sender --------------------------------------------------


class _FakeBot:
    """Records ``send_photo`` calls; never contacts Telegram."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_photo(self, chat_id: int, photo: BytesIO, caption: str) -> object:
        self.calls.append({"chat_id": chat_id, "photo": photo, "caption": caption})
        return object()


async def test_the_telegram_sender_posts_the_bytes_from_memory_to_the_chat() -> None:
    bot = _FakeBot()
    send = telegram_photo_sender(bot)

    await send(_CHAT, PORTRAIT_BYTES, "Specimen: the Meerkat.\nSecond line.")

    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert call["chat_id"] == 4242
    assert call["caption"] == "Specimen: the Meerkat.\nSecond line."
    photo = call["photo"]
    assert isinstance(photo, BytesIO)
    assert photo.getvalue() == PORTRAIT_BYTES


# --- A superseded run cannot touch a newer run's delivery ---------------------


class _SequencedPhotoSender:
    """Each send waits on its own gate, so a test can finish sends out of order.

    The sends listed in ``fail`` (by call index) raise after their gate opens.
    """

    def __init__(self, count: int, fail: frozenset[int] = frozenset()) -> None:
        self.entered = [asyncio.Event() for _ in range(count)]
        self.release = [asyncio.Event() for _ in range(count)]
        self._fail = fail
        self._index = 0
        self.sent: list[tuple[ChatId, bytes, str]] = []

    async def __call__(self, chat_id: ChatId, image_bytes: bytes, caption: str) -> None:
        index = self._index
        self._index += 1
        self.entered[index].set()
        await self.release[index].wait()
        if index in self._fail:
            raise RuntimeError("telegram said no")
        self.sent.append((chat_id, image_bytes, caption))


async def test_a_failed_send_from_a_reset_run_does_not_roll_back_a_newer_run() -> None:
    store = SessionStore()
    _ready(store)
    photos = _SequencedPhotoSender(count=2, fail=frozenset({0}))
    old_run = asyncio.create_task(
        run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)
    )
    await asyncio.wait_for(photos.entered[0].wait(), timeout=5)

    store.reset(_CHAT)
    _ready(store)
    new_run = asyncio.create_task(
        run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)
    )
    await asyncio.wait_for(photos.entered[1].wait(), timeout=5)
    photos.release[1].set()
    await new_run
    assert store.get(_CHAT).phase is Phase.DELIVERING

    photos.release[0].set()
    reply = await old_run

    assert reply is None
    assert store.get(_CHAT).phase is Phase.DELIVERING
    assert store.get(_CHAT).in_flight is False
    assert [chat for chat, _, _ in photos.sent] == [_CHAT]


async def test_a_finish_from_a_reset_run_keeps_a_newer_runs_in_flight_flag() -> None:
    store = SessionStore()
    _ready(store)
    photos = _SequencedPhotoSender(count=2)
    old_run = asyncio.create_task(
        run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)
    )
    await asyncio.wait_for(photos.entered[0].wait(), timeout=5)

    store.reset(_CHAT)
    _ready(store)
    new_run = asyncio.create_task(
        run_generation(_CHAT, store, FakeDownloader(), FakePortraitClient(), photos)
    )
    await asyncio.wait_for(photos.entered[1].wait(), timeout=5)

    photos.release[0].set()
    await old_run

    # The newer run's send is still in progress, so its in-flight guard must hold.
    state = store.get(_CHAT)
    assert state.phase is Phase.DELIVERING
    assert state.in_flight is True

    photos.release[1].set()
    await new_run
    assert store.get(_CHAT).in_flight is False
