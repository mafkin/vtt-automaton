# vtt-automaton

Pathfinder 2e rules assistant for Foundry VTT and session transcription via a Discord bot, backed by
a single service. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
**[docs/SETUP.md](docs/SETUP.md) for server setup and the end-to-end test plan**.

## Backend (phase 1: rules)

```bash
cd backend
cp .env.example .env          # set VTT_CLIENT_TOKENS and VTT_GEMINI_API_KEY
uv sync
uv run uvicorn --factory app.main:app_factory --port 8765
```

### Rules database

The rules DB is imported from Archives of Nethys (Remaster versions only, ~29k entries, ~130 MB,
about a minute). The running backend checks for a new AoN build on startup and every
`VTT_RULES_REFRESH_HOURS`, and only re-imports when AoN has changed. To import by hand:

```bash
uv run python -m app.ingest.aon            # writes VTT_RULES_DB_PATH; --force to re-import
```

### Deploy on the home server

Public address: **https://arbiter.ttrpg-arbiter.org** (Cloudflare tunnel → `http://backend:8765`).

On the server, with Docker installed:

```bash
git clone https://github.com/mafkin/vtt-automaton.git && cd vtt-automaton
./scripts/setup-server.sh
```

The script asks for the Cloudflare tunnel token, the Gemini API key and the Foundry (Molten)
address. It generates the GM's client token, writes `.env` and `backend/.env`, starts the stack,
waits for the rules import, checks the public address with a test ruling, and prints what the
GM enters in Foundry. Re-running it keeps existing values. To update later:
`git pull && docker compose up -d --build`.

It also offers to set up **Discord transcription** (the `transcription` compose profile: the
Discord bot and the GPU speech-to-text worker). For that you need:

- A Discord application with a bot: discord.com/developers/applications → New Application → Bot →
  Reset Token. Invite it with OAuth2 → URL Generator, scopes `bot` + `applications.commands`,
  permissions *View Channels*, *Send Messages*, *Attach Files*, *Connect*.
- Your Discord server ID (Developer Mode → right-click the server → Copy Server ID).
- An NVIDIA GPU with the driver and the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/).
  The Whisper `large-v3` model (~3 GB) downloads on first start.

## Discord transcription

In Discord, join the voice channel and use:

| Command | What it does |
|---|---|
| `/session start [label]` | Bot joins your voice channel and starts transcribing each speaker |
| `/session stop` | Stops, and posts the transcript as a text file in the channel |
| `/session status` | Duration, speakers, lines transcribed so far |
| `/session transcript` | Posts the latest session's transcript again |
| `/link character:<name>` | Your character's name, shown next to yours in the transcript |
| `/optout`, `/optin` | Leave yourself out of recording, or back in |

No audio is saved; clips live in memory only until transcribed. Saying "Nethys, …" during a
session asks the rules arbiter, and the ruling appears in Foundry.

Tests: `cd discord-bot && npm ci && npm test`, `cd stt-worker && uv run pytest`.

## Foundry module

`foundry-module/pf2e-ai-arbiter`. Install on Molten from the manifest URL
`https://github.com/mafkin/vtt-automaton/releases/latest/download/module.json` (published by
tagging `module-vX.Y.Z`). The backend URL defaults to `https://arbiter.ttrpg-arbiter.org`; the GM
only enters the client token, **in their own browser**.

- `/rule <question>`: public ruling. `/gmrule <question>`: whispered to the GM.
- Spoken questions: say "Nethys, …" at the table; once transcription runs, the ruling appears in
  chat. To try it now, with the GM connected in Foundry:

```bash
curl -s https://arbiter.ttrpg-arbiter.org/api/v1/transcript/segments \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"speaker": "Aino", "text": "Nethys, kaatuuko örkki jos teen Tripin?"}'
```

Tests: `cd foundry-module/pf2e-ai-arbiter && npm test`.
