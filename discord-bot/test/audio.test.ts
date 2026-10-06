import assert from "node:assert/strict";
import { test } from "node:test";
import { UtteranceBuffer, encodeWav, pcmSeconds } from "../src/audio.js";

test("encodeWav writes a canonical header", () => {
  const pcm = Buffer.alloc(96_000); // 1 s of 48 kHz mono
  const wav = encodeWav(pcm);
  assert.equal(wav.length, 44 + pcm.length);
  assert.equal(wav.toString("ascii", 0, 4), "RIFF");
  assert.equal(wav.readUInt32LE(4), 36 + pcm.length);
  assert.equal(wav.toString("ascii", 8, 16), "WAVEfmt ");
  assert.equal(wav.readUInt16LE(22), 1); // mono
  assert.equal(wav.readUInt32LE(24), 48_000);
  assert.equal(wav.readUInt16LE(34), 16);
  assert.equal(wav.readUInt32LE(40), pcm.length);
  assert.equal(pcmSeconds(pcm), 1);
});

test("UtteranceBuffer cuts long speech into max-length pieces", () => {
  const buffer = new UtteranceBuffer(10);
  assert.deepEqual(buffer.push(Buffer.alloc(4)), []);
  const full = buffer.push(Buffer.alloc(23));
  assert.deepEqual(full.map((b) => b.length), [10, 10]);
  assert.equal(buffer.bytes, 7);
  assert.equal(buffer.finish()?.length, 7);
  assert.equal(buffer.finish(), null);
});
