// Records one voice channel: every speaker separately, cut into utterances at pauses, each sent
// to the STT worker as a WAV clip.
import type { EventEmitter } from "node:events";
import type { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { EndBehaviorType } from "@discordjs/voice";
import prism from "prism-media";
import { BYTES_PER_SECOND_MONO, UtteranceBuffer, encodeWav, pcmSeconds } from "./audio.js";
import type { UtteranceMeta } from "./clients.js";

/** The parts of @discordjs/voice's VoiceReceiver the recorder uses (a fake in tests). */
export interface ReceiverLike {
  speaking: EventEmitter;
  subscribe(
    userId: string,
    options: { end: { behavior: EndBehaviorType; duration: number } },
  ): Readable;
}

export interface SpeakerInfo {
  speaker: string;
  character?: string | undefined;
}

export interface RecorderOptions {
  receiver: ReceiverLike;
  sessionId: string;
  stt: { sendUtterance(meta: UtteranceMeta, wav: Buffer): Promise<void> };
  speakerInfo: (userId: string) => Promise<SpeakerInfo>;
  isOptedOut: (userId: string) => boolean;
  ignoreUserIds?: Set<string>;
  /** Opus → 48 kHz mono s16le PCM. */
  decoder?: () => Transform;
  /** Pause that ends an utterance. */
  silenceMs?: number;
  /** Longer speech is sent in pieces of this length. */
  maxSeconds?: number;
  /** Shorter clips (coughs, clicks) are not sent. */
  minSeconds?: number;
  now?: () => number;
  log?: (message: string) => void;
  /** Every received Opus packet, before decoding (podcast tracks). */
  onPacket?: (userId: string, opus: Buffer) => void;
}

// Discord sends stereo Opus; libopus can decode it straight to mono, which is cheaper than
// decoding stereo and mixing it down in JavaScript.
const opusDecoder = (): Transform =>
  new prism.opus.Decoder({ rate: 48_000, channels: 1, frameSize: 960 }) as unknown as Transform;

export class Recorder {
  utterances = 0;
  readonly speakers = new Set<string>();
  private readonly streams = new Map<string, Readable>();
  private readonly inflight = new Set<Promise<void>>();
  private stopped = false;
  private readonly o: Required<Omit<RecorderOptions, "ignoreUserIds" | "onPacket">> & {
    ignoreUserIds: Set<string>;
    onPacket?: (userId: string, opus: Buffer) => void;
  };

  constructor(options: RecorderOptions) {
    this.o = {
      ...options,
      decoder: options.decoder ?? opusDecoder,
      silenceMs: options.silenceMs ?? 800,
      maxSeconds: options.maxSeconds ?? 30,
      minSeconds: options.minSeconds ?? 0.4,
      now: options.now ?? (() => Date.now() / 1000),
      log: options.log ?? ((m) => console.log(m)),
      ignoreUserIds: options.ignoreUserIds ?? new Set(),
    };
  }

  start(): void {
    this.o.receiver.speaking.on("start", this.onSpeakingStart);
  }

  /** Stop listening, finish the utterances in progress and wait (up to 10 s) for uploads. */
  async stop(): Promise<void> {
    this.stopped = true;
    this.o.receiver.speaking.off("start", this.onSpeakingStart);
    for (const stream of this.streams.values()) stream.push(null);
    // Let the ended streams flush their last utterance into `inflight` first.
    await new Promise((resolve) => setImmediate(resolve));
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise((resolve) => (timer = setTimeout(resolve, 10_000)));
    await Promise.race([Promise.allSettled([...this.inflight]), timeout]);
    clearTimeout(timer);
  }

  private readonly onSpeakingStart = (userId: string): void => {
    if (this.stopped || this.streams.has(userId)) return;
    if (this.o.ignoreUserIds.has(userId) || this.o.isOptedOut(userId)) return;

    const opus = this.o.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: this.o.silenceMs },
    });
    this.streams.set(userId, opus);
    const buffer = new UtteranceBuffer(Math.round(this.o.maxSeconds * BYTES_PER_SECOND_MONO));
    let tStart = this.o.now();
    const send = (pcm: Buffer) => {
      this.flush(userId, pcm, tStart);
      tStart += pcmSeconds(pcm);
    };

    const onPacket = this.o.onPacket;
    if (onPacket) opus.on("data", (packet: Buffer) => onPacket(userId, packet));
    const decoder = this.o.decoder();
    decoder.on("data", (pcm: Buffer) => {
      for (const piece of buffer.push(pcm)) send(piece);
    });
    pipeline(opus, decoder)
      .catch((error: Error) => this.o.log(`Audio stream from ${userId} failed: ${error.message}`))
      .finally(() => {
        const rest = buffer.finish();
        if (rest) send(rest);
        this.streams.delete(userId);
      });
  };

  private flush(userId: string, pcm: Buffer, tStart: number): void {
    // Opting out mid-sentence still drops the rest of that sentence.
    if (pcmSeconds(pcm) < this.o.minSeconds || this.o.isOptedOut(userId)) return;
    const upload = this.o
      .speakerInfo(userId)
      .then(({ speaker, character }) => {
        this.speakers.add(userId);
        this.utterances++;
        return this.o.stt.sendUtterance(
          { sessionId: this.o.sessionId, speaker, speakerId: userId, character, tStart },
          encodeWav(pcm),
        );
      })
      .catch((error: Error) => this.o.log(`Could not send audio from ${userId}: ${error.message}`))
      .finally(() => this.inflight.delete(upload));
    this.inflight.add(upload);
  }
}
