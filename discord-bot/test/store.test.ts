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
