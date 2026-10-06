// User-facing texts (Finnish: the table plays in Finnish).

export function formatDuration(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} h ${m} min` : `${m} min`;
}

export const text = {
  started: (voiceChannelId: string) =>
    [
      `🔴 **Nauhoitus alkoi** kanavalla <#${voiceChannelId}>.`,
      "Puhe muutetaan tekstiksi sessiomuistiinpanoja varten. Äänitiedostoja ei tallenneta.",
      "Et halua mukaan? Käytä `/optout`. Hahmon nimen saat litterointiin komennolla `/link`.",
      'Sääntökysymys: sano "Nethys, …" ja vastaus tulee Foundryyn.',
    ].join("\n"),
  stopped: (seconds: number, speakers: number, utterances: number) =>
    `⏹️ **Nauhoitus päättyi.** Kesto ${formatDuration(seconds)}, ${speakers} puhujaa, ` +
    `${utterances} puheenvuoroa. Litterointi tulee tähän kanavaan hetken kuluttua.`,
  status: (seconds: number, speakers: number, utterances: number, segments: number) =>
    `🔴 Nauhoitus käynnissä ${formatDuration(seconds)}: ${speakers} puhujaa, ` +
    `${utterances} puheenvuoroa lähetetty, ${segments} riviä litteroitu.`,
  notRecording: "Nauhoitus ei ole käynnissä. Aloita komennolla `/session start`.",
  alreadyRecording: "Nauhoitus on jo käynnissä. Lopeta se ensin komennolla `/session stop`.",
  joinVoiceFirst: "Liity ensin puhekanavalle, niin tiedän minne tulla.",
  connectFailed: "En päässyt puhekanavalle. Tarkista, että minulla on Connect-oikeus kanavaan.",
  connectionLost: "⚠️ Yhteys puhekanavaan katkesi, joten nauhoitus päättyi. Aloita uudelleen `/session start`.",
  emptyChannel: "Puhekanava on ollut tyhjä, joten lopetin nauhoituksen.",
  transcript: (label: string) => `📜 Litterointi: ${label}`,
  noTranscript: "Litterointia ei vielä ole.",
  linked: (name: string) => `Hahmosi on nyt **${name}**. Se näkyy litteroinnissa nimesi vieressä.`,
  unlinked: "Hahmon nimi poistettu.",
  optedOut: "Puhettasi ei enää nauhoiteta eikä litteroida. Palaa mukaan komennolla `/optin`.",
  optedIn: "Puheesi on taas mukana litteroinnissa.",
  backendError: (message: string) => `Taustapalvelu ei vastannut: ${message}`,
};

