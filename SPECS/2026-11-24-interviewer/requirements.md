# Requirements — Interviewer / Dossier (Phase 3)

Spec folder: `SPECS/2026-11-24-interviewer/`
Roadmap phase: **Phase 3 — Interviewer (orchestrator + dossier)**
Branch: `feature/2026-11-24-interviewer` (based on `main`, which includes the merged Phase 1 gateway and Phase 2 Bouncer, PR #2)

## Context

Phase 2 ends with a human photo getting a `hey mate!` confirmation and the chat
staying `AWAITING_PHOTO`. Phase 3 gives the bot its conversation engine: once a
photo passes the Bouncer, the Interviewer asks a short, playful series of
questions, one at a time, and builds a behavioural dossier about the user. The
dossier is the only input the later stages (Converter, Scripter, Narrator)
need, so this phase is where the joke's content is decided.

The Interviewer is also the orchestrator (TECH.md). In this phase the
orchestrator owns the phases `IDLE → AWAITING_PHOTO → INTERVIEWING →
DOSSIER_READY`. `GENERATING`, `DELIVERING` are declared but not driven.

## User decisions (confirmed before this spec was written)

| Decision | Choice | Rationale |
|---|---|---|
| Model | `gemini-3.1-flash-lite`, defined once in the single config module | TECH.md; one-line change for model swaps |
| Interview length | 5 to 7 questions; the model decides how many within that range | MISSION/ROADMAP acceptance |
| Question pacing | One question per message; the bot waits for the answer | MISSION step 4 |
| Question generation | Each question is generated from earlier answers | ROADMAP Phase 3 |
| Tone | Investigative, playful, slightly eccentric nature-documentary researcher | User decision |
| Start trigger | After a photo passes the Bouncer, the first question **replaces** the Phase 2 `hey mate!` confirmation | User decision; deliberate change, see below |
| Output | Dossier with quirks (sleep, territory, forage, etc.) and one suggested animal with a short reason. No further fields | User decision |
| Session | In memory, keyed by `chat_id`, versioned schema | TECH.md; no persistence (MISSION out of scope) |
| Reset | `/restart` mid-interview discards the partial dossier | User decision |
| Backward compatibility | Not needed | User decision |

## Deliberate change: the `hey mate!` confirmation is replaced

The Phase 2 Bouncer spec (`SPECS/2026-11-19-bouncer/requirements.md`, decision
"Pass confirmation" and Open decision 12) and ROADMAP Phase 2 record a pass
reply of `hey mate!`. This phase **intentionally removes that reply on the
photo path**: a passing photo now produces the first interview question
instead. This is a deliberate change of behaviour, not a regression, and it is
recorded in ROADMAP Phase 3 at verification time.

What is preserved:

- Text sent while the chat is `AWAITING_PHOTO` keeps the Phase 1 fixed reply
  `hey mate!` (the constant is still imported from `gateway.py`, not duplicated).
- Non-human photos keep the Phase 2 cheeky rejection and reset to
  `AWAITING_PHOTO`.

## Scope

### In scope

1. **Interview orchestration** — a pass from the Bouncer moves the chat to
   `INTERVIEWING` and sends the first question. Each subsequent text answer is
   committed, and the next question is generated from the answers so far.
2. **Question count** — the code enforces a minimum of 5 and a maximum of 7
   questions. Within that range the model decides when to finish. A model
   "finish" before 5 answers is treated as an invalid response (see Failure
   behaviour). At 7 answers the code forces completion regardless of the model.
3. **Dossier** — a validated, typed object built when the interview completes
   (see Contracts). It contains quirks and one suggested animal with a short
   reason, and nothing else.
4. **Dossier delivery** — on successful completion the bot sends the dossier to
   the chat as one text message (quirks, animal, reason) and moves the chat to
   `DOSSIER_READY`. This phase delivers no image, no narration, and no
   `GENERATING` transition.
5. **Versioned in-memory session** — extend the Phase 2 `SessionStore` keyed by
   `ChatId`. The session holds: phase, photo reference, the answers so far, the
   outstanding question, and the schema version. A stored version mismatch is
   discarded and the chat restarts cleanly. The state driver is the only writer.
6. **`/restart`** — available in every phase. Purges the photo reference,
   answers, outstanding question, and any partial or completed dossier. Returns
   the chat to `AWAITING_PHOTO` and sends the opening prompt. No process restart;
   other chats are untouched.
7. **Wrong-payload guards** for this phase:
   - text with no outstanding question (phase `AWAITING_PHOTO`, or phase
     `DOSSIER_READY`) → nudge or the Phase 1 reply as listed below; no state
     change;
   - a photo while `INTERVIEWING` → nudge; no state change; the Bouncer is not
     called;
   - a photo while `DOSSIER_READY` → nudge (send `/restart` for a new one); no
     state change;
   - empty, whitespace-only, or over-length text while a question is outstanding
     → nudge; the answer is not stored and the question stays outstanding.
8. **Concurrency guard** — while a model call is in flight for a chat, any other
   update for that chat receives the short "one at a time" nudge and changes no
   state. This is the same rule as the Bouncer's in-flight photo guard.
9. **Typed errors** — `ProviderTimeout`, `VisionUnavailable`, `RateLimited`,
   `InvalidResponse` (already defined by Phase 2) are reused for the interviewer
   provider path. The routing layer maps them to user-facing copy.
10. **Copy** — a single tested set of constants in `messages.py` for the opening
    prompt, nudges, the resend message, and the dossier layout. No prompt text,
    stack traces, error codes, or model payloads reach the user.
11. **Structured logging** via `@log_stage` and the existing `configure_logging()`.
12. **Red/Green TDD tests** — all offline; the model is faked at the seam.

### Out of scope (do not build)

- Converter, image generation, Scripter, Narrator/TTS, `GENERATING` and
  `DELIVERING` transitions (Phases 4–6).
- Any dossier field beyond quirks, suggested animal, and animal reason. No
  extra fields "just in case", no per-question tags, no confidence scores, no
  summaries of the user.
- Persistence, databases, session replay, history, restart survival.
- Webhooks or any inbound HTTP server.
- Editing or regenerating answers or a dossier. The user may only resend the
  current answer when the provider fails; there is no "go back" command.
- `/start` behaviour changes (owned by Phase 7). `/restart` is in scope here.
- Languages other than English; localisation.
- Caption-based input, voice, stickers, or documents. These get a nudge or are
  ignored by the existing Phase 1/2 routing.
- Any config toggle, feature flag, or extension point for question count or
  model choice beyond the single config module.

## Decisions

| Decision | Value | Rationale |
|---|---|---|
| Model | `gemini-3.1-flash-lite`, in the single config module | TECH.md |
| Agent framework | Google ADK `Agent` with structured output, run via `Runner` + `InMemorySessionService`, the same pattern as the Bouncer | Consistent with Phase 2; TECH.md hub-and-spoke |
| Seam | `InterviewClient` protocol; Only the model-client modules (`vision_client.py`, `interview_client.py`) import ADK; no other module does | Tests must never import ADK or open a socket |
| Model turn shape | One structured response per model turn: either an `ask` with the next question, or a `finish` with a `Dossier`. Validated against Pydantic models at the boundary | TECH.md "prefer schemas over regex" |
| Question count | Minimum 5, maximum 7, enforced in code | User decision: model chooses within range; code guarantees the range |
| Question text | 1 to 300 characters, single line | Untrusted-output bound; keeps Telegram messages short |
| Answer text | 1 to 1000 characters after trimming | Bound for absurdly long input (TECH.md); the number is a decision, see Open decisions |
| Timeout / retry | 10 s per call; max 2 attempts with backoff for timeout / 429 / 5xx; non-retryable fails fast | Same policy as the Bouncer (TECH.md) |
| Repair retry | Exactly one retry with the validation error when a model response fails schema validation | TECH.md |
| Degradation | Provider failure → no state change; user asked to resend the last answer. Schema failure after repair → same resend message, no state change | Resend-style, per user decision |
| First question | Generated by the model with an empty answer history, through the same code path as later questions | One code path; see Open decisions |
| Dossier delivery | One text message with a fixed layout built from validated fields | No prose from the model reaches the user unvalidated |
| Terminal phase | `DOSSIER_READY`: a declared phase; `GENERATING` is not driven | Keeps Phase 4 free to add `GENERATING`; see Open decisions |
| Copy | Constants in `messages.py`; `HEY_MATE` remains imported from `gateway.py` | Single source of truth |

## Contracts (typed at the boundary)

### `Quirk`

- `topic: str` — required, 1 to 40 characters. Examples: `sleep`, `territory`,
  `forage`. The topic set is not an enum: the model chooses topics, and the
  prompt suggests the examples.
- `observation: str` — required, 1 to 300 characters, single line.

### `SuggestedAnimal`

- `name: str` — required, 1 to 60 characters, the common name of an animal.
- `reason: str` — required, 1 to 300 characters, short and playful.

### `Dossier` (Pydantic v2, `extra="forbid"`)

- `quirks: list[Quirk]` — required, 3 to 7 entries.
- `suggested_animal: SuggestedAnimal` — required.

Nothing else. Extra fields are rejected.

### `InterviewTurn` (model output, validated)

One of two shapes, chosen by `action`:

- `action="ask"`: `question: str` (1 to 300 characters); `dossier` must be absent.
- `action="finish"`: `dossier: Dossier`; `question` must be absent.

Extra fields are forbidden. A response that breaks these rules fails validation.

### `InterviewClient` (protocol / seam)

`async def next_turn(history: list[Answer]) -> InterviewTurn`, where `Answer` is
`(question: str, answer: str)`. `history` is the list of committed exchanges. The
orchestrator passes the history; the client builds the prompt. `AdkInterviewClient`
implements the protocol. Tests provide a fake that returns scripted turns, raises
a typed error, or returns invalid payloads.

The orchestrator, not the model, enforces the 5-to-7 rule and the turn-shape
rule:

- `finish` with fewer than 5 answers → invalid; one repair retry; then
  `InvalidResponse`.
- `ask` with 7 answers → the code does not ask again; it requests `finish` on
  the same history, with one call and the same repair rule.
- `ask` with fewer than 7 answers and 5 or more answers is allowed; the model
  may finish at 5, 6, or 7.

### `SessionState` (Pydantic, versioned)

- `schema_version: int` — currently `2` (Phase 2 state was version `1`).
- `phase: Phase`
- `photo_file_id: str | None`
- `answers: list[Answer]` — committed exchanges, oldest first.
- `outstanding_question: str | None` — set only while `INTERVIEWING`.
- `dossier: Dossier | None` — set only in `DOSSIER_READY`.
- `in_flight: bool` — a model call is running for this chat.

Invariants: `outstanding_question` is set exactly when `phase == INTERVIEWING`
and no call is in flight after a committed turn. `dossier` is set exactly when
`phase == DOSSIER_READY`.

### `Phase` enum

`IDLE`, `AWAITING_PHOTO`, `INTERVIEWING`, `DOSSIER_READY` are driven.
`GENERATING`, `DELIVERING` are declared with no transitions.

### `SessionStore`

Keyed by `ChatId`. Single driver with: `get(chat_id)`, `begin_turn(chat_id)`,
`commit_question(chat_id, question)`, `commit_answer(chat_id, answer)`,
`commit_dossier(chat_id, dossier)`, `reset(chat_id)`. Stages never mutate session
fields directly. A version mismatch on read discards the stored state and returns
a fresh `AWAITING_PHOTO` session.

## Flow

1. **Photo passes the Bouncer** (`AWAITING_PHOTO`) → set `photo_file_id`, call
   `begin_turn`, request `ask` with empty history → `commit_question` → send the
   question → phase `INTERVIEWING`.
2. **Text answer** (`INTERVIEWING`, a question outstanding, not in flight) →
   validate length → `begin_turn` → request the next turn with the history plus
   this exchange:
   - `ask` → `commit_answer` + `commit_question` → send the new question.
   - `finish` (only if the answer count is at least 5) → `commit_answer` +
     `commit_dossier` → send the dossier → phase `DOSSIER_READY`.
   - Forced `finish` at 7 answers as described under Contracts.
3. **Dossier delivered** (`DOSSIER_READY`) → any further text or photo gets a
   nudge pointing to `/restart`. No state change.
4. **`/restart`** (any phase) → `reset` → opening prompt; phase `AWAITING_PHOTO`.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| Text in `AWAITING_PHOTO` | Phase 1 reply `hey mate!`; no state change |
| Text in `DOSSIER_READY` | Nudge pointing to `/restart`; no state change |
| Photo in `INTERVIEWING` | Nudge (`PHOTO_MID_INTERVIEW_TEXT`); Bouncer not called; no state change |
| Photo in `DOSSIER_READY` | Nudge pointing to `/restart`; no state change |
| Text in `INTERVIEWING` with no outstanding question | Nudge; no state change. Should not happen in normal flow, so it is logged at `warning` |
| Empty, whitespace-only, or over 1000-char answer | Nudge (`ANSWER_TOO_LONG_TEXT` or `EMPTY_ANSWER_TEXT`); answer not stored; question stays outstanding |
| Any update for a chat with a call in flight | `ONE_AT_A_TIME_TEXT` nudge; no state change; no model call |
| `ProviderTimeout` / `VisionUnavailable` / `RateLimited` after bounded retry | `RESEND_ANSWER_TEXT` ("Signal's fuzzy — mind resending your last answer?"); no state change (answer not committed) |
| `InvalidResponse` after one repair retry | `RESEND_ANSWER_TEXT`; no state change; logged at `error` |
| `finish` before 5 answers | Treated as invalid; one repair retry; then as `InvalidResponse` |
| Photo fails the Bouncer's resend path at the start | Phase 2 behaviour; stays `AWAITING_PHOTO`; no question sent |
| First question fails after retry | `RESEND_TEXT` ("Vision's acting up — mind resending the photo?") with no photo stored; phase stays `AWAITING_PHOTO` |
| Handler raises unexpectedly | Caught, logged at `error`; the next update is still served |
| Malformed update (no `chat_id`) | Ignored; logged at `debug`; no exception |

No failure path shows a stack trace, provider payload, prompt text, or error
code.

## Copy

All in `messages.py`, tested for content and absence of forbidden text:

- `OPENING_PROMPT` — "Send me a portrait photo to begin your documentary." (used
  after `/restart`)
- `HEY_MATE` — imported from `gateway.py` (text in `AWAITING_PHOTO`)
- `PHOTO_MID_INTERVIEW_TEXT` — "I'm still mid-expedition with you — please answer the question above."
- `ONE_AT_A_TIME_TEXT` — reused from the Bouncer, "One at a time, please — I'm still checking your last photo." Wording stays generic enough to suit both phases. See Open decisions.
- `RESEND_ANSWER_TEXT` — "Signal's fuzzy — mind resending your last answer?"
- `RESEND_TEXT` — reused from the Bouncer.
- `EMPTY_ANSWER_TEXT` — "I didn't catch anything there — tell me a little more."
- `ANSWER_TOO_LONG_TEXT` — "That's a lot to observe in one go — keep it under 1000 characters."
- `ALREADY_DONE_TEXT` — "Your dossier is complete. Send /restart for a new subject."
- Dossier layout: a fixed template with a header, one line per quirk, then the
  suggested animal and reason. Built from validated fields only.

The tone is investigative and playful: a nature-documentary researcher reporting
field notes. Copy must not claim anything the dossier does not contain.

## Logging

- `configure_logging()` and `@log_stage` as before. One logger per module.
- At each boundary, log: entry, outcome, duration, error type, `chat_id`, phase
  before and after, question index (count of committed answers), and the
  suggested animal's `name`.
- Never log: answer text, question text if it echoes an answer, quirk text,
  animal `reason`, image bytes, base64, tokens, or API keys.
- Forbidden constructs remain forbidden: bare `except`, swallowed errors,
  recovered errors logged below `error`.

## Testing requirements (Red/Green TDD)

Tests are written first, confirmed red, then implemented to green. Use
`scripts/test` and `scripts/hooks`, never raw pytest/ruff/mypy. Invoke the
`write-tests` skill before writing tests.

Every test uses the fake `InterviewClient`. No test imports ADK, opens a socket,
or reads `.env`.

Required cases:

- **Full interview:** a fake that asks 5 questions and then finishes, plus a
  second run that asks 7 questions, reaching a valid dossier and `DOSSIER_READY`.
  The chat sends one question per message.
- **Wrong payload:**
  - `finish` before 5 answers → repair retry → `InvalidResponse` → resend message,
    no state change;
  - a dossier with extra fields, too few quirks, or an empty animal name → invalid;
  - `ask` and `finish` both set, or neither set → invalid;
  - text in `AWAITING_PHOTO` keeps `hey mate!`;
  - photo mid-interview → nudge, fake client never called, state unchanged;
  - empty and over-length answers → nudge, answer not stored.
- **Reset mid-interview:** `/restart` after 2 answers → answers, question,
  photo reference, and dossier all cleared; phase `AWAITING_PHOTO`; opening
  prompt sent; a new photo starts a fresh interview with an empty history.
- **Provider error:** timeout, 429, 5xx retried max 2 times with backoff (fake
  counts attempts); non-retryable fails fast; after the bounded retry the
  resend message is sent and no state changes.
- **Two chats isolated:** chat A and chat B run interleaved; each dossier holds
  only its own answers; a reset in A leaves B's state and outstanding question
  intact.
- **Versioning:** a stored `schema_version` of `1` is discarded on read.
- **In-flight guard:** an update arriving while a call is in flight gets
  `ONE_AT_A_TIME_TEXT` and no second model call.
- **Logging:** with `caplog`, a full run logs chat id, phases, and animal name
  but never answer text, quirk text, reason text, image bytes, or keys.
- **Configuration:** `gemini-3.1-flash-lite` appears once in the config module.

## Repo hygiene

- Reuse the existing dependency set (`google-adk` from Phase 2). Add no new
  dependency unless the plan's first task proves one is required, and then stop
  and report.
- Model IDs remain in the single config module; no scattered literals.
- `.env`, `.venv`, caches, and generated media stay untracked and ignored.

## Open decisions / assumptions

1. **Deliberate change to the Phase 2 success step (approved by the user):** a
   pass now sends the first question instead of `hey mate!`. Recorded here and in
   ROADMAP Phase 3 at verification time. The Phase 2 spec remains a historical
   record.
2. **First question is model-generated** with an empty history, through the same
   code path as later questions. The alternative, a fixed opening line, is not
   chosen. Revisit if the first-question latency or variety is a problem.
3. **Terminal phase `DOSSIER_READY`:** this spec adds a declared phase after
   `INTERVIEWING`, so Phase 4 can add `GENERATING` without a rewrite. This is an
   addition to TECH.md's phase list. **CONFIRMED by the Tech Lead (2026-11-24);
   recorded in TECH.md (state machine).**
4. **Answer length bound (1000 characters)** and **question bound (300
   characters)** are working defaults chosen for this spec, not user-specified.
   Adjust in one place if needed. **CONFIRMED by the Tech Lead (2026-11-24);
   recorded in TECH.md (interview limits).**
5. **Dossier delivered as text** at completion. The dossier is the phase's
   output. The Converter (Phase 4) will later consume the same validated object.
6. **Quirk topics are free text**, not an enum. The prompt suggests `sleep`,
   `territory`, `forage`, and similar. Three to seven quirks are required.
7. **`ONE_AT_A_TIME_TEXT`** is shared with the Bouncer. If the wording needs to
   differ per phase, split it into two constants. **CONFIRMED by the Tech Lead
   (2026-11-24): the shared copy stays.**
8. **`/start`** is unchanged in this phase and stays owned by Phase 7. **CONFIRMED
   by the Tech Lead (2026-11-24): during an interview, `/start` counts as a normal
   answer until Phase 7.**
9. **Resend semantics:** a failed turn never commits the user's answer, so the
   user resends the same answer. There is no partial commit.
10. **Live Telegram and Gemini checks are deferred** until real credentials are
    supplied, as in Phases 1 and 2. Offline validation must be complete and green
    regardless.
11. **Backward compatibility:** not a concern. The Phase 2 `SessionState` version
    is bumped; no migration is written.
