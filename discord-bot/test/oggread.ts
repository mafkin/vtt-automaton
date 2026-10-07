// Test helper: reads back what OggOpusWriter wrote.
import assert from "node:assert/strict";
import { oggCrc } from "../src/ogg.js";

/** Split an Ogg byte stream into pages and their packets; checks each page's CRC. */
export function readOgg(data: Buffer): { granule: bigint; flags: number; packets: Buffer[] }[] {
  const pages = [];
  let offset = 0;
  let carry = Buffer.alloc(0);
  while (offset < data.length) {
    assert.equal(data.toString("ascii", offset, offset + 4), "OggS");
    const segments = data[offset + 26]!;
    const lacing = [...data.subarray(offset + 27, offset + 27 + segments)];
    const size = 27 + segments + lacing.reduce((a, b) => a + b, 0);
    const raw = Buffer.from(data.subarray(offset, offset + size));
    const crc = raw.readUInt32LE(22);
    raw.writeUInt32LE(0, 22);
    assert.equal(oggCrc(raw), crc, "page CRC");
    const packets: Buffer[] = [];
    let at = offset + 27 + segments;
    for (const len of lacing) {
      carry = Buffer.concat([carry, data.subarray(at, at + len)]);
      at += len;
      if (len < 255) {
        packets.push(carry);
        carry = Buffer.alloc(0);
      }
    }
    pages.push({ granule: data.readBigInt64LE(offset + 6), flags: data[offset + 5]!, packets });
    offset += size;
  }
  return pages;
}
