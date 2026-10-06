import { join } from "node:path";
import { Client, Events, GatewayIntentBits, MessageFlags, REST, Routes } from "discord.js";
import { generateDependencyReport } from "@discordjs/voice";
import { TranscriptionBot } from "./bot.js";
import { BackendClient, SttClient } from "./clients.js";
import { commands } from "./commands.js";
import { loadConfig } from "./config.js";
import { installRtpTap } from "./rtp.js";
import { UserStore } from "./store.js";

const config = loadConfig();
console.log(generateDependencyReport());
installRtpTap();

const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates] });
const bot = new TranscriptionBot(
  client,
  config,
  new BackendClient(config.backendUrl, config.backendToken),
  new SttClient(config.sttUrl, config.sttToken),
  new UserStore(join(config.dataDir, "users.json")),
);

client.once(Events.ClientReady, async (ready) => {
  // Guild commands appear immediately (global ones can take up to an hour).
  const rest = new REST().setToken(config.discordToken);
  await rest.put(Routes.applicationGuildCommands(ready.application.id, config.guildId), {
    body: commands,
  });
  console.log(`Logged in as ${ready.user.tag}; commands registered in guild ${config.guildId}`);
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  try {
    await bot.handle(interaction);
  } catch (error) {
    console.error("Command failed", error);
    const content = "Jokin meni pieleen. Katso botin loki.";
    if (interaction.deferred || interaction.replied) await interaction.editReply(content).catch(() => {});
    else await interaction.reply({ content, flags: MessageFlags.Ephemeral }).catch(() => {});
  }
});

client.on(Events.VoiceStateUpdate, (oldState, newState) => bot.onVoiceStateUpdate(oldState, newState));

async function shutdown(signal: string): Promise<void> {
  console.log(`${signal}: stopping active sessions`);
  await bot.stopAll().catch(() => {});
  await client.destroy();
  process.exit(0);
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));

await client.login(config.discordToken);
