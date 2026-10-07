// RTP timing of received Opus packets. @discordjs/voice hands out bare Opus packets; their RTP
// header (sequence number, 48 kHz timestamp) is dropped. Discord sends nothing during short
// pauses, so without the timestamps a track can't tell how long the gaps were.
//
// The tap wraps VoiceReceiver.prototype.parsePacket, which turns one UDP message into the
// packet the receive stream emits (the same Buffer object): it remembers each returned
// packet's RTP header. A library upgrade that renames the method disables the tap (with a log
// line) and tracks fall back to arrival times.
import { VoiceReceiver } from "@discordjs/voice";

export interface RtpInfo {
  sequence: number;
  timestamp: number;
}

const tags = new WeakMap<Buffer, RtpInfo>();
let installed = false;

type ParsePacket = (this: unknown, buffer: Buffer, ...rest: unknown[]) => Buffer | undefined;

export function installRtpTap(
  proto: object = VoiceReceiver.prototype,
  log: (message: string) => void = (m) => console.warn(m),
): boolean {
  if (installed && proto === VoiceReceiver.prototype) return true;
  const target = proto as { parsePacket?: ParsePacket };
  const original = target.parsePacket;
  if (typeof original !== "function") {
    log("RTP tap: VoiceReceiver.parsePacket not found; podcast tracks use arrival times");
    return false;
  }
  target.parsePacket = function (buffer: Buffer, ...rest: unknown[]) {
    const packet = original.call(this, buffer, ...rest);
    if (packet && buffer.length >= 12) {
      tags.set(packet, { sequence: buffer.readUInt16BE(2), timestamp: buffer.readUInt32BE(4) });
    }
    return packet;
  };
  if (proto === VoiceReceiver.prototype) installed = true;
  return true;
}

export function rtpInfo(packet: Buffer): RtpInfo | undefined {
  return tags.get(packet);
}
