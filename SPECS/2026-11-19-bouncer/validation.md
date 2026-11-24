# Validation — Bouncer / Vision Routing (Phase 2, Activity 4)

Work top to bottom. Every step is checkable and produces an observable result.
"Merge-ready" means every box is ticked, or the exception is explicitly accepted
by the user. Offline/unit validation must be complete and green regardless of
live-credential deferral.

## A. Reproducible environment & dependency

- [x] On `feature/2026-11-19-bouncer`, `uv sync` completes and `google-adk` is
      installed (no manual `pip install`).
- [x] `uv.lock` is updated with `google-adk` and reflects in `pyproject.toml`.
- [x] Model ID `gemini-3.1-flash-lite` appears once, in the single config module
      (grep for the literal — one hit in code, none scattered).
- [x] `scripts/test` and `scripts/hooks` still exist and run.

## B. Tests are ground truth (Red/Green evidence)

- [x] `scripts/test` exits `0`.
- [x] `scripts/test` visibly runs ruff check, ruff format --check, mypy, pytest.
- [x] `scripts/test` fails when deliberately broken (inject an unused import,
      confirm non-zero exit with `F401`, revert).
- [x] Suite performs **no network I/O** and does not import ADK: unset `.env`,
      confirm tests still pass, and grep tests for `google.adk` → zero hits.
- [x] `scripts/hooks` exits `0` and leaves the tree byte-for-byte unchanged.

## C. Typed contracts at the boundary

- [x] `VisionVerdict` rejects empty fields, wrong types, extra fields, and
      overlength fields (unit-test assertions).
- [x] `PhotoPayload` rejects empty and >10 MiB byte payloads; accepts valid with
      `caption=None`; requires `chat_id`.
- [x] `ChatId` converts a raw `str` and `int` to the same typed key — no
      string-vs-int session mismatch.
- [x] A model response failing `VisionVerdict` validation triggers exactly one
      repair retry, then a typed `InvalidResponse` (no crash, no prose scraping).

## D. Bouncer behaviour (offline, faked vision)

- [x] Human verdict (`contains_human=True`) → confirmation is `hey mate!` and
      `note_photo` records the `file_id`; no rejection sent.
- [x] Non-human verdicts — cat, pizza, car, coffee mug, landscape — each produce
      the cheeky rejection template naming `detected_subject`, call `reset`, and
      leave the chat awaiting-photo.
- [x] Rejection copy contains no HTTP/code wording, no stack trace, no prompt
      text, and no model payload; `reason` prose is not shown to the user.
- [x] `VisionUnavailable` / `ProviderTimeout` / `RateLimited` after bounded retry
      → `RESEND_TEXT`, no state mutation, no exception.
- [x] `InvalidResponse` after one repair retry → resend nudge, logged at error.
- [x] Retry is bounded (max 2 attempts) with backoff; a non-retryable error fails
      fast (assert attempt count in a fake).
- [x] Empty/oversized/malformed photo → nudge, and the vision client is never
      called (assert call count zero).

## E. State, routing & isolation

- [x] Fresh chat starts `AWAITING_PHOTO`; `reset` clears `last_file_id` and
      returns to `AWAITING_PHOTO`.
- [x] Two concurrent chats: one passes and one rejects; each state is independent
      with no bleed, and a reset in one leaves the other intact.
- [x] Photo update routed through the Bouncer; text update still returns
      `hey mate!` (constant imported, not duplicated).
- [x] Non-text/non-photo, edited messages, and malformed updates → no exception.
- [x] A handler exception is caught, logged at error level, and the next update
      is still served.
- [x] A second photo arriving while one is in flight does not double-classify or
      corrupt that chat's state.

## F. Logging & safety

- [x] With `caplog`, a full pass and a full rejection log `chat_id`, verdict bool,
      and `detected_subject`.
- [x] Those log records never contain image bytes, base64, tokens, API keys, or
      the model's free-form `reason` prose.
- [x] `@log_stage` records entry, outcome, and duration at the Bouncer boundary.
- [x] Grep source: no bare `except`, no `except Exception: pass`, no recovered
      error logged below `error`.

## G. Live Telegram verification (requires real credentials)

- [ ] DEFERRED: **Negative test:** send a coffee mug / landscape photo; confirm a humorous
      in-character rejection naming the object and no interview/next step.
- [ ] DEFERRED: **Positive test:** send a clear photo of a person; confirm approval and the
      `hey mate!` confirmation step.
- [ ] DEFERRED: Bot starts and logs "polling started"; Ctrl-C stops it cleanly.
- [ ] DEFERRED: Logs show no traceback, tokens, keys, image bytes, or full `reason` prose.

> **Deferred on this box:** `.env` holds placeholder-only credentials (verified
> without printing values). Steps in section G cannot pass until real
> `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` are supplied. Per the user's confirmed
> decision, mark them deferred in the spec and ROADMAP — sections A–F must all
> pass regardless. Do **not** fabricate a live pass.

## H. Scope discipline

- [x] No Interviewer, dossier, suggested animal, or interview question.
- [x] No Converter, image generation, Scripter, Scripter text, or TTS.
- [x] No phase transitions beyond `IDLE` / `AWAITING_PHOTO` driven in code.
- [x] No webhook code, inbound HTTP server, database, or persistence.
- [x] `.env` gitignored and absent from `git status`; no media/caches/test
      artifacts staged (`leak-test.txt`, `.env.verify_backup`, etc.).
- [x] Only `vision_client.py` imports ADK; no other module imports provider SDKs.

## I. Documentation & constitution sync

- [x] `README.md` documents the photo gate and both branches of Bouncer behaviour.
- [x] `SPECS/ROADMAP.md` Phase 2 status reflects the outcome, with the live-test
      deferral and the approved "reply `hey mate!` instead of INTERVIEWING"
      deviation noted (do not claim a live pass).
- [x] Any deviation between spec and implementation is recorded in
      `requirements.md` under "Open decisions / assumptions" with user approval
      for anything impactful.
- [x] `MISSION.md` / `TECH.md` touched only if genuinely inaccurate.

## Definition of merge-ready

1. Sections A–F, H, I pass.
2. Section G passes **or** is explicitly deferred by the user (placeholder
   credentials), recorded in ROADMAP.md Phase 2.
3. `scripts/test` and `scripts/hooks` are clean.
4. No secrets, raw image bytes, or user answers in logs, repo, or fixtures.
5. Diff reviewed against `main` with no blocking findings.

## Verifier results (2026-11-24)

Result key: PASS = checked against code and/or the offline suite; DEFERRED =
needs real credentials (not run, not claimed).

- **A (environment):** PASS. `uv sync --locked` exits 0; `google-adk` 2.14.1 is
  in `pyproject.toml` and `uv.lock`. `gemini-3.1-flash-lite` has one code hit
  (`config.py`). `tests/test_models.py` also asserts that literal, a deliberate
  pin rather than a scattered use. `scripts/test` and `scripts/hooks` exist and run.
- **B (tests):** PASS. `scripts/test` exits 0 (163 tests) and runs ruff check,
  ruff format --check, mypy, and pytest. An injected unused import fails it with
  `F401` (run in a scratch copy, exit 1). With no `.env` and no credentials in the
  environment, the full suite still passes (163). No test imports `google.adk`.
  `scripts/hooks` exits 0 and leaves the tree unchanged (checksummed before and after).
- **C (contracts):** PASS. Covered by `test_models.py`: empty, wrong-type, extra,
  overlength fields; PhotoPayload empty, oversize, valid, `caption=None`, required
  `chat_id`; raw `str`/`int` chat ids map to one key. One repair retry then
  `InvalidResponse` is covered in `test_bouncer.py`.
- **D (Bouncer):** PASS. Human → `hey mate!` and `note_photo`. Cat, pizza, car,
  coffee mug, and landscape each get the rejection naming the subject, a reset,
  and awaiting-photo. Provider errors after retry → resend nudge with no state
  change. Retry is capped at 2 attempts (asserted in a fake). `InvalidResponse`
  is logged at `error` in `vision_client.py`. Empty, oversize, and malformed
  photos never call the client.
  - *Note (non-blocking):* no test asserts the `error` level for the
    invalid-response log. The code emits it; a test would lock it in.
- **E (state, routing, isolation):** PASS. Fresh chat → `AWAITING_PHOTO`. Reset
  clears the reference. Two chats are independent. Text still gets `hey mate!`.
  Non-photo and malformed updates are ignored. A handler exception is logged and
  the next update is served. A second photo in flight gets the nudge.
- **F (logging & safety):** PASS. Pass and reject paths log `chat_id`, verdict,
  and `detected_subject`. Logs carry no bytes, base64, tokens, keys, or `reason`
  prose. `@log_stage` logs entry, outcome, and duration. Source grep: no bare
  `except`, no `except Exception: pass`; every `except Exception` logs at error or
  re-raises. The malformed and oversize nudges are logged at `info` as user-input
  outcomes, not recovered internal errors.
- **G (live Telegram/Gemini):** DEFERRED. `.env` holds placeholder credentials.
  The negative, positive, polling-started, Ctrl-C, and log checks were not run.
  No live pass is claimed.
- **H (scope):** PASS. No Interviewer, dossier, Converter, Scripter, or TTS code.
  Only `IDLE`/`AWAITING_PHOTO` are driven; the other phases are declared only. No
  webhook, HTTP server, database, or persistence. `.env` is gitignored and absent
  from `git status`. Only `vision_client.py` imports ADK or `google.genai`.
- **I (docs):** PASS after fixes. README was stale (it said Phase 1 with photos
  ignored); it now documents the photo gate and both Bouncer branches, plus known
  limitations. ROADMAP Phase 2 is `[~]` with the live deferral and approved
  deviations noted. Deviations are recorded in `requirements.md` Open decisions
  9-12 (plus 1, 7, 8). TECH.md and MISSION.md were checked and are accurate.

Blocking findings: none. Deferred: section G only.

