# Plan — Interviewer / Dossier (Phase 3)

Red/Green TDD throughout. For each task group: write the failing test(s), run
`scripts/test` to confirm **red**, implement, run `scripts/test` to confirm
**green**. Run `scripts/hooks` at the end of each group that changes code. Use
`scripts/test` and `scripts/hooks` only, never raw `pytest`/`ruff`/`mypy`.
Invoke the `write-tests` skill before writing any tests.

Branch: `feature/2026-11-24-interviewer` (already created from `main`).
Spec folder: `SPECS/2026-11-24-interviewer/`.

## Task group 1 — Dossier and turn contracts (RED first)

1. Confirm `google-adk` is already present from Phase 2 (`pyproject.toml`,
   `uv.lock`). If not, stop and report. Do not add a dependency silently.
2. Write `tests/test_interview_models.py` (RED):
   - `Quirk`: valid; empty or over-40 `topic` rejected; empty or over-300
     `observation` rejected; extra fields rejected.
   - `SuggestedAnimal`: valid; empty `name` rejected; over-60 `name` rejected;
     over-300 `reason` rejected.
   - `Dossier`: valid with 3 and with 7 quirks; 2 and 8 quirks rejected; extra
     top-level fields rejected; missing `suggested_animal` rejected.
   - `InterviewTurn`: `ask` with `question` valid; `finish` with `dossier` valid;
     `ask` with `dossier` rejected; `finish` with `question` rejected; neither
     rejected; both rejected; over-300 `question` rejected.
   - `Answer`: valid; empty and whitespace-only rejected at the answer boundary;
     over-1000 rejected.
3. Run `scripts/test` → red.
4. Implement `src/telegram_documentaries/interview_models.py`: `Quirk`,
   `SuggestedAnimal`, `Dossier`, `Answer`, `InterviewTurn` (Pydantic v2,
   `extra="forbid"`), with the bounds from `requirements.md`. Use a model
   validator for the `ask`/`finish` rule.
5. Add the bounds as named constants in one place (no magic numbers in
   validators).
6. Run `scripts/test` → green.

**Acceptance:** every malformed contract is rejected at construction; bounds are
named constants; no ADK import.

## Task group 2 — Copy and config (RED first)

1. Write `tests/test_messages.py` (RED):
   - each copy constant exists and is non-empty, with no code, stack-trace, or
     error-code wording (assert a small set of forbidden substrings);
   - `HEY_MATE` is the same object as `gateway.REPLY_TEXT` (imported, not
     duplicated);
   - the dossier layout renders quirks and the animal from validated fields only,
     and contains the animal name and reason;
   - `ONE_AT_A_TIME_TEXT` and `RESEND_TEXT` are reused from the Bouncer, not
     redefined.
2. Write `tests/test_config.py` assertion (RED): `gemini-3.1-flash-lite` occurs
   once in `config.py`, and the interview model is read from the same mapping
   the Bouncer uses.
3. Run `scripts/test` → red.
4. Implement:
   - `messages.py`: add the new constants (`OPENING_PROMPT`,
     `PHOTO_MID_INTERVIEW_TEXT`, `RESEND_ANSWER_TEXT`, `EMPTY_ANSWER_TEXT`,
     `ANSWER_TOO_LONG_TEXT`, `ALREADY_DONE_TEXT`) and `render_dossier(dossier)`.
   - `config.py`: add an `interviewer` entry to the existing model mapping.
5. Run `scripts/test` → green.

**Acceptance:** copy is single-sourced; the dossier renderer takes only the
validated `Dossier`; model ID is still in one place.

## Task group 3 — Versioned session store and state driver (RED first)

1. Write `tests/test_state.py` additions (RED):
   - a fresh chat starts `AWAITING_PHOTO` with empty answers and no question;
   - `begin_turn` sets `in_flight`; a second `begin_turn` while in flight is
     refused;
   - `commit_question` sets `outstanding_question` and `phase=INTERVIEWING`;
     `commit_answer` appends one `Answer` and clears the outstanding question
     and the in-flight flag;
   - `commit_dossier` sets `dossier`, clears the question, and sets
     `phase=DOSSIER_READY`;
   - `reset` clears the photo reference, answers, question, dossier, and
     in-flight flag, and returns to `AWAITING_PHOTO`;
   - a stored `schema_version=1` is discarded on read and a fresh session starts;
   - two chats keep independent state; mutating one leaves the other untouched;
   - invariants from `requirements.md` hold after each commit (assert in a helper).
2. Run `scripts/test` → red.
3. Implement in `state.py`: `Phase` (driven: `IDLE`, `AWAITING_PHOTO`,
   `INTERVIEWING`, `DOSSIER_READY`; declared only: `GENERATING`, `DELIVERING`),
   `SessionState` with `schema_version=2`, and `SessionStore` with the single
   driver methods. Stages never mutate fields.
4. Run `scripts/test` → green.

**Acceptance:** the driver is the only writer; invariants hold; version mismatch
restarts cleanly; isolation holds.

## Task group 4 — Interview client seam and ADK agent (RED first)

1. Write `tests/test_interview_client.py` (RED) using a fake client and a fake
   ADK-free transport seam:
   - a fake `InterviewClient` returns scripted `InterviewTurn`s and satisfies the
     protocol;
   - `AdkInterviewClient` maps a provider timeout to `ProviderTimeout`, a 429 to
     `RateLimited`, a 5xx to `VisionUnavailable`, and a schema failure to
     `InvalidResponse` after one repair retry (exercised through the injected
     transport, never the network);
   - retries are bounded at 2 attempts with backoff; non-retryable errors fail
     fast (attempt count asserted).
2. Run `scripts/test` → red.
3. Implement:
   - `interview_client.py`: `InterviewClient` protocol and `AdkInterviewClient`
     (the only interview module importing ADK). It builds the `Agent` with the
     interview model from config and the interview instruction, runs it via
     `Runner` + `InMemorySessionService`, applies the 10 s timeout and bounded
     retry, and validates the output against `InterviewTurn`.
   - `prompts.py`: `INTERVIEW_INSTRUCTION` (investigative, playful nature
     documentary researcher; ask one question per turn, built from prior
     answers; choose `ask` or `finish`; when finishing, produce 3 to 7 quirks
     with suggested topics `sleep`, `territory`, `forage`, and a suggested animal
     with a short reason; no invented facts beyond the answers; output strict
     JSON). Prompt text stays here, not in orchestration.
4. Run `scripts/test` → green.

**Acceptance:** tests never import ADK or open sockets; only
the model-client modules (`vision_client.py`, `interview_client.py`) import ADK; retry and timeout are bounded and observable.

## Task group 5 — Interview orchestrator (RED first)

1. Write `tests/test_interview.py` (RED) with the fake `InterviewClient`:
   - **Full interview (5 questions):** photo pass → first question sent;
     5 answers → `finish` allowed → dossier sent → `DOSSIER_READY`; exactly one
     question per message.
   - **Full interview (7 questions):** the 7th answer forces `finish`; the code
     does not ask a question at 7 answers.
   - **Early finish is invalid:** `finish` with 4 answers → one repair retry →
     `InvalidResponse` → resend message, no state change.
   - **Schema failure:** invalid dossier → one repair retry with the validation
     error → valid → accepted; invalid again → `InvalidResponse`, resend, no
     state change.
   - **Provider error:** timeout after retry → resend message, the answer is not
     committed, the question stays outstanding.
   - **Empty and over-long answers:** nudge, answer not stored, no model call.
   - **Text with no outstanding question:** nudge (or `hey mate!` in
     `AWAITING_PHOTO`); no state change.
   - **Photo mid-interview:** nudge; the fake Bouncer and the fake client are not
     called; state unchanged.
   - **In-flight guard:** an update while a call is in flight gets
     `ONE_AT_A_TIME_TEXT`; no second model call.
   - **Reset mid-interview (`/restart` after 2 answers):** answers, question,
     photo reference, and partial state cleared; `OPENING_PROMPT` sent; phase
     `AWAITING_PHOTO`; a new photo starts an empty history.
   - **Dossier delivered:** further text or photo in `DOSSIER_READY` → nudge
     pointing to `/restart`, no state change.
   - **Unexpected exception:** caught, logged at error; the next update is served.
2. Run `scripts/test` → red.
3. Implement `interview.py`:
   - `async def handle_photo_pass(chat_id, photo_file_id, client, store)` → first
     question;
   - `async def handle_answer(chat_id, text, client, store)` → next question or
     dossier, with the repair-retry rule and the 5-to-7 enforcement;
   - `def handle_restart(chat_id, store)`;
   - each returns a typed result (text to send plus the new phase). It emits no
     Telegram calls itself.
   - Decorate the boundaries with `@log_stage`.
4. Run `scripts/test` → green.

**Acceptance:** the 5-to-7 range holds in code regardless of model output; the
resend path never commits a failed answer; the orchestrator is the only caller
of the store's writing methods.

## Task group 6 — Routing and wiring (RED first)

1. Write `tests/test_interview_routing.py` (RED):
   - a passing photo (from the Bouncer) routes to `handle_photo_pass` and sends
     the first question, not `hey mate!`;
   - a rejected photo still sends the Phase 2 rejection and resets to
     `AWAITING_PHOTO`;
   - text in `INTERVIEWING` routes to `handle_answer`; text in `AWAITING_PHOTO`
     sends `hey mate!`;
   - photo in `INTERVIEWING` and `DOSSIER_READY` → nudge, no Bouncer call;
   - `/restart` in each driven phase → reset and `OPENING_PROMPT`;
   - edited messages, stickers, voice notes, and malformed updates → no exception;
   - the Phase 1 text reply constant is imported, not duplicated.
2. Run `scripts/test` → red.
3. Implement:
   - `routing.py` (or extend `photo_handler.py` and `gateway.py`): a single
     `(phase, payload type)` router that calls the Bouncer for photos in
     `AWAITING_PHOTO` and the interview handlers otherwise, and maps typed errors
     to copy;
   - `app.py`: register the `/restart` command handler; keep existing handlers;
     unknown updates stay ignored.
   - Remove the Phase 2 `hey mate!` photo-pass reply path. Keep the text reply in
     `AWAITING_PHOTO`.
4. Run `scripts/test` → green.

**Acceptance:** every `(phase, payload type)` pair in the Failure behaviour table
has a route; no handler exception escapes; the pass path no longer sends
`hey mate!`.

## Task group 7 — Logging and safety sweep (RED first)

1. Write tests (extend `tests/test_interview.py`) (RED), using `caplog`:
   - a full run logs chat id, phase transitions, question index, and animal name;
   - the same run never contains answer text, question text echoing an answer,
     quirk text, animal reason, image bytes, base64, tokens, or keys (assert on
     the captured text for known sentinel strings planted in the fake answers);
   - `@log_stage` records entry, outcome, and duration on each interview
     boundary;
   - a recovered `InvalidResponse` is logged at `error`.
2. Run `scripts/test` → red.
3. Implement: wire `@log_stage` and the field allow-list on the interview and
   routing boundaries. Make sure nothing logs `Answer` objects or `Dossier`
   objects directly.
4. Run `scripts/test` → green.
5. Source grep: no bare `except`, no `except Exception: pass`, no recovered error
   below `error` in the new modules.

**Acceptance:** no forbidden content in any log record at any level; decorators
carry the cross-cutting logging.

## Task group 8 — Docs, checks, and hygiene

1. Update `README.md`: describe the interview stage (5 to 7 questions, one at a
   time, dossier as text), the `/restart` command, and the changed pass path
   (first question instead of `hey mate!`). Keep setup, run, and dev-script
   sections accurate. No credentials in docs.
2. Run `scripts/test` → clean.
3. Run `scripts/hooks` → clean, and the tree is unchanged afterwards.
4. Confirm `git status` shows no `.env`, `.venv`, caches, or test artifacts.
   Do not commit (the user asked for no commit in this step).
5. Report what changed for the validation checklist.

**Acceptance:** both dev scripts green; docs reflect the new behaviour; repo
hygiene clean.

## Verification handoff

When this plan is complete, hand off to the verifier with the spec folder path.
Live Telegram and Gemini checks are deferred until real credentials are
supplied. Offline validation must be fully green. ROADMAP Phase 3 status is
updated by the verifier, not by this plan.
