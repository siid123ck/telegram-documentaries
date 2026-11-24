# TECH — The Telegram Documentaries

> The technical contract. Policies here are enforceable; the reviewer and
> verifier treat deviations as blocking findings.

## Stack

- **Language:** Python 3.11+ (async throughout).
- **Dependencies:** `uv` with `pyproject.toml` and a committed `uv.lock`.
  No ad-hoc `pip install` in docs or CI.
- **Telegram transport:** `python-telegram-bot` v21+ (async), long polling via
  `getUpdates`. No webhooks, no inbound HTTP server, no public URL.
- **Agents:** Google Agent Development Kit (ADK), hub-and-spoke.
- **Models:**
  - Bouncer — `gemini-3.1-flash-lite` (vision).
  - Interviewer / orchestrator — `gemini-3.1-flash-lite`.
  - Converter — `gemini-3.1-flash-image` (native multimodal image output).
  - Scripter — `gemini-3.1-flash-lite`.
  - Narrator — `gemini-3.1-flash-tts-preview` (TTS, **not** an agent).
- **Audio:** OGG/Opus, delivered with `sendVoice` as a Telegram voice note.
- **Validation:** Pydantic v2.
- **Tests:** `pytest` (+ `pytest-asyncio`), no real network.

Model IDs live in one config module. Changing a model is a one-line change, never
a scattered find-and-replace.

## Architecture

Hub-and-spoke. The **Interviewer is the orchestrator**: it owns the conversation
state and delegates to the specialised modules. Stages do not call each other
directly; they are invoked by the orchestrator and return typed results.

```
Telegram update
   -> transport adapter (parse + validate)
   -> orchestrator (state machine)
        -> Bouncer      (human? yes/no)
        -> Interviewer  (question, dossier, suggested animal)
        -> Converter    (portrait bytes)
        -> Scripter     (narration text)
        -> Narrator     (ogg bytes via TTS tool)
   -> Telegram send
```

Each stage is its own module with a narrow interface. The orchestrator contains
routing and state, not prompt text or HTTP details.

## State machine

Explicit phases, no booleans-as-state:

`IDLE` → `AWAITING_PHOTO` → `INTERVIEWING` → `DOSSIER_READY` (confirmed 2026-11-24)
→ `GENERATING` → `DELIVERING` → `IDLE`

Driven today: `AWAITING_PHOTO`, `INTERVIEWING`, `DOSSIER_READY`. `GENERATING` and
`DELIVERING` are declared but have no transitions yet (Phases 4–6). Phase 4 adds
`DOSSIER_READY → GENERATING`.

Interview rules (confirmed 2026-11-24):

- A passing photo moves `AWAITING_PHOTO → INTERVIEWING` and sends the first
  question. It does not send `hey mate!` (deliberate change to the Phase 2 pass path).
- `DOSSIER_READY` is terminal for the interview. Any text or photo gets a nudge
  to use `/restart`; no state change.
- While a question is outstanding, `/start` is taken as an answer. Phase 7 owns
  `/start` behaviour.
- The in-flight guard uses the shared "One at a time, please" copy (the Bouncer's
  `ONE_AT_A_TIME_TEXT`). It is reused for both phases.

- Every inbound update is routed by `(phase, payload type)`. Unhandled
  combinations produce a nudge (or are ignored for Telegram noise such as
  `edited_message`), never an exception.
- Transitions are centralised in the state driver; stages must not mutate session
  fields directly.
- `GENERATING`/`DELIVERING` must be safe against a duplicate update arriving
  while work is in flight for the same `chat_id`.

## Session state

- In-memory, single process, one store keyed by `chat_id`.
- The schema is **versioned**; an incompatible stored version is discarded and
  the chat restarts cleanly.
- One shared state driver is the only writer. Access from stages goes through it.
- Reset (`/start`, `/restart`, or any recovery) purges: dossier, interview
  progress, suggested animal, photo reference, temporary media, and derived
  output. Reset never restarts the process and never touches other chats.
- Every user-visible outbound message must be derivable from the session; nothing
  may be assumed from a previously sent message that is not stored.

### Interview limits (confirmed 2026-11-24)

| Limit | Value | Where enforced |
|---|---|---|
| Questions per interview | 5 minimum, 7 maximum (the code forces `finish` at 7) | `interview_models.py`, `interview.py` |
| Question text | 1–300 characters, single line | `interview_models.py` |
| Answer text | 1–1000 characters after trimming | `interview_models.py`, `interview.py` |
| Quirks in a dossier | 3–7 | `interview_models.py` |
| Quirk topic / observation | 1–40 / 1–300 characters | `interview_models.py` |
| Suggested animal name / reason | 1–60 / 1–300 characters | `interview_models.py` |

## Boundary contracts

- **Parse at the edge.** Telegram updates, LLM outputs, and TTS results are
  converted into typed Pydantic models at the boundary. Raw dicts, raw provider
  payloads, and unvalidated strings never cross a module boundary.
- **Prefer schemas over regex.** LLM stages use structured output / JSON schema
  and validate against a model. If a response fails validation, retry once with
  the validation error, then fail with a typed error. Never scrape prose.
- **Treat all external input as untrusted and arbitrary:** wrong content type,
  empty text, 5 MB images, malformed UTF-8, Unicode tricks, absurdly long
  answers, unexpected fields, extra photos in a media group.
- **Typed errors.** Each stage raises/returns a defined error type
  (`VisionUnavailable`, `InvalidResponse`, `ProviderTimeout`, `RateLimited`).
  The orchestrator maps them to user-facing messages.

## Prompts

- Prompts live beside their stage, are versioned in code, and produce validated
  structured output.
- The dossier model must carry: the user's answers, inferred behaviours, the
  suggested animal, and a short rationale for the animal choice.
- Scripter output: one paragraph, 60–90 words, third person, British
  RP/Attenborough register, built strictly from the dossier. No invented facts
  about the user beyond what the dossier supports.
- Prompt text is not inlined in orchestration logic.

## Logging & error policy

- **Structured logging** (key=value or JSON), one logger per module.
- Prefer **decorators** for cross-cutting concerns (`@log_stage`,
  `@timed`, `@retry`) over sprinkling log calls through business logic.
- Every stage boundary logs: entry, outcome, duration, and error type. Never log
  tokens, keys, raw image bytes, or full user answers.
- **Fail loudly and log** for non-critical, user-invisible work (cleanup,
  metrics, temp-file removal).
- **On the user's validated conversation path:** catch, log loudly at error
  level, and degrade gracefully — the conversation must continue. Never raise
  into the user's flow.
- Forbidden: bare `except:`, `except Exception: pass`, silent fallbacks, and
  recovered errors logged below `error`.
- Never expose stack traces, provider payloads, or internal IDs to users.

## External calls, timeouts, retries

- Every provider call has an explicit timeout.
- Retries are bounded (max 2) with backoff, and only for retryable classes
  (timeout, 429, 5xx). Non-retryable errors fail fast.
- Degradation policy: Bouncer unavailable → ask the user to resend the photo;
  Converter failure → apologise and offer `/restart`; TTS failure → deliver the
  script as text so the run is not lost; Scripter failure → retry once, then
  deliver the portrait with a short apology.
- No unbounded queues, no fire-and-forget tasks without error capture.

## Testing

- **Red/Green TDD.** A test exists and fails before the implementation is
  written.
- Every behaviour change ships with tests; every bug fix ships with a regression
  test.
- Tests never hit the network. Telegram and Gemini clients are faked at the
  boundary; provider responses are fixtures.
- Required coverage per feature: happy path, wrong-payload-at-wrong-stage, reset
  mid-flow, provider error/timeout, and cross-chat isolation.
- **`scripts/test` is the ground truth** for running tests.
  **`scripts/hooks` is the ground truth** for lint, format, and type checks
  (ruff + ruff-format + mypy). Both must run clean before a change is considered
  done. Neither is bypassed.

## Repo hygiene

- `.env` is gitignored; `.env.example` lists required keys with placeholder
  values and is kept in sync.
- `uv.lock` is committed. `uv sync` reproduces the environment.
- No secrets, tokens, chat IDs, or generated media in the repository.
- Generated/temporary audio and image files are written outside the repo tree
  (temp dir) and deleted after send.

## README policy

`README.md` is developer-facing and kept in sync in the same change that alters
behaviour. It must always document:

- What the bot does and its pipeline stages, in one screen.
- Prerequisites and setup: `uv sync`, `.env` keys, how to launch.
- `scripts/test` and `scripts/hooks` usage.
- How to run the bot locally (long polling) and what is deliberately not
  supported (webhooks, persistence).
- Current known limitations.

## Service boundaries summary

| Concern | Owner | Must not |
|---|---|---|
| Telegram IO, update parsing | transport adapter | know about prompts or phases |
| Phase + routing + reset | orchestrator/state driver | contain prompt text or HTTP details |
| Human detection | Bouncer | mutate session state |
| Questioning + dossier | Interviewer | send Telegram messages directly (returns content) |
| Image fusion | Converter | write files to the repo tree |
| Narration text | Scripter | call TTS |
| Speech synthesis | Narrator tool | be modelled as an autonomous agent |
