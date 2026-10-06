// HTTP clients for the backend (sessions, transcripts) and the STT worker (audio clips).

export interface Session {
  id: string;
  label: string | null;
  started_at: number;
  ended_at: number | null;
  segment_count: number;
}

export interface UtteranceMeta {
  sessionId: string;
  speaker: string;
  speakerId: string;
  character?: string | undefined;
  tStart: number;
}

// A non-2xx answer, keeping the status and body so callers can tell refusals apart.
export class HttpError extends Error {
  constructor(
    what: string,
    readonly status: number,
    readonly body: string,
  ) {
    super(`${what} failed: HTTP ${status} ${body.slice(0, 200)}`);
    this.name = "HttpError";
  }
}

async function check(response: Response, what: string): Promise<Response> {
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new HttpError(what, response.status, body);
  }
  return response;
}

export class BackendClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  private request(path: string, init: RequestInit = {}): Promise<Response> {
    return fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
        ...init.headers,
      },
      signal: AbortSignal.timeout(15_000),
    });
  }

  async startSession(label: string | null): Promise<Session> {
    const response = await this.request("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ label, source: "discord" }),
    });
    return (await check(response, "Starting the session")).json() as Promise<Session>;
  }

  async stopSession(id: string): Promise<Session> {
    const response = await this.request(`/api/v1/sessions/${id}/stop`, { method: "POST" });
    return (await check(response, "Stopping the session")).json() as Promise<Session>;
  }

  async getSession(id: string): Promise<Session> {
    const response = await this.request(`/api/v1/sessions/${id}`);
    return (await check(response, "Reading the session")).json() as Promise<Session>;
  }

  async latestSession(): Promise<Session | undefined> {
    const response = await this.request("/api/v1/sessions");
    const sessions = (await (await check(response, "Listing sessions")).json()) as Session[];
    return sessions[0];
  }

  async transcriptText(id: string): Promise<string> {
    const response = await this.request(`/api/v1/sessions/${id}/transcript?format=text`);
    return (await check(response, "Reading the transcript")).text();
  }
}

export class SttClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  async sendUtterance(meta: UtteranceMeta, wav: Buffer): Promise<void> {
    const params = new URLSearchParams({
      session_id: meta.sessionId,
      speaker: meta.speaker,
      speaker_id: meta.speakerId,
      t_start: meta.tStart.toFixed(3),
    });
    if (meta.character) params.set("character", meta.character);
    const response = await fetch(`${this.baseUrl}/v1/utterances?${params}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "audio/wav" },
      body: new Uint8Array(wav),
      signal: AbortSignal.timeout(30_000),
    });
    await check(response, "Sending audio to the STT worker");
  }
}
