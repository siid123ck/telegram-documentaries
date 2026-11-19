# The Telegram Documentaries

A Telegram bot that, eventually, turns a user's portrait photo into a comedy
wildlife documentary *about them*: a hybrid animal portrait plus a short,
hushed, Attenborough-style voice note narrating "this magnificent specimen".

**Current status: Phase 1 — Repository & gateway.** The transport loop is provably
reachable and debuggable. There is no intelligence yet: **every text message gets
the fixed reply `hey mate!`**, in any chat. Non-text messages (photos, voice
notes, stickers) and Telegram noise (edits, channel posts) are ignored without
crashing the process.

## What the bot does / pipeline

The intended product pipeline is a hub-and-spoke orchestrator with five stages:

```
Telegram update -> transport adapter -> orchestrator (state machine)
                                           -> Bouncer      (human?)
                                           -> Interviewer  (questions + dossier)
                                           -> Converter    (hybrid portrait)
                                           -> Scripter     (narration text)
                                           -> Narrator     (TTS voice note)
                                        -> Telegram send
```

In Phase 1 **only the gateway exists**: an update arrives, a text message is
answered with `hey mate!`, and nothing else happens. The stages above, the state
machine, and the `chat_id` session store are deliberately deferred to later
phases (see [Not supported / deliberately deferred](#not-supported--deliberately-deferred)).

## Prerequisites

- **Python 3.11+**
- **[`uv`](https://docs.astral.sh/uv/)** for dependency management (this project
  uses `uv 0.13.8`; any recent version works). `uv` is commonly installed at
  `~/.local/bin/uv`.

No manual `pip install` is needed — `uv sync` builds the locked environment.

## Setup

From the repository root:

```bash
uv sync                       # create .venv from the committed uv.lock
cp .env.example .env          # create your local config file
```

Then edit `.env` and fill in real values. The file (and its committed template
`.env.example`) has two keys:

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) (**requires credentials**). |
| `GEMINI_API_KEY` | Google AI API key for the deferred model stages (**requires credentials**). |

> **Placeholder values fail fast by design.** `.env.example` ships markers such
> as `your_telegram_bot_token_here`. With those in place, the bot refuses to
> start: it prints **one** line naming the offending variable(s) and points at
> `.env.example`, then exits with status `2` — no stack trace, no partial boot,
> no secret values printed. This is a feature: a day-one failure must be a
> configuration failure, never a model failure.

`.env` is gitignored and is never committed. Secrets are read from the
environment only, are wrapped in `SecretStr`, and are never logged.

## Run locally (long polling)

```bash
uv run python -m telegram_documentaries
```

The bot uses **long polling** (`getUpdates`): no public URL, webhook, or inbound
HTTP server is needed. On startup it logs `polling started`, the package
version, and the config source (`file:.env` or `environment`) — never the values.
Send any text message to your bot in Telegram and it replies `hey mate!`.
Press **Ctrl-C** to stop cleanly.

Without a real token (the current placeholder setup), the same command exits
immediately with the single fail-fast line described above — that is expected
until you supply real credentials. **Live-token verification is not performed
here because it requires credentials.**

## Developer scripts

`scripts/test` and `scripts/hooks` are the ground truth. Both resolve `uv`
robustly even if `~/.local/bin` is not on your `PATH`.

```bash
scripts/test     # verify: ruff check, ruff format --check, mypy, pytest
scripts/hooks    # auto-fix: ruff check --fix, ruff format, then mypy, pytest
```

Run `scripts/test` to check a change and `scripts/hooks` before committing. The
suite is fully offline: Telegram objects are faked at the boundary, so no test
contacts Telegram, installs packages, or reads your real `.env`.

## Pre-commit hooks

Install the git hooks once per clone:

```bash
uv run pre-commit install
```

Each commit then runs `ruff check --fix`, `ruff format`, `mypy`, `pytest`, and
[gitleaks](https://github.com/gitleaks/gitleaks) (hardcoded-secret detection).
The checks are whole-tree and cannot be silently skipped. To run them all
manually without committing:

```bash
uv run pre-commit run --all-files
```

The first install/run may download hook environments and the gitleaks binary,
so it needs network access once.

## Project layout

```
src/telegram_documentaries/   package (config, errors, logging, gateway, app, entrypoint)
tests/                        offline unit tests
scripts/test, scripts/hooks   ground-truth dev scripts
.env.example                  documented config contract (placeholders)
pyproject.toml, uv.lock        reproducible uv-managed environment
```

## Not supported / deliberately deferred

These are **out of scope** and will not work in Phase 1; they need a later spec:

- **Webhooks, public URLs, inbound HTTP servers** — transport is long polling only.
- **Persistence / restart survival** — no databases, session replay, or `chat_id`
  store; state is not kept across restarts.
- **The real pipeline (Phases 2–7)** — no Bouncer, Interviewer, Converter,
  Scripter, or Narrator; no Gemini/ADK calls, prompts, model IDs, vision, image
  generation, or TTS. No state machine, dossier, reset (`/start`, `/restart`), or
  per-chat isolation yet.
- **Authored `/start` behaviour** — Telegram delivers `/start` as plain text, so
  in this phase it gets `hey mate!` like any other message.
- Media, group administration, payments, rate limiting, localisation, and
  analytics are also out of scope by the project constitution.

See `SPECS/ROADMAP.md` for the phase plan and `SPECS/` for the full contracts and
validation checklist.
