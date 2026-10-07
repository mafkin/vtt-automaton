// Small JSON file for per-user preferences: character names, recording opt-outs and podcast
// consent.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

/** Bump when the podcast consent text (messages.ts: podcastJoined) changes meaning. */
export const PODCAST_CONSENT_VERSION = 1;

export interface PodcastConsent {
  at: number;
  version: number;
}

interface Data {
  characters: Record<string, string>;
  optedOut: string[];
  /** Opted in to podcast tracks: when, and to which version of the consent text. */
  podcast: Record<string, PodcastConsent>;
}

export class UserStore {
  private data: Data = { characters: {}, optedOut: [], podcast: {} };

  constructor(private readonly path: string) {
    try {
      const parsed = JSON.parse(readFileSync(path, "utf8")) as Partial<Data>;
      this.data = {
        characters: parsed.characters ?? {},
        optedOut: parsed.optedOut ?? [],
        podcast: parsed.podcast ?? {},
      };
    } catch {
      // first run, or an unreadable file: start empty
    }
  }

  character(userId: string): string | undefined {
    return this.data.characters[userId];
  }

  setCharacter(userId: string, name: string | null): void {
    if (name) this.data.characters[userId] = name;
    else delete this.data.characters[userId];
    this.save();
  }

  isOptedOut(userId: string): boolean {
    return this.data.optedOut.includes(userId);
  }

  setOptedOut(userId: string, optedOut: boolean): void {
    const others = this.data.optedOut.filter((id) => id !== userId);
    this.data.optedOut = optedOut ? [...others, userId] : others;
    this.save();
  }

  podcastConsent(userId: string): PodcastConsent | undefined {
    return this.data.podcast[userId];
  }

  setPodcast(userId: string, joined: boolean, now = Date.now()): void {
    if (joined) this.data.podcast[userId] = { at: now, version: PODCAST_CONSENT_VERSION };
    else delete this.data.podcast[userId];
    this.save();
  }

  private save(): void {
    mkdirSync(dirname(this.path), { recursive: true });
    const tmp = `${this.path}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.data, null, 2));
    renameSync(tmp, this.path); // atomic: never leaves a half-written file
  }
}
