# vtt-automaton

Pathfinder 2e rules assistant for Foundry VTT and session transcription via a Discord bot, backed by
a single service. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
