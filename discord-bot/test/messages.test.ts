import assert from "node:assert/strict";
import { test } from "node:test";
import { HttpError } from "../src/clients.js";
import { loadConfig } from "../src/config.js";
import { formatDuration, sessionStartError, text } from "../src/messages.js";

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

test("a session refused during comic generation gets its own message", () => {
  const comic = new HttpError("Starting a session", 409, '{"detail":"comic_in_progress"}');
  assert.equal(sessionStartError(comic), text.comicInProgress);
  assert.match(text.comicInProgress, /Sarjakuvaa/);

  const other = new HttpError("Starting a session", 500, "boom");
  assert.equal(sessionStartError(other), text.backendError(other.message));
  assert.equal(sessionStartError(new Error("timeout")), text.backendError("timeout"));
});
