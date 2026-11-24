# Validation — Interviewer / Dossier (Phase 3)

Work top to bottom. Every step is checkable and produces an observable result.
"Mergeable" means every box is ticked, or the exception is explicitly accepted by
the user. Offline validation must be complete and green regardless of the
live-credential deferral. Do not claim a live pass that was not run.

**Verification run: 2026-11-24, branch `feature/2026-11-24-interviewer` (uncommitted).**
Marks: `[x]` pass · `[ ]` fail or open · `DEFERRED` live check, not run.

## Results summary (re-verified 2026-11-24 after fixes)

- `scripts/test`: exit **0** (ruff check, ruff format --check, mypy, pytest: 364 passed).
- `scripts/hooks`: exit **0**; ruff reported nothing to change.
- **B-1 fixed and verified:** HTTP 400, 401, 403, and 404 map to `ProviderRejected`
  (non-retryable). A probe confirmed one attempt for 400 through the interview path
  and two for 503. Bouncer test covers the same policy. Section G passes.
- **B-2 fixed and verified:** zero `google.adk` / `google.genai` hits under `tests/`.
- **Tech Lead decisions recorded:** red-first evidence accepted (B); the DOSSIER_READY
  phase, the 300 and 1000 character limits, the shared "one at a time" copy, and
  `/start` counting as an answer until Phase 7 are confirmed (TECH.md, spec, README).
- **Open for the Tech Lead:** the ADK import wording (K), left as-is with no code or
  wording change; and decisions 1, 2, 5, 6, 9, 10, and 11 in L, not confirmed in this
  round.
- No live Telegram or Gemini call was made and the bot was not run. Section M is DEFERRED
  (Tech Lead instruction).

## A. Environment & dependencies

- [x] On `feature/2026-11-24-interviewer`, `uv sync --locked` completes.
      *Exit 0, lock not changed.*
- [x] No new dependency was added; `pyproject.toml` and `uv.lock` are unchanged.
      *`git diff main` shows no change to either file.*
- [x] `gemini-3.1-flash-lite` appears once, in `config.py`.
      *One literal in `src/` (`config.py:17`). Test assertions and SPECS/README text mention it too; those are not scattered model uses.*
- [x] `scripts/test` and `scripts/hooks` exist and run.

## B. Tests are ground truth (Red/Green evidence)

- [x] `scripts/test` exits `0` and visibly runs ruff check, ruff format --check,
      mypy, and pytest.
      *Exit 0. Output shows "All checks passed!", "59 files already formatted", "Success: no issues found in 34 source files", and "355 passed".*
- [x] `scripts/test` fails when deliberately broken (injected unused import, then revert in a scratch copy).
      *In a scratch copy, an unused `import os` in `interview.py` gave exit 1 with `F401`. The real tree was not touched.*
- [x] Each new test file was seen red before its implementation (evidence in the run log or commit history).
      *Accepted by the Tech Lead (2026-11-24) as an exception. The work is uncommitted and no red run log was kept, so there is no commit-history evidence.*
- [x] The suite performs **no network I/O**: with `.env` absent and no credentials in the environment, `scripts/test` still passes.
      *Scratch copy with no `.env*` and no credential variables: exit 0, 355 passed. Also run with sockets blocked at `connect` (sitecustomize): exit 0. `unshare` is not permitted in this sandbox, so that was the substitute.*
- [x] `grep -r "google.adk\|google.genai" tests/` returns zero hits.
      *Re-run 2026-11-24: zero hits. The B-2 docstring was reworded.*
- [x] `scripts/hooks` exits `0` and leaves the tree byte-for-byte unchanged.
      *Exit 0. Hook output says "All checks passed!" and "59 files already formatted", so no ruff rewrite happened. Hashes of modified tracked files were identical before and after.*

## C. Typed contracts at the boundary

- [x] `Dossier` rejects extra fields, fewer than 3 or more than 7 quirks, and a missing or empty `suggested_animal`.
      *Probe: 2 quirks, 8 quirks, extra key, missing animal, and empty name all rejected.*
- [x] `Quirk` and `SuggestedAnimal` reject empty and overlength fields.
      *Probe: topic 41 chars and name 61 chars rejected. Tests cover the limits.*
- [x] `InterviewTurn` accepts `ask` with a question and `finish` with a dossier; rejects both set, neither set, and any extra field.
      *Probe: ask with neither, ask with both, and finish with neither all rejected. Extra field rejected by test.*
- [x] `Answer` rejects empty, whitespace-only, and over-1000-character text.
      *Probe: whitespace rejected; 1001 rejected; 1000 accepted.*
- [x] A model response that fails validation triggers exactly one repair retry, then a typed `InvalidResponse`. No prose scraping anywhere.
      *`interview._request_turn` runs one repair; tests cover the second failure. No regex parsing in the interview path.*

## D. Interview behaviour (offline, faked model)

- [x] **Full interview, 5 questions:** a passing photo sends the first question (not `hey mate!`); each answer produces exactly one next message; the dossier arrives after 5 answers when the model finishes; phase ends at `DOSSIER_READY`.
      *`test_interview.py` 5-question test; `test_interview_routing.py` pass-path test.*
- [x] **Full interview, 7 questions:** the 7th answer forces `finish`; the code does not ask an 8th question.
      *`test_a_seven_question_interview_forces_finish_at_seven_answers`; `test_an_ask_after_seven_answers_twice_is_refused`.*
- [x] **Range enforced in code:** a `finish` before 5 answers is rejected, goes through one repair retry, and then produces the resend message with no state change.
      *`test_a_finish_before_five_answers_gets_one_repair_then_is_refused`.*
- [x] One question per message.
      *`test_each_outbound_message_carries_exactly_one_question`.*
- [x] Questions after the first are built from the earlier answers: the fake client receives the full committed history on each call.
      *`test_a_later_question_is_built_from_the_committed_answers_so_far`.*
- [x] The dossier layout contains quirks and the suggested animal with its reason, and nothing else.
      *Rendered sample (below) shows only quirk lines, animal name, and reason.*

## E. Wrong payloads & nudges (no state change)

- [x] Text in `AWAITING_PHOTO` → `hey mate!`; state unchanged.
- [x] Photo in `INTERVIEWING` → `PHOTO_MID_INTERVIEW_TEXT`; the Bouncer and the interview client are not called; state unchanged.
- [x] Empty or whitespace-only answer → `EMPTY_ANSWER_TEXT`; not stored; question still outstanding.
- [x] Over-1000-character answer → `ANSWER_TOO_LONG_TEXT`; not stored.
- [x] Text with no outstanding question → nudge; state unchanged; logged at `warning`.
- [x] Photo or text in `DOSSIER_READY` → `ALREADY_DONE_TEXT`; state unchanged.
- [x] Stickers, voice notes, documents, edited messages, and malformed updates → no exception; no state change.
      *Routing registers only TEXT and PHOTO handlers plus `/restart`; `test_noise_and_malformed_updates_are_ignored_without_an_exception`.*

## F. Reset

- [x] `/restart` after two answers clears the answers, the outstanding question, the photo reference, and any partial dossier.
- [x] After `/restart`, the chat receives `OPENING_PROMPT` and is in `AWAITING_PHOTO`.
- [x] A new passing photo after reset starts an interview with an empty history.
- [x] `/restart` in `DOSSIER_READY` clears the completed dossier.
- [x] `/restart` does not restart the process and does not touch other chats.
      *`test_restart_leaves_the_other_chat_untouched`; `reset` only replaces one key.*

## G. Provider errors & degradation

- [x] Timeout, 429, and 5xx are retried at most 2 times with backoff (fake counts attempts).
      *`test_a_retryable_failure_twice_is_raised_after_exactly_two_attempts`. Backoff tested.*
- [x] A non-retryable error fails fast (attempt count is 1).
      *B-1 fixed. Re-run 2026-11-24: 400, 401, 403, and 404 map to `ProviderRejected`, which is not retryable. Probe through `next_turn_with_retry`: 400 gives attempts=1. Tests: `test_vision_client.py` (`test_a_provider_rejection_is_attempted_exactly_once`, parametrized status mapping). Non-blocking gap: no direct test on the interview path; the shared mapping is covered.*
- [x] After the bounded retry, a provider failure sends `RESEND_ANSWER_TEXT` and does not commit the answer. The user can resend the same answer.
      *`test_a_provider_timeout_after_retry_commits_nothing`; `test_a_resent_answer_after_a_provider_failure_is_committed_once`.*
- [x] A failing first question (after retry) sends `RESEND_TEXT`, stores no photo, and stays `AWAITING_PHOTO`.
      *`test_a_failed_first_question_stores_no_photo_and_stays_awaiting_photo`.*
- [x] An `InvalidResponse` after one repair retry sends the resend message and is logged at `error`.
      *`test_a_recovered_invalid_reply_is_logged_at_error_level`; routing `interview_turn_failed` at error.*
- [x] No exception escapes any handler; the next update is served after a failure.
      *`test_an_unexpected_error_is_logged_and_the_next_update_is_still_served`; `test_a_handler_crash_during_text_never_escapes_the_router`.*

## H. State & isolation

- [x] `SessionState` carries `schema_version=2`; a stored version of `1` is discarded on read.
- [x] Session invariants hold after each commit: `outstanding_question` is set only in `INTERVIEWING`; `dossier` is set only in `DOSSIER_READY`.
      *`state._check_invariants` runs on every `_replace`.*
- [x] Two chats interleaved: each dossier contains only its own answers; a reset in one chat leaves the other's outstanding question and answers intact.
- [x] The in-flight guard: an update during an in-flight call gets `ONE_AT_A_TIME_TEXT`; no second model call is made.
- [x] Stages do not write session fields directly; only the state driver does.
      *Grep for direct assignment to session fields and `_states[` outside `state.py`: no hits.*

## I. Logging & safety

- [x] With `caplog`, a full run logs `chat_id`, phase transitions, question index, and the suggested animal's `name`.
      *`test_a_full_run_logs_chat_phases_question_index_and_animal_name`.*
- [x] The same run never logs answer text, quirk text, animal `reason`, question text that echoes an answer, image bytes, base64, tokens, or keys (sentinel strings in the fakes).
      *`test_a_full_run_never_logs_answers_questions_quirks_reasons_or_images`.*
- [x] `@log_stage` records entry, outcome, and duration at each interview boundary.
      *`test_each_interview_boundary_records_entry_outcome_and_duration`.*
- [x] Grep source: no bare `except`, no `except Exception: pass`, and no recovered error logged below `error` in the new modules.
      *Only `log+re-raise` or `log+map` sites use `except Exception`. Recovered invalid replies are logged at error.*
- [x] No `Answer` or `Dossier` object is passed to a logger directly.
      *Grep: logger calls carry only ids, phases, counts, and `animal=<name>`.*

## J. Copy

- [x] Every copy constant is non-empty and free of code, stack-trace, error-code, and prompt wording (tested).
- [x] `HEY_MATE` is imported from `gateway.py`, not duplicated.
      *`messages.py` aliases `gateway.REPLY_TEXT`.*
- [x] `ONE_AT_A_TIME_TEXT` and `RESEND_TEXT` are reused from the Bouncer.
      *Both constants already exist in Phase 2 `messages.py` on `main`, with unchanged wording.*
- [x] The tone is investigative and playful, and the dossier text claims nothing beyond the validated fields.
      *Rendered sample dossier reviewed:*

  ```
  Field notes complete. Here is your dossier:

  - sleep: Naps in a bin.
  - forage: Digs for grubs at dusk.
  - territory: Patrols the fence line.

  Suggested animal: Meerkat
  Why: Stands guard like a pro.
  ```

## K. Scope discipline

- [x] No Converter, image generation, Scripter, Narrator, or TTS code.
- [x] `GENERATING` and `DELIVERING` are declared only, with no transitions.
- [x] No dossier field beyond `quirks`, `suggested_animal`, and the animal's `name` and `reason`.
- [x] No persistence, database, webhook, or inbound HTTP server.
      *Grep for db, pickle, dump, webhook, and HTTP server: none. The only `json.dumps` is the request body.*
- [x] `/start` behaviour unchanged (Phase 7).
      *`/start` is still plain text. It gets `hey mate!` while awaiting a photo, and during an interview it is taken as an answer (requirements Open decision 8, pending user confirmation).*
- [x] Only the model-client modules (`vision_client.py`, `interview_client.py`) import ADK; no other module imports a provider SDK. (Tech Lead decision: option a.)
      **OPEN, Tech Lead decision. Left as-is; wording and code not changed.** Phase 3 modules: only `interview_client.py` imports ADK (lazily). `vision_client.py`, the Phase 2 module, also imports it. This was approved in Phase 2 but contradicts the literal wording. Either narrow the wording to "no other Phase 3 module", or require the Bouncer's import to move too.
- [x] `.env` is gitignored and absent from `git status`; no media, caches, or test artifacts are staged.
      *`.gitignore` covers `.env` and `.env.*`. The index is empty. **Note:** the `.env` file holds two values of 46 and 53 characters. Neither matches a placeholder marker, which contradicts the "placeholder-only" premise. I did not print them. Confirm whether these are real credentials. If they are, they must stay out of every log and repo.*

## L. Documentation

- [x] `README.md` describes the interview stage, the 5-to-7 question range, the dossier as text, `/restart`, and the changed pass path (first question, not `hey mate!`).
      *One sentence was corrected by the verifier: the photo is stored once the first question is generated, not "delivered".*
- [x] `SPECS/ROADMAP.md` Phase 3 status is updated by the verifier, noting the deliberate change to the pass path, the `DOSSIER_READY` phase, and the deferred live checks. Phase status stays `[~]` (not `[x]`), because B-1 is open.
- [ ] Every decision in `requirements.md` "Open decisions / assumptions" is confirmed or revised by the user before merge (notably the `DOSSIER_READY` phase and the 300/1000 character bounds).
      **Partly confirmed (2026-11-24).** Confirmed by the Tech Lead and recorded in TECH.md and the spec: decision 3 (`DOSSIER_READY`), decision 4 (300/1000 bounds), decision 7 (shared one-at-a-time copy), decision 8 (`/start` counts as an answer until Phase 7). Still open: decision 1 (approved in the spec text, not re-confirmed here), decisions 2, 5, 6, 9, 10, and 11.

## M. Live verification (requires real credentials; DEFERRED)

- DEFERRED: send a portrait photo; confirm the first question arrives instead of `hey mate!`.
- DEFERRED: answer 5 to 7 questions; confirm one question per message and a dossier at the end.
- DEFERRED: send a photo mid-interview; confirm the nudge and no state change.
- DEFERRED: `/restart` mid-interview; confirm a clean restart.
- DEFERRED: two chats interleaved; confirm no bleed.
- DEFERRED: logs show no traceback, tokens, keys, image bytes, or answer text.

> Deferred by Tech Lead instruction (2026-11-24). `.env` holds credentials that are not
> verified as live. Do not fabricate a live pass. The offline sections (A to L) are
> the only passes claimed.
> No live bot run and no model call were made in this verification.

## Definition of mergeable

1. Sections A to L pass. **Not met:** K ADK-import wording (Tech Lead decision, open) and
   L decisions 1, 2, 5, 6, 9, 10, and 11 (not yet confirmed). B-1, B-2, and red-first (B) are
   now resolved.
2. Section M passes **or** is explicitly deferred by the user, recorded in ROADMAP Phase 3.
   **Deferred** by Tech Lead instruction and recorded in ROADMAP Phase 3.
3. `scripts/test` and `scripts/hooks` are clean. **Met:** both exit 0 (364 passed).
4. No secrets, raw image bytes, or user answers in logs, the repo, or fixtures. **Met** for
   logs, repo, and fixtures. The `.env` check remains open: the file holds two values that are
   not placeholder-shaped. Their status was not checked this round (`.env` was not read). The
   Tech Lead should confirm they are real credentials kept out of logs and the repo.
5. The diff against `main` has no blocking findings, and the deliberate `hey mate!` change is
   recorded. **Met** for B-1 and B-2. The `hey mate!` change is recorded in ROADMAP and README.
