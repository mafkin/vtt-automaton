import assert from "node:assert/strict";
import { test } from "node:test";
import { VoiceReceiver } from "@discordjs/voice";
import { installRtpTap, rtpInfo } from "../src/rtp.js";

test("the tap tags each parsed packet with its RTP header", () => {
  class FakeReceiver {
    parsePacket(buffer: Buffer): Buffer {
      return Buffer.from(buffer.subarray(12)); // a new Buffer, as decryption makes
    }
  }
  assert.equal(installRtpTap(FakeReceiver.prototype, () => {}), true);
  const message = Buffer.alloc(20);
  message.writeUInt16BE(4242, 2);
  message.writeUInt32BE(0xdeadbeef, 4);
  const packet = new FakeReceiver().parsePacket(message);
  assert.deepEqual(rtpInfo(packet), { sequence: 4242, timestamp: 0xdeadbeef });
  assert.equal(rtpInfo(Buffer.alloc(1)), undefined);
});

test("the installed @discordjs/voice still has the method the tap wraps", () => {
  // Fails on a library upgrade that renames it: tracks would silently fall back to arrival times.
  assert.equal(typeof (VoiceReceiver.prototype as unknown as { parsePacket?: unknown }).parsePacket, "function");
});

test("a receiver without the method leaves the tap off, with a log line", () => {
  const logs: string[] = [];
  assert.equal(installRtpTap({}, (m) => logs.push(m)), false);
  assert.match(logs[0]!, /arrival times/);
});
