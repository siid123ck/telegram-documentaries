# The Telegram Documentaries

A Telegram bot that, eventually, turns a user's portrait photo into a comedy
wildlife documentary *about them*: a hybrid animal portrait plus a short,
hushed, Attenborough-style voice note narrating "this magnificent specimen".

**Current status: Phase 2 — Bouncer (vision gate), offline-verified.** Every text
message still gets the fixed reply `hey mate!`. A **photo** now goes through the
Bouncer, a Gemini vision check that decides whether a human is in the picture:

- **Human photo** → the bot replies `hey mate!` (the confirmation step; the
  interview itself arrives in Phase 3). The photo reference is stored for the chat.
- **Non-human photo** (cat, pizza, car, coffee mug, landscape, …) → a playful
  rejection names the object, e.g. *"Nice coffee mug — but this documentary needs a
  human. Send me a photo of a person."* The chat's photo state is reset and it is
  awaiting a new photo.
- **Vision failure** (timeout, rate limit, outage, unreadable reply) → *"Vision's
  acting up — mind resending the photo?"*. Nothing is stored and nothing crashes.
- **Empty, oversized (over 10 MiB), or malformed photos** → a short nudge; the
  vision model is never called.

Other messages (voice notes, stickers, edits, channel posts) are ignored without
crashing the process. Live Telegram and Gemini verification is **deferred** until
real credentials are supplied (see [Known limitations](#known-limitations)).

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

So far the **gateway** and the **Bouncer** exist. The Bouncer is the first Google
ADK agent: it classifies a photo as human or not and updates a minimal per-chat
photo state (`IDLE` / `AWAITING_PHOTO`). The Interviewer, Converter, Scripter,
Narrator, and the full state machine are deliberately deferred to later phases
(see [Not supported / deliberately deferred](#not-supported--deliberately-deferred)).

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
| `GEMINI_API_KEY` | Google AI API key used by the Bouncer vision gate (**requires credentials**). |

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
Send a photo and the Bouncer decides: a person gets `hey mate!`, anything else gets
the in-character rejection, and a vision failure asks you to resend.
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
src/telegram_documentaries/   package (config, errors, logging, gateway, app, entrypoint,
                              models, state, bouncer, vision_client, prompts,
                              photo_handler, messages)
tests/                        offline unit tests
scripts/test, scripts/hooks   ground-truth dev scripts
.env.example                  documented config contract (placeholders)
pyproject.toml, uv.lock        reproducible uv-managed environment
```

## Not supported / deliberately deferred

These are **out of scope** and will not work yet; they need a later spec:

- **Webhooks, public URLs, inbound HTTP servers** — transport is long polling only.
- **Persistence / restart survival** — session state is in memory only; a restart
  clears every chat's photo state.
- **The rest of the pipeline (Phases 3–7)** — no Interviewer, dossier, suggested
  animal, Converter, image generation, Scripter, or Narrator/TTS. The Bouncer is
  the only Gemini/ADK call. No interview, no `INTERVIEWING` transition, and no
  `/start` / `/restart` reset command yet.
- **Authored `/start` behaviour** — Telegram delivers `/start` as plain text, so
  it gets `hey mate!` like any other message.
- Media groups: only the first valid photo is considered; caption text is ignored.
- Media, group administration, payments, rate limiting, localisation, and
  analytics are also out of scope by the project constitution.

## Known limitations

- **Live verification is deferred.** The offline suite covers the Bouncer, the
  photo gate, and the state store with faked vision. The real Telegram and Gemini
  paths (a coffee mug or landscape rejected in character, a person approved, a
  clean start and Ctrl-C) have **not** been run, because `.env` holds placeholder
  credentials only.
- **Pass path replies `hey mate!`, not an interview.** This is an approved deviation
  from the ROADMAP wording until Phase 3.
- **One photo at a time per chat.** A second photo that arrives while the first is
  still being checked gets a short "one at a time" nudge.
- The Bouncer's free-form `reason` is logged but never shown to the user.
- Image bytes are always sent to the model as `image/jpeg`, since Telegram photo
  sizes are always JPEG.

See `SPECS/ROADMAP.md` for the phase plan and `SPECS/` for the full contracts and
validation checklist.
