// Podcast tracks: one Ogg Opus file per consenting speaker, aligned to the session start, so
// the tracks can be mixed without moving anything.
//
// Discord sends packets only while someone talks, and nothing during short pauses. Each packet
// is placed at its RTP timestamp (exact, 48 kHz) relative to the speaker's first packet, which
// is pinned to the wall clock; gaps are filled with 20 ms silence packets. A packet that arrives
// earlier than its RTP position says moves the pin earlier (the first packet was delayed by
// network jitter), so the pin settles on the least-delayed packet. If the RTP position and the
// wall clock drift apart (a reconnect, a new SSRC after rejoining), the speaker is pinned again.
// Without RTP information, arrival times are used.
import {
  closeSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { join } from "node:path";
import { OggOpusWriter, SILENCE_PACKETS } from "./ogg.js";
import type { RtpInfo } from "./rtp.js";

const PER_MS = 48; // samples per millisecond
/** RTP position further than this from the wall clock: pin again. */
const REPIN_SAMPLES = 500 * PER_MS;
/** Without RTP: a packet arriving this much after the end of the last one starts after a gap. */
const JITTER_SAMPLES = 60 * PER_MS;

export class TrackTimer {
  private anchor: { rtp: number; sample: number } | null = null;

  /** Where (in samples from the session start) a packet belongs. */
  position(arrivalSample: number, nextSample: number, rtp?: RtpInfo): number {
    if (!rtp) {
      return arrivalSample - nextSample > JITTER_SAMPLES ? arrivalSample : nextSample;
    }
    if (this.anchor) {
      let delta = (rtp.timestamp - this.anchor.rtp) >>> 0;
      if (delta >= 2 ** 31) delta -= 2 ** 32; // an older packet (reordered)
      const position = this.anchor.sample + delta;
      if (position > arrivalSample) {
        // Arrived before its predicted time: the pin was set on a delayed packet.
        if (position - arrivalSample <= REPIN_SAMPLES) {
          this.anchor.sample -= position - arrivalSample;
          return arrivalSample;
        }
      } else if (arrivalSample - position <= REPIN_SAMPLES) {
        return position;
      }
    }
    this.anchor = { rtp: rtp.timestamp, sample: arrivalSample };
    return arrivalSample;
  }
}

export class Track {
  private readonly timer = new TrackTimer();
  private lastSequence: number | null = null;

  constructor(
    private readonly writer: OggOpusWriter,
    private readonly startedAtMs: number,
  ) {}

  packet(opus: Buffer, arrivalMs: number, rtp?: RtpInfo): void {
    if (rtp) {
      // A repeated or older sequence number (16 bits, wrapping) is a duplicate or late: skip.
      const step = this.lastSequence === null ? 1 : (rtp.sequence - this.lastSequence) & 0xffff;
      if (step === 0 || step >= 0x8000) return;
      this.lastSequence = rtp.sequence;
    }
    const arrival = Math.round((arrivalMs - this.startedAtMs) * PER_MS);
    const position = this.timer.position(arrival, this.writer.samples, rtp);
    // Earlier than what's written (a pin moved earlier): continue right after it.
    for (const [samples, silence] of SILENCE_PACKETS) {
      while (position - this.writer.samples >= samples) this.writer.add(silence);
    }
    this.writer.add(opus);
  }

  close(): void {
    this.writer.close();
  }
}

export interface TrackSpeaker {
  speaker: string;
  character?: string | undefined;
  consentedAt: number;
}

interface Manifest {
  sessionId: string;
  label: string | null;
  startedAt: number;
  stoppedAt: number | null;
  sampleRate: 48000;
  /** Every track starts at `startedAt`; silence fills the gaps. */
  tracks: Record<string, TrackSpeaker & { file: string }>;
}

/**
 * The podcast tracks of one session: data/recordings/<session>/tracks/<userId>.ogg plus a
 * manifest.json the dashboard reads. Only speakers `consent` approves get a track.
 */
export class SessionTracks {
  private readonly tracks = new Map<string, { track: Track; fd: number }>();
  private readonly dropped = new Set<string>();
  private readonly manifest: Manifest;
  readonly dir: string;

  constructor(
    root: string,
    sessionId: string,
    label: string | null,
    private readonly startedAtMs: number,
    private readonly consent: (userId: string) => TrackSpeaker | null,
    private readonly now: () => number = Date.now,
    private readonly log: (message: string) => void = (m) => console.error(m),
  ) {
    this.dir = join(root, sessionId);
    mkdirSync(join(this.dir, "tracks"), { recursive: true });
    this.manifest = {
      sessionId,
      label,
      startedAt: startedAtMs,
      stoppedAt: null,
      sampleRate: 48000,
      tracks: {},
    };
    this.save();
  }

  /** Speakers with a track (still counted after stop). */
  get speakers(): number {
    return Object.keys(this.manifest.tracks).length;
  }

  /** Never throws: a failing track (disk full) is closed and logged; transcription goes on. */
  packet(userId: string, opus: Buffer, rtp?: RtpInfo): void {
    if (this.dropped.has(userId)) return;
    try {
      this.write(userId, opus, rtp);
    } catch (error) {
      this.log(`Podcast track of ${userId} stopped: ${(error as Error).message}`);
      this.dropped.add(userId); // keep what was written; record nothing more
      try {
        this.closeTrack(userId);
      } catch {
        this.tracks.delete(userId);
      }
    }
  }

  private write(userId: string, opus: Buffer, rtp?: RtpInfo): void {
    let entry = this.tracks.get(userId);
    if (!entry) {
      const who = this.consent(userId);
      if (!who) return;
      const file = `tracks/${userId}.ogg`;
      const fd = openSync(join(this.dir, file), "w");
      const writer = new OggOpusWriter((page) => writeSync(fd, page));
      entry = { track: new Track(writer, this.startedAtMs), fd };
      this.tracks.set(userId, entry);
      this.manifest.tracks[userId] = { ...who, file };
      this.save();
    }
    entry.track.packet(opus, this.now(), rtp);
  }

  /** A speaker withdrew consent: stop and delete their track. */
  drop(userId: string): void {
    this.dropped.add(userId);
    this.closeTrack(userId);
    rmSync(join(this.dir, "tracks", `${userId}.ogg`), { force: true });
    delete this.manifest.tracks[userId];
    this.save();
  }

  stop(): void {
    for (const userId of [...this.tracks.keys()]) this.closeTrack(userId);
    this.manifest.stoppedAt = this.now();
    this.save();
  }

  private closeTrack(userId: string): void {
    const entry = this.tracks.get(userId);
    if (!entry) return;
    entry.track.close();
    closeSync(entry.fd);
    this.tracks.delete(userId);
  }

  private save(): void {
    const path = join(this.dir, "manifest.json");
    writeFileSync(`${path}.tmp`, JSON.stringify(this.manifest, null, 2));
    renameSync(`${path}.tmp`, path);
  }
}

/** Delete a speaker's tracks from every recorded session (consent withdrawn). */
export function deleteSpeakerTracks(root: string, userId: string): number {
  let deleted = 0;
  let sessions: string[] = [];
  try {
    sessions = readdirSync(root);
  } catch {
    return 0;
  }
  for (const session of sessions) {
    const file = join(root, session, "tracks", `${userId}.ogg`);
    try {
      rmSync(file);
      deleted++;
    } catch {
      continue; // no track of theirs in this session
    }
    const manifestPath = join(root, session, "manifest.json");
    try {
      const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Manifest;
      delete manifest.tracks[userId];
      writeFileSync(`${manifestPath}.tmp`, JSON.stringify(manifest, null, 2));
      renameSync(`${manifestPath}.tmp`, manifestPath);
    } catch {
      // a missing manifest: the deleted file was all there was
    }
  }
  return deleted;
}

