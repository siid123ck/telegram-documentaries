# Requirements — Bouncer / Vision Routing (Phase 2, Activity 4)

Spec folder: `SPECS/2026-11-19-bouncer/`
Roadmap phase: **Phase 2 — Bouncer (vision gate)**
Branch: `feature/2026-11-19-bouncer` (branched from `feature/2026-11-16-telegram-gateway`, which holds the Phase 1 code; Phase 1 PR #1 is open but not yet merged)

## Context

Phase 1 delivered a bot that answers every text message with `hey mate!` but
has no intelligence. Phase 2 gives the bot eyes. The Bouncer is the first
Google ADK agent in the pipeline: it inspects an incoming Telegram photo and
decides whether a **discernible human subject** is present.

- **Human photo** → approve and reply with the hardcoded Activity 3 message
  `hey mate!` (the confirmation step available at this stage). Store minimal
  per-chat state.
- **Non-human photo** (cat, pizza, car, mug, landscape, …) → stop immediately,
  reset that chat's ephemeral state, and reply with a cheeky in-character
  rejection that names the offending subject. Never sound like an HTTP error.

Why now: the rest of the pipeline (Interviewer, Converter, Scripter, Narrator)
only makes sense if the subject is a person. Catching non-human uploads at the
edge keeps every later stage simple.

This spec deliberately does **not** build the Interviewer (Phase 3). On a pass,
the bot replies `hey mate!` and stays awaiting-photo; there is no interview
question yet. This is recorded as an approved deviation from the ROADMAP
Phase 2 wording ("transition to INTERVIEWING"), chosen to match the Activity 4
success criteria and keep scope tight.

## User decisions (confirmed before this spec was written)

| Decision | Choice | Rationale |
|---|---|---|
| Pass-path behaviour | Reply `hey mate!` + minimal per-chat reset state | Matches Activity 4 success criteria; Interviewer not built yet; sets up Phase 3 |
| Base branch | Branch from the Phase 1 feature branch | Phase 1 PR not merged, so `main` has no gateway code; Bouncer needs that foundation. Once PR #1 merges, the Bouncer PR diff becomes Bouncer-only automatically |
| Live Telegram tests | Deferred | `.env` holds placeholder credentials only; same deferral as Phase 1 |

## Scope

### In scope

1. **Photo intake at the edge** — a Telegram photo update is parsed into a typed
   `PhotoPayload`; the largest available photo size is selected and its bytes
   downloaded through an injectable seam. Bounded size limit; empty/oversized
   payloads produce a nudge, never an exception.
2. **Typed contracts** (Pydantic v2) at every boundary:
   - `ChatId` — a typed value for `chat_id`, used consistently as the state key
     (avoids string-vs-int session lookup bugs).
   - `PhotoPayload` — `chat_id`, `file_id`, `photo_bytes`, `caption: str | None`.
   - `VisionVerdict` — `contains_human: bool`, `reason: str`,
     `detected_subject: str`.
3. **Google ADK Bouncer agent** — the first ADK agent, model
   `gemini-3.1-flash-lite` (from TECH.md / one config module), given the image
   bytes as a multimodal part plus a versioned classification prompt. Uses
   structured output validated against `VisionVerdict`.
4. **Bounded external call** — explicit timeout (10 s) and bounded retry
   (max 2 attempts, backoff) for timeout / 429 / 5xx; non-retryable errors fail
   fast. All failures degrade to a "please resend" nudge.
5. **Routing** — a photo update runs the Bouncer flow; a text update keeps the
   Phase 1 `hey mate!` reply; all other updates remain safely ignored.
6. **Minimal per-chat state and reset** — a single state driver keyed by
   `ChatId`. Pass stores the photo reference; rejection purges it and returns
   the chat to `AWAITING_PHOTO`. No other phase is implemented.
7. **Typed errors** — extend `errors.py` with `VisionUnavailable`,
   `ProviderTimeout`, `InvalidResponse` (and `RateLimited` for the retry family
   per TECH.md); the routing layer maps them to user-facing messages.
8. **Cheeky rejection copy** — a single tested template in `messages.py` that
   names `detected_subject` and never exposes prompts, model payloads, stack
   traces, or error codes.
9. **Structured logging** via the existing `@log_stage` decorator — log
   `chat_id`, verdict bool, `detected_subject`, duration, and error type; never
   image bytes, secrets, tokens, or full model prose.
10. **Red/Green TDD tests** — all offline, no network, no real credentials.

### Out of scope (do not build)

- Interviewer, dossier, suggested animal, or any interview question (Phase 3).
- Converter, image generation, Scripter, Narrator/TTS (Phases 4–6).
- Full state machine transitions beyond `IDLE`/`AWAITING_PHOTO`; the other
  `Phase` enum members may be declared but must not be driven in this phase.
- Webhooks, inbound HTTP server, databases, persistence, restart survival.
- Multiple photos in a media group (take the first valid photo; ignore extras).
- Caption-based classification, OCR, face recognition/identity, or bulk upload.
- Any config toggle, feature flag, or extension point "just in case".

## Decisions

| Decision | Value | Rationale |
|---|---|---|
| Model | `gemini-3.1-flash-lite`, in the single config module | TECH.md; cheap, fast, multimodal |
| Agent framework | Google ADK `Agent` with `output_schema=VisionVerdict`, run via `Runner` + `InMemorySessionService` | User instruction "pass the image payload directly into the ADK agent toolset"; TECH.md hub-and-spoke |
| Structured output | ADK `output_schema` bound to `VisionVerdict`; validate again at the boundary | TECH.md "prefer schemas over regex"; never scrape prose |
| Image part | `Part.from_bytes(data=photo_bytes, mime_type="image/jpeg")` | ADK multimodal input. Always `image/jpeg`: Telegram photo sizes carry no MIME type and are always JPEG. Deviation 5 (see Open decisions). |
| Timeout / retry | 10 s timeout; max 2 attempts with backoff for timeout/429/5xx | TECH.md external-call policy |
| Photo size limit | 10 MiB (reject larger with a nudge) | TECH.md names "5 MB images" as arbitrary input; bound defensively |
| Largest photo | Select the `PhotoSize` with the greatest `file_size` (last in Telegram's list) | Activity 4: "fetches the largest available photo size" |
| Rejection copy | Deterministic template from `messages.py` using `detected_subject` | Avoid leaking arbitrary model prose; still names the object |
| Testability seam | `VisionClient` protocol; tests inject a fake; `AdkVisionClient` is the only ADK importer | Tests must never hit the network or import provider SDKs |
| Pass confirmation | `REPLY_TEXT = "hey mate!"` reused from Phase 1 `gateway.py` (imported, not duplicated) | Activity 4 success criteria; single source of truth |

## Contracts (typed at the boundary)

> **Deviation 3 (approved):** `parse_photo_update` returns a `PhotoRef`
> (`chat_id`, `file_id`, `file_size`, `caption`), not a `PhotoPayload`. The photo
> bytes are only known after the download, so `PhotoPayload` is built in the
> gate after the download succeeds. Both types exist; the `PhotoPayload` rules
> below are unchanged.

### `ChatId`

A typed value (`NewType("ChatId", int)` or a constrained Pydantic field) used as
the session-store key and carried in `PhotoPayload`. All state access goes
through this type; a raw `str`/`int` is converted exactly once at the edge.

### `PhotoPayload` (Pydantic, strict)

- `chat_id: ChatId` — required, positive.
- `file_id: str` — required, non-empty; the Telegram file identifier.
- `photo_bytes: bytes` — required, non-empty, `<= 10 * 1024 * 1024`.
- `caption: str | None` — optional, ignored for classification.

Invalid payloads are rejected before the model call with a typed
`InvalidResponse`-class error mapped to a nudge ("send a smaller photo" for
oversize, "I couldn't read that photo" for empty/malformed).

### `VisionVerdict` (Pydantic, strict)

- `contains_human: bool` — required.
- `reason: str` — required, non-empty, max 300 chars. Logged only; never shown
  verbatim to the user.
- `detected_subject: str` — required, non-empty, max 80 chars. Used in the
  rejection template when `contains_human` is false.

Extra fields are forbidden. A model response that fails validation triggers
one retry with the validation error, then a typed `InvalidResponse`.

### `VisionClient` (protocol / seam)

`async def classify(photo: PhotoPayload) -> VisionVerdict`.
`AdkVisionClient` implements it via ADK; tests provide a fake that returns a
`VisionVerdict` or raises a defined error. `bouncer.py` depends only on this
protocol, so no test imports ADK or opens a socket.

### State

- `Phase` enum: declare `IDLE` and `AWAITING_PHOTO`; other TECH.md phases may be
  listed but are not driven this phase.
- `SessionState` (Pydantic, versioned): `phase: Phase`,
  `last_file_id: str | None`.
- `SessionStore` keyed by `ChatId`; one driver with `get(chat_id)`,
  `note_photo(chat_id, file_id)`, and `reset(chat_id)`. Stages never mutate
  state directly. `reset` returns the chat to `AWAITING_PHOTO` and clears
  `last_file_id`. A stored version mismatch is discarded and restarted cleanly.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| `contains_human=False` | Cheeky in-character rejection naming `detected_subject`; `store.reset(chat_id)`; stay awaiting-photo |
| `VisionUnavailable` / `ProviderTimeout` / `RateLimited` after bounded retry | "Vision's acting up — mind resending the photo?"; no state mutation; no exception |
| `InvalidResponse` after one repair retry | Same gentle resend nudge; log at error level |
| Empty / oversized / malformed photo | Nudge ("couldn't read that photo" / "send a smaller one"); no state change |
| Photo with no `chat_id` (malformed update) | Ignored; log at debug; no exception |
| Handler raises unexpectedly | Caught, logged at error level; next update still served |
| Second photo while one is in flight | Reply with the short "one at a time" nudge (`ONE_AT_A_TIME_TEXT`). No download, no model call, no state change; the first photo's result is unaffected. Decided by the Tech Lead (deviation 7). |

## Rejection / confirmation copy

- Confirmation on pass: reuse `REPLY_TEXT = "hey mate!"` from `gateway.py`.
- Rejection template (single constant in `messages.py`), shaped like:
  `Nice {subject} — but this documentary needs a human. Send me a photo of a person.`
  It must name the subject, be playful, and contain no code, stack trace,
  prompt text, or model payload.
- Rejection template (as implemented in `messages.py`, matching the example above):
  `Nice {subject} — but this documentary needs a human. Send me a photo of a person.`
- One-at-a-time nudge (single constant): `One at a time, please — I'm still checking your last photo.`
- Resend nudge (single constant): `Vision's acting up — mind resending the photo?`

## Logging

- Use the existing `configure_logging()` and `@log_stage`. One logger per module.
- At each boundary log: entry, outcome, duration, error type, `chat_id`, verdict
  bool, and `detected_subject`.
- Never log: image bytes, base64, tokens, API keys, full model prose (`reason`),
  or full user text.
- Forbidden constructs remain forbidden: bare `except`, swallowed errors,
  recovered errors logged below `error`.

## Testing requirements (Red/Green TDD)

Tests written first, confirmed red, then implemented to green. Use `scripts/test`
and `scripts/hooks` — never raw pytest/ruff/mypy.

- `tests/test_models.py` — `VisionVerdict` valid/invalid (empty fields, wrong
  types, extra fields, overlength); `PhotoPayload` empty/oversize/valid;
  `ChatId` typing consistency.
- `tests/test_bouncer.py` — pass verdict → approve + `note_photo`; non-human
  verdict (cat, pizza, car) → rejection template + `reset`; `VisionUnavailable`
  / timeout / `InvalidResponse` → resend nudge, no state mutation, no exception;
  one repair retry on invalid response; cross-chat isolation (one chat passes,
  another rejects, no bleed); no bytes/secrets in captured logs.
- `tests/test_photo_routing.py` — photo update routed through Bouncer; text
  update still returns `hey mate!`; non-text/non-photo and malformed updates
  produce no exception; reply constant imported, not duplicated.
- No test may import ADK, contact a network, or read `.env`.

## Repo hygiene

- Add `google-adk` via `uv add google-adk`; commit the refreshed `uv.lock`.
- `.env`, `.venv`, caches, and generated media stay untracked/ignored.
- Model IDs remain in the single config module; no scattered literals.

## Open decisions / assumptions

1. **ROADMAP deviation (approved):** ROADMAP Phase 2 says a pass transitions to
   `INTERVIEWING`; this spec instead replies `hey mate!` and stays
   awaiting-photo, per the user's confirmed choice and Activity 4 success
   criteria. The Interviewer is Phase 3. ROADMAP/spec will be updated at
   verification time to reflect this.
2. **`detected_subject` vs `reason`:** the user sees only the deterministic
   template built from `detected_subject`; the free-form `reason` is logged but
   never displayed, to avoid leaking arbitrary model text.
3. **Media groups:** first valid photo only; extra photos ignored (YAGNI).
4. **ADK availability:** if `google-adk` cannot be installed or the configured
   model ID is rejected by the provider, the implementer must report back
   rather than silently substituting a different model or SDK.
5. **Backward compatibility:** not a concern — greenfield; Phase 1's text
   `hey mate!` behaviour is intentionally preserved, not treated as a legacy
   constraint.
6. **Live tests deferred** as recorded above; offline validation must still be
   complete and green.
7. **Second photo in flight (Tech Lead decision):** instead of staying silent, a
   second photo from a chat whose first photo is still being checked gets the
   short "one at a time" nudge. It is not downloaded or classified, and the first
   photo's result is unaffected. Implemented in `photo_handler.py`
   (`ONE_AT_A_TIME_TEXT`).
8. **Rejection wording (Tech Lead decision):** the rejection copy now matches the
   example wording in this file ("this documentary needs a human. Send me a photo
   of a person."). It still names the object, is in character, and never includes
   prompts or model payloads.
9. **Deviation 3, parser return type (Tech Lead decision, approved):** the parser
   returns `PhotoRef`, not `PhotoPayload`, because the bytes are only known after
   the download. The gate builds `PhotoPayload` after a successful download.
10. **Deviation 4, logger level (Tech Lead decision, approved):** the whole
    `telegram` logger (python-telegram-bot and its children) is pinned to `INFO`,
    not just the request-level loggers. This keeps the bot token out of DEBUG output.
    The pin is applied in `logging_setup.py`.
11. **Deviation 5, MIME type (Tech Lead decision, approved):** the image part is
    always sent as `image/jpeg`, and the Telegram `mime_type` is not read. Telegram
    photo sizes are always JPEG. The constant is `DEFAULT_MIME_TYPE` in
    `vision_client.py`.
12. **Deviation 6, success step (Tech Lead decision, approved):** a valid photo
    currently triggers the `hey mate!` confirmation reply. The interview (and the
    `INTERVIEWING` transition) comes in Phase 3. This is recorded in ROADMAP.md
    Phase 2.
13. **Verifier confirmation (2026-11-24):** deviations 1 (success step, item 1),
    3 (`PhotoRef`, item 9), 4 (`telegram` logger pinned to INFO, item 10),
    5 (`image/jpeg` always, item 11), and 6 (`hey mate!` success step, item 12)
    were checked against the code and are recorded in ROADMAP.md Phase 2. Live
    Telegram/Gemini checks (validation.md section G) remain deferred.

