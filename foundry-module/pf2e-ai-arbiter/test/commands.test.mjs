import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCommand, toSocketUrl, escapeHtml } from "../scripts/commands.mjs";

test("parses /rule and /gmrule", () => {
  assert.deepEqual(parseCommand("/rule mitä Trip tekee?"), { mode: "public", query: "mitä Trip tekee?" });
  assert.deepEqual(parseCommand("  /GMRULE  näkeekö hän minut\nrivi 2 "), {
    mode: "gm",
    query: "näkeekö hän minut\nrivi 2",
  });
  assert.deepEqual(parseCommand("/rule"), { mode: "public", query: "" });
});

test("ignores other messages", () => {
  for (const message of ["hello", "/roll 1d20", "/ruler", "/rules x", "say /rule x", null]) {
    assert.equal(parseCommand(message), null);
  }
});

test("builds the socket URL", () => {
  assert.equal(toSocketUrl("https://arbiter.example.com"), "wss://arbiter.example.com/ws/foundry");
  assert.equal(toSocketUrl("https://example.com/arbiter/?x=1"), "wss://example.com/arbiter/ws/foundry");
  assert.equal(toSocketUrl("http://localhost:8765"), "ws://localhost:8765/ws/foundry");
  assert.throws(() => toSocketUrl("not a url"));
});

test("escapes HTML", () => {
  assert.equal(escapeHtml(`<b>"x" & 'y'</b>`), "&lt;b&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/b&gt;");
});
