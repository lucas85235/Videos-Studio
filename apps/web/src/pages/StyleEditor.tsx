import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AudioLines,
  Braces,
  Captions,
  CircleCheck,
  CircleDot,
  CircleX,
  Copy,
  FileText,
  History,
  Image,
  LayoutTemplate,
  Lock,
  Music,
  RectangleHorizontal,
  RectangleVertical,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  TriangleAlert,
  Type,
  Video,
} from 'lucide-react';
import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api, type Channel, type StyleItem, type StyleSpec } from '../api';
import {
  CAMERA_LABELS,
  CAPTION_POSITION_LABELS,
  CAPTION_STYLE_LABELS,
  MUSIC_SOURCE_LABELS,
  STRUCTURE_LABELS,
  TEXT_ANIMATION_LABELS,
  THUMBNAIL_TEMPLATE_LABELS,
  TRANSITION_LABELS,
  TREATMENT_LABELS,
  label,
  langLabel,
} from '../labels';
import { readLocal, useProviders, writeLocal } from '../shell/data';
import {
  Badge,
  Button,
  Checkbox,
  Empty,
  ErrorBox,
  Field,
  Input,
  Page,
  PageHeader,
  Section,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  buttonClass,
  tabPanelProps,
  useConfirm,
  useToast,
} from '../ui';
import { useVoiceCatalog, VoicePicker } from '../voices';
import { ReferenceOrigins } from './references/ReferenceStyle';
import {
  CaptionPreview,
  ColorField,
  EffectsEditor,
  RangeField,
  SectionNav,
  Segmented,
  TextCardPreview,
  ThumbnailPreview,
  WeightsEditor,
  sectionDomId,
  type EditorSection,
} from './styles/editorParts';
import {
  FONTS,
  MOOD_OPTIONS,
  StyleFonts,
  canonicalJson,
  formatDuration,
  formatFixed,
  formatShort,
  moodLabel,
  styleIdError,
  suggestStyleId,
} from './styles/shared';

type Detail = { style: StyleSpec; preset: boolean; warnings: string[] };

const CAMERA = ['ken_burns_in', 'ken_burns_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down', 'punch_zoom', 'drift', 'static', 'parallax'];
const TREATMENTS = ['plain', 'whiteboard', 'collage', 'screen'];
const TRANSITIONS = ['cut', 'flash', 'fade_black', 'glitch', 'light_leak', 'zoom_blur', 'film_burn', 'static_burst'];
const STRUCTURES = ['none', 'top10', 'iceberg', 'tierlist', 'quiz', 'timeline', 'history_in_n', 'pov', 'series', 'would_you_rather'];
const ANIMATIONS = ['fade_up', 'typewriter', 'pop', 'slide', 'flicker'];
const TEMPLATES = ['texto-grande-esquerda', 'texto-centro', 'texto-baixo', 'sem-texto'];

const SECTIONS: EditorSection[] = [
  { id: 'formato', label: 'Formato e roteiro', icon: FileText },
  { id: 'voz', label: 'Voz', icon: AudioLines },
  { id: 'arte', label: 'Arte', icon: Image },
  { id: 'ritmo', label: 'Ritmo e câmera', icon: Video },
  { id: 'efeitos', label: 'Efeitos', icon: Sparkles },
  { id: 'legendas', label: 'Legendas', icon: Captions },
  { id: 'textos', label: 'Textos na tela', icon: Type },
  { id: 'audio', label: 'Áudio', icon: Music },
  { id: 'thumbnail', label: 'Thumbnail', icon: LayoutTemplate },
];

/** Opções de um select com o valor atual incluído (mesmo que não esteja na lista conhecida). */
const withCurrent = (options: string[], current: string | null | undefined) => (current && !options.includes(current) ? [...options, current] : options);

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

/** Rótulos de campos desabilitados (preset): a regra do index.css perde para `text-fg` das utilities; esta fica fora das camadas. */
const READONLY_CSS = 'fieldset[data-somente-leitura]:disabled [data-field-label]{color:var(--color-subtle)}';

/** Valores de um preset (campos desabilitados) com contraste de texto normal: a tela existe para ler o preset; o aviso já diz que é só leitura. */
const READONLY_VALUES =
  '[&_:is(input,textarea,select):disabled]:text-fg [&_:is(input,textarea,select):disabled]:opacity-100 [&_:is(input,textarea,select):disabled]:cursor-default';

/* ------------------------------------------------------------------ */
/* Validação do JSON (ao vivo)                                         */
/* ------------------------------------------------------------------ */

const isObj = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);

const getPath = (obj: unknown, path: string) => path.split('.').reduce<unknown>((cur, key) => (isObj(cur) ? cur[key] : undefined), obj);

/** Objetos cujas chaves são escolhas do usuário (pesos, mapas): não recebem chaves da versão salva. */
const OPEN_MAPS = new Set(['edit.camera', 'edit.transitions', 'edit.treatments', 'art.sources_mix', 'script.speakers', 'script.words_per_minute']);

/**
 * Completa as chaves ausentes com a versão salva (como o deep_merge do backend: listas e valores presentes ficam como estão).
 * Devolve o objeto completo e os caminhos que foram completados.
 */
const fillMissing = (value: unknown, base: unknown, path = '', filled: string[] = []): unknown => {
  if (!isObj(value) || !isObj(base) || OPEN_MAPS.has(path)) return value;
  const out: Record<string, unknown> = { ...value };
  for (const [key, b] of Object.entries(base)) {
    const sub = path ? `${path}.${key}` : key;
    if (key in value) out[key] = fillMissing(value[key], b, sub, filled);
    else {
      out[key] = structuredClone(b);
      filled.push(sub);
    }
  }
  return out;
};

type Kind = 'object' | 'text' | 'textOrNull' | 'number' | 'bool' | 'textList' | 'list' | 'weights';

/** Campos que o formulário e as prévias leem, na ordem (o objeto antes das folhas). */
const LEAVES: [string, Kind][] = [
  ['id', 'text'],
  ['name', 'text'],
  ['description', 'text'],
  ['family', 'text'],
  ['structure', 'text'],
  ['families_mix', 'textList'],
  ['tags', 'textList'],
  ['format', 'object'],
  ['format.aspect', 'text'],
  ['format.duration', 'object'],
  ['format.duration.target_s', 'number'],
  ['languages', 'object'],
  ['languages.primary', 'text'],
  ['languages.outputs', 'textList'],
  ['script', 'object'],
  ['script.tone', 'text'],
  ['script.structure', 'textList'],
  ['script.cta', 'textOrNull'],
  ['voice', 'object'],
  ['voice.chain', 'textList'],
  ['voice.voice', 'textOrNull'],
  ['voice.style_prompt', 'textOrNull'],
  ['voice.speed', 'number'],
  ['art', 'object'],
  ['art.bible', 'text'],
  ['art.banned', 'textList'],
  ['art.photoreal', 'bool'],
  ['edit', 'object'],
  ['edit.shot_seconds', 'object'],
  ['edit.shot_seconds.min', 'number'],
  ['edit.shot_seconds.max', 'number'],
  ['edit.camera', 'weights'],
  ['edit.transitions', 'weights'],
  ['edit.transition_seconds', 'number'],
  ['edit.zoom_strength', 'number'],
  ['edit.effects', 'list'],
  ['edit.progress_bar', 'bool'],
  ['edit.progress_color', 'text'],
  ['edit.captions', 'object'],
  ['edit.captions.style', 'text'],
  ['edit.captions.font', 'text'],
  ['edit.captions.weight', 'number'],
  ['edit.captions.size', 'number'],
  ['edit.captions.color', 'text'],
  ['edit.captions.highlight', 'text'],
  ['edit.captions.stroke', 'number'],
  ['edit.captions.stroke_color', 'text'],
  ['edit.captions.position', 'text'],
  ['edit.captions.max_words_per_line', 'number'],
  ['edit.captions.uppercase', 'bool'],
  ['edit.captions.background', 'textOrNull'],
  ['edit.text_cards', 'object'],
  ['edit.text_cards.font', 'text'],
  ['edit.text_cards.color', 'text'],
  ['edit.text_cards.accent', 'text'],
  ['edit.text_cards.background', 'text'],
  ['edit.text_cards.animation', 'text'],
  ['audio', 'object'],
  ['audio.music', 'object'],
  ['audio.music.source', 'text'],
  ['audio.music.mood', 'text'],
  ['audio.music.volume_db', 'number'],
  ['audio.sfx', 'object'],
  ['audio.sfx.auto', 'bool'],
  ['thumbnail', 'object'],
  ['thumbnail.template', 'text'],
  ['thumbnail.font', 'text'],
  ['thumbnail.color', 'text'],
  ['thumbnail.accent', 'text'],
  ['thumbnail.variations', 'number'],
  ['thumbnail.per_language', 'bool'],
];

const isNumber = (v: unknown) => typeof v === 'number' && Number.isFinite(v);

const KIND_OK: Record<Kind, (v: unknown) => boolean> = {
  object: isObj,
  text: (v) => typeof v === 'string',
  textOrNull: (v) => v === null || typeof v === 'string',
  number: isNumber,
  bool: (v) => typeof v === 'boolean',
  textList: (v) => Array.isArray(v) && v.every((x) => typeof x === 'string'),
  list: Array.isArray,
  weights: (v) => isObj(v) && Object.values(v).every(isNumber),
};

const KIND_MESSAGE: Record<Kind, (path: string) => string> = {
  object: (p) => `O campo “${p}” precisa ser um objeto (entre chaves).`,
  text: (p) => `O campo “${p}” precisa ser um texto (entre aspas).`,
  textOrNull: (p) => `O campo “${p}” precisa ser um texto (entre aspas) ou null.`,
  number: (p) => `O campo “${p}” precisa ser um número (sem aspas).`,
  bool: (p) => `O campo “${p}” precisa ser true ou false.`,
  textList: (p) => `O campo “${p}” precisa ser uma lista de textos, como ["a", "b"].`,
  list: (p) => `O campo “${p}” precisa ser uma lista (entre colchetes).`,
  weights: (p) => `O campo “${p}” precisa ser um objeto com pesos numéricos, como {"cut": 3}.`,
};

/** Primeiro problema de tipo nos campos que o formulário lê (o estilo já completado). */
const leafError = (style: unknown): { message: string; path: string | null } | null => {
  for (const [path, kind] of LEAVES) {
    if (!KIND_OK[kind](getPath(style, path))) return { message: KIND_MESSAGE[kind](path), path };
  }
  const type = getPath(style, 'format.type');
  if (type !== 'short' && type !== 'long') return { message: 'O campo “format.type” precisa ser "short" (curto) ou "long" (longo).', path: 'format.type' };
  const resolution = getPath(style, 'format.resolution');
  if (typeof resolution !== 'string' || !/^\d+x\d+$/.test(resolution))
    return { message: 'O campo “format.resolution” precisa ser um texto como "1080x1920".', path: 'format.resolution' };
  const effects = getPath(style, 'edit.effects') as unknown[];
  for (let i = 0; i < effects.length; i += 1) {
    const e = effects[i];
    if (!isObj(e) || typeof e.type !== 'string' || !e.type || !isNumber(e.amount) || !(e.preset == null || typeof e.preset === 'string'))
      return { message: `O efeito ${i + 1} de “edit.effects” precisa ser um objeto com "type" (texto) e "amount" (número entre 0 e 1).`, path: null };
  }
  return null;
};

/** Efeitos sem intensidade recebem a mesma do backend (0,5). */
const fillEffects = (style: unknown) => {
  const effects = getPath(style, 'edit.effects');
  if (!Array.isArray(effects)) return;
  effects.forEach((e, i) => {
    if (isObj(e) && e.amount === undefined) effects[i] = { ...e, amount: 0.5 };
  });
};

type JsonCheck =
  | { ok: true; value: StyleSpec; filled: string[] }
  | { ok: false; message: string; offset: number | null };

const offsetOf = (text: string, line: number, column: number) => {
  const lines = text.split('\n');
  let offset = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i += 1) offset += lines[i].length + 1;
  return offset + Math.max(0, column - 1);
};

const lineColumnOf = (text: string, offset: number) => {
  const before = text.slice(0, offset).split('\n');
  return { line: before.length, column: before[before.length - 1].length + 1 };
};

/** Posição da chave no texto, quando ela aparece uma vez só (para o botão "Ir para o erro"). */
const keyOffset = (text: string, path: string | null) => {
  if (!path) return null;
  const key = path.split('.').pop() ?? path;
  const matches = [...text.matchAll(new RegExp(`"${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"\\s*:`, 'g'))];
  return matches.length === 1 ? (matches[0].index ?? null) : null;
};

const checkStyleJson = (text: string, baseline: StyleSpec): JsonCheck => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    const lc = /line (\d+) column (\d+)/i.exec(raw);
    const pos = /position (\d+)/i.exec(raw);
    let offset: number | null = null;
    if (lc) offset = offsetOf(text, Number(lc[1]), Number(lc[2]));
    else if (pos) offset = Number(pos[1]);
    if (offset === null && /end of (json )?(input|data)/i.test(raw)) offset = text.length;
    if (offset === null) return { ok: false, message: 'JSON inválido: confira vírgulas, aspas, chaves e colchetes.', offset: null };
    const { line, column } = lineColumnOf(text, offset);
    const end = offset >= text.trimEnd().length;
    return {
      ok: false,
      message: end
        ? 'O JSON terminou antes da hora: falta fechar alguma chave ou colchete.'
        : `JSON inválido na linha ${line}, coluna ${column}. Confira vírgulas, aspas, chaves e colchetes por perto.`,
      offset,
    };
  }
  if (!isObj(parsed)) return { ok: false, message: 'O estilo precisa ser um objeto JSON (entre chaves).', offset: null };
  const filled: string[] = [];
  const value = fillMissing(parsed, baseline, '', filled);
  fillEffects(value);
  const problem = leafError(value);
  if (problem) return { ok: false, message: problem.message, offset: keyOffset(text, problem.path) };
  return { ok: true, value: value as StyleSpec, filled };
};

/* ------------------------------------------------------------------ */
/* Rascunho local (sobrevive a Voltar do navegador, recarga e queda)   */
/* ------------------------------------------------------------------ */

type Draft = { style: StyleSpec; savedAt: number; baseVersion: number | null };

const draftKeyOf = (styleId: string) => `vulto.rascunhoEstilo.${styleId}`;
const isDraft = (v: unknown): v is Draft => isObj(v) && isObj(v.style) && isNumber(v.savedAt);
const removeLocal = (key: string) => {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* sem armazenamento */
  }
};

/* ------------------------------------------------------------------ */
/* Largura da tela                                                     */
/* ------------------------------------------------------------------ */

const useMediaQuery = (query: string) =>
  useSyncExternalStore(
    (onChange) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => undefined;
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    },
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : true),
    () => true,
  );

/** Altura de um elemento, atualizada quando ele muda de tamanho. */
const useHeight = (getEl: () => Element | null | undefined, deps: React.DependencyList) => {
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => {
    const el = getEl();
    if (!el) return;
    const measure = () => setHeight(Math.round(el.getBoundingClientRect().height));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, deps);
  return height;
};

/* ------------------------------------------------------------------ */
/* Campos com rascunho local (número e lista separada por vírgulas)    */
/* ------------------------------------------------------------------ */

const NumberField: React.FC<{
  label: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
  min?: number;
  max?: number;
  hint?: React.ReactNode;
  error?: string | null;
}> = ({ label: text, value, onChange, step = 1, min, max, hint, error }) => {
  const [draft, setDraft] = useState(String(value));
  // Só reescreve o rascunho quando ele não representa mais o valor (ex.: mudou pelo JSON). Assim "1.0" não vira "1" ao digitar.
  useEffect(() => setDraft((d) => (d.trim() !== '' && Number(d.replace(',', '.')) === value ? d : String(value))), [value]);
  const n = Number(draft.replace(',', '.'));
  const outOfRange = draft.trim() !== '' && Number.isFinite(n) && ((min !== undefined && n < min) || (max !== undefined && n > max));
  const rangeText = min !== undefined && max !== undefined ? `Use um valor entre ${formatFixed(min, step < 1 ? 2 : 0)} e ${formatFixed(max, step < 1 ? 2 : 0)}.` : null;
  const localError = draft.trim() === '' || !Number.isFinite(n) ? 'Informe um número.' : outOfRange ? (rangeText ?? 'Valor fora do permitido.') : null;
  return (
    <Field label={text} hint={hint} error={localError ?? error ?? undefined}>
      <Input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={draft}
        className="w-full tabular-nums"
        onChange={(e) => {
          const next = e.target.value;
          setDraft(next);
          const v = Number(next.replace(',', '.'));
          if (next.trim() !== '' && Number.isFinite(v) && (min === undefined || v >= min) && (max === undefined || v <= max)) onChange(v);
        }}
        onBlur={() => setDraft(String(value))}
      />
    </Field>
  );
};

const splitList = (text: string) =>
  text
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

const ListField: React.FC<{ label: string; hint?: React.ReactNode; value: string[]; onChange: (value: string[]) => void }> = ({ label: text, hint, value, onChange }) => {
  const [draft, setDraft] = useState(value.join(', '));
  // Só reescreve o rascunho quando o valor muda por fora (ex.: JSON), para a vírgula final não sumir ao digitar.
  useEffect(() => {
    setDraft((d) => (canonicalJson(splitList(d)) === canonicalJson(value) ? d : value.join(', ')));
  }, [value]);
  return (
    <Field label={text} hint={hint}>
      <Input
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          onChange(splitList(e.target.value));
        }}
        onBlur={() => setDraft(value.join(', '))}
      />
    </Field>
  );
};

const FontSelect: React.FC<{ label: string; value: string; onChange: (value: string) => void }> = ({ label: text, value, onChange }) => (
  <Field label={text}>
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      {withCurrent(FONTS, value).map((f) => (
        <option key={f} value={f}>
          {f}
        </option>
      ))}
    </Select>
  </Field>
);

/** Conteúdo de uma seção: em preset, tudo fica desabilitado (somente leitura). */
const Fields: React.FC<{ disabled: boolean; children: React.ReactNode; className?: string }> = ({ disabled, children, className = '' }) => (
  <fieldset data-somente-leitura="" disabled={disabled} className={`m-0 flex min-w-0 flex-col gap-4 border-0 p-0 ${READONLY_VALUES} ${className}`}>
    {children}
  </fieldset>
);

const Anchor: React.FC<{ id: string; children: React.ReactNode }> = ({ id, children }) => <div id={sectionDomId(id)}>{children}</div>;

/** Nome legível de uma voz quando o catálogo não está à mão ("Microsoft Maria Desktop" → "Maria"). */
const cleanVoiceId = (id: string) => {
  const chirp = /-Chirp3-HD-([A-Za-z]+)$/.exec(id);
  if (chirp) return chirp[1];
  return id.replace(/^Microsoft\s+/i, '').replace(/\s+(Desktop|Online.*)$/i, '');
};

/** Rede de segurança: um valor do JSON que o formulário não consegue mostrar não derruba a página (nem as edições). */
class FormBoundary extends React.Component<{ onRecover: () => void; children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onRecover();
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="flex flex-col items-start gap-2 rounded-xl border border-red-400/35 bg-red-400/10 p-4 text-sm">
        <p className="font-medium text-fg">O JSON tem um valor que o formulário não entende</p>
        <p className="text-red-200">Suas alterações continuam aqui. Corrija o valor na aba JSON completo.</p>
        <Button icon={Braces} onClick={this.props.onRecover}>
          Abrir o JSON
        </Button>
      </div>
    );
  }
}

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

export const StyleEditorPage: React.FC = () => {
  const { styleId = '' } = useParams();
  // Trocar de estilo (ex.: depois de duplicar) recomeça o editor do zero.
  return <StyleEditor key={styleId} styleId={styleId} />;
};

const StyleEditor: React.FC<{ styleId: string }> = ({ styleId }) => {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const detail = useQuery({ queryKey: ['style', styleId], queryFn: () => api.get<Detail>(`/api/styles/${styleId}`) });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const providers = useProviders();

  const [style, setStyle] = useState<StyleSpec | null>(null);
  const [baseline, setBaseline] = useState<StyleSpec | null>(null);
  const [tab, setTab] = useState<'form' | 'json'>('form');
  const [json, setJson] = useState('');
  const [jsonError, setJsonError] = useState<{ message: string; offset: number | null } | null>(null);
  const [jsonFilled, setJsonFilled] = useState<string[]>([]);
  const [jsonAnnouncement, setJsonAnnouncement] = useState('');
  const [savedFlash, setSavedFlash] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [dupOpen, setDupOpen] = useState(false);
  const [dupName, setDupName] = useState<string | null>(null);
  const [dupId, setDupId] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const [draftOffer, setDraftOffer] = useState<Draft | null>(null);
  const [checkingUse, setCheckingUse] = useState(false);
  const flashTimer = useRef<number | undefined>(undefined);
  const jsonId = useId();
  const voicePanelId = useId();
  const dupNameId = useId();
  const dupIdId = useId();
  const draftKey = draftKeyOf(styleId);

  const isSm = useMediaQuery('(min-width: 640px)');
  const isLg = useMediaQuery('(min-width: 1024px)');

  const preset = detail.data?.preset ?? false;
  const dirty = Boolean(style && baseline && (canonicalJson(style) !== canonicalJson(baseline) || (tab === 'json' && jsonError)));
  const loaded = Boolean(style && baseline && detail.data);

  const styleRef = useRef(style);
  styleRef.current = style;
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const version = styles.data?.find((i) => i.style.id === styleId)?.version;
  const versionRef = useRef(version);
  versionRef.current = version;
  const draftChecked = useRef(false);

  // Cabeçalho fixo e índice: as alturas definem quanto a rolagem deixa livre no topo.
  const headerWrapRef = useRef<HTMLDivElement>(null);
  const navWrapRef = useRef<HTMLElement>(null);
  const headerH = useHeight(() => headerWrapRef.current?.firstElementChild, [loaded]);
  const navH = useHeight(() => navWrapRef.current, [loaded, tab]);
  const topOffset = isLg ? headerH + 16 : (isSm ? headerH : 0) + (tab === 'form' ? navH : 0) + 12;

  // Voz: o mesmo catálogo do seletor (mesma chave), para mostrar o nome da voz escolhida.
  const voiceLang = style?.languages.primary || 'pt-BR';
  const voiceCatalog = useVoiceCatalog(voiceLang, { purpose: 'final' });

  const clearDraft = () => removeLocal(draftKey);
  const clearDraftRef = useRef(clearDraft);
  clearDraftRef.current = clearDraft;

  // Carrega (e recarrega) o estilo do servidor, sem atropelar edições em andamento.
  useEffect(() => {
    if (!detail.data || dirtyRef.current) return;
    const server = detail.data.style;
    setStyle(server);
    setBaseline(server);
    if (tabRef.current === 'json') {
      setJson(pretty(server));
      setJsonError(null);
      setJsonFilled([]);
    }
    if (!draftChecked.current) {
      draftChecked.current = true;
      const draft = detail.data.preset ? null : readLocal<Draft | null>(draftKey, null, isDraft);
      if (draft) {
        const filledStyle = fillMissing(draft.style, server);
        if (!leafError(filledStyle) && canonicalJson(filledStyle) !== canonicalJson(server)) setDraftOffer({ ...draft, style: filledStyle as StyleSpec });
        else removeLocal(draftKey);
      }
    }
  }, [detail.data, draftKey]);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  // Guarda as alterações no navegador enquanto não forem salvas.
  useEffect(() => {
    if (!dirty || preset || !style) return;
    writeLocal(draftKey, { style, savedAt: Date.now(), baseVersion: versionRef.current ?? null } satisfies Draft);
  }, [style, baseline, dirty, preset, draftKey]);

  /* ---------------- proteção de saída ---------------- */

  // Sem data router não há useBlocker. Com alterações, uma entrada extra no histórico (mesmo endereço) segura o Voltar do navegador.
  const guardRef = useRef(false);
  const pendingPop = useRef<Promise<void> | null>(null);
  const allowNextClick = useRef(false);
  const skipUnload = useRef(false);
  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;
  const nameRef = useRef('');
  nameRef.current = baseline?.name ?? '';
  const leaveDialog = () =>
    confirmRef.current({
      title: 'Sair sem salvar?',
      description: `As alterações em “${nameRef.current}” ainda não foram salvas e vão se perder.`,
      confirmLabel: 'Sair sem salvar',
      cancelLabel: 'Continuar editando',
      tone: 'danger',
    });
  const leaveDialogRef = useRef(leaveDialog);
  leaveDialogRef.current = leaveDialog;

  /** Tira a entrada extra do histórico (se for a atual) e espera o navegador voltar para ela. */
  const dropGuard = (): Promise<void> => {
    if (pendingPop.current) return pendingPop.current;
    if (!guardRef.current) return Promise.resolve();
    guardRef.current = false;
    if (!(window.history.state as { vultoGuard?: boolean } | null)?.vultoGuard) return Promise.resolve();
    pendingPop.current = new Promise<void>((resolve) => {
      const done = () => {
        window.removeEventListener('popstate', done);
        window.clearTimeout(timer);
        pendingPop.current = null;
        resolve();
      };
      const timer = window.setTimeout(done, 600);
      window.addEventListener('popstate', done);
      window.history.back();
    });
    return pendingPop.current;
  };
  const dropGuardRef = useRef(dropGuard);
  dropGuardRef.current = dropGuard;

  useEffect(() => {
    if (!dirty) return;
    const path = window.location.pathname;
    const pushGuard = () => {
      if (!(window.history.state as { vultoGuard?: boolean } | null)?.vultoGuard)
        window.history.pushState({ ...(window.history.state as object | null), vultoGuard: true }, '', window.location.href);
      guardRef.current = true;
    };
    pushGuard();
    const onPop = () => {
      if (pendingPop.current || !guardRef.current) return;
      // Saiu da entrada extra para a anterior (mesmo endereço): o roteador não trocou de página.
      if (window.location.pathname !== path || (window.history.state as { vultoGuard?: boolean } | null)?.vultoGuard) return;
      guardRef.current = false;
      void leaveDialogRef.current().then((ok) => {
        if (ok) {
          clearDraftRef.current();
          skipUnload.current = true;
          window.history.back();
        } else pushGuard();
      });
    };
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      void dropGuardRef.current();
    };
  }, [dirty]);

  // Aviso ao fechar ou recarregar a aba (o rascunho continua guardado).
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (skipUnload.current) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  // Cliques em links internos (trilha, barra lateral). Confirmado, o clique original é repetido: o próprio Link navega (state, replace, onClick).
  useEffect(() => {
    if (!dirty) return;
    const onClick = (e: MouseEvent) => {
      if (allowNextClick.current) {
        allowNextClick.current = false;
        return;
      }
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const anchor = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (!(anchor instanceof HTMLAnchorElement) || (anchor.target && anchor.target !== '_self') || anchor.hasAttribute('download')) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
      e.preventDefault();
      e.stopPropagation();
      void leaveDialogRef.current().then(async (ok) => {
        if (!ok) return;
        clearDraftRef.current();
        await dropGuardRef.current();
        if (anchor.isConnected) {
          allowNextClick.current = true;
          anchor.click();
          allowNextClick.current = false;
        } else navigate(`${url.pathname}${url.search}${url.hash}`);
      });
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [dirty, navigate]);

  /* ---------------- rolagem e índice ---------------- */

  // A rolagem até uma seção (e o foco do Tab) deixa livre a altura do que fica fixo no topo e na base.
  useEffect(() => {
    const main = document.getElementById('conteudo');
    if (!main || !loaded) return;
    const prevTop = main.style.scrollPaddingTop;
    const prevBottom = main.style.scrollPaddingBottom;
    main.style.scrollPaddingTop = `${topOffset}px`;
    main.style.scrollPaddingBottom = isSm ? '' : '80px';
    return () => {
      main.style.scrollPaddingTop = prevTop;
      main.style.scrollPaddingBottom = prevBottom;
    };
  }, [topOffset, isSm, loaded]);

  // Seção visível no índice.
  const formReady = loaded && tab === 'form';
  useEffect(() => {
    if (!formReady || typeof IntersectionObserver === 'undefined') return;
    const root = document.getElementById('conteudo');
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((en) => en.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveSection(visible[0].target.id.replace('secao-', ''));
      },
      { root, rootMargin: `-${topOffset}px 0px -55% 0px`, threshold: 0 },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(sectionDomId(s.id));
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [formReady, topOffset]);

  // Leitor de tela: o estado do JSON é anunciado só quando a digitação para.
  const jsonStatusText = preset ? '' : jsonError ? jsonError.message : 'JSON válido.';
  useEffect(() => {
    if (tab !== 'json') return;
    const timer = window.setTimeout(() => setJsonAnnouncement(jsonStatusText), 800);
    return () => window.clearTimeout(timer);
  }, [jsonStatusText, tab]);

  /* ---------------- mutações ---------------- */

  const save = useMutation({
    mutationFn: (s: StyleSpec) => api.put<{ style: StyleSpec; warnings: string[] }>(`/api/styles/${styleId}`, { style: s }),
    onSuccess: (data, sent) => {
      client.invalidateQueries({ queryKey: ['style', styleId] });
      client.invalidateQueries({ queryKey: ['styles'] });
      clearDraft();
      setBaseline(data.style);
      // Se nada mudou durante o envio, adota a versão normalizada do servidor (também no texto do JSON).
      const cur = styleRef.current;
      if (cur && canonicalJson(cur) === canonicalJson(sent)) {
        setStyle(data.style);
        if (tabRef.current === 'json') {
          setJson(pretty(data.style));
          setJsonError(null);
          setJsonFilled([]);
        }
      }
      setSavedFlash(true);
      window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => setSavedFlash(false), 3000);
    },
  });

  const duplicate = useMutation({
    mutationFn: (vars: { new_id: string; name: string }) => api.post<{ style: StyleSpec }>(`/api/styles/${styleId}/duplicate`, vars),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ['styles'] });
      // Um estilo apagado antes com o mesmo id não pode aparecer no lugar da cópia.
      client.removeQueries({ queryKey: ['style', data.style.id] });
      toast({ tone: 'good', title: 'Cópia criada', description: `“${data.style.name}” já pode ser editado.` });
      void dropGuardRef.current().then(() => navigate(`/estilos/${data.style.id}`));
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del(`/api/styles/${styleId}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['styles'] });
      clearDraft();
      toast({ tone: 'good', title: 'Estilo apagado', description: baseline ? `“${baseline.name}” saiu da lista.` : undefined });
      void dropGuardRef.current().then(() => {
        navigate('/estilos');
        client.removeQueries({ queryKey: ['style', styleId] });
      });
    },
  });

  const shotError = style && style.edit.shot_seconds.min > style.edit.shot_seconds.max ? 'A cena máxima precisa ser maior ou igual à mínima.' : null;
  const formError =
    shotError ??
    (style && !Object.values(style.edit.camera).some((v) => v > 0) ? 'Ligue pelo menos um movimento de câmera.' : null) ??
    (style && !Object.values(style.edit.transitions).some((v) => v > 0) ? 'Ligue pelo menos uma transição.' : null);
  const busy = save.isPending || remove.isPending || duplicate.isPending;
  const canSave = !preset && dirty && !jsonError && !formError && !busy;
  const doSave = () => {
    if (canSave && style) save.mutate(style);
  };
  const doSaveRef = useRef(doSave);
  doSaveRef.current = doSave;

  // Ctrl+S / Cmd+S salva.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault();
        doSaveRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const takenIds = useMemo(() => new Set((styles.data ?? []).map((i) => i.style.id)), [styles.data]);
  const savedName = baseline?.name ?? '';
  const suggestedId = useMemo(() => suggestStyleId(savedName || 'estilo', takenIds), [savedName, takenIds]);
  const dupIdValue = dupId ?? suggestedId;
  const dupNameValue = dupName ?? `Cópia de ${savedName || 'Estilo'}`;
  const dupIdError = styles.data ? styleIdError(dupIdValue, takenIds) : null;
  const dupNameError = dupNameValue.trim() ? null : 'Informe um nome.';
  const canDuplicate = Boolean(styles.data) && !dupIdError && !dupNameError;
  const providerName = (id: string | undefined) => (id ? (providers.data?.find((p) => p.id === id)?.name ?? 'provedor de voz') : '');

  /* ---------------- carregando / erro ---------------- */

  if (!style || !baseline || !detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <Page width="wide">
        <PageHeader title={notFound ? 'Estilo não encontrado' : 'Estilo'} breadcrumb={[{ label: 'Estilos', to: '/estilos' }, { label: notFound ? 'Não encontrado' : 'Carregando' }]} />
        {notFound ? (
          <Empty
            icon={TriangleAlert}
            title="Este estilo não existe mais"
            action={
              <Link to="/estilos" className={buttonClass({ variant: 'secondary' })}>
                Voltar para Estilos
              </Link>
            }
          >
            Ele pode ter sido apagado ou o endereço está errado.
          </Empty>
        ) : detail.error ? (
          <ErrorBox error={detail.error} title="Não foi possível carregar o estilo" onRetry={() => detail.refetch()} />
        ) : (
          <div role="status" aria-label="Carregando estilo" className="flex flex-col gap-4">
            <Skeleton className="h-10 w-full" />
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[180px_minmax(0,1fr)]">
              <Skeleton className="hidden h-64 lg:block" />
              <div className="flex flex-col gap-4">
                <Skeleton className="h-56" />
                <Skeleton className="h-40" />
              </div>
            </div>
          </div>
        )}
      </Page>
    );
  }

  /* ---------------- ações ---------------- */

  const set = (mutate: (s: StyleSpec) => void) => {
    const next = structuredClone(style);
    mutate(next);
    setStyle(next);
  };

  const openJson = (current: StyleSpec, error: { message: string; offset: number | null } | null = null) => {
    setJson(pretty(current));
    setJsonError(error);
    setJsonFilled([]);
    setTab('json');
  };

  const changeTab = (id: string) => {
    if (id === tab) return;
    if (id === 'json') {
      openJson(style);
      return;
    }
    if (jsonError) {
      toast({ tone: 'warn', title: 'Corrija o JSON antes de voltar ao formulário', description: jsonError.message });
      return;
    }
    setTab('form');
  };

  const recoverForm = () => {
    const current = styleRef.current;
    if (!current) return;
    openJson(current, { message: 'O formulário não conseguiu mostrar um dos valores. Confira se textos, números e listas estão no tipo certo.', offset: null });
    toast({ tone: 'warn', title: 'O JSON tem um valor que o formulário não entende', description: 'Abrimos o JSON para você corrigir. Nada foi perdido.' });
  };

  const onJsonChange = (text: string) => {
    setJson(text);
    const result = checkStyleJson(text, baseline);
    if (result.ok) {
      setStyle(result.value);
      setJsonError(null);
      setJsonFilled(result.filled);
    } else {
      setJsonError({ message: result.message, offset: result.offset });
    }
  };

  const goToJsonError = () => {
    const el = document.getElementById(jsonId);
    if (!(el instanceof HTMLTextAreaElement) || jsonError?.offset == null) return;
    el.focus();
    el.setSelectionRange(jsonError.offset, Math.min(json.length, jsonError.offset + 1));
  };

  const discardChanges = async () => {
    const ok = await confirm({
      title: 'Descartar as alterações?',
      description: 'O estilo volta para a última versão salva. As alterações não salvas vão se perder.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (!ok) return;
    clearDraft();
    setStyle(baseline);
    setJson(pretty(baseline));
    setJsonError(null);
    setJsonFilled([]);
  };

  const recoverDraft = () => {
    if (!draftOffer) return;
    setStyle(draftOffer.style);
    if (tab === 'json') {
      setJson(pretty(draftOffer.style));
      setJsonError(null);
      setJsonFilled([]);
    }
    setDraftOffer(null);
    toast({ tone: 'good', title: 'Alterações recuperadas', description: 'Confira e clique em Salvar para guardar.' });
  };

  const dismissDraft = async () => {
    const ok = await confirm({
      title: 'Descartar o rascunho?',
      description: 'As alterações guardadas neste navegador vão se perder. O estilo salvo não muda.',
      confirmLabel: 'Descartar rascunho',
      tone: 'danger',
    });
    if (!ok) return;
    if (!dirty) clearDraft();
    setDraftOffer(null);
  };

  const focusDupField = () =>
    window.setTimeout(() => {
      const target = document.getElementById(dupNameError ? dupNameId : dupIdId);
      if (target instanceof HTMLInputElement) {
        target.focus();
        target.select();
      }
    }, 0);

  const runDuplicate = async () => {
    if (!canDuplicate) {
      setDupOpen(true);
      if (styles.data) focusDupField();
      return;
    }
    if (dirty) {
      if (canSave) {
        const ok = await confirm({
          title: 'Salvar antes de duplicar?',
          description: 'A cópia é feita a partir da versão salva. Salve agora para a cópia levar as alterações deste estilo.',
          confirmLabel: 'Salvar e duplicar',
          tone: 'primary',
        });
        if (!ok) return;
        try {
          await save.mutateAsync(style);
        } catch {
          return; // o erro aparece no aviso "Não foi possível salvar"
        }
      } else {
        const ok = await confirm({
          title: 'Duplicar e descartar as alterações?',
          description: `A cópia usa a última versão salva. As alterações não salvas deste estilo vão se perder${
            formError || jsonError ? ' (elas ainda não podem ser salvas: corrija os erros para mantê-las)' : ''
          }.`,
          confirmLabel: 'Descartar e duplicar',
          tone: 'danger',
        });
        if (!ok) return;
        clearDraft();
      }
    }
    duplicate.mutate({ new_id: dupIdValue, name: dupNameValue.trim() });
  };

  const onDelete = async () => {
    // Canais que usam este estilo como padrão ficariam apontando para um estilo que não existe.
    let users: Channel[] | null = null;
    setCheckingUse(true);
    try {
      const channels = await client.fetchQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
      users = channels.filter((c) => c.default_style_id === styleId);
    } catch {
      users = null;
    } finally {
      setCheckingUse(false);
    }
    const names = users?.length ? new Intl.ListFormat('pt-BR', { type: 'conjunction' }).format(users.map((c) => `“${c.name}”`)) : '';
    const usage =
      users === null
        ? ' Não foi possível conferir os canais: veja em Canais se algum usa este estilo como padrão.'
        : users.length === 1
          ? ` O canal ${names} usa este estilo como padrão e vai ficar sem estilo padrão.`
          : users.length > 1
            ? ` Os canais ${names} usam este estilo como padrão e vão ficar sem estilo padrão.`
            : ' Nenhum canal usa este estilo como padrão.';
    const ok = await confirm({
      title: `Apagar o estilo “${baseline.name}”?`,
      description: `Ele sai da lista de estilos e não dá para desfazer.${dirty ? ' As alterações não salvas também se perdem.' : ''}${usage}`,
      confirmLabel: 'Apagar estilo',
      tone: 'danger',
    });
    if (ok) remove.mutate();
  };

  const goToSection = (id: string) => {
    const el = document.getElementById(sectionDomId(id));
    if (!el) return;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    // O foco vai para o título da seção (com nome e contorno visível no teclado), não para o bloco.
    const heading = el.querySelector('h2');
    if (heading instanceof HTMLElement) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
    setActiveSection(id);
  };

  /* ---------------- dados derivados ---------------- */

  const c = style.edit.captions;
  const t = style.edit.text_cards;
  const chain = style.voice.chain;
  const voiceId = style.voice.voice;
  const voiceName = voiceId
    ? (voiceCatalog.data?.providers.find((p) => p.id === chain[0])?.voices.find((v) => v.id === voiceId)?.label ??
      voiceCatalog.data?.providers.flatMap((p) => p.voices).find((v) => v.id === voiceId)?.label ??
      cleanVoiceId(voiceId))
    : 'padrão do provedor';
  const pickVoice = (choice: { provider: string; voice: string }) =>
    set((s) => {
      s.voice.voice = choice.voice;
      // O nome da voz vale para o provedor escolhido: ele passa a ser o primeiro da cadeia.
      if (s.voice.chain[0] !== choice.provider) s.voice.chain = [choice.provider, ...s.voice.chain.filter((p) => p !== choice.provider)];
    });

  const statusBadge = preset ? null : dirty ? (
    <Badge tone="warn" icon={CircleDot}>
      Alterações não salvas
    </Badge>
  ) : savedFlash ? (
    <Badge tone="good" icon={CircleCheck}>
      Salvo
    </Badge>
  ) : null;

  const saveButton = (
    <Button
      variant="primary"
      icon={Save}
      busy={save.isPending}
      disabled={!canSave && !save.isPending}
      onClick={doSave}
      title={formError && dirty ? formError : 'Salvar (Ctrl+S)'}
      aria-keyshortcuts="Control+S"
    >
      Salvar
    </Button>
  );
  const discardButton = dirty ? (
    <Button variant="secondary" onClick={discardChanges} disabled={busy}>
      Descartar
    </Button>
  ) : null;
  const duplicatePrimary = (
    <Button variant="primary" icon={Copy} busy={duplicate.isPending} disabled={!styles.data} onClick={runDuplicate}>
      Duplicar e editar
    </Button>
  );

  // No celular, o cabeçalho rola junto e Salvar/Descartar ficam numa barra fixa na base.
  const headerActions = preset ? (
    isSm ? (
      duplicatePrimary
    ) : null
  ) : (
    <>
      <Button variant="ghost" icon={Trash2} busy={remove.isPending || checkingUse} disabled={save.isPending || duplicate.isPending} onClick={onDelete} className="hover:!text-red-300">
        Apagar
      </Button>
      <Button variant="ghost" icon={Copy} busy={duplicate.isPending} disabled={!styles.data || save.isPending || remove.isPending} onClick={runDuplicate}>
        Duplicar
      </Button>
      {isSm ? (
        <>
          {discardButton}
          {saveButton}
        </>
      ) : null}
    </>
  );

  const dupFields = (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label="Nome da cópia" error={dupNameError}>
        <Input id={dupNameId} value={dupNameValue} maxLength={120} onChange={(e) => setDupName(e.target.value)} />
      </Field>
      <Field label="Id da cópia" hint="Aparece no endereço da página. Letras minúsculas, números e hífens." error={dupIdError}>
        <Input
          id={dupIdId}
          value={dupIdValue}
          spellCheck={false}
          onChange={(e) => setDupId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
          className="font-mono text-[13px]"
        />
      </Field>
    </div>
  );

  const toggleDupFields = () => {
    if (dupOpen) {
      setDupName(null);
      setDupId(null);
    }
    setDupOpen((v) => !v);
  };

  const draftWhen = draftOffer ? new Date(draftOffer.savedAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '';
  const draftOutdated = Boolean(draftOffer && draftOffer.baseVersion != null && version != null && draftOffer.baseVersion !== version);

  return (
    <Page width="wide">
      <StyleFonts />
      <style href="vulto-estilo-somente-leitura" precedence="default">
        {READONLY_CSS}
      </style>
      <div ref={headerWrapRef} className="contents">
        <PageHeader
          sticky={isSm}
          breadcrumb={[{ label: 'Estilos', to: '/estilos' }, { label: baseline.name || 'Estilo' }]}
          title={style.name || 'Estilo sem nome'}
          meta={
            <>
              {preset ? null : <Badge tone="info">Meu estilo{version ? ` · v${version}` : ''}</Badge>}
              <span>
                {formatShort(style.format)} · {formatDuration(style.format.duration.target_s)}
              </span>
              <span role="status" className="inline-flex items-center gap-2">
                {statusBadge}
              </span>
              {!preset && dirty && formError ? (
                <Badge tone="bad" icon={CircleX}>
                  {formError}
                </Badge>
              ) : null}
            </>
          }
          actions={headerActions}
        />
      </div>

      <ReferenceOrigins styleId={styleId} />

      {preset ? (
        <div role="note" className="mb-6 flex flex-col gap-3 rounded-xl border border-line bg-card p-4">
          <div className="flex items-start gap-3">
            <Lock size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-fg">Somente leitura</p>
              <p className="mt-0.5 text-sm text-muted">
                Para mudar qualquer coisa, clique em <strong className="font-medium text-fg">Duplicar e editar</strong>: a cópia “{dupNameValue}” vai para Meus estilos e
                abre aqui, pronta para editar.
              </p>
              <button
                type="button"
                aria-expanded={dupOpen}
                onClick={toggleDupFields}
                className="mt-1.5 rounded-sm text-[13px] font-medium text-fg underline underline-offset-2 hover:text-accent"
              >
                {dupOpen ? 'Usar o nome sugerido' : 'Mudar o nome da cópia'}
              </button>
            </div>
          </div>
          {dupOpen ? <div className="sm:ml-8">{dupFields}</div> : null}
        </div>
      ) : dupOpen ? (
        <div role="region" aria-label="Cópia deste estilo" className="mb-6 flex flex-col gap-3 rounded-xl border border-line-strong bg-card p-4">
          <div>
            <p className="font-medium text-fg">Cópia deste estilo</p>
            <p className="mt-0.5 text-sm text-muted">Escolha o nome e o id da cópia. Ela vai para Meus estilos e abre aqui.</p>
          </div>
          {dupFields}
          <div className="flex flex-wrap gap-2">
            <Button icon={Copy} busy={duplicate.isPending} disabled={!canDuplicate || save.isPending} onClick={runDuplicate}>
              Criar cópia
            </Button>
            <Button variant="ghost" onClick={toggleDupFields}>
              Cancelar
            </Button>
          </div>
        </div>
      ) : null}

      {draftOffer && !preset ? (
        <div role="region" aria-label="Alterações não salvas encontradas" className="mb-6 flex flex-wrap items-start gap-3 rounded-xl border border-sky-400/35 bg-sky-400/10 p-4 text-sm">
          <History size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-sky-300" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-fg">Há alterações não salvas de {draftWhen}</p>
            <p className="mt-0.5 text-muted">
              Elas ficaram guardadas neste navegador. Recuperar põe essas alterações no formulário; nada é salvo até você clicar em Salvar.
              {draftOutdated ? ' O estilo foi salvo de novo depois disso: recuperar substitui a versão mais nova no formulário.' : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" icon={RotateCcw} onClick={recoverDraft}>
              Recuperar
            </Button>
            <Button size="sm" variant="ghost" onClick={dismissDraft}>
              Descartar rascunho
            </Button>
          </div>
        </div>
      ) : null}

      {detail.data.warnings.length ? (
        <div role="note" className="mb-6 flex gap-3 rounded-xl border border-amber-300/35 bg-amber-300/10 p-3 text-sm">
          <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          <div className="min-w-0">
            <p className="font-medium text-fg">{detail.data.warnings.length === 1 ? 'Um aviso sobre este estilo' : `${detail.data.warnings.length} avisos sobre este estilo`}</p>
            <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4 text-amber-100/90">
              {detail.data.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      <div className="mb-4 flex flex-col gap-2 empty:hidden">
        <ErrorBox error={styles.error} title="Não foi possível conferir os ids existentes (duplicar fica indisponível)" onRetry={() => styles.refetch()} />
        <ErrorBox error={save.error} title="Não foi possível salvar" onClose={() => save.reset()} />
        <ErrorBox error={duplicate.error} title="Não foi possível duplicar" onClose={() => duplicate.reset()} />
        <ErrorBox error={remove.error} title="Não foi possível apagar" onClose={() => remove.reset()} />
      </div>

      <Tabs
        idPrefix="estilo"
        ariaLabel="Modo de edição"
        className="mb-6"
        active={tab}
        onChange={changeTab}
        tabs={[
          { id: 'form', label: 'Formulário', icon: SlidersHorizontal },
          {
            id: 'json',
            label: 'JSON completo',
            icon: Braces,
            badge: jsonError ? (
              <Badge tone="bad" className="ml-1">
                com erro
              </Badge>
            ) : undefined,
          },
        ]}
      />

      {tab === 'json' ? (
        <div {...tabPanelProps('json', 'estilo')} className="flex flex-col gap-2 rounded-lg">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label htmlFor={jsonId} className="text-sm font-medium text-fg">
              {preset ? 'Definição completa do preset (somente leitura)' : 'Definição completa do estilo'}
            </label>
            <Button
              size="sm"
              variant="ghost"
              icon={Copy}
              onClick={() =>
                navigator.clipboard?.writeText(json).then(
                  () => toast({ tone: 'good', title: 'JSON copiado' }),
                  () => toast({ tone: 'bad', title: 'Não foi possível copiar', description: 'Selecione o texto e copie com Ctrl+C.' }),
                )
              }
            >
              Copiar JSON
            </Button>
          </div>
          <Textarea
            id={jsonId}
            readOnly={preset}
            spellCheck={false}
            invalid={Boolean(jsonError)}
            aria-describedby={`${jsonId}-estado`}
            className="h-[65vh] w-full resize-y font-mono text-[13px] leading-relaxed"
            value={json}
            onChange={(e) => onJsonChange(e.target.value)}
          />
          <div id={`${jsonId}-estado`} className="text-[13px]">
            {preset ? (
              <span className="text-subtle">Você pode selecionar e copiar. Para editar, duplique o preset.</span>
            ) : jsonError ? (
              <span className="flex flex-wrap items-center gap-2 text-red-300">
                {jsonError.message}
                {jsonError.offset != null ? (
                  <Button size="sm" variant="ghost" onClick={goToJsonError}>
                    Ir para o erro
                  </Button>
                ) : null}
              </span>
            ) : (
              <span className="flex flex-wrap items-center gap-1.5 text-emerald-300">
                <CircleCheck size={14} aria-hidden="true" />
                JSON válido. As mudanças já aparecem no formulário.
                {jsonFilled.length ? (
                  <span className="text-subtle">
                    {jsonFilled.length === 1 ? `O campo “${jsonFilled[0]}” não está no texto e` : `${jsonFilled.length} campos não estão no texto e`} continua
                    {jsonFilled.length === 1 ? '' : 'm'} com o valor salvo.
                  </span>
                ) : null}
              </span>
            )}
          </div>
          <span className="sr-only" aria-live="polite">
            {jsonAnnouncement}
          </span>
        </div>
      ) : (
        <div {...tabPanelProps('form', 'estilo')} className="flex flex-col gap-6 rounded-lg lg:grid lg:grid-cols-[180px_minmax(0,1fr)]">
          <aside
            ref={navWrapRef}
            style={{ top: isLg ? headerH + 16 : isSm ? headerH : 0 }}
            className="sticky z-10 -mx-4 min-w-0 border-b border-line bg-canvas/95 px-4 py-1.5 backdrop-blur-md sm:-mx-6 sm:px-6 lg:mx-0 lg:self-start lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none"
          >
            <SectionNav sections={SECTIONS} active={activeSection} onGo={goToSection} />
          </aside>

          <FormBoundary onRecover={recoverForm}>
            <div className="flex min-w-0 flex-col gap-6">
              {/* Formato e roteiro */}
              <Anchor id="formato">
                <Section icon={FileText} title="Formato e roteiro" description="Nome, tipo de vídeo, duração e como o roteiro é escrito.">
                  <Fields disabled={preset}>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <Field label="Nome">
                        <Input value={style.name} maxLength={120} onChange={(e) => set((s) => void (s.name = e.target.value))} />
                      </Field>
                      <Field label="Estrutura do roteiro" hint="Formatos de lista, ranking ou linha do tempo mudam como o roteiro é dividido.">
                        <Select value={style.structure} onChange={(e) => set((s) => void (s.structure = e.target.value))}>
                          {withCurrent(STRUCTURES, style.structure).map((o) => (
                            <option key={o} value={o}>
                              {label(STRUCTURE_LABELS, o)}
                            </option>
                          ))}
                        </Select>
                      </Field>
                    </div>
                    <Field label="Descrição">
                      <Textarea rows={2} value={style.description} onChange={(e) => set((s) => void (s.description = e.target.value))} />
                    </Field>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <Segmented
                        legend="Formato do vídeo"
                        value={style.format.type}
                        options={[
                          { id: 'short', label: 'Curto · 9:16', icon: RectangleVertical },
                          { id: 'long', label: 'Longo · 16:9', icon: RectangleHorizontal },
                        ]}
                        onChange={(type) =>
                          set((s) => {
                            s.format.type = type;
                            s.format.aspect = type === 'short' ? '9:16' : '16:9';
                            s.format.resolution = type === 'short' ? '1080x1920' : '1920x1080';
                          })
                        }
                      />
                      <NumberField
                        label="Duração alvo (segundos)"
                        min={1}
                        value={style.format.duration.target_s}
                        hint={`Cerca de ${formatDuration(style.format.duration.target_s).replace('~', '')}.`}
                        onChange={(v) => set((s) => void (s.format.duration.target_s = v))}
                      />
                    </div>
                    <Field label="Tom do roteiro" hint="Como o narrador fala: ritmo, humor, vocabulário.">
                      <Textarea rows={2} value={style.script.tone} onChange={(e) => set((s) => void (s.script.tone = e.target.value))} />
                    </Field>
                    <ListField
                      label="Etapas do roteiro"
                      hint="Na ordem em que aparecem, separadas por vírgula."
                      value={style.script.structure}
                      onChange={(v) => set((s) => void (s.script.structure = v))}
                    />
                    <Field label="Chamada final (CTA)" hint="Opcional. Ex.: “Siga para ver a parte 2”.">
                      <Input value={style.script.cta ?? ''} onChange={(e) => set((s) => void (s.script.cta = e.target.value || null))} />
                    </Field>
                  </Fields>
                </Section>
              </Anchor>

              {/* Voz */}
              <Anchor id="voz">
                <Section icon={AudioLines} title="Voz" description={`Narração em ${langLabel(style.languages.primary)}.`}>
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-canvas px-3 py-2.5">
                    <div className="min-w-0 text-sm">
                      <span className="text-muted">Voz do vídeo final: </span>
                      <span className="font-medium text-fg">{voiceName}</span>
                      {chain[0] ? <span className="text-subtle"> · {providerName(chain[0])}</span> : null}
                    </div>
                    <Button size="sm" icon={AudioLines} aria-expanded={voiceOpen} aria-controls={voicePanelId} onClick={() => setVoiceOpen((v) => !v)}>
                      {voiceOpen ? 'Fechar lista de vozes' : preset ? 'Ouvir vozes' : 'Ouvir e trocar'}
                    </Button>
                  </div>
                  {voiceOpen ? (
                    <div id={voicePanelId} className="flex flex-col gap-2">
                      <VoicePicker
                        lang={style.languages.primary}
                        purpose="final"
                        value={style.voice.voice && chain[0] ? { provider: chain[0], voice: style.voice.voice } : null}
                        onChange={preset ? undefined : pickVoice}
                        defaultVoice={baseline.voice.voice}
                        disabled={preset}
                      />
                      {!preset && chain.length > 1 ? (
                        <p className="text-xs text-subtle">Escolher uma voz de outro provedor coloca esse provedor em primeiro lugar na ordem de uso.</p>
                      ) : null}
                    </div>
                  ) : null}
                  <Fields disabled={preset}>
                    <Checkbox
                      label="Prévia com a voz local grátis"
                      description="A prévia narra com uma voz que roda no seu computador, sem cota, para você conferir o conteúdo. A voz acima só é gravada ao renderizar o vídeo final."
                      checked={style.voice.preview?.local ?? true}
                      disabled={preset}
                      onChange={(on) =>
                        set((s) => {
                          s.voice.preview = { voice: s.voice.preview?.voice ?? null, local: on };
                        })
                      }
                    />
                    <RangeField
                      label="Velocidade da fala"
                      min={0.7}
                      max={1.5}
                      step={0.05}
                      value={style.voice.speed}
                      display={`${formatFixed(style.voice.speed)}x`}
                      hint="1,00x é a velocidade natural da voz."
                      onChange={(v) => set((s) => void (s.voice.speed = v))}
                    />
                    <Field
                      label="Interpretação da voz"
                      hint="Instrução de como falar (ex.: “sussurrado, tenso”). O Gemini ignora este campo, a não ser que a interpretação esteja ligada no servidor (VS_GEMINI_TTS_STYLE=1)."
                    >
                      <Textarea rows={2} value={style.voice.style_prompt ?? ''} onChange={(e) => set((s) => void (s.voice.style_prompt = e.target.value || null))} />
                    </Field>
                  </Fields>
                </Section>
              </Anchor>

              {/* Arte */}
              <Anchor id="arte">
                <Section icon={Image} title="Arte" description="Como as imagens das cenas são geradas.">
                  <Fields disabled={preset}>
                    <Field label="Bíblia de arte" hint="Descreva o visual que todas as imagens devem seguir: técnica, luz, paleta, época.">
                      <Textarea rows={4} value={style.art.bible} onChange={(e) => set((s) => void (s.art.bible = e.target.value))} />
                    </Field>
                    <ListField
                      label="Evitar nas imagens"
                      hint="Separe por vírgula. Ex.: texto, logotipos, marca d'água."
                      value={style.art.banned}
                      onChange={(v) => set((s) => void (s.art.banned = v))}
                    />
                    <Checkbox
                      label="Arte fotorrealista"
                      description="Liga o rótulo de conteúdo sintético na publicação."
                      checked={style.art.photoreal}
                      disabled={preset}
                      onChange={(v) => set((s) => void (s.art.photoreal = v))}
                    />
                  </Fields>
                </Section>
              </Anchor>

              {/* Ritmo e câmera */}
              <Anchor id="ritmo">
                <Section icon={Video} title="Ritmo e câmera" description="Quanto tempo cada cena dura e como a câmera se move entre elas.">
                  <Fields disabled={preset}>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      <NumberField
                        label="Cena mínima (segundos)"
                        step={0.1}
                        min={0.1}
                        value={style.edit.shot_seconds.min}
                        onChange={(v) => set((s) => void (s.edit.shot_seconds.min = v))}
                      />
                      <NumberField
                        label="Cena máxima (segundos)"
                        step={0.1}
                        min={0.1}
                        value={style.edit.shot_seconds.max}
                        error={shotError}
                        onChange={(v) => set((s) => void (s.edit.shot_seconds.max = v))}
                      />
                      <NumberField
                        label="Duração da transição (segundos)"
                        step={0.05}
                        min={0.05}
                        max={2}
                        value={style.edit.transition_seconds}
                        onChange={(v) => set((s) => void (s.edit.transition_seconds = v))}
                      />
                    </div>
                    <RangeField
                      label="Força do zoom"
                      min={0}
                      max={0.6}
                      step={0.01}
                      value={style.edit.zoom_strength}
                      display={`${Math.round(style.edit.zoom_strength * 100)}%`}
                      hint="Quanto a imagem aproxima ou afasta durante a cena."
                      onChange={(v) => set((s) => void (s.edit.zoom_strength = v))}
                    />
                    <WeightsEditor
                      legend="Movimentos de câmera"
                      hint="O peso define com que frequência cada movimento é sorteado para uma cena."
                      options={CAMERA}
                      labels={CAMERA_LABELS}
                      value={style.edit.camera}
                      onChange={(v) => set((s) => void (s.edit.camera = v))}
                    />
                    <WeightsEditor
                      legend="Transições"
                      hint="O peso define com que frequência cada transição aparece entre as cenas."
                      options={TRANSITIONS}
                      labels={TRANSITION_LABELS}
                      value={style.edit.transitions}
                      onChange={(v) => set((s) => void (s.edit.transitions = v))}
                    />
                    <WeightsEditor
                      legend="Tratamento das cenas de imagem"
                      hint="Lousa desenha o traço, colagem vira recorte de papel e tela mostra a imagem numa janela, celular ou feed com cursor."
                      options={TREATMENTS}
                      labels={TREATMENT_LABELS}
                      value={style.edit.treatments ?? {}}
                      allowEmpty="Tudo desligado: automático pela família do estilo (Ilustrado 2D vira lousa, Textura artesanal vira colagem, Interface de tela vira tela; as outras ficam com a imagem com câmera)."
                      onChange={(v) => set((s) => void (s.edit.treatments = v))}
                    />
                  </Fields>
                </Section>
              </Anchor>

              {/* Efeitos */}
              <Anchor id="efeitos">
                <Section icon={Sparkles} title="Efeitos" description="Textura e cor aplicadas por cima de todas as cenas.">
                  <Fields disabled={preset}>
                    <EffectsEditor disabled={preset} value={style.edit.effects} onChange={(v) => set((s) => void (s.edit.effects = v))} />
                  </Fields>
                </Section>
              </Anchor>

              {/* Legendas */}
              <Anchor id="legendas">
                <Section icon={Captions} title="Legendas" description="Como a fala aparece escrita no vídeo.">
                  <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_auto]">
                    <Fields disabled={preset}>
                      <Segmented
                        legend="Estilo da legenda"
                        value={c.style}
                        options={['karaoke', 'word', 'line', 'none'].map((id) => ({ id, label: label(CAPTION_STYLE_LABELS, id) }))}
                        onChange={(v) => set((s) => void (s.edit.captions.style = v))}
                      />
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                        <FontSelect label="Fonte" value={c.font} onChange={(v) => set((s) => void (s.edit.captions.font = v))} />
                        <NumberField label="Tamanho (px)" min={12} max={240} value={c.size} onChange={(v) => set((s) => void (s.edit.captions.size = Math.round(v)))} />
                        <NumberField
                          label="Palavras por linha"
                          min={1}
                          max={12}
                          value={c.max_words_per_line}
                          onChange={(v) => set((s) => void (s.edit.captions.max_words_per_line = Math.round(v)))}
                        />
                      </div>
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <ColorField label="Cor do texto" value={c.color} onChange={(v) => set((s) => void (s.edit.captions.color = v))} />
                        <ColorField label="Cor de destaque" value={c.highlight} onChange={(v) => set((s) => void (s.edit.captions.highlight = v))} />
                        <NumberField label="Contorno (px)" min={0} max={30} value={c.stroke} onChange={(v) => set((s) => void (s.edit.captions.stroke = Math.round(v)))} />
                        <ColorField label="Cor do contorno" value={c.stroke_color} onChange={(v) => set((s) => void (s.edit.captions.stroke_color = v))} />
                      </div>
                      <Segmented
                        legend="Posição na tela"
                        value={c.position}
                        options={['top', 'center', 'bottom'].map((id) => ({ id, label: label(CAPTION_POSITION_LABELS, id) }))}
                        onChange={(v) => set((s) => void (s.edit.captions.position = v))}
                      />
                      <Checkbox
                        label="Tudo em maiúsculas"
                        checked={c.uppercase}
                        disabled={preset}
                        onChange={(v) => set((s) => void (s.edit.captions.uppercase = v))}
                      />
                    </Fields>
                    <CaptionPreview style={style} />
                  </div>
                </Section>
              </Anchor>

              {/* Textos na tela */}
              <Anchor id="textos">
                <Section icon={Type} title="Textos na tela" description="Cartões de texto (títulos, números, citações) e barra de progresso.">
                  <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_auto]">
                    <Fields disabled={preset}>
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <FontSelect label="Fonte dos cartões" value={t.font} onChange={(v) => set((s) => void (s.edit.text_cards.font = v))} />
                        <Field label="Animação de entrada">
                          <Select value={t.animation} onChange={(e) => set((s) => void (s.edit.text_cards.animation = e.target.value))}>
                            {withCurrent(ANIMATIONS, t.animation).map((a) => (
                              <option key={a} value={a}>
                                {label(TEXT_ANIMATION_LABELS, a)}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <ColorField label="Cor do texto" value={t.color} onChange={(v) => set((s) => void (s.edit.text_cards.color = v))} />
                        <ColorField label="Cor de destaque" value={t.accent} onChange={(v) => set((s) => void (s.edit.text_cards.accent = v))} />
                        <ColorField label="Fundo" value={t.background} onChange={(v) => set((s) => void (s.edit.text_cards.background = v))} />
                      </div>
                      <Checkbox
                        label="Barra de progresso"
                        description="Uma faixa fina na base do vídeo mostra quanto falta."
                        checked={style.edit.progress_bar}
                        disabled={preset}
                        onChange={(v) => set((s) => void (s.edit.progress_bar = v))}
                      />
                      {style.edit.progress_bar ? (
                        <div className="sm:w-1/2">
                          <ColorField label="Cor da barra" value={style.edit.progress_color} onChange={(v) => set((s) => void (s.edit.progress_color = v))} />
                        </div>
                      ) : null}
                      <Checkbox
                        label="Avatar do apresentador nas cenas"
                        description="Marcado (automático): segue o avatar do canal e do projeto. Desmarcado (nunca): o vídeo feito com este estilo não mostra o avatar, mesmo com ele ligado no canal. A thumb não muda."
                        checked={(style.edit.presenter ?? 'auto') !== 'off'}
                        disabled={preset}
                        onChange={(v) => set((s) => void (s.edit.presenter = v ? 'auto' : 'off'))}
                      />
                    </Fields>
                    <TextCardPreview style={style} />
                  </div>
                </Section>
              </Anchor>

              {/* Áudio */}
              <Anchor id="audio">
                <Section icon={Music} title="Áudio" description="Trilha de fundo e efeitos sonoros.">
                  <Fields disabled={preset}>
                    <Segmented
                      legend="Trilha de fundo"
                      hint={
                        <>
                          Suas faixas ficam na biblioteca de músicas, em{' '}
                          <Link to="/configuracoes" className="rounded-sm text-fg underline underline-offset-2 hover:text-accent">
                            Configurações
                          </Link>
                          .
                        </>
                      }
                      value={style.audio.music.source}
                      options={['procedural', 'library', 'none'].map((id) => ({ id, label: label(MUSIC_SOURCE_LABELS, id) }))}
                      onChange={(v) => set((s) => void (s.audio.music.source = v))}
                    />
                    {style.audio.music.source !== 'none' ? (
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Clima da trilha">
                          <Select value={style.audio.music.mood} onChange={(e) => set((s) => void (s.audio.music.mood = e.target.value))}>
                            {withCurrent(MOOD_OPTIONS, style.audio.music.mood).map((m) => (
                              <option key={m} value={m}>
                                {MOOD_OPTIONS.includes(m) ? moodLabel(m) : `${moodLabel(m)} (“${m}”)`}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <NumberField
                          label="Volume da trilha (dB)"
                          min={-60}
                          max={0}
                          value={style.audio.music.volume_db}
                          hint="0 é o volume máximo; −22 fica bem atrás da voz."
                          onChange={(v) => set((s) => void (s.audio.music.volume_db = v))}
                        />
                      </div>
                    ) : null}
                    <Checkbox
                      label="Efeitos sonoros automáticos nas transições"
                      checked={style.audio.sfx.auto}
                      disabled={preset}
                      onChange={(v) => set((s) => void (s.audio.sfx.auto = v))}
                    />
                  </Fields>
                </Section>
              </Anchor>

              {/* Thumbnail */}
              <Anchor id="thumbnail">
                <Section icon={LayoutTemplate} title="Thumbnail" description="A capa do vídeo.">
                  <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_auto]">
                    <Fields disabled={preset}>
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        <Field label="Modelo">
                          <Select value={style.thumbnail.template} onChange={(e) => set((s) => void (s.thumbnail.template = e.target.value))}>
                            {withCurrent(TEMPLATES, style.thumbnail.template).map((o) => (
                              <option key={o} value={o}>
                                {label(THUMBNAIL_TEMPLATE_LABELS, o)}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <FontSelect label="Fonte" value={style.thumbnail.font} onChange={(v) => set((s) => void (s.thumbnail.font = v))} />
                        <ColorField label="Cor do texto" value={style.thumbnail.color} onChange={(v) => set((s) => void (s.thumbnail.color = v))} />
                        <ColorField label="Cor de destaque" value={style.thumbnail.accent} onChange={(v) => set((s) => void (s.thumbnail.accent = v))} />
                        <NumberField
                          label="Variações para escolher"
                          min={1}
                          max={6}
                          value={style.thumbnail.variations}
                          onChange={(v) => set((s) => void (s.thumbnail.variations = Math.round(v)))}
                        />
                      </div>
                      <Checkbox
                        label="Uma thumbnail por idioma"
                        description="Cada idioma de saída ganha a própria capa, com o texto traduzido."
                        checked={style.thumbnail.per_language}
                        disabled={preset}
                        onChange={(v) => set((s) => void (s.thumbnail.per_language = v))}
                      />
                    </Fields>
                    <ThumbnailPreview style={style} />
                  </div>
                </Section>
              </Anchor>
            </div>
          </FormBoundary>
        </div>
      )}

      {/* Celular: barra de ações presa à base da área de conteúdo. */}
      {isSm ? null : (
        <div
          role="region"
          aria-label="Ações do estilo"
          className="sticky bottom-0 z-20 -mx-4 -mb-10 mt-8 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-panel/95 px-4 py-3 backdrop-blur-md"
        >
          {preset ? (
            duplicatePrimary
          ) : (
            <>
              <span className="mr-auto min-w-0 text-[13px]">
                {dirty && formError ? (
                  <span className="text-red-300">{formError}</span>
                ) : dirty ? (
                  <span className="text-amber-200">Alterações não salvas</span>
                ) : savedFlash ? (
                  <span className="text-emerald-300">Salvo</span>
                ) : (
                  <span className="text-subtle">Tudo salvo</span>
                )}
              </span>
              {discardButton}
              {saveButton}
            </>
          )}
        </div>
      )}
    </Page>
  );
};
