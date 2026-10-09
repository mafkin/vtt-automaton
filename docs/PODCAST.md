# Podcast episodes from sessions

Goal: every session can become a podcast episode of about **1.5 hours**, cut from the session's
own audio (empty content and silence removed), **mono**, published first from this server and
later on Spotify and Apple Podcasts. Everyone at the table is over 18 and opts in with
`/podcast join`.

```
Discord voice ──Opus packets──► discord-bot
                                 ├─► decode → utterances → STT worker   (transcription, unchanged)
                                 └─► one Ogg Opus track per consenting speaker   (no re-encoding)
                                          │  data/recordings/<session>/
                                          ▼
                 comic service (dashboard + worker): Podcast card            (phases 2-3)
                   cuts, levels, chapters, notes → render (ffmpeg) → MP3 + transcript
                                          │  data/podcast/public/
                                          ▼
                 backend serves /podcast/feed.xml through the Cloudflare tunnel   (phase 4)
```

## Phases
| Phase | Status | Result |
|---|---|---|
| 0 Timing and tracks | **built**, awaiting the sync test on the server | Aligned per-speaker tracks, opt-in |
| 1 Retention and disk | planned | Unpublished raw tracks deleted after N days; disk use on the dashboard |
| 2 Rough mix | planned | Podcast card: levels, per-track nudge, a loudness-normalised mono MP3 |
| 3 Editing to ~1.5 h | planned | Silence compressed, empty content cut, chapters, show notes, intro/outro |
| 4 Publishing | planned | RSS feed (iTunes + Podcasting 2.0 tags) served by the backend, then submitted to Spotify/Apple |

### Phase 0 (built)
- **Consent:** `/podcast join` stores the time and the consent text's version (`users.json`);
  `/podcast leave` withdraws it, stops the live track and deletes the speaker's raw tracks from
  `data/recordings`. People who haven't joined, and other bots, get no track at all.
  `/optout` (no transcription) also means no track.
- **Recording:** `/session start podcast:true` records one track per consenting speaker next to
  the transcription. The start message lists who is recorded and who isn't.
- **Files:** `data/recordings/<session>/tracks/<discord-user-id>.ogg` and `manifest.json`
  (session start/stop times; each track's speaker, character and consent time). Every track
  starts at the session start, so they mix without moving anything. About 30 MB per hour of
  actual speech per speaker; silence costs almost nothing.
- **Timing:** Discord sends nothing during pauses, so each packet is placed by its RTP timestamp
  (read by a small tap on `@discordjs/voice`'s packet parser), pinned to the wall clock on the
  speaker's least-delayed packet. Gaps are filled with silence to 2.5 ms. A jump (reconnect,
  rejoin) pins the speaker again. If a library upgrade removes the tapped method, a test fails
  and tracks fall back to arrival times.
- **What remains:** speakers differ by their network delay to Discord (typically 20–60 ms),
  which can't be measured from the server. Fine for conversation; phase 2 adds a per-track
  nudge (and can align automatically on a spoken marker at the start).

### The sync test (do this before phase 1)
Use a **spoken marker**, not a clap: the bot only hears what each Discord client sends, and
Discord's noise suppression (Krisp) removes claps, while voice activity detection may not even
open the microphone for one. A short, sharp word survives both.
1. Deploy, and everyone in the test runs `/podcast join`.
2. `/session start label:sync-test podcast:true` in the voice channel.
3. Count down and everyone says **"TAK!"** at once. Talk for a minute with pauses, say "TAK!"
   together again; one person leaves and rejoins the channel, then once more.
4. `/session stop`, then on the server:
   `scripts/podcast-mix.sh data/recordings/<session-id>` and listen to `mix.wav`. It uses
   ffmpeg if installed (`apt-get install ffmpeg` / `pacman -S ffmpeg`), otherwise a Docker image.
   The session id is the newest folder in `data/recordings/`.
5. Good: each "TAK!" sounds like one voice, or a tight cluster (under ~60 ms). Bad: they drift
   apart over the minute, or the rejoin breaks alignment. Report what you hear.

For exact numbers instead, everyone can set *User Settings → Voice & Video → Noise
Suppression: None* and a fixed input sensitivity for the test; then claps work too.

### Phase 3 notes (decided)
- Target length ~1.5 h from a 3-4 h session: silences longer than ~1.5 s are shortened to
  ~0.5 s (from the tracks, no AI needed), and "empty content" (breaks, setup, off-topic) is cut.
  Gemini proposes the cuts from the transcript; you accept or reject them on the dashboard.
- Mono MP3, loudness −16 LUFS, true peak −1 dB.
- Chapters from the events the comic pipeline already extracts; Finnish show notes drafted by
  Gemini; transcript (.vtt) from the Whisper segments, remapped to episode time after cuts.
