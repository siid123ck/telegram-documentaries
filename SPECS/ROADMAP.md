# ROADMAP — The Telegram Documentaries

> The ordered build plan. One phase per pipeline capability.
> A phase is marked complete only when its acceptance criteria are verified
> against a running bot and `scripts/test` + `scripts/hooks` are clean.
> Order is deliberate: each phase depends on the one before it.

**Status key:** `[ ]` not started · `[~]` in progress · `[x]` verified complete

---

## Phase 1 — Repository & gateway

**Goal:** a bot that exists and answers.

- Project skeleton (`pyproject.toml` via `uv`, `src/` layout, `scripts/`).
- `.env` loading with startup validation of `TELEGRAM_BOT_TOKEN` and
  `GEMINI_API_KEY` — fail fast with a clear message if either is missing.
- `scripts/test` and `scripts/hooks` (ruff, ruff-format, mypy) exist and run.
- Long-polling loop; any text message gets a fixed reply.
- README covers setup, run, and dev scripts.

**Acceptance criteria**
- `uv sync` then `scripts/test` passes from a clean checkout.
- Missing token produces one clear startup error, not a traceback storm.
- A text message to the bot receives the fixed reply within a few seconds.
- Bot survives an unknown update type without crashing.

**Serves:** transport/gateway capability — the bot is reachable and debuggable.

Status: `[x]`

> **Verifier note (2026-11-19) — merge-ready, with one explicit deferral.**
> Re-verification on `feature/2026-11-16-telegram-gateway` confirmed that every
> blocker from the 2026-11-16 pass is resolved.
>
> - **Verified:** `scripts/test` and `scripts/hooks` both exit 0 (33 tests); an
>   injected lint error correctly fails `scripts/test`; `uv sync` succeeds;
>   `uv.lock`, `pyproject.toml` (Python `>=3.11`, `python-telegram-bot>=21`),
>   `gateway.py`, `app.py`/`__main__.py`, `test_gateway.py`/`test_main.py`,
>   `.pre-commit-config.yaml` (all five hooks, both self-tests blocking) and
>   `README.md` are present. Placeholder/missing config produces one clean line
>   and exit 2 with no traceback or secret values; the gateway returns exactly
>   `hey mate!` for text and ignores every other payload offline. Repo hygiene
>   holds: no `.env`, `.env.save`, `.venv`, caches, media or `leak-test*` file is
>   staged, and the index is empty.
> - **Section F (live Telegram) DEFERRED, not passed.** `.env` still contains
>   only placeholder values; a real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`
>   must be supplied out-of-band by the Tech Lead before live verification.
>   This is the explicit deferral permitted by `validation.md`'s Definition of
>   merge-ready, as instructed by the orchestrator. No live verification is
>   claimed.
>
> Status is `[x]` for the implemented, offline-verifiable scope; live
> Telegram behaviour remains to be confirmed when credentials are available.

---

## Phase 2 — Bouncer (vision gate)

**Goal:** only humans get a documentary.

- Photo handler that fetches the largest available photo size and passes it to
  the vision model.
- Structured verdict model: `contains_human: bool`, `reason: str`,
  `detected_subject: str`.
- Pass → transition to `INTERVIEWING`. Fail → cheeky in-character rejection,
  reset to `AWAITING_PHOTO`.
- Vision timeout/unavailable → ask the user to resend; never crash the chat.

**Acceptance criteria**
- Portrait photo advances the flow to the first interview question.
- Car/pet/food photo is refused in character and the chat returns to
  awaiting-photo.
- A forced vision timeout yields "please resend", not an exception.
- Rejection does not leak any prompt or model payload to the user.
- Unit tests cover pass, fail, and provider-error paths with faked responses.

**Serves:** image validation + state routing.

Status: `[~]` — offline-verified; live Telegram/Gemini verification deferred.

> **Verifier note (2026-11-24, branch `feature/2026-11-19-bouncer`, uncommitted).**
>
> - **Verified offline:** `scripts/test` and `scripts/hooks` both exit 0 (163
>   tests; ruff check, ruff format, mypy, pytest all run). `scripts/test` fails on
>   an injected unused import (`F401`) and passes with no `.env` present. The
>   suite has no network I/O and no `google.adk` import outside `vision_client.py`.
>   `uv sync --locked` succeeds and `google-adk` 2.14.1 is in `pyproject.toml` and
>   `uv.lock`. `gemini-3.1-flash-lite` is defined once, in `config.py`. Photo
>   routing, the Bouncer, typed contracts, retry bounds, state isolation, and
>   log-content safety are covered by tests and checked against the code.
> - **Live Telegram and Gemini verification: DEFERRED, not passed.** `.env` holds
>   placeholder credentials only. The negative (mug/landscape) and positive
>   (person) photo tests, the polling-started check, and the clean Ctrl-C check
>   must be run once real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` values exist.
>   No live pass is claimed.
> - **Approved deviations from the Phase 2 wording above:**
>   1. **Success step:** a pass replies `hey mate!` and stays `AWAITING_PHOTO`; it
>      does not transition to `INTERVIEWING`. The Interviewer is Phase 3.
>   2. **Parser return type:** `parse_photo_update` returns a `PhotoRef`, not a
>      `PhotoPayload`. The bytes are only known after the download, so the gate
>      builds `PhotoPayload` afterwards (requirements.md, Open decisions 9).
>   3. **Logger level:** the whole `telegram` logger is pinned to `INFO`, along
>      with `httpx`/`httpcore` at `WARNING`, so the bot token cannot reach DEBUG
>      logs (Open decisions 10).
>   4. **MIME type:** images are always sent as `image/jpeg`, since Telegram photo
>      sizes are always JPEG (Open decisions 11).
>   5. **Second photo while one is in flight:** gets a "one at a time" nudge; it is
>      not downloaded or classified (Open decisions 7).

---

## Phase 3 — Interviewer (orchestrator + dossier)

**Goal:** the conversation engine.

- State machine phases wired: `IDLE → AWAITING_PHOTO → INTERVIEWING →
  GENERATING → DELIVERING → IDLE`.
- Per-`chat_id` versioned session store and single state driver.
- One question per message, 5–7 total, each generated from prior answers so the
  interview feels responsive rather than a fixed form.
- Dossier model accumulates answers, inferred behaviours, suggested animal, and
  a rationale for that animal.
- Interview completion triggers the handoff to `GENERATING`.

**Acceptance criteria**
- [x] A full interview asks between 5 and 7 questions, one message at a time, and
  ends with a valid dossier.
- [x] Text arriving mid-interview is treated as the next answer only when a question
  is outstanding; otherwise it produces a nudge.
- [x] Two concurrent chats build independent dossiers with no bleed.
- [x] Dossier fails schema validation → one repair retry → typed error, no crash.
- [x] `/restart` mid-interview discards the partial dossier.

**Serves:** conversational state machine + structured extraction.

Status: `[~]` — implemented and offline-verified on `feature/2026-11-24-interviewer`;
live Telegram/Gemini verification deferred. Not `[x]` until the verifier passes.

> **Implementation note (2026-11-24, spec `SPECS/2026-11-24-interviewer/`).**
>
> - **Built:** the interview orchestrator (`interview.py`), the typed contracts
>   (`interview_models.py`), the versioned session store (schema v2, `state.py`),
>   the provider seam with bounded retry and a Gemini response schema
>   (`interview_client.py`), the phase router (`routing.py`), copy and the dossier
>   layout (`messages.py`), and the `/restart` command. The model is
>   `gemini-3.1-flash-lite`, read from `config.py` only.
> - **Flow:** a passing photo sends the first question (model-generated, empty
>   history). Each answer is committed only after the model replies. The model may
>   finish from 5 answers on; at 7 answers the code forces `finish`. The dossier is
>   delivered as one text message and the chat moves to `DOSSIER_READY`.
> - **Deliberate deviations from the wording above (approved by the Tech Lead):**
>   1. **Pass path:** a passing photo sends the first question, not `hey mate!`. The
>      Phase 2 confirmation is retired on the photo path. `hey mate!` remains the
>      reply to text while awaiting a photo.
>   2. **New phase `DOSSIER_READY`** is driven after `INTERVIEWING`, so Phase 4 can
>      add `GENERATING` without a rewrite. `GENERATING` and `DELIVERING` are declared
>      but not driven, so the handoff from completion to `GENERATING` is not built.
>   3. **Dossier contents:** quirks (3 to 7, each a topic and an observation), plus
>      one suggested animal with a short reason. The "accumulated answers" and
>      "inferred behaviours" wording is not stored in the dossier. The answers live
>      only in session state for the duration of the interview.
>   4. **Length bounds (working defaults):** questions 300 characters, answers 1000,
>      quirk topic 40, quirk observation 300, animal name 60, animal reason 300.
>      Single line for questions, quirk text, and animal text.
> - **Error handling:** a provider timeout, rate limit, or outage is retried at most
>   once, then the user is asked to resend the answer (nothing is committed). An
>   invalid reply gets one repair attempt, then the same resend message. The
>   orchestrator enforces the 5-to-7 range; the model cannot override it.
> - **Logging:** chat id, phase before and after, question index, and the animal's
>   name are logged. Answers, question text, quirk text, reasons, image bytes, and
>   keys are not.
> - **Verified offline:** `scripts/test` and `scripts/hooks` both exit 0. The suite
>   runs with no network and no ADK import in tests; the model is faked at the seam.
> - **Deferred (live):** sending a portrait and receiving the first question, a full
>   5-to-7 question run, a mid-interview photo nudge, `/restart` mid-interview, two
>   chats interleaved, and a log check on a real run. These need real credentials.
>   The Phase 2 live checks are also still deferred.
> - **Confirmed by the Tech Lead (2026-11-24, requirements.md Open decisions 3, 4,
>   7, 8):** the `DOSSIER_READY` phase (recorded in TECH.md state machine); the 300
>   question and 1000 answer character limits (recorded in TECH.md interview limits);
>   the shared "one at a time" copy with the Bouncer; and `/start` counting as a
>   normal answer during an interview until Phase 7.

> **Verifier note (2026-11-24, branch `feature/2026-11-24-interviewer`, uncommitted; re-verified after fixes).**
>
> - **Verified offline:** `scripts/test` exits 0 (364 tests; ruff check, ruff format,
>   mypy, pytest). `scripts/hooks` exits 0 and ruff changed nothing.
> - **Blockers from the first pass are fixed and verified:**
>   1. **B-1, non-retryable provider errors:** HTTP 400, 401, 403, and 404 now map to
>      `ProviderRejected` and are attempted once. A probe confirmed 1 attempt for 400
>      and 2 for 503. The Bouncer test covers the same policy.
>   2. **B-2, `google.genai` in a test docstring:** reworded. Zero `google.adk` or
>      `google.genai` hits under `tests/`.
> - **Red-first evidence (validation B):** accepted by the Tech Lead as an exception,
>   because the work is uncommitted and no red run log was kept.
> - **Open, which keeps `[~]`:**
>   1. **ADK import wording (validation K): resolved.** Tech Lead chose option a: the rule
>      is now "only the model-client modules (`vision_client.py`, `interview_client.py`)
>      import ADK".
>   2. **Requirements decisions 1, 2, 5, 6, 9, 10, and 11 (validation L):** not
>      re-confirmed this round.
>   3. **`.env` check (validation K/M):** `.env` holds two values that are not
>      placeholder-shaped. It was not read in this run. The Tech Lead should confirm
>      they are real credentials kept out of logs and the repo.
> - **Live Telegram and Gemini checks: DEFERRED (validation section M, six items),**
>   by Tech Lead instruction. No live bot run and no model call were made. No live pass
>   is claimed.
> - **Deliberate changes, recorded:** a passing photo sends the first question, not
>   `hey mate!`. The chat moves to `DOSSIER_READY` after completion. `/start` counts as
>   an answer during an interview until Phase 7.
>
> Status stays `[~]` because the ADK wording and the open decisions are not closed.
> Flip to `[x]` once the Tech Lead resolves them.

---

## Phase 4 — Converter (hybrid portrait)

**Goal:** the payoff image.

- Photo + dossier passed to the image model with a prompt that preserves the
  user's recognisable features while fusing the dossier's animal and traits.
- Returned image is delivered to the chat with a short caption naming the
  animal.
- Temp file written outside the repo, deleted after send.
- Failure path: apologise, keep the dossier, offer `/restart`.

**Acceptance criteria**
- A completed interview produces exactly one hybrid portrait delivered to the
  correct chat.
- The caption names the suggested animal from the dossier.
- No image bytes or base64 appear in logs.
- Generation failure degrades to a message; the process stays alive.
- No leftover files in the repo tree or temp dir after the send.

**Serves:** multimodal image generation + delivery.

Status: `[ ]`

---

## Phase 5 — Scripter (narration text)

**Goal:** the words.

- One-paragraph script, 60–90 words, third person, British RP nature-documentary
  register, derived strictly from the dossier.
- Held in session state for the Narrator; not yet delivered to the user.
- Retry once on validation failure, then deliver portrait with an apology.

**Acceptance criteria**
- Script word count is within 60–90 words and is a single paragraph.
- Script references the dossier's behaviours and the chosen animal, and invents
  no unsupported personal facts.
- Validation failure produces one retry, then a graceful fallback.
- Unit tests assert the length/format contract with a faked model response.

**Serves:** constrained text generation.

Status: `[ ]`

---

## Phase 6 — Narrator (TTS voice note)

**Goal:** the voice.

- Script is sent to Gemini TTS at
  `gemini-3.1-flash-tts-preview`.
- Output converted to OGG/Opus and delivered via `sendVoice` as a voice note.
- TTS failure falls back to delivering the script as plain text so the run is
  not lost.
- Exactly one synthesis per successful run.

**Acceptance criteria**
- A completed run ends with a playable voice note in the chat.
- The voice note is OGG/Opus and plays inline in Telegram.
- TTS failure still delivers the script text and logs at error level.
- No duplicate synthesis when a duplicate update arrives during delivery.
- Temp audio removed after send.

**Serves:** TTS tool integration + audio delivery.

Status: `[ ]`

---

## Phase 7 — Resilience & reset semantics

**Goal:** it never breaks in the user's hands.

- `/start` and `/restart`: purge dossier, interview progress, suggested animal,
  photo reference, temp media, and derived output; return to the opening prompt.
  No process restart; other chats untouched.
- Wrong-payload guards for every `(phase, payload type)` combination, including
  stickers, voice notes, documents, media groups, captions, and edited messages.
- Bounded retry + timeout on every provider call.
- Structured logging across all stage boundaries; no secrets or raw media logged.
- README and this ROADMAP updated to reflect verified behaviour.

**Acceptance criteria**
- `/restart` at every phase returns the chat to a clean start and leaves the
  other chat's session intact.
- Each wrong-payload case yields a nudge (or silence for Telegram noise) and no
  state change.
- Provider timeout on each stage follows the defined degradation policy.
- Grep of logs from a full run shows no token, key, image bytes, or full answer
  text.
- `scripts/test` and `scripts/hooks` pass clean.

**Serves:** reset/rubric-resilience criterion.

Status: `[ ]`

---

## Definition of done (every phase)

1. Red/Green TDD followed; new tests fail before implementation.
2. `scripts/test` passes; `scripts/hooks` passes (ruff + mypy).
3. No secrets, raw media, or user answers in logs or the repo.
4. README touched if behaviour or setup changed.
5. Spec folder for the phase updated to match what was actually built.
6. Phase status flipped to `[x]` only after the verifier confirms it.

## Explicitly deferred (see MISSION.md out-of-scope)

Webhooks, persistence, video, group support, payments, rate limiting,
localisation, image regeneration, analytics. If a later spec wants any of these,
it must amend MISSION.md first.
