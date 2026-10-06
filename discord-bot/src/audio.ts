// PCM helpers. The recorder decodes Discord's Opus to 48 kHz, 16-bit, mono, little-endian.

export const SAMPLE_RATE = 48_000;
export const BYTES_PER_SECOND_MONO = SAMPLE_RATE * 2;

/** Wrap mono s16le PCM in a canonical 44-byte WAV header. */
export function encodeWav(pcm: Buffer, sampleRate = SAMPLE_RATE): Buffer {
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16); // fmt chunk size
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28); // byte rate
  header.writeUInt16LE(2, 32); // block align
  header.writeUInt16LE(16, 34); // bits per sample
  header.write("data", 36, "ascii");
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

export function pcmSeconds(pcm: Buffer): number {
  return pcm.length / BYTES_PER_SECOND_MONO;
}

/**
 * Collects one speaker's mono PCM. Long monologues are cut into pieces of at most `maxBytes`
 * so a transcript line arrives while the speaker is still talking.
 */
export class UtteranceBuffer {
  private chunks: Buffer[] = [];
  private size = 0;

  constructor(private readonly maxBytes: number) {}

  get bytes(): number {
    return this.size;
  }

  /** Add PCM; returns any pieces that reached the maximum length. */
  push(pcm: Buffer): Buffer[] {
    const full: Buffer[] = [];
    let rest = pcm;
    while (this.size + rest.length >= this.maxBytes) {
      const take = this.maxBytes - this.size;
      this.chunks.push(rest.subarray(0, take));
      full.push(Buffer.concat(this.chunks));
      this.chunks = [];
      this.size = 0;
      rest = rest.subarray(take);
    }
    if (rest.length) {
      this.chunks.push(rest);
      this.size += rest.length;
    }
    return full;
  }

  /** Whatever is left when the speaker stops; null if nothing. */
  finish(): Buffer | null {
    if (!this.size) return null;
    const out = Buffer.concat(this.chunks);
    this.chunks = [];
    this.size = 0;
    return out;
  }
}
