// Pure helpers, kept free of Foundry globals so they can be unit tested with Node.

const COMMAND_RE = /^\/(rule|gmrule)(?:\s+([\s\S]*))?$/i;

/** Parse "/rule …" or "/gmrule …". Returns null for any other chat message. */
export function parseCommand(message) {
  const match = COMMAND_RE.exec(String(message ?? "").trim());
  if (!match) return null;
  return {
    mode: match[1].toLowerCase() === "gmrule" ? "gm" : "public",
    query: (match[2] ?? "").trim(),
  };
}

/** "https://arbiter.example.com" -> "wss://arbiter.example.com/ws/foundry" */
export function toSocketUrl(backendUrl) {
  const url = new URL(backendUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/ws/foundry`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}
