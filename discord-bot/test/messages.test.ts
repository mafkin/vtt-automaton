import assert from "node:assert/strict";
import { test } from "node:test";
import { loadConfig } from "../src/config.js";
import { formatDuration, text } from "../src/messages.js";

test("formatDuration", () => {
  assert.equal(formatDuration(59), "1 min");
  assert.equal(formatDuration(3600 * 2 + 60 * 13), "2 h 13 min");
});

test("consent notice mentions opting out and that audio is not stored", () => {
  const notice = text.started("123");
  assert.match(notice, /<#123>/);
  assert.match(notice, /\/optout/);
  assert.match(notice, /Äänitiedostoja ei tallenneta/);
});

test("config requires secrets and trims URLs", () => {
  assert.throws(() => loadConfig({}), /DISCORD_TOKEN/);
  const config = loadConfig({
    DISCORD_TOKEN: "t",
    DISCORD_GUILD_ID: "g",
    BACKEND_TOKEN: "b",
    STT_TOKEN: "s",
    BACKEND_URL: "http://backend:8765/",
  });
  assert.equal(config.backendUrl, "http://backend:8765");
  assert.equal(config.sttUrl, "http://stt-worker:8770");
  assert.equal(config.emptyChannelMinutes, 5);
});
