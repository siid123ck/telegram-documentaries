# MISSION — The Telegram Documentaries

> The product contract. Every feature spec is judged against this file.
> If a feature contradicts MISSION.md, the feature is wrong, not MISSION.md.

## Vision

A Telegram bot that turns a user's portrait photo into a comedy wildlife
documentary about *them*. Send a selfie, answer a handful of questions, and
receive a hybrid animal portrait of yourself plus a short, hushed, David
Attenborough-style narration of "this magnificent specimen" in the wild.

The joke works because the user's own interview answers become the animal's
observed behaviour. The user is the subject, not the viewer.

## End-to-end user experience

1. User sends `/start` (or any first photo). The bot explains the game in one
   short message.
2. User sends a portrait photo.
3. **Bouncer** confirms the image contains a human. On failure it replies with a
   cheeky rejection and resets to the start of the conversation (not the whole
   process).
4. **Interviewer** asks 5–7 questions, exactly one message at a time, waiting for
   each answer. It builds a behavioural dossier and settles on a suggested
   animal.
5. **Converter** fuses the original photo with the dossier into a hybrid animal
   portrait and returns it to the chat as an image.
6. **Scripter** writes one paragraph (~60–90 words) of dramatic narration from
   the dossier.
7. **Narrator** renders that script with Gemini TTS and delivers it as an
   OGG/Opus Telegram voice note.

## In scope

- The five pipeline stages above: Bouncer, Interviewer (also the orchestrator),
  Converter, Scripter, Narrator (TTS, not an agent).
- Telegram long polling; several users in separate chats served concurrently
  within one process.
- `/start` and `/restart` commands.
- Graceful handling of any payload arriving at any stage (text when a photo is
  expected, photo mid-interview, stickers, voice notes, captions, edits).
- A short per-stage failure path that keeps the conversation alive.

## Out of scope (YAGNI — do not build these)

- Webhooks, public URLs, or any inbound HTTP server.
- Databases, persistence across process restarts, session replay, or history.
- Video, GIFs, or animation output.
- Group-chat administration, channel support, or multi-user shared sessions.
- Payments, subscriptions, quotas, rate limiting, or cost dashboards.
- Languages other than English; localisation infrastructure.
- Editing, regenerating, or A/B-testing a delivered portrait or script.
- Analytics, dashboards, admin UIs, or telemetry beyond structured logs.

Anything not listed under "In scope" is out of scope until a new spec argues
for it and this file is amended.

## Success criteria

The project is "working" when, from a cold start with valid `.env` values:

- **Happy path:** a photo plus 5–7 answers produces a hybrid portrait, then a
  one-paragraph script, then a playable voice note — in that order, in the same
  chat.
- **Reset:** `/restart` at any point (mid-interview, mid-generation) discards the
  dossier, the photo reference, and temporary media, and returns the chat to the
  opening prompt. The process is not restarted and other chats are unaffected.
- **Wrong payload:** text sent where a photo is expected, or a photo sent
  mid-interview, produces a helpful nudge and leaves the stage unchanged.
- **Non-human photo:** is rejected in character and returns the chat to
  awaiting-photo.
- **Isolation:** two concurrent chats never see each other's state, image, or
  narration.
- **No secrets:** no token or API key appears in source, logs, error messages, or
  committed files.

## Non-negotiables

- **Session isolation.** All state is keyed by `chat_id`. No global mutable
  "current user". Data from one chat must be unreachable from another.
- **Secrets.** `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` are read from the
  environment only. Never hardcoded, never logged, never echoed to the user,
  never committed.
- **Privacy.** Raw image bytes and full user answers are never written to logs;
  log identifiers and stage transitions instead.
- **The conversation must not die.** A failing stage degrades to a helpful
  message; it never surfaces a stack trace or leaves the user stranded in a
  silent state.
- **Cost discipline.** Exactly one TTS synthesis and one portrait generation per
  successful run. No speculative background generations.
