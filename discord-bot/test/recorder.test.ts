import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Readable } from "node:stream";
import { test } from "node:test";
import type { UtteranceMeta } from "../src/clients.js";
import { Recorder } from "../src/recorder.js";

// The decoder outputs 48 kHz mono s16le: 96 000 bytes per second.
const SECOND = 96_000;

class FakeReceiver {
  speaking = new EventEmitter();
  streams = new Map<string, Readable>();
  subscribe(userId: string): Readable {
    const stream = new Readable({ read() {} });
    this.streams.set(userId, stream);
    return stream;
  }
}

function setup(options: Partial<ConstructorParameters<typeof Recorder>[0]> = {}) {
  const receiver = new FakeReceiver();
  const sent: { meta: UtteranceMeta; wav: Buffer }[] = [];
  let clock = 1000;
  const optedOut = new Set<string>();
  const recorder = new Recorder({
    receiver,
    sessionId: "s1",
    stt: { sendUtterance: async (meta, wav) => void sent.push({ meta, wav }) },
    speakerInfo: async (userId) => ({ speaker: `user-${userId}`, character: userId === "1" ? "Valeros" : undefined }),
    isOptedOut: (userId) => optedOut.has(userId),
    ignoreUserIds: new Set(["bot"]),
    decoder: () => new PassThrough(), // the fake "opus" is already mono PCM
    now: () => clock,
    log: () => {},
    ...options,
  });

  recorder.start();
  return { receiver, sent, recorder, optedOut, tick: (s: number) => (clock += s) };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

test("one utterance per speaker turn, as mono WAV with speaker details", async () => {
  const { receiver, sent, recorder } = setup();
  receiver.speaking.emit("start", "1");
  const stream = receiver.streams.get("1")!;
  stream.push(Buffer.alloc(SECOND));
  stream.push(Buffer.alloc(SECOND / 2));
  stream.push(null); // Discord ends the stream after the silence timeout
  await settle();

  assert.equal(sent.length, 1);
  const { meta, wav } = sent[0]!;
  assert.deepEqual(meta, { sessionId: "s1", speaker: "user-1", speakerId: "1", character: "Valeros", tStart: 1000 });
  assert.equal(wav.length, 44 + 1.5 * 96_000);
  assert.equal(recorder.utterances, 1);
  assert.equal(recorder.speakers.size, 1);
});

test("long speech is sent in pieces with advancing start times", async () => {
  const { receiver, sent } = setup({ maxSeconds: 1 });
  receiver.speaking.emit("start", "2");
  const stream = receiver.streams.get("2")!;
  stream.push(Buffer.alloc(SECOND * 2.5));
  stream.push(null);
  await settle();
  assert.deepEqual(sent.map((s) => s.meta.tStart), [1000, 1001, 1002]);
  assert.deepEqual(sent.map((s) => s.wav.length - 44), [96_000, 96_000, 48_000]);
});

test("short clips, the bot itself and opted-out users are not sent", async () => {
  const { receiver, sent, optedOut } = setup();
  optedOut.add("3");
  receiver.speaking.emit("start", "bot");
  receiver.speaking.emit("start", "3");
  assert.equal(receiver.streams.size, 0);

  receiver.speaking.emit("start", "4");
  receiver.streams.get("4")!.push(Buffer.alloc(SECOND * 0.2)); // a cough
  receiver.streams.get("4")!.push(null);
  await settle();
  assert.equal(sent.length, 0);
});

test("a repeated speaking event while already listening does not resubscribe", () => {
  const { receiver } = setup();
  receiver.speaking.emit("start", "1");
  const first = receiver.streams.get("1");
  receiver.speaking.emit("start", "1");
  assert.equal(receiver.streams.get("1"), first);
});

test("stop() flushes speech in progress and waits for uploads", async () => {
  const { receiver, sent, recorder } = setup();
  receiver.speaking.emit("start", "1");
  receiver.streams.get("1")!.push(Buffer.alloc(SECOND));
  await settle();
  await recorder.stop();
  await settle();
  assert.equal(sent.length, 1);
  receiver.speaking.emit("start", "5");
  assert.equal(receiver.streams.has("5"), false);
});

test("a failing upload is logged, not thrown", async () => {
  const logs: string[] = [];
  const { receiver } = setup({
    stt: { sendUtterance: async () => Promise.reject(new Error("worker down")) },
    log: (m) => logs.push(m),
  });
  receiver.speaking.emit("start", "1");
  receiver.streams.get("1")!.push(Buffer.alloc(SECOND));
  receiver.streams.get("1")!.push(null);
  await settle();
  assert.match(logs[0] ?? "", /worker down/);
});

test("real Opus packets are decoded (opusscript) into the expected amount of audio", async () => {
  const prism = (await import("prism-media")).default;
  const encoder = new prism.opus.Encoder({ rate: 48_000, channels: 2, frameSize: 960 });
  const packets: Buffer[] = [];
  encoder.on("data", (packet: Buffer) => packets.push(packet));
  const frame = Buffer.alloc(960 * 2 * 2); // 20 ms of stereo silence-ish PCM
  for (let i = 0; i < frame.length; i += 2) frame.writeInt16LE(Math.round(Math.sin(i / 20) * 8000), i);
  for (let i = 0; i < 50; i++) encoder.write(frame); // 1 second
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(packets.length, 50);

  const { receiver, sent } = setup({ decoder: undefined }); // undefined → the real decoder
  receiver.speaking.emit("start", "1");
  const stream = receiver.streams.get("1")!;
  for (const packet of packets) stream.push(packet);
  stream.push(null);
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0]!.wav.length - 44, 96_000); // 1 s of 48 kHz mono s16le
  // The signal survives the stereo → mono decode (not silence).
  const pcm = sent[0]!.wav.subarray(44);
  let peak = 0;
  for (let i = 0; i < pcm.length; i += 2) peak = Math.max(peak, Math.abs(pcm.readInt16LE(i)));
  assert.ok(peak > 2000, `peak ${peak}`);
});

test("raw packets go to the podcast tracks too, before decoding", async () => {
  const seen: [string, number][] = [];
  const { receiver } = setup({ onPacket: (userId, opus) => void seen.push([userId, opus.length]) });
  receiver.speaking.emit("start", "1");
  receiver.streams.get("1")!.push(Buffer.alloc(SECOND));
  receiver.streams.get("1")!.push(null);
  receiver.speaking.emit("start", "bot"); // ignored users never reach the tracks
  await settle();
  assert.deepEqual(seen, [["1", SECOND]]);
});
