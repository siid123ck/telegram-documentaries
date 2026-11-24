# Requirements — Scripter / Narration Text (Phase 5)

Spec folder: `SPECS/2026-11-24-scripter/`
Roadmap phase: **Phase 5 — Scripter (narration text)**
Branch: `feature/2026-11-24-scripter` (based on `main`, which includes the merged Phase 4 Converter, PR #4)

## Context

Phase 4 ends with the portrait sent and the chat resting in `DELIVERING`. Phase 5
adds the narration: one paragraph of dramatic wildlife-documentary text written from
the dossier. The paragraph is sent as a plain text message after the portrait and is
kept in session state so that the Narrator (Phase 6) can render it later.

The Phase 5 stop point is the script text being sent and stored. The chat then rests
in `DELIVERING`, as it does after Phase 4. The Narrator (TTS, voice notes) is not
started.

## User decisions (confirmed before this spec was written)

| Decision | Choice | Rationale |
|---|---|---|
| Model | `gemini-3.1-flash-lite`, with its ID defined once in the single config module (`config.py` `MODELS` mapping, key `scripter`) | TECH.md; the same model as the Bouncer and Interviewer |
| Only model-client modules import ADK | The Scripter's client module is a model-client module and imports ADK. The stage, router, and tests do not | TECH.md Architecture; D14 of the Converter spec |
| Input | The dossier from the Interviewer (quirks and suggested animal). No photo, no image, no answers | User decision |
| Output | Exactly one paragraph, 60 to 90 words, plain text, no markdown, third person, British RP wildlife-documentary narrator | User decision; TECH.md Prompts |
| Register | Frame the user's quirks, sleep habits, and snacking as funny animal behaviour, as if a documentary about humans | User decision |
| Grounding | Built only from the dossier. No invented facts | User decision; MISSION |
| Validation | A typed Pydantic model checks the word count and the single-paragraph shape. On failure, retry once with the validation error. Then fall back to the portrait-only path with a short apology | User decision; TECH.md Boundary contracts |
| Ordering | The script is sent as a Telegram text message after the portrait | User decision; MISSION step 6 |
| Resting phase | The chat rests in `DELIVERING`, as before. No new phase | User decision |
| Timeout and retry | Explicit timeout; max 2 attempts; retry only for timeout, 429, and 5xx; non-retryable fails fast. The same pattern as the Converter | User decision; TECH.md "External calls" |
| Storage | The script text is stored in session state for the Narrator | User decision |
| Logging | Log only ids, phase, word count, and error type. Never log the script text or the answers | User decision; MISSION privacy |
| Tests | The model is faked. No network | User decision; TECH.md Testing |
| Backward compatibility | Not needed | User decision |
| Out of scope | TTS, voice notes, the Narrator, editing or regenerating a script | User decision |

## Decisions beyond the user's list

These were not in the brief. Each is a choice made for this spec, listed so that the
Tech Lead can confirm or reject it.

| # | Decision | Value | Why |
|---|---|---|---|
| S1 | Attempt budget across validation and provider retries | **2 model attempts per run, in total.** A validation failure on attempt 1 triggers the repair on attempt 2. A timeout, 429, or 5xx on attempt 1 triggers a plain retry on attempt 2. Worst case is two paid calls, not four | "Retry once" and "max 2 attempts" are read as one shared budget. Keeps cost discipline (MISSION) |
| S2 | Repair note content | The repair note names the rule that failed (for example `word_count_low`, `word_count_high`, `multi_paragraph`, `markdown`) and the measured word count. It does **not** echo the failed script text | Avoids putting generated prose back into prompts and logs; the rule and count are enough for a correction |
| S3 | Where the script step runs | In `DELIVERING`, after the portrait is sent and `finish_delivery` has run. No await is placed between `finish_delivery` and `begin_script`. `in_flight` is `True` during the script step, so duplicates get `ONE_AT_A_TIME_TEXT` | No new phase (user decision). The in-flight guard covers the script step |
| S4 | Guard for the script step | The reset **epoch** (`SessionStore.epoch`, already used by Phase 4 and Phase 3), not the run token. The run token is cleared by `begin_delivery`, so it cannot guard this step. The script methods take the epoch and return `False` if the chat was reset | Reuses the existing stale-result mechanism; a `/restart` drops the script |
| S5 | Session schema | Bump `STATE_VERSION` from 3 to 4. Add `script: str \| None`. Invariant: `script` is set only in `DELIVERING`. A stored version 3 is discarded on read | Adds stored output; versioning rule (TECH.md) |
| S6 | New driver methods | `begin_script(chat_id, epoch) -> bool` (from `DELIVERING`, `script is None`, `in_flight is False`, epoch matches; sets `in_flight=True`). `commit_script(chat_id, epoch, text) -> bool` (from `DELIVERING`, `in_flight`, epoch matches; sets `script` and `in_flight=False`). `abandon_script(chat_id, epoch) -> bool` (epoch matches; sets `in_flight=False`) | Stages never write fields (TECH.md). Mirrors the Phase 4 methods |
| S7 | Output shape from the model | The model returns structured JSON with one string field, `script`, through a response schema. The stage validates it into the `Script` model. Prose is never scraped for the paragraph | TECH.md "Prefer schemas over regex" |
| S8 | Word count definition | `len(text.split())` on the stripped text. Whitespace-separated tokens; a hyphenated word counts as one; numbers count as words | One testable definition; the bounds are inclusive (60 and 90 pass; 59 and 91 fail) |
| S9 | Single-paragraph rule | After stripping, no `\n` or `\r` anywhere, and the text is non-empty | Makes "one paragraph" checkable in code |
| S10 | Markdown rule | Reject the text if it contains any of: `*`, `_`, `#`, `` ` ``, `~`, `[`, `]`, `<`, `>`, `\|`, or a backslash. Parentheses and apostrophes are allowed. This is a deny-list, checked in code | Makes "no markdown" checkable. The deny-list is conservative and names its characters in the test |
| S11 | Timeout | `SCRIPT_TIMEOUT_SECONDS = 10.0` per attempt, the same as the text calls (Bouncer and Interviewer) | Text generation is short; the Converter's 60 s is for images only |
| S12 | Retry backoff | The fixed short backoff used by `vision_client.py` (`RETRY_BACKOFF_SECONDS`) before a plain provider retry. No backoff before the validation repair | Same pattern as the Converter; the repair is a correction, not a wait for the provider |
| S13 | Script text Telegram send | Sent as plain text with no `parse_mode`, so no markup is interpreted | The script is plain text (user decision); no accidental Markdown rendering |
| S14 | Failure copy | `SCRIPT_FAILED_TEXT`: a short apology that says the portrait stands and names `/restart`. No code, error type, or provider wording | Same copy rules as `GENERATION_FAILED_TEXT` |
| S15 | Failure state | A script failure leaves the chat in `DELIVERING` with `script is None` and `in_flight` cleared. The portrait is not re-sent and the dossier is kept | The portrait is already sent (user decision); no new phase |
| S16 | Scripter input | The `Dossier` only. Not the photo, not the raw answers. The dossier's quirks, animal name, and animal reason are passed as the Interviewer stored them | User decision (dossier only); keeps raw answers out of this call |

## Scope

### In scope

1. **Script trigger.** After the portrait is sent and `finish_delivery` has run (S3),
   the Scripter runs once for the chat. It starts automatically. The user does not
   confirm.
2. **Scripter stage** (`scripter.py`). Takes the `Dossier` and returns a validated
   `Script`. It calls the model through the seam and never calls Telegram.
3. **Scripter seam** (`scripter_client.py`). A `ScriptClient` protocol with an
   ADK-backed implementation. It is the only Phase 5 module that imports ADK, and it
   builds the ADK objects lazily.
4. **Prompt** (`prompts.py`, `SCRIPTER_INSTRUCTION`). Asks for one paragraph of 60 to
   90 words, third person, British RP wildlife-documentary narration, built only from
   the dossier, framing the quirks as funny animal behaviour. The prompt states the
   word bounds and the no-markdown rule. Prompt text lives only in `prompts.py`.
5. **Storage.** The validated text is committed to `SessionState.script` (S6). It is
   not stored anywhere else and not written to disk.
6. **Delivery.** The script is sent with `send_message` as plain text (S13).
7. **Fallback.** Any Scripter failure after the retry budget (S1) sends
   `SCRIPT_FAILED_TEXT` (S14) and leaves the chat in `DELIVERING` (S15).
8. **Duplicate guard.** While the script step is in flight, any update except
   `/restart` gets `ONE_AT_A_TIME_TEXT`. No second script call starts.
9. **`/restart` in every phase.** `/restart` during the script step resets the chat
   (epoch bump). A late script result is dropped without a send and without a state
   change, and logged at `info` (S4).
10. **Typed errors.** Reuse `ProviderTimeout`, `RateLimited`, `VisionUnavailable`,
    `ProviderRejected`, and `InvalidResponse` from `errors.py`. The stage maps them to
    the fallback. Nothing else reaches the user.
11. **Copy** (`messages.py`). `SCRIPT_FAILED_TEXT`. Existing copy is reused:
    `ALREADY_DONE_TEXT`, `ONE_AT_A_TIME_TEXT`, `OPENING_PROMPT`.
12. **Structured logging** via `@log_stage` and `configure_logging()`, one logger per
    module.
13. **Red/Green TDD tests**, offline, with the model faked at the seam.
14. **README** updated: the script step, the stored script (for Phase 6), the
    fallback apology, and the resting `DELIVERING` phase.

### Out of scope (do not build)

- TTS, voice notes, `sendVoice`, OGG/Opus, and the Narrator (Phase 6).
- Editing, regenerating, or A/B-testing a script. The user cannot ask for a second
  script.
- Any new phase. `GENERATING` and `DELIVERING` are reused as before.
- Automatic checks of style, British spelling, third person, or factual grounding.
  These are prompt requirements and are reviewed by a human; code checks only the
  shape and length contract (S7 to S10).
- Persisting the script to disk or a database.
- A repair retry for the portrait (Phase 4, D8 unchanged).
- Any config toggle for word bounds, model, or timeout beyond the single config module
  and the named constants.
- `/start` behaviour changes (Phase 7 owns them).
- Localisation, rate limiting, quotas, or cost dashboards.
- Adding a dependency. Use the existing `google-adk` and `python-telegram-bot`.

## Phases and state

| Phase | Status |
|---|---|
| `IDLE` | declared, not driven (as before) |
| `AWAITING_PHOTO` | driven |
| `INTERVIEWING` | driven |
| `DOSSIER_READY` | driven |
| `GENERATING` | driven (Phase 4) |
| `DELIVERING` | driven (Phase 4); rests here after the script step (S3, S15) |

No new phase. Transitions added by this phase: none in the `Phase` enum. The script
step runs inside `DELIVERING` with `in_flight` set (S3, S6).

## Contracts (typed at the boundary)

### `Script` (Pydantic v2, `extra="forbid"`, strict)

- `script: str`: the paragraph text.
- Validation rules (each a named reason code, S8 to S10):
  - `empty`: after stripping, the text is empty.
  - `multi_paragraph`: the text contains `\n` or `\r`.
  - `word_count_low`: fewer than 60 words.
  - `word_count_high`: more than 90 words.
  - `markdown`: the text contains a character from the S10 deny-list.
- Failure raises a Pydantic `ValidationError`. The stage converts it to
  `InvalidResponse` carrying the reason code and the measured word count only, never
  the text.
- Nothing else. The model's raw reply is never stored.

### `ScriptClient` (protocol / seam)

`async def write(dossier: Dossier, *, repair_note: str | None = None) -> str`

Returns the raw `script` string from the model's JSON reply. The stage validates it
into `Script`. `repair_note` carries the S2 note on the repair attempt only.

`AdkScriptClient` implements the protocol:

- Built lazily. Creating the client does not import ADK or touch the network.
- Uses `MODELS["scripter"]` from `config.py`.
- Requests JSON output that matches a one-field response schema (S7).
- Applies `SCRIPT_TIMEOUT_SECONDS` (10 s, S11).
- Maps provider failures with the policy used by the Bouncer and Interviewer: 400,
  401, 403, 404 → `ProviderRejected`; 429 → `RateLimited`; 5xx and unknown →
  `VisionUnavailable`; timeout → `ProviderTimeout`. A reply that is not JSON with a
  string `script` field → `InvalidResponse`.
- Logs the error type and HTTP status only. Never the prompt, the dossier, or the
  response body.

### `SessionState` (version 4, S5)

- `schema_version: int`: `4`. A stored `3` is discarded on read.
- `phase: Phase`, `photo_file_id`, `answers`, `outstanding_question`, `dossier`,
  `in_flight`, `generation_run`: as in Phase 4.
- `script: str | None`: set only in `DELIVERING`, and only after `commit_script`.
  Cleared on reset.

Invariant: `script` is set only in `DELIVERING`. Violations raise `StateError`.

## Flow

1. **Portrait delivered** (Phase 4 path, unchanged): `begin_delivery` → `send_photo`
   → `finish_delivery`. The chat is in `DELIVERING` with `in_flight=False`.
2. **Script step starts** (same handler, no await between steps 1 and 2, S3): record
   the epoch; `begin_script(chat_id, epoch)`. If `False` (a reset or an in-flight
   step), stop and log at `info`.
3. **Generate with the attempt budget** (S1): `generate_script(chat_id, dossier,
   client, still_current)`:
   - Before every attempt, check `still_current()` (the epoch). If stale, stop with
     no model call (the paid-call guard, as in Phase 4).
   - Attempt 1: call `client.write(dossier)`, then validate into `Script`.
   - Validation failure on attempt 1 → attempt 2 with the S2 repair note.
   - Timeout, 429, or 5xx on attempt 1 → attempt 2 with no repair note, after the
     S12 backoff.
   - `ProviderRejected` on attempt 1 → no retry.
   - Any failure on attempt 2 → fallback (step 5).
4. **Commit and send.**
   a. `commit_script(chat_id, epoch, text)`. If `False` (a reset landed), log
      `stage_dropped` at `info`, send nothing, return.
   b. `send_message(chat_id, text)`, plain text (S13).
   c. If the send raises, log the error type at `error`, then try the fallback apology
      once. Do not retry the send.
5. **Fallback.** `abandon_script(chat_id, epoch)`. If the epoch is current, send
   `SCRIPT_FAILED_TEXT`. Log the error type (typed error name or reason code) at
   `error`. Phase stays `DELIVERING`; `script` stays `None` (S15).
6. **`/restart` in any phase** → `reset` → `OPENING_PROMPT` (unchanged from Phase 4).
   Bypasses the in-flight guard. A late script result is dropped (step 4a).

## Copy

- `SCRIPT_FAILED_TEXT`: one short apology. It must say the portrait stands and name
  `/restart`. Example: "The narration crew has wandered off. Your portrait stands;
  send /restart for a new subject." It contains no code, error type, provider
  wording, or stack trace.
- The script is sent as plain text, with the model's paragraph as the whole message.
  No header and no caption. (The user decision is "sent as a text message".)

Existing copy reused: `ALREADY_DONE_TEXT`, `ONE_AT_A_TIME_TEXT`, `OPENING_PROMPT`.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| Script validation fails on attempt 1, passes on attempt 2 | Script sent; stored; `DELIVERING` |
| Script validation fails on both attempts | `SCRIPT_FAILED_TEXT`; `script` stays `None`; `DELIVERING`; logged at `error` with reason code and word count |
| Timeout, 429, or 5xx on both attempts | Same apology; logged at `error` with the typed error name |
| Timeout, 429, or 5xx on attempt 1, valid on attempt 2 | Script sent; stored |
| 400, 401, 403, 404 (`ProviderRejected`) | One attempt; same apology |
| Reply is not JSON, or has no string `script` | `InvalidResponse`; treated as a validation failure (one repair attempt, S1) |
| Telegram `send_message` of the script fails | Logged at `error`; one apology attempt; no retry of the script send; `script` is already stored (the Narrator can use it) |
| `/restart` during the script call | Reset wins; late result dropped; `OPENING_PROMPT` sent; logged at `info` |
| `/restart` between commit and send | Reset clears `script`; the send is skipped only if the epoch check is made before it (step 4a). The race after commit is accepted, as with D18 in Phase 4 |
| Duplicate text or photo during the script step | `ONE_AT_A_TIME_TEXT`; no state change; no model call |
| Text or photo after the script step (`in_flight=False`) | `ALREADY_DONE_TEXT`; no state change |
| Unexpected exception in the handler | Caught and logged at `error`; the chat is left in `DELIVERING` with `in_flight` cleared (the handler's `finally` abandons the script step) |

No failure path shows a stack trace, provider payload, prompt text, script text,
answers, or error code.

## Logging

- `@log_stage` on `generate_script` and on the stage entry point. As in Phase 4,
  `run_script_step` logs its own outcome line where the decorator would mislabel a
  fallback as `outcome=ok`.
- Log: `chat_id`, phase before and after, attempt number (1 or 2), outcome, duration,
  error type (typed error class name or validation reason code), word count of the
  measured text (count only), and the epoch-drop line.
- Never log: the script text, the answers, the quirks, the animal reason, the prompt,
  the repair note text, the dossier, tokens, or API keys.
- Forbidden constructs remain forbidden: bare `except`, swallowed errors, recovered
  errors logged below `error`.

## Testing requirements (Red/Green TDD)

Tests are written first, confirmed red, then implemented to green. Use `scripts/test`
and `scripts/hooks`, never raw pytest, ruff, or mypy. Invoke the `write-tests` skill
before writing tests.

Every test uses the fake `ScriptClient`, a fake `Downloader` only where a photo path is
involved, and a fake Telegram sender. No test imports ADK, opens a socket, or reads
`.env`.

Required cases:

- **Happy path:** portrait sent, then one script call, then one `send_message` with
  the exact script text after the portrait send. `script` is stored. Phase `DELIVERING`,
  `in_flight=False`. Exactly one script call.
- **Word count:** 59 words → `word_count_low`; 60 → accepted; 90 → accepted; 91 →
  `word_count_high`. Each rejected case gets exactly one repair call with the S2 note,
  and the note contains the rule name and the count, not the text.
- **Shape:** a text with a newline (`multi_paragraph`); a blank or whitespace-only text
  (`empty`).
- **Markdown:** each character in the S10 deny-list is rejected (parametrised);
  asterisk bold, underscore italics, a heading, a bullet list, and a link are each
  rejected. Parentheses and apostrophes are accepted.
- **Retry then fallback:** a validation failure on both attempts → exactly 2 calls,
  apology sent, `script is None`, phase `DELIVERING`, portrait not re-sent.
- **Provider error:** timeout, 429, and 5xx each → exactly 2 attempts, then apology;
  a timeout on attempt 1 and a valid script on attempt 2 → script sent. 400, 401, 403,
  and 404 → exactly 1 attempt, then apology.
- **Reply shape:** a non-JSON reply and a JSON reply without `script` each → treated as
  a validation failure (`InvalidResponse`), with the repair attempt.
- **`/restart` mid-script:** the fake client blocks; `/restart` resets and sends
  `OPENING_PROMPT`; releasing the call drops the result (no send, no state change,
  `info` log). A later photo starts a fresh interview.
- **Duplicate during the script step:** a second text or photo while the call is
  blocked → `ONE_AT_A_TIME_TEXT`; the script client is called exactly once.
- **After the script step:** text and photo in `DELIVERING` → `ALREADY_DONE_TEXT`; no
  script call.
- **Cross-chat isolation:** chat A gets a script while chat B is mid-interview; each
  chat's script goes only to that chat (chat id asserted on the send). `/restart` in A
  leaves B's interview and answers intact.
- **Session:** `schema_version` `3` is discarded on read; `script` is set only in
  `DELIVERING` (invariant, `StateError` on violation); `begin_script` refuses a
  second call and a stale epoch; `commit_script` refuses a stale epoch and changes
  nothing; `abandon_script` clears `in_flight` only; `reset` clears `script`.
- **Seam:** `AdkScriptClient` maps status codes to typed errors through an injected
  transport; creating it does not import ADK; a reply that is not the expected JSON
  becomes `InvalidResponse`.
- **Configuration:** `MODELS["scripter"]` equals `gemini-3.1-flash-lite`, and the
  Bouncer and Interviewer entries are unchanged.
- **Prompt:** `SCRIPTER_INSTRUCTION` states the 60 to 90 word bound, the single
  paragraph, the no-markdown rule, third person, British RP, and the dossier-only
  grounding. It contains no caption or Telegram instructions.
- **Logging:** with `caplog`, a full run logs chat id, phases, attempt numbers,
  outcome, and error types. It never logs the script text (a sentinel paragraph in the
  fake), the answers, the quirks, the reason, the prompt, the dossier, or keys.

## Repo hygiene

- No new dependency. `google-adk` and `python-telegram-bot` are reused.
- Model IDs stay in `config.py` only. `gemini-3.1-flash-lite` is already defined there
  as `_FLASH_LITE`; the Scripter reuses that constant through `MODELS["scripter"]`.
- Only `scripter_client.py` (with the existing `vision_client.py` and
  `interview_client.py`) imports ADK. No stage, router, or test imports ADK.
- No temp files, no media, no script written to disk. `.env`, `.venv`, and caches stay
  untracked.
- Nothing is committed. The ROADMAP Phase 5 status is changed by the verifier, not by
  the build.

## Open decisions / assumptions

1. **Attempt budget (S1).** Read as one shared budget of 2 model attempts. If the
   Tech Lead wants the repair to be a separate retry (up to four paid calls), S1 must
   change, and the cost note in TECH.md must follow.
2. **Markdown deny-list (S10)** is a working list. It may need a parenthesis or quote
   rule later; it is a conservative first cut.
3. **Word count definition (S8)** is whitespace tokens. Hyphenated words and numerals
   count as one word each.
4. **Timeout 10 s (S11)** is a working value, matching the text calls.
5. **Style and grounding are not machine-checked.** British spelling, third person, and
   "no invented facts" depend on the prompt and on human review of sample output. The
   Tech Lead must review a few live samples before merge.
6. **Epoch as the guard (S4).** The script step uses the reset epoch, not a run token,
   because `begin_delivery` clears the run token. If the Tech Lead prefers a token that
   survives into `DELIVERING`, that changes the D13 invariant and must be decided
   first.
7. **Live Telegram and Gemini checks are deferred** until real credentials are supplied,
   as in Phases 1 to 4. Offline validation must be complete and green regardless.
8. **Backward compatibility:** not a concern. `STATE_VERSION` goes to 4 (S5); no
   migration is written.
