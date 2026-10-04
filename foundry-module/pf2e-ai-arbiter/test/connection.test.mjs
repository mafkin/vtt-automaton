import { test } from "node:test";
import assert from "node:assert/strict";
import { BackendConnection } from "../scripts/connection.mjs";

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    FakeSocket.instances.push(this);
  }
  send(data) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  // test helpers
  open() {
    this.onopen?.();
  }
  receive(message) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  drop() {
    this.onclose?.();
  }
}

function fakeTimers() {
  const timers = { timeouts: [], intervals: [] };
  timers.setTimeout = (fn, ms) => timers.timeouts.push({ fn, ms }) - 1;
  timers.clearTimeout = () => {};
  timers.setInterval = (fn, ms) => timers.intervals.push({ fn, ms }) - 1;
  timers.clearInterval = () => {};
  return timers;
}

function setup() {
  FakeSocket.instances = [];
  const timers = fakeTimers();
  const messages = [];
  const statuses = [];
  const connection = new BackendConnection({
    url: "wss://x/ws/foundry",
    token: "secret",
    onMessage: (m) => messages.push(m),
    onStatus: (s) => statuses.push(s),
    WebSocketImpl: FakeSocket,
    timers,
  });
  connection.start();
  return { connection, timers, messages, statuses, socket: () => FakeSocket.instances.at(-1) };
}

test("handshake, messages and keep-alive", () => {
  const { connection, timers, messages, statuses, socket } = setup();
  socket().open();
  assert.deepEqual(socket().sent, [{ type: "hello", v: 1, token: "secret" }]);
  assert.equal(connection.send({ type: "x" }), false); // not ready before welcome

  socket().receive({ type: "welcome", v: 1 });
  assert.equal(connection.ready, true);
  assert.deepEqual(statuses, ["connected"]);
  assert.equal(connection.send({ type: "ruling.request", id: "1" }), true);

  socket().receive({ type: "ruling.result", id: "1" });
  socket().receive({ type: "pong" });
  assert.deepEqual(messages, [{ type: "ruling.result", id: "1" }]);

  timers.intervals[0].fn();
  assert.deepEqual(socket().sent.at(-1), { type: "ping" });
});

test("reconnects with backoff after a drop", () => {
  const { timers, statuses, socket } = setup();
  socket().open();
  socket().receive({ type: "welcome", v: 1 });
  socket().drop();
  assert.deepEqual(statuses, ["connected", "disconnected"]);
  assert.equal(timers.timeouts[0].ms, 1000);

  timers.timeouts[0].fn(); // reconnect attempt 1
  assert.equal(FakeSocket.instances.length, 2);
  socket().drop(); // fails before welcome: no extra status, longer delay
  assert.equal(timers.timeouts[1].ms, 2000);
  assert.deepEqual(statuses, ["connected", "disconnected"]);
});

test("stops retrying when the token is rejected", () => {
  const { timers, statuses, socket } = setup();
  socket().open();
  socket().receive({ type: "error", code: "auth.invalid" });
  socket().drop();
  assert.deepEqual(statuses, ["auth-failed"]);
  assert.equal(timers.timeouts.length, 0);
});

test("stop() closes without reconnecting", () => {
  const { connection, timers, socket } = setup();
  socket().open();
  socket().receive({ type: "welcome", v: 1 });
  connection.stop();
  assert.equal(socket().closed, true);
  assert.equal(timers.timeouts.length, 0);
  assert.equal(connection.ready, false);
});
