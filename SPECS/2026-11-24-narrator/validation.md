# Validation — Narrator / Voice Note (Phase 6)

Work top to bottom. Every step is checkable and produces an observable result.
"Mergeable" means every box is ticked, or the exception is explicitly accepted by the
Tech Lead. Offline validation must be complete and green regardless of the
live-credential deferral. Do not claim a live pass that was not run.

**Verification status: re-checked 2026-11-24, offline. NOT MERGEABLE yet.** V-1, V-2, and
V-3 are confirmed fixed. Still open, and not closable offline: V-4 (red-run evidence,
Tech Lead decisions and the N3 approval), V-5 (no group 0 spike record), and V-6 (the
provisional voice). Marks: `[x]` pass · `[ ]` open (the tag says why) · `DEFERRED` live
check, not run.

Offline results (re-check 2026-11-24, after the V-1 to V-3 fixes): `scripts/test` exit 0
(986 passed, about 23 s); `scripts/hooks` exit 0 (about 23 s, no tree change); `scripts/test`
re-run after hooks exit 0 (986 passed). The 978-test scratch-copy run from the first pass predates the fixes. Nothing was committed or pushed. No live call was made.

## A. Environment and dependencies

- [x] On `feature/2026-11-24-narrator`, `uv sync --locked` completes.
- [ ] Dependency change is at most the encoder approved in N3. `git diff main -- *(open: diff is `lameenc` only (pyproject +1, uv.lock +87), but no N3 approval is recorded (V-4))*
      pyproject.toml uv.lock` shows nothing else. If no encoder was approved, the diff is
      empty.
- [x] `gemini-3.1-flash-tts-preview` is defined once in `src/` (in `config.py`).
      `MODELS["narrator"]` refers to it.
- [x] `NARRATOR_VOICE` is defined once, in `config.py`. It is not a literal in any other *(value `Charon` is provisional (N7))*
      module.
- [x] `scripts/test` and `scripts/hooks` exist and run.
- [ ] The group 0 spike is recorded: the TTS reply MIME type and sample rate, and the *(open: no spike record in SPECS; the MIME type and sample rate are unconfirmed against a live reply (V-5))*
      encoder decision (N3), with evidence.

## B. Tests are ground truth (Red/Green evidence)

- [x] `scripts/test` exits `0` and visibly runs ruff check, ruff format --check, mypy, *(78 ruff/format/mypy/pytest lines; 978 passed, 27 s)*
      and pytest.
- [x] `scripts/test` fails when deliberately broken. Inject an unused import into a *(scratch copy: F401 on an unused import, exit 1; real tree untouched)*
      scratch copy; it fails with `F401` and exit 1. The real tree is not touched.
- [ ] Each new test file was seen red before its implementation. Keep the red run output *(open: no red-run output recorded (V-4))*
      in the verifier note (commit history is not available while uncommitted).
- [ ] Any existing test whose expected behaviour changed (group 6, step 2) is listed by *(changed by name: `tests/test_state.py::test_the_schema_version_is_four` renamed to `..._is_five` (STATE_VERSION 4 to 5); `tests/test_portrait_logging.py` guard exemption (D-2). All other test edits are additions. Red-first not evidenced (V-4))*
      name in the verifier note, with the reason, and was updated red first.
- [x] The suite performs no network I/O. A scratch copy with no `.env` and no *(scratch copy with no `.env`, run under `env -i`: 978 passed)*
      `TELEGRAM_BOT_TOKEN` or `GEMINI_API_KEY` in the environment passes.
- [x] Only the one fixture test performs real encoding. Every other test fakes the *(V-2 fixed: the only real encode is `tests/test_narrator_audio.py::test_the_default_encoder_turns_a_valid_reply_into_a_checked_mp3`; `test_narrator.py` injects a fake encoder)*
      encoder.
- [x] `grep -r "google.genai\|google.adk" tests/` returns zero hits, except the string *(hits only in the subprocess guard literal `tests/test_narrator_client.py:155` and in docstrings)*
      literals in subprocess guards (if any), which are listed in the verifier note.
- [x] `scripts/hooks` exits `0`; `scripts/test` is re-run afterwards with no unexpected *(hooks exit 0, no tree change; test re-run 978 passed)*
      file changes.

## C. Typed contracts at the boundary

- [x] `TtsReply` accepts the expected MIME type and sample rate with non-empty bytes.
- [x] `TtsReply` rejects empty bytes (`empty_audio`), bytes over `MAX_PCM_BYTES`
      (`oversize_audio`), and a wrong MIME type or sample rate (`unexpected_format`).
- [x] `VoiceNote` accepts an `ID3` start and an MPEG Layer III frame-sync start
      (`0xFF 0xFB`, `0xFF 0xF3`, `0xFF 0xF2`). It rejects other starts (`bad_mp3`), empty
      bytes, and oversize bytes.
- [x] Both models reject extra fields.
- [x] Error text carries the reason code and byte count, never the bytes.
- [x] A reply with no audio part becomes `InvalidResponse`.

## D. Happy path

- [x] A completed run sends the portrait, then the script text, then exactly one voice
      note, in that order, to the correct chat.
- [x] Exactly one synthesis call per successful run.
- [x] The voice note is sent with `sendVoice` (`send_voice`), as MP3 bytes, with no *(MP3 is a recorded deviation (D-1))*
      caption and no `parse_mode`.
- [x] `narration_started` is `True` after the run. Phase is `DELIVERING`. `in_flight` is
      `False`. `script` is kept. The dossier is kept.
- [x] No Phase 7 code is present (no `/start` change). *(`routing.py` diff has no `/start` change)*

## E. Voice and prompt

- [x] `NARRATOR_INSTRUCTION` contains the words "speak with a posh British accent", a
      deep male voice description, and an Attenborough-style description.
- [x] The synthesis request contains the instruction and the script text, and the
      `NARRATOR_VOICE` value. It contains no dossier fields, photo bytes, or answers.
- [x] The voice name is read from `config.py` only. Logic reads the constant.
- DEFERRED: Manual check (Tech Lead, deferred to live samples): the accent, the deep voice, and
      the Attenborough delivery. Not checkable offline.

## F. Retry and fallback

- [x] Timeout, 429, or 5xx on attempt 1 and success on attempt 2 → the voice note is sent.
- [x] Timeout, 429, or 5xx on both attempts → exactly 2 synthesis calls, then
      `NARRATION_UNAVAILABLE_TEXT`.
- [x] 400, 401, 403, and 404 → exactly 1 call, then `NARRATION_UNAVAILABLE_TEXT`. *(one case per status in `test_narrator_client.py` (parametrised); tool-level fail-fast is tested once)*
- [x] Empty, oversize, wrong-format, and no-audio replies → exactly 1 call, no retry, then
      the fallback.
- [x] A plain provider retry waits `RETRY_BACKOFF_SECONDS`. No other backoff occurs.
- [x] `NARRATION_TIMEOUT_SECONDS == 30.0`. A transport timeout maps to `ProviderTimeout`.
- [x] Total synthesis attempts per run never exceed 2 (asserted across all cases). *(`MAX_ATTEMPTS = 2`)*
- [x] `bad_mp3` or an encoder error → no retry, then the fallback.
- [x] A failed voice send → exactly one `send_voice` call, then one
      `NARRATION_UNAVAILABLE_TEXT`, no voice retry.
- [x] Every fallback leaves the script text delivered, `script` kept, and the phase at
      `DELIVERING`.

## G. Temp audio

- [x] The temp file is created in `tempfile.gettempdir()`. It is never under the repo root
      (asserted on the resolved path).
- [x] The temp file is removed after a successful send, after a failed send, after an *(removal after encoder, synthesis, write, and stale-drop failures holds by construction (no file is created on those paths); no dedicated test)*
      encoder failure, after a synthesis failure, after a temp write failure, and after a
      stale-epoch drop.
- [x] A removal failure is logged at `error`, the step completes, and no exception
      escapes.
- [x] No audio file exists under the repo after any test (`git status` clean of media). *(`find` finds no `.mp3`, `.ogg`, `.wav`, or `.pcm` outside the fixture)*
- [x] Source grep: `mkstemp` appears only in the narration step, with a `finally` removal. *(deviation (D-4): `mkstemp` is in `narrator_sender.py`, called by the narration step; `finally` removal present; no `NamedTemporaryFile`)*
      No `NamedTemporaryFile` with `delete=False` outside it.

## H. `/restart` and duplicates

- [x] `/restart` while synthesis is blocked → reset, `OPENING_PROMPT` sent, phase
      `AWAITING_PHOTO`. It is not nudged with `ONE_AT_A_TIME_TEXT`.
- [x] When the blocked synthesis returns, nothing is sent and the state does not change.
      An `info` line records the drop.
- [x] `/restart` between encode and send → no `send_voice` after the reset; the temp *(V-3 fixed: `test_restart_between_encode_and_send_drops_the_voice_note`; temp removal is covered in `test_narrator_sender.py`)*
      file is removed.
- [x] A text or photo during synthesis → `ONE_AT_A_TIME_TEXT`; synthesis is called exactly
      once.
- [x] A text or photo during `send_voice` → `ONE_AT_A_TIME_TEXT`; exactly one voice note. *(V-3 fixed: `test_a_duplicate_during_the_voice_send_is_nudged_and_sends_one_note`)*
- [x] A text or photo after narration → `ALREADY_DONE_TEXT`; no synthesis.
- [x] `begin_narration` refuses a second call in the same run, including after a failure.
- [x] A new photo after a restart starts a fresh interview. *(V-3 fixed: `test_after_a_restart_a_fresh_interview_runs_to_narration_again`)*

## I. State and isolation

- [x] `STATE_VERSION == 5`; a stored version `4` is discarded on read.
- [x] `narration_started` defaults to `False`, is set only by `begin_narration`, and is
      cleared by `reset`.
- [x] `narration_started` is `True` only in `DELIVERING` with `script` set (invariant
      tested; `StateError` on violation).
- [x] `begin_narration` and `end_narration` refuse a stale epoch and change nothing.
- [x] Cross-chat isolation: chat A's voice note goes only to chat A. `/restart` in A *(V-3 fixed: `test_narration_in_one_chat_does_not_reach_another_chat`, with chat B mid-interview)*
      leaves B's interview and answers intact.
- [x] Stages do not write session fields. Grep: no assignment to `narration_started`, *(grep: `narration_started`, `in_flight`, `script`, `phase` are assigned only in `state.py`)*
      `in_flight`, `script`, or `phase` outside `state.py` and the driver.

## J. Config, copy, and seams

- [x] `MODELS["narrator"] == "gemini-3.1-flash-tts-preview"`. The Scripter, Bouncer, and
      Interviewer entries are unchanged.
- [x] `NARRATION_UNAVAILABLE_TEXT` is non-empty, names `/restart`, says the portrait and
      script stand, and contains no code, error type, provider wording, or stack trace.
- [x] `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused, not
      redefined.
- [x] `GeminiNarrationClient` construction does not import `google.genai` and does not
      touch the network.
- [x] Status mapping matches the Bouncer, Interviewer, and Scripter policy (table in *(`narrator_client` calls the shared `map_provider_error`; no second table)*
      `requirements.md`). The table is not duplicated.

## K. Logging and safety

- [x] With `caplog`, a full run logs chat id, phase transitions, attempt numbers, *(V-1 fixed: `@log_stage` on `generate_narration` and per-attempt stages `synthesise_attempt_1/2`; `test_a_retried_run_logs_each_attempt_number_and_its_outcome`, `test_a_failed_run_logs_the_error_type_and_duration_at_error`; `narration_delivered` logs `mp3_bytes`)*
      outcomes, durations, error types, and byte counts.
- [x] The same run never logs the script text (sentinel), the audio bytes (sentinel), *(V-1 fixed: `test_a_run_never_logs_script_text_audio_bytes_or_keys`; sentinels for text, key, raw and base64 audio)*
      base64, the prompt, the dossier, the answers, the provider body, the temp path,
      tokens, or keys.
- [x] A failed narration logs the error type or reason code at `error` and never the *(the step logs the error type only; the reason code and byte count for malformed audio are not logged (see V-1))*
      provider message.
- [x] A stale-epoch drop logs at `info` with no text and no bytes. *(by code (`narration_dropped reason=stale_run`); no dedicated test)*
- [x] Source grep over the changed modules: no bare `except`, no `except Exception: pass`, *(no bare `except`; `narrator_audio` re-raises as `ValueError`; no `parse_mode`)*
      no recovered error logged below `error`, no `parse_mode` on the voice send.
- [x] Only `narrator_client.py` imports `google.genai`. Only `narrator_audio.py` imports *(`lameenc` only in `narrator_audio.py`)*
      the encoder library. Grep `src/` to confirm.
- [x] ADK is imported only in `vision_client.py`, `interview_client.py`, and *(`converter_client.py` imports `google.genai` as the spec says)*
      `scripter_client.py`. `converter_client.py` imports `google.genai`. Grep `src/`.

## L. Scope discipline

- [x] No voice selection, multiple voices, other languages, or video code.
- [x] No new `Phase` member. `GENERATING` is not used for narration.
- [x] No second synthesis path, no re-send path, no audio cache, no persistence.
- [x] No caption on the voice note. No header on the script text.
- [x] No dependency other than the approved encoder (N3). *(only `lameenc`; approval open (V-4))*
- [x] No webhook, database, or inbound HTTP server.
- [x] `/start` behaviour is unchanged (Phase 7).
- [x] ROADMAP, MISSION, and TECH are unchanged by the build. The OGG/Opus wording is *(amended by the verifier: OGG to MP3 in ROADMAP, MISSION, TECH)*
      listed for the verifier.

## M. Documentation

- [x] `README.md` describes the voice note as step 3, the MP3 format, the temp-file rule, *(README updated by the verifier)*
      `NARRATION_UNAVAILABLE_TEXT` with `/restart`, `/restart` during narration, and the
      resting `DELIVERING` phase.
- [x] The MP3 deviation from the roadmap's OGG/Opus is recorded in `requirements.md` *(`requirements.md` roadmap section and V-notes below)*
      (roadmap deviation section) and in the verifier note.
- [x] `SPECS/ROADMAP.md` Phase 6 status is not flipped by the build. *(set to `[~]` by the verifier, not `[x]`)*
- [ ] Decisions N1 to N18 in `requirements.md` are confirmed or revised by the Tech Lead *(open: awaiting Tech Lead (V-4))*
      before merge. In particular: N1 (attempt budget), N2 (30 s timeout), N3 (encoder),
      N13 (narration after a failed text send), N14 (no voice retry), and N16 (temp file
      versus in-memory bytes).

## N. Live verification (requires real credentials; DEFERRED)

- DEFERRED: a completed run sends the portrait, then the script text, then one voice
  note, in the same chat.
- DEFERRED: the voice note plays inline in Telegram and is recognised as a voice note.
- DEFERRED: a live sample has the deep male, posh British, Attenborough delivery. The
  Tech Lead chooses `NARRATOR_VOICE` from the audition (N7).
- DEFERRED: the reply format matches the group 0 record (MIME type and sample rate) on a
  real call.
- DEFERRED: `/restart` during a real synthesis resets the chat promptly and the late
  voice note is not delivered.
- DEFERRED: a duplicate during a real synthesis gets the one-at-a-time nudge and starts no
  second call (check the provider usage count).
- DEFERRED: a forced provider failure (for example, an invalid key in a scratch run)
  shows the narration-unavailable note only, with no payload or stack trace.
- DEFERRED: logs for a full run show no script text, audio, tokens, keys, or answer text.
- DEFERRED: the temp directory has no leftover `.mp3` file after a full run.

> Deferred until real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` values are supplied. Do
> not fabricate a live pass. The offline checks above do not claim a live pass.

## O. Secrets, privacy, and media scan

- [x] No secret-shaped string (a Gemini `AIza…` key or a Telegram `id:token`) appears in *(grep for `AIza…` and `id:token` shapes: 0 hits outside `.env` (not read))*
      the change.
- [x] `.env` and `.env.save` remain gitignored. *(both gitignored; the fixture is the only new binary, 1024 bytes)* No `.env`, audio, or temp file is in the
      tree. The fixture (`tests/fixtures/narrator_tone.pcm`) is the only new binary file,
      and it is about 1 KB.

## Definition of mergeable

1. Sections A to L pass, or each open item is explicitly accepted by the Tech Lead.
2. Section N is deferred (live). The deferral is recorded in the ROADMAP Phase 6 verifier
   note.
3. `scripts/test` exit 0 and `scripts/hooks` exit 0, with no tree change after the hooks
   run.
4. No secrets, raw answers, script text, audio, or model replies in logs, the repo, or
   fixtures committed to the tree. No temp files left behind.
5. Exactly one synthesis per successful run, with at most 2 model attempts in total, and
   at most one voice note per run.
6. The OGG/Opus wording in ROADMAP, MISSION, and TECH is listed for amendment, and the
   MP3 deviation is recorded.
7. No blocking code finding. Decisions N1 to N18 are recorded, and the Tech Lead has
   accepted or revised them.

---

## Verifier re-check (2026-11-24, after the fixes)

- **V-1 (logging): fixed.** `generate_narration` is `@log_stage`; each attempt has its own
  stage name (`synthesise_attempt_1/2`), with `outcome`, `duration_ms`, and `error_type`.
  The sentinel test passes (script text, key, raw and base64 audio absent).
- **V-2 (real encoder): fixed.** The only real encode is in `test_narrator_audio.py`.
  `test_narrator.py` uses a fake encoder.
- **V-3 (routing): fixed for the checklist items.** Duplicate during `send_voice`,
  `/restart` between encode and send, cross-chat narration, and a fresh interview after
  restart each have a passing test. Gap, not in the checklist: no routing-level test for a
  sticker, voice note, or document after narration. `app.py` registers only TEXT and PHOTO
  handlers, so such updates never reach the router. A test should still pin this.
- **Still open:** V-4, V-5, V-6 (see below). Live section N stays deferred.

## Verifier results (2026-11-24)

### Blocking findings (send back to the spec-implementer)

- **V-1, logging (K).** `src/telegram_documentaries/narrator.py` has no `@log_stage` and
  no logger. `generate_narration` logs no attempt number, duration, or byte count. The
  step logs only the error type on failure, not the reason code or the byte count. There
  is no full-run `caplog` sentinel test for narration (script, audio, and prompt
  sentinels absent from all records). Required by `requirements.md` (Logging) and plan
  group 5 and group 8.
- **V-2, real encoding (B).** `tests/test_narrator.py::test_the_default_encoder_turns_a_valid_reply_into_a_checked_mp3`
  runs the real `lameenc` encoder. Only the one fixture test may encode. Fix: inject a
  fake encoder, and keep the real encode in `test_narrator_audio.py`.
- **V-3, missing required tests (H, I, plan groups 6 and 7).** No test for: a duplicate
  text or photo during `send_voice` (exactly one `send_voice`, `ONE_AT_A_TIME_TEXT`);
  `/restart` between encode and send (no `send_voice`, temp file removed); cross-chat
  narration (chat A narrates while chat B is mid-interview); a new photo after a restart
  starting a fresh interview; a sticker, voice note, or document after narration
  (`ALREADY_DONE_TEXT`, no state change).

### Open items (not blocking code, but needed before merge)

- **V-4, process.** No red-run output recorded for each new test file (commit history
  is unavailable while uncommitted). No Tech Lead approval of N3 (`lameenc`) or of
  decisions N1 to N18 is recorded.
- **V-5, spike.** No group 0 spike record exists in `SPECS/`. The MIME type
  (`audio/L16`) and sample rate (24000 Hz) in `narrator_models.py` are unconfirmed against
  a live reply. That confirmation is part of the deferred live checks.
- **V-6, voice.** `NARRATOR_VOICE = "Charon"` is provisional (N7). The Tech Lead chooses
  the final voice after a live audition.

### Deviations from the plan (recorded)

- **D-1, MP3, not OGG/Opus.** Telegram's `sendVoice` accepts MP3, so an MP3 voice note is
  still a voice note. Amended in `SPECS/ROADMAP.md` (Phase 6 bullets and acceptance
  criteria), `SPECS/MISSION.md` (step 7), and `SPECS/TECH.md` (Audio, pipeline diagram).
  Recorded in `requirements.md` (roadmap deviation section).
- **D-2, Phase 4 guard exemption (narrator_sender.py only).** `tests/test_portrait_logging.py`
  bans `tempfile` imports in source. The exemption is `_TEMPFILE_EXEMPT_FILES =
  frozenset({"narrator_sender.py"})`, and the test asserts that set stays exactly that one
  file. The exemption covers imports only. `write_bytes` and `write_text` stay banned
  everywhere. Verified in the test source.
- **D-3, test file.** `tests/test_narration_step.py` (plan group 6) was not created. The
  narration-step coverage is in `tests/test_narrator_wiring.py` and
  `tests/test_narrator_sender.py`.
- **D-4, temp file location.** `mkstemp` is in `narrator_sender.py` (the Telegram sender),
  not in `generation.py`. It is called from the narration step, with `finally` removal.
- **D-5, narration step location.** `_run_narration_step` is in `generation.py`, next to
  `_run_script_step`, as the plan allows.
- **D-6, existing test expectation.** `test_the_schema_version_is_four` became
  `test_the_schema_version_is_five` (STATE_VERSION 4 to 5, N9). Expected and recorded.

### Deferred (live, section N)

Not run. No live Gemini or Telegram call was made. Deferred: the end-to-end send order,
inline playback, the voice sample, the reply format on a real call, `/restart` during a
real synthesis, a duplicate during a real synthesis (provider usage count), a forced
provider failure, log content for a real run, and the temp directory after a real run.

### Docs updated by the verifier

- `README.md`: status (Phase 6), script and voice-note section (step 2 and 3, MP3, temp
  file, 2-attempt budget, narration-unavailable note, `/restart` during narration),
  pipeline, deferred list, layout.
- `SPECS/TECH.md`: Audio (MP3, `lameenc`), pipeline diagram, Narrator policy line.
- `SPECS/MISSION.md`: step 7 (MP3, with the deviation note).
- `SPECS/ROADMAP.md`: Phase 6 bullets and acceptance criteria (MP3), status `[~]` with a
  verifier note. Not `[x]`, because the blocking findings and open items are unresolved.
