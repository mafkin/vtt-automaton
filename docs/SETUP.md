# Server setup and end-to-end tests

This guide takes you from an empty server to a verified, working installation of everything built
so far:

- the **rules arbiter**: `/rule` and `/gmrule` in Foundry, plus rulings for spoken questions;
- the **Foundry module** on your Molten world;
- **Discord transcription**: the bot, the GPU speech-to-text worker, and transcripts.

Part A sets things up. Part B is a test plan in the order things depend on each other: if a test
fails, fix it before moving on. Part C covers troubleshooting and maintenance.

Throughout, **"the server"** means your home machine running Docker, and the public address is
`https://arbiter.ttrpg-arbiter.org`.

Everything runs in Docker, so the operating system only matters for installing Docker and the
GPU driver. Where the steps differ, this guide gives them for **Ubuntu** (22.04 or 24.04 LTS) and
for **Arch Linux / CachyOS** (the production server runs CachyOS). The commands are written for
bash: CachyOS's default shell is fish, so run `bash` first there.

---

## Part A: Setup

### A1. What you need

| Item | Notes |
|---|---|
| Server | Linux: Ubuntu 22.04/24.04 LTS or Arch Linux/CachyOS (others with Docker should work), or Windows with WSL2. 4+ CPU cores, 16 GB RAM, ~15 GB free disk |
| NVIDIA GPU | Only for transcription. 6 GB+ VRAM recommended for `large-v3`; with less, see [C3](#c3-tuning) |
| Cloudflare | `ttrpg-arbiter.org` on Cloudflare, a tunnel with public hostname `arbiter.ttrpg-arbiter.org` → `HTTP` `backend:8765` (already done) |
| Gemini API key | [aistudio.google.com](https://aistudio.google.com) → *Get API key* |
| Foundry on Molten | Foundry **v13 or later**, a **pf2e** world, and admin access to install modules (the GM does this) |
| Discord | A server where you can add bots, and the GM's and players' accounts in it |
| GitHub | Push access to `mafkin/vtt-automaton` (for publishing the module) |

Whoever runs the setup needs these 3–5 secrets at hand: the Cloudflare tunnel token, the Gemini
API key, the Molten world address, and for transcription the Discord bot token and server ID.
The setup script generates the rest.

### A2. Install Docker on the server

You need the Docker engine, the Compose plugin (`docker compose`) and buildx.

**Ubuntu**: Docker's install script (Docker's own packages, with Compose and buildx):

```bash
curl -fsSL https://get.docker.com | sh
```

or Ubuntu's packages, which are recent enough on both 22.04 and 24.04:

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 docker-buildx
```

Don't use the Docker *snap* that the Ubuntu Server installer offers: it confines file access and
sets the GPU up differently from this guide. Check with `snap list docker`; if it's listed, run
`sudo snap remove docker` before installing.

**Arch Linux / CachyOS**: `docker` alone has no `docker compose`, and Docker's install script
doesn't support Arch:

```bash
sudo pacman -S --needed docker docker-compose docker-buildx
sudo systemctl enable --now docker.service    # Arch doesn't start it by itself
```

**Then, on both:**

```bash
sudo usermod -aG docker "$USER"     # then log out and back in
docker run --rm hello-world         # should print "Hello from Docker!"
docker compose version              # should print v2 or later
```

No ports need opening in a firewall (ufw on Ubuntu, or any other): the Cloudflare tunnel connects
outward, and the comic dashboard listens on 127.0.0.1 only.

On Windows, install Docker Desktop with the WSL2 backend instead, and run every command in this
guide inside your WSL Ubuntu shell.

### A3. GPU support for Docker (transcription only)

1. Install the NVIDIA driver for your GPU, then reboot.

   - **Ubuntu:** `sudo ubuntu-drivers install` picks the recommended driver
     (`ubuntu-drivers devices` lists it first).
   - **CachyOS:** the installer already set the driver up (its hardware detection, chwd).
   - **Arch Linux:** `sudo pacman -S --needed nvidia-open nvidia-utils` for the `linux` and
     `linux-lts` kernels (`nvidia-open-dkms` for others). Cards older than the GTX 16xx/RTX 20xx
     series need a legacy driver: see the Arch wiki's NVIDIA page.

   Check it: `nvidia-smi` should list the card.
2. Install the NVIDIA Container Toolkit and register it with Docker.

   **Ubuntu** (from NVIDIA's repository; Ubuntu doesn't package it):

   ```bash
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
     | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
     | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
     | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   ```

   **Arch Linux / CachyOS** (in the official repositories):

   ```bash
   sudo pacman -S --needed nvidia-container-toolkit
   ```

   **Then, on both:**

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

3. Check that containers can see the GPU:

   ```bash
   docker run --rm --gpus all ubuntu nvidia-smi
   ```

   It should print the same table as on the host. Docker Desktop on Windows supports GPUs through
   WSL2 without the toolkit step.

The official instructions are at
[docs.nvidia.com/datacenter/cloud-native/container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

### A4. Create the Discord bot (transcription only)

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) →
   **New Application**, and name it e.g. *Nethys*.
2. **Bot** → **Reset Token** → copy the token. You only see it once.
   No privileged intents are needed; leave them all off.
3. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: *View Channels*, *Send Messages*, *Attach Files*, *Connect*
4. Open the generated URL and add the bot to your Discord server.
5. Get the server ID: Discord → *User Settings → Advanced → Developer Mode* on, then right-click
   the server icon → **Copy Server ID**.

### A5. Run the setup script

On the server:

```bash
git clone https://github.com/mafkin/vtt-automaton.git
cd vtt-automaton
./scripts/setup-server.sh
```

It asks, in order:

| Prompt | Where to get it |
|---|---|
| Cloudflare tunnel token | Zero Trust → Networks → Tunnels → your tunnel → the long token in the install command |
| Gemini API key | A1 |
| Foundry (Molten) address | The exact address in the GM's browser bar when in the world, e.g. `https://ourworld.moltenhosting.com` (no path) |
| Set up Discord transcription? | `y` if you did A3 and A4 |
| Discord bot token | A4 step 2 |
| Discord server ID | A4 step 5 |

The script then:

1. writes the env files (`.env`, `backend/.env`, `discord-bot/.env`, `stt-worker/.env`, all with
   permissions 600) and generates the client tokens;
2. builds and starts the containers;
3. waits for the Archives of Nethys rules import (about a minute);
4. checks the public address and makes one test ruling;
5. waits for the Discord bot to log in;
6. prints the **backend URL and the GM's client token**.

**Save that token** and send it to the GM privately (a Discord DM). It's also in
`backend/.env` as the first value of `VTT_CLIENT_TOKENS`.

Running the script again is safe: press Enter at each question to keep the current value.

The script is the same on Ubuntu and Arch/CachyOS. It also records the group that owns the
Docker socket (`DOCKER_GID` in `.env`). That number differs from machine to machine (e.g. 996 on
an Ubuntu 24.04 machine, 969 on a fresh Arch install), and the comic dashboard's container list needs
it, so re-run the script after moving to another machine.

> On the first start, the speech-to-text worker downloads the Whisper `large-v3` model (~3 GB).
> Follow it with `docker compose logs -f stt-worker` until you see the model load (A7).

### A6. Publish and install the Foundry module

**Publish** (once per version, from any clone with push access):

```bash
git tag module-v0.1.0
git push origin module-v0.1.0
```

The `foundry-module` GitHub Action builds the release and attaches `module.json` and
`pf2e-ai-arbiter.zip`. Check that it appears under *Releases* on GitHub before installing.

**Install** (the GM, on Molten):

1. Return to Foundry's *Setup* screen → **Add-on Modules** → **Install Module**.
2. Paste the manifest URL and click **Install**:
   `https://github.com/mafkin/vtt-automaton/releases/latest/download/module.json`
3. Launch the pf2e world → *Game Settings* → **Manage Modules** → enable **PF2e AI Arbiter** →
   save.
4. *Game Settings* → **Configure Settings** → PF2e AI Arbiter:
   - **Taustapalvelun osoite** (backend URL): `https://arbiter.ttrpg-arbiter.org` (the default)
   - **Asiakastunniste (vain GM)** (client token): the token from A5. This is stored only in that
     browser, so every GM or assistant GM who might run the session enters it in their own browser.
   - Optional: speaker name (default *Nethys Arbiter*), and *Vastaa puhuttuihin kysymyksiin*
     (answer spoken questions, on by default).

Players don't configure anything.

### A7. Check that everything is running

```bash
docker compose ps
```

You should see `backend` and `cloudflared` running, and with transcription also `discord-bot`
and `stt-worker`. Useful log commands:

```bash
docker compose logs backend      | grep -E "Rules DB|Uvicorn running"
docker compose logs stt-worker   | grep -E "Loading Whisper model|Application startup complete"
docker compose logs discord-bot  | grep -E "Logged in as|DAVE|opusscript"
```

The bot prints a dependency report on start. It must list `@snazzah/davey` under *DAVE
Libraries* and `opusscript` under *Opus Libraries*.

---

## Part B: End-to-end tests

Run these in order. Each test lists what to do, what you should see, and where to look if it
doesn't work (details in [Part C](#part-c-troubleshooting-and-maintenance)).

Set these once in your shell on the server:

```bash
URL=https://arbiter.ttrpg-arbiter.org
TOKEN=$(sed -n 's/^VTT_CLIENT_TOKENS=//p' backend/.env | cut -d, -f1)
```

### Backend

**B1. Local health**

```bash
curl -s http://127.0.0.1:8765/healthz
```

Expect `{"status":"ok"}`. If not: `docker compose logs backend`.

**B2. Public health through Cloudflare**

```bash
curl -s $URL/healthz
```

Expect `{"status":"ok"}`. Also open the URL in a browser on another network (your phone on mobile
data). If it fails: `docker compose logs cloudflared`, and check the tunnel's public hostname.

**B3. Rules database**

```bash
docker compose logs backend | grep "Rules DB updated"
ls -lh data/pf2e_remaster.db
```

Expect roughly *29,000 entries (1,400 legacy)* and a ~65 MB file.

**B4. A ruling over HTTP**

```bash
curl -s $URL/api/v1/rulings -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "Voiko Trip-toiminnon tehdä, jos molemmissa käsissä on jotain?"}' | python3 -m json.tool
```

Expect:
- `raw` lists **Trip** (Player Core) with its full rules text;
- `interpretation` is a Finnish ruling that mentions the free-hand requirement;
- `"raw_only": false`. `true` means Gemini didn't answer: see C1.

Typical time: 3–8 seconds.

**B5. Remaster-only rules**

```bash
curl -s $URL/api/v1/rulings -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "Mitä Attack of Opportunity tekee?"}' | python3 -c \
  "import json,sys; r=json.load(sys.stdin)['ruling']; print([x['name'] for x in r['raw']]); print(r['interpretation'])"
```

Expect **Reactive Strike** cited, never *Attack of Opportunity*.

**B6. Authentication is enforced**

```bash
curl -s -o /dev/null -w "%{http_code}\n" $URL/api/v1/rulings -d '{}'   # expect 401
```

### Foundry

**B7. GM connects**

The GM loads the world. Within a few seconds a notification says **"Tuomari yhdistetty
taustapalveluun."** If there's no notification or an error appears, see C2.

**B8. `/rule` from the GM**

In Foundry chat: `/rule voiko Trip-toiminnon tehdä ilman vapaata kättä?`

Expect a card from *Nethys Arbiter*:
1. it shows "Selataan arkistoja…" first;
2. within a few seconds that's replaced by **Säännöt (RAW)** with the full Trip text;
3. then **Tulkinta** with the ruling and a confidence label.

The command text itself is not posted to chat.

**B9. `/rule` from a player**

A player (with the GM online) types `/rule mitä prone tekee?`. The player briefly sees "Kysymys
lähetetty tuomarille.", and the same kind of card appears for everyone, headed "Aino kysyy: …".
This confirms the player → GM browser → backend relay.

**B10. `/gmrule` stays secret**

A player types `/gmrule huomaako vartija minut, jos olen hidden?`. The card is marked **Vain GM**
and is visible to the GM only. Check from the player's screen that it doesn't appear there.

**B11. No GM online**

With no GM logged in, a player types `/rule mitä Stride tekee?`. Expect the warning "GM ei ole
paikalla, joten tuomari ei voi vastata juuri nyt." and no card.

**B12. Reconnect**

On the server: `docker compose restart backend`. The GM sees "Tuomarin yhteys katkesi;
yhdistetään uudelleen…" and then "Tuomari yhdistetty taustapalveluun." again within about 30
seconds. `/rule` works again afterwards.

**B13. Spoken question without Discord**

This tests the voice-ruling path on its own. With the GM in the world:

```bash
curl -s $URL/api/v1/transcript/segments -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"speaker": "Testi", "text": "Nethys, saako reaktion, kun vihollinen liikkuu ohi?"}'
```

Expect `{"ruling_id":"voice-…","stored":false}`. In Foundry, a card headed **"Kuultu: Testi:
saako reaktion, kun vihollinen liikkuu ohi?"** appears and fills in with Reactive Strike.
Sending the same request again within 30 seconds should *not* create a second card.

### Discord transcription

**B14. Bot is online with commands**

The bot shows as online in your server. Typing `/` in a text channel lists `/session`, `/link`,
`/optout` and `/optin` from the bot. If the commands are missing, see C4.

**B15. Personal settings**

Each player types `/link character:<hahmon nimi>`, e.g. `/link character:Valeros`. Expect a
private reply: "Hahmosi on nyt **Valeros**…".

**B16. Start a session (the key live test)**

1. Join a voice channel with at least one other person.
2. In a text channel, type `/session start label:Testisessio`.
3. The bot joins the voice channel and posts the consent notice: "🔴 **Nauhoitus alkoi**…".
4. Both people talk normally for a minute, one sentence at a time.
5. On the server:

   ```bash
   docker compose logs --since 2m stt-worker | grep -c "POST /v1/utterances"   # > 0
   docker compose logs --since 2m discord-bot                                  # no errors
   ```

6. `/session status` shows the speakers, the utterances sent, and *rows transcribed* > 0.
7. Check transcription speed. Each clip logs one line:

   ```bash
   docker compose logs --since 5m stt-worker | grep "Transcribed"
   # Transcribed 4.2 s from Aino in 0.61 s (6.9x real time), waited 0.0 s, 0 queued: 57 chars
   ```

   The rolling figures for the last 100 clips:

   ```bash
   docker compose exec stt-worker python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8770/healthz').read().decode())"
   ```

   What to look for: `speed_x_realtime` should stay well above 1 (several people talking means
   several times real-time is needed), and `waited` / `max_wait_seconds` should stay near 0. A
   wait of several seconds means spoken questions get answered late; see C3.

This is the first real test of receiving encrypted (DAVE) Discord audio. If utterances stay at
0, see C4.

**B17. Spoken rules question, live**

With the GM in Foundry and the session running, someone says clearly: **"Nethys, mitä Trip
tekee?"** Expect a "Kuultu: …" card in Foundry about 3–6 seconds after the sentence ends, then the
ruling. Also try:
- **A pause:** "Nethys…" (pause) "voinko tehdä Shield Blockin?". The question in the second
  sentence is answered.
- **Secret:** "Nethys, salaa: näkeekö vartija minut?". Only the GM sees the card.

If nothing appears, check the transcript (B19) for how "Nethys" was spelled and see C5.

Measure the delay, which is the number to report:

```bash
docker compose logs --since 10m backend | grep -E "Spoken question|Ruling voice-"
# Spoken question voice-1a2b3c from Aino: 'mitä Trip tekee?'
# Ruling voice-1a2b3c took 1.8 s: 2 rules cited, confidence high
# Spoken question voice-1a2b3c answered 4.6 s after the speaker finished
```

The *answered … after the speaker finished* figure covers everything: Discord's pause
detection, the queue, Whisper, and the ruling. Subtract the *Ruling … took* figure to see how
much was speech-to-text.

**B18. Opt-out**

One person types `/optout` and says a sentence, then `/optin` and says another. Only the second
sentence should appear in the transcript (B19).

**B19. Stop and transcript**

Type `/session stop`. Expect:
1. the summary "⏹️ **Nauhoitus päättyi.** Kesto …, N puhujaa, M puheenvuoroa";
2. about 20 seconds later, a file `litterointi-<id>.txt` in the same channel, with lines like
   `[0:00:42] Aino (Valeros): …`.

`/session transcript` posts the latest transcript again. The bot leaves the voice channel.

**B20. Auto-stop (optional)**

Start a session, then everyone leaves the voice channel. After 5 minutes the bot stops by itself
and posts "Puhekanava on ollut tyhjä, joten lopetin nauhoituksen."

### Quality baseline

**B21. Finnish accuracy sample**

Do this once at a real game session, because it sets the baseline for tuning.

1. Record a session normally. Pick a 10–15 minute stretch with typical play: rules talk,
   character names, English game terms.
2. Copy that stretch of the transcript to `hyp.txt`. Make a copy, `ref.txt`, and correct every
   error by hand: what was actually said, keeping the same line breaks.
3. Compute the word error rate (WER). The script strips the `[0:00:42] Name (Character):`
   prefixes so that only the spoken words are compared:

   On Ubuntu, `python3 -m venv` first needs `sudo apt-get install -y python3-venv` (Arch
   includes it).

   ```bash
   python3 -m venv /tmp/wer && /tmp/wer/bin/pip install -q jiwer
   /tmp/wer/bin/python - <<'PY'
   import re, jiwer
   strip = lambda path: "\n".join(re.sub(r"^\[[0-9:]+\] [^:]+: ", "", line) for line in open(path, encoding="utf-8"))
   print(round(jiwer.wer(strip("ref.txt"), strip("hyp.txt")), 3))
   PY
   ```

   Lower is better: 0.15 means 15% of words were wrong. Note the number, and which kinds of
   errors dominate (names, game terms, crosstalk).
4. Share `ref.txt`, `hyp.txt` and the number. They decide what to tune: the word list, the model,
   or the compute type (C3).

The transcript has no audio behind it, so do step 2 the same day while you still remember what
was said, or have each player check their own lines.

### Test checklist

| # | Test | ✔ |
|---|---|---|
| B1–B3 | Backend up locally and publicly, rules DB imported | |
| B4–B6 | Ruling over HTTP, Remaster only, auth enforced | |
| B7–B12 | Foundry: connect, `/rule` from GM and player, `/gmrule` secret, no-GM warning, reconnect | |
| B13 | Voice ruling via the test endpoint | |
| B14–B16 | Discord bot online, `/link`, live recording | |
| B17 | Live "Nethys, …" question answered in Foundry | |
| B18–B20 | Opt-out, stop and transcript, auto-stop | |
| B21 | Finnish accuracy baseline recorded | |

---

## Part C: Troubleshooting and maintenance

### C1. Backend and rulings

| Symptom | Cause and fix |
|---|---|
| Cards show only the rules text ("vain RAW") | Gemini didn't answer. `docker compose logs backend \| grep -A5 "Ruling generation failed"`. Usually a wrong `VTT_GEMINI_API_KEY` or an unavailable `VTT_GEMINI_MODEL`: fix it in `backend/.env`, then `docker compose up -d backend`. |
| "Sopivia sääntöjä ei löytynyt" | No matching rules. Try the English name of the action, condition or spell. If it happens for obvious terms, check B3. |
| A rule you expected is missing | It may be legacy only (pre-Remaster books). That's intentional. |
| Rulings are slow (> 15 s) | Check the Gemini status page. Try a faster model in `VTT_GEMINI_MODEL`. |
| `/healthz` works locally but not publicly | `docker compose logs cloudflared`. The tunnel token is in `.env`. The public hostname must point to `http://backend:8765`. |
| Setup script: "Test ruling failed (HTTP 401)" although the public address answers | Another server answers the hostname, e.g. the old one while you set up a new machine. Its tunnel still serves `arbiter.ttrpg-arbiter.org`, and it doesn't know the new client token. Stop the old stack, or move the tunnel token over, then re-run the script. |

### C2. Foundry module

| Symptom | Cause and fix |
|---|---|
| "Tuomari: aseta taustapalvelun osoite ja asiakastunniste…" | The client token isn't set **in this GM's browser** (A6 step 4). |
| "…taustapalvelu hylkäsi asiakastunnisteen" | Wrong token. Compare it with the first value of `VTT_CLIENT_TOKENS` in `backend/.env`. |
| No notification at all, nothing happens | Usually the origin check. `docker compose logs backend \| grep "Rejected Foundry socket"` shows the address the browser used. Put exactly that address in `VTT_CORS_ORIGINS` (re-run the setup script, or edit `backend/.env` and run `docker compose up -d backend`). Also check the browser console (F12) for errors from `pf2e-ai-arbiter`. |
| "Taustapalvelu ei vastannut ajoissa." | The ruling took over 90 s. Check the backend logs for Gemini errors. |
| Player sees "Kysymys lähetetty tuomarille." but no card appears | The GM's browser isn't running the arbiter: the GM should have seen the "yhdistetty" notification (B7). Reload the GM's browser and check that the module is enabled. |
| Spoken questions don't show in Foundry | *Vastaa puhuttuihin kysymyksiin* is turned off, or B13 fails (then it's the backend, not Discord). |

### C3. Speech-to-text worker

| Symptom | Cause and fix |
|---|---|
| `stt-worker` won't start: *could not select device driver "nvidia"* | The NVIDIA Container Toolkit is missing or Docker wasn't restarted (A3). |
| `nvidia-smi` says *Driver/library version mismatch*, or the GPU vanished after a system update | The driver was updated but the old kernel module is still loaded (common after `pacman -Syu` on Arch/CachyOS, or a driver update on Ubuntu). Reboot, then `docker compose up -d`. |
| Out of GPU memory | Set `STT_COMPUTE_TYPE=int8_float16` in `stt-worker/.env` (less VRAM, nearly the same quality), or `STT_MODEL=medium` (noticeably worse Finnish). Then `docker compose up -d stt-worker`. |
| Transcription falls behind (log says *"Transcription is falling behind"*, or `waited` grows) | First check `docker compose logs stt-worker \| grep "Loading Whisper model"`: if it says `cpu`, the GPU isn't used (A3). Otherwise, in `stt-worker/.env`, try in this order: `STT_BEAM_SIZE=1` (roughly twice as fast, slightly less accurate), then `STT_COMPUTE_TYPE=int8_float16`. Apply with `docker compose up -d stt-worker` and compare the *x real time* figures and the B21 accuracy before and after. |
| Names or game terms are misspelled | Add them to `STT_PROMPT_TERMS` in `stt-worker/.env` (comma separated: characters, NPCs, places), then `docker compose up -d stt-worker`. |
| Lines like "Kiitos katsomisesta" in transcripts | A hallucination that slipped through the filter. Note the exact text; the filter list in `stt-worker/app/transcriber.py` can be extended. |

### C4. Discord bot

| Symptom | Cause and fix |
|---|---|
| Commands don't appear | Wrong `DISCORD_GUILD_ID`, or the bot was invited without the `applications.commands` scope (A4 step 3). Check `docker compose logs discord-bot \| grep "Logged in as"`. |
| "En päässyt puhekanavalle" | The bot lacks the *Connect* permission on that voice channel. |
| Bot joins but utterances stay at 0 | Check `docker compose logs discord-bot` for `Failed to decrypt` (a DAVE problem) or `Audio stream … failed`. Try updating: `git pull && docker compose up -d --build discord-bot`. Report the log lines if it persists: this is the part that couldn't be tested before deployment. |
| "Could not send audio … STT worker" in bot logs | The worker isn't running or is still loading the model: `docker compose logs stt-worker`. |
| No transcript file after stop | Check that the bot has *Attach Files* in that text channel, and `docker compose logs discord-bot \| grep "Could not post transcript"`. |

### C5. Voice rulings

| Symptom | Cause and fix |
|---|---|
| "Nethys, …" is in the transcript but no card | Check how Whisper spelled it. If it's e.g. "Netis", add it: `VTT_WAKE_WORDS=Nethys,Netis` in `backend/.env`, then `docker compose up -d backend`. Also check that the GM is connected (B7). |
| The question is cut short or wrong | Speak the question in one breath after "Nethys". A pause longer than 0.8 s splits it, but the same speaker's next sentence (within 8 s) is used if the first had no question. |
| The same question is answered twice | Two people asked it more than 30 s apart. This is intentional. |

### C6. Maintenance

**Update to the latest version**

```bash
cd vtt-automaton
git pull
docker compose up -d --build
```

**Update the operating system**: `sudo apt-get update && sudo apt-get upgrade` on Ubuntu,
`sudo pacman -Syu` on Arch/CachyOS. If the kernel or the NVIDIA driver was updated, reboot
before the next session. The containers come back by themselves (`restart: unless-stopped`).

For a new module version, tag it (`module-v0.1.1`). Molten offers the update on the *Setup*
screen.

**Restart, stop, logs**

```bash
docker compose restart            # all services
docker compose down               # stop everything (data is kept)
docker compose logs -f --tail 50  # follow all logs
```

**What to back up**

| Path | Contents |
|---|---|
| `.env`, `backend/.env`, `discord-bot/.env`, `stt-worker/.env` | All configuration and secrets |
| `data/sessions.db` | Session transcripts (the table's own data) |
| `data/discord-bot/users.json` | Character links and opt-outs |

`data/pf2e_remaster.db` doesn't need a backup; it's rebuilt from Archives of Nethys.

**Rotate the GM's token** (e.g. if it leaked): in `backend/.env`, delete the
`VTT_CLIENT_TOKENS` line, run `./scripts/setup-server.sh` (Enter at each question keeps the rest),
and give the GM the new token.

**Turn transcription off or on**: re-run the setup script and answer the transcription question,
or set `COMPOSE_PROFILES=` (empty) or `COMPOSE_PROFILES=transcription` in `.env`, then
`docker compose up -d --remove-orphans`.
