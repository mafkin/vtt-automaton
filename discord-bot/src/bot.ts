import {
  AttachmentBuilder,
  type ChatInputCommandInteraction,
  type Client,
  type Guild,
  MessageFlags,
  type SendableChannels,
  type VoiceState,
} from "discord.js";
import {
  type VoiceConnection,
  VoiceConnectionStatus,
  entersState,
  joinVoiceChannel,
} from "@discordjs/voice";
import type { BackendClient, SttClient } from "./clients.js";
import type { Config } from "./config.js";
import { text } from "./messages.js";
import { Recorder } from "./recorder.js";
import { rtpInfo } from "./rtp.js";
import type { UserStore } from "./store.js";
import { SessionTracks, deleteSpeakerTracks } from "./track.js";

// The STT queue needs a moment to finish the last utterances before the transcript is posted.
const TRANSCRIPT_DELAY_MS = 20_000;

interface ActiveSession {
  id: string;
  label: string | null;
  startedAt: number;
  connection: VoiceConnection;
  recorder: Recorder;
  /** Podcast tracks, when the session was started with podcast:true. */
  tracks: SessionTracks | null;
  voiceChannelId: string;
  textChannel: SendableChannels | null;
  emptyTimer?: NodeJS.Timeout;
}

export class TranscriptionBot {
  private readonly sessions = new Map<string, ActiveSession>();
  private readonly names = new Map<string, string>();

  constructor(
    private readonly client: Client,
    private readonly config: Config,
    private readonly backend: BackendClient,
    private readonly stt: SttClient,
    private readonly users: UserStore,
  ) {}

  async handle(interaction: ChatInputCommandInteraction): Promise<void> {
    if (!interaction.inCachedGuild()) return;
    switch (interaction.commandName) {
      case "session": {
        const sub = interaction.options.getSubcommand();
        if (sub === "start") return this.start(interaction);
        if (sub === "stop") return this.stopCommand(interaction);
        if (sub === "status") return this.status(interaction);
        if (sub === "transcript") return this.postLatestTranscript(interaction);
        return;
      }
      case "link": {
        const name = interaction.options.getString("character")?.trim() || null;
        this.users.setCharacter(interaction.user.id, name);
        await interaction.reply({
          content: name ? text.linked(name) : text.unlinked,
          flags: MessageFlags.Ephemeral,
        });
        return;
      }
      case "podcast": {
        const join = interaction.options.getSubcommand() === "join";
        this.users.setPodcast(interaction.user.id, join);
        let deleted = 0;
        if (!join) {
          for (const active of this.sessions.values()) active.tracks?.drop(interaction.user.id);
          deleted = deleteSpeakerTracks(this.config.recordingsDir, interaction.user.id);
        }
        await interaction.reply({
          content: join ? text.podcastJoined : text.podcastLeft(deleted),
          flags: MessageFlags.Ephemeral,
        });
        return;
      }
      case "optout":
      case "optin": {
        const optOut = interaction.commandName === "optout";
        this.users.setOptedOut(interaction.user.id, optOut);
        await interaction.reply({
          content: optOut ? text.optedOut : text.optedIn,
          flags: MessageFlags.Ephemeral,
        });
        return;
      }
    }
  }

  private async start(interaction: ChatInputCommandInteraction<"cached">): Promise<void> {
    const guild = interaction.guild;
    if (this.sessions.has(guild.id)) {
      await interaction.reply({ content: text.alreadyRecording, flags: MessageFlags.Ephemeral });
      return;
    }
    const voiceChannel = interaction.member.voice.channel;
    if (!voiceChannel) {
      await interaction.reply({ content: text.joinVoiceFirst, flags: MessageFlags.Ephemeral });
      return;
    }
    await interaction.deferReply();

    const connection = joinVoiceChannel({
      channelId: voiceChannel.id,
      guildId: guild.id,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false, // a deafened bot receives no audio
      selfMute: true,
    });
    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
    } catch {
      connection.destroy();
      await interaction.editReply(text.connectFailed);
      return;
    }

    let session;
    try {
      session = await this.backend.startSession(interaction.options.getString("label"));
    } catch (error) {
      connection.destroy();
      await interaction.editReply(text.backendError((error as Error).message));
      return;
    }

    const startedAt = Date.now();
    const tracks = interaction.options.getBoolean("podcast")
      ? new SessionTracks(this.config.recordingsDir, session.id, session.label, startedAt, (userId) => {
          const consent = this.users.podcastConsent(userId);
          if (!consent) return null;
          return {
            speaker: guild.members.cache.get(userId)?.displayName ?? this.names.get(userId) ?? userId,
            character: this.users.character(userId),
            consentedAt: consent.at,
          };
        })
      : null;
    const recorder = new Recorder({
      receiver: connection.receiver,
      sessionId: session.id,
      stt: this.stt,
      speakerInfo: async (userId) => ({
        speaker: await this.displayName(guild, userId),
        character: this.users.character(userId),
      }),
      isOptedOut: (userId) => this.users.isOptedOut(userId),
      ignoreUserIds: new Set([this.client.user?.id ?? ""]),
      ...(tracks ? { onPacket: (userId: string, opus: Buffer) => tracks.packet(userId, opus, rtpInfo(opus)) } : {}),
    });
    recorder.start();

    const active: ActiveSession = {
      id: session.id,
      label: session.label,
      startedAt,
      connection,
      recorder,
      tracks,
      voiceChannelId: voiceChannel.id,
      textChannel: interaction.channel?.isSendable() ? interaction.channel : null,
    };
    this.sessions.set(guild.id, active);
    this.watchConnection(guild.id, active);
    if (!tracks) {
      await interaction.editReply(text.started(voiceChannel.id));
      return;
    }
    const people = voiceChannel.members.filter((m) => !m.user.bot);
    const on = people.filter((m) => this.users.podcastConsent(m.id)).map((m) => m.displayName);
    const off = people.filter((m) => !this.users.podcastConsent(m.id)).map((m) => m.displayName);
    await interaction.editReply(text.startedPodcast(voiceChannel.id, on, off));
  }

  private async stopCommand(interaction: ChatInputCommandInteraction<"cached">): Promise<void> {
    if (!this.sessions.has(interaction.guildId)) {
      await interaction.reply({ content: text.notRecording, flags: MessageFlags.Ephemeral });
      return;
    }
    await interaction.deferReply();
    const summary = await this.stop(interaction.guildId);
    await interaction.editReply(summary ?? text.notRecording);
  }

  /** Stop recording; returns the summary line, and posts the transcript a little later. */
  async stop(guildId: string): Promise<string | null> {
    const active = this.sessions.get(guildId);
    if (!active) return null;
    this.sessions.delete(guildId);
    clearTimeout(active.emptyTimer);
    await active.recorder.stop();
    active.tracks?.stop();
    active.connection.destroy();
    await this.backend.stopSession(active.id).catch((error: Error) => {
      console.error(`Could not stop session ${active.id}: ${error.message}`);
    });
    const seconds = (Date.now() - active.startedAt) / 1000;
    setTimeout(() => void this.postTranscript(active.id, active.label, active.textChannel), TRANSCRIPT_DELAY_MS);
    const summary = text.stopped(seconds, active.recorder.speakers.size, active.recorder.utterances);
    return active.tracks ? summary + text.podcastTracks(active.tracks.speakers) : summary;
  }

  async stopAll(): Promise<void> {
    await Promise.all([...this.sessions.keys()].map((guildId) => this.stop(guildId)));
  }

  private async status(interaction: ChatInputCommandInteraction<"cached">): Promise<void> {
    const active = this.sessions.get(interaction.guildId);
    if (!active) {
      await interaction.reply({ content: text.notRecording, flags: MessageFlags.Ephemeral });
      return;
    }
    const segments = await this.backend
      .getSession(active.id)
      .then((s) => s.segment_count)
      .catch(() => 0);
    await interaction.reply({
      content: text.status(
        (Date.now() - active.startedAt) / 1000,
        active.recorder.speakers.size,
        active.recorder.utterances,
        segments,
      ),
      flags: MessageFlags.Ephemeral,
    });
  }

  private async postLatestTranscript(interaction: ChatInputCommandInteraction<"cached">): Promise<void> {
    await interaction.deferReply();
    try {
      const latest = await this.backend.latestSession();
      if (!latest) {
        await interaction.editReply(text.noTranscript);
        return;
      }
      const transcript = await this.backend.transcriptText(latest.id);
      await interaction.editReply({
        content: text.transcript(latest.label ?? latest.id),
        files: [transcriptFile(latest.id, transcript)],
      });
    } catch (error) {
      await interaction.editReply(text.backendError((error as Error).message));
    }
  }

  private async postTranscript(
    sessionId: string,
    label: string | null,
    channel: SendableChannels | null,
  ): Promise<void> {
    if (!channel) return;
    try {
      const transcript = await this.backend.transcriptText(sessionId);
      await channel.send({
        content: text.transcript(label ?? sessionId),
        files: [transcriptFile(sessionId, transcript)],
      });
    } catch (error) {
      console.error(`Could not post transcript ${sessionId}: ${(error as Error).message}`);
    }
  }

  /** Reconnect after brief network drops (Discord moves calls between servers); give up otherwise. */
  private watchConnection(guildId: string, active: ActiveSession): void {
    active.connection.on(VoiceConnectionStatus.Disconnected, async () => {
      try {
        await Promise.race([
          entersState(active.connection, VoiceConnectionStatus.Signalling, 5_000),
          entersState(active.connection, VoiceConnectionStatus.Connecting, 5_000),
        ]);
      } catch {
        if (this.sessions.get(guildId) !== active) return;
        await this.stop(guildId);
        await active.textChannel?.send(text.connectionLost).catch(() => {});
      }
    });
  }

  /** Stop automatically when everyone has left the voice channel for a while. */
  onVoiceStateUpdate(oldState: VoiceState, newState: VoiceState): void {
    const guildId = newState.guild.id;
    const active = this.sessions.get(guildId);
    if (!active) return;
    if (oldState.channelId !== active.voiceChannelId && newState.channelId !== active.voiceChannelId) return;
    const channel = newState.guild.channels.cache.get(active.voiceChannelId);
    const people = channel?.isVoiceBased() ? channel.members.filter((m) => !m.user.bot).size : 0;
    clearTimeout(active.emptyTimer);
    if (people === 0) {
      active.emptyTimer = setTimeout(async () => {
        if (this.sessions.get(guildId) !== active) return;
        await this.stop(guildId);
        await active.textChannel?.send(text.emptyChannel).catch(() => {});
      }, this.config.emptyChannelMinutes * 60_000);
    }
  }

  private async displayName(guild: Guild, userId: string): Promise<string> {
    const cached = this.names.get(userId);
    if (cached) return cached;
    const member = await guild.members.fetch(userId).catch(() => null);
    const name = member?.displayName ?? userId;
    this.names.set(userId, name);
    return name;
  }
}

function transcriptFile(sessionId: string, transcript: string): AttachmentBuilder {
  return new AttachmentBuilder(Buffer.from(transcript, "utf8"), {
    name: `litterointi-${sessionId}.txt`,
  });
}
