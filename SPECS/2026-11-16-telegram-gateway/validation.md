# Validation — Telegram Gateway (Phase 1)

Work top to bottom. Every step is checkable and produces an observable result.
"Merge-ready" means every box is ticked, or the exception is explicitly
accepted by the user.

## A. Reproducible environment

- [x] `uv --version` succeeds (uv `0.13.8` on this box; `~/.local/bin` on PATH).
      Verified: `uv 0.13.8` at `~/.local/bin/uv`.
- [x] `uv sync` completes from a clean checkout with no manual `pip install`.
      Verified: `uv sync` exited 0 (38 packages resolved).
- [x] `uv.lock` exists and is committed.
      Verified present in the working tree (156 KB); the actual commit is the
      orchestrator's step.
- [x] `pyproject.toml` declares Python `>=3.11` and pins `python-telegram-bot>=21`.
      Verified by inspection.

## B. Tests are ground truth

- [x] `scripts/test` exits `0`. Re-verified: exit 0, 33 tests passed.
- [x] `scripts/test` includes, and visibly runs: ruff check, ruff format --check,
      mypy, pytest. Verified in output: "All checks passed!", "30 files already
      formatted", "Success: no issues found in 11 source files", "33 passed".
- [x] `scripts/test` fails when deliberately broken: inject a lint error (e.g.
      unused import), confirm non-zero exit, revert. Verified by temporarily
      adding `import os` to a probe module: exit 1 with `F401 ... imported but
      unused`; probe removed afterwards.
- [x] Test suite performs **no network I/O**. Verified: tests `monkeypatch.chdir`
      to `tmp_path`, clear the credential env vars and construct settings
      explicitly; no network calls exist in the current source modules.

## C. Configuration fails fast and safely

> **Result: PASS (re-verified 2026-11-19).** `__main__.py` and `app.py` now wire
> `main()` to `load_settings()` and the polling seam. With the placeholder `.env`
> the command exits `2`, prints exactly one non-empty line, has no traceback and
> prints no secret value; output inspected programmatically:
> `TELEGRAM_BOT_TOKEN (placeholder), GEMINI_API_KEY (placeholder). Set real values in .env and see .env.example.`
> Renaming `.env` away produces the same single-line shape with `(missing)`
> instead, exit `2`. `.env` was restored byte-for-byte (SHA-256 matched) and the
> temporary backup removed.

- [x] With `.env` values set to the placeholders
      (`your_telegram_bot_token_here`, `your_gemini_api_key_here`), running
      `uv run python -m telegram_documentaries` prints **one** clear line naming
      the placeholder variable(s) and exits non-zero. Verified: 1 line, exit 2.
- [x] The same run prints **no** stack trace. Verified: no "Traceback" in output.
- [x] The same run prints **no** secret value (it is a placeholder, but the code
      path must redact regardless — inspect the code for `SecretStr` usage).
      Verified: no `.env` value appears in stdout/stderr; fields are `SecretStr`.
- [x] Rename `.env` away → one clear line naming unset variables, non-zero exit.
      Verified: `... (missing), ... (missing)` line, exit 2.
- [x] Restore `.env` afterwards. Verified: SHA-256 unchanged, backup removed.

## D. Pre-commit hooks actually fire

> **Result: PASS (re-verified 2026-11-19).** `.pre-commit-config.yaml` exists with
> all four local hooks plus the pinned gitleaks repo hook (`v8.30.1`).
> `pre-commit install` exited 0 and `pre-commit run --all-files` exited 0 showing
> all five hooks (ruff check, ruff format, mypy, pytest, gitleaks) Passed, with no
> source changes. Both self-tests blocked a bad commit: a staged AWS-style key
> made gitleaks fail (exit 1) and a deliberately failing assertion made pytest
> fail (exit 1, "1 failed, 32 passed"). The installed git hook is the standard
> `pre-commit hook-impl`, so self-tests were driven by `pre-commit run` on the
> staged state — the identical gate — avoiding creation of any real commit.
> All probe files were removed and the index left empty (SHA-256 of the modified
> test file matched the original after restore).
>
> **Fixture note:** the spec's suggested `AKIAIOSFODNN7EXAMPLE` does *not* fire
> gitleaks — its rule allowlists `.+EXAMPLE$` and only accepts `[A-Z2-7]{16}`
> with entropy >= 3. A compliant high-entropy fake key does fire. Future
> self-tests should use such a key rather than the spec's example string.

- [x] `.pre-commit-config.yaml` lists ruff, ruff-format, mypy, pytest, and the
      gitleaks repo hook. Verified: four local hooks + gitleaks `v8.30.1`.
- [x] `pre-commit install` succeeds. Verified: exit 0.
- [x] `pre-commit run --all-files` runs and the hook list is visible. Verified:
      all five hooks listed and Passed, exit 0.
- [x] **gitleaks self-test:** create `leak-test.txt` containing a fake AWS key
      pattern, `git add` it, attempt `git commit`, and
      confirm the commit is **blocked** by the gitleaks hook. Then unstage and
      delete the file. Verified with a compliant high-entropy key: hook Failed,
      exit 1; `leak-test.txt` unstaged and deleted (see fixture note above).
- [x] **pytest self-test:** temporarily break one test, attempt a commit, confirm
      it is blocked. Revert. Verified: pytest Failed, exit 1; test restored and
      unstaged, SHA-256 matched.
- [x] `scripts/hooks` run manually leaves the tree unchanged (no diff after).
      Verified by before/after checksum.

## E. Gateway behaves in isolation (offline)

> **Result: PASS (re-verified 2026-11-19).** `gateway.py` and
> `tests/test_gateway.py` exist. A manual offline call to the pure `build_reply`
> confirmed `REPLY_TEXT == 'hey mate!'` for text, and `None` for empty text,
> photo, voice, sticker, edited_message, channel_post and no-message updates.
> The async handler sent the reply once via an injected sender, sent nothing for
> non-text updates, logged `stage_error`/`handler_error` at error level when the
> sender raised, and still served the next update.

- [x] A text message produces exactly `hey mate!` (unit test assertion, and a
      manual call of the pure reply function if such a seam exists). Verified:
      `REPLY_TEXT = 'hey mate!'`, `build_reply(text) -> 'hey mate!'`.
- [x] Two consecutive text messages produce the same reply twice. Verified by
      `test_two_sequential_text_messages_produce_the_same_reply_twice`.
- [x] Photo / voice / sticker updates produce no reply and no exception. Verified
      manually and by parametrized unit tests.
- [x] `edited_message` and channel-post updates produce no exception. Verified:
      both return `None` without raising.
- [x] A handler exception is caught and logged at error level; the next update is
      still served. Verified manually and by `test_handler_catches_send_error...`.

## F. Live Telegram verification (requires a real token)

- [ ] `.env` contains a real `TELEGRAM_BOT_TOKEN` from @BotFather and a real
      `GEMINI_API_KEY` (placeholders are rejected by design).
- [ ] `uv run python -m telegram_documentaries` starts and logs
      "polling started" (polling loop confirmed in logs).
- [ ] Send "Hello" from Telegram; the bot replies `hey mate!`.
- [ ] Send several messages; each receives `hey mate!`.
- [ ] Send a photo; the bot does not crash (no reply expected this phase).
- [ ] Logs show the update handled without tracebacks.
- [ ] Ctrl-C stops the process cleanly.

> **Blocker on this box:** `.env` currently holds placeholders only. Steps in
> section F cannot pass until the Tech Lead supplies real credentials
> out-of-band. Sections A–E must all pass regardless.
>
> **Verifier status (2026-11-16): DEFERRED by the spec's definition of
> merge-ready.** `.env` was checked without printing values: both
> `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` contain only placeholder markers
> (`your_` / `_here`). No live Telegram verification was attempted. The deferral
> is recorded in `SPECS/ROADMAP.md` under Phase 1.
> **Re-verified 2026-11-19:** `.env` still holds placeholder values only
> (checked without printing values); section F remains explicitly deferred by
> the orchestrator. No live verification was attempted or fabricated.

## G. Scope discipline

- [x] No Gemini/ADK import, no prompt text, no model ID referenced in code.
      Verified by source search (only the `GEMINI_API_KEY` settings field exists).
- [x] No webhook code, no inbound HTTP server, no database. Verified by source
      search (no webhook/FastAPI/aiohttp/sqlite/etc.).
- [x] No `chat_id` session store (that is Phase 3). Verified by source search.
- [x] `.env` is gitignored and does not appear in `git status`. Verified:
      `.env` is untracked+ignored, not in the staged diff.
- [x] No generated media, `.venv`, or caches staged. Re-verified 2026-11-19:
      `git status --short` lists only `.pre-commit-config.yaml`, `README.md`,
      `SPECS/`, `pyproject.toml`, `scripts/`, `src/`, `tests/`, `uv.lock`;
      `git diff --cached --name-only` is empty; no `leak-test*` file exists
      anywhere; `.env`, `.env.save`, `.venv` and caches are ignored.

## H. Documentation and constitution sync

> **Result: PASS (re-verified 2026-11-19).** `README.md` exists and documents
> the pipeline, prerequisites, setup, long-polling run, dev scripts, pre-commit
> hooks, placeholder fail-fast behaviour and a "Not supported / deliberately
> deferred" section. All documented commands were executed successfully:
> `uv sync`, `uv run python -m telegram_documentaries` (fails fast as documented),
> `scripts/test`, `scripts/hooks`, `uv run pre-commit install`, `uv run pre-commit
> run --all-files`. The `cp .env.example .env` setup step was not re-run to avoid
> clobbering the existing `.env`; both files were confirmed to hold identical
> values. Live-token steps are explicitly marked as requiring credentials.
> `MISSION.md` and `TECH.md` remain accurate and untouched.

- [x] `README.md` exists and every command in it has been executed successfully
      (except the live-token steps, which are marked as requiring credentials).
      Verified as described above.
- [x] `README.md` states what is deliberately unsupported. Verified: the
      "Not supported / deliberately deferred" section covers webhooks,
      persistence, the real pipeline, authored `/start`, and more.
- [x] `SPECS/ROADMAP.md` Phase 1 status updated (or left `[ ]` with a note if
      live verification is blocked). Verified: Phase 1 status and note updated
      on re-verification to reflect all blockers resolved and the section-F
      deferral.
- [x] Any deviation between this spec and the implementation is recorded in
      `requirements.md` under "Open decisions / assumptions" — with user
      approval for anything impactful. Verified: resolution recorded; the only
      remaining deviation is the gitleaks self-test fixture, documented above.

## Definition of merge-ready

1. Sections A–E, G, H pass.
2. Section F passes **or** is explicitly deferred by the user because real
   credentials are not yet available (recorded in ROADMAP.md Phase 1).
3. `scripts/test` and `scripts/hooks` are clean.
4. No secrets anywhere in the repo, logs, or test fixtures.
5. Diff reviewed against `main` with no blocking findings.


## Verifier results appendix (2026-11-16; re-verified 2026-11-19)

Initial verification 2026-11-16 found blockers; re-verification on 2026-11-19
on branch `feature/2026-11-16-telegram-gateway` confirms all blockers resolved.
Ground-truth scripts: `scripts/test` exit **0** (33 passed); `scripts/hooks`
exit **0** and left the tree byte-for-byte unchanged.

| Section | Result | Why |
|---|---|---|
| A — Reproducible environment | **PASS** | uv 0.13.8, `uv sync` 0, `uv.lock` present, pyproject pins correct |
| B — Tests are ground truth | **PASS** | 33 tests green; lint-failure probe exits 1; no network I/O |
| C — Config fails fast | **PASS** | One-line placeholder/missing errors, exit 2, no traceback/secrets; `.env` restored |
| D — Pre-commit hooks | **PASS** | All five hooks fire; gitleaks + pytest self-tests each block; probes cleaned |
| E — Gateway isolation | **PASS** | `hey mate!` reply, non-text/no-message ignored, handler errors contained |
| F — Live Telegram | **DEFERRED (per spec)** | `.env` holds placeholders only; real credentials required |
| G — Scope discipline | **PASS** | No out-of-scope code; secrets/caches/venv ignored and unstaged |
| H — Docs/constitution sync | **PASS** | `README.md` present, all non-live commands executed; notes updated |

**Merge-ready: YES, with section F explicitly deferred** by the orchestrator
because real credentials are unavailable, as allowed by the Definition of
merge-ready. `scripts/test` and `scripts/hooks` are clean; no blockers remain.
