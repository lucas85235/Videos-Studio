// Peças compartilhadas pelas páginas Estilos e Editor de estilo.

import React from 'react';
import { MOOD_LABELS, type StyleSpec } from '../../api';
import {
  CAMERA_LABELS,
  CAPTION_POSITION_LABELS,
  CAPTION_STYLE_LABELS,
  EFFECT_LABELS,
  FAMILY_LABELS,
  FORMAT_TYPE_LABELS,
  GRADE_LABELS,
  MUSIC_SOURCE_LABELS,
  STRUCTURE_LABELS,
  TEXT_ANIMATION_LABELS,
  THUMBNAIL_TEMPLATE_LABELS,
  TRANSITION_LABELS,
  label,
  langLabel,
  type LabelMap,
} from '../../labels';

/* ------------------------------------------------------------------ */
/* Fontes que já vêm no app (assets/fonts, servidas em /fonts)         */
/* ------------------------------------------------------------------ */

export const FONTS = ['Montserrat', 'Bebas Neue', 'Cinzel', 'Special Elite', 'VT323', 'Oswald', 'Anton', 'Noto Sans', 'IBM Plex Sans'];

// Montserrat e IBM Plex Sans já são registradas no index.css.
const EXTRA_FONT_FILES: Record<string, { file: string; weight: string }> = {
  'Bebas Neue': { file: 'BebasNeue-Regular.ttf', weight: '400' },
  Cinzel: { file: 'Cinzel-Variable.ttf', weight: '400 900' },
  'Special Elite': { file: 'SpecialElite-Regular.ttf', weight: '400' },
  VT323: { file: 'VT323-Regular.ttf', weight: '400' },
  Oswald: { file: 'Oswald-Variable.ttf', weight: '200 700' },
  Anton: { file: 'Anton-Regular.ttf', weight: '400' },
  'Noto Sans': { file: 'NotoSans-Variable.ttf', weight: '100 900' },
};

const FONT_FACES = Object.entries(EXTRA_FONT_FILES)
  .map(
    ([family, { file, weight }]) =>
      `@font-face{font-family:'${family}';src:url('/fonts/${file}') format('truetype');font-weight:${weight};font-style:normal;font-display:swap}`,
  )
  .join('\n');

/** Registra as fontes dos estilos (para as prévias). O React 19 deduplica pelo `href`. */
export const StyleFonts: React.FC = () => (
  <style href="vulto-fontes-estilos" precedence="default">
    {FONT_FACES}
  </style>
);

export const fontStack = (font: string | null | undefined) => `'${font || 'Montserrat'}', 'IBM Plex Sans', sans-serif`;

/* ------------------------------------------------------------------ */
/* Ids e comparação                                                    */
/* ------------------------------------------------------------------ */

export const STYLE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,80}$/;

export const slugify = (text: string) =>
  text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/g, '');

/** Id livre sugerido a partir do nome: "nome-meu", "nome-meu-2"… (sem colidir com presets nem com seus estilos). */
export const suggestStyleId = (name: string, taken: Iterable<string>, suffix = 'meu') => {
  const used = new Set(taken);
  let base = slugify(name);
  if (base.length < 2) base = 'estilo';
  base = suffix && !base.endsWith(`-${suffix}`) ? `${base}-${suffix}` : base;
  if (!used.has(base)) return base;
  for (let i = 2; i < 1000; i += 1) {
    const candidate = `${base}-${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${base}-${Date.now().toString(36)}`;
};

/** Validação do id de um estilo novo (mesma regra do backend). */
export const styleIdError = (id: string, taken: Set<string>) => {
  if (!id) return 'Informe um id.';
  if (!STYLE_ID_PATTERN.test(id)) return 'Use de 2 a 81 letras minúsculas, números ou hífens, começando por letra ou número.';
  if (taken.has(id)) return 'Já existe um estilo com esse id.';
  return null;
};

/** JSON com as chaves em ordem, para comparar dois objetos sem depender da ordem. */
export const canonicalJson = (value: unknown): string =>
  JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  );

/* ------------------------------------------------------------------ */
/* Formatação                                                          */
/* ------------------------------------------------------------------ */

const NUMBER = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });
export const formatNumber = (n: number) => (Number.isFinite(n) ? NUMBER.format(n) : '–');
export const formatFixed = (n: number, digits = 2) => (Number.isFinite(n) ? n.toFixed(digits).replace('.', ',') : '–');

/** Duração aproximada: "~55 s", "~10 min". */
export const formatDuration = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds <= 0) return '–';
  if (seconds < 90) return `~${Math.round(seconds)} s`;
  return `~${Math.round(seconds / 60)} min`;
};

/** "Curto · 9:16" / "Longo · 16:9". */
export const formatShort = (format: StyleSpec['format']) => `${format.type === 'short' ? 'Curto' : 'Longo'} · ${format.aspect}`;
export const formatLong = (format: StyleSpec['format']) => label(FORMAT_TYPE_LABELS, format.type);

export const familyLabel = (id: string, metaLabel?: string) => FAMILY_LABELS[id] ?? metaLabel ?? id;

/* ------------------------------------------------------------------ */
/* Clima da trilha (o backend reduz o texto a um de 7 climas)          */
/* ------------------------------------------------------------------ */

/** Mesma regra de `mood_key` no backend (media/audio.py). */
export const moodKey = (mood: string | null | undefined) => {
  const text = (mood ?? '').toLowerCase();
  const rules: [string, string[]][] = [
    ['horror', ['horror', 'terror', 'medo', 'assust']],
    ['tension', ['tension', 'tensão', 'tensao', 'suspense', 'thriller']],
    ['mystery', ['myster', 'mistér', 'mister', 'noir', 'investiga']],
    ['epic', ['epic', 'épic', 'epico', 'heroic', 'grandios']],
    ['pulse', ['pulse', 'pulso', 'batida', 'heartbeat', 'ritm']],
    ['calm', ['calm', 'calma', 'relax', 'sereno', 'lofi', 'lo-fi']],
  ];
  for (const [key, words] of rules) if (words.some((w) => text.includes(w))) return key;
  return 'drone';
};

const capitalize = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const moodLabel = (mood: string | null | undefined) => capitalize(MOOD_LABELS[moodKey(mood)] ?? moodKey(mood));

/** Valores que o editor oferece (os mesmos de antes); o texto salvo não muda. */
export const MOOD_OPTIONS = ['dark ambient drone', 'tension', 'horror', 'mystery', 'epic', 'pulse', 'calm'];

/* ------------------------------------------------------------------ */
/* Rótulos dos campos do estilo (para o resultado de "criar por texto") */
/* ------------------------------------------------------------------ */

const SECTION_LABELS: [string, string][] = [
  ['edit.captions', 'Legendas'],
  ['edit.text_cards', 'Textos na tela'],
  ['edit.effects', 'Efeitos'],
  ['edit.camera', 'Câmera'],
  ['edit.transitions', 'Transições'],
  ['edit', 'Ritmo e câmera'],
  ['format', 'Formato'],
  ['script', 'Roteiro'],
  ['voice', 'Voz'],
  ['art', 'Arte'],
  ['audio', 'Áudio'],
  ['thumbnail', 'Thumbnail'],
  ['languages', 'Idiomas'],
  ['family', 'Formato'],
  ['families_mix', 'Formato'],
  ['structure', 'Formato'],
  ['tags', 'Formato'],
];

export const sectionOfPath = (path: string) => SECTION_LABELS.find(([prefix]) => path === prefix || path.startsWith(`${prefix}.`))?.[1] ?? 'Outros';

const FIELD_LABELS: Record<string, string> = {
  name: 'Nome',
  description: 'Descrição',
  family: 'Família visual',
  families_mix: 'Mistura de famílias',
  structure: 'Estrutura',
  tags: 'Etiquetas',
  'format.type': 'Tipo de vídeo',
  'format.aspect': 'Proporção',
  'format.resolution': 'Resolução',
  'format.fps': 'Quadros por segundo',
  'format.duration': 'Duração',
  'format.duration.target_s': 'Duração alvo',
  'format.duration.min_s': 'Duração mínima',
  'format.duration.max_s': 'Duração máxima',
  'languages.primary': 'Idioma principal',
  'languages.outputs': 'Idiomas de saída',
  'languages.delivery': 'Entrega dos idiomas',
  'script.tone': 'Tom do roteiro',
  'script.structure': 'Etapas do roteiro',
  'script.hook_max_s': 'Duração máxima do gancho',
  'script.fact_check': 'Checagem de fatos',
  'script.cta': 'Chamada final',
  'script.banned_phrases': 'Frases proibidas',
  'voice.voice': 'Voz',
  'voice.voice_profile': 'Perfil de voz',
  'voice.chain': 'Provedores de voz',
  'voice.style_prompt': 'Interpretação da voz',
  'voice.speed': 'Velocidade',
  'voice.gap_s': 'Pausa entre falas',
  'art.bible': 'Bíblia de arte',
  'art.banned': 'Evitar nas imagens',
  'art.photoreal': 'Arte fotorrealista',
  'art.image_chain': 'Provedores de imagem',
  'art.thumbnail_chain': 'Provedores da thumbnail',
  'edit.shot_seconds': 'Duração das cenas',
  'edit.shot_seconds.min': 'Cena mínima',
  'edit.shot_seconds.max': 'Cena máxima',
  'edit.camera': 'Movimentos de câmera',
  'edit.transitions': 'Transições',
  'edit.transition_seconds': 'Duração da transição',
  'edit.effects': 'Efeitos',
  'edit.zoom_strength': 'Força do zoom',
  'edit.progress_bar': 'Barra de progresso',
  'edit.progress_color': 'Cor da barra de progresso',
  'edit.watermark': "Marca d'água",
  'edit.captions': 'Legendas',
  'edit.captions.style': 'Estilo',
  'edit.captions.font': 'Fonte',
  'edit.captions.weight': 'Peso da fonte',
  'edit.captions.size': 'Tamanho',
  'edit.captions.color': 'Cor',
  'edit.captions.highlight': 'Cor de destaque',
  'edit.captions.stroke': 'Contorno',
  'edit.captions.stroke_color': 'Cor do contorno',
  'edit.captions.position': 'Posição',
  'edit.captions.max_words_per_line': 'Palavras por linha',
  'edit.captions.uppercase': 'Maiúsculas',
  'edit.captions.background': 'Fundo',
  'edit.text_cards': 'Cartões de texto',
  'edit.text_cards.font': 'Fonte dos cartões',
  'edit.text_cards.color': 'Cor do texto',
  'edit.text_cards.accent': 'Cor de destaque',
  'edit.text_cards.background': 'Fundo',
  'edit.text_cards.animation': 'Animação',
  'audio.music': 'Trilha',
  'audio.music.source': 'Fonte da trilha',
  'audio.music.mood': 'Clima da trilha',
  'audio.music.volume_db': 'Volume da trilha',
  'audio.music.ducking': 'Abaixar a trilha na narração',
  'audio.sfx': 'Efeitos sonoros',
  'audio.sfx.auto': 'Efeitos sonoros automáticos',
  'audio.sfx.pack': 'Pacote de efeitos sonoros',
  'audio.sfx.volume_db': 'Volume dos efeitos sonoros',
  'audio.loudness_lufs': 'Volume final',
  'thumbnail.template': 'Modelo',
  'thumbnail.variations': 'Variações',
  'thumbnail.per_language': 'Uma por idioma',
  'thumbnail.font': 'Fonte',
  'thumbnail.color': 'Cor do texto',
  'thumbnail.accent': 'Cor de destaque',
};

const EFFECT_FIELD_LABELS: Record<string, string> = {
  amount: 'Intensidade',
  preset: 'Gradação',
  type: 'Tipo',
  params: 'Ajustes finos',
};

/**
 * Rótulo legível de um campo do estilo. Para efeitos, usa o nome do efeito em `style` (se houver);
 * para caminhos desconhecidos, cai no nome da seção (nunca mostra o id técnico).
 */
export const fieldLabel = (path: string, style?: StyleSpec | null) => {
  if (FIELD_LABELS[path]) return FIELD_LABELS[path];
  // Peso de um movimento/transição: edit.camera.pan_left → "Câmera: Panorâmica para a esquerda".
  const m = /^edit\.(camera|transitions)\.(.+)$/.exec(path);
  if (m) return `${m[1] === 'camera' ? 'Câmera' : 'Transição'}: ${label(m[1] === 'camera' ? CAMERA_LABELS : TRANSITION_LABELS, m[2])}`;
  const e = /^edit\.effects\.(\d+)(?:\.([^.]+))?/.exec(path);
  if (e) {
    const index = Number(e[1]);
    const type = style?.edit?.effects?.[index]?.type;
    const name = typeof type === 'string' && type ? label(EFFECT_LABELS, type) : `Efeito ${index + 1}`;
    if (!e[2]) return name;
    return `${name}: ${EFFECT_FIELD_LABELS[e[2]] ?? 'ajuste'}`;
  }
  return sectionOfPath(path);
};

const VALUE_LABELS: Record<string, LabelMap> = {
  structure: STRUCTURE_LABELS,
  family: FAMILY_LABELS,
  'format.type': FORMAT_TYPE_LABELS,
  'edit.captions.style': CAPTION_STYLE_LABELS,
  'edit.captions.position': CAPTION_POSITION_LABELS,
  'edit.text_cards.animation': TEXT_ANIMATION_LABELS,
  'audio.music.source': MUSIC_SOURCE_LABELS,
  'thumbnail.template': THUMBNAIL_TEMPLATE_LABELS,
};

const UNITS: Record<string, string> = {
  'format.duration.target_s': ' s',
  'format.duration.min_s': ' s',
  'format.duration.max_s': ' s',
  'script.hook_max_s': ' s',
  'voice.gap_s': ' s',
  'edit.shot_seconds.min': ' s',
  'edit.shot_seconds.max': ' s',
  'edit.transition_seconds': ' s',
  'edit.captions.size': ' px',
  'edit.captions.stroke': ' px',
  'audio.music.volume_db': ' dB',
  'audio.sfx.volume_db': ' dB',
  'audio.loudness_lufs': ' LUFS',
};

export const HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

type Effect = { type?: string; amount?: number; preset?: string | null };

const effectText = (e: Effect) =>
  `${label(EFFECT_LABELS, e.type ?? '')}${e.type === 'grade' && e.preset ? ` (${label(GRADE_LABELS, e.preset)})` : ''}${
    typeof e.amount === 'number' ? ` ${formatFixed(e.amount)}` : ''
  }`;

const weightsText = (map: LabelMap, value: Record<string, unknown>) =>
  Object.entries(value)
    .map(([k, v]) => `${label(map, k)} ${typeof v === 'number' ? formatNumber(v) : String(v)}`)
    .join(', ') || 'nenhum';

/** Texto legível para um valor de um campo do estilo. */
export const formatValue = (path: string, value: unknown): string => {
  if (value === null || value === undefined || value === '') return 'vazio';
  if (typeof value === 'boolean') return value ? 'Sim' : 'Não';
  if (typeof value === 'number') {
    if (path === 'voice.speed') return `${formatFixed(value)}x`;
    if (path === 'edit.zoom_strength') return `${Math.round(value * 100)}%`;
    return `${formatNumber(value)}${UNITS[path] ?? ''}`;
  }
  if (typeof value === 'string') {
    if (path === 'audio.music.mood') return moodLabel(value);
    if (path === 'languages.primary') return langLabel(value);
    if (path.startsWith('edit.effects') && path.endsWith('preset')) return label(GRADE_LABELS, value);
    const map = VALUE_LABELS[path];
    return map ? label(map, value) : value;
  }
  if (Array.isArray(value)) {
    if (path === 'edit.effects') return value.map((e) => effectText(e as Effect)).join(', ') || 'nenhum';
    if (path === 'families_mix') return value.map((f) => label(FAMILY_LABELS, String(f))).join(', ');
    if (path === 'languages.outputs') return value.map((l) => langLabel(String(l))).join(', ');
    return value.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ') || 'nenhum';
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (path === 'edit.camera') return weightsText(CAMERA_LABELS, obj);
    if (path === 'edit.transitions') return weightsText(TRANSITION_LABELS, obj);
    if (/^edit\.effects\.\d+$/.test(path)) return effectText(obj as Effect);
    return Object.entries(obj)
      .map(([k, v]) => `${fieldLabel(`${path}.${k}`)}: ${formatValue(`${path}.${k}`, v)}`)
      .join(' · ');
  }
  return String(value);
};

/** Cores hexadecimais dentro de um valor (para mostrar amostras). */
export const colorsIn = (value: unknown): string[] => {
  if (typeof value === 'string') return HEX_COLOR.test(value) ? [value] : [];
  if (value && typeof value === 'object') return Object.values(value as Record<string, unknown>).flatMap(colorsIn).slice(0, 4);
  return [];
};

/* ------------------------------------------------------------------ */
/* Miniatura do estilo (cores e fonte)                                 */
/* ------------------------------------------------------------------ */

export const ColorSwatch: React.FC<{ color: string; className?: string }> = ({ color, className = '' }) => (
  <span aria-hidden="true" className={`inline-block h-3.5 w-3.5 shrink-0 rounded-sm border border-line-strong ${className}`} style={{ background: color }} />
);

/** Quadro no formato do vídeo, com o fundo dos cartões e uma legenda na fonte, cores e contorno do estilo. Decorativo. */
export const StyleThumb: React.FC<{ style: StyleSpec; className?: string }> = ({ style, className = '' }) => {
  const vertical = style.format.type === 'short';
  const captions = style.edit.captions;
  const words = ['Assim', 'fica'];
  const shown = captions.uppercase ? words.map((w) => w.toUpperCase()) : words;
  // Mesmo contorno da legenda no vídeo, em escala (texto de 12 px aqui contra `size` px no vídeo).
  const strokePx = captions.stroke > 0 && captions.size > 0 ? Math.max(0.75, (captions.stroke / captions.size) * 12) : 0;
  const captionStyle: React.CSSProperties = {
    fontFamily: fontStack(captions.font),
    fontWeight: captions.weight,
    WebkitTextStroke: strokePx ? `${strokePx}px ${captions.stroke_color}` : undefined,
    paintOrder: 'stroke fill',
    background: captions.background ?? undefined,
  };
  // Moldura fixa para os dois formatos: o texto ao lado começa sempre no mesmo x.
  return (
    <div aria-hidden="true" className={`flex h-[100px] w-24 shrink-0 items-center justify-center self-start ${className}`}>
      <div
        className={`flex flex-col items-center justify-center overflow-hidden rounded-md border border-line-strong px-1 text-center leading-none ${
          vertical ? 'aspect-[9/16] h-full' : 'aspect-video w-full'
        }`}
        style={{ background: style.edit.text_cards.background }}
      >
        <span className="block max-w-full truncate px-px text-xs" style={{ ...captionStyle, color: captions.color }}>
          {shown[0]}
        </span>
        <span className="mt-0.5 block max-w-full truncate px-px text-xs" style={{ ...captionStyle, color: captions.highlight }}>
          {shown[1]}
        </span>
        <span className="mt-1.5 block h-0.5 w-1/2 rounded-full" style={{ background: style.edit.text_cards.accent }} />
      </div>
    </div>
  );
};
