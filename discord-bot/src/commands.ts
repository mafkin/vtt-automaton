import { SlashCommandBuilder } from "discord.js";

export const commands = [
  new SlashCommandBuilder()
    .setName("session")
    .setDescription("Pelisession nauhoitus ja litterointi")
    .addSubcommand((s) =>
      s
        .setName("start")
        .setDescription("Aloita nauhoitus puhekanavalla, jolla olet")
        .addStringOption((o) =>
          o.setName("label").setDescription("Session nimi, esim. 'Session 12'").setMaxLength(100),
        ),
    )
    .addSubcommand((s) => s.setName("stop").setDescription("Lopeta nauhoitus ja julkaise litterointi"))
    .addSubcommand((s) => s.setName("status").setDescription("Näytä nauhoituksen tila"))
    .addSubcommand((s) =>
      s.setName("transcript").setDescription("Julkaise viimeisimmän session litterointi"),
    ),
  new SlashCommandBuilder()
    .setName("link")
    .setDescription("Kerro hahmosi nimi litterointia varten (tyhjä poistaa)")
    .addStringOption((o) => o.setName("character").setDescription("Hahmon nimi").setMaxLength(60)),
  new SlashCommandBuilder().setName("optout").setDescription("Älä nauhoita puhettani"),
  new SlashCommandBuilder().setName("optin").setDescription("Nauhoita puheeni taas"),
].map((command) => command.toJSON());
