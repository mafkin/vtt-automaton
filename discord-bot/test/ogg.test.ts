import assert from "node:assert/strict";
import { test } from "node:test";
import OpusScript from "opusscript";
import { OggOpusWriter, SILENCE_PACKET, SILENCE_PACKETS, oggCrc, opusSamples } from "../src/ogg.js";
import { readOgg } from "./oggread.js";

test("Opus packet durations come from the TOC byte", () => {
  assert.equal(opusSamples(SILENCE_PACKET), 960); // CELT 20 ms, one frame
  assert.equal(opusSamples(Buffer.from([0x78])), 960); // hybrid FB 20 ms
  assert.equal(opusSamples(Buffer.from([0x19])), 2 * 2880); // SILK 60 ms, code 1: two frames
  assert.equal(opusSamples(Buffer.from([0xfb, 0x03])), 3 * 960); // code 3: three frames
});

test("the CRC is CRC-32/POSIX without its final XOR, as Ogg uses it", () => {
  // CRC-32/POSIX (same polynomial, initial value 0) checks "123456789" as 0x765e7680.
  assert.equal(oggCrc(Buffer.from("123456789", "ascii")), (0x765e7680 ^ 0xffffffff) >>> 0);
});

test("a stream has the Opus headers, pages of about a second and an end-of-stream page", () => {
  const out: Buffer[] = [];
  const writer = new OggOpusWriter((page) => out.push(page));
  for (let i = 0; i < 120; i++) writer.add(SILENCE_PACKET);
  writer.add(Buffer.alloc(600, 1)); // a packet longer than 255 bytes spans lacing values
  writer.close();
  const pages = readOgg(Buffer.concat(out));
  assert.equal(pages[0]!.packets[0]!.toString("ascii", 0, 8), "OpusHead");
  assert.equal(pages[0]!.flags, 0x02);
  assert.equal(pages[1]!.packets[0]!.toString("ascii", 0, 8), "OpusTags");
  const audio = pages.slice(2);
  assert.deepEqual(audio.map((p) => p.packets.length), [50, 50, 21]);
  assert.equal(audio.at(-1)!.flags, 0x04);
  assert.equal(audio.at(-1)!.packets.at(-1)!.length, 600);
  assert.equal(audio[0]!.granule, 50n * 960n);
});

test("the short silence packets are valid Opus of the stated length", () => {
  const decoder = new OpusScript(48_000, 2, OpusScript.Application.AUDIO);
  for (const [samples, packet] of SILENCE_PACKETS) {
    assert.equal(opusSamples(packet), samples);
    const pcm = Buffer.from(decoder.decode(packet));
    assert.equal(pcm.length, samples * 4); // stereo s16le
    assert.ok(pcm.every((b) => b === 0), "silent");
  }
  decoder.delete();
});
