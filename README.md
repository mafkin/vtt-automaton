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

The rules DB is imported from Archives of Nethys (Remaster versions only, ~29k entries, ~65 MB,
about a minute). The running backend checks for a new AoN build on startup and every
`VTT_RULES_REFRESH_HOURS`, and only re-imports when AoN has changed. To import by hand:

```bash
uv run python -m app.ingest.aon            # writes VTT_RULES_DB_PATH; --force to re-import
```

### Deploy on the home server

Public address: **https://arbiter.ttrpg-arbiter.org** (Cloudflare tunnel → `http://backend:8765`).

On the server (Ubuntu 22.04/24.04 or Arch Linux/CachyOS), with Docker and the Compose plugin
installed ([docs/SETUP.md A2](docs/SETUP.md#a2-install-docker-on-the-server) has the commands for
both):

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
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
  ([docs/SETUP.md A3](docs/SETUP.md#a3-gpu-support-for-docker-transcription-only): Ubuntu and
  Arch/CachyOS). The Whisper `large-v3` model (~3 GB) downloads on first start.

## Discord transcription

In Discord, join the voice channel and use:

| Command | What it does |
|---|---|
| `/session start [label] [podcast]` | Bot joins your voice channel and starts transcribing each speaker; `podcast:true` also records podcast tracks |
| `/session stop` | Stops, and posts the transcript as a text file in the channel |
| `/session status` | Duration, speakers, lines transcribed so far |
| `/session transcript` | Posts the latest session's transcript again |
| `/link character:<name>` | Your character's name, shown next to yours in the transcript |
| `/optout`, `/optin` | Leave yourself out of recording, or back in |
| `/podcast join`, `/podcast leave` | Consent to podcast tracks, or withdraw (your unpublished tracks are deleted) |

Without `podcast:true` no audio is saved; clips live in memory only until transcribed. With
it, each speaker who ran `/podcast join` gets their own track in `data/recordings/` for
podcast episodes ([docs/PODCAST.md](docs/PODCAST.md)). Saying "Nethys, …" during a
session asks the rules arbiter, and the ruling appears in Foundry.

Tests: `cd discord-bot && npm ci && npm test`, `cd stt-worker && uv run pytest`.

## Dashboard

With the `comic` profile, the server has a dashboard at `http://127.0.0.1:8771` (on the server
only; reach it from another machine with `ssh -L 8771:127.0.0.1:8771 server`). Three pages:

- **Server:** the stack's services with on/off switches. **Transcription** (Discord bot and
  GPU speech-to-text) can be switched off between sessions to free the GPU; **Rules arbiter**
  (backend and Cloudflare tunnel) too, which also switches transcription off since it needs the
  arbiter. **Comics** (this dashboard) is always on. A switched-off service stays off after a
  reboot; `docker compose up -d` (e.g. after an update) starts everything again. Switching
  transcription off during a recording ends the session (after a confirmation) and its
  transcript is still posted. Recent sessions are listed below.
- **Comics:** sessions to make comics from, each comic step by step, and the Limits.
- **Characters:** the cast as cards showing what each character still needs before comics
  draw them well, a page per character (profile, reference images, character sheets), and
  **Campaign & style** (setting, tone, art style, page look, style reference).

The dashboard reaches Docker only through `docker-proxy`, which lets it list this stack's
containers and start or stop exactly `backend`, `cloudflared`, `discord-bot` and `stt-worker`,
nothing else. It has no login, so it refuses POSTs that another website makes the browser send.

## Comic generation (experimental)

Turns a finished session's transcript into comic pages, step by step on the dashboard's
**Comics** page (`http://127.0.0.1:8771/comics`):

1. **Start comic** on a finished session. Gemini reads the transcript (it copes with
   speech-recognition errors and missing speaker names), writes down what happened in the game
   and suggests moments worth a page.
2. **Choose moments** (one page each), or describe your own. Gemini writes the script: a title,
   4-6 panels with a scene description and speech balloons per page.
3. **Edit the script** right there: titles, which characters are on each page, panel
   descriptions, balloons (`Speaker: text`, one per line).
4. **Draw**: `GEMINI_IMAGE_MODEL` draws each page in one go from the script and the reference
   images of the characters on it. The lettering is read back and compared with the script; a
   page whose lettering doesn't match is redrawn automatically (once by default). Any page can
   be redrawn with an extra instruction; earlier versions are kept.

Pages and the comic's state are in `data/comic/comics/<id>/`. Comics don't use the local GPU,
so they can be made while a session is recording.

**Comic bible.** The **Characters** page holds what carries over from one comic to the next
(`data/comic/bible.json`): on **Campaign & style**, the setting and tone, the art style and
things to avoid, and the language of the speech balloons; on each character's page, names and
aliases as they appear in transcripts, the height, a short appearance text and reference
images. Reference images should show only the character (drop them on the upload area); **Draft
from images** lets Gemini write the appearance text and the character's **must-haves**
(details the page drawer is told never to change). Each page has a checklist of what's still
missing; a character is *Ready* with a reference image, a height and a description.

**Consistency.** Three things keep characters and pages looking the same:
- **Character sheets:** **Draw sheet** draws the character in the comic's own style (front,
  three-quarter and side view, plain background) from the reference images. Approve one and
  every page uses it as the character's reference. Pages also get the character's first
  uploaded image ("On pages"; ★ makes an image the first; how many: Limits on the Comics
  page), the design as you gave it, since a drawn sheet can drift from it.
- **Who is where:** each panel tells the drawer which characters are in it and which
  reference images are theirs; anyone else is an unnamed extra who must not look like them.
- **Page look:** a fixed description of lettering, balloons, borders and page colour, added to
  every page.
- **Style reference:** **Use as style reference** under a drawn page you like sends that page
  with every new page as the style to match (Characters → Campaign & style: shown, and
  removable).
- **Exact specs:** each character's must-haves (exact about shape, colour and position) and a
  **never** list ("a tabard", "a cross on the helm") are given to the page drawer, and win over
  the panel descriptions.
- **Detail sheets:** **Draw detail sheet** (after a sheet is approved) draws large close-ups of
  helm, emblem, shield and weapon; once approved it's sent with the sheet on every page.
- **Continuity:** each page is drawn with the page before it, so looks and rendering carry over.
- **Look check:** the same call that reads the lettering back also checks each character
  against their must-haves and never list; a clear miss is redrawn like a lettering error
  (Limits: on by default).
- **Speaker tags:** the transcript preview shows the session's `/link` characters with ✓ when
  the bible knows them, ✗ when it doesn't (add the name or an alias).

Each drawing records what it was made with; when a comic has been drawn more than once,
**Compare drawing rounds** shows the rounds side by side, labelled with the references used.

**Limits.** Drawing is what can run away, so a comic's page images and their lettering checks
are charged to its token budget (Limits, default 80,000 per comic; a page is about 5,500,
so a 10-page comic with a few redraws fits).
Drawing stops with "Budget reached" before a page that would go over it. Reading the transcript
and writing the script are single calls per step; their tokens are shown but not budgeted (a
whole session is about 30,000).

To enable it on the server:

1. `cp comic/.env.example comic/.env` and set `GEMINI_API_KEY`.
2. Add `comic` to `COMPOSE_PROFILES` in `.env` (e.g. `COMPOSE_PROFILES=transcription,comic`;
   the setup script keeps it) and set `DOCKER_GID` to the group of `/var/run/docker.sock`
   (`stat -c %g /var/run/docker.sock`; the setup script fills it in; the Server page needs
   it), then `docker compose up -d --build`. The number differs per machine; if the Server page
   says it "can't reach Docker through docker-proxy", it doesn't match: fix it in `.env` and run
   `docker compose up -d docker-proxy`.

Known issues, measured costs and recommendations from testing: [docs/COMIC-KNOWN-ISSUES.md](docs/COMIC-KNOWN-ISSUES.md).

Tests: `cd comic && uv run pytest`.

## Development tools

Only needed to run the tests on a machine (the server itself needs just Docker):

| Tool | Ubuntu 22.04/24.04 | Arch Linux / CachyOS |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) (Python projects) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `sudo pacman -S uv` |
| Node.js 22+ (bot, Foundry module) | [NodeSource](https://github.com/nodesource/distributions): `curl -fsSL https://deb.nodesource.com/setup_22.x \| sudo -E bash - && sudo apt-get install -y nodejs` (Ubuntu's own `nodejs`: 18 on 24.04 still runs the tests, with a warning; 12 on 22.04 is too old) | `sudo pacman -S nodejs npm` (or `nodejs-lts-jod` for 22 LTS) |
| ffmpeg (optional, `scripts/podcast-mix.sh`) | `sudo apt-get install -y ffmpeg` | `sudo pacman -S ffmpeg` |

uv downloads a suitable Python by itself when the system one is too old (Ubuntu 22.04 has 3.10;
the projects need 3.11+). The test suites pass on Ubuntu 22.04, Ubuntu 24.04 and Arch.

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
