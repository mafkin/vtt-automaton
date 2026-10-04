// WebSocket connection to the VTT Automaton backend, with auth handshake, keep-alive and
// reconnect. Free of Foundry globals so it can be unit tested with Node.

export const PROTOCOL_VERSION = 1;
const MAX_RECONNECT_DELAY_MS = 30_000;
// Cloudflare closes idle WebSockets after ~100 s; ping well inside that.
const PING_INTERVAL_MS = 25_000;

export class BackendConnection {
  #url;
  #token;
  #onMessage;
  #onStatus;
  #WebSocket;
  #timers;
  #ws = null;
  #ready = false;
  #stopped = true;
  #authFailed = false;
  #attempts = 0;
  #reconnectTimer = null;
  #pingTimer = null;

  constructor({
    url,
    token,
    onMessage,
    onStatus = () => {},
    WebSocketImpl = globalThis.WebSocket,
    timers = globalThis,
  }) {
    this.#url = url;
    this.#token = token;
    this.#onMessage = onMessage;
    this.#onStatus = onStatus;
    this.#WebSocket = WebSocketImpl;
    this.#timers = timers;
  }

  get ready() {
    return this.#ready;
  }

  start() {
    this.#stopped = false;
    this.#authFailed = false;
    this.#open();
  }

  stop() {
    this.#stopped = true;
    this.#ready = false;
    this.#timers.clearTimeout(this.#reconnectTimer);
    this.#timers.clearInterval(this.#pingTimer);
    this.#ws?.close();
    this.#ws = null;
  }

  /** Send a message; returns false if the connection is not ready. */
  send(message) {
    if (!this.#ready) return false;
    this.#ws.send(JSON.stringify(message));
    return true;
  }

  #open() {
    const ws = new this.#WebSocket(this.#url);
    this.#ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", v: PROTOCOL_VERSION, token: this.#token }));
    };
    ws.onmessage = (event) => this.#receive(event.data);
    ws.onerror = () => {}; // onclose follows and handles reconnecting
    ws.onclose = () => {
      if (this.#ws !== ws) return;
      const wasReady = this.#ready;
      this.#ready = false;
      this.#timers.clearInterval(this.#pingTimer);
      if (this.#stopped || this.#authFailed) return;
      if (wasReady) this.#onStatus("disconnected");
      const delay = Math.min(MAX_RECONNECT_DELAY_MS, 1000 * 2 ** this.#attempts++);
      this.#reconnectTimer = this.#timers.setTimeout(() => this.#open(), delay);
    };
  }

  #receive(data) {
    let message;
    try {
      message = JSON.parse(data);
    } catch {
      return;
    }
    if (message?.type === "welcome") {
      this.#ready = true;
      this.#attempts = 0;
      this.#pingTimer = this.#timers.setInterval(
        () => this.send({ type: "ping" }),
        PING_INTERVAL_MS,
      );
      this.#onStatus("connected");
    } else if (message?.type === "error" && message.code === "auth.invalid") {
      this.#authFailed = true;
      this.#onStatus("auth-failed");
    } else if (message?.type !== "pong") {
      this.#onMessage(message);
    }
  }
}
