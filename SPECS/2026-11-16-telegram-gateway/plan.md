# Plan — Telegram Gateway (Phase 1)

Red/Green TDD. Each task group: write the failing test(s), run `scripts/test` to
confirm red, implement, run `scripts/test` to confirm green. Use
`scripts/test` and `scripts/hooks` — never raw `pytest`/`ruff`/`mypy` commands —
once they exist (Task group 1 creates them; before that, bootstrap them first).

## Task group 1 — Skeleton and dev scripts (no feature code yet)

1. Create `pyproject.toml`: project metadata, Python `>=3.11`, runtime deps
   (`python-telegram-bot>=21`, `pydantic>=2`, `pydantic-settings>=2`), dev deps
   (`pytest`, `pytest-asyncio`, `ruff`, `mypy`, `pre-commit`), and tool config:
   - `[tool.ruff]` — line length 88, target `py311`, rules incl. `E,F,I,UP,B,SIM`
   - `[tool.mypy]` — `strict = true`, `files = ["src", "tests"]`
   - `[tool.pytest.ini_options]` — `asyncio_mode = "auto"`, `testpaths = ["tests"]`
2. Create `src/telegram_documentaries/__init__.py` with `__version__`.
3. Create `scripts/test`: runs, in order and with `set -euo pipefail`,
   `ruff check .`, `ruff format --check .`, `mypy`, `pytest`. Resolve `uv`
   robustly (prepend `$HOME/.local/bin` if `uv` is not on PATH). Use `uv run`.
4. Create `scripts/hooks`: same trio but auto-fixing (`ruff check --fix`,
   `ruff format`) then `mypy`, then `pytest`. Must be safe to run repeatedly.
5. `chmod +x scripts/test scripts/hooks`.
6. Run `scripts/test`; it should pass on the empty skeleton. Commit-worthy
   baseline.

**Acceptance:** `scripts/test` exits 0 on the skeleton and exits non-zero if a
lint/type/test problem is introduced.

## Task group 2 — Settings contract (RED first)

1. Write `tests/test_config.py` covering every case in requirements.md:
   unset token, placeholder token, unset key, placeholder key, valid pair,
   secret never in `repr`/`str`, clear single-line error naming the variable.
2. Run `scripts/test` → red.
3. Implement `src/telegram_documentaries/errors.py` (`ConfigError`) and
   `src/telegram_documentaries/config.py` (`Settings` via pydantic-settings,
   `SecretStr` fields, placeholder validator, `load_settings()` raising
   `ConfigError` with a single remediation line).
4. Run `scripts/test` → green.

**Acceptance:** each invalid case raises `ConfigError`; the message names the
offending env var exactly once; no secret value appears in any assertion output.

## Task group 3 — Logging setup

1. Write `tests/test_logging.py`: calling the setup function twice does not
   duplicate handlers; a `@log_stage`-decorated function logs entry and outcome;
   a `SecretStr` argument is never rendered into the record (assert the raw
   secret is absent from captured output).
2. Run `scripts/test` → red.
3. Implement `src/telegram_documentaries/logging_setup.py`: one
   `configure_logging()` (idempotent), structured key=value or JSON formatting,
   and a `@log_stage` decorator that logs function name, outcome, duration, and
   exception type only.
4. Run `scripts/test` → green.

**Acceptance:** a test that logs `settings` shows `<redacted>`/absent secrets.

## Task group 4 — Gateway handler (RED first)

1. Write `tests/test_gateway.py`:
   - text message → reply text `hey mate!`
   - two sequential text messages → same reply twice
   - non-text (photo/voice/sticker) → no reply, no exception
   - update with no message (e.g. `edited_message`, channel post) → no
     exception
   - handler does not touch the network (no real `Bot` token in tests)
2. Run `scripts/test` → red.
3. Implement `src/telegram_documentaries/gateway.py`:
   - `REPLY_TEXT = "hey mate!"` constant
   - a pure `build_reply(update) -> str | None` function (testable without
     Telegram) plus the async handler that sends it when non-`None`
   - handler wrapped in try/except that logs at error level and never
     propagates
4. Run `scripts/test` → green.

**Acceptance:** reply constant imported by tests (single source), all
malformed-input cases pass, suite runs offline.

## Task group 5 — Entrypoint and startup validation

1. Write `tests/test_main.py`:
   - `main()` with `ConfigError` from settings → prints one line to stderr,
     returns/exits non-zero, and does **not** start polling (assert the poller
     was not called via a seam/fake).
   - `main()` with valid settings → calls the polling seam once.
2. Run `scripts/test` → red.
3. Implement `src/telegram_documentaries/__main__.py` and
   `telegram_documentaries/app.py`:
   - `main()`: `configure_logging()`, `load_settings()`, build
     `Application.builder().token(...)`, register handler, `run_polling()`.
   - Catch `ConfigError` → single `logging.error`/stderr line → `SystemExit(2)`.
   - Log "polling started" (no secrets) immediately before polling.
   - Provide a thin seam (`_run_polling(app)` or injected callable) so tests can
     assert polling was not reached.
4. Run `scripts/test` → green.

**Acceptance:** `uv run python -m telegram_documentaries` with placeholder
`.env` prints one clear line and exits non-zero, without a traceback.

## Task group 6 — Pre-commit hooks

1. Create `.pre-commit-config.yaml` with:
   - local hooks: `ruff check --fix`, `ruff format`, `mypy` (src+tests),
     `pytest` — each via `uv run`, `language: system`
   - `https://github.com/gitleaks/gitleaks` hook (`rev` pinned)
2. Run `pre-commit install`.
3. Verify by staged-file experiment: introduce a temporary file containing a
   fake AWS key (`AKIA` + 16 chars) and a failing test, attempt a commit, and
   confirm the commit is blocked (gitleaks and/or pytest fire). Remove the
   temporary file afterwards. Never actually create a commit in this step.
4. Run `scripts/hooks` manually → green.

**Acceptance:** hooks install cleanly; the self-test proves at least gitleaks
and pytest block a bad commit; `scripts/hooks` passes on the real tree.

## Task group 7 — README

1. Write `README.md`: what the bot is, Phase 1 status, prerequisites (Python
   3.11+, `uv`), setup (`uv sync`, copy `.env.example` → `.env` and fill),
   run (`uv run python -m telegram_documentaries`), `scripts/test`,
   `scripts/hooks`, `pre-commit install`, and an explicit "not supported:
   webhooks, persistence, real pipeline (Phases 2–7)" section.
2. Add a note that placeholder values fail fast by design.

**Acceptance:** a new developer can go from clone to a running poller using only
the README; every command in it was actually run.

## Task group 8 — Final checks

1. `scripts/test` → clean.
2. `scripts/hooks` → clean (no diff after run).
3. `uv lock` produces/updates `uv.lock`; commit it.
4. Confirm `git status` shows no `.env`, `.venv`, caches, or media.
5. Report what changed for the validation checklist.
