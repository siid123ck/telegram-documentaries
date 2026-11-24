# Plan — Bouncer / Vision Routing (Phase 2, Activity 4)

Red/Green TDD throughout. For each task group: write the failing test(s), run
`scripts/test` to confirm **red**, implement, run `scripts/test` to confirm
**green**. Use `scripts/test` and `scripts/hooks` — never raw `pytest`/`ruff`/
`mypy`. Invoke the `write-tests` skill before writing any tests.

Branch: `feature/2026-11-19-bouncer` (already created from the Phase 1 branch).
Spec folder: `SPECS/2026-11-19-bouncer/`.

## Task group 1 — Dependencies & typed contracts (RED first)

1. `uv add google-adk` (refreshes `uv.lock`). If install fails, stop and report —
   do not substitute another SDK.
2. Write `tests/test_models.py` (RED):
   - `VisionVerdict`: valid instance; empty `reason`/`detected_subject` rejected;
     wrong types rejected; extra fields rejected; overlength fields rejected.
   - `PhotoPayload`: valid; empty `photo_bytes` rejected; bytes over 10 MiB
     rejected; `caption=None` accepted; missing `chat_id` rejected.
   - `ChatId`: a raw `str` and `int` are converted to the same typed key (no
     string-vs-int mismatch).
3. Run `scripts/test` → red.
4. Implement in `src/telegram_documentaries/`:
   - `models.py`: `ChatId`, `PhotoPayload`, `VisionVerdict` (Pydantic v2,
     `extra="forbid"`, strict where sensible); a `MAX_PHOTO_BYTES` constant.
   - Add model IDs to the existing single config module (e.g. a `MODELS`
     mapping with `bouncer = "gemini-3.1-flash-lite"`); no literal elsewhere.
5. Run `scripts/test` → green.

**Acceptance:** every malformed contract is rejected at construction; model ID
lives in one place; `google-adk` present in `pyproject.toml`/`uv.lock`.

## Task group 2 — Typed errors & messages (RED first)

1. Write assertions (can live in `tests/test_bouncer.py`) (RED): each error type
   is distinct and carries a safe message; `REJECTION_TEMPLATE.format(subject=...)`
   names the subject and contains no error-code/regex/prompt leak; the resend
   and confirmation constants are importable.
2. Run `scripts/test` → red.
3. Implement:
   - Extend `errors.py` with `VisionUnavailable`, `ProviderTimeout`,
     `RateLimited`, `InvalidResponse` (all with safe, user-agnostic messages).
   - `messages.py`: `CONFIRMATION_TEXT` (re-export/alias of Phase 1
     `gateway.REPLY_TEXT`, not a duplicate literal), `REJECTION_TEMPLATE`,
     `RESEND_TEXT`.
4. Run `scripts/test` → green.

**Acceptance:** no duplicate `"hey mate!"` literal; rejection template names the
subject and is cheeky; all copy constants are single-sourced.

## Task group 3 — Minimal per-chat state (RED first)

1. Write `tests/test_state.py` (RED):
   - a fresh chat starts `AWAITING_PHOTO`;
   - `note_photo(chat_id, file_id)` stores the reference;
   - `reset(chat_id)` clears `last_file_id` and returns to `AWAITING_PHOTO`;
   - version mismatch with a stored state is discarded and restarts cleanly;
   - two chats keep independent state; mutating one leaves the other untouched.
2. Run `scripts/test` → red.
3. Implement `state.py`: `Phase` enum (`IDLE`, `AWAITING_PHOTO`; other TECH.md
   phases declared but not driven), versioned `SessionState`, `SessionStore`
   keyed by `ChatId` with `get`/`note_photo`/`reset` as the only writer.
4. Run `scripts/test` → green.

**Acceptance:** no stage mutates session fields directly; isolation holds; reset
is a single deterministic operation.

## Task group 4 — Vision client seam & ADK agent (RED first)

1. Write `tests/test_vision_client.py` (RED):
   - a fake `VisionClient` returning a canned verdict satisfies the protocol;
   - `bouncer.py` calls `client.classify` with the exact `PhotoPayload` and
     returns the verdict unchanged (test with an injected fake).
2. Run `scripts/test` → red.
3. Implement:
   - `vision_client.py`: `VisionClient` protocol; `AdkVisionClient` builds an ADK
     `Agent(name="bouncer", model=<config model>, instruction=BOUNCER_INSTRUCTION,
     output_schema=VisionVerdict)` and runs it via `Runner` +
     `InMemorySessionService`, passing `Part.from_bytes(...)` (MIME from payload
     or `image/jpeg`) and the prompt; applies the 10 s timeout and bounded
     retry (max 2, backoff) for timeout/429/5xx; maps provider failures to the
     typed errors.
   - `prompts.py`: `BOUNCER_INSTRUCTION` — classify a discernible human subject;
     output strict JSON matching `VisionVerdict`; in `reason`, explain rejection
     playfully without error codes or prompt leakage; put the plain object name
     in `detected_subject` when non-human.
   - `bouncer.py`: `async def inspect(photo, client, store) -> VisionVerdict`
     — thin stage that calls the client, then `note_photo` on pass or `reset` +
     rejection on fail; emits no user text itself (returns a typed result for the
     routing layer to render).
4. Run `scripts/test` → green.

**Acceptance:** tests never import ADK or open sockets; only `vision_client.py`
imports ADK; the retry/timeout policy is observable and bounded.

## Task group 5 — Photo routing & wiring (RED first)

1. Write `tests/test_photo_routing.py` (RED):
   - a photo update is parsed to `PhotoPayload` and routed through `bouncer.inspect`
     with the fake client; pass → `CONFIRMATION_TEXT` sent; fail →
     `REJECTION_TEMPLATE` with subject sent and state reset;
   - typed provider errors → `RESEND_TEXT`, no state mutation, no exception;
   - oversized/empty photo → nudge, no model call;
   - text update still returns `hey mate!` (imported constant);
   - non-text/non-photo and malformed updates → no exception;
   - unexpected handler exception is caught and logged at error level; the next
     update is still served.
2. Run `scripts/test` → red.
3. Implement:
   - `photo_handler.py`: a pure parser `parse_photo_update(update) -> PhotoPayload |
     None` (largest photo size, typed `ChatId`, byte-size guard) plus the async
     handler that calls the Bouncer and sends the rendered message. Use an
     injectable byte-downloader seam so tests never touch the network.
   - Extend `app.py` to register a `MessageHandler(filters.PHOTO, ...)` alongside
     the existing text handler; keep unknown updates ignored.
4. Run `scripts/test` → green.

**Acceptance:** text behaviour preserved; photo behaviour fully routed; all
external IO behind seams; no exception escapes a handler.

## Task group 6 — Logging & safety sweep (RED first)

1. Write tests (extend `tests/test_bouncer.py`) (RED):
   - with `caplog`, a full pass and a full rejection log `chat_id`, verdict bool,
     and `detected_subject`, and never contain image bytes, base64, token,
     key, or the model's `reason` prose;
   - `@log_stage` records entry, outcome, and duration for the Bouncer boundary.
2. Run `scripts/test` → red.
3. Implement: wire `@log_stage` on the Bouncer stage and photo handler boundary;
   sanitise log records through the existing logging setup; assert no raw
   payloads in logs.
4. Run `scripts/test` → green.

**Acceptance:** no forbidden content in any log record at any level; decorators
carry the cross-cutting logging.

## Task group 7 — Docs & final checks

1. Update `README.md`: add the Bouncer stage to the pipeline description and note
   the photo gate (human photo → confirmation; non-human → cheeky rejection).
   Keep setup/run/dev-scripts sections accurate. No credentials in docs.
2. Run `scripts/test` → clean.
3. Run `scripts/hooks` → clean and tree unchanged after.
4. Confirm `git status` shows no `.env`, `.venv`, caches, media, or test artifacts;
   `uv.lock` staged/committed with the new dependency.
5. Report what changed for the validation checklist.

**Acceptance:** both dev scripts green; docs reflect the new behaviour; repo
hygiene clean.

## Verification handoff

When this plan is complete, hand to the verifier with the spec folder path. Live
Telegram tests (negative mug/landscape, positive person) are deferred because
`.env` holds placeholders only; offline validation must be fully green.
