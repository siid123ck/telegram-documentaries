# Plan — Scripter / Narration Text (Phase 5)

Red/Green TDD throughout. For each task group: write the failing test(s), run
`scripts/test` to confirm **red**, implement, run `scripts/test` to confirm
**green**. Run `scripts/hooks` at the end of each group that changes code.

Use `scripts/test` and `scripts/hooks` only, never raw `pytest`, `ruff`, or `mypy`.
Invoke the `write-tests` skill before writing any tests.

`scripts/hooks` runs `ruff check --fix` and `ruff format`, so it can rewrite files.
Re-run `scripts/test` after it. Confirm `git status` afterwards.

Branch: `feature/2026-11-24-scripter` (already checked out).
Spec folder: `SPECS/2026-11-24-scripter/`.
Do **not** write production code outside the task groups. Do **not** commit.
Do **not** start the Narrator or TTS (Phase 6).

Decisions referenced below (S1 to S16) are in `requirements.md`.

## Task group 0 — Baseline (no code changes)

1. Run `scripts/test` and `scripts/hooks` on the untouched branch. Record the test
   count. Both must exit 0 before any change.
2. Confirm `google-adk` and `python-telegram-bot` are in `pyproject.toml` and
   `uv.lock`. Do not add a dependency.
3. Confirm `gemini-3.1-flash-lite` is defined once, as `_FLASH_LITE` in `config.py`.
4. Read `interview_client.py` and `vision_client.py` for the ADK seam pattern
   (lazy construction, `map_provider_error`, `classify_with_retry`). The Scripter
   client follows it. Record any deviation in the plan notes.

**Acceptance:** baseline green; no dependency change; no production code changed.

## Task group 1 — Script contract (RED first)

1. Write `tests/test_scripter_models.py` (RED):
   - a 60-word text is accepted; a 90-word text is accepted; a 59-word text fails
     with `word_count_low`; a 91-word text fails with `word_count_high` (S8, inclusive
     bounds);
   - a text containing `\n` fails with `multi_paragraph`; a text containing `\r`
     fails the same way;
   - an empty string and a whitespace-only string fail with `empty`;
   - each character of the S10 deny-list (`*`, `_`, `#`, `` ` ``, `~`, `[`, `]`,
     `<`, `>`, `|`, `\`) fails with `markdown` (parametrised); parentheses and
     apostrophes are accepted;
   - leading and trailing whitespace is stripped before counting;
   - extra fields are rejected;
   - the error text carries the reason code and the count, and never the script text
     (assert the sentinel paragraph is absent from `str(error)`).
2. Run `scripts/test` → red.
3. Implement `scripter_models.py`:
   - `Script` (Pydantic v2, `extra="forbid"`, strict) with the field `script: str`;
   - a `model_validator` that applies S8 to S10 in order and raises a typed error with
     the reason code and count;
   - named constants: `MIN_WORDS = 60`, `MAX_WORDS = 90`, `MARKDOWN_CHARS`.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** `Script` rejects every malformed paragraph at construction; the
rules are named constants; no rule depends on the model's prose beyond the text.

## Task group 2 — Config, prompt, and copy (RED first)

1. Extend `tests/test_config.py` (RED):
   - `MODELS["scripter"] == "gemini-3.1-flash-lite"`;
   - the bouncer and interviewer entries are unchanged;
   - the flash-lite ID appears exactly once in `config.py` (as `_FLASH_LITE`).
2. Extend `tests/test_prompts.py` (RED): `SCRIPTER_INSTRUCTION` is non-empty and
   states the 60 to 90 word bound, the single paragraph, the no-markdown rule, third
   person, British RP, and dossier-only grounding. It contains no caption or Telegram
   instruction.
3. Extend `tests/test_messages.py` (RED):
   - `SCRIPT_FAILED_TEXT` is non-empty, names `/restart`, says the portrait stands,
     and contains no code, error-type, provider, or stack-trace wording (assert a
     forbidden-substring set, as Phase 4 does);
   - `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused, not
     redefined.
4. Run `scripts/test` → red.
5. Implement:
   - `config.py`: add `"scripter": _FLASH_LITE` to `MODELS`;
   - `prompts.py`: `SCRIPTER_INSTRUCTION`;
   - `messages.py`: `SCRIPT_FAILED_TEXT`.
6. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the model ID is still defined once; the prompt lives in `prompts.py`
only; the apology copy is checked for forbidden wording.

## Task group 3 — Session v4 and script driver methods (RED first)

1. Extend `tests/test_state.py` (RED):
   - `STATE_VERSION == 4`; a stored version `3` is discarded on read and a fresh
     `AWAITING_PHOTO` session starts;
   - `SessionState.script` defaults to `None`;
   - `begin_script(chat_id, epoch)` works only from `DELIVERING` with `script is None`
     and `in_flight=False`; sets `in_flight=True`; returns `False` for a stale epoch
     or from any other phase (S6);
   - a second `begin_script` while in flight returns `False`;
   - `commit_script(chat_id, epoch, text)` works only from `DELIVERING` with
     `in_flight=True` and a matching epoch; sets `script=text` and `in_flight=False`;
     returns `False` and changes nothing for a stale epoch;
   - `abandon_script(chat_id, epoch)` clears `in_flight` only, with a matching epoch;
     returns `False` and changes nothing for a stale epoch;
   - invariant: `script` is set only in `DELIVERING`; a violation raises `StateError`
     at the next commit;
   - `reset` from each of the seven phases clears `script`, `in_flight`, and returns
     to `AWAITING_PHOTO`;
   - two chats keep independent `script` values; a reset in one leaves the other's
     script intact.
2. Run `scripts/test` → red.
3. Implement in `state.py`:
   - `STATE_VERSION = 4`; `SessionState.script: str | None = None`;
   - `begin_script`, `commit_script`, `abandon_script` (S6), each taking the epoch and
     checking it with the existing `SessionStore.epoch`;
   - `_check_invariants` extended for `script`;
   - update the module docstring.
   Stages do not write fields.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the driver is the only writer of `script`; a stale epoch cannot change
a newer state; invariants hold after every commit; version 3 states are discarded.

## Task group 4 — Scripter client seam (RED first)

1. Write `tests/test_scripter_client.py` (RED), with an injected fake transport and no
   ADK import in the test module:
   - the `ScriptClient` protocol is satisfied by a fake that returns a string;
   - `AdkScriptClient` construction does not import ADK and does not touch the network
     (assert `google.adk` is not in `sys.modules` after construction, in a subprocess
     or with a guard, as the Phase 3 and 4 tests do);
   - `SCRIPT_TIMEOUT_SECONDS == 10.0`; a transport timeout maps to `ProviderTimeout`;
   - status mapping: 429 → `RateLimited`; 500, 502, 503 → `VisionUnavailable`; 400,
     401, 403, 404 → `ProviderRejected`; unknown → `VisionUnavailable`;
   - a reply that is not JSON, or JSON without a string `script`, → `InvalidResponse`;
   - the provider message is never copied into the typed error text (sentinel string);
   - the request uses `MODELS["scripter"]` and asks for JSON output matching the
     one-field response schema (assert on what the fake transport receives).
2. Run `scripts/test` → red.
3. Implement `scripter_client.py`:
   - `ScriptClient` protocol: `async def write(dossier, *, repair_note=None) -> str`;
   - `AdkScriptClient`, the only Phase 5 module that imports ADK, built lazily, with
     the import inside the methods or under `TYPE_CHECKING` (as `vision_client.py`
     does);
   - `SCRIPT_TIMEOUT_SECONDS = 10.0`, the one-field response schema, and the JSON
     parse into a string;
   - provider error mapping through `map_provider_error` from `vision_client.py` if
     its signature allows; otherwise factor the shared status table into a helper
     module and cover it with tests (do not duplicate the table).
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** tests never import ADK or open sockets; `AdkScriptClient` is the only
Phase 5 module that imports ADK; the status table is not duplicated.

## Task group 5 — Scripter stage and attempt budget (RED first)

1. Write `tests/test_scripter.py` (RED) with the fake `ScriptClient` and a fake
   `still_current` callable:
   - **Happy path:** one valid paragraph → one call → one `Script`; attempt count 1;
   - **Validation repair:** attempt 1 fails validation (each of the S9 and S10 rules,
     and word counts 59 and 91), attempt 2 passes → `Script`, attempt count 2; the
     repair call carries a `repair_note` that names the rule and the count and does
     **not** contain the failed text;
   - **Validation fails twice:** attempt count exactly 2, then `InvalidResponse`
     raised to the caller;
   - **Provider retry:** timeout, 429, and 5xx on attempt 1 → attempt 2 with no repair
     note, and a valid script → `Script`; the same error on both attempts → the last
     typed error is raised, attempt count exactly 2;
   - **Mixed:** validation failure on attempt 1 and timeout on attempt 2 → the timeout
     is raised; attempt count exactly 2 (the budget is shared, S1);
   - **Fail fast:** `ProviderRejected` → attempt count 1, error re-raised;
   - **Paid-call guard:** `still_current()` returns `False` before attempt 2 → no
     second call; a stale result is not validated;
   - **Backoff:** a plain provider retry waits `RETRY_BACKOFF_SECONDS` (patch
     `asyncio.sleep`); the validation repair waits no backoff (S12);
   - **Logging:** a failure logs the error type, reason code, and attempt number at
     `error`, and never the text (sentinel paragraph in the fake).
2. Run `scripts/test` → red.
3. Implement `scripter.py`:
   - `@log_stage async def generate_script(chat_id, dossier, client, *,
     still_current) -> Script`;
   - the S1 budget of 2 attempts shared by validation and provider retries;
   - the paid-call guard before every attempt (`still_current()`);
   - the S12 backoff on provider retries only;
   - validation failures become `InvalidResponse` with the reason code;
   - `ProviderRejected` is re-raised at once.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** total model attempts per run never exceed 2; no call is made after a
reset; the stage never calls Telegram and never writes to the session.

## Task group 6 — Script step orchestration and delivery (RED first)

1. Write `tests/test_script_step.py` (RED) with the real `SessionStore`, the fake
   `ScriptClient`, and a fake Telegram sender that records the chat id, the photo or
   text, and the order of sends:
   - **Happy path:** portrait sent (Phase 4 path), then one script call, then one
     `send_message` with the exact script text, in that order; `script` stored; phase
     `DELIVERING`; `in_flight=False`; exactly one script call;
   - **Ordering:** the script send happens after the photo send (assert the send log);
   - **No window:** a duplicate text sent after `finish_delivery` but before the script
     completes gets `ONE_AT_A_TIME_TEXT` (the script step is in flight, S3);
   - **Fallback after failure:** validation fails twice → `SCRIPT_FAILED_TEXT`; the
     portrait is not re-sent; `script is None`; phase `DELIVERING`; `in_flight=False`;
   - **Provider failure:** timeout, 429, 5xx → 2 attempts, then the apology; 400, 401,
     403, 404 → 1 attempt, then the apology;
   - **`/restart` mid-script:** the fake client blocks; `/restart` → reset, then
     `OPENING_PROMPT`; release the call → no `send_message`, no state change, `info`
     log; a later photo starts a fresh interview;
   - **`/restart` between commit and send:** the send is skipped if the epoch check
     fails first (step 4a); assert no send after a reset;
   - **Send failure:** `send_message` raises → error logged, one apology attempt, no
     script retry; `script` stays stored (S15 and the failure table);
   - **Duplicate during the script call:** a second text or photo while blocked →
     `ONE_AT_A_TIME_TEXT`; the script client is called exactly once;
   - **After the script step:** text and photo in `DELIVERING` → `ALREADY_DONE_TEXT`;
     no script call;
   - **Cross-chat isolation:** chat A gets its script while chat B is mid-interview;
     each chat's script reaches only that chat; `/restart` in A leaves B's interview
     and answers intact;
   - **Dossier input only (S16):** the script client receives the `Dossier` and no
     photo bytes and no answer text.
2. Run `scripts/test` → red. Record any existing test whose expected behaviour changes
   (for example, a Phase 4 test that asserted that nothing is sent after the portrait).
   Update each one red first and list it in the verifier note.
3. Implement:
   - `generation.py` (or a new `script_step.py` called from it): `run_script_step(
     chat_id, store, dossier, client, send_message) -> None`, following the Flow in
     `requirements.md`; the epoch is captured before `begin_script`;
   - the call happens after `finish_delivery` with no `await` between them (S3);
   - the send uses an injected `MessageSender` protocol, so no test touches Telegram.
     The send is plain text with no `parse_mode` (S13);
   - `@log_stage` on the stage entry; the step logs its own outcome line (as
     `run_generation` does);
   - wire into `routing.py`: `run_generation` calls the script step after the portrait
     is delivered. `handle_restart` is unchanged. The broad `except Exception` logs at
     `error`, never escapes, and abandons the script step with the captured epoch if
     it is still in flight.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** exactly one script call per successful run; the script is sent after
the portrait; every failure returns the chat to `DELIVERING` with the portrait kept;
`/restart` drops a late script; duplicates are nudged, not generated.

## Task group 7 — Logging and safety sweep (RED first)

1. Extend tests (RED) with `caplog`:
   - a full run (photo → interview → dossier → portrait → script) logs chat id, phase
     transitions, attempt numbers, outcomes, durations, and error types;
   - the same run never logs the script text (sentinel paragraph), answers, quirks,
     reason text, prompt text, the dossier, image bytes, tokens, or keys;
   - a failed script step logs the typed error name or reason code at `error` and
     never the provider message;
   - a stale-epoch drop logs at `info` with no text;
   - `@log_stage` records entry, outcome, and duration at the new boundaries.
2. Run `scripts/test` → red.
3. Implement: apply `@log_stage` and the field allow-list on the new boundaries; make
   sure nothing logs `Script`, `Dossier`, `Answer`, or the raw model reply.
4. Run `scripts/test` → green. Run `scripts/hooks`.
5. Source grep over the changed modules: no bare `except`, no `except Exception: pass`,
   no recovered error logged below `error`, no `tempfile`, no `open(` for script data,
   no `write_bytes`, no `parse_mode` on the script send.

**Acceptance:** no forbidden content in any log record at any level; no file I/O for
the script anywhere in the change.

## Task group 8 — Docs, checks, and hygiene

1. Update `README.md`: the script step after the portrait; the plain-text paragraph
   (60 to 90 words); the stored script for Phase 6; the apology `SCRIPT_FAILED_TEXT`
   and its `/restart` pointer; the resting `DELIVERING` phase; `/restart` during the
   script step. Keep setup, run, and dev-script sections accurate. No credentials in
   docs.
2. Run `scripts/test` → clean.
3. Run `scripts/hooks` → clean. Re-run `scripts/test` afterwards and confirm the tree
   is unchanged.
4. Confirm `git status` shows no `.env`, `.venv`, caches, or media. Confirm no new
   dependency (`git diff --stat` on `pyproject.toml` and `uv.lock` is empty).
5. Do **not** edit `SPECS/ROADMAP.md` status; the verifier flips it. Do **not** commit.
6. Report what changed, for the validation checklist.

**Acceptance:** both dev scripts green; docs match the behaviour; hygiene clean;
nothing committed.

## Verification handoff

When this plan is complete, hand off to the verifier with the spec folder path
(`SPECS/2026-11-24-scripter/`). The verifier checks `validation.md`. Live Telegram and
Gemini checks are deferred until real credentials are supplied. Offline validation must
be fully green. The ROADMAP Phase 5 status is updated by the verifier, not by this plan.
