# vtt-automaton

Pathfinder 2e rules arbiter and session transcription for Foundry VTT and Discord, backed by a
single service. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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

```bash
cp .env.example .env                   # CLOUDFLARE_TUNNEL_TOKEN
cp backend/.env.example backend/.env   # VTT_CORS_ORIGINS = your Molten world URL
docker compose up -d --build
```

In the Cloudflare tunnel's public hostname settings, route `arbiter.<your-domain>` to
`http://backend:8765`.

```bash
curl -s localhost:8765/api/v1/rulings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "Mitä tapahtuu kun villisika tekee Trample?", "render": "discord"}'
```

Tests: `uv run pytest`. Lint: `uv run ruff check . && uv run ruff format --check .`
