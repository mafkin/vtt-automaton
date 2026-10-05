export interface Config {
  discordToken: string;
  guildId: string;
  backendUrl: string;
  backendToken: string;
  sttUrl: string;
  sttToken: string;
  dataDir: string;
  /** Stop recording after the voice channel has had no people in it for this long. */
  emptyChannelMinutes: number;
}

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`Environment variable ${name} is required`);
  return value;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    discordToken: required(env, "DISCORD_TOKEN"),
    guildId: required(env, "DISCORD_GUILD_ID"),
    backendUrl: (env.BACKEND_URL ?? "http://backend:8765").replace(/\/+$/, ""),
    backendToken: required(env, "BACKEND_TOKEN"),
    sttUrl: (env.STT_URL ?? "http://stt-worker:8770").replace(/\/+$/, ""),
    sttToken: required(env, "STT_TOKEN"),
    dataDir: env.DATA_DIR ?? "data",
    emptyChannelMinutes: Number(env.EMPTY_CHANNEL_MINUTES ?? 5),
  };
}
