import assert from "node:assert/strict";
import { test } from "node:test";
import { BackendClient, HttpError } from "../src/clients.js";

test("a refused session start carries the HTTP status and detail", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "comic_in_progress" }), { status: 409 });
  try {
    const backend = new BackendClient("http://backend", "token");
    await assert.rejects(backend.startSession(null), (error) => {
      assert.ok(error instanceof HttpError);
      assert.equal(error.status, 409);
      assert.match(error.body, /comic_in_progress/);
      return true;
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});
