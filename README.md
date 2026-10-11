# Vitals: Telegram health + founder input tracker (Phase 1)

A personal bot that pulls overnight sleep / HRV / resting HR from Google Health (Fitbit Air), messages you a
2-sentence recovery brief each morning, and asks what you did, by text or voice note, so inputs can later be
correlated with outcomes.

```
auth.py              one-time Google OAuth (PKCE) helper
probe.py             dump raw Google Health responses to verify endpoints/parsers
vitals/
  config.py          .env loading
  schema.sql, db.py  SQLite: biometric_snapshots, input_events (ledger), daily_input_totals (view), kv
  google_health.py   token refresh + v4 fetch + defensive parsers
  llm.py             Anthropic tool-calling: free text -> structured events
  transcribe.py      Whisper (OpenAI API) for voice notes
  briefs.py          baseline comparison + 2-sentence brief
  bot.py             Telegram handlers + 08:15 / 17:45 jobs
tests/test_core.py
```

## Setup

1. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `cp .env.example .env`
3. **Telegram:** message @BotFather, `/newbot`, put the token in `TELEGRAM_BOT_TOKEN`.
4. **Google Cloud (Desktop OAuth client):**
   1. console.cloud.google.com, create or pick a project.
   2. APIs & Services, Library, enable the **Google Health API**.
   3. APIs & Services, OAuth consent screen: User type *External*, fill the app name and your email, and add your
      own Google account under **Test users**. (Testing mode refresh tokens expire after 7 days; use **Publish app**
      to move to production, which for personal use only shows an "unverified app" warning, to avoid re-auth.)
   4. Add the scopes from `GOOGLE_SCOPES` (Data access / Scopes page).
   5. Credentials, Create credentials, OAuth client ID, Application type **Desktop app**.
   6. Copy the client ID and secret into `.env`.
   7. Run `python auth.py`, approve in the browser, and the refresh token is saved to `data/google_token.json`.
5. **Verify the API shapes:** `python probe.py`. The v4 endpoint and response parsing in
   `vitals/google_health.py` are unverified (see the module docstring). If `PARSED` is empty while `RAW` has
   data, adjust the `parse_*` functions. If `RAW` shows HTTP errors, fix `GOOGLE_HEALTH_BASE_URL`, `GH_TYPE_*`
   or the scopes.
6. Add `ANTHROPIC_API_KEY` (parsing + brief) and `OPENAI_API_KEY` (voice notes).
7. `python -m vitals`, message the bot `/start`, put the printed id in `TELEGRAM_OWNER_ID`, restart.

## Using it

- 08:15: sync + brief, then the habits prompt. 17:45: the founder prompt. Reply with text or a voice note.
- Replies to the morning prompt are filed under *yesterday*; replies to the evening prompt under *today*.
- `/brief` re-syncs and re-sends. `/today` shows yesterday's and today's totals. `/undo` removes your last message's entries.
- Everything is an append-only row in `input_events`; `daily_input_totals` aggregates on the fly.

## Tests

`python -m pytest`
