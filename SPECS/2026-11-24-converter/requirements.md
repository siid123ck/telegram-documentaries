# Requirements — Converter / Hybrid Portrait (Phase 4)

Spec folder: `SPECS/2026-11-24-converter/`
Roadmap phase: **Phase 4 — Converter (hybrid portrait)**
Branch: `feature/2026-11-24-converter` (based on `main`, which includes the merged Phase 1 gateway, Phase 2 Bouncer, and Phase 3 Interviewer, PR #3)

## Context

Phase 3 ends with a dossier (quirks plus a suggested animal) and the chat in
`DOSSIER_READY`. Phase 4 turns that dossier and the user's original photo into
one hybrid animal portrait and sends it to the chat with a two-line caption. This
is the payoff image of the joke.

The Phase 4 stop point is the portrait being sent. The chat then waits for
`/restart`. The Scripter (Phase 5) and Narrator (Phase 6) are not started. The
state machine gains two driven phases: `GENERATING` for the generation step and
`DELIVERING` for the send step.

## User decisions (confirmed before this spec was written)

| Decision | Choice | Rationale |
|---|---|---|
| Model | `gemini-3.1-flash-image`, with its ID defined once in the single config module (`config.py` `MODELS` mapping, key `converter`) | TECH.md; one-line change for model swaps |
| Inputs | The user's original photo plus the dossier (quirks and suggested animal) | MISSION step 5 |
| Output | One hybrid animal portrait image | ROADMAP Phase 4 |
| Likeness | Recognisable features kept, stylisation allowed. **Point to confirm:** the Tech Lead answered "fine" to a stylised-vs-recognisable question; this wording is recorded from that answer and must be confirmed explicitly (see Open decisions) | ROADMAP Phase 4 says "preserves the user's recognisable features" |
| Caption | Two lines: a short line naming the suggested animal, then a short second line. Short, playful documentary tone | User decision |
| Failure | Apologise in one short message, keep the interview answers and dossier, offer `/restart`. Never show stack traces or provider payloads | ROADMAP Phase 4; TECH.md degradation policy |
| Stop point | Phase 4 ends after the portrait is sent. The chat then waits for `/restart`. The Scripter is not started | User decision |
| Phases | Add `GENERATING` (generation step) and `DELIVERING` (send step) as driven phases | User decision; TECH.md state machine |
| Image handling | Bytes are sent straight from memory. No temp file | User decision; removes the temp-file cleanup from ROADMAP Phase 4 |
| Logging | Never log image bytes, base64, or answers | MISSION privacy; TECH.md logging |
| Generation count | Exactly one portrait generation per successful run. A duplicate update while generation is in flight gets the one-at-a-time nudge; no second generation starts | MISSION cost discipline |
| Timeouts / retries | Explicit timeout; max 2 attempts; retry only for timeout, 429, and 5xx; non-retryable fails fast | TECH.md "External calls" |
| Model seam | A protocol with an ADK implementation, the same pattern as `vision_client.py` and `interview_client.py`. Only model-client modules import ADK | User decision; TECH.md |
| Tests | The model is faked; no network | TECH.md Testing |
| Backward compatibility | Not needed | User decision |
| Out of scope | The Scripter, the Narrator, editing or regenerating portraits | User decision |

## Decisions beyond the user's list

These were not in the brief. Each one is a choice made for this spec and is
listed here so the Tech Lead can confirm or reject it.

| # | Decision | Value | Why |
|---|---|---|---|
| D1 | Resting phase after delivery | `DELIVERING` stays the resting phase after the portrait is sent. Text or photos in `DELIVERING` get `ALREADY_DONE_TEXT`. Phases 5 and 6 extend from here | The user asked for the chat to wait for `/restart` and for `DELIVERING` to be the send step. No new phase is added for "done" |
| D2 | Phase on failure | `GENERATING` returns to `DOSSIER_READY` on any generation failure. The dossier is kept, as the brief asks | Dossier invariant (`dossier` set exactly in `DOSSIER_READY`) stays true without a new phase |
| D3 | `/restart` during generation | `/restart` bypasses the in-flight guard and resets at once. The in-flight result is discarded when it returns. The MISSION rule ("`/restart` at any point, mid-generation") wins over the interview guard | MISSION success criteria |
| D4 | How a stale result is discarded | Each generation gets a run token stored in session state. A result is committed only if the token still matches. The token is cleared on reset | Stops a late portrait from reaching a restarted chat, or reaching a newer run |
| D5 | Concurrent update processing | The PTB application is built with concurrent updates enabled. PTB's default is `max_concurrent_updates=1`, which serialises updates, so as built a `/restart` or duplicate would wait behind a 60 s generation. Enabling concurrency is part of this phase | Without it, D3 and the duplicate guard cannot work in production. Touches the Phase 2 and 3 handlers too; the change is one builder setting |
| D6 | Where the original photo bytes come from | Re-downloaded by `file_id` at generation time through the existing `Downloader`. The session keeps only the `file_id`, as today. Not stored in memory across turns | Keeps the session free of image bytes; `/restart` purge stays a reference purge. Cost: one extra Telegram download per run. Alternative (store bytes in session) is not chosen |
| D7 | Image payload validation | Non-empty bytes, size at most 10 MB (`MAX_PHOTO_BYTES`), and a PNG or JPEG magic-byte check. The provider's declared MIME type is not trusted on its own | A text-only or non-image reply must not be sent to Telegram |
| D8 | Repair retry for image payloads | **No** repair retry. A payload that fails D7 is `InvalidResponse`, which fails the run with the apology. TECH.md's repair rule is for structured-output stages; a repair here would be a second paid generation | Cost discipline (MISSION); a repair prompt cannot fix a missing image |
| D9 | Generation timeout | 60 s per attempt (`REQUEST_TIMEOUT_SECONDS`). Image generation is slower than the 10 s text calls | TECH.md requires an explicit timeout but names no value. Needs confirmation |
| D10 | Generation retry bound | Max 2 attempts, as TECH.md. A retry after a timeout, 429, or 5xx may cost a second paid call; this is accepted because the run is only successful once | TECH.md |
| D11 | Caption content | Built from a fixed template and the sanitised animal name (`render_subject`). The model's `reason` and any model prose are not used in the caption | The caption is user-visible and model prose is not trusted for it. Only the name is shown |
| D12 | Dossier text before the portrait | The dossier text message (Phase 3) is still sent at completion, then generation starts. The portrait follows as a separate photo message | Keeps Phase 3 behaviour; the dossier is the Phase 3 output |
| D13 | Session schema | Bump `STATE_VERSION` from 2 to 3. Add `generation_run: str \| None`. Invariant: `generation_run` is set exactly when `phase` is `GENERATING`. The `dossier` invariant now covers `DOSSIER_READY`, `GENERATING`, and `DELIVERING` | Adds driven phases and the run token. A v2 state is discarded on read, as the versioning rule says |
| D14 | Model-client import rule | Wording becomes: only the model-client modules (`vision_client.py`, `interview_client.py`, `converter_client.py`) import ADK. This follows the Tech Lead's option (a) from the Phase 3 verifier note | `converter_client.py` is the third model-client module. The Phase 3 wording is still open for the Tech Lead |
| D15 | Portrait is not stored | The image bytes exist only in memory for the send call. They are not kept in session state, so nothing needs purging on `/restart` beyond the run token | Matches "bytes straight from memory"; no temp files |
| D16 | Second caption line | Fixed copy in `messages.py`, not per-animal. Example: "Filmed in its natural habitat, unaware it is being watched." | Keeps the caption bounded and on-tone without model prose |
| D17 | Edited or noise updates during generation | Ignored as today (no reply), so the nudge is not spammed | Matches Phase 3 routing |
| D18 | Delivery order | Check the run token and move to `DELIVERING` **before** `send_photo`; roll back to `DOSSIER_READY` if the send raises. A `/restart` that lands during the send cannot recall a photo already sent; this one race is accepted and logged | Commit-after-send would let a restarted chat receive a portrait from a dead run. Commit-before-send with rollback keeps the phase honest in both failure modes |

## Tech Lead decisions (recorded 2026-11-24, verifier pass)

The Tech Lead's decisions for this phase are recorded here and cross-referenced to
the D-numbers above. Each is in code, in the spec, and in TECH.md or README.

| Decision | Recorded as | Where it is enforced |
|---|---|---|
| Concurrent update processing (D5) | `concurrent_updates(True)` in `app.py`. Its effect on the Phase 2 and 3 handlers is recorded | `app.py`; `test_main.py`; README "Known limitations"; TECH "State machine" |
| 60 s generation timeout (D9) | `REQUEST_TIMEOUT_SECONDS = 60.0` | `converter_client.py`; TECH "External calls" |
| Likeness: recognisable features kept, stylisation allowed (Open decision 1) | `CONVERTER_INSTRUCTION` in `prompts.py` | `prompts.py`; `test_prompts.py`; README "The portrait" |
| Failure returns the chat to `DOSSIER_READY` (D2) | `abandon_generation` and `fail_delivery`; answers and dossier kept | `state.py`; `generation.py`; `test_generation.py` |
| Caption template (D11, D16) | Line 1 is `Specimen: the <sanitised animal name>.`; line 2 is fixed copy; no model prose | `messages.py` `render_caption`; `test_messages.py` |
| Paid-call guard (D10, plus the duplicate guard) | `begin_generation` refuses a second run. The converter checks the run token before every model call, including the retry, so a `/restart` makes no further paid call. Max 2 attempts, retry only for timeout, 429, and 5xx | `state.py`; `converter.py` (`still_current`); `test_converter.py`; `test_generation.py` |
| Outcome logging for a failed run (logging) | One `stage_end stage=run_generation outcome=error error_type=...` line at `error`. A stale run logs `stage_dropped` at `info`. A successful run logs `outcome=ok` | `generation.py` `_log_run_end`; `test_portrait_logging.py` |

**Still open for the Tech Lead:** D1, D3, D4, D6, D7, D8, D12, D13, D14, D15, D17,
and D18 were not in this round's list. They stay as proposed until confirmed or
revised.

## Scope

### In scope

1. **Generation trigger** — when the Phase 3 interview completes (`DOSSIER_READY`),
   the dossier text is sent (D12), then generation starts automatically
   (`DOSSIER_READY → GENERATING`). The user does not confirm.
2. **Converter stage** — a stage module takes the original photo bytes and the
   validated `Dossier`, and returns a validated `Portrait` (PNG or JPEG bytes).
   It calls the model through the seam and never calls Telegram.
3. **Converter seam** — a `PortraitClient` protocol with an ADK-backed
   implementation `AdkPortraitClient`. It is the only Phase 4 module that imports
   ADK, and it builds the ADK objects lazily.
4. **Prompt** — the converter instruction lives in `prompts.py`. It asks for one
   hybrid animal portrait that keeps the user's recognisable features and fuses
   the suggested animal and the quirks as visible traits. It contains no
   instructions about the caption.
5. **Delivery** — the portrait is sent with `send_photo` from an in-memory buffer
   (`BytesIO`). The caption is the two-line template (D11, D16). Phase goes
   `GENERATING → DELIVERING` after a successful send.
6. **Failure path** — any generation failure (after bounded retry) or invalid
   payload sends the apology with `/restart` and returns the phase to
   `DOSSIER_READY` (D2). Answers and dossier are kept. No state change other than
   the phase rollback and the cleared run token.
7. **Duplicate guard** — while a run is in flight for a chat, any update except
   `/restart` gets `ONE_AT_A_TIME_TEXT`. No second generation starts. Updates that
   Phase 3 routes to `ALREADY_DONE_TEXT` in `DOSSIER_READY` and `DELIVERING` keep
   that reply when no run is in flight.
8. **`/restart` in every phase** — including `GENERATING` (D3). It resets the chat,
   clears the run token, sends `OPENING_PROMPT`, and returns to `AWAITING_PHOTO`.
   A late result for the old run is dropped without a send and with a log line
   that contains no bytes.
9. **Concurrent update processing** (D5) — the application processes updates
   concurrently so that (7) and (8) work in production.
10. **Typed errors** — reuse `ProviderTimeout`, `RateLimited`, `VisionUnavailable`,
    `ProviderRejected`, and `InvalidResponse` from `errors.py`, or add a
    `GenerationError` base in the same module. The routing layer maps them to the
    apology. Nothing else reaches the user.
11. **Copy** — `messages.py` gains `GENERATION_FAILED_TEXT`, the caption
    templates, and a `render_caption(animal_name)` function. Copy is tested for
    absence of forbidden wording.
12. **Structured logging** via `@log_stage` and `configure_logging()`. One logger
    per module.
13. **Red/Green TDD tests** — offline, with the model faked at the seam.
14. **README** updated for the generation step, the new phases, the caption, and
    the `/restart` behaviour during generation.

### Out of scope (do not build)

- The Scripter (Phase 5), the Narrator and TTS (Phase 6), and any `voice` send.
- Editing, regenerating, or A/B-testing a portrait. The user cannot ask for a second
  image.
- Storing the portrait or the original photo bytes in session state or on disk.
- Temp files of any kind (no `tempfile`, no writes under the repo or `/tmp`).
- Video, GIF, or animation output.
- A repair retry for image payloads (D8).
- Any config toggle for image size, style, or model beyond the single config module.
- Persistence, databases, webhooks, or an inbound HTTP server.
- `/start` behaviour changes (Phase 7 owns them).
- Localisation.
- Rate limiting, quotas, or cost dashboards.
- Adding a dependency. Use the existing `google-adk` and `python-telegram-bot`.

## Phases and state

The phase set after this spec:

| Phase | Status |
|---|---|
| `IDLE` | declared, not driven (as before) |
| `AWAITING_PHOTO` | driven |
| `INTERVIEWING` | driven |
| `DOSSIER_READY` | driven; generation starts from here |
| `GENERATING` | **driven** (new): generation in flight; `generation_run` set |
| `DELIVERING` | **driven** (new): portrait sent; resting phase until Phase 5 (D1) |

Transitions added by this phase:

- `DOSSIER_READY → GENERATING` (begin run, set `generation_run`, `in_flight=True`)
- `GENERATING → DELIVERING` (`begin_delivery`, before the send; clears
  `generation_run`; keep dossier)
- `DELIVERING → DOSSIER_READY` (`fail_delivery`, when `send_photo` raises)
- `GENERATING → DOSSIER_READY` (`abandon_generation`, any generation failure;
  clears `in_flight` and `generation_run`; keep dossier and answers)
- Any phase → `AWAITING_PHOTO` via `/restart` (reset; an in-flight run is abandoned)

`DELIVERING` has no outgoing transition in this phase except `/restart`.

## Contracts (typed at the boundary)

### `Portrait` (Pydantic v2, `extra="forbid"`, strict)

- `image_bytes: bytes` — required, at least 1 byte, at most `MAX_PHOTO_BYTES`
  (10 MB). Must start with the PNG or JPEG magic bytes (D7).
- `mime_type: Literal["image/png", "image/jpeg"]` — derived from the magic bytes,
  not trusted from the provider.

Nothing else. The portrait is never stored in session (D15).

### `PortraitClient` (protocol / seam)

`async def render(photo_bytes: bytes, dossier: Dossier, *, repair_note: str | None = None) -> bytes`

Returns the raw generated image bytes. The stage validates them into `Portrait`.
`repair_note` is part of the signature only for symmetry with the other seams;
the stage never passes it in this phase (D8).

`AdkPortraitClient` implements the protocol:

- Built lazily; creating the client does not import ADK or touch the network.
- Uses `MODELS["converter"]` from `config.py`.
- Requests image output from the model (`response_modalities` IMAGE, or the ADK
  equivalent). The exact mechanism is the Phase 4 spike (plan, group 3).
- Applies `REQUEST_TIMEOUT_SECONDS` (60 s, D9).
- Maps provider failures with the same policy as the Bouncer and the Interviewer:
  400, 401, 403, 404 → `ProviderRejected`; 429 → `RateLimited`; 5xx and unknown
  → `VisionUnavailable`; timeout → `ProviderTimeout`.
- Logs the error type and HTTP status only. Never the prompt, the photo, or the
  response body.

### `SessionState` (version 3, D13)

- `schema_version: int` — `3`. A stored `2` is discarded on read.
- `phase: Phase`
- `photo_file_id: str | None`
- `answers: list[Answer]`
- `outstanding_question: str | None` — set only in `INTERVIEWING`.
- `dossier: Dossier | None` — set exactly in `DOSSIER_READY`, `GENERATING`, and
  `DELIVERING`.
- `in_flight: bool`
- `generation_run: str | None` — set exactly in `GENERATING` (D4, D13). An opaque
  random id (`uuid4().hex`). It holds no user data.

### `SessionStore` additions

Stages still never write fields directly. New driver methods:

- `begin_generation(chat_id) -> str | None` — from `DOSSIER_READY` only. Sets
  `phase=GENERATING`, `in_flight=True`, and a new `generation_run`. Returns the
  token, or `None` if a run is already in flight.
- `begin_delivery(chat_id, run) -> bool` — from `GENERATING` only, and only if
  `run` matches `generation_run`. Sets `phase=DELIVERING`, `generation_run=None`;
  `in_flight` stays `True` during the send. Returns `False` for a stale token;
  nothing changes.
- `finish_delivery(chat_id) -> None` — from `DELIVERING` only. Sets
  `in_flight=False`. The chat rests in `DELIVERING` (D1).
- `fail_delivery(chat_id) -> None` — from `DELIVERING` only. Sets
  `phase=DOSSIER_READY`, `in_flight=False`. Used when `send_photo` raises.
- `abandon_generation(chat_id, run) -> bool` — from `GENERATING` only, and only if
  `run` matches. Sets `phase=DOSSIER_READY`, `in_flight=False`,
  `generation_run=None`. Returns `False` for a stale token.

Each method that takes a `run` returns `False` when the chat has been reset or
restarted, so a late result cannot change a newer state.
- `reset(chat_id)` — unchanged in shape; clears everything including
  `generation_run`.

A stale token means the run was abandoned by `/restart`. The orchestrator drops
the result and logs at `info`.

## Flow

1. **Interview completes** (Phase 3 path): `commit_dossier` → `DOSSIER_READY`;
   send the dossier text (D12).
2. **Generation starts** (same handler, awaited): `begin_generation` → token.
   If `None` (already in flight), reply `ONE_AT_A_TIME_TEXT` and stop. This should
   not happen on the normal path because the interview clears `in_flight` first.
3. Download the original photo by `photo_file_id` (D6) through `Downloader`.
4. Call the converter stage: `generate_portrait(chat_id, photo_bytes, dossier, client)`.
   - Validation failure (D7) or provider failure (after bounded retry) → go to
     step 6.
5. Delivery, in this order (D18):
   a. `begin_delivery(chat_id, run)`. If `False` (a `/restart` happened during the
      call), log `info`, send nothing, return. This check is the last point at
      which a restart can stop the send.
   b. `send_photo` with the in-memory buffer and the caption.
   c. On success, `finish_delivery` → phase `DELIVERING`, `in_flight=False`.
   d. On a Telegram failure, `fail_delivery` → `DOSSIER_READY`, then send
      `GENERATION_FAILED_TEXT`. Log the error type at `error`.
6. Generation failure (provider, payload, or download): `abandon_generation(chat_id,
   run)`. If the token is current, send `GENERATION_FAILED_TEXT`. Phase is
   `DOSSIER_READY`. Log the error type at `error`.
7. **`/restart` in any phase** → `reset` → `OPENING_PROMPT`. Bypasses the in-flight
   guard (D3).

## Caption and copy

Caption (two lines, built by `render_caption(animal_name)`):

```
Specimen: the <sanitised animal name>.
Filmed in its natural habitat, unaware it is being watched.
```

- Line 1 uses `render_subject(name)` from `messages.py`, so markup and
  injected text are removed and the length is capped.
- Line 2 is fixed copy (D16).
- Total caption length stays well under Telegram's 1024-character limit.

Copy added to `messages.py`:

- `GENERATION_FAILED_TEXT` — one short apology that names `/restart`. Example:
  "Sorry — the portrait studio jammed on that one. Send /restart to try again."
  No code, error type, or provider wording.
- The caption template and `render_caption`.

Existing copy reused: `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, `ONE_AT_A_TIME_TEXT`.

The tone is investigative and playful, as in Phase 3. The caption claims nothing
beyond the animal name.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| Dossier sent, generation starts | Normal path |
| Download of the original photo fails | Treated as `VisionUnavailable`-class: bounded retry is not applied to the download in this phase; send `GENERATION_FAILED_TEXT`; `DOSSIER_READY`; logged at `error` |
| `ProviderTimeout` / `RateLimited` / `VisionUnavailable` after 2 attempts | `GENERATION_FAILED_TEXT`; `DOSSIER_READY`; logged at `error` |
| `ProviderRejected` (400, 401, 403, 404) | One attempt; same apology; logged at `error` |
| Image payload fails D7 (no image, wrong magic bytes, empty, too large) | `InvalidResponse`; no repair (D8); same apology; logged at `error` |
| Telegram `send_photo` fails | Same apology is sent if the chat is still current; phase `DOSSIER_READY`; logged at `error`. If the apology also fails, log at `error` and stop |
| `/restart` during generation | Reset wins; the late result is dropped; `OPENING_PROMPT` sent; logged at `info` |
| Duplicate update during generation | `ONE_AT_A_TIME_TEXT`; no state change; no model call |
| Any update during `DELIVERING` (no run in flight) | `ALREADY_DONE_TEXT`; no state change (D1) |
| Photo during `GENERATING` or `DELIVERING` | Same as above (the in-flight guard covers `GENERATING`; `ALREADY_DONE_TEXT` covers `DELIVERING`) |
| Unexpected exception in the handler | Caught and logged at `error`; the next update is still served; the chat is left in `DOSSIER_READY` if it was in `GENERATING` (the handler's `finally` abandons the run) |

No failure path shows a stack trace, provider payload, prompt text, image data,
or error code.

## Logging

- `@log_stage` on the converter stage, the generation orchestrator, and the
  delivery call.
- Log `chat_id`, phase before and after, outcome, duration, error type, and the
  animal `name` (as the Interviewer does). Log the run token only as a short
  prefix if needed for correlation.
- Never log: the image bytes, base64, the caption if it contains a name the user
  supplied (the name is model-derived, so log it at most once, as the Interviewer
  does), answers, quirks, reasons, prompts, tokens, or API keys.
- Forbidden constructs remain forbidden: bare `except`, swallowed errors, recovered
  errors logged below `error`.

## Testing requirements (Red/Green TDD)

Tests are written first, confirmed red, then implemented to green. Use
`scripts/test` and `scripts/hooks`, never raw pytest, ruff, or mypy. Invoke the
`write-tests` skill before writing tests.

Every test uses the fake `PortraitClient`, a fake `Downloader`, and a fake
Telegram sender. No test imports ADK, opens a socket, or reads `.env`.

Required cases:

- **Happy path:** dossier → `GENERATING` → one generation → one `send_photo` with
  the two-line caption naming the animal → `DELIVERING`. Exactly one fake
  generation call.
- **Wrong payload during generation:** a text-only reply, wrong magic bytes, empty
  bytes, and oversize bytes each → no send, no repair call, apology, `DOSSIER_READY`,
  answers and dossier kept, no bytes in logs.
- **`/restart` mid-generation:** the fake client blocks; `/restart` → reset and
  `OPENING_PROMPT` sent, phase `AWAITING_PHOTO`; then the blocked call returns →
  no `send_photo`, no state change, `info` log. A new photo afterwards starts a
  fresh interview.
- **Provider error:** timeout, 429, and 5xx each attempted exactly 2 times
  (fake counts attempts) then apology; 400/401/403/404 attempted once; the answers
  and dossier survive; `/restart` is offered.
- **Duplicate update:** a second text or photo during an in-flight generation →
  `ONE_AT_A_TIME_TEXT`; the fake generation client is called exactly once.
- **Cross-chat isolation:** chat A and chat B interleaved; A generates while B is
  mid-interview; each chat gets only its own portrait and caption; `/restart` in A
  leaves B's run and state intact.
- **Caption:** two lines; line 1 names the animal; markup, newlines, and
  injected text in the animal name are removed; length capped.
- **Wrong payloads outside generation:** text in `DELIVERING` →
  `ALREADY_DONE_TEXT`; photo in `DELIVERING` → `ALREADY_DONE_TEXT`; sticker and
  edited messages → no reply, no state change.
- **Session:** `schema_version` `2` is discarded on read; `generation_run` is set
  exactly in `GENERATING`; `begin_generation` refuses a second run; a stale token
  is refused by `begin_delivery` and `abandon_generation` (the name `commit_portrait_sent` in an earlier draft does not exist; corrected by the verifier).
- **Seam:** `AdkPortraitClient` maps status codes to typed errors through an
  injected transport, never the network; creating it does not import ADK.
- **Configuration:** `gemini-3.1-flash-image` appears exactly once in `config.py`;
  `MODELS["converter"]` is that value.
- **Concurrency wiring:** the application builder enables concurrent updates
  (D5), asserted without starting the bot.
- **Logging:** with `caplog`, a full run logs chat id, phases, outcome, and animal
  name, and never image bytes, base64, answer text, quirk text, reason text, or
  keys.

## Repo hygiene

- No new dependency. The existing `google-adk` and `python-telegram-bot` are used.
  If the spike shows ADK cannot return image output, stop and report (plan group
  3). Do not add a library silently.
- Model IDs stay in `config.py` only.
- No temp files; no media in the repo tree. `.env`, `.venv`, caches, and generated
  media stay untracked and ignored.

## Open decisions / assumptions

1. **Likeness wording (point to confirm).** The Tech Lead answered "fine" to a
   stylised-vs-recognisable question. This spec records **"recognisable features
   kept, stylisation allowed"** from that answer. The Tech Lead must confirm this
   explicitly before merge. The prompt in `prompts.py` is written to this wording
   and will need changing if the answer differs.
2. **Image-output mechanism (spike).** Whether the installed `google-adk` (2.14.1)
   can return inline image bytes from `gemini-3.1-flash-image` is not verified.
   Plan group 3 checks it first. If ADK cannot do it cleanly, the seam still
   holds, but the implementation may call `google.genai` directly inside
   `converter_client.py`. That is still an ADK-free-tests change, not a new
   dependency. The Tech Lead is told if this happens.
3. **Generation timeout 60 s (D9)** and **retry bound 2 (D10)** are working values.
   The Tech Lead should confirm the timeout value.
4. **Original photo re-download (D6)** adds one Telegram download per run. Storing
   bytes in session was the alternative and is not chosen.
5. **Concurrent update processing (D5)** changes the Phase 2 and 3 behaviour as
   well. It has no visible effect on the offline tests and must be checked live
   (validation section M).
6. **Caption line 2 is fixed copy (D16).** If the Tech Lead wants per-animal
   second lines, that needs a model-generated field and its own validation. That is
   not in scope here.
7. **Phase 3 open decisions** (1, 2, 5, 6, 9, 10, 11 in the interviewer spec) remain
   open as the Phase 3 verifier recorded. This spec does not close them.
8. **Live Telegram and Gemini checks are deferred** until real credentials are
   supplied, as in Phases 1 to 3. Offline validation must be complete and green
   regardless.
9. **Backward compatibility:** not a concern. The `SessionState` version is bumped
   to 3 (D13); no migration is written.
