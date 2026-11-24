# Plan — Narrator / Voice Note (Phase 6)

Red/Green TDD throughout. For each task group: write the failing test(s), run
`scripts/test` to confirm **red**, implement, run `scripts/test` to confirm **green**.
Run `scripts/hooks` at the end of each group that changes code.

Use `scripts/test` and `scripts/hooks` only, never raw `pytest`, `ruff`, or `mypy`.
Invoke the `write-tests` skill before writing any tests.

`scripts/hooks` runs `ruff check --fix` and `ruff format`, so it can rewrite files.
Re-run `scripts/test` after it. Confirm `git status` afterwards.

Branch: `feature/2026-11-24-narrator` (already checked out).
Spec folder: `SPECS/2026-11-24-narrator/`.
Do **not** write production code outside the task groups. Do **not** commit.
Do **not** make a live Gemini or Telegram call. Do **not** edit ROADMAP, MISSION, or TECH.

Decisions referenced below (N1 to N18) are in `requirements.md`.

## Task group 0 — Baseline and spike (no production code)

1. Run `scripts/test` and `scripts/hooks` on the untouched branch. Record the test
   count. Both must exit 0 before any change.
2. Confirm `google-adk` pulls in `google-genai`, and that `converter_client.py` already
   imports `google.genai`. Record the installed version from `uv.lock`. Do not add a
   direct dependency yet.
3. **TTS reply format (N4).** Read the installed `google.genai` types (no network) for
   the TTS response: the inline audio part, its MIME type, and any sample-rate parameter.
   Record the exact expected MIME type and sample rate. Record the request-side field
   names for `response_modalities` and the speech voice config.
4. **Encoder (N3).** In a scratch directory under `/tmp/opencode`, check whether
   `lameenc` installs as a wheel for this platform, its licence, and whether it encodes
   the expected PCM to MP3. Check whether `ffmpeg` is present. Record the result. Do not
   edit `pyproject.toml`. The Tech Lead approves N3 before group 2.
5. Confirm `gemini-3.1-flash-lite` is defined once (`_FLASH_LITE` in `config.py`), and
   read `vision_client.py` (`map_provider_error`, `RETRY_BACKOFF_SECONDS`) and the Phase 5
   `scripter_client.py` seam. The Narrator client follows that pattern. Record any
   deviation.
6. Confirm the Phase 5 script step location (`generation.py`, `_run_script_step`) and
   where `run_generation` calls it. The narration step is wired after it.
7. Record the open items in `requirements.md` that the spike answers (Open decisions 1
   and 2). Stop and report if the TTS reply is not raw PCM, or if the encoder is not
   acceptable. Do not proceed to group 2 without an answer.

**Acceptance:** baseline green; the TTS reply format and the encoder choice are recorded
with evidence; no production code changed; no dependency changed; no live call made.

## Task group 1 — Config, prompt, and copy (RED first)

1. Extend `tests/test_config.py` (RED):
   - `MODELS["narrator"] == "gemini-3.1-flash-tts-preview"`;
   - the bouncer, interviewer, and scripter entries are unchanged;
   - `NARRATOR_VOICE` is a non-empty string defined in `config.py`;
   - the TTS model ID appears exactly once in `config.py`.
2. Extend `tests/test_prompts.py` (RED): `NARRATOR_INSTRUCTION` is non-empty; contains
   "speak with a posh British accent"; describes a deep male voice and an
   Attenborough-style delivery; contains no caption or Telegram instruction.
3. Extend `tests/test_messages.py` (RED):
   - `NARRATION_UNAVAILABLE_TEXT` is non-empty, names `/restart`, says the portrait and
     script stand, and contains no code, error-type, provider, or stack-trace wording
     (assert a forbidden-substring set, as Phases 4 and 5 do);
   - `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused, not
     redefined.
4. Run `scripts/test` → red.
5. Implement:
   - `config.py`: `"narrator": "gemini-3.1-flash-tts-preview"` (as a named constant, not
     a second literal elsewhere), and `NARRATOR_VOICE` (the voice chosen in the spike,
     N7);
   - `prompts.py`: `NARRATOR_INSTRUCTION` (N8);
   - `messages.py`: `NARRATION_UNAVAILABLE_TEXT` (N15).
6. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the TTS model ID is defined once; the voice name is in config only; the
prompt lives in `prompts.py` only; the copy is checked for forbidden wording.

## Task group 2 — Audio contracts and encoder seam (RED first)

Starts only after the Tech Lead approves N3 (or confirms that no encoder is needed).

1. Add the approved encoder with `uv add` (only if N3 requires it). Confirm `uv sync
   --locked` and the lockfile diff. This is the only dependency change in the phase.
2. Add a small fixture, `tests/fixtures/narrator_tone.pcm` (about 1 KB, at the expected
   sample rate and format from group 0). Commit nothing; it is a repo file for tests
   only, and the verifier checks its size.
3. Write `tests/test_narrator_models.py` (RED):
   - `TtsReply` accepts the expected MIME type and sample rate with non-empty bytes;
   - empty bytes → `empty_audio`; bytes over `MAX_PCM_BYTES` → `oversize_audio`;
     a wrong MIME type or a wrong sample rate → `unexpected_format`;
   - extra fields are rejected;
   - `VoiceNote` accepts bytes that start with `ID3` and bytes that start with `0xFF 0xFB`,
     `0xFF 0xF3`, or `0xFF 0xF2`; rejects other starts with `bad_mp3`; rejects empty
     bytes (`empty_audio`) and oversize bytes (`oversize_audio`);
   - the error text carries the reason code and the byte count, and never the bytes
     (assert a sentinel byte string is absent from `str(error)`).
4. Write `tests/test_narrator_audio.py` (RED):
   - `encode_mp3` is called through an injected encoder seam in every test except one;
     the fake records the call and returns a fixed valid MP3 header;
   - the one fixture test runs the real encoder on `narrator_tone.pcm`, and asserts the
     output passes `VoiceNote` (an `ID3` or MPEG frame-sync start). It is the only test
     that does real encoding;
   - an encoder exception becomes `bad_mp3` with no raw message in the error;
   - the module does not import `google.genai` or ADK (checked by source scan).
5. Run `scripts/test` → red.
6. Implement:
   - `narrator_models.py`: `TtsReply`, `VoiceNote` (Pydantic v2, `extra="forbid"`,
     strict), the constants `EXPECTED_PCM_MIME`, `EXPECTED_SAMPLE_RATE_HZ`,
     `MAX_PCM_BYTES`, `MAX_MP3_BYTES` (N4, N5, N6), and the reason-code constants;
   - `narrator_audio.py`: `encode_mp3(reply: TtsReply) -> VoiceNote` with the encoder
     library imported only here.
7. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** every malformed audio case is rejected at construction; the encoder
import lives in one module; the single real-encoding test passes on the small fixture.

## Task group 3 — Session v5 and narration driver methods (RED first)

1. Extend `tests/test_state.py` (RED):
   - `STATE_VERSION == 5`; a stored version `4` is discarded on read and a fresh
     `AWAITING_PHOTO` session starts;
   - `SessionState.narration_started` defaults to `False`;
   - `begin_narration(chat_id, epoch)` works only from `DELIVERING` with `script` set,
     `narration_started is False`, `in_flight is False`, and a matching epoch. It sets
     `narration_started = True` and `in_flight = True` (N10). It returns `False` for a
     stale epoch, a second call in the same run, a second call after a failure, a
     missing script, or any other phase;
   - `end_narration(chat_id, epoch)` clears `in_flight` only, with a matching epoch;
     returns `False` and changes nothing for a stale epoch; leaves `narration_started`
     `True`;
   - invariant: `narration_started` is `True` only in `DELIVERING` with `script` set;
     a violation raises `StateError` at the next commit;
   - `reset` from each of the seven phases clears `narration_started` and `in_flight`,
     and returns to `AWAITING_PHOTO`;
   - two chats keep independent `narration_started` values; a reset in one leaves the
     other's state intact.
2. Run `scripts/test` → red.
3. Implement in `state.py`:
   - `STATE_VERSION = 5`; `SessionState.narration_started: bool = False`;
   - `begin_narration` and `end_narration` (N10, N11), each taking the epoch and checking
     it with the existing `SessionStore.epoch`;
   - `_check_invariants` extended for `narration_started`;
   - the module docstring updated.
   Stages do not write fields.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the driver is the only writer of `narration_started`; a stale epoch
cannot change a newer state; a second synthesis in one run is refused by state; version 4
states are discarded.

## Task group 4 — Narration client seam (RED first)

1. Write `tests/test_narrator_client.py` (RED), with an injected fake transport and no
   `google.genai` import in the test module:
   - the `NarrationClient` protocol is satisfied by a fake that returns a `TtsReply`;
   - `GeminiNarrationClient` construction does not import `google.genai` and does not
     touch the network (checked in a subprocess or with a guard, as Phases 3 to 5 do);
   - `NARRATION_TIMEOUT_SECONDS == 30.0`; a transport timeout maps to `ProviderTimeout`;
   - status mapping: 429 → `RateLimited`; 500, 502, 503 → `VisionUnavailable`; 400, 401,
     403, 404 → `ProviderRejected`; unknown → `VisionUnavailable`;
   - a reply with no audio part → `InvalidResponse`;
   - the provider message is never copied into the typed error text (sentinel string);
   - the request uses `MODELS["narrator"]`, the voice name passed in, audio-only output,
     and the instruction followed by the script text (assert on what the fake transport
     receives);
   - the request contains no dossier fields and no photo bytes (the client takes only
     text and voice).
2. Run `scripts/test` → red.
3. Implement `narrator_client.py`:
   - `NarrationClient` protocol: `async def synthesise(text, *, voice) -> TtsReply`;
   - `NARRATION_TIMEOUT_SECONDS = 30.0`;
   - `GeminiNarrationClient`, the only Phase 6 module that imports `google.genai` (the
     import is lazy, inside the methods or under `TYPE_CHECKING`, as `vision_client.py`
     does);
   - provider error mapping through `map_provider_error` (`vision_client.py`) if its
     signature allows; otherwise the shared helper from Phase 5. The table is not
     duplicated;
   - the reply's audio part is converted into `TtsReply` with its MIME type and sample
     rate.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** tests never import `google.genai` or open sockets; the client is the only
Phase 6 module that imports `google.genai`; the status table is not duplicated.

## Task group 5 — Narrator tool and attempt budget (RED first)

1. Write `tests/test_narrator.py` (RED) with a fake `NarrationClient`, a fake
   `still_current` callable, and a patched `asyncio.sleep`:
   - **Happy path:** one valid reply → one call → one `TtsReply`; attempt count 1;
   - **Retry classes:** timeout, 429, and 5xx on attempt 1 → attempt 2 after
     `RETRY_BACKOFF_SECONDS`, valid reply → `TtsReply`; the same error on both attempts
     → attempt count exactly 2, the last typed error raised;
   - **Fail fast:** `ProviderRejected` → attempt count 1, error re-raised;
   - **No retry on malformed audio:** empty, oversize, wrong format, and no-audio replies
     → attempt count 1, the reason code raised, no backoff;
   - **Paid-call guard:** `still_current()` returns `False` before attempt 2 → no second
     call; a stale reply is not validated;
   - **Paid-call guard, before attempt 1:** `still_current()` is `False` at the start →
     no call at all;
   - **Backoff:** a plain provider retry waits `RETRY_BACKOFF_SECONDS`; no backoff
     anywhere else;
   - **Input:** the client receives the script text and the voice name only;
   - **Logging:** a failure logs the error type or reason code, the attempt number, and
     the byte count at `error`, and never the audio or the script text (sentinel values).
2. Run `scripts/test` → red.
3. Implement `narrator.py`:
   - `@log_stage async def generate_narration(text, client, *, still_current) -> TtsReply`;
   - the N1 budget of 2 attempts, with no repair;
   - the N12 guard before every attempt;
   - backoff on plain provider retries only;
   - malformed replies raise `InvalidResponse` with the reason code and byte count;
   - `ProviderRejected` is re-raised at once.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** total synthesis attempts per run never exceed 2; no call is made after a
reset; the tool never calls Telegram and never writes to the session.

## Task group 6 — Narration step: temp file, send, cleanup, fallback (RED first)

1. Write `tests/test_narration_step.py` (RED) with the real `SessionStore`, a fake
   `NarrationClient`, a fake encoder seam, and a fake Telegram voice sender that records
   the chat id, the temp path, the bytes it reads, and the order of sends:
   - **Happy path:** one synthesis, one encode, one temp file written under
     `tempfile.gettempdir()`, one `send_voice` with the voice note bytes and no caption,
     temp file removed, `end_narration` called, `narration_started` `True`, `in_flight`
     `False`;
   - **Temp location:** the temp path is not under the repo root (assert with the
     resolved repo path);
   - **Cleanup on success, send failure, encoder failure, and synthesis failure:** the
     temp file does not exist afterwards in each case;
   - **Removal failure:** removing the temp file raises (patched) → logged at `error`;
     the step completes; no exception escapes;
   - **Voice send failure (N14):** `send_voice` raises → exactly one `send_voice` call,
     one `NARRATION_UNAVAILABLE_TEXT`, temp file removed, `end_narration` called;
   - **Fallback copy (N15):** synthesis fails after the budget → one
     `NARRATION_UNAVAILABLE_TEXT`; no voice note; `script` kept; phase `DELIVERING`;
   - **Temp write failure:** the write raises → `NARRATION_UNAVAILABLE_TEXT`; nothing left
     behind;
   - **Stale epoch before send (N11):** the epoch changes after encode → no `send_voice`,
     temp file removed, `info` log, no state change;
   - **`/restart` mid-synthesis:** the fake client blocks; `/restart` → reset and
     `OPENING_PROMPT`; releasing the call drops the result (no send, no state change, an
     `info` log);
   - **`/restart` between encode and send:** no `send_voice` after the reset;
   - **Script absent:** `script is None` → `begin_narration` refuses; no synthesis;
   - **Script text send failed (N13):** the text send raises; narration still runs once;
     the voice note is sent;
   - **Input (N8):** the synthesis client receives the script text and the voice name
     only; no dossier, no photo, no answers.
2. Run `scripts/test` → red. Record any existing test whose expected behaviour changes
   (for example, a Phase 5 test that asserted that nothing is sent after the script
   text). Update each one red first and list it in the verifier note.
3. Implement the narration step (in `generation.py`, next to `_run_script_step`, or in a
   new `narration_step.py` called from it, following the Phase 5 pattern):
   - `run_narration_step(chat_id, store, script, client, send_voice, *, epoch) -> None`,
     following the Flow in `requirements.md`;
   - the temp file: `tempfile.mkstemp(suffix=".mp3")`, written once, closed, passed to the
     sender, and removed in `finally` (N16);
   - the send uses an injected `VoiceSender` protocol, so no test touches Telegram. The
     send passes no caption and no `parse_mode` (N17);
   - `@log_stage` on the stage entry; the step logs its own outcome line;
   - wire in `generation.py`: the narration step runs after the script step, with no
     `await` between `begin_narration` and the script step's end. The broad
     `except Exception` logs at `error`, never escapes, and calls `end_narration` in
     `finally` if the step is still in flight.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** exactly one synthesis and at most one voice note per run; the temp file
never outlives the step; every failure returns the chat to `DELIVERING` with the script
text kept; `/restart` drops a late result; duplicates are nudged, not generated.

## Task group 7 — Routing, ordering, and isolation (RED first)

1. Extend tests (RED), using the full pipeline with fakes at every seam:
   - **Full run order:** portrait, then script text, then voice note; the assertion is on
     the send log (method and order);
   - **Duplicates during each step:** a text or photo during the synthesis and during
     `send_voice` → `ONE_AT_A_TIME_TEXT`; after the run → `ALREADY_DONE_TEXT`; no second
     synthesis, no second voice note;
   - **Cross-chat isolation:** chat A completes narration while chat B is mid-interview;
     each chat's voice note goes only to that chat; `/restart` in A leaves B intact;
   - **Restart at every narration point:** before synthesis, during synthesis, during
     encode, during write, during send → the chat returns to `OPENING_PROMPT` and no
     voice note arrives after the reset;
   - **Wrong payload after narration:** a sticker, a voice note from the user, and a
     document in `DELIVERING` after narration → `ALREADY_DONE_TEXT`; no state change.
2. Run `scripts/test` → red.
3. Implement the minimum routing change needed to make these pass. `handle_restart` is
   unchanged except for what the reset clears.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the three messages arrive in the required order; no duplicate or
post-reset voice note is possible in any tested interleaving.

## Task group 8 — Logging and safety sweep (RED first)

1. Extend tests (RED) with `caplog`:
   - a full run logs chat id, phase transitions, attempt numbers, outcomes, durations,
     error types, and byte counts;
   - the same run never logs the script text (sentinel), the audio bytes (sentinel bytes),
     the base64 of any audio, the prompt, the dossier, the answers, the provider body,
     tokens, or keys;
   - a failed narration logs the typed error name or reason code at `error` and never the
     provider message;
   - a stale-epoch drop logs at `info` with no text and no bytes;
   - `@log_stage` records entry, outcome, and duration at the new boundaries.
2. Run `scripts/test` → red.
3. Implement: apply `@log_stage` and the field allow-list on the new boundaries. Make sure
   nothing logs `TtsReply`, `VoiceNote`, the raw reply, the script text, or the temp path.
4. Run `scripts/test` → green. Run `scripts/hooks`.
5. Source grep over the changed modules: no bare `except`, no `except Exception: pass`,
   no recovered error logged below `error`, no `write_bytes` outside `narration_step`,
   every `mkstemp` is inside the narration step with a `finally` removal, no
   `parse_mode` on the voice send, no temp path under the repo root, no `google.genai`
   import outside `narrator_client.py`, no encoder import outside `narrator_audio.py`.

**Acceptance:** no forbidden content in any log record at any level; the temp file is the
only file I/O in the change, and it is always removed.

## Task group 9 — Docs, checks, and hygiene

1. Update `README.md`: the voice note as step 3 after the portrait and the script text;
   MP3 format (with the note that the roadmap said OGG/Opus); the temp file rule (system
   temp dir, deleted after send); `NARRATION_UNAVAILABLE_TEXT` and its `/restart`
   pointer; `/restart` during narration; the resting `DELIVERING` phase. Keep setup, run,
   and dev-script sections accurate. No credentials in docs.
2. Run `scripts/test` → clean.
3. Run `scripts/hooks` → clean. Re-run `scripts/test` afterwards and confirm the tree is
   unchanged.
4. Confirm `git status` shows no `.env`, `.venv`, caches, media, or temp files. Confirm
   the dependency diff is empty or is exactly the approved encoder (`git diff --stat` on
   `pyproject.toml` and `uv.lock`). Confirm the fixture is the only new binary file.
5. Do **not** edit `SPECS/ROADMAP.md`, `SPECS/MISSION.md`, or `SPECS/TECH.md`. List the
   OGG/Opus wording that the verifier must amend (requirements.md, roadmap deviation).
   Do **not** flip the Phase 6 status. Do **not** commit.
6. Report what changed, for the validation checklist.

**Acceptance:** both dev scripts green; docs match the behaviour; hygiene clean; nothing
committed.

## Verification handoff

When this plan is complete, hand off to the verifier with the spec folder path
(`SPECS/2026-11-24-narrator/`). The verifier checks `validation.md`. Live Telegram and
Gemini checks are deferred until real credentials are supplied. Offline validation must
be fully green. The ROADMAP Phase 6 status, and the OGG/Opus wording in ROADMAP, MISSION,
and TECH, are updated by the verifier, not by this plan.
