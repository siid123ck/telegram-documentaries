"""Logging and safety tests for the Phase 4 generation path (task group 7).

Each test plants sentinel strings in the fakes (answers, quirks, reasons, the
animal's name is allowed, the portrait bytes, the photo bytes, and a provider
message) and runs the real router end to end. The captured log text must carry
the operational fields and must never carry the sentinels, or the base64 of any
image bytes. A final static guard checks the Phase 4 source for forbidden
constructs.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes import (
    JPEG_BYTES,
    FakeDownloader,
    FakeInterviewClient,
    FakePortraitClient,
    FakeVisionClient,
    RecordingPhotoSender,
    RecordingSender,
    ask,
    dossier,
    finish,
    photo_update,
    text_update,
    verdict,
)

import telegram_documentaries
from telegram_documentaries.converter_client import AdkPortraitClient
from telegram_documentaries.converter_models import PNG_MAGIC
from telegram_documentaries.errors import (
    InvalidResponse,
    ProviderRejected,
    ProviderTimeout,
    RateLimited,
    VisionError,
    VisionUnavailable,
)
from telegram_documentaries.generation import telegram_photo_sender
from telegram_documentaries.interview_models import Dossier
from telegram_documentaries.models import as_chat_id
from telegram_documentaries.prompts import CONVERTER_INSTRUCTION
from telegram_documentaries.routing import UpdateRouter
from telegram_documentaries.state import Phase, SessionStore

SENTINEL_ANSWER = "SENTINEL-ANSWER-7f3a"
SENTINEL_QUIRK = "SENTINEL-QUIRK-9c1d"
SENTINEL_REASON = "SENTINEL-REASON-2b8e"
SENTINEL_QUESTION = "SENTINEL-QUESTION-4e6f"
SENTINEL_PROVIDER = "SENTINEL-PROVIDER-MESSAGE-0a5b"
SENTINEL_PHOTO = b"SENTINEL-PHOTO-BYTES-3c7d"
SENTINEL_PORTRAIT = b"SENTINEL-PORTRAIT-BYTES-6e1f"
ANIMAL = "Meerkat"
_CHAT = as_chat_id(4242)

#: Upper bound on loop yields while waiting for a flow step, so a bug fails fast.
_MAX_YIELDS = 10_000


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


def _portrait_bytes() -> bytes:
    return PNG_MAGIC + SENTINEL_PORTRAIT


def _router(
    portraits: FakePortraitClient,
    *,
    photos: RecordingPhotoSender | None = None,
) -> tuple[UpdateRouter, RecordingSender, RecordingPhotoSender, SessionStore]:
    asks = [ask(f"{SENTINEL_QUESTION} {index}?") for index in range(1, 6)]
    interview = FakeInterviewClient([*asks, finish()])
    store = SessionStore()
    sender = RecordingSender()
    photos = photos if photos is not None else RecordingPhotoSender()
    router = UpdateRouter(
        vision=FakeVisionClient(result=verdict(human=True)),
        interview=interview,
        store=store,
        download=FakeDownloader(data=b"\xff\xd8\xff" + SENTINEL_PHOTO),
        portraits=portraits,
        send_photo=photos,
        reply=sender,
    )
    return router, sender, photos, store


async def _run_full_interview(router: UpdateRouter) -> None:
    await router.handle_photo(photo_update("p-1", update_id=1))
    for index in range(1, 6):
        await router.handle_text(
            text_update(f"{SENTINEL_ANSWER} {index}", update_id=index + 1)
        )


async def _until(condition: Callable[[], bool]) -> None:
    for _ in range(_MAX_YIELDS):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the expected step in the flow never happened")


def _every_forbidden_fragment(portrait: bytes) -> list[str]:
    """Strings that must never appear in any log record at any level."""
    return [
        SENTINEL_ANSWER,
        SENTINEL_QUIRK,
        SENTINEL_REASON,
        SENTINEL_QUESTION,
        SENTINEL_PHOTO.decode(),
        SENTINEL_PORTRAIT.decode(),
        base64.b64encode(portrait).decode(),
        base64.b64encode(SENTINEL_PHOTO).decode(),
        CONVERTER_INSTRUCTION.strip().splitlines()[0],
    ]


# --- A full run: what is logged ------------------------------------------------


async def test_a_full_run_logs_the_chat_phases_outcome_and_animal_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _, _ = _router(FakePortraitClient([_portrait_bytes()]))

    with caplog.at_level(logging.INFO):
        await _run_full_interview(router)

    text = caplog.text
    assert "chat_id=4242" in text
    assert "phase_before=generating phase_after=delivering" in text
    assert f"animal={ANIMAL}" in text
    assert "portrait_delivered" in text


async def test_each_new_boundary_records_entry_outcome_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _, _ = _router(FakePortraitClient([_portrait_bytes()]))

    with caplog.at_level(logging.INFO):
        await _run_full_interview(router)

    text = caplog.text
    for stage in ("run_generation", "generate_portrait"):
        assert f"stage_start stage={stage}" in text
        assert f"stage_end stage={stage} outcome=ok" in text
    assert "duration_ms=" in text


async def test_a_full_run_never_logs_text_bytes_or_their_base64(
    caplog: pytest.LogCaptureFixture,
) -> None:
    portrait = _portrait_bytes()
    router, _, _, _ = _router(FakePortraitClient([portrait]))

    with caplog.at_level(logging.DEBUG):
        await _run_full_interview(router)

    for fragment in _every_forbidden_fragment(portrait):
        assert fragment not in caplog.text


# --- Failures: the error type at error, never the provider message -------------


async def test_a_failed_run_logs_the_error_type_and_never_the_provider_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rejected = ProviderRejected(SENTINEL_PROVIDER)
    router, _, _, _ = _router(FakePortraitClient([rejected]))

    with caplog.at_level(logging.DEBUG):
        await _run_full_interview(router)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("error_type=ProviderRejected" in r.getMessage() for r in errors)
    assert SENTINEL_PROVIDER not in caplog.text


async def test_a_telegram_send_failure_is_logged_at_error_without_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing = RecordingPhotoSender(error=RuntimeError(SENTINEL_PROVIDER))
    portraits = FakePortraitClient([_portrait_bytes()])
    router, _, _, store = _router(portraits, photos=failing)

    with caplog.at_level(logging.DEBUG):
        await _run_full_interview(router)

    assert any(
        r.levelno == logging.ERROR and "portrait_send_failed" in r.getMessage()
        for r in caplog.records
    )
    assert SENTINEL_PROVIDER not in caplog.text
    assert store.get(_CHAT).phase is Phase.DOSSIER_READY


# --- A stale result: info, with no bytes ---------------------------------------


async def _run_partial_interview(router: UpdateRouter) -> None:
    """Photo and four answers, leaving the fifth answer to finish the dossier."""
    await router.handle_photo(photo_update("p-1", update_id=1))
    for index in range(1, 5):
        await router.handle_text(
            text_update(f"{SENTINEL_ANSWER} {index}", update_id=index + 1)
        )


async def test_a_dropped_stale_result_is_logged_at_info_with_no_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = asyncio.Event()
    portrait = _portrait_bytes()
    portraits = FakePortraitClient([portrait], gate=gate)
    router, _, photos, _ = _router(portraits)
    await _run_partial_interview(router)
    pending = asyncio.create_task(
        router.handle_text(text_update(f"{SENTINEL_ANSWER} 5", update_id=40))
    )
    await _until(lambda: portraits.calls > 0)

    with caplog.at_level(logging.INFO):
        await router.handle_restart(text_update("/restart", update_id=41))
        gate.set()
        await pending

    assert photos.sent == []
    dropped = [r for r in caplog.records if "stale_run" in r.getMessage()]
    assert dropped, "a dropped stale result must be logged"
    assert all(r.levelno == logging.INFO for r in dropped)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    for fragment in _every_forbidden_fragment(portrait):
        assert fragment not in caplog.text


# --- Static guard over the Phase 4 source --------------------------------------

_PHASE_4_MODULES = (
    "converter.py",
    "converter_client.py",
    "converter_models.py",
    "generation.py",
    "routing.py",
    "interview.py",
    "photo_handler.py",
    "state.py",
    "messages.py",
    "prompts.py",
    "app.py",
)

_FORBIDDEN_IN_PHASE_4 = (
    "tempfile",
    "write_bytes",
    "except:",
    "except Exception:\n        pass",
)


@pytest.mark.parametrize("module", _PHASE_4_MODULES)
def test_the_phase_4_source_has_no_temp_files_or_bare_excepts(module: str) -> None:
    source_dir = Path(telegram_documentaries.__file__).parent
    text = (source_dir / module).read_text(encoding="utf-8")

    for fragment in _FORBIDDEN_IN_PHASE_4:
        assert fragment not in text, f"{module} contains {fragment!r}"


def test_no_phase_4_module_opens_a_file_for_media() -> None:
    source_dir = Path(telegram_documentaries.__file__).parent
    for module in ("converter.py", "converter_client.py", "generation.py"):
        text = (source_dir / module).read_text(encoding="utf-8")
        assert "open(" not in text, f"{module} must not open files for media"


# --- Group 7 sweep: the provider and Telegram boundaries are logged stages ----

CREDENTIAL_MARKER = "credential-marker-not-a-secret"


async def test_the_provider_call_is_a_logged_stage_with_outcome_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated = PNG_MAGIC + b"generated"

    async def transport(**_: object) -> bytes:
        return generated

    client = AdkPortraitClient(api_key=CREDENTIAL_MARKER, transport=transport)

    with caplog.at_level(logging.INFO):
        assert await client.render(JPEG_BYTES, dossier()) == generated

    text = caplog.text
    assert "stage_start stage=AdkPortraitClient.render" in text
    assert "stage_end stage=AdkPortraitClient.render outcome=ok" in text
    assert "duration_ms=" in text
    assert CREDENTIAL_MARKER not in text
    assert base64.b64encode(generated).decode() not in text


async def test_a_failed_provider_call_logs_the_stage_error_never_the_key_or_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def transport(**_: object) -> bytes:
        raise RuntimeError(f"invalid key {CREDENTIAL_MARKER} {SENTINEL_PROVIDER}")

    client = AdkPortraitClient(api_key=CREDENTIAL_MARKER, transport=transport)

    with caplog.at_level(logging.DEBUG), pytest.raises(VisionError):
        await client.render(JPEG_BYTES, dossier())

    text = caplog.text
    assert (
        "stage_error stage=AdkPortraitClient.render error_type=VisionUnavailable"
        in text
    )
    assert CREDENTIAL_MARKER not in text
    assert SENTINEL_PROVIDER not in text


class _PhotoBot:
    """A stand-in for ``telegram.Bot`` that records or fails the photo send."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def send_photo(self, chat_id: int, photo: object, caption: str) -> object:
        if self._error is not None:
            raise self._error
        return object()


async def test_a_photo_send_is_a_logged_stage_that_never_logs_the_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    send = telegram_photo_sender(_PhotoBot())

    with caplog.at_level(logging.INFO):
        await send(_CHAT, _portrait_bytes(), "Specimen: the Meerkat.\nSecond line.")

    text = caplog.text
    assert "stage_start stage=_send_portrait" in text
    assert "stage_end stage=_send_portrait outcome=ok" in text
    assert SENTINEL_PORTRAIT.decode() not in text
    assert base64.b64encode(_portrait_bytes()).decode() not in text


async def test_a_failed_photo_send_logs_the_stage_error_without_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    send = telegram_photo_sender(_PhotoBot(error=RuntimeError(SENTINEL_PROVIDER)))

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        await send(_CHAT, _portrait_bytes(), "Specimen: the Meerkat.\nSecond line.")

    text = caplog.text
    assert "stage_error stage=_send_portrait error_type=RuntimeError" in text
    assert SENTINEL_PROVIDER not in text


@pytest.mark.parametrize(
    "failure",
    [
        ProviderTimeout(),
        RateLimited(),
        VisionUnavailable(),
        ProviderRejected(SENTINEL_PROVIDER),
        InvalidResponse(),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_each_failure_class_logs_its_error_type_at_error_level(
    failure: VisionError,
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _, _ = _router(FakePortraitClient([failure, failure]))

    with caplog.at_level(logging.DEBUG):
        await _run_full_interview(router)

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(f"error_type={type(failure).__name__}" in m for m in errors)
    assert SENTINEL_PROVIDER not in caplog.text


# --- Group 7 sweep: source-wide static checks ----------------------------------

_SOURCE_DIR = Path(telegram_documentaries.__file__).parent
_LOGGER_NAMES = frozenset({"logger", "module_logger"})
_BELOW_ERROR = frozenset({"debug", "info", "warning"})
#: Names that must never be passed to a log call (secrets, media, user text).
_FORBIDDEN_LOG_NAMES = frozenset(
    {
        "api_key",
        "token",
        "secret",
        "image_bytes",
        "photo_bytes",
        "portrait_bytes",
        "raw",
        "data",
        "text",
        "answer",
        "answers",
        "caption",
        "prompt",
        "request_text",
        "instruction",
        "exc",
    }
)
#: Attributes that must never be passed to a log call. ``dossier.suggested_animal
#: .name`` is allowed, because the animal name is the one value the logs carry.
_FORBIDDEN_LOG_ATTRIBUTES = frozenset(
    {
        "api_key",
        "token",
        "secret",
        "image_bytes",
        "photo_bytes",
        "data",
        "text",
        "answer",
        "observation",
        "reason",
        "prompt",
    }
)
_SAFE_EXCEPTION_CALLS = frozenset({"type", "_http_status"})
_FORBIDDEN_CALLS = frozenset({"open"})
_FORBIDDEN_IMPORTS = frozenset({"tempfile"})
#: The only source module allowed to import ``tempfile`` (its temp-file staging).
_TEMPFILE_EXEMPT_FILES = frozenset({"narrator_sender.py"})
_FORBIDDEN_ATTRIBUTE_CALLS = frozenset({"write_bytes", "write_text"})


def _is_silent_broad_handler(handler: ast.ExceptHandler) -> bool:
    """True for a broad except (Exception or BaseException) whose body is pass."""
    broad = isinstance(handler.type, ast.Name) and handler.type.id in {
        "Exception",
        "BaseException",
    }
    silent = all(
        isinstance(statement, ast.Pass)
        or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis
        )
        for statement in handler.body
    )
    return broad and silent


def _log_call_violations(call: ast.Call, level: str) -> list[str]:
    """Forbidden content in one logger call, as human-readable findings."""
    found: list[str] = []
    # ``exc`` may reach a log call only through a call that yields a safe value:
    # ``type(exc).__name__`` (the class name) or ``_http_status(exc)`` (an int).
    exempt = {
        id(argument)
        for inner in ast.walk(call)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id in _SAFE_EXCEPTION_CALLS
        for argument in inner.args
    }
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    for argument in arguments:
        for node in ast.walk(argument):
            if id(node) in exempt:
                continue
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_LOG_NAMES:
                found.append(f"logs name {node.id!r}")
            if isinstance(node, ast.Attribute) and (
                node.attr in _FORBIDDEN_LOG_ATTRIBUTES
            ):
                found.append(f"logs attribute {node.attr!r}")
            if (
                level in _BELOW_ERROR
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "error_type" in node.value
            ):
                found.append("logs a recovered error below error level")
    return found


def _sweep_source(path: Path) -> list[str]:
    """Return every forbidden construct in one module, as ``file:line`` findings."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    where = path.name
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append(f"{where}:{node.lineno} bare except")
            elif _is_silent_broad_handler(node):
                findings.append(f"{where}:{node.lineno} broad except that passes")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if (
                any(name.split(".")[0] in _FORBIDDEN_IMPORTS for name in names)
                and where not in _TEMPFILE_EXEMPT_FILES
            ):
                findings.append(f"{where}:{node.lineno} imports a temp-file module")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                findings.append(f"{where}:{node.lineno} calls {func.id}()")
            if isinstance(func, ast.Attribute) and (
                func.attr in _FORBIDDEN_ATTRIBUTE_CALLS
            ):
                findings.append(f"{where}:{node.lineno} calls .{func.attr}()")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in _LOGGER_NAMES
            ):
                findings.extend(
                    f"{where}:{node.lineno} {finding}"
                    for finding in _log_call_violations(node, func.attr)
                )
    return findings


def test_the_source_has_no_bare_excepts_silent_catches_or_media_file_io() -> None:
    findings: list[str] = []
    for path in sorted(_SOURCE_DIR.glob("*.py")):
        findings.extend(_sweep_source(path))

    assert findings == []
    # The tempfile exemption covers exactly one module, so nothing else can slip past.
    assert frozenset({"narrator_sender.py"}) == _TEMPFILE_EXEMPT_FILES


def test_the_sweep_itself_catches_each_forbidden_construct(tmp_path: Path) -> None:
    """The static checks must fail on known-bad code, or they prove nothing."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import tempfile\n"
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n    pass\nexcept:\n    pass\n"
        "try:\n    pass\nexcept Exception:\n    pass\n"
        "open('x.png', 'wb')\n"
        "logger.info('got %s', api_key)\n"
        "logger.error('failed %s', exc)\n"
        "logger.info('recovered error_type=%s', type(exc).__name__)\n"
        "logger.error('ok error_type=%s', type(exc).__name__)\n",
        encoding="utf-8",
    )

    findings = _sweep_source(bad)

    assert any("bare except" in f for f in findings)
    assert any("broad except that passes" in f for f in findings)
    assert any("temp-file" in f for f in findings)
    assert any("calls open()" in f for f in findings)
    assert any("logs name 'api_key'" in f for f in findings)
    assert any("logs name 'exc'" in f for f in findings)
    assert any("recovered error below error" in f for f in findings)
    # The safe form, ``type(exc).__name__`` at error level, is not a finding.
    assert len([f for f in findings if "logs name 'exc'" in f]) == 1
