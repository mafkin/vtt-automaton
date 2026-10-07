// Ogg Opus files (RFC 7845) from Discord's Opus packets, written as they arrive without
// re-encoding.

const SAMPLE_RATE = 48_000;
/** A 20 ms Opus packet of silence (the one Discord itself sends). */
export const SILENCE_PACKET = Buffer.from([0xf8, 0xff, 0xfe]);
export const SILENCE_SAMPLES = 960;
/** Silence packets of 20, 10, 5 and 2.5 ms (CELT, mono): gaps are filled to 2.5 ms. */
export const SILENCE_PACKETS: [samples: number, packet: Buffer][] = [
  [960, SILENCE_PACKET],
  [480, Buffer.from([0xf0, 0xff, 0xfe])],
  [240, Buffer.from([0xe8, 0xff, 0xfe])],
  [120, Buffer.from([0xe0, 0xff, 0xfe])],
];

/** Samples (at 48 kHz) in one Opus packet, from its TOC byte (RFC 6716 section 3.1). */
export function opusSamples(packet: Buffer): number {
  if (packet.length === 0) return 0;
  const toc = packet[0]!;
  const config = toc >> 3;
  let frame: number;
  if (config < 12) frame = [480, 960, 1920, 2880][config % 4]!; // SILK: 10/20/40/60 ms
  else if (config < 16) frame = [480, 960][config % 2]!; // hybrid: 10/20 ms
  else frame = [120, 240, 480, 960][config % 4]!; // CELT: 2.5/5/10/20 ms
  const code = toc & 0x03;
  const frames = code === 0 ? 1 : code === 3 ? (packet[1] ?? 0) & 0x3f : 2;
  return frame * frames;
}

// CRC-32 as Ogg uses it: polynomial 0x04c11db7, no reflection, initial value 0.
const CRC_TABLE = new Uint32Array(256).map((_, i) => {
  let r = i << 24;
  for (let bit = 0; bit < 8; bit++) r = r & 0x80000000 ? (r << 1) ^ 0x04c11db7 : r << 1;
  return r >>> 0;
});

export function oggCrc(data: Buffer): number {
  let crc = 0;
  for (const byte of data) crc = ((crc << 8) ^ CRC_TABLE[((crc >>> 24) ^ byte) & 0xff]!) >>> 0;
  return crc;
}

function page(
  packets: Buffer[],
  granule: bigint,
  serial: number,
  sequence: number,
  flags: number,
): Buffer {
  const lacing: number[] = [];
  for (const p of packets) {
    for (let n = p.length; ; n -= 255) {
      lacing.push(Math.min(n, 255));
      if (n < 255) break;
    }
  }
  const header = Buffer.alloc(27 + lacing.length);
  header.write("OggS", 0, "ascii");
  header[5] = flags;
  header.writeBigInt64LE(granule, 6);
  header.writeUInt32LE(serial, 14);
  header.writeUInt32LE(sequence, 18);
  header[26] = lacing.length;
  Buffer.from(lacing).copy(header, 27);
  const data = Buffer.concat([header, ...packets]);
  data.writeUInt32LE(oggCrc(data), 22);
  return data;
}

function opusHead(channels: number): Buffer {
  const head = Buffer.alloc(19);
  head.write("OpusHead", 0, "ascii");
  head[8] = 1; // version
  head[9] = channels;
  head.writeUInt16LE(0, 10); // pre-skip: the track starts with silence anyway
  head.writeUInt32LE(SAMPLE_RATE, 12);
  return head; // output gain 0, channel mapping family 0
}

function opusTags(vendor: string): Buffer {
  const name = Buffer.from(vendor, "utf8");
  const tags = Buffer.alloc(8 + 4 + name.length + 4);
  tags.write("OpusTags", 0, "ascii");
  tags.writeUInt32LE(name.length, 8);
  name.copy(tags, 12);
  return tags; // no user comments
}

/**
 * Writes one Ogg Opus stream. `write` receives finished pages (a file append in production).
 * Packets are collected into pages of about a second, so a crash loses at most that much.
 */
export class OggOpusWriter {
  /** Samples written so far: the position of the next packet. */
  samples = 0;
  private pending: Buffer[] = [];
  private pendingSegments = 0;
  private sequence = 0;
  private readonly serial = Math.floor(Math.random() * 0xffffffff);
  private closed = false;

  constructor(
    private readonly write: (page: Buffer) => void,
    channels = 2, // Discord sends stereo Opus; players downmix as needed
  ) {
    this.emit([opusHead(channels)], 0n, 0x02);
    this.emit([opusTags("vtt-automaton discord-bot")], 0n, 0);
  }

  add(packet: Buffer): void {
    if (this.closed) return;
    const segments = Math.floor(packet.length / 255) + 1;
    if (this.pendingSegments + segments > 255) this.flush();
    this.pending.push(packet);
    this.pendingSegments += segments;
    this.samples += opusSamples(packet);
    if (this.pending.length >= 50) this.flush();
  }

  flush(flags = 0): void {
    if (this.pending.length === 0) return;
    this.emit(this.pending, BigInt(this.samples), flags);
    this.pending = [];
    this.pendingSegments = 0;
  }

  /** Finish the stream (end-of-stream flag on the last page). */
  close(): void {
    if (this.closed) return;
    if (this.pending.length === 0) this.add(SILENCE_PACKET);
    this.flush(0x04);
    this.closed = true;
  }

  private emit(packets: Buffer[], granule: bigint, flags: number): void {
    this.write(page(packets, granule, this.serial, this.sequence++, flags));
  }
}
