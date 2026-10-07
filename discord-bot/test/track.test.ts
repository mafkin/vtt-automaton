import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import OpusScript from "opusscript";
import { OggOpusWriter, SILENCE_PACKET } from "../src/ogg.js";
import { SessionTracks, Track, TrackTimer, deleteSpeakerTracks } from "../src/track.js";
import { readOgg } from "./oggread.js";

const FRAME = 960; // 20 ms at 48 kHz

function trackFor(startMs = 0) {
  const out: Buffer[] = [];
  const writer = new OggOpusWriter((page) => out.push(page));
  return { track: new Track(writer, startMs), writer, ogg: () => Buffer.concat(out) };
}

test("RTP timestamps place packets, the first one pinned to its arrival time", () => {
  const timer = new TrackTimer();
  assert.equal(timer.position(48_000, 0, { sequence: 1, timestamp: 1_000_000 }), 48_000);
  // 3 s later by RTP, though it arrived 120 ms "late": RTP wins.
  assert.equal(timer.position(48_000 + 3 * 48_000 + 5760, 52_800, { sequence: 2, timestamp: 1_144_000 }), 192_000);
  // One arriving 10 ms earlier than predicted: the first packet was delayed; the pin moves.
  assert.equal(timer.position(192_000 + 960 - 480, 0, { sequence: 3, timestamp: 1_144_960 }), 192_480);
  assert.equal(timer.position(194_000, 0, { sequence: 4, timestamp: 1_144_960 + 960 }), 192_480 + 960);
  // The RTP clock wraps around 2^32.
  const wrap = new TrackTimer();
  wrap.position(0, 0, { sequence: 1, timestamp: 2 ** 32 - 960 });
  assert.equal(wrap.position(1920, 960, { sequence: 2, timestamp: 960 }), 1920);
});

test("a jump in RTP time (rejoin, new SSRC) pins the speaker to the wall clock again", () => {
  const timer = new TrackTimer();
  timer.position(0, 0, { sequence: 1, timestamp: 5000 });
  const position = timer.position(10 * 48_000, 960, { sequence: 1, timestamp: 777 });
  assert.equal(position, 10 * 48_000);
  assert.equal(timer.position(10 * 48_000 + 960, 0, { sequence: 2, timestamp: 777 + 960 }), 10 * 48_000 + 960);
});

test("without RTP, arrival times decide, with room for network jitter", () => {
  const timer = new TrackTimer();
  assert.equal(timer.position(1000, 960), 960); // 0.8 ms late: continues
  assert.equal(timer.position(10 * 48_000, 960), 10 * 48_000); // after a pause: a gap
});

test("gaps become 20 ms silence packets, late duplicates are skipped", () => {
  const { track, writer, ogg } = trackFor();
  const voice = Buffer.from([0xfc, 1, 2, 3]); // CELT 20 ms, stereo
  track.packet(voice, 107.5, { sequence: 1, timestamp: 0 }); // 107.5 ms after the start
  track.packet(voice, 160, { sequence: 2, timestamp: 960 });
  track.packet(voice, 170, { sequence: 2, timestamp: 960 }); // a duplicate
  track.packet(voice, 175, { sequence: 1, timestamp: 0 }); // an old one, late
  track.packet(voice, 1107.5, { sequence: 3, timestamp: 48_000 }); // after a 1 s pause
  track.close();
  const packets = readOgg(ogg()).slice(2).flatMap((p) => p.packets);
  const kinds = packets.map((p) => (p.equals(voice) ? "v" : p.equals(SILENCE_PACKET) ? "." : ",")).join("");
  // 107.5 ms: five 20 ms silences, then 5 and 2.5 ms; voice, voice; 48 silences; voice.
  assert.equal(kinds, `${".".repeat(5)},,vv${".".repeat(48)}v`);
  assert.equal(writer.samples, (5 + 2 + 48 + 1) * FRAME + 360);
});

test("real Opus clicks land where they were spoken, despite gaps and jittery arrival", () => {
  const encoder = new OpusScript(48_000, 2, OpusScript.Application.AUDIO);
  const quiet = encoder.encode(Buffer.alloc(FRAME * 4), FRAME);
  const click = Buffer.alloc(FRAME * 4);
  for (let i = 0; i < FRAME * 2; i++) click.writeInt16LE(i % 40 < 20 ? 20_000 : -20_000, i * 2);
  const loud = encoder.encode(click, FRAME);
  encoder.delete();

  // Clicks at 1.0 s, 2.5 s and 7.0 s of the session. Discord sends nothing between bursts
  // (the speaker pauses), and every packet arrives 0-40 ms late.
  const { track, ogg } = trackFor(10_000);
  const clicksAt = [1.0, 2.5, 7.0];
  const rtp = 123_456;
  let jitter = 0;
  let sequence = 65_530; // wraps around during the test
  for (const at of clicksAt) {
    for (let f = -3; f <= 3; f++) {
      const t = at + (f * FRAME) / 48_000;
      jitter = (jitter + 17) % 40;
      const timestamp = (rtp + Math.round((t - clicksAt[0]!) * 48_000)) >>> 0;
      sequence = (sequence + 1) & 0xffff;
      track.packet(f === 0 ? loud : quiet, 10_000 + t * 1000 + jitter, { sequence, timestamp });
    }
  }
  track.close();

  const decoder = new OpusScript(48_000, 2, OpusScript.Application.AUDIO);
  const pcm = Buffer.concat(
    readOgg(ogg()).slice(2).flatMap((p) => p.packets).map((p) => Buffer.from(decoder.decode(p))),
  );
  decoder.delete();
  const loudFrames: number[] = [];
  for (let f = 0; f * FRAME * 4 < pcm.length; f++) {
    let peak = 0;
    for (let i = f * FRAME * 2; i < (f + 1) * FRAME * 2 && i * 2 < pcm.length; i++) {
      peak = Math.max(peak, Math.abs(pcm.readInt16LE(i * 2)));
    }
    if (peak > 8000) loudFrames.push(f);
  }
  const heard = loudFrames.filter((f, i) => i === 0 || f - loudFrames[i - 1]! > 5);
  // The first click is pinned to its (late) arrival: within 40 ms. The rest keep its RTP spacing.
  assert.equal(heard.length, 3);
  heard.forEach((frame, i) => {
    const seconds = (frame * FRAME) / 48_000;
    assert.ok(Math.abs(seconds - clicksAt[i]!) <= 0.06, `click ${i} at ${seconds} s`);
  });
  const spacing = ((heard[2]! - heard[0]!) * FRAME) / 48_000;
  assert.ok(Math.abs(spacing - 6.0) <= 0.02, `spacing ${spacing}`);
});

test("only consenting speakers get a track; withdrawing deletes it", () => {
  const root = mkdtempSync(join(tmpdir(), "rec-"));
  let clock = 1000;
  const tracks = new SessionTracks(
    root,
    "s1",
    "Session 12",
    1000,
    (id) => (id === "yes" ? { speaker: "Aino", character: "Valeros", consentedAt: 5 } : null),
    () => clock,
  );
  const voice = Buffer.from([0xfc, 1, 2, 3]);
  tracks.packet("yes", voice, { sequence: 1, timestamp: 0 });
  tracks.packet("no", voice, { sequence: 1, timestamp: 0 });
  clock = 2000;
  tracks.stop();
  const manifest = JSON.parse(readFileSync(join(root, "s1", "manifest.json"), "utf8"));
  assert.deepEqual(Object.keys(manifest.tracks), ["yes"]);
  assert.equal(manifest.tracks.yes.file, "tracks/yes.ogg");
  assert.equal(manifest.tracks.yes.character, "Valeros");
  assert.deepEqual([manifest.startedAt, manifest.stoppedAt], [1000, 2000]);
  assert.equal(tracks.speakers, 1);
  assert.ok(existsSync(join(root, "s1", "tracks", "yes.ogg")));
  assert.ok(!existsSync(join(root, "s1", "tracks", "no.ogg")));

  assert.equal(deleteSpeakerTracks(root, "yes"), 1);
  assert.ok(!existsSync(join(root, "s1", "tracks", "yes.ogg")));
  const after = JSON.parse(readFileSync(join(root, "s1", "manifest.json"), "utf8"));
  assert.deepEqual(after.tracks, {});
  assert.equal(deleteSpeakerTracks(join(root, "missing"), "yes"), 0);
});

test("withdrawing during a session stops and deletes the live track", () => {
  const root = mkdtempSync(join(tmpdir(), "rec-"));
  const tracks = new SessionTracks(root, "s2", null, 0, () => ({ speaker: "A", consentedAt: 1 }), () => 100);
  tracks.packet("u", Buffer.from([0xfc, 1]), undefined);
  tracks.drop("u");
  tracks.packet("u", Buffer.from([0xfc, 1]), undefined); // nothing more is recorded
  tracks.stop();
  assert.ok(!existsSync(join(root, "s2", "tracks", "u.ogg")));
  assert.equal(tracks.speakers, 0);
});

test("a track that can't be written stops alone, without throwing", () => {
  const root = mkdtempSync(join(tmpdir(), "rec-"));
  const logs: string[] = [];
  const tracks = new SessionTracks(root, "s3", null, 0, () => ({ speaker: "A", consentedAt: 1 }), () => 100, (m) => logs.push(m));
  rmSync(join(root, "s3", "tracks"), { recursive: true }); // the folder vanished (or the disk is full)
  assert.doesNotThrow(() => tracks.packet("u", Buffer.from([0xfc, 1]), undefined));
  assert.doesNotThrow(() => tracks.packet("u", Buffer.from([0xfc, 1]), undefined));
  assert.equal(logs.length, 1);
  assert.match(logs[0]!, /Podcast track of u stopped/);
  tracks.stop();
});
