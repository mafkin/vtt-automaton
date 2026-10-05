#!/usr/bin/env bash
# One-time setup of the VTT Automaton backend on the home server.
#
# Writes the env files, starts the Docker Compose stack (backend + cloudflared, and optionally the
# Discord transcription bot + GPU speech-to-text worker), waits for the rules database import,
# checks the public URL and prints what the GM enters in Foundry.
# Safe to re-run: existing values are kept unless you choose to replace them.
#
# Usage: ./scripts/setup-server.sh            (from anywhere inside the repo)
# Linux or macOS with Docker. On Windows, run it inside WSL with Docker Desktop's WSL integration.
set -euo pipefail

PUBLIC_URL="${PUBLIC_URL:-https://arbiter.ttrpg-arbiter.org}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_ENV="$ROOT/.env"
BACKEND_ENV="$ROOT/backend/.env"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✔\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$*" >&2; exit 1; }

# Read KEY's value from an env file (empty if missing).
env_get() { [[ -f "$2" ]] && sed -n "s/^$1=//p" "$2" | tail -n1 || true; }

# Ask for a value, offering to keep the current one. $3 = "secret" hides input.
ask() {
  local prompt="$1" current="$2" secret="${3:-}" value=""
  if [[ -n "$current" ]]; then
    read -rp "  $prompt [press Enter to keep the current value]: " ${secret:+-s} value
  else
    while [[ -z "$value" ]]; do read -rp "  $prompt: " ${secret:+-s} value; done
  fi
  [[ -n "$secret" ]] && echo >&2
  printf '%s' "${value:-$current}"
}

new_token() {
  if command -v python3 >/dev/null; then python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
  else openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'; fi
}

say "1/5 Checking prerequisites"
command -v docker >/dev/null || fail "Docker is not installed: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || fail "Docker is installed but not running (or this user can't use it)."
docker compose version >/dev/null 2>&1 || fail "The Docker Compose plugin is missing ('docker compose')."
command -v curl >/dev/null || fail "curl is required."
ok "Docker $(docker version --format '{{.Server.Version}}'), Compose plugin and curl found"

say "2/5 Configuration"
echo "  Cloudflare: Zero Trust → Networks → Tunnels → your tunnel → copy the token from the install command."
TUNNEL_TOKEN="$(ask "Cloudflare tunnel token" "$(env_get CLOUDFLARE_TUNNEL_TOKEN "$ROOT_ENV")" secret)"
GEMINI_KEY="$(ask "Gemini API key" "$(env_get VTT_GEMINI_API_KEY "$BACKEND_ENV")" secret)"
echo "  Foundry address: copy it from the GM's browser bar, e.g. https://ourworld.moltenhosting.com"
ORIGIN="$(ask "Foundry (Molten) address" "$(env_get VTT_CORS_ORIGINS "$BACKEND_ENV")")"
ORIGIN="${ORIGIN%/}"
[[ "$ORIGIN" =~ ^https://[^/]+$ ]] || fail "The Foundry address must look like https://host (no path), got: $ORIGIN"

# The first client token is the GM's; the bot and the STT worker get their own.
CLIENT_TOKEN="$(env_get VTT_CLIENT_TOKENS "$BACKEND_ENV" | cut -d, -f1)"
if [[ -z "$CLIENT_TOKEN" ]]; then
  CLIENT_TOKEN="$(new_token)"
  ok "Generated a new client token for the GM"
else
  ok "Keeping the existing client token"
fi

BOT_ENV="$ROOT/discord-bot/.env"
STT_ENV="$ROOT/stt-worker/.env"
PROFILES="$(env_get COMPOSE_PROFILES "$ROOT_ENV")"
default_answer="n"; [[ "$PROFILES" == *transcription* ]] && default_answer="y"
read -rp "  Set up Discord transcription (bot + GPU speech-to-text)? [y/n, default $default_answer]: " answer
answer="${answer:-$default_answer}"
ALL_TOKENS="$CLIENT_TOKEN"
if [[ "$answer" =~ ^[yYkK] ]]; then
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
    ok "NVIDIA container runtime found"
  else
    echo "  ! Docker has no NVIDIA runtime, so the speech-to-text worker can't use the GPU. Install the"
    echo "    NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"
  fi
  echo "  Discord: discord.com/developers/applications → your app → Bot → Reset Token."
  DISCORD_TOKEN="$(ask "Discord bot token" "$(env_get DISCORD_TOKEN "$BOT_ENV")" secret)"
  echo "  Server ID: Discord settings → Advanced → Developer Mode on, then right-click the server → Copy Server ID."
  GUILD_ID="$(ask "Discord server ID" "$(env_get DISCORD_GUILD_ID "$BOT_ENV")")"
  [[ "$GUILD_ID" =~ ^[0-9]+$ ]] || fail "The server ID is a number, got: $GUILD_ID"
  BOT_BACKEND_TOKEN="$(env_get BACKEND_TOKEN "$BOT_ENV")"; BOT_BACKEND_TOKEN="${BOT_BACKEND_TOKEN:-$(new_token)}"
  STT_SHARED_TOKEN="$(env_get STT_TOKEN "$STT_ENV")"; STT_SHARED_TOKEN="${STT_SHARED_TOKEN:-$(new_token)}"
  STT_BACKEND_TOKEN="$(env_get STT_BACKEND_TOKEN "$STT_ENV")"; STT_BACKEND_TOKEN="${STT_BACKEND_TOKEN:-$(new_token)}"
  ALL_TOKENS="$CLIENT_TOKEN,$BOT_BACKEND_TOKEN,$STT_BACKEND_TOKEN"
  PROFILES="transcription"
else
  PROFILES=""
fi

umask 077  # the env files hold secrets: readable by this user only
cat > "$ROOT_ENV" <<ENV
CLOUDFLARE_TUNNEL_TOKEN=$TUNNEL_TOKEN
COMPOSE_PROFILES=$PROFILES
ENV
cat > "$BACKEND_ENV" <<ENV
VTT_RULES_DB_PATH=data/pf2e_remaster.db
VTT_CLIENT_TOKENS=$ALL_TOKENS
VTT_CORS_ORIGINS=$ORIGIN
VTT_LLM_PROVIDER=gemini
VTT_GEMINI_API_KEY=$GEMINI_KEY
VTT_GEMINI_MODEL=$(env_get VTT_GEMINI_MODEL "$BACKEND_ENV" | grep . || echo gemini-3.8-flash)
VTT_ANSWER_LANGUAGE=Finnish
VTT_MAX_RULING_CHARS=3000
VTT_WAKE_WORDS=$(env_get VTT_WAKE_WORDS "$BACKEND_ENV" | grep . || echo Nethys)
VTT_RULES_REFRESH_HOURS=24
ENV
if [[ "$PROFILES" == transcription ]]; then
  cat > "$BOT_ENV" <<ENV
DISCORD_TOKEN=$DISCORD_TOKEN
DISCORD_GUILD_ID=$GUILD_ID
BACKEND_TOKEN=$BOT_BACKEND_TOKEN
STT_TOKEN=$STT_SHARED_TOKEN
EMPTY_CHANNEL_MINUTES=$(env_get EMPTY_CHANNEL_MINUTES "$BOT_ENV" | grep . || echo 5)
ENV
  cat > "$STT_ENV" <<ENV
STT_TOKEN=$STT_SHARED_TOKEN
STT_BACKEND_TOKEN=$STT_BACKEND_TOKEN
STT_MODEL=$(env_get STT_MODEL "$STT_ENV" | grep . || echo large-v3)
STT_DEVICE=$(env_get STT_DEVICE "$STT_ENV" | grep . || echo auto)
STT_COMPUTE_TYPE=$(env_get STT_COMPUTE_TYPE "$STT_ENV" | grep . || echo default)
STT_LANGUAGE=fi
STT_BEAM_SIZE=$(env_get STT_BEAM_SIZE "$STT_ENV" | grep . || echo 5)
STT_PROMPT_TERMS=$(env_get STT_PROMPT_TERMS "$STT_ENV" | grep . || echo "Nethys,Pathfinder,Strike,Stride,Step,Trip,Grapple,Shove,Demoralize,Reactive Strike,Shield Block,off-guard,prone,frightened,flanking,hit points,AC,DC")
ENV
fi
mkdir -p "$ROOT/data"
ok "Wrote the env files (permissions 600)"

say "3/5 Starting the stack"
(cd "$ROOT" && docker compose up -d --build)
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8765/healthz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8765/healthz >/dev/null 2>&1 || {
  (cd "$ROOT" && docker compose logs --tail 40 backend)
  fail "The backend did not start; see the log above."
}
ok "Backend is running on this machine"

say "4/5 Importing rules from Archives of Nethys (about a minute on first start)"
for _ in $(seq 1 150); do
  [[ -s "$ROOT/data/pf2e_remaster.db" ]] && break
  sleep 2
done
[[ -s "$ROOT/data/pf2e_remaster.db" ]] || {
  (cd "$ROOT" && docker compose logs --tail 40 backend)
  fail "The rules database was not created; see the log above."
}
ok "Rules database ready ($(du -h "$ROOT/data/pf2e_remaster.db" | cut -f1))"

say "5/5 Checking the public address and a test ruling"
public_ok=""
for _ in $(seq 1 15); do
  curl -fsS "$PUBLIC_URL/healthz" >/dev/null 2>&1 && { public_ok=1; break; }
  sleep 2
done
if [[ -n "$public_ok" ]]; then
  ok "$PUBLIC_URL answers through the Cloudflare tunnel"
else
  echo "  ! $PUBLIC_URL is not reachable yet. Check that the tunnel's public hostname points to"
  echo "    http://backend:8765 and run: docker compose logs cloudflared"
fi

base_url="http://127.0.0.1:8765"
[[ -n "$public_ok" ]] && base_url="$PUBLIC_URL"
ruling_status="$(curl -s -o /tmp/vtt-ruling.json -w '%{http_code}' -m 60 \
  "$base_url/api/v1/rulings" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "Mitä Trip tekee?"}' || true)"
if [[ "$ruling_status" == "200" ]] && grep -q '"raw_only":false' /tmp/vtt-ruling.json; then
  ok "Test ruling worked (Gemini answered)"
elif [[ "$ruling_status" == "200" ]]; then
  echo "  ! The ruling came back as rules text only, so Gemini did not answer. Check the API key:"
  echo "    docker compose logs backend | grep -i -A3 'ruling generation failed'"
else
  echo "  ! Test ruling failed (HTTP $ruling_status): $(head -c 300 /tmp/vtt-ruling.json 2>/dev/null)"
fi
rm -f /tmp/vtt-ruling.json

if [[ "$PROFILES" == transcription ]]; then
  say "Transcription"
  bot_ok=""
  for _ in $(seq 1 30); do
    (cd "$ROOT" && docker compose logs discord-bot 2>/dev/null | grep -q "Logged in as") && { bot_ok=1; break; }
    sleep 2
  done
  if [[ -n "$bot_ok" ]]; then
    ok "Discord bot is online; /session start works in your server"
  else
    echo "  ! The Discord bot did not log in yet: docker compose logs discord-bot"
  fi
  echo "  The speech-to-text worker downloads the Whisper model (~3 GB) on first start."
  echo "  Follow it with: docker compose logs -f stt-worker"
fi

say "Done. Send these to the GM privately (e.g. a Discord DM):"
cat <<INFO
  Foundry → Game Settings → Configure Settings → PF2e AI Arbiter
    Backend URL:   $PUBLIC_URL
    Client token:  $CLIENT_TOKEN   (entered in the GM's own browser)

  The client token is also stored in backend/.env. To revoke it, delete the VTT_CLIENT_TOKENS
  line and re-run this script; it generates new tokens.
INFO
