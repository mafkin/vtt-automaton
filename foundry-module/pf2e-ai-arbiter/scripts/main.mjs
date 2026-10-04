import { parseCommand, toSocketUrl, escapeHtml } from "./commands.mjs";
import { BackendConnection } from "./connection.mjs";

const MODULE_ID = "pf2e-ai-arbiter";
const SOCKET = `module.${MODULE_ID}`;
const REQUEST_TIMEOUT_MS = 90_000;

const t = (key, data) =>
  data ? game.i18n.format(`ARBITER.${key}`, data) : game.i18n.localize(`ARBITER.${key}`);
const setting = (key) => game.settings.get(MODULE_ID, key);

/**
 * Runs only in the active GM's browser. Holds the backend connection, creates the chat cards
 * (so whispers and permissions are handled in one place) and fills them in when rulings arrive.
 * Players' /rule commands are relayed here through the module socket.
 */
class Arbiter {
  connection = null;
  /** ruling id -> { message: Promise<ChatMessage>, timer } */
  pending = new Map();

  start() {
    const backendUrl = setting("backendUrl");
    const token = setting("token");
    if (!backendUrl || !token) {
      ui.notifications.warn(t("Status.NotConfigured"));
      return;
    }
    let url;
    try {
      url = toSocketUrl(backendUrl);
    } catch {
      ui.notifications.warn(t("Status.NotConfigured"));
      return;
    }
    this.connection = new BackendConnection({
      url,
      token,
      onMessage: (message) => this.#onMessage(message),
      onStatus: (status) => this.#onStatus(status),
    });
    this.connection.start();
  }

  stop() {
    this.connection?.stop();
    this.connection = null;
    for (const { timer } of this.pending.values()) clearTimeout(timer);
    this.pending.clear();
  }

  /** Handle a /rule or /gmrule from any user (the GM's own, or relayed from a player). */
  async ask({ query, mode, userId, context }) {
    const user = game.users.get(userId);
    if (!this.connection?.ready) {
      notifyUser(userId, "Error.NotConnected");
      return;
    }
    const id = foundry.utils.randomID();
    const label = t("Asked", { user: user?.name ?? "?" });
    this.#track(id, createCard({ id, label, query, mode }));
    this.connection.send({
      type: "ruling.request",
      id,
      request: { query, mode, user: user?.name ?? null, render: "foundry", context },
    });
  }

  #track(id, message) {
    const timer = setTimeout(() => this.#finish(id, errorHtml(t("Error.Timeout"))), REQUEST_TIMEOUT_MS);
    this.pending.set(id, { message, timer });
  }

  async #finish(id, content) {
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);
    clearTimeout(entry.timer);
    const message = await entry.message;
    await message?.update({ content });
  }

  async #onMessage(message) {
    const { type, id, origin } = message ?? {};
    if (origin === "voice" && !setting("voiceRulings")) return;

    // Spoken questions start on the server: open a card showing what was heard.
    if (origin === "voice" && !this.pending.has(id) && type !== "ruling.pending") {
      // Result arrived before (or without) the pending message; show it directly.
      this.#track(id, createVoiceCard(message));
    } else if (type === "ruling.pending" && origin === "voice") {
      this.#track(id, createVoiceCard(message));
      return;
    }

    if (type === "ruling.result") {
      await this.#finish(id, message.html);
    } else if (type === "ruling.error") {
      const text =
        message.code === "rules.no_match"
          ? t("Error.NoMatch")
          : t("Error.Failed", { message: message.message ?? message.code });
      await this.#finish(id, errorHtml(text));
    }
  }

  #onStatus(status) {
    const notices = {
      connected: ["info", "Status.Connected"],
      disconnected: ["warn", "Status.Disconnected"],
      "auth-failed": ["error", "Status.AuthFailed"],
    };
    const [level, key] = notices[status] ?? [];
    if (level) ui.notifications[level](t(key));
  }
}

function pendingHtml() {
  return `<div class="vtt-arbiter vtt-arbiter-pending"><i class="fa-solid fa-book-open fa-fade"></i> ${escapeHtml(t("Pending"))}</div>`;
}

function errorHtml(text) {
  return `<div class="vtt-arbiter vtt-arbiter-error"><p>${escapeHtml(text)}</p></div>`;
}

function createCard({ id, label, query, mode, icon = "fa-scroll" }) {
  const gmTag = mode === "gm" ? ` <span class="arbiter-gm-only">${escapeHtml(t("GmOnly"))}</span>` : "";
  return ChatMessage.create({
    speaker: { alias: setting("speakerName") },
    flavor:
      `<span class="arbiter-question"><i class="fa-solid ${icon}"></i> ` +
      `<strong>${escapeHtml(label)}:</strong> ${escapeHtml(query)}${gmTag}</span>`,
    content: pendingHtml(),
    whisper: mode === "gm" ? ChatMessage.getWhisperRecipients("GM").map((u) => u.id) : [],
    flags: { [MODULE_ID]: { rulingId: id } },
  });
}

function createVoiceCard({ id, speaker, query, mode }) {
  return createCard({
    id,
    label: t("Heard", { user: speaker ?? "?" }),
    query: query ?? "",
    mode,
    icon: "fa-microphone",
  });
}

/** Show a localized warning to a user; relays it over the socket if it is someone else. */
function notifyUser(userId, key) {
  if (userId === game.user.id) ui.notifications.warn(t(key));
  else game.socket.emit(SOCKET, { action: "notify", userId, key });
}

function collectContext() {
  const token = canvas?.tokens?.controlled?.[0];
  return {
    actor: token?.name ?? game.user.character?.name ?? null,
    targets: [...game.user.targets].map((target) => target.name),
  };
}

let arbiter = null;

/** Start or stop the arbiter depending on whether this browser is the active GM. */
function refreshRole() {
  const shouldRun = game.user.isGM && game.users.activeGM?.isSelf;
  if (shouldRun && !arbiter) {
    arbiter = new Arbiter();
    arbiter.start();
  } else if (!shouldRun && arbiter) {
    arbiter.stop();
    arbiter = null;
  }
}

function restartArbiter() {
  arbiter?.stop();
  arbiter = null;
  refreshRole();
}

Hooks.once("init", () => {
  game.settings.register(MODULE_ID, "backendUrl", {
    name: "ARBITER.Settings.BackendUrl.Name",
    hint: "ARBITER.Settings.BackendUrl.Hint",
    scope: "world",
    config: true,
    restricted: true,
    type: String,
    default: "https://arbiter.ttrpg-arbiter.org",
    onChange: restartArbiter,
  });
  // Client scope: kept in the GM's browser only, never synced to players like world settings are.
  game.settings.register(MODULE_ID, "token", {
    name: "ARBITER.Settings.Token.Name",
    hint: "ARBITER.Settings.Token.Hint",
    scope: "client",
    config: true,
    type: String,
    default: "",
    onChange: restartArbiter,
  });
  game.settings.register(MODULE_ID, "speakerName", {
    name: "ARBITER.Settings.SpeakerName.Name",
    hint: "ARBITER.Settings.SpeakerName.Hint",
    scope: "world",
    config: true,
    restricted: true,
    type: String,
    default: "Nethys Arbiter",
  });
  game.settings.register(MODULE_ID, "voiceRulings", {
    name: "ARBITER.Settings.VoiceRulings.Name",
    hint: "ARBITER.Settings.VoiceRulings.Hint",
    scope: "world",
    config: true,
    restricted: true,
    type: Boolean,
    default: true,
  });

  Hooks.on("chatMessage", (_chatLog, message) => {
    const command = parseCommand(message);
    if (!command) return true;
    if (!command.query) {
      ui.notifications.warn(t("Error.EmptyQuery"));
      return false;
    }
    const request = { ...command, userId: game.user.id, context: collectContext() };
    if (arbiter) {
      arbiter.ask(request);
    } else if (!game.users.activeGM) {
      ui.notifications.warn(t("Error.NoGm"));
    } else {
      game.socket.emit(SOCKET, { action: "ask", ...request });
      ui.notifications.info(t("Sent"));
    }
    return false; // don't post the raw command as a chat message
  });
});

Hooks.once("ready", () => {
  game.socket.on(SOCKET, (data) => {
    if (data?.action === "ask" && arbiter) arbiter.ask(data);
    else if (data?.action === "notify" && data.userId === game.user.id) {
      ui.notifications.warn(t(data.key));
    }
  });
  refreshRole();
  // Fires when any user connects or disconnects, which can change who the active GM is.
  Hooks.on("userConnected", refreshRole);
});
