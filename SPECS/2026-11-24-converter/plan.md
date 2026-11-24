# Plan — Converter / Hybrid Portrait (Phase 4)

Red/Green TDD throughout. For each task group: write the failing test(s), run
`scripts/test` to confirm **red**, implement, run `scripts/test` to confirm
**green**. Run `scripts/hooks` at the end of each group that changes code.

Use `scripts/test` and `scripts/hooks` only, never raw `pytest`, `ruff`, or
`mypy`. Invoke the `write-tests` skill before writing any tests.

`scripts/hooks` runs `ruff check --fix` and `ruff format`, so it can rewrite
files. Re-run `scripts/test` after it. Confirm `git status` afterwards.

Branch: `feature/2026-11-24-converter` (already checked out).
Spec folder: `SPECS/2026-11-24-converter/`.
Do **not** write production code outside the task groups. Do **not** commit.
Do **not** start the Scripter (Phase 5) or Narrator (Phase 6).

## Task group 0 — Baseline and spike (no production code)

1. Run `scripts/test` and `scripts/hooks` on the untouched branch. Record the
   test count. Both must exit 0 before any change.
2. Confirm `google-adk` and `python-telegram-bot` are already in `pyproject.toml`
   and `uv.lock`. Do not add a dependency.
3. **ADK image-output spike (read-only, no network).** Inspect the installed
   `google-adk` (`.venv/lib/python3.11/site-packages/google/adk/`) and decide how
   `gemini-3.1-flash-image` returns inline image bytes:
   - (a) through ADK's Gemini model wrapper, with the response modality set to
     image and the image read from the response parts; or
   - (b) through `google.genai` directly, inside `converter_client.py`, with ADK
     not used for this call.
   Record the outcome as a short note at the top of `converter_client.py` when it
   is written, and in the Phase 4 verifier note. If neither route is possible
   without a new dependency, **stop and report**. Do not add a library.
4. Confirm the PTB default: `Application.builder()` uses
   `max_concurrent_updates=1`, so updates are serialised. This is the reason for
   group 6. Record it in the plan notes.

**Acceptance:** baseline green; spike result written down (route (a) or (b));
no dependency change; no production code changed.

## Task group 1 — Contracts, config, and copy (RED first)

1. Write `tests/test_converter_models.py` (RED):
   - `Portrait`: valid PNG bytes accepted and `mime_type == "image/png"`; valid
     JPEG bytes accepted and `mime_type == "image/jpeg"`;
   - empty bytes rejected; bytes over `MAX_PHOTO_BYTES` rejected;
   - bytes that start with neither magic sequence (for example, UTF-8 text such as
     `b"hello"`) rejected;
   - a declared MIME type is not trusted: a JPEG body with a PNG declaration is
     not accepted as PNG (the type comes from the bytes);
   - extra fields rejected.
2. Write `tests/test_messages.py` additions (RED):
   - `GENERATION_FAILED_TEXT` is non-empty, names `/restart`, and contains no
     code, stack-trace, error-code, or provider wording (assert a forbidden-substring
     set, as Phase 3 does);
   - `render_caption("Meerkat")` returns exactly two lines; line 1 contains
     `Meerkat`; line 2 equals the fixed second-line constant;
   - `render_caption` passes the name through `render_subject`: markup, newlines,
     and injected instruction text are removed; length capped at
     `MAX_SUBJECT_CHARS`; an empty or fully unsafe name falls back to the existing
     fallback;
   - the total caption is under 1024 characters for a 60-character name;
   - `OPENING_PROMPT`, `ALREADY_DONE_TEXT`, and `ONE_AT_A_TIME_TEXT` are reused,
     not redefined.
3. Write `tests/test_config.py` additions (RED):
   - `gemini-3.1-flash-image` appears exactly once in `config.py`;
   - `MODELS["converter"] == "gemini-3.1-flash-image"`;
   - existing `bouncer` and `interviewer` entries are unchanged.
4. Run `scripts/test` → red.
5. Implement:
   - `converter_models.py`: `Portrait` (Pydantic v2, `extra="forbid"`, strict),
     with the named constant `MAX_PHOTO_BYTES` imported from `models.py`. Magic
     bytes are checked as named constants (`PNG_MAGIC`, `JPEG_MAGIC`). The MIME
     type is derived, not accepted from input.
   - `messages.py`: add `GENERATION_FAILED_TEXT`, the second caption line
     constant, and `render_caption(animal_name: str) -> str`.
   - `config.py`: add `"converter": _IMAGE` to `MODELS`, with `_IMAGE =
     "gemini-3.1-flash-image"`.
6. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** `Portrait` rejects every malformed payload at construction; the
caption is built only from the sanitised name and fixed copy; the image model ID
appears once.

## Task group 2 — Session phases and run token (RED first)

1. Extend `tests/test_state.py` (RED):
   - `STATE_VERSION == 3`; a stored version `2` is discarded on read and a fresh
     `AWAITING_PHOTO` session starts;
   - `begin_generation` works only from `DOSSIER_READY`; sets
     `phase=GENERATING`, `in_flight=True`, and a non-empty `generation_run`; a
     second `begin_generation` while in flight returns `None`;
   - `begin_generation` from any other phase is refused (`StateError` or `None`,
     as the driver decides, consistently with `begin_turn`);
   - `begin_delivery(chat_id, run)` works only from `GENERATING` with a matching
     token; sets `phase=DELIVERING` and `generation_run=None`; `in_flight` stays
     `True`;
   - `begin_delivery` and `abandon_generation` with a stale or wrong token return
     `False` and change nothing;
   - `finish_delivery` sets `in_flight=False` from `DELIVERING` only;
   - `fail_delivery` sets `phase=DOSSIER_READY` and `in_flight=False` from
     `DELIVERING` only;
   - `abandon_generation(chat_id, run)` sets `phase=DOSSIER_READY`, clears
     `in_flight` and `generation_run`, keeps `dossier` and `answers`;
   - invariants: `generation_run` is set exactly in `GENERATING`; `dossier` is
     set exactly in `DOSSIER_READY`, `GENERATING`, and `DELIVERING`; a violation
     raises `StateError`;
   - `reset` from each of the six phases clears `generation_run`, `dossier`,
     answers, and `in_flight`, and returns to `AWAITING_PHOTO`;
   - two chats keep independent `generation_run` values; a reset in one leaves the
     other's token valid.
2. Run `scripts/test` → red.
3. Implement in `state.py`:
   - `STATE_VERSION = 3`; `SessionState.generation_run: str | None = None`;
   - the new driver methods (`begin_generation`, `begin_delivery`,
     `finish_delivery`, `fail_delivery`, `abandon_generation`);
   - the run token is `uuid4().hex`, generated in `state.py`;
   - update `_check_invariants` for `generation_run` and the widened `dossier`
     rule.
   Stages do not write fields. Update the module docstring to match.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the driver is the only writer; a stale run token cannot change a
newer state; invariants hold after every commit; version 2 states are discarded.

## Task group 3 — Portrait client seam and ADK implementation (RED first)

Depends on the spike in group 0, step 3.

1. Write `tests/test_converter_client.py` (RED), with an injected fake transport
   and no ADK import in the test module:
   - the `PortraitClient` protocol is satisfied by a fake that returns bytes;
   - `AdkPortraitClient` construction does not import ADK and does not touch the
     network (assert `google.adk` is not in `sys.modules` after construction, in a
     subprocess or by a guard, as the Phase 3 test does);
   - the timeout is `REQUEST_TIMEOUT_SECONDS == 60.0`, and a timeout raised by the
     transport maps to `ProviderTimeout`;
   - status mapping through the transport: 429 → `RateLimited`; 500, 502, 503 →
     `VisionUnavailable`; 400, 401, 403, 404 → `ProviderRejected`; unknown → `VisionUnavailable`;
   - the provider message is never copied into the typed error text or logs
     (sentinel string in the fake's error);
   - the request uses `MODELS["converter"]` (assert on the model string the fake
     transport receives) and asks for image output (assert the modality flag the
     transport receives).
2. Write `tests/test_prompts.py` additions (RED): `CONVERTER_INSTRUCTION` exists,
   is non-empty, asks for one hybrid animal portrait, names the recognisable
   features rule (see requirements Open decision 1), and contains no caption
   instructions.
3. Run `scripts/test` → red.
4. Implement:
   - `prompts.py`: `CONVERTER_INSTRUCTION`. Prompt text lives here only.
   - `converter_client.py`:
     - `PortraitClient` protocol: `async def render(photo_bytes, dossier,
       *, repair_note=None) -> bytes`;
     - `AdkPortraitClient` (the only Phase 4 module that imports ADK), built
       lazily, using the route chosen in group 0, step 3. Import ADK inside the
       methods or under `TYPE_CHECKING`, as `vision_client.py` does;
     - `REQUEST_TIMEOUT_SECONDS = 60.0`, `MAX_ATTEMPTS = 2` (the attempt policy
       itself lives in the stage, group 4; this module raises the typed error);
     - provider error mapping, the same shape as `map_provider_error` in
       `vision_client.py`. Reuse that function if its signature allows, and do not
       duplicate the status table. Otherwise factor the shared table into a small
       helper module and cover it with tests.
5. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** tests never import ADK or open sockets; `AdkPortraitClient` is
the only converter module that imports ADK (or none, under route (b), which
must be recorded); timeout and status mapping are explicit.

## Task group 4 — Converter stage (RED first)

1. Write `tests/test_converter.py` (RED) with the fake `PortraitClient`:
   - **Happy path:** one valid PNG → one call → one `Portrait`; attempt count is 1.
   - **Retry policy:** a timeout, a 429, and a 5xx each raise on attempt 1 and
     succeed on attempt 2 → one `Portrait`, attempt count is 2; the same three
     errors on both attempts → the last typed error is raised, attempt count is
     exactly 2;
   - **Fail fast:** `ProviderRejected` raised once → attempt count 1, error
     re-raised;
   - **Payload validation:** a text-only reply (`b"Sorry, I cannot draw that."`),
     empty bytes, bytes with wrong magic, and oversize bytes each → `InvalidResponse`,
     attempt count 1, **no** second call (no repair, per D8);
   - **Backoff:** the retry waits the configured backoff (patch `asyncio.sleep`
     and assert the call);
   - **Logging:** a failure logs the error type and attempt count at `error`, and
     never the bytes or the prompt text (sentinel bytes planted in the fake).
2. Run `scripts/test` → red.
3. Implement `converter.py`:
   - `@log_stage async def generate_portrait(chat_id, photo_bytes, dossier, client)
     -> Portrait`;
   - bounded retry (`MAX_ATTEMPTS = 2`) for `ProviderTimeout`, `RateLimited`,
     and `VisionUnavailable` only, with the small fixed backoff pattern from
     `vision_client.py`;
   - validate the returned bytes into `Portrait`; a validation failure raises
     `InvalidResponse` with no retry;
   - `ProviderRejected` is re-raised at once.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** retries are bounded and only for the three retryable classes; no
repair call is ever made; the stage never calls Telegram and never writes to the
session.

## Task group 5 — Generation orchestration and delivery (RED first)

1. Write `tests/test_generation.py` (RED) with fakes for the `PortraitClient`, the
   `Downloader`, the Telegram photo sender, and the `SessionStore` (real store).
   Cover the required cases:
   - **Happy path:** dossier pending in `DOSSIER_READY` → `begin_generation` →
     download by `photo_file_id` → one generation → `begin_delivery` → one
     `send_photo` with the portrait bytes and the two-line caption (line 1 names
     the animal) → `finish_delivery` → phase `DELIVERING`, `in_flight=False`,
     dossier kept. Exactly one generation call.
   - **Wrong payload during generation:** text-only reply, wrong magic, empty,
     and oversize → no `send_photo`, no second generation call, apology sent
     (`GENERATION_FAILED_TEXT`), phase `DOSSIER_READY`, answers and dossier kept,
     `in_flight=False`, `generation_run=None`.
   - **`/restart` mid-generation:** the fake generation client blocks on an event.
     While blocked, route `/restart` → reset, `OPENING_PROMPT` sent, phase
     `AWAITING_PHOTO`. Release the event → the stale run's result is dropped: no
     `send_photo`, no state change, `info` log. A new photo afterwards starts a
     fresh interview.
   - **`/restart` during the send:** documented limitation (D18). Test that after
     `begin_delivery` the phase is `DELIVERING`; a `/restart` after that resets
     normally; no crash.
   - **Provider error:** timeout, 429, 5xx → exactly 2 attempts, then apology;
     400, 401, 403, 404 → exactly 1 attempt, then apology; answers and dossier
     survive; `GENERATION_FAILED_TEXT` names `/restart`.
   - **Download failure:** the fake `Downloader` raises → apology, phase
     `DOSSIER_READY`, no generation call.
   - **Telegram send failure:** `send_photo` raises → `fail_delivery` → phase
     `DOSSIER_READY`, apology sent, logged at `error`.
   - **Duplicate update:** a second text or photo while the first generation is
     blocked → `ONE_AT_A_TIME_TEXT`; generation client called exactly once.
   - **Routing in `DELIVERING`:** text and photo → `ALREADY_DONE_TEXT`; no
     generation, no state change.
   - **Cross-chat isolation:** chat A generates while chat B is mid-interview;
     each chat receives only its own portrait and caption (assert the chat id on
     each send); `/restart` in A leaves B's run, answers, and outstanding question
     intact.
   - **Dossier text still sent first (D12):** the dossier message precedes the
     portrait send.
   - **Existing behaviour change:** any existing test that expects `/restart`
     during an in-flight call to be nudged is updated here, red first, to the new
     rule (D3). Record each updated test name.
2. Run `scripts/test` → red.
3. Implement `generation.py` (the orchestrator for this phase), with:
   - `async def run_generation(chat_id, store, download, client, send_photo,
     reply) -> None` (or the closest signature that keeps routing thin); each
     step follows the Flow in `requirements.md`;
   - the token check (`begin_delivery`, `abandon_generation`) before any send;
   - the send through an injected `PhotoSender` protocol (`send_photo(chat_id,
     portrait_bytes, caption)`), so no test touches Telegram;
   - the photo bytes are read into memory and dropped after the call; no file is
     opened;
   - `@log_stage` on the entry point;
   - the apology is sent through the same reply channel as the rest of the
     routing layer.
4. Wire it into `routing.py`:
   - after `answer_question` returns `DOSSIER_READY`, send the dossier text, then
     call `run_generation`;
   - `handle_restart` no longer checks `_is_busy` before resetting (D3); it still
     sends `OPENING_PROMPT`;
   - `handle_text` and `handle_photo` keep the busy check and the `DELIVERING`
     `ALREADY_DONE_TEXT` route;
   - keep the broad `except Exception` that logs at `error` and never escapes. In
     `GENERATING`, that handler must call `abandon_generation` with the current
     token, so the chat is never stranded in `GENERATING`.
5. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** exactly one generation per successful run; no `send_photo` for a
stale run; every failure path returns the chat to `DOSSIER_READY` with the dossier
kept; `/restart` works in `GENERATING`; duplicates are nudged, not generated.

## Task group 6 — Concurrent update processing (RED first)

1. Write `tests/test_app.py` additions (RED), without starting the bot:
   - the application builder is configured with concurrent updates enabled (for
     example, `application.update_processor.max_concurrent_updates > 1`), using
     the same `_build_application` factory the app already has, with a dummy
     token and no network. The token must be a shape that PTB accepts without
     contacting the network;
   - the build still does not import ADK (existing test stays green).
2. Run `scripts/test` → red.
3. Implement `app.py`: `.concurrent_updates(True)` (or the explicit integer that
   the tests set) on the builder. Add a one-line comment naming the reason: a
   `/restart` or a duplicate must be processed while a generation is awaiting the
   model.
4. Run `scripts/test` → green. Run `scripts/hooks`.

**Acceptance:** the production application processes updates concurrently; the
test fails on the old builder configuration.

## Task group 7 — Logging and safety sweep (RED first)

1. Extend tests (RED), using `caplog`:
   - a full run (photo → interview → dossier → generation → delivery) logs chat
     id, phase transitions, outcome, durations, and the animal name;
   - the same run never logs answer text, question text, quirk text, reason text,
     image bytes (sentinel bytes in the fake), base64 (assert the base64 of the
     sentinel is absent), prompts, tokens, or keys;
   - a failed run logs the error type at `error` and never the provider message;
   - a stale-token drop logs at `info` with no bytes;
   - `@log_stage` records entry, outcome, and duration at each new boundary.
2. Run `scripts/test` → red.
3. Implement: apply `@log_stage` and the field allow-list on the new boundaries;
   make sure nothing logs `Portrait`, `Dossier`, `Answer`, or the bytes.
4. Run `scripts/test` → green. Run `scripts/hooks`.
5. Source grep over the changed modules: no bare `except`, no `except Exception:
   pass`, no recovered error logged below `error`, no `tempfile`, no `open(` for
   image data, no `write_bytes`.

**Acceptance:** no forbidden content in any log record at any level; no file I/O
for media anywhere in the change.

## Task group 8 — Docs, checks, and hygiene

1. Update `README.md`: the generation step, the `GENERATING` and `DELIVERING`
   phases, the two-line caption, the failure apology with `/restart`,
   `/restart` working during generation, and the resting `DELIVERING` phase (the
   chat waits for `/restart` until Phase 5). Keep setup, run, and dev-script
   sections accurate. No credentials in docs.
2. Run `scripts/test` → clean.
3. Run `scripts/hooks` → clean, and the tree is unchanged afterwards (re-run
   `scripts/test`).
4. Confirm `git status` shows no `.env`, `.venv`, caches, or media. Confirm no
   new dependency in `pyproject.toml` or `uv.lock` (`git diff --stat`).
5. Do **not** edit `SPECS/ROADMAP.md` status; the verifier flips it. Do **not**
   commit.
6. Report what changed for the validation checklist.

**Acceptance:** both dev scripts green; docs reflect the new behaviour; hygiene
clean; nothing committed.

## Verification handoff

When this plan is complete, hand off to the verifier with the spec folder path
(`SPECS/2026-11-24-converter/`). The verifier checks `validation.md`. Live Telegram
and Gemini checks are deferred until real credentials are supplied. Offline
validation must be fully green. ROADMAP Phase 4 status is updated by the verifier,
not by this plan.

## Deviations (recorded by the verifier, 2026-11-24)

The shipped code was checked against this plan. Each deviation is listed with its
reason and its status.

1. **Spike route (b), not (a).** `converter_client.py` calls `google.genai` directly
   (`response_modalities=["IMAGE"]`). The reason is recorded in the module header.
   No dependency was added. `AdkPortraitClient` keeps the spec name, but the
   implementation does not use the ADK. **Accepted: needed to test offline.**
2. **`run_generation` is not decorated with `@log_stage`.** It logs its own
   `stage_start` and `stage_end` (`outcome=ok` or `outcome=error error_type=...`).
   The decorator would record a returned apology as `outcome=ok`. The docstring says
   so. **Accepted.**
3. **Signatures.** `run_generation(chat_id, store, download, portraits, send_photo)`
   returns the apology text, and the router sends it. The plan's `reply` parameter
   is not present. `generate_portrait` takes a keyword-only `still_current` callable,
   which is the paid-call guard. **Accepted; the guard is recorded as a Tech Lead
   decision.**
4. **Delivery tokens.** `finish_delivery` and `fail_delivery` take only `chat_id`, as
   planned. During the send, `run_generation` also compares the chat's reset epoch,
   so a `/restart` during the send cannot move a newer run. This is safety beyond the
   plan. **Accepted.**
5. **Beyond the brief.** The stale-result drop (`SessionStore.epoch`,
   `require_current`, `StaleResult`) also covers the interview and photo stages, so
   those Phase 3 modules changed (`interview.py`, `photo_handler.py`,
   `logging_setup.py`). The ROADMAP Phase 4 note lists this. **Needs the Tech Lead's
   acceptance under D1 to D18.**
6. **Method name in requirements.** `commit_portrait_sent` (requirements, Session
   section) does not exist. The correct names are `begin_delivery` and
   `abandon_generation`. **Corrected in the spec.**
7. **Temp-file bullet in ROADMAP Phase 4** ("Temp file written outside the repo,
   deleted after send") is superseded by D15. The portrait is sent from memory.
   **Recorded in the ROADMAP note.**
8. **Minor edge (non-blocking).** If the dossier text reply itself fails in
   `handle_text`, the exception is logged at `error` and the portrait does not start.
   The chat stays in `DOSSIER_READY` with nothing sent, and the user gets no apology.
   The spec's failure table does not cover this case. **Open for the Tech Lead; it
   does not block the offline checks.**
9. **Minor edge (non-blocking).** `/restart` during a photo's Bouncer check does not
   clear the photo gate's in-flight marker until the check returns. A new photo sent
   in that window gets the one-at-a-time nudge. Nothing is corrupted. **Noted.**
