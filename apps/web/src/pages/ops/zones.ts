// Fusos horários com nome legível ("São Paulo (GMT-3)") para Canais e Agenda.

/** Fuso deste computador (padrão: São Paulo, se o navegador não informar). */
export const browserZone: string = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/Sao_Paulo';
  } catch {
    return 'America/Sao_Paulo';
  }
})();

const CITY_LABELS: Record<string, string> = {
  'America/Sao_Paulo': 'São Paulo',
  'America/Manaus': 'Manaus',
  'America/Fortaleza': 'Fortaleza',
  'America/Mexico_City': 'Cidade do México',
  'America/New_York': 'Nova York',
  'America/Chicago': 'Chicago',
  'America/Denver': 'Denver',
  'America/Los_Angeles': 'Los Angeles',
  'Europe/Lisbon': 'Lisboa',
  'Europe/London': 'Londres',
  'Europe/Madrid': 'Madri',
  'Europe/Berlin': 'Berlim',
  'Asia/Kolkata': 'Índia (Calcutá)',
  'Asia/Tokyo': 'Tóquio',
  UTC: 'UTC',
};

/** Nome da cidade do fuso (America/Sao_Paulo → São Paulo). */
export const zoneCity = (zone: string): string => CITY_LABELS[zone] ?? (zone.split('/').pop() ?? zone).replace(/_/g, ' ');

/** Deslocamento do fuso em relação a UTC agora ("GMT-3"), ou '' se o navegador não souber. */
export const zoneOffset = (zone: string, at: Date = new Date()): string => {
  try {
    const part = new Intl.DateTimeFormat('pt-BR', { timeZone: zone, timeZoneName: 'shortOffset' })
      .formatToParts(at)
      .find((p) => p.type === 'timeZoneName');
    return part?.value === 'GMT' ? 'GMT+0' : (part?.value ?? '');
  } catch {
    return '';
  }
};

/** Rótulo legível: "São Paulo (GMT-3)". */
export const zoneLabel = (zone: string): string => {
  if (zone === 'UTC') return 'UTC (GMT+0)';
  const offset = zoneOffset(zone);
  return offset ? `${zoneCity(zone)} (${offset})` : zoneCity(zone);
};

/** O fuso tem o mesmo horário que este computador agora? */
export const sameOffsetAsBrowser = (zone: string): boolean => zone === browserZone || zoneOffset(zone) === zoneOffset(browserZone);

const DAY_MS = 86_400_000;

const dayStamp = (d: Date, zone?: string) => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  return Date.parse(`${parts}T00:00:00Z`);
};

/**
 * Rótulo do dia de um horário: "Hoje", "Amanhã" ou "sexta, 14 de nov." (no fuso indicado, ou no do computador).
 */
export const dayLabel = (iso: string, zone?: string): string => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  try {
    const diff = Math.round((dayStamp(date, zone) - dayStamp(new Date(), zone)) / DAY_MS);
    if (diff === 0) return 'Hoje';
    if (diff === 1) return 'Amanhã';
    if (diff === -1) return 'Ontem';
    const text = date.toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long', timeZone: zone });
    return text.charAt(0).toUpperCase() + text.slice(1);
  } catch {
    return date.toLocaleDateString('pt-BR');
  }
};

/** Hora curta ("18:00") no fuso indicado, ou no do computador. */
export const timeOf = (iso: string, zone?: string): string => {
  try {
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: zone });
  } catch {
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  }
};

/** Data e hora curtas ("sex., 14/11 18:00") no fuso indicado (com proteção contra fuso inválido). */
export const whenIn = (iso: string, zone?: string): string => {
  const opts: Intl.DateTimeFormatOptions = { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' };
  try {
    return new Date(iso).toLocaleString('pt-BR', { ...opts, timeZone: zone });
  } catch {
    return new Date(iso).toLocaleString('pt-BR', opts);
  }
};
