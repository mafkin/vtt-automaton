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

The rules database (`VTT_RULES_DB_PATH`) must follow the schema in `backend/app/rules/store.py`.

```bash
curl -s localhost:8765/api/v1/rulings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "Mitä tapahtuu kun villisika tekee Trample?", "render": "discord"}'
```

Tests: `uv run pytest`. Lint: `uv run ruff check . && uv run ruff format --check .`
