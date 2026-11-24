# Requirements — Narrator / Voice Note (Phase 6)

Spec folder: `SPECS/2026-11-24-narrator/`
Roadmap phase: **Phase 6 — Narrator (TTS voice note)**
Branch: `feature/2026-11-24-narrator` (based on `main`, which includes the merged Phase 5 Scripter, PR #5)

## Context

Phase 5 ends with the portrait sent, the script text sent, and the validated paragraph
held in `SessionState.script`. Phase 6 renders that paragraph with Gemini TTS and
delivers it as one Telegram voice note. The order in the chat is fixed:

1. portrait (Phase 4)
2. script text (Phase 5, the fallback)
3. voice note (Phase 6)

The Narrator is a **tool**, not an agent. It is a direct, single call to the TTS model.
It has no planning loop and no ADK agent.

## User decisions (confirmed before this spec was written)

| Decision | Choice | Rationale |
|---|---|---|
| Narrator kind | A tool, not an agent. One direct TTS call per run | TECH.md (Narrator is "TTS, not an agent") |
| Model | `gemini-3.1-flash-tts-preview`. The ID is defined once, in the single config module (`config.py`, `MODELS["narrator"]`) | TECH.md Models; config is the only place a model ID lives |
| Provider SDK imports | Only model-client modules import ADK or `google.genai`. The Narrator uses `google.genai` directly, in `narrator_client.py`. The stage, router, step, and tests do not import either | TECH.md Architecture (D14) |
| Input | The script text stored in session state (`SessionState.script`, Phase 5) | User decision |
| Output | Exactly one audio payload per successful run | User decision |
| Voice | A deep male voice with a posh British accent, Attenborough-style. The TTS prompt includes the words "speak with a posh British accent". The voice name is chosen in `config.py` and is not hard-coded in logic | User decision |
| Audio format | MP3, sent with Telegram `sendVoice` as a voice note | User decision (see the roadmap note below) |
| Delivery order | Portrait, then script text, then voice note | User decision; MISSION step 7 |
| Script text | Kept as the fallback. It is delivered before the voice note and is never removed | User decision |
| Failure | If TTS (or encoding or the voice send) fails, the script text is already delivered, so the run is not lost. Log at `error`. Send a short user-facing note that narration is unavailable. No stack trace | User decision |
| Synthesis count | Exactly one synthesis per successful run | User decision; MISSION cost discipline |
| Duplicates | A duplicate update during synthesis or delivery starts no second synthesis and sends no second voice note | User decision |
| Temp audio | Written to the system temp directory only, never inside the repo. Deleted after send, on success or failure | User decision; TECH.md Repo hygiene |
| Logging | Never log audio bytes, script text, or keys. Byte counts and ids are allowed | User decision; MISSION privacy |
| Timeout and retry | Explicit timeout. Max 2 attempts. Retry only for timeout, 429, and 5xx. Same pattern as the Converter and Scripter | User decision; TECH.md External calls |
| Output validation | A typed model for the TTS reply: audio bytes present, non-empty, format as expected. Empty or malformed audio is rejected | User decision |
| Tests | The TTS client and the Telegram sender are faked. No network. No real audio encoding in tests beyond one small fixture | User decision; TECH.md Testing |
| Backward compatibility | Not needed | User decision |
| Out of scope | Voice selection by the user, multiple voices, other languages, video | User decision |

### Roadmap deviation: MP3, not OGG/Opus (recorded)

ROADMAP Phase 6 and MISSION step 7 say "OGG/Opus". **MP3 is a deliberate change.**
Telegram's `sendVoice` accepts MP3, M4A and OGG/Opus, so an MP3 voice note is still a
voice note. Recorded by the verifier on 2026-11-24: the wording below is now amended to MP3 (see
`validation.md`, D-1). The original list was:  `SPECS/ROADMAP.md` (Phase 6 bullets and acceptance
criteria), `SPECS/MISSION.md` (end-to-end step 7), and `SPECS/TECH.md` (Stack, Audio).
This spec does not edit those files.

## Decisions beyond the user's list

These were not in the brief. Each is a choice made for this spec. The Tech Lead must
confirm or reject each one before merge.

| # | Decision | Value | Why |
|---|---|---|---|
| N1 | Attempt budget | **2 synthesis attempts per run.** Timeout, 429, or 5xx on attempt 1 → attempt 2 after the backoff. Any other failure, and any failure on attempt 2, ends the synthesis. A malformed or empty TTS reply fails at once with no retry, because the brief allows retries only for timeout, 429, and 5xx. Worst case is two paid calls | Brief: max 2, retry classes limited; cost discipline |
| N2 | TTS timeout | `NARRATION_TIMEOUT_SECONDS = 30.0` per attempt | Speech synthesis is slower than text (Scripter 10 s) and faster than images (Converter 60 s). Working value; see Open decision 4 |
| N3 | PCM-to-MP3 encoding | Gemini TTS is expected to return raw PCM (`audio/L16`, mono, 24 kHz, per the SDK types; confirm in group 0). MP3 is not a format the TTS call returns, so the bytes are encoded locally. **Proposed encoder: `lameenc`** (a pip wheel with no system binary). This is a new dependency and needs Tech Lead approval before group 2 adds it. If the spike shows the TTS reply is already MP3, no encoder is added | Encoding is required by the MP3 decision; Phase 5 added no dependency, so this one must be explicit |
| N4 | Reply format check | The TTS reply must have the MIME type and sample rate recorded in group 0 (`EXPECTED_PCM_MIME`, `EXPECTED_SAMPLE_RATE_HZ`). Any other value is `unexpected_format` | "Format as expected" must be checkable in code |
| N5 | Size caps | Raw TTS reply at most 10 MiB (`MAX_PCM_BYTES`). Encoded MP3 at most 10 MiB (`MAX_MP3_BYTES`). Empty bytes are `empty_audio`. Larger is `oversize_audio` | Bounds untrusted provider output; matches the Converter's 10 MiB image cap |
| N6 | MP3 output check | The encoded bytes must be non-empty and start with an ID3 tag (`ID3`) or an MPEG Layer III frame sync (`0xFF` followed by `0xFB`, `0xF3`, or `0xF2`). Otherwise `bad_mp3` | Checks the encoder output before it reaches Telegram |
| N7 | Voice name | Set once in `config.py` as `NARRATOR_VOICE`. The build uses a documented Gemini prebuilt voice that is deep and male. The final choice is the Tech Lead's, after a live audition (Open decision 3). Logic reads the constant only | User decision (in config); the audition choice is not made offline |
| N8 | TTS prompt | One string, `NARRATOR_INSTRUCTION`, in `prompts.py`. It describes a deep male voice, hushed Attenborough-style delivery, and includes the words "speak with a posh British accent". The script text follows it as the content. The dossier, the photo, and the answers are never sent to TTS | Dossier-only and minimum-data principle (as Phase 5, S16) |
| N9 | Session schema | Bump `STATE_VERSION` from 4 to 5. Add `narration_started: bool = False`. Invariant: `narration_started` is `True` only in `DELIVERING`, and only when `script` is set. A stored version 4 is discarded on read | Stored state changes; versioning rule (TECH.md) |
| N10 | One-run guard | `begin_narration` sets `narration_started = True` and `in_flight = True` together. It refuses if `narration_started` is already set, so a run can never start a second synthesis. `narration_started` is cleared only by `reset` | Makes "exactly one synthesis" a state fact, not a convention. A failed run is not retried, so the count stays at one |
| N11 | Epoch guard | The reset epoch (`SessionStore.epoch`, as in Phase 4 and Phase 5) guards every narration write and every send. A stale epoch is dropped with no send, logged at `info` | Reuses the existing stale-result mechanism |
| N12 | Paid-call guard | `still_current()` is checked before each synthesis attempt. A reset stops any further paid call | Phase 4 D18 and Phase 5 flow step 3 |
| N13 | Narration after a failed script text send | If the script text was committed (`script` set) but the text send failed, narration still runs. The voice note is then the only narration the user receives. This is a judgment call | The brief says the script text is the fallback. If the text did not arrive, the voice note is the more useful payload. Open decision 5 |
| N14 | Voice send | Exactly one `send_voice` attempt. A failure logs at `error`, sends the note once (N15), and the temp file is deleted. No retry of the voice send, matching Phase 5 (no retry of the text send) | Cost and duplicate avoidance; a voice send retry could duplicate a note |
| N15 | Failure copy | `NARRATION_UNAVAILABLE_TEXT`, sent as plain text. It says that narration is unavailable, that the portrait and script stand, and names `/restart`. It contains no code, error type, provider wording, or stack trace | User decision (short note); copy rules as `SCRIPT_FAILED_TEXT` |
| N16 | Temp file handling | `tempfile.mkstemp(suffix=".mp3")` in the system temp directory (`tempfile.gettempdir()`). The file is written once, read by the sender, and removed in a `finally`. A failed removal is logged at `error` and never raised. The file is never inside the repo | User decision. Note: PTB also accepts in-memory bytes for `send_voice`, which would avoid the temp file. The brief says temp dir, so the temp file is used (Open decision 6) |
| N17 | Voice note has no caption | The voice note is sent with no caption and no `parse_mode`. The script text is already in the chat | Avoids a duplicate of the script text; keeps the message minimal |
| N18 | Narration in flight | While the synthesis, encoding, write, and send are in progress, any update except `/restart` gets `ONE_AT_A_TIME_TEXT`. After `end_narration`, any update gets `ALREADY_DONE_TEXT` | Reuses the existing copy and the existing in-flight guard |

## Scope

### In scope

1. **Narration trigger.** After the script text step (Phase 5) has committed `script`,
   the Narrator runs once for the chat. It starts automatically. The user does not
   confirm.
2. **Narration client seam** (`narrator_client.py`). A `NarrationClient` protocol with a
   `google.genai`-backed implementation. It is a model-client module and the only
   Phase 6 module that imports `google.genai`. It builds the client lazily.
3. **Reply contract** (`narrator_models.py`). `TtsReply` (raw PCM, N4 and N5) and
   `VoiceNote` (MP3 bytes, N5 and N6), both Pydantic v2 with `extra="forbid"`.
4. **Encoder seam** (`narrator_audio.py`). `encode_mp3(reply: TtsReply) -> VoiceNote`.
   It is the only module that imports the encoder library (N3). Tests fake it, except
   one fixture test.
5. **Narrator tool** (`narrator.py`). `generate_narration(text, client, *, still_current)
   -> TtsReply`, with the N1 attempt budget and the N12 guard. It never calls Telegram
   and never writes to the session.
6. **Narration step** (`generation.py` or a new `narration_step.py`, following the
   Phase 5 script step). Runs `begin_narration`, synthesis, encoding, temp write,
   `send_voice`, temp removal, and `end_narration`, with the fallback (N15).
7. **Config** (`config.py`). `MODELS["narrator"] = "gemini-3.1-flash-tts-preview"` and
   `NARRATOR_VOICE`. The TTS model ID is defined once.
8. **Prompt** (`prompts.py`, `NARRATOR_INSTRUCTION`, N8).
9. **Copy** (`messages.py`, `NARRATION_UNAVAILABLE_TEXT`, N15).
10. **Session v5** (`state.py`, N9 to N11): `narration_started`, `begin_narration`,
    `end_narration`, with the invariant.
11. **`/restart` in every phase.** During narration it resets the chat. A late synthesis
    result or a late voice send is dropped (N11). `info` is logged.
12. **Typed errors.** Reuse `ProviderTimeout`, `RateLimited`, `VisionUnavailable`,
    `ProviderRejected`, and `InvalidResponse` from `errors.py`. Add the narration
    reason codes `empty_audio`, `oversize_audio`, `unexpected_format`, `bad_mp3`
    (as constants or a typed error with a reason). The stage maps them to the fallback.
13. **Structured logging** via `@log_stage` and `configure_logging()`, one logger per
    module.
14. **Red/Green TDD tests**, offline, with the TTS client, the encoder (except one
    fixture), and the Telegram sender faked.
15. **README** updated: the voice note as step 3, the MP3 format, the temp-file rule,
    the narration-unavailable note, and `/restart` during narration.

### Out of scope (do not build)

- Voice selection by the user, multiple voices, other languages, and video (user
  decision).
- A caption or header on the voice note (N17).
- A voice send retry (N14). Regenerating or re-sending narration on request.
- Streaming TTS, partial audio, or a second synthesis for any reason.
- Persisting audio, the script, or a cache of audio to disk or a database.
- Any new phase. `DELIVERING` is reused. `GENERATING` is not used for narration.
- Editing the ROADMAP, MISSION, or TECH OGG wording (the verifier amends it).
- Changing the Scripter or the Converter, except where routing must call the narration
  step after the script step.
- `/start` behaviour changes (Phase 7 owns them).
- Localisation, rate limiting, quotas, or cost dashboards.
- A dependency other than the encoder in N3. Any other new dependency needs a new spec.

## Phases and state

| Phase | Status |
|---|---|
| `IDLE` | declared, not driven (as before) |
| `AWAITING_PHOTO` | driven |
| `INTERVIEWING` | driven |
| `DOSSIER_READY` | driven |
| `GENERATING` | driven (Phase 4) |
| `DELIVERING` | driven (Phase 4); rests here after narration |

No new `Phase` member. Narration runs inside `DELIVERING` with `in_flight` set during
the work and `narration_started` set for the run.

## Contracts (typed at the boundary)

### `TtsReply` (Pydantic v2, `extra="forbid"`, strict)

- `pcm: bytes`: the raw audio from the TTS reply.
- `mime_type: str`: must equal `EXPECTED_PCM_MIME` (N4).
- `sample_rate_hz: int`: must equal `EXPECTED_SAMPLE_RATE_HZ` (N4).
- Reason codes (N4, N5): `empty_audio`, `oversize_audio`, `unexpected_format`.
- Failure raises a Pydantic `ValidationError`. The stage converts it to
  `InvalidResponse` carrying the reason code and the byte count only, never the bytes.

### `VoiceNote` (Pydantic v2, `extra="forbid"`, strict)

- `mp3: bytes`: the encoded audio.
- Reason codes (N5, N6): `empty_audio`, `oversize_audio`, `bad_mp3`.
- Failure carries the reason code and the byte count only.

### `NarrationClient` (protocol / seam)

`async def synthesise(text: str, *, voice: str) -> TtsReply`

- `text` is the script text. The request content is `NARRATOR_INSTRUCTION` followed by
  `text`. The instruction is built in `narrator_client.py` from `prompts.py`.
- `voice` is `NARRATOR_VOICE` from `config.py`, passed in. It is not a literal in logic.

`GeminiNarrationClient` implements the protocol:

- Built lazily. Creating the client does not import `google.genai` and does not touch
  the network.
- Uses `MODELS["narrator"]`.
- Requests audio output only (`response_modalities=["AUDIO"]`) with the named voice
  (`speech_config`). The exact SDK field names are confirmed in group 0.
- Applies `NARRATION_TIMEOUT_SECONDS` (N2).
- Maps provider failures with the policy of the Bouncer, Interviewer, and Scripter:
  400, 401, 403, 404 → `ProviderRejected`; 429 → `RateLimited`; 5xx and unknown →
  `VisionUnavailable`; timeout → `ProviderTimeout`. A reply with no audio part →
  `InvalidResponse`.
- Logs the error type and HTTP status only. Never the prompt, the script, or the
  response body.
- Reuses the status table from `vision_client.py` (`map_provider_error`) or the helper
  factored out in Phase 5. The table is not duplicated.

### `SessionState` (version 5, N9)

- `schema_version: int`: `5`. A stored `4` is discarded on read.
- Existing fields as in Phase 5, including `script` and `in_flight`.
- `narration_started: bool = False`. Set only by `begin_narration`. Cleared on reset.

Invariant: `narration_started` is `True` only in `DELIVERING`, and only when `script` is
set. Violations raise `StateError`.

### Driver methods (N10, N11)

- `begin_narration(chat_id, epoch) -> bool`: from `DELIVERING`; `script is not None`;
  `narration_started is False`; `in_flight is False`; epoch matches. Sets
  `narration_started = True` and `in_flight = True`. Returns `False` otherwise.
- `end_narration(chat_id, epoch) -> bool`: epoch matches. Clears `in_flight`. Returns
  `False` and changes nothing for a stale epoch. `narration_started` stays `True`.

Stages do not write session fields. Only the driver writes them.

## Flow

1. **Script committed** (Phase 5, unchanged): `commit_script` sets `script`, and the
   script text is sent (or the apology is sent on a send failure, N13).
2. **Narration step starts** (same handler, no `await` between the script step and this
   step's `begin_narration`; the run's epoch is captured at the start of the run):
   `begin_narration(chat_id, epoch)`. If `False`, stop, log `narration_dropped` at
   `info`.
3. **Synthesise with the attempt budget** (N1): `generate_narration(script, client,
   still_current)`.
   - Before each attempt, `still_current()` is checked (N12). If stale, stop with no
     model call.
   - Attempt 1: `client.synthesise(text, voice=NARRATOR_VOICE)`, then validate into
     `TtsReply`.
   - Timeout, 429, or 5xx on attempt 1 → backoff (`RETRY_BACKOFF_SECONDS`), then
     attempt 2.
   - Malformed or empty reply, `ProviderRejected`, or any other typed error → no retry.
   - Any failure on attempt 2 → fallback (step 7).
4. **Encode.** `encode_mp3(reply)` → `VoiceNote` (N3, N6). Failure (`bad_mp3`, encoder
   error) → fallback (step 7).
5. **Write and send.**
   a. If the epoch is stale, log `narration_dropped` at `info`, send nothing, and stop
      (N11).
   b. Write the MP3 to a `mkstemp` file in the system temp directory (N16).
   c. `send_voice(chat_id, path)` with no caption (N17). If the send raises, log the
      error type at `error`, then go to step 7 (the single apology note, N14).
6. **Cleanup** (always, in `finally`): remove the temp file (N16). Then
   `end_narration(chat_id, epoch)`. A removal failure is logged at `error`, and the step
   continues.
7. **Fallback.** If the epoch is current, send `NARRATION_UNAVAILABLE_TEXT` once (N15).
   Log the error type or reason code at `error`. The phase stays `DELIVERING`.
   `script` stays set. `narration_started` stays `True`, so no second synthesis starts.
8. **`/restart` in any phase** → `reset` → `OPENING_PROMPT`. Bypasses the in-flight
   guard. A late synthesis result, late encode, or late send is dropped (N11).

## Copy

- `NARRATION_UNAVAILABLE_TEXT`: a short line that narration is unavailable for this run,
  that the portrait and script stand, and that `/restart` starts again. Example:
  "Narration is unavailable for this run. Your portrait and script stand; send /restart
  for a new subject." No code, error type, provider wording, or stack trace.
- The voice note is sent with no caption and no `parse_mode` (N17).

Existing copy reused: `ALREADY_DONE_TEXT`, `ONE_AT_A_TIME_TEXT`, `OPENING_PROMPT`.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| Synthesis succeeds on attempt 1, encode and send succeed | Voice note sent; temp file removed; `narration_started` set; `DELIVERING` |
| Timeout, 429, or 5xx on attempt 1; success on attempt 2 | Voice note sent |
| Timeout, 429, or 5xx on both attempts | `NARRATION_UNAVAILABLE_TEXT`; logged at `error` with the typed error name; no third attempt |
| 400, 401, 403, or 404 (`ProviderRejected`) | One attempt; `NARRATION_UNAVAILABLE_TEXT` |
| Empty, oversize, or wrong-format reply (`empty_audio`, `oversize_audio`, `unexpected_format`) | No retry; `NARRATION_UNAVAILABLE_TEXT`; logged at `error` with the reason code and byte count |
| Encoder failure or `bad_mp3` | No retry; `NARRATION_UNAVAILABLE_TEXT`; logged at `error` |
| Temp file cannot be written | `NARRATION_UNAVAILABLE_TEXT`; logged at `error`; nothing left behind |
| `send_voice` raises | Logged at `error`; `NARRATION_UNAVAILABLE_TEXT` once; no voice retry; temp file removed |
| Temp file removal fails | Logged at `error`; the run continues; not user-visible |
| `/restart` during synthesis | Reset wins; late result dropped; `OPENING_PROMPT`; logged at `info` |
| `/restart` during encode or between write and send | Epoch check before the send drops the note; temp file removed in `finally` |
| `/restart` after the send has started | The send in flight is not recalled. Accepted race, as with Phase 4 D18 and Phase 5 |
| Duplicate text or photo during narration | `ONE_AT_A_TIME_TEXT`; no state change; no synthesis |
| Text or photo after narration ends | `ALREADY_DONE_TEXT`; no state change; no synthesis |
| Script text absent (`script is None`) | Narration does not start (`begin_narration` refuses) |

No failure path shows a stack trace, provider payload, prompt, script text, audio
bytes, or error code to the user.

## Logging

- `@log_stage` on `generate_narration` and on the narration step entry point. The step
  logs its own outcome line where the decorator would mislabel a fallback as `outcome=ok`
  (as in Phase 4 and Phase 5).
- Log: `chat_id`, phase before and after, attempt number (1 or 2), outcome, duration,
  error type (typed class name or reason code), byte counts (`pcm_bytes`, `mp3_bytes`),
  and the drop lines (`narration_dropped`, `reason=stale_run|not_started`).
- Never log: the script text, the audio bytes (raw or encoded), base64, the prompt, the
  dossier, the answers, the provider response body, tokens, or API keys.
- Forbidden constructs remain forbidden: bare `except`, swallowed errors, recovered
  errors logged below `error`.

## Testing requirements (Red/Green TDD)

Tests are written first, confirmed red, then implemented to green. Use `scripts/test`
and `scripts/hooks`, never raw `pytest`, `ruff`, or `mypy`. Invoke the `write-tests`
skill before writing tests.

Every test uses a fake `NarrationClient`, a fake encoder (except the one fixture test),
and a fake Telegram sender that records the chat id, the method, and the order of
sends. No test imports `google.genai` or ADK, opens a socket, or reads `.env`.

The fixture is one small raw PCM file under `tests/fixtures/` (about 1 KB, a short
silent or tone clip at the expected rate). It is used only by the single encoder test.

Required cases:

- **Happy path:** portrait sent, then script text, then one synthesis, then one
  `send_voice` with the voice note bytes, in that order. `narration_started` is `True`.
  `in_flight` is `False`. Temp file removed. Exactly one synthesis call.
- **Ordering:** the voice note is sent after the script text (assert the send log).
- **Retry classes:** timeout, 429, and 5xx on attempt 1 → exactly 2 attempts, backoff
  of `RETRY_BACKOFF_SECONDS` between them (patched `asyncio.sleep`), voice note sent on
  success. The same error on both attempts → exactly 2 attempts, then the fallback.
- **Fail fast:** 400, 401, 403, and 404 → exactly 1 attempt, then the fallback.
- **Malformed reply:** empty bytes, oversize bytes, a wrong MIME type, a wrong sample
  rate, and a reply with no audio part → exactly 1 attempt, no retry, then the fallback.
  The reason code is logged and the bytes are not.
- **Encoder:** `bad_mp3` from the encoder → no retry, the fallback. A fixture test
  checks that `encode_mp3` on the fixture produces bytes that start with `ID3` or an MPEG
  frame sync, and that the result passes `VoiceNote`.
- **Temp file:** the file is in `tempfile.gettempdir()`, never under the repo root. It
  is removed after a successful send, after a failed send, after an encoder failure, and
  after `/restart`. A removal failure is logged at `error`; the step completes.
- **Voice send failure:** `send_voice` raises → exactly one `send_voice` call, one
  `NARRATION_UNAVAILABLE_TEXT`, the temp file removed, `end_narration` called.
- **Duplicate during synthesis:** a second text or photo while the synthesis call is
  blocked → `ONE_AT_A_TIME_TEXT`. The synthesis client is called exactly once.
- **Duplicate during delivery:** a second text or photo while `send_voice` is blocked →
  `ONE_AT_A_TIME_TEXT`. No second voice note. Exactly one `send_voice`.
- **After narration:** text and photo after `end_narration` → `ALREADY_DONE_TEXT`. No
  synthesis.
- **One-run guard:** `begin_narration` refuses a second call in the same run, and after
  a failure. The synthesis client is never called twice for one run.
- **`/restart` mid-synthesis:** the fake client blocks; `/restart` → reset and
  `OPENING_PROMPT`. Releasing the call drops the result: no send, no state change, an
  `info` log. A later photo starts a fresh interview.
- **`/restart` between encode and send:** no `send_voice` after the reset.
- **Script absent:** `script is None` → `begin_narration` refuses; no synthesis.
- **Script send failed (N13):** the text send raises; narration still runs once; the
  voice note is sent.
- **Input (N8):** the synthesis client receives the script text and the voice name only.
  It receives no dossier, no photo bytes, and no answers.
- **Cross-chat isolation:** chat A's voice note goes only to chat A while chat B is
  mid-interview. `/restart` in A leaves B's interview and answers intact.
- **Session:** `STATE_VERSION == 5`; a stored version `4` is discarded on read.
  `narration_started` is `False` by default. `begin_narration` and `end_narration` refuse
  a stale epoch and change nothing. `narration_started` is `True` only in `DELIVERING`
  with `script` set (`StateError` on violation). `reset` clears `narration_started`.
- **Seam:** `GeminiNarrationClient` maps status codes to typed errors through an injected
  transport. Creating it does not import `google.genai`. A reply with no audio part
  becomes `InvalidResponse`.
- **Configuration:** `MODELS["narrator"] == "gemini-3.1-flash-tts-preview"`. The
  Scripter, Bouncer, and Interviewer entries are unchanged. `NARRATOR_VOICE` is defined
  in `config.py`. The TTS model ID appears once in `config.py`.
- **Prompt:** `NARRATOR_INSTRUCTION` contains "speak with a posh British accent", a deep
  male voice description, and an Attenborough-style description. It contains no caption
  or Telegram instruction.
- **Copy:** `NARRATION_UNAVAILABLE_TEXT` is non-empty, names `/restart`, says the portrait
  and script stand, and contains no code, error type, provider wording, or stack-trace
  wording.
- **Logging:** with `caplog`, a full run logs chat id, phases, attempt numbers, outcome,
  and error types. It never logs the script text (a sentinel string), the audio bytes
  (a sentinel byte string), the prompt, the dossier, the answers, or keys.

## Repo hygiene

- One new dependency at most: the encoder in N3, and only after Tech Lead approval. Use
  `uv add`, and commit nothing. `uv.lock` changes only with that addition.
- `google-adk` and `python-telegram-bot` are reused. `google.genai` is already used by
  `converter_client.py`.
- Model IDs and the voice name stay in `config.py` only.
- Only `narrator_client.py` imports `google.genai` in Phase 6. Only `narrator_audio.py`
  imports the encoder library. No stage, step, router, or test imports either.
- Temp files live only in the system temp directory and are removed in `finally`. No
  audio is written inside the repo. `.env`, `.venv`, caches, and media stay untracked.
- Nothing is committed. The ROADMAP Phase 6 status is changed by the verifier, not by
  the build.

## Open decisions / assumptions

1. **Encoder (N3).** `lameenc` is the proposed encoder. Its licence, wheel availability
   for the target platform, and its maintenance status are checked in group 0. If the
   Tech Lead prefers an `ffmpeg` system binary, N3, the repo hygiene line, and the
   Docker and README notes change.
2. **TTS reply format (N4).** The expected MIME type and sample rate come from the
   installed `google.genai` types and must be confirmed in group 0. If the reply is not
   raw PCM, `TtsReply` and the encoder are changed before group 2.
3. **Voice name (N7).** The build uses one documented deep male prebuilt voice in
   `config.py`. The Tech Lead chooses the final voice after a live audition. Offline
   tests cannot judge the accent.
4. **Timeout 30 s (N2)** is a working value. Confirm it on a live 60-to-90-word sample.
5. **Narration after a failed script send (N13)** is a judgment call. The Tech Lead may
   prefer to skip narration in that case.
6. **Temp file (N16).** The brief says temp directory, so the temp file is used. PTB can
   send in-memory bytes, which would remove the temp file and match the Converter's
   pattern (Phase 4 D15). The Tech Lead may choose that instead. It would change N16 and
   the temp-file tests.
7. **Attempt budget (N1).** Read as 2 synthesis attempts in total, with no repair for
   malformed audio. A repair would be a second paid call on a reply that cannot be
   repaired by a prompt change, so none is planned.
8. **Live Telegram and Gemini checks are deferred** until real credentials are supplied,
   as in Phases 1 to 5. Offline validation must be complete and green regardless.
9. **Backward compatibility:** not a concern. `STATE_VERSION` goes to 5 (N9). No
   migration is written.
