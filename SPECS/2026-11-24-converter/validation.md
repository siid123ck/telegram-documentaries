# Validation — Converter / Hybrid Portrait (Phase 4)

Work top to bottom. Every step is checkable and produces an observable result.
"Mergeable" means every box is ticked, or the exception is explicitly accepted by
the Tech Lead. Offline validation must be complete and green regardless of the
live-credential deferral. Do not claim a live pass that was not run.

**Verification status: run 2026-11-24 by the verifier (branch
`feature/2026-11-24-converter`, uncommitted).** Marks: `[x]` pass · `[ ]` open
(not passed; the reason is given) · `DEFERRED` live check, not run.

**Run results**

| Command | Exit code | Result |
|---|---|---|
| `scripts/test` | 0 | ruff check, ruff format --check, mypy (46 files), pytest: 589 passed |
| `scripts/hooks` | 0 | ruff check --fix, ruff format, mypy, pytest: 589 passed |
| `scripts/test` in a scratch copy with no `.env` and no token or key in the environment | 0 | 589 passed |
| `scripts/test` in a scratch copy with an injected unused import | 1 | `F401` on `converter.py` (the real tree was not changed) |
| `uv sync --locked` | 0 | Resolved 72 packages; nothing changed |

Offline result: **all checks pass except the open items marked `[ ]` below**
(B3 and F5 red-first evidence, and N3 Tech Lead confirmation of D1 to D18). The
roadmap status therefore stays `[~]`. See the verifier note in `SPECS/ROADMAP.md`.

## A. Environment & dependencies

- [x] On `feature/2026-11-24-converter`, `uv sync --locked` completes. *(exit 0)*
- [x] No new dependency: `git diff main -- pyproject.toml uv.lock` is empty.
- [x] `gemini-3.1-flash-image` appears exactly once in `src/` (`config.py:18`). The
      only other matches are binary `.pyc` build caches.
- [x] `scripts/test` and `scripts/hooks` exist and run.
- [x] The ADK image-output spike is recorded: route (b), `google.genai` called
      directly from `converter_client.py`, with the reason, in the module header and
      in the ROADMAP Phase 4 note. Route (a) was assessed and not exercised.

## B. Tests are ground truth (Red/Green evidence)

- [x] `scripts/test` exits `0` and visibly runs ruff check, ruff format --check,
      mypy, and pytest. *(output: "All checks passed!", "74 files already
      formatted", "Success: no issues found in 46 source files", 589 passed)*
- [x] `scripts/test` fails when deliberately broken. An unused `import os` was
      injected into a scratch copy. It failed with `F401` and exit 1. The real tree
      was not touched.
- [ ] **OPEN.** Each new test file was seen red before its implementation. No red
      run log was kept, and the work is uncommitted, so the red sequence cannot be
      checked from history. **Needs a Tech Lead exception, as in Phase 3, or a
      re-run of the red steps.**
- [x] The suite performs no network I/O. A scratch copy with no `.env` and no
      `TELEGRAM_BOT_TOKEN` or `GEMINI_API_KEY` in the environment passed (589, exit
      0). All model and Telegram calls go through fakes. No socket-level guard test
      exists; the check is by inspection plus the env-less run.
- [x] `grep -r "google.adk\|google.genai" tests/` returns zero hits.
- [x] `scripts/hooks` exits `0`. `scripts/test` is re-run after it, with no
      unexpected file changes. *(see the final run note at the end of this file)*

## C. Typed contracts at the boundary

- [x] `Portrait` rejects empty bytes, bytes over `MAX_PHOTO_BYTES`, and bytes that
      are neither PNG nor JPEG. The exact-ceiling case is accepted.
      *(`test_converter_models.py`)*
- [x] `Portrait.mime_type` is derived from the bytes (a property, not an input).
      A declared PNG type on JPEG bytes is not trusted. *(two tests)*
- [x] `Portrait` rejects extra fields. *(`test_extra_fields_are_rejected`)*
- [x] A text-only or non-image provider reply becomes `InvalidResponse`, and
      `photos.sent == []`. *(`test_converter.py`; `test_generation.py` wrong-payload
      cases)*
- [x] No repair call is made on an invalid image payload (D8). The attempt count is 1
      and no `repair_note` is sent. *(`test_no_repair_note_is_ever_sent_for_a_portrait`)*

## D. Happy path

- [x] A completed interview sends the dossier text, then exactly one generation
      call, then exactly one `send_photo`, in that order. *(`test_portrait_routing.py`:
      `test_the_dossier_text_is_sent_before_the_portrait`,
      `test_a_completed_interview_delivers_one_portrait_to_its_chat`)*
- [x] The portrait is sent to the correct chat (chat id asserted on the send).
      *(`test_generation.py`: `test_two_chats_each_get_only_their_own_portrait_and_caption`)*
- [x] The caption has exactly two lines. Line 1 names the sanitised animal. Line 2
      is the fixed copy. *(`test_the_caption_has_two_lines_and_names_the_animal`)*
- [x] The phase ends at `DELIVERING`; `in_flight` is `False`; the dossier is kept.
      *(`test_the_chat_rests_in_delivering_with_the_dossier_kept`)*
- [x] The chat rests in `DELIVERING`. Text and photos there get `ALREADY_DONE_TEXT`
      (D1). *(`test_portrait_routing.py`: text and photo cases)*
- [x] The bytes passed to `send_photo` are the generated bytes, from memory. No file
      is opened. *(`test_the_photo_bytes_are_handed_over_in_memory_without_a_file`;
      grep of `src/` finds no `open(`, `tempfile`, or `write_bytes`)*

## E. Wrong payload during generation

- [x] Text-only reply, wrong magic bytes, empty bytes, and oversize bytes each → no
      send, no second generation call, `GENERATION_FAILED_TEXT`, phase
      `DOSSIER_READY`, answers and dossier kept. *(parametrised
      `test_a_wrong_payload_sends_the_apology_and_no_portrait`, 4 cases)*
- [x] `GENERATION_FAILED_TEXT` names `/restart` and contains no code, error type,
      provider wording, or stack trace. *(`test_messages.py`, forbidden-substring set)*

## F. `/restart` mid-generation

- [x] `/restart` while generation is blocked → reset, `OPENING_PROMPT` sent, phase
      `AWAITING_PHOTO`. Not nudged with `ONE_AT_A_TIME_TEXT`. *(`test_portrait_routing.py`:
      `test_restart_mid_generation_resets_at_once_and_drops_the_portrait`;
      `test_interview_routing.py`: `test_restart_during_a_model_call_resets_at_once...`)*
- [x] When the blocked call returns, nothing is sent and the state does not change
      (stale token). An `info` line records the drop. *(`test_generation.py`:
      `test_a_reset_during_generation_drops_the_late_portrait`;
      `test_a_dropped_stale_result_is_logged_at_info_with_no_bytes`)*
- [x] A new photo after the restart starts a fresh interview with an empty history.
      *(`test_a_photo_after_a_mid_generation_restart_starts_a_fresh_interview`)*
- [x] `/restart` during the send is accepted and documented as the one race (D18).
      The phase after the send is consistent: no crash, no stuck `GENERATING`.
      *(`test_a_reset_during_the_send_is_accepted_without_a_crash`;
      `test_a_finish_from_a_reset_run_keeps_a_newer_runs_in_flight_flag`)*
- [ ] **OPEN.** Existing tests that expected the old `/restart` nudge were updated.
      The list is now recorded here (the red-first order is not evidenced; see B3):
      `tests/test_interview_routing.py`:
      1. `test_a_photo_in_dossier_ready_gets_the_already_done_nudge` renamed to
         `test_a_photo_after_delivery_gets_the_already_done_nudge`. It now asserts
         `DELIVERING` (auto-generation).
      2. `test_text_in_dossier_ready_gets_the_already_done_nudge` renamed to
         `test_text_after_the_portrait_is_delivered_gets_the_already_done_nudge`. It
         now asserts `DELIVERING`.
      3. `test_restart_during_a_model_call_is_nudged_and_changes_nothing` replaced
         by `test_restart_during_a_model_call_resets_at_once_and_sends_the_opening`
         (D3).
      `tests/test_state.py`: the schema-version-2 assertion is now 3, and the
      dossier invariant is widened to `DOSSIER_READY`, `GENERATING`, `DELIVERING`.
      **Open until the Tech Lead accepts the red-first exception (B3).**

## G. Provider errors & degradation

- [x] Timeout, 429, and 5xx are attempted exactly 2 times, then
      `GENERATION_FAILED_TEXT`. *(parametrised `test_a_retryable_provider_error_is_attempted_twice_then_apologised`)*
- [x] 400, 401, 403, and 404 are attempted exactly once, then
      `GENERATION_FAILED_TEXT`. *(`test_a_rejected_request_is_attempted_once_then_apologised`;
      status mapping for each code in `test_converter_client.py`)*
- [x] Backoff between the two attempts is the fixed short backoff (a patched `sleep`
      is asserted). *(`test_the_retry_waits_the_configured_backoff_before_the_second_attempt`)*
- [x] `AdkPortraitClient` maps the status table to typed errors through an injected
      transport (no network). *(`test_a_provider_status_maps_to_its_typed_error`)*
- [x] `REQUEST_TIMEOUT_SECONDS == 60.0`. A transport timeout maps to
      `ProviderTimeout`. *(`test_the_generation_timeout_is_sixty_seconds`;
      `test_a_timeout_raised_by_the_transport_maps_to_provider_timeout`)*
- [x] A download failure sends the apology, stays `DOSSIER_READY`, and makes no
      generation call. *(`test_a_download_failure_apologises_and_makes_no_generation_call`)*
- [x] A `send_photo` failure rolls back to `DOSSIER_READY` (`fail_delivery`), sends
      the apology, and logs at `error`. *(`test_a_telegram_send_failure_rolls_back_and_apologises`;
      `test_a_telegram_send_failure_is_logged_at_error_without_its_message`)*
- [x] No exception escapes any handler. The chat is not left in `GENERATING`. The
      `finally` in `_run_once` calls `abandon_generation` with the current token.
      *(`test_an_unexpected_error_is_not_swallowed_and_the_chat_is_not_stranded`;
      `test_an_unexpected_error_is_logged_and_never_escapes_the_handler`)*

## H. Duplicate updates

- [x] A text or photo arriving while generation is in flight → `ONE_AT_A_TIME_TEXT`;
      the generation client is called exactly once; no state change.
      *(`test_a_duplicate_text_during_generation_is_nudged_and_not_regenerated`;
      `test_a_duplicate_photo_during_generation_is_nudged_without_a_download`)*
- [x] A text or photo arriving during `DELIVERING` (no run in flight) →
      `ALREADY_DONE_TEXT`; no generation; no state change.
- [x] Edited messages, stickers, and malformed updates → no reply, no exception, no
      state change (D17). The `MessageHandler` filters pass only text and photo
      messages, and the router returns early when there is no text. The explicit
      test runs at `AWAITING_PHOTO` (`test_noise_and_malformed_updates_are_ignored...`).
      This is independent of phase by construction. There is no separate noise test
      during `GENERATING`/`DELIVERING`.

## I. State & isolation

- [x] `STATE_VERSION == 3`; a stored version `2` is discarded on read.
      *(`test_the_schema_version_is_three`;
      `test_a_stored_schema_version_two_is_discarded_on_read`)*
- [x] `generation_run` is set exactly in `GENERATING` (invariant tested).
      *(`test_an_inconsistent_state_is_refused_at_the_next_commit`;
      `test_a_new_session_has_no_generation_run`)*
- [x] `dossier` is set exactly in `DOSSIER_READY`, `GENERATING`, and `DELIVERING`
      (invariant tested). *(`test_state.py`)*
- [x] `begin_generation` refuses a second run; `begin_delivery`, `abandon_generation`,
      and the other driver methods refuse a stale or wrong token and change nothing.
      *(`test_begin_generation_during_an_in_flight_delivery_returns_none`;
      `test_begin_delivery_with_a_wrong_token_changes_nothing`;
      `test_abandon_generation_with_a_stale_token_changes_nothing`)*
- [x] Cross-chat isolation: chat A generates while chat B is mid-interview; each
      chat's portrait and caption go only to that chat; `/restart` in A leaves B's
      run, answers, and outstanding question intact. *(`test_portrait_routing.py`:
      `test_a_portrait_completes_for_one_chat_while_another_is_mid_interview`,
      `test_a_restart_in_one_chat_leaves_the_other_chats_interview_intact`)*
- [x] Stages do not write session fields directly. Grep: no `_replace(` or `_states`
      outside `state.py`, and no assignment to session fields.

## J. Caption & copy

- [x] `render_caption` returns exactly two lines. *(`test_the_caption_has_exactly_two_lines`)*
- [x] Markup, newlines, and injected instruction text in the animal name are removed
      (through `render_subject`); the name is capped. *(`test_the_caption_passes_the_animal_name_through_the_subject_sanitiser`;
      `test_the_caption_caps_a_long_animal_name`)*
- [x] The caption is under 1024 characters for a 60-character name.
      *(`test_the_caption_stays_well_under_the_telegram_limit`)*
- [x] `GENERATION_FAILED_TEXT` is non-empty, names `/restart`, and has no code or
      provider wording. *(`test_messages.py`)*
- [x] `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused, not
      redefined. *(`test_the_reused_copy_literal_is_written_once_in_the_package`)*
- [x] The caption claims nothing beyond the animal name. It is built from
      `render_subject` and the fixed line 2 only. No model `reason` or prose appears
      in it (D11). *(`test_generation.py`: caption test)*

## K. Logging & safety

- [x] With `caplog`, a full run logs `chat_id`, phase transitions, outcome, duration,
      and the animal's name. *(`test_portrait_logging.py`:
      `test_a_full_run_logs_the_chat_phases_outcome_and_animal_name`)*
- [x] The same run never logs image bytes, base64, answer text, question text, quirk
      text, reason text, the prompt, tokens, or keys. *(`test_a_full_run_never_logs_text_bytes_or_their_base64`;
      sentinel strings and bytes in the fakes)*
- [x] A failed run logs the error type at `error` and never the provider message.
      *(`test_a_failed_run_logs_the_error_type_and_never_the_provider_message`)*
- [x] `@log_stage` records entry, outcome, and duration on the new boundaries
      (`generate_portrait`, `AdkPortraitClient.render`, `_send_portrait`).
      `run_generation` does its own entry, outcome, and duration logging, because
      `@log_stage` would record a returned apology as `outcome=ok`. This is a recorded
      deviation (see `plan.md`, "Deviations"). *(`test_each_new_boundary_records_entry_outcome_and_duration`)*
- [x] Source grep over the changed modules: no bare `except`, no `except Exception:
      pass`, no recovered error logged below `error`. The static guard test is
      `test_the_phase_4_source_has_no_temp_files_or_bare_excepts`.
- [x] No `tempfile`, no `write_bytes`, and no `open(` for image data anywhere in the
      change (grep of `src/` returns zero hits).
- [x] No `Portrait`, `Dossier`, `Answer`, or bytes object is passed to a logger
      directly. Logged fields are IDs, phase names, counts, error type names, and the
      animal name.

## L. Concurrency wiring

- [x] The application builder enables concurrent update processing (D5). Asserted
      without starting the bot. *(`test_app.py`; `test_main.py::test_the_application_processes_updates_concurrently`)*
- [x] `_build_application` still does not import ADK or contact the network.
      *(`test_main.py::test_building_the_application_does_not_import_the_adk_or_send_anything`)*
- DEFERRED (live): a `/restart` sent during a real generation is answered promptly
  rather than after the generation ends.

## M. Scope discipline

- [x] No Scripter, Narrator, TTS, or voice code. No `sendVoice`. *(grep of `src/`)*
- [x] `GENERATING` and `DELIVERING` are driven as specified. No other new phase. The
      `Phase` enum is unchanged.
- [x] No portrait or photo bytes stored in `SessionState` (D6, D15). *(the model has
      no bytes field)*
- [x] No portrait edit, regenerate, or second-image path. *(no such handler or method)*
- [x] No persistence, database, webhook, or inbound HTTP server. *(grep of `src/`)*
- [x] `/start` behaviour is unchanged (Phase 7). The only command handler is
      `/restart`; `/start` still takes the Phase 2/3 text path.

## N. Documentation

- [x] `README.md` describes the generation step, the `GENERATING` and `DELIVERING`
      phases, the two-line caption, the apology with `/restart`, `/restart` during
      generation, and the resting `DELIVERING` phase. *(verifier re-checked the
      README against the code, and updated the likeness and TECH sections; see
      "Verifier changes" below)*
- [x] `SPECS/ROADMAP.md` status is not flipped by the build. The build agent did add
      implementation notes to Phase 4 (status still `[ ]`). The verifier replaced the
      status with `[~]` and added its own note.
- [ ] **OPEN.** Every decision in `requirements.md` (D1 to D18) is confirmed or
      revised by the Tech Lead before merge. Confirmed in this round: D2, D5, D9, D10,
      D11, D16, and the paid-call guard (see `requirements.md`, "Tech Lead decisions").
      **Not confirmed: D1, D3, D4, D6, D7, D8, D12, D13, D14, D15, D17, D18.**
- [x] The likeness wording ("recognisable features kept, stylisation allowed") is
      confirmed by the Tech Lead (requirements Open decision 1, recorded 2026-11-24).
      The visual check of a real portrait is DEFERRED (O).
- [x] The generation timeout (60 s, D9) is confirmed by the Tech Lead. The retry
      bound (max 2 attempts, D10) matches the TECH.md policy and is confirmed with the
      paid-call guard.
- [x] The concurrent-update change (D5) is confirmed by the Tech Lead. Its effect on
      the Phase 2 and Phase 3 handlers is noted in README "Known limitations" and in
      ROADMAP Phase 4.

## O. Live verification (requires real credentials; DEFERRED)

- DEFERRED: a completed interview produces one portrait and one caption naming the
  animal, in the same chat.
- DEFERRED: the portrait keeps the user's recognisable features (visual check by the
  Tech Lead).
- DEFERRED: a `/restart` during a real generation resets the chat promptly and the
  late portrait is not delivered.
- DEFERRED: a duplicate message during a real generation gets the one-at-a-time nudge
  and does not start a second generation (check the provider usage count).
- DEFERRED: a forced provider failure (for example, an invalid key in a scratch run)
  shows the apology only, with no payload or stack trace.
- DEFERRED: logs for a full run show no image bytes, tokens, keys, or answer text.

> Deferred until real `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` values are supplied.
> Do not fabricate a live pass. No live bot run and no model call were made by the
> verifier. The offline checks above do not claim a live pass.

## Secrets and privacy scan (verifier, 2026-11-24)

- The configured secret values from the local `.env` were used only as search
  patterns. No tracked or untracked (non-ignored) file contains one. Nothing was
  printed.
- No secret-shaped string (a Gemini `AIza…` key or a Telegram `id:token`) appears in
  the change.
- `.env` and `.env.save` are gitignored. No `.env`, media, or temp file is in the
  tree.

## Definition of mergeable

1. **Not yet met.** Sections A to N pass except B3 and F5 (red-first evidence), and
   N3 (D1 to D18 confirmation). Both need the Tech Lead.
2. Section O is deferred (live). It must be recorded in the ROADMAP Phase 4 verifier
   note, which is done.
3. **Met.** `scripts/test` exit 0 and `scripts/hooks` exit 0.
4. **Met.** No secrets, raw image bytes, base64, or user answers in logs, the repo, or
   fixtures. No temp files.
5. **Met.** Exactly one portrait generation per successful run. The happy-path test
   asserts one `render` call with no retry (`test_one_valid_png_becomes_one_portrait_after_one_attempt`).
6. **Partly met.** No blocking code findings. The changes beyond the brief (D1 to D18)
   are recorded. Acceptance of D1, D3, D4, D6, D7, D8, D12, D13, D14, D15, D17, D18 is
   still open.

## Verifier changes (2026-11-24)

- Updated this file with the results above.
- Corrected `requirements.md` (the non-existent `commit_portrait_sent` name) and
  recorded the Tech Lead decisions and the paid-call guard.
- Recorded the deviations in `plan.md`.
- Updated `SPECS/TECH.md` to the shipped behaviour.
- Updated `README.md` (likeness wording is confirmed; the live checks stay deferred).
- Set `SPECS/ROADMAP.md` Phase 4 to `[~]` with a verifier note.
- No code was changed. No commit or push was made.

## Final run note (verifier, 2026-11-24, after the documentation edits)

- `scripts/test`: exit 0, 589 passed.
- `scripts/hooks` (run before the documentation edits): exit 0, 589 passed. It changed
  no files; the tree was the same before and after.
- The only tree changes after the verifier started are the documentation files:
  `SPECS/TECH.md`, `SPECS/ROADMAP.md`, `README.md`, and the three files in
  `SPECS/2026-11-24-converter/`. No code was changed. Nothing was committed or pushed.
