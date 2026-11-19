# Requirements — Telegram Gateway (Phase 1)

Spec folder: `SPECS/2026-11-16-telegram-gateway/`
Roadmap phase: **Phase 1 — Repository & gateway**
Branch: `feature/2026-11-16-telegram-gateway`

## Context

The project has a constitution (MISSION/TECH/ROADMAP) but no code. Phase 1
creates the skeleton that every later agent plugs into: a reproducible Python
project, a validated configuration boundary, and a Telegram long-polling gateway
that answers every message with the same hardcoded reply.

**No intelligence in this phase.** No Gemini, no ADK agents, no vision, no
image, no TTS. A message goes in, `hey mate!` comes out.

Why: the pipeline must be provably reachable and debuggable before any model is
introduced. A failure on day one should be a transport or config failure, never
a prompt failure.

## Scope

### In scope

1. **Project skeleton** — `uv`-managed `pyproject.toml` (Python 3.11+),
   `src/` layout, committed `uv.lock`, `scripts/` directory.
2. **Typed configuration** at the edge — load `.env`, validate
   `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`, fail fast with exactly one clear
   line when absent or placeholder. Never print or log the values.
3. **Long-polling gateway** — `python-telegram-bot` v21+ async. Any inbound
   text message returns the fixed reply `hey mate!`. Non-text messages and
   unknown/unhandled update types must not crash the process.
4. **Dev scripts as ground truth** — `scripts/test` and `scripts/hooks`.
5. **Pre-commit hooks** — ruff, ruff-format, mypy, pytest, gitleaks.
6. **README.md** — setup, run, dev scripts, deliberate limitations.

### Out of scope (do not build)

- Any Gemini/ADK call, any agent, any prompt text.
- State machine, session store, `chat_id` bookkeeping (Phase 3).
- Webhooks, inbound HTTP server, public URL.
- Databases, persistence, restart survival.
- Audio, images, `/start` or `/restart` custom behaviour (Phase 7).
- Rate limiting, metrics, dashboards.

## Decisions

| Decision | Value | Rationale |
|---|---|---|
| Dependency manager | `uv` + `pyproject.toml` + `uv.lock` | TECH.md |
| Telegram library | `python-telegram-bot` v21+ (async) | Confirmed by Tech Lead |
| Transport | Long polling (`run_polling`) | TECH.md; no public URL |
| Reply text | `hey mate!` (exactly) | Phase 1 acceptance criterion |
| Reply scope | Every text message, any chat, identical | Proves the loop |
| Config validation | Fail fast, single line, exit non-zero | TECH.md error policy |
| Secrets in logs | Never | MISSION.md non-negotiable |

## Contracts (typed at the boundary)

### `Settings`

Pydantic settings model loaded from the environment / `.env`:

- `telegram_bot_token: SecretStr` — required, non-empty, and must not match the
  known placeholder form (contains `your_` / `_here`, e.g.
  `your_telegram_bot_token_here`).
- `gemini_api_key: SecretStr` — required, non-empty, same placeholder rule.
  Not used by any call in this phase, but validated now so Phase 2+ cannot boot
  half-configured.

Failure behaviour: a single `ConfigError` with a message naming the missing or
placeholder variable(s) and the remediation (`see .env.example`). The process
exits with a non-zero status. No stack trace. No partial boot.

`SecretStr` guarantees a token cannot be interpolated into a log line by
accident; any diagnostic must use a redacted form.

### Message handling

The handler accepts a parsed Telegram update, and for a text message returns the
constant reply. It does not read or store per-chat state. The reply constant
lives in one module and is asserted by tests.

Malformed or unexpected updates (no message, empty text, non-text message, edits,
channel posts) are ignored or acknowledged without raising. Nothing reaches the
network beyond Telegram itself.

## Failure behaviour

| Condition | Behaviour |
|---|---|
| `.env` missing entirely | One line: which variables are unset; exit non-zero |
| Placeholder token present | One line: placeholder detected; exit non-zero |
| Telegram rejects the token | Logged clearly, process exits non-zero |
| Transient network error while polling | Library handles retry; log at warning/error, stay alive |
| Unsupported update type | Ignored; log at debug; process stays alive |
| Handler raises unexpectedly | Caught and logged at error level; next update still served |

## Logging

- Structured, one logger per module, configured once at startup.
- The stage boundary is decorated (`@log_stage`) rather than logging sprinkled
  through the handler body.
- Log at startup: "polling started", the package version, and the *destination*
  of config (file/`env`) — never the values.
- Forbidden: token, API key, or `SecretStr` contents in any log record at any
  level.

## Pre-commit hook requirements

`.pre-commit-config.yaml` must run, on commit:

1. `ruff check --fix`
2. `ruff format`
3. `mypy` (on `src/` and `tests/`)
4. `pytest`
5. `gitleaks` (official hook repo `https://github.com/gitleaks/gitleaks`, since
   no system binary is installed)

Notes for the implementer:

- `uv` lives at `~/.local/bin/uv` and may not be on the PATH that git uses for
  hooks. Scripts and hook entries must resolve it robustly (e.g. prepend
  `$HOME/.local/bin` when `uv` is not on PATH) rather than assuming a shell
  profile was sourced.
- Hook installation may download the gitleaks binary and hook environments;
  that is expected and requires network on first `pre-commit install`.
- `pytest` inside a commit hook is intentional and must be fast: unit tests
  only, no network, no sleeps.

## Testing requirements (Red/Green TDD)

Tests are written first and must fail before implementation.

- `test_config.py`: unset token → `ConfigError` naming the variable; placeholder
  token → `ConfigError`; unset key → `ConfigError`; valid pair → `Settings`
  constructed; `repr`/`str` of `Settings` never contains the secret value.
- `test_gateway.py`: a text update yields exactly `hey mate!`; a second text
  update yields the same reply; a non-text update yields no reply and no
  exception; a malformed update yields no exception; no network access occurs
  (Telegram objects are constructed/faked locally).
- A test asserting the reply constant is imported, not duplicated.

No test may contact Telegram, install packages, or depend on `.env` contents.

## Repo hygiene

- `.env` stays gitignored; `.env.example` stays the documented contract.
- `uv.lock` committed.
- No generated media, caches, or `.venv` committed.

## Open decisions / assumptions

The planning subagent was unavailable (connection drops), so this spec was
drafted by the Build orchestrator. Assumptions recorded for the user:

1. **Placeholder detection** is a heuristic on known placeholder forms
   (`your_`, `_here`) rather than a strict token-shape regex. Simpler and
   sufficient; a shape regex would be brittle.
2. **`/start` is not customised** in Phase 1 — Telegram sends `/start` as text,
   so it receives `hey mate!` like everything else. Dedicated `/start` copy
   lands in Phase 7 with reset semantics.
3. **`GEMINI_API_KEY` is validated but unused** this phase. Chosen to keep one
   config boundary for all later phases.
4. **No `logging` config file**; configuration is code (one setup function).
   YAGNI.
5. **Backward compatibility is not a concern** — greenfield, no legacy callers.
6. **`hey mate!` is a module constant**, not config. Phase 1 does not need a
   toggle.
7. **`uv` install on this box** is a prerequisite already satisfied
   (`0.13.8`); CI/dev machines need it too, documented in the README.


## Verification deviations — resolved on re-verification (2026-11-19)

The first verification (2026-11-16) recorded the following gaps against this
spec. All were closed by the spec-implementer and independently re-verified on
branch `feature/2026-11-16-telegram-gateway`:

| Previously missing (task group) | Now verified present |
|---|---|
| Gateway `REPLY_TEXT = "hey mate!"`, `build_reply`, async handler (TG 4) | `gateway.py` + `tests/test_gateway.py`; manual offline call confirmed the reply and that all other payloads are ignored |
| Entrypoint `main()` wired to `load_settings()` + `run_polling()` (TG 5) | `app.py`, `__main__.py` + `tests/test_main.py`; placeholder `.env` yields one line, exit 2, no traceback or secret values |
| `.pre-commit-config.yaml` with ruff/ruff-format/mypy/pytest/gitleaks (TG 6) | Present with all five hooks; `install` and `--all-files` green; gitleaks and pytest self-tests each block a bad commit |
| `README.md` with setup/run/dev scripts (TG 7) | Present; all non-live commands executed successfully |
| `uv.lock` committed | Still present in the working tree; the actual commit is the orchestrator's step |

**Remaining documented deviation (fixture only, no code impact):** the
self-test key suggested in `validation.md` section D,
`AKIAIOSFODNN7EXAMPLE`, is explicitly allowlisted by gitleaks (`.+EXAMPLE$`)
and does not match its AWS rule charset `[A-Z2-7]{16}`. Verification used a
compliant high-entropy fake key instead, which correctly fires the hook. The
`validation.md` fixture note has been corrected accordingly.

`scripts/test` and `scripts/hooks` both run clean with 33 passing tests. Live
Telegram verification (validation section F) remains explicitly deferred by the
orchestrator because `.env` holds placeholders only; the deferral is recorded
in `SPECS/ROADMAP.md` Phase 1. `MISSION.md` and `TECH.md` are unchanged.
