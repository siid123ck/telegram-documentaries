# Validation — Scripter / Narration Text (Phase 5)

Work top to bottom. Every step is checkable and produces an observable result.
"Mergeable" means every box is ticked, or the exception is explicitly accepted by the
Tech Lead. Offline validation must be complete and green regardless of the
live-credential deferral. Do not claim a live pass that was not run.

**Verification status: not yet run.** Marks: `[x]` pass · `[ ]` open · `DEFERRED` live
check, not run.

## A. Environment and dependencies

- [ ] On `feature/2026-11-24-scripter`, `uv sync --locked` completes.
- [ ] No new dependency: `git diff main -- pyproject.toml uv.lock` is empty.
- [ ] `gemini-3.1-flash-lite` is defined once in `src/` (as `_FLASH_LITE` in
      `config.py`). `MODELS["scripter"]` refers to it.
- [ ] `scripts/test` and `scripts/hooks` exist and run.

## B. Tests are ground truth (Red/Green evidence)

- [ ] `scripts/test` exits `0` and visibly runs ruff check, ruff format --check, mypy,
      and pytest.
- [ ] `scripts/test` fails when deliberately broken. Inject an unused import into a
      scratch copy; it fails with `F401` and exit 1. The real tree is not touched.
- [ ] Each new test file was seen red before its implementation. Keep the red run
      output in the verifier note (commit history is not available while uncommitted).
- [ ] Any existing test whose expected behaviour changed (group 6, step 2) is listed by
      name in the verifier note, with the reason, and was updated red first.
- [ ] The suite performs no network I/O. A scratch copy with no `.env` and no
      `TELEGRAM_BOT_TOKEN` or `GEMINI_API_KEY` in the environment passes.
- [ ] `grep -r "google.adk\|google.genai" tests/` returns zero hits.
- [ ] `scripts/hooks` exits `0`; `scripts/test` is re-run afterwards with no unexpected
      file changes.

## C. Typed contracts at the boundary

- [ ] `Script` accepts exactly 60 and exactly 90 words; rejects 59 (`word_count_low`)
      and 91 (`word_count_high`).
- [ ] `Script` rejects a newline or carriage return (`multi_paragraph`), and an empty or
      whitespace-only text (`empty`).
- [ ] Each S10 deny-list character is rejected (`markdown`). Parentheses and apostrophes
      are accepted.
- [ ] `Script` rejects extra fields.
- [ ] Error text carries the reason code and count, never the script text.
- [ ] A non-JSON reply, or JSON without a string `script`, becomes `InvalidResponse`.
- [ ] The repair note names the rule and count and does not contain the failed text.

## D. Happy path

- [ ] A completed run sends the portrait, then exactly one script call, then exactly
      one text message carrying the exact script, in that order.
- [ ] The script goes to the correct chat (chat id asserted on the send).
- [ ] `SessionState.script` holds the validated text after the send. Phase `DELIVERING`,
      `in_flight` is `False`, the dossier is kept.
- [ ] The script text is sent as plain text, with no `parse_mode`.
- [ ] No Phase 6 code is present: no TTS call, no `sendVoice`, no OGG, no audio file.

## E. Word count, shape, and markdown

- [ ] Parametrised tests cover 59, 60, 90, and 91 words, with a repair call on each
      rejected case.
- [ ] Newline, carriage return, empty, and whitespace-only cases are rejected.
- [ ] Each markdown character and each markdown form (bold, italics, heading, bullet,
      link) is rejected.

## F. Retry and fallback

- [ ] Validation failure on both attempts → exactly 2 calls, `SCRIPT_FAILED_TEXT` sent,
      `script is None`, phase `DELIVERING`, the portrait is not re-sent.
- [ ] Validation failure on attempt 1 and a valid script on attempt 2 → the script is
      sent and stored.
- [ ] Timeout, 429, or 5xx on both attempts → exactly 2 attempts, then the apology.
- [ ] Timeout, 429, or 5xx on attempt 1 and a valid script on attempt 2 → the script is
      sent.
- [ ] Validation failure then a timeout → 2 attempts total (shared budget, S1), then the
      apology.
- [ ] 400, 401, 403, and 404 → exactly 1 attempt, then the apology.
- [ ] A plain provider retry waits `RETRY_BACKOFF_SECONDS`; the validation repair waits
      no backoff.
- [ ] `SCRIPT_TIMEOUT_SECONDS == 10.0`; a transport timeout maps to `ProviderTimeout`.
- [ ] Total model attempts per run never exceed 2 (asserted across all cases).

## G. `/restart` and duplicates

- [ ] `/restart` while the script call is blocked → reset, `OPENING_PROMPT` sent, phase
      `AWAITING_PHOTO`. It is not nudged with `ONE_AT_A_TIME_TEXT`.
- [ ] When the blocked call returns, nothing is sent and the state does not change.
      An `info` line records the drop.
- [ ] `/restart` between commit and send: no send after the reset.
- [ ] A new photo after the restart starts a fresh interview.
- [ ] A text or photo during the script step → `ONE_AT_A_TIME_TEXT`; the script client
      is called exactly once.
- [ ] A text or photo after the script step → `ALREADY_DONE_TEXT`; no script call.
- [ ] A duplicate sent after `finish_delivery` and before the script completes is
      nudged (no window, S3).

## H. State and isolation

- [ ] `STATE_VERSION == 4`; a stored version `3` is discarded on read.
- [ ] `script` is set only in `DELIVERING` (invariant tested; `StateError` on violation).
- [ ] `begin_script`, `commit_script`, and `abandon_script` refuse a stale epoch and
      change nothing.
- [ ] `begin_script` refuses a second call while in flight.
- [ ] `reset` clears `script` from each phase.
- [ ] Cross-chat isolation: chat A's script goes only to chat A; `/restart` in A leaves
      B's interview and answers intact.
- [ ] Stages do not write session fields. Grep: no assignment to `script`, `in_flight`,
      or `phase` outside `state.py`.

## I. Prompt and config

- [ ] `SCRIPTER_INSTRUCTION` states the 60 to 90 word bound, one paragraph, no markdown,
      third person, British RP, and dossier-only grounding.
- [ ] `SCRIPTER_INSTRUCTION` contains no caption or Telegram instruction.
- [ ] `MODELS["scripter"] == "gemini-3.1-flash-lite"`; the Bouncer and Interviewer
      entries are unchanged.
- [ ] Manual check (Tech Lead, deferred to live samples): British spelling, third
      person, the funny-animal framing, and no invented facts. Not checkable offline.

## J. Copy

- [ ] `SCRIPT_FAILED_TEXT` is non-empty, names `/restart`, says the portrait stands, and
      contains no code, error type, provider wording, or stack trace.
- [ ] `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused, not
      redefined.

## K. Logging and safety

- [ ] With `caplog`, a full run logs chat id, phase transitions, attempt numbers,
      outcomes, durations, and error types.
- [ ] The same run never logs the script text (sentinel paragraph), answers, quirks,
      reason text, the prompt, the dossier, the raw model reply, image bytes, tokens, or
      keys.
- [ ] A failed script step logs the error type or reason code at `error` and never the
      provider message.
- [ ] A stale-epoch drop logs at `info` with no text.
- [ ] Source grep over the changed modules: no bare `except`, no `except Exception:
      pass`, no recovered error logged below `error`, no `tempfile`, no `write_bytes`,
      no `open(` for script data, no `parse_mode` on the script send.

## L. Scope discipline

- [ ] No Narrator, TTS, `sendVoice`, OGG, or audio code.
- [ ] No new `Phase` member. `GENERATING` and `DELIVERING` are reused.
- [ ] No script editing, regenerating, or second-script path.
- [ ] No persistence, database, webhook, or inbound HTTP server.
- [ ] `/start` behaviour is unchanged (Phase 7).
- [ ] ADK is imported only in `vision_client.py`, `interview_client.py`,
      `converter_client.py` (Phase 4), and `scripter_client.py`. Grep `src/` to confirm.

## M. Documentation

- [ ] `README.md` describes the script step, the plain-text 60 to 90 word paragraph,
      the stored script (for Phase 6), the apology with `/restart`, `/restart` during
      the script step, and the resting `DELIVERING` phase.
- [ ] `SPECS/ROADMAP.md` Phase 5 status is not flipped by the build. The verifier
      updates it.
- [ ] Decisions S1 to S16 in `requirements.md` are confirmed or revised by the Tech
      Lead before merge. In particular: S1 (shared attempt budget of 2), S4 (epoch
      guard), S10 (markdown deny-list), and S11 (10 s timeout).

## N. Live verification (requires real credentials; DEFERRED)

- DEFERRED: a completed run sends the portrait, then one script message, in the same
  chat.
- DEFERRED: a live sample reads as one paragraph of 60 to 90 words, in third person,
  British RP, with no markdown, and invents no facts beyond the dossier (Tech Lead
  review).
- DEFERRED: `/restart` during a real script call resets the chat promptly and the late
  script is not delivered.
- DEFERRED: a duplicate during a real script call gets the one-at-a-time nudge and
  starts no second call (check the provider usage count).
- DEFERRED: a forced provider failure (for example, an invalid key in a scratch run)
  shows the apology only, with no payload or stack trace.
- DEFERRED: logs for a full run show no script text, tokens, keys, or answer text.

> Deferred until real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` values are supplied. Do
> not fabricate a live pass. The offline checks above do not claim a live pass.

## Secrets and privacy scan

- [ ] No secret-shaped string (a Gemini `AIza…` key or a Telegram `id:token`) appears in
      the change.
- [ ] `.env` and `.env.save` remain gitignored. No `.env`, media, or temp file is in
      the tree.

## Definition of mergeable

1. Sections A to M pass, or each open item is explicitly accepted by the Tech Lead.
2. Section N is deferred (live). The deferral is recorded in the ROADMAP Phase 5
   verifier note.
3. `scripts/test` exit 0 and `scripts/hooks` exit 0, with no tree change after the
   hooks run.
4. No secrets, raw answers, script text, or model replies in logs, the repo, or
   fixtures committed to the tree. No temp files.
5. Exactly one script call per successful run, with at most 2 model attempts in total.
6. No blocking code finding. Decisions S1 to S16 are recorded, and the Tech Lead has
   accepted or revised them.
