import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { UserStore } from "../src/store.js";

test("characters and opt-outs persist across restarts", () => {
  const path = join(mkdtempSync(join(tmpdir(), "bot-")), "nested", "users.json");
  const store = new UserStore(path);
  store.setCharacter("1", "Valeros");
  store.setOptedOut("2", true);
  store.setOptedOut("2", true);

  const reloaded = new UserStore(path);
  assert.equal(reloaded.character("1"), "Valeros");
  assert.equal(reloaded.isOptedOut("2"), true);
  assert.equal(reloaded.isOptedOut("1"), false);

  reloaded.setCharacter("1", null);
  reloaded.setOptedOut("2", false);
  const again = new UserStore(path);
  assert.equal(again.character("1"), undefined);
  assert.equal(again.isOptedOut("2"), false);
});

test("a missing or corrupt file starts empty", () => {
  const store = new UserStore(join(tmpdir(), "does-not-exist", "users.json"));
  assert.equal(store.character("1"), undefined);
});

test("podcast consent is stored with its time and text version, and can be withdrawn", async () => {
  const { PODCAST_CONSENT_VERSION } = await import("../src/store.js");
  const path = join(mkdtempSync(join(tmpdir(), "bot-")), "users.json");
  const store = new UserStore(path);
  store.setPodcast("1", true, 1234);
  assert.deepEqual(new UserStore(path).podcastConsent("1"), { at: 1234, version: PODCAST_CONSENT_VERSION });
  store.setPodcast("1", false);
  assert.equal(new UserStore(path).podcastConsent("1"), undefined);
});
