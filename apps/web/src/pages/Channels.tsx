import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarClock,
  ClipboardPaste,
  Clock,
  Coins,
  ExternalLink,
  Globe,
  Hash,
  ListVideo,
  LogIn,
  Mic,
  Package,
  Palette,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  SquarePlay,
  Target,
  Telescope,
  Trash2,
  Tv,
  Unlink,
  UserRound,
  Users,
  X,
} from 'lucide-react';
import React, { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  api,
  type Channel,
  type ChannelNovelty,
  type ChannelSlots,
  type ChannelSuggestion,
  type ChannelTokenStatus,
  type ChannelYouTube,
  fileUrl,
  type GlossaryEntry,
  LANGUAGES,
  type PostingPlan,
  type PostingSlot,
  type PronunciationEntry,
  type StyleItem,
  TIMEZONES,
  type VoiceChoice,
  WEEKDAYS,
  type YouTubeAccountStatus,
  type YouTubeLinkStart,
  type YouTubeLinkStatus,
} from '../api';
import { langLabel } from '../labels';
import { useVoiceCatalog, VoicePicker } from '../voices';
import {
  Badge,
  type BadgeTone,
  Button,
  buttonClass,
  Checkbox,
  Empty,
  ErrorBox,
  errorMessage,
  Field,
  FieldGroup,
  IconButton,
  Input,
  Page,
  PageHeader,
  Select,
  Skeleton,
  Spinner,
  Textarea,
  useConfirm,
  useToast,
} from '../ui';
import { cleanGlossary, cleanPronunciation, GlossaryEditor, halfFilledPronunciation, PronunciationEditor } from './ChannelSpeech';
import { CharactersSheet } from './characters/CharactersSheet';
import { chipClass, Sheet } from './ops/common';
import { browserZone, sameOffsetAsBrowser, whenIn, zoneCity, zoneLabel } from './ops/zones';
import { Notice } from './project/delivery/shared';
import { PlaylistPicker } from '../youtube';

type Form = {
  name: string;
  niche: string;
  persona: string;
  languages: string[];
  default_style_id: string | null;
  posting: PostingPlan;
  novelty: ChannelNovelty;
  /** Identidade visual: `style_bible.identidade` do canal. */
  identity: string;
  /* Operação do canal (ADR 0002). Números como texto: o campo aceita vírgula e fica vazio para "sem meta". */
  audience: string;
  cadence: string;
  posts_per_day: string;
  min_stock: string;
  stock_format: StockFormat;
  short_hashtag: string;
  credits_per_char: string;
  credit_price_usd: string;
  /** Voz final padrão (`voice_defaults.final`): o Novo projeto deste canal já vem com ela. */
  voice_final: VoiceChoice | null;
  pronunciation: PronunciationEntry[];
  glossary: GlossaryEntry[];
};

type StockFormat = 'short' | 'long' | 'any';

const STOCK_FORMATS: { id: StockFormat; label: string; unit: string }[] = [
  { id: 'short', label: 'Shorts', unit: 'Shorts' },
  { id: 'long', label: 'Vídeos longos', unit: 'vídeos longos' },
  { id: 'any', label: 'Qualquer formato', unit: 'vídeos' },
];

const numText = (n: number | undefined) => (n ? String(n).replace('.', ',') : '');

/** Padrões do servidor: a música não repete nos 3 últimos vídeos; a novidade compara os 10 últimos. */
const NOVELTY_DEFAULT: ChannelNovelty = { music_window: 3, window: 10 };

const blank = (): Form => ({
  name: '',
  niche: '',
  persona: '',
  languages: ['pt-BR'],
  default_style_id: null,
  posting: { timezone: browserZone, slots: [] },
  novelty: { ...NOVELTY_DEFAULT },
  identity: '',
  audience: '',
  cadence: '',
  posts_per_day: '',
  min_stock: '',
  stock_format: 'short',
  short_hashtag: '',
  credits_per_char: '',
  credit_price_usd: '',
  voice_final: null,
  pronunciation: [],
  glossary: [],
});

const identityOf = (c: Channel) => (typeof c.style_bible?.identidade === 'string' ? c.style_bible.identidade : '');

const formOf = (c: Channel): Form => ({
  name: c.name,
  niche: c.niche,
  persona: c.persona,
  languages: c.languages,
  default_style_id: c.default_style_id,
  posting: c.posting,
  novelty: c.novelty ?? { ...NOVELTY_DEFAULT },
  identity: identityOf(c),
  audience: c.audience ?? '',
  cadence: c.cadence ?? '',
  posts_per_day: numText(c.posts_per_day),
  min_stock: numText(c.min_stock),
  stock_format: c.stock_format ?? 'short',
  short_hashtag: c.short_hashtag ?? '',
  credits_per_char: numText(c.credits_per_char),
  credit_price_usd: numText(c.credit_price_usd),
  voice_final: c.voice_defaults?.final?.provider && c.voice_defaults.final.voice ? { ...c.voice_defaults.final } : null,
  pronunciation: c.pronunciation ?? [],
  glossary: c.glossary ?? [],
});

/** Inteiro entre min e max ("" = 0); null se inválido. */
const intIn = (text: string, min: number, max: number) => {
  const t = text.trim();
  if (!t) return 0;
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= min && n <= max ? n : null;
};

/** Decimal >= 0 com vírgula ou ponto ("" = 0); null se inválido. */
const decimal = (text: string) => {
  const t = text.trim().replace(',', '.');
  if (!t) return 0;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
};

const HASHTAG = /^(#\S+)?$/;

type OpsErrors = Partial<Record<'posts_per_day' | 'min_stock' | 'short_hashtag' | 'credits_per_char' | 'credit_price_usd', string>>;

/** Mesmos limites do servidor (ChannelBody). */
const opsErrors = (f: Form): OpsErrors => {
  const e: OpsErrors = {};
  if (intIn(f.posts_per_day, 0, 20) === null) e.posts_per_day = 'Um número de 0 a 20.';
  if (intIn(f.min_stock, 0, 50) === null) e.min_stock = 'Um número de 0 a 50.';
  const tag = f.short_hashtag.trim();
  if (tag.length > 60 || !HASHTAG.test(tag)) e.short_hashtag = 'Comece com # e não use espaço (ex.: #plantao).';
  if (decimal(f.credits_per_char) === null) e.credits_per_char = 'Um número maior ou igual a zero (ex.: 0,44).';
  if (decimal(f.credit_price_usd) === null) e.credit_price_usd = 'Um valor maior ou igual a zero (ex.: 0,0003).';
  return e;
};

/** Corpo do POST/PUT: a identidade vai dentro do `style_bible` (as outras chaves dele ficam como estão). */
const bodyOf = (f: Form, base: Channel | undefined) => {
  const bible: Record<string, unknown> = { ...(base?.style_bible ?? {}) };
  if (f.identity.trim()) bible.identidade = f.identity.trim();
  else delete bible.identidade;
  return {
    name: f.name,
    niche: f.niche,
    persona: f.persona,
    languages: f.languages,
    default_style_id: f.default_style_id,
    posting: f.posting,
    novelty: f.novelty,
    style_bible: bible,
    audience: f.audience.trim(),
    cadence: f.cadence.trim(),
    posts_per_day: intIn(f.posts_per_day, 0, 20) ?? 0,
    min_stock: intIn(f.min_stock, 0, 50) ?? 0,
    stock_format: f.stock_format,
    short_hashtag: f.short_hashtag.trim(),
    credits_per_char: decimal(f.credits_per_char) ?? 0,
    credit_price_usd: decimal(f.credit_price_usd) ?? 0,
    voice_defaults: voiceDefaultsOf(f, base),
    pronunciation: cleanPronunciation(f.pronunciation),
    glossary: cleanGlossary(f.glossary),
  };
};

/** `voice_defaults` com a voz final do formulário (outras chaves que existam ficam como estão). */
const voiceDefaultsOf = (f: Form, base: Channel | undefined) => {
  const rest: Record<string, unknown> = { ...(base?.voice_defaults ?? {}) };
  delete rest.final;
  return f.voice_final ? { ...rest, final: { provider: f.voice_final.provider, voice: f.voice_final.voice } } : rest;
};

/* ------------------------------------------------------------------ */
/* Novidade: música sem repetir e verificador de vídeos parecidos       */
/* ------------------------------------------------------------------ */

const MUSIC_WINDOWS = [0, 1, 2, 3, 5, 10, 20];
const NOVELTY_WINDOWS = [0, 5, 10, 20, 50];

/** Opções da lista, com o valor salvo incluído mesmo se não estiver entre elas. */
const windowOptions = (values: number[], current: number) => [...new Set([...values, current])].sort((a, b) => a - b);

const NoveltyEditor: React.FC<{ value: ChannelNovelty; onChange: (v: ChannelNovelty) => void }> = ({ value, onChange }) => (
  <div className="flex flex-col gap-3">
    <div>
      <div className="text-sm font-medium text-fg">Novidade</div>
      <p className="text-[13px] text-muted">Para os vídeos do canal não ficarem repetidos.</p>
    </div>
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Música sem repetir" hint="A mesma faixa da sua biblioteca não toca de novo nesses vídeos seguidos do canal.">
        <Select value={value.music_window} onChange={(e) => onChange({ ...value, music_window: Number(e.target.value) })}>
          {windowOptions(MUSIC_WINDOWS, value.music_window).map((n) => (
            <option key={n} value={n}>
              {n === 0 ? 'Pode repetir' : n === 1 ? 'Diferente do vídeo anterior' : `Diferente dos ${n} últimos vídeos`}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Verificador de novidade" hint="Antes de publicar, avisa se imagens, miniatura ou título parecem demais com esses vídeos.">
        <Select value={value.window} onChange={(e) => onChange({ ...value, window: Number(e.target.value) })}>
          {windowOptions(NOVELTY_WINDOWS, value.window).map((n) => (
            <option key={n} value={n}>
              {n === 0 ? 'Desligado' : `Comparar com os ${n} últimos vídeos`}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  </div>
);

/* ------------------------------------------------------------------ */
/* Dias da semana (0 = segunda … 6 = domingo, como no servidor)        */
/* ------------------------------------------------------------------ */

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const WORK_DAYS = [0, 1, 2, 3, 4];
const WEEKEND = [5, 6];

const DAY_PRESETS: { id: string; label: string; days: number[] }[] = [
  { id: 'todos', label: 'Todos os dias', days: ALL_DAYS },
  { id: 'uteis', label: 'Dias úteis', days: WORK_DAYS },
  { id: 'fds', label: 'Fim de semana', days: WEEKEND },
];

const sameDays = (a: number[], b: number[]) => a.length === b.length && [...a].sort().every((d, i) => d === [...b].sort()[i]);

const DAY_NAMES = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'];

/** "Todos os dias", "Dias úteis", "Fim de semana" ou "seg, qua, sex". */
const describeDays = (days: number[]) => {
  const preset = DAY_PRESETS.find((p) => sameDays(p.days, days));
  if (preset) return preset.label;
  return [...days].sort().map((d) => WEEKDAYS[d]).join(', ');
};

const validTime = (t: string) => /^\d{2}:\d{2}$/.test(t);

/** Dias da semana com menos horários (distintos) que a meta de posts por dia. */
const thinDays = (slots: PostingSlot[], perDay: number) =>
  perDay > 0 ? ALL_DAYS.filter((d) => new Set(slots.filter((s) => s.weekdays.includes(d) && validTime(s.time)).map((s) => s.time)).size < perDay) : [];

const thinDaysText = (slots: PostingSlot[], perDay: number) => {
  const thin = thinDays(slots, perDay);
  if (!thin.length) return null;
  const goal = `Meta de ${perDay} ${perDay === 1 ? 'post' : 'posts'} por dia`;
  if (!slots.length) return `${goal}, mas o canal não tem horários de postagem.`;
  const which = thin.length === 7 ? 'todos os dias têm' : `${thin.map((d) => DAY_NAMES[d]).join(', ')} ${thin.length === 1 ? 'tem' : 'têm'}`;
  return `${goal}, mas ${which} menos horários que isso. Um horário para cada post do dia: o que fica sem horário não entra na agenda.`;
};

/** Hora sugerida para um horário novo: a primeira ainda não usada. */
const NO_SLOTS_TEXT = 'Sem horários: a Fila do YouTube não agenda vídeos deste canal e a Agenda não sugere horários.';

const nextFreeTime = (slots: PostingSlot[]) => ['18:00', '12:00', '21:00', '09:00', '15:00'].find((t) => !slots.some((s) => s.time === t)) ?? '18:00';

/* ------------------------------------------------------------------ */
/* Editor dos horários                                                 */
/* ------------------------------------------------------------------ */

const SlotEditor: React.FC<{
  slot: PostingSlot;
  index: number;
  timezone: string;
  onChange: (patch: Partial<PostingSlot>) => void;
  onRemove: () => void;
}> = ({ slot, index, timezone, onChange, onRemove }) => {
  const n = index + 1;
  const onlyOne = slot.weekdays.length === 1;
  return (
    <fieldset className="relative m-0 flex min-w-0 flex-col gap-2.5 rounded-lg border border-line bg-card p-3">
      <legend className="sr-only">Horário {n}</legend>
      <div className="flex items-center gap-2">
        <Clock size={16} aria-hidden="true" className="shrink-0 text-muted" />
        <Input
          type="time"
          aria-label={`Hora do horário ${n}`}
          className="w-32 tabular-nums"
          value={slot.time}
          invalid={!validTime(slot.time)}
          onChange={(e) => onChange({ time: e.target.value })}
        />
        <span className="truncate text-[13px] text-subtle">hora de {zoneCity(timezone)}</span>
        <IconButton label={`Remover horário ${n}`} icon={Trash2} size="sm" onClick={onRemove} className="ml-auto hover:text-red-300" />
      </div>
      <div role="group" aria-label={`Dias do horário ${n}`} className="flex flex-wrap gap-1">
        {WEEKDAYS.map((label, day) => {
          const on = slot.weekdays.includes(day);
          const locked = on && onlyOne;
          return (
            <button
              key={day}
              type="button"
              aria-pressed={on}
              aria-label={DAY_NAMES[day]}
              aria-disabled={locked || undefined}
              title={locked ? 'Cada horário precisa de pelo menos um dia' : DAY_NAMES[day]}
              onClick={() => {
                if (locked) return;
                onChange({ weekdays: on ? slot.weekdays.filter((d) => d !== day) : [...slot.weekdays, day].sort() });
              }}
              className={`${chipClass(on)} min-w-10 justify-center ${locked ? 'cursor-not-allowed' : ''}`}
            >
              {label}
            </button>
          );
        })}
      </div>
      <div role="group" aria-label={`Atalhos de dias do horário ${n}`} className="flex flex-wrap items-center gap-1">
        {DAY_PRESETS.map((p) => {
          const on = sameDays(p.days, slot.weekdays);
          return (
            <button
              key={p.id}
              type="button"
              aria-pressed={on}
              onClick={() => onChange({ weekdays: p.days })}
              className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${on ? 'text-fg underline decoration-accent decoration-2 underline-offset-4' : 'text-muted hover:bg-raised hover:text-fg'}`}
            >
              {p.label}
            </button>
          );
        })}
        {onlyOne ? <span className="ml-1 text-xs text-subtle">Pelo menos um dia fica marcado.</span> : null}
      </div>
      <Input
        aria-label={`Motivo do horário ${n}`}
        placeholder="Motivo (opcional), ex.: pico de audiência no almoço"
        maxLength={120}
        value={slot.reason ?? ''}
        onChange={(e) => onChange({ reason: e.target.value })}
        className="text-[13px]"
      />
    </fieldset>
  );
};

/** Mini grade da semana com os horários de cada dia (repetidos no mesmo dia em aviso). */
const WeekOverview: React.FC<{ slots: PostingSlot[] }> = ({ slots }) => {
  const days = ALL_DAYS.map((d) => slots.filter((s) => s.weekdays.includes(d) && validTime(s.time)).map((s) => s.time).sort());
  // Horários repetidos no mesmo dia contam uma vez só (o servidor agenda um vídeo por horário).
  const total = days.reduce((n, d) => n + new Set(d).size, 0);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[13px] text-muted">
        <span className="tabular-nums text-fg">{total}</span> {total === 1 ? 'vídeo' : 'vídeos'} por semana
      </div>
      <ul aria-label="Horários por dia da semana" className="grid grid-cols-7 gap-1">
        {days.map((times, d) => (
          <li key={d} aria-label={`${DAY_NAMES[d]}: ${times.length ? times.join(', ') : 'sem horário'}`} className="flex min-w-0 flex-col items-center gap-1 rounded-md border border-line bg-canvas px-0.5 py-1.5">
            <span aria-hidden="true" className="text-xs font-medium text-muted">{WEEKDAYS[d]}</span>
            {times.length ? (
              times.map((t, i) => {
                const dup = times.indexOf(t) !== i;
                return (
                  <span
                    key={`${t}-${i}`}
                    aria-hidden="true"
                    title={dup ? 'Horário repetido neste dia' : undefined}
                    className={`text-xs tabular-nums ${dup ? 'rounded bg-amber-300/15 px-0.5 text-amber-200' : 'text-fg'}`}
                  >
                    {t}
                  </span>
                );
              })
            ) : (
              <span aria-hidden="true" className="text-xs text-subtle">—</span>
            )}
          </li>
        ))}
      </ul>
      {days.some((times) => new Set(times).size !== times.length) ? (
        <p className="text-[13px] text-amber-200">Há horários repetidos no mesmo dia: só um vídeo é agendado por horário.</p>
      ) : null}
    </div>
  );
};

const PostingEditor: React.FC<{ value: PostingPlan; onChange: (p: PostingPlan) => void }> = ({ value, onChange }) => {
  const zones = TIMEZONES.includes(value.timezone) ? TIMEZONES : [value.timezone, ...TIMEZONES];
  const setSlot = (index: number, patch: Partial<PostingSlot>) =>
    onChange({ ...value, slots: value.slots.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  return (
    <div className="flex flex-col gap-3">
      <Field label="Fuso dos horários" hint="Os horários de postagem valem neste fuso, mesmo que o computador esteja em outro.">
        <Select value={value.timezone} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
          {zones.map((z) => (
            <option key={z} value={z}>
              {zoneLabel(z)}
              {z === browserZone ? ' · este computador' : ''}
            </option>
          ))}
        </Select>
      </Field>
      <FieldGroup
        legend="Horários de postagem"
        hint={value.slots.length ? undefined : NO_SLOTS_TEXT}
      >
        {value.slots.map((slot, index) => (
          <SlotEditor
            key={index}
            slot={slot}
            index={index}
            timezone={value.timezone}
            onChange={(patch) => setSlot(index, patch)}
            onRemove={() => onChange({ ...value, slots: value.slots.filter((_, i) => i !== index) })}
          />
        ))}
        <div>
          <Button size="sm" icon={Plus} onClick={() => onChange({ ...value, slots: [...value.slots, { weekdays: ALL_DAYS, time: nextFreeTime(value.slots) }] })}>
            Adicionar horário
          </Button>
        </div>
      </FieldGroup>
      {value.slots.length ? <WeekOverview slots={value.slots} /> : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Operação do canal e voz final padrão                                */
/* ------------------------------------------------------------------ */

/** Nome humano da voz (catálogo de vozes finais do idioma); null enquanto o catálogo não tem a voz. */
const useVoiceName = (lang: string, choice: VoiceChoice | null) => {
  const catalog = useVoiceCatalog(lang, { purpose: 'final' });
  if (!choice) return null;
  const group = catalog.data?.providers.find((g) => g.id === choice.provider);
  const found = group?.voices.find((v) => v.id === choice.voice);
  return found && group ? `${found.label} · ${group.name}` : null;
};

const VoiceName: React.FC<{ lang: string; choice: VoiceChoice }> = ({ lang, choice }) => <>{useVoiceName(lang, choice) ?? 'voz escolhida'}</>;

const VoiceDefaultField: React.FC<{ lang: string; value: VoiceChoice | null; onChange: (v: VoiceChoice | null) => void }> = ({ lang, value, onChange }) => {
  const [open, setOpen] = useState(false);
  const name = useVoiceName(lang, value);
  return (
    <FieldGroup
      legend="Voz final padrão"
      hint="O Novo projeto deste canal já vem com esta voz (dá para trocar em cada projeto). A prévia continua com a voz local grátis."
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Mic size={16} aria-hidden="true" className="shrink-0 text-muted" />
        <span className={value ? 'text-fg' : 'text-muted'}>{value ? (name ?? 'Voz escolhida') : 'Nenhuma: vale a voz do estilo'}</span>
        <Button size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Fechar a lista' : value ? 'Trocar' : 'Escolher voz'}
        </Button>
        {value ? (
          <Button size="sm" variant="ghost" icon={X} onClick={() => onChange(null)}>
            Tirar
          </Button>
        ) : null}
      </div>
      {open ? (
        <div className="max-h-72 overflow-y-auto rounded-lg border border-line bg-canvas p-3">
          <VoicePicker
            lang={lang}
            purpose="final"
            value={value}
            onChange={(v) => {
              onChange(v);
              setOpen(false);
            }}
          />
        </div>
      ) : null}
    </FieldGroup>
  );
};

const OpsFields: React.FC<{ form: Form; setForm: (f: Form) => void; errors: OpsErrors; showErrors: boolean }> = ({ form, setForm, errors, showErrors }) => {
  const err = (k: keyof OpsErrors) => (showErrors ? errors[k] : undefined);
  const warning = thinDaysText(form.posting.slots, intIn(form.posts_per_day, 0, 20) ?? 0);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="font-display text-[15px] font-semibold text-fg">Operação do canal</h3>
        <p className="text-[13px] text-muted">Meta de posts, estoque mínimo (alerta na página Hoje), hashtag dos Shorts e créditos da voz.</p>
      </div>
      <Field label="Público" hint="Para quem é o canal: idade, interesses, país.">
        <Textarea rows={2} value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })} placeholder="ex.: gamers de 18 a 34 anos no Brasil" />
      </Field>
      <Field label="Cadência" hint="Texto livre: o ritmo combinado do canal.">
        <Input value={form.cadence} onChange={(e) => setForm({ ...form, cadence: e.target.value })} placeholder="ex.: 2 Shorts por dia + 1 vídeo longo por semana" />
      </Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Posts por dia" hint="Vazio = sem meta." error={err('posts_per_day')}>
          <Input
            inputMode="numeric"
            value={form.posts_per_day}
            invalid={Boolean(err('posts_per_day'))}
            onChange={(e) => setForm({ ...form, posts_per_day: e.target.value })}
            placeholder="0"
          />
        </Field>
        <Field label="Estoque mínimo" hint="Prontos ou agendados. Vazio = sem alerta." error={err('min_stock')}>
          <Input
            inputMode="numeric"
            value={form.min_stock}
            invalid={Boolean(err('min_stock'))}
            onChange={(e) => setForm({ ...form, min_stock: e.target.value })}
            placeholder="0"
          />
        </Field>
        <Field label="Formato do estoque">
          <Select value={form.stock_format} onChange={(e) => setForm({ ...form, stock_format: e.target.value as StockFormat })}>
            {STOCK_FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {warning ? <p className="-mt-2 text-[13px] text-amber-200">{warning}</p> : null}
      <Field label="Hashtag dos Shorts" hint="Vai no fim do título dos Shorts cortados do longo." error={err('short_hashtag')}>
        <Input
          value={form.short_hashtag}
          maxLength={60}
          invalid={Boolean(err('short_hashtag'))}
          onChange={(e) => setForm({ ...form, short_hashtag: e.target.value })}
          placeholder="#plantao"
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Créditos por caractere" hint="Medido no uso real (ex.: 0,44 no ElevenLabs). Mostra os créditos antes de renderizar." error={err('credits_per_char')}>
          <Input
            inputMode="decimal"
            value={form.credits_per_char}
            invalid={Boolean(err('credits_per_char'))}
            onChange={(e) => setForm({ ...form, credits_per_char: e.target.value })}
            placeholder="0,44"
          />
        </Field>
        <Field label="Preço do crédito (US$)" hint="Opcional: converte os créditos em dólar." error={err('credit_price_usd')}>
          <Input
            inputMode="decimal"
            value={form.credit_price_usd}
            invalid={Boolean(err('credit_price_usd'))}
            onChange={(e) => setForm({ ...form, credit_price_usd: e.target.value })}
            placeholder="0,0003"
          />
        </Field>
      </div>
      <VoiceDefaultField lang={form.languages[0] ?? 'pt-BR'} value={form.voice_final} onChange={(voice_final) => setForm({ ...form, voice_final })} />
    </div>
  );
};

/** Resumo da operação no cartão do canal. */
const OpsSummary: React.FC<{ c: Channel }> = ({ c }) => {
  const voice = c.voice_defaults?.final;
  const perDay = c.posts_per_day ?? 0;
  const stock = c.min_stock ?? 0;
  const unit = STOCK_FORMATS.find((f) => f.id === (c.stock_format ?? 'short'))?.unit ?? 'vídeos';
  const thinText = thinDaysText(c.posting.slots, perDay);
  const hasVoice = Boolean(voice?.provider && voice.voice);
  const badges = Boolean(perDay || stock || c.short_hashtag || c.credits_per_char || hasVoice);
  if (!c.audience && !c.cadence && !badges) return null;
  return (
    <div className="flex flex-col gap-1.5">
      {c.cadence ? (
        <div className="flex gap-1.5" title={c.cadence}>
          <CalendarClock size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
          <p className="line-clamp-2 min-w-0 text-[13px] leading-snug text-muted">{c.cadence}</p>
        </div>
      ) : null}
      {c.audience ? (
        <div className="flex gap-1.5" title={c.audience}>
          <Users size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
          <p className="line-clamp-2 min-w-0 text-[13px] leading-snug text-muted">{c.audience}</p>
        </div>
      ) : null}
      {badges ? (
        <ul aria-label="Operação do canal" className="flex flex-wrap gap-1.5">
          {perDay ? (
            <li>
              <Badge icon={Target} tone={thinText ? 'warn' : 'neutral'} title={thinText ?? undefined}>
                {perDay} {perDay === 1 ? 'post' : 'posts'} por dia{thinText ? ' · faltam horários' : ''}
              </Badge>
            </li>
          ) : null}
          {stock ? (
            <li>
              <Badge icon={Package} title="Estoque mínimo de vídeos prontos ou agendados">
                Estoque mínimo: {stock} {unit}
              </Badge>
            </li>
          ) : null}
          {c.short_hashtag ? (
            <li>
              <Badge icon={Hash} title="Hashtag no fim do título dos Shorts">
                {c.short_hashtag.replace(/^#/, '')}
              </Badge>
            </li>
          ) : null}
          {c.credits_per_char ? (
            <li>
              <Badge icon={Coins} title="Créditos da voz final por caractere">
                {numText(c.credits_per_char)} créditos/caractere
                {c.credit_price_usd ? ` · US$ ${numText(c.credit_price_usd)} o crédito` : ''}
              </Badge>
            </li>
          ) : null}
          {voice && hasVoice ? (
            <li>
              <Badge icon={Mic} title="Voz final padrão dos projetos novos">
                <VoiceName lang={c.languages[0] ?? 'pt-BR'} choice={voice} />
              </Badge>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Cartão do canal                                                     */
/* ------------------------------------------------------------------ */

const NextSlots: React.FC<{ channelId: string }> = ({ channelId }) => {
  const slots = useQuery({ queryKey: ['channel-slots', channelId], queryFn: () => api.get<ChannelSlots>(`/api/channels/${channelId}/slots?count=3`) });
  if (slots.isLoading) return <Skeleton className="h-5 w-56" />;
  if (slots.error) return <p className="text-[13px] text-red-300">Não foi possível calcular os próximos horários.</p>;
  if (!slots.data?.configured) return null;
  const zone = slots.data.timezone;
  const list = slots.data.slots;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[13px] text-muted">
        Próximos horários livres <span className="text-subtle">· no fuso do canal, {zoneLabel(zone)}</span>
      </div>
      {list.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {list.map((s) => (
            <li key={s}>
              <Badge icon={CalendarClock} title={sameOffsetAsBrowser(zone) ? undefined : `No seu computador: ${whenIn(s)}`}>
                <span className="tabular-nums">{whenIn(s, zone)}</span>
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-subtle">Nenhum horário livre nos próximos 60 dias.</p>
      )}
      {list.length && !sameOffsetAsBrowser(zone) ? (
        <p className="text-[13px] text-subtle">
          No seu computador, {zoneLabel(browserZone)}: <span className="tabular-nums">{list.map((s) => whenIn(s)).join(' · ')}</span>
        </p>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Canal do YouTube ligado                                             */
/* ------------------------------------------------------------------ */

const compactNumber = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 });

const TOKEN_BADGES: Record<ChannelTokenStatus, { tone: BadgeTone; label: string; title: string }> = {
  ok: { tone: 'good', label: 'Publica neste canal', title: 'Vinculado pelo login do Google: os envios ao YouTube dos projetos deste canal vão para ele.' },
  global: { tone: 'good', label: 'Publica pela conta conectada', title: 'A conta conectada na aba Vídeo e publicação é deste mesmo canal do YouTube.' },
  error: { tone: 'warn', label: 'Acesso expirado', title: 'O Google recusou o acesso a este canal. Vincule de novo com o login.' },
  public: {
    tone: 'neutral',
    label: 'Só leitura (link público)',
    title: 'Ligado pelo link: o app lê os dados, mas não publica. Para publicar, vincule com o login do Google.',
  },
  missing: { tone: 'warn', label: 'Sem acesso para publicar', title: 'Desvinculado. Para publicar neste canal, vincule de novo com o login do Google.' },
};

const Avatar: React.FC<{ y: ChannelYouTube | null }> = ({ y }) =>
  y?.avatar ? (
    <img src={`${fileUrl(y.avatar)}?v=${encodeURIComponent(y.imported_at ?? '')}`} alt="" className="h-10 w-10 shrink-0 rounded-full border border-line object-cover" />
  ) : y ? (
    <span aria-hidden="true" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line bg-raised text-muted">
      <SquarePlay size={18} />
    </span>
  ) : null;

const YouTubeInfo: React.FC<{ y: ChannelYouTube }> = ({ y }) => {
  const badge = TOKEN_BADGES[y.token] ?? TOKEN_BADGES.missing;
  const stats = [
    y.subscribers != null ? `${compactNumber.format(y.subscribers)} ${y.subscribers === 1 ? 'inscrito' : 'inscritos'}` : null,
    y.videos != null ? `${y.videos} ${y.videos === 1 ? 'vídeo' : 'vídeos'}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const recent = y.recent_videos ?? [];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
        <a
          href={y.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-w-0 items-center gap-1 text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"
        >
          <span className="truncate">{y.handle || 'Ver no YouTube'}</span>
          <ExternalLink size={12} aria-hidden="true" className="shrink-0" />
          <span className="sr-only"> (abre o YouTube em outra aba)</span>
        </a>
        {stats ? <span className="tabular-nums text-muted">{stats}</span> : null}
        <Badge tone={badge.tone} title={y.token === 'error' && y.token_error ? `${badge.title} (${y.token_error})` : badge.title}>
          {badge.label}
        </Badge>
      </div>
      {y.description ? (
        <p className="line-clamp-2 text-[13px] leading-snug text-muted" title={y.description}>
          {y.description}
        </p>
      ) : null}
      {recent.length ? (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-muted hover:text-fg">Últimos vídeos ({recent.length})</summary>
          <ul className="mt-1.5 flex flex-col gap-1 pl-4">
            {recent.map((v, i) => (
              <li key={v.video_id || i} className="list-disc truncate text-muted" title={v.title}>
                {v.title}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
};

/** Quantos personagens fixos o canal tem (`style_bible.characters`). */
const characterCount = (c: Channel) => (Array.isArray(c.style_bible?.characters) ? c.style_bible.characters.length : 0);

type CardActions = {
  onEdit: () => void;
  onDelete: () => void;
  onLink: () => void;
  onRefresh: () => void;
  onUnlink: () => void;
  onForget: () => void;
  onSuggest: () => void;
  onCharacters: () => void;
};

/** Playlist padrão do canal: os envios dos projetos dele entram nela. A lista só é lida ao abrir (gasta cota). */
const ChannelPlaylist: React.FC<{ channel: Channel }> = ({ channel: c }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const save = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.put<{ channel: Channel }>(`/api/channels/${c.id}/youtube/playlist`, { id: id || null, title }),
    onSuccess: ({ channel }) => {
      client.invalidateQueries({ queryKey: ['channels'] });
      setOpen(false);
      toast({
        tone: 'good',
        title: channel.youtube_playlist
          ? `Os envios de “${c.name}” vão para “${channel.youtube_playlist.title || 'a playlist'}”`
          : `Os envios de “${c.name}” não entram em playlist`,
      });
    },
  });
  const current = c.youtube_playlist ?? null;
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <ListVideo size={14} aria-hidden="true" className="shrink-0 text-subtle" />
        <span className="text-muted">
          Playlist dos envios: <span className="text-fg">{current ? current.title || 'escolhida' : 'nenhuma'}</span>
        </span>
        {!open ? (
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            {current ? 'Trocar' : 'Escolher'}
          </Button>
        ) : null}
      </div>
      {open ? (
        <div className="flex flex-wrap items-end gap-2">
          <PlaylistPicker
            className="min-w-56 flex-1"
            label="Playlist padrão"
            scope={{ channel_id: c.id }}
            value={current?.id ?? ''}
            disabled={save.isPending}
            onChange={(id, title) => save.mutate({ id, title })}
            extra={[{ value: '', label: 'Nenhuma playlist' }]}
          />
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
            Fechar
          </Button>
          {save.error ? <ErrorBox error={save.error} title="Playlist não salva" onClose={() => save.reset()} className="w-full" /> : null}
        </div>
      ) : null}
    </div>
  );
};

const ChannelCard: React.FC<
  {
    channel: Channel;
    styleName: string | null;
    editing: boolean;
    deleting: boolean;
    refreshing: boolean;
    suggesting: boolean;
    /** Outra sugestão da IA em andamento: uma por vez (a resposta de uma não passa por cima da outra). */
    suggestDisabled: boolean;
  } & CardActions
> = ({
  channel: c,
  styleName,
  editing,
  onEdit,
  onDelete,
  deleting,
  onLink,
  onRefresh,
  onUnlink,
  onForget,
  onSuggest,
  onCharacters,
  refreshing,
  suggesting,
  suggestDisabled,
}) => {
  const y = c.youtube ?? null;
  const hasToken = y?.token === 'ok' || y?.token === 'error';
  return (
    <article
      aria-label={c.name}
      className={`flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 transition-colors ${
        editing ? 'border-accent/60 ring-1 ring-accent/30' : 'border-line hover:border-line-strong'
      }`}
    >
      {/* Em tela estreita os botões descem para a linha de baixo (e quebram entre si) em vez de espremer o nome. */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-[12rem] flex-1 items-center gap-3">
          <Avatar y={y} />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display text-[15px] font-semibold leading-6 text-fg [overflow-wrap:anywhere]">{c.name}</h2>
              {editing ? <Badge tone="accent" icon={Pencil}>Editando</Badge> : null}
            </div>
            {c.niche ? <div className="text-[13px] text-muted">{c.niche}</div> : null}
          </div>
        </div>
        <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-1">
          <Link to={`/pesquisa?canal=${c.id}`} className={buttonClass({ size: 'sm' })} aria-label={`Pesquisar vídeos para o canal ${c.name}`}>
            <Telescope size={14} strokeWidth={2} aria-hidden="true" className="shrink-0" />
            Pesquisar vídeos
          </Link>
          <Button size="sm" icon={UserRound} onClick={onCharacters} aria-label={`Personagens do canal ${c.name}`}>
            Personagens{characterCount(c) ? ` (${characterCount(c)})` : ''}
          </Button>
          <Button size="sm" icon={Pencil} onClick={onEdit} aria-label={`Editar o canal ${c.name}`}>
            Editar
          </Button>
          <IconButton label={`Apagar o canal ${c.name}`} icon={Trash2} size="sm" busy={deleting} onClick={onDelete} className="hover:text-red-300" />
        </div>
      </div>
      {y ? (
        <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-canvas p-3">
          <YouTubeInfo y={y} />
          <div role="group" aria-label={`Ações do YouTube de ${c.name}`} className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              icon={Sparkles}
              busy={suggesting}
              disabled={suggestDisabled}
              onClick={onSuggest}
              title="Usa o Claude (gasta um pouco do limite do plano). Você revisa antes de salvar."
            >
              Preencher com IA
            </Button>
            <Button size="sm" variant="ghost" icon={RefreshCw} busy={refreshing} onClick={onRefresh}>
              Atualizar dados
            </Button>
            {y.token !== 'ok' ? (
              <Button size="sm" variant="ghost" icon={LogIn} onClick={onLink}>
                {y.token === 'public' ? 'Vincular com login' : 'Vincular de novo'}
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" icon={Unlink} onClick={hasToken ? onUnlink : onForget}>
              {hasToken ? 'Desvincular' : 'Remover link'}
            </Button>
          </div>
          {y.token === 'ok' || y.token === 'global' ? <ChannelPlaylist channel={c} /> : null}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        {c.languages.map((l) => (
          <Badge key={l} icon={Globe}>
            {langLabel(l)}
          </Badge>
        ))}
        <Badge icon={Palette} title="Estilo padrão dos novos projetos deste canal">
          {styleName ?? 'Sem estilo padrão'}
        </Badge>
      </div>
      {c.persona ? (
        <div className="flex gap-1.5" title={c.persona}>
          <UserRound size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
          <p className="line-clamp-2 min-w-0 text-[13px] leading-snug text-muted">{c.persona}</p>
        </div>
      ) : null}
      <OpsSummary c={c} />
      {!y ? (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-subtle">
          Não ligado ao YouTube.
          <Button size="sm" variant="ghost" icon={SquarePlay} onClick={onLink}>
            Vincular ao YouTube
          </Button>
        </div>
      ) : null}
      <div className="flex flex-col gap-2 border-t border-line pt-3">
        {c.posting.slots.length ? (
          <>
            <ul aria-label="Horários de postagem" className="flex flex-wrap gap-1.5">
              {c.posting.slots.map((s, i) => (
                <li key={i}>
                  <Badge icon={Clock} title={s.reason || undefined}>
                    {describeDays(s.weekdays)} · <span className="tabular-nums">{s.time}</span>
                    {s.reason ? <span className="max-w-[16rem] truncate text-subtle">· {s.reason}</span> : null}
                  </Badge>
                </li>
              ))}
            </ul>
            <span className="text-[13px] text-subtle">
              Hora de {zoneCity(c.posting.timezone)}
              {c.posting.timezone === browserZone ? ' (este computador)' : ''}
            </span>
          </>
        ) : (
          <p className="text-[13px] text-subtle">{NO_SLOTS_TEXT}</p>
        )}
        <NextSlots channelId={c.id} />
      </div>
    </article>
  );
};

/* ------------------------------------------------------------------ */
/* Vincular canal do YouTube (login do Google ou link colado)          */
/* ------------------------------------------------------------------ */

const SETUP_STEPS = [
  'No Google Cloud (console.cloud.google.com), crie um projeto e ative a YouTube Data API v3.',
  'Em “Tela de permissão OAuth”, escolha Externo e publique o app (“In production”).',
  'Em “Credenciais”, crie um ID do cliente OAuth do tipo App para computador.',
  'Cole o client ID e o client secret em Configurações › Publicação › YouTube.',
];

const LinkSheet: React.FC<{
  /** Canal do Vulto que recebe o vínculo; null cria um canal novo. */
  target: Channel | null;
  onClose: () => void;
  onManual?: () => void;
  onLinked: (channelId: string, name: string, created: boolean, warnings?: string[]) => void;
}> = ({ target, onClose, onManual, onLinked }) => {
  const loginId = useId();
  const pasteId = useId();
  const status = useQuery({ queryKey: ['youtube-status'], queryFn: () => api.get<YouTubeAccountStatus>('/api/youtube/status') });
  const [url, setUrl] = useState('');
  const [tried, setTried] = useState(false);
  const [login, setLogin] = useState<YouTubeLinkStart | null>(null);
  const [revenue, setRevenue] = useState(false);
  const finished = useRef<string | null>(null);
  // Canal que já tem a receita ligada começa com a caixa marcada (vincular de novo sem ela desliga a receita).
  const targetYt = target?.youtube?.channel_id;
  const hadRevenue = Boolean(targetYt && status.data?.linked?.some((l) => l.channel_id === targetYt && l.revenue));
  const revenueSynced = useRef(false);
  useEffect(() => {
    if (revenueSynced.current || !status.data) return;
    revenueSynced.current = true;
    if (hadRevenue) setRevenue(true);
  }, [status.data, hadRevenue]);

  const start = useMutation({
    mutationFn: () => api.post<YouTubeLinkStart>('/api/youtube/link', { channel_id: target?.id ?? null, revenue }),
    onSuccess: (data) => {
      setLogin(data);
      window.open(data.auth_url, '_blank', 'noopener');
    },
  });
  // Confere a cada 2 s se o login terminou (a aba do Google chama o servidor local no fim).
  const poll = useQuery({
    queryKey: ['youtube-link', login?.state],
    queryFn: () => api.get<YouTubeLinkStatus>(`/api/youtube/link/${login?.state}`),
    enabled: Boolean(login),
    refetchInterval: (q) => (q.state.data && q.state.data.status !== 'pending' ? false : 2000),
  });
  const result = login ? poll.data : undefined;
  useEffect(() => {
    if (!login || result?.status !== 'done' || !result.vulto_channel_id || finished.current === login.state) return;
    finished.current = login.state;
    onLinked(result.vulto_channel_id, result.channel_title ?? '', Boolean(result.created), result.warnings);
  }, [login, result, onLinked]);
  const failed = result?.status === 'error' || result?.status === 'unknown';

  const paste = useMutation({
    mutationFn: () => api.post<{ channel: Channel; created: boolean }>('/api/channels/from-link', { url: url.trim(), channel_id: target?.id ?? null }),
    onSuccess: (data) => onLinked(data.channel.id, data.channel.name, data.created),
  });
  const urlMissing = !url.trim();
  const submit = () => {
    setTried(true);
    if (urlMissing || paste.isPending) return;
    paste.mutate();
  };

  const s = status.data;
  return (
    <Sheet
      title={target ? `Vincular “${target.name}” ao YouTube` : 'Vincular canal do YouTube'}
      description={
        target
          ? 'O canal passa a ter o nome, a foto e os vídeos do canal do YouTube. Nicho, persona e horários ficam.'
          : 'Traga o canal que você já tem: nome, foto, descrição e vídeos recentes.'
      }
      onRequestClose={onClose}
      onSubmit={submit}
      footer={<Button onClick={onClose}>Fechar</Button>}
    >
      <section aria-labelledby={loginId} className="flex flex-col gap-2.5">
        <h3 id={loginId} className="font-display text-[15px] font-semibold text-fg">
          Entrar com o Google <span className="font-sans text-xs font-normal text-subtle">· recomendado</span>
        </h3>
        {status.isLoading ? (
          <Skeleton className="h-16" />
        ) : status.error ? (
          <ErrorBox error={status.error} title="Não foi possível ver a conexão do YouTube" onRetry={() => status.refetch()} />
        ) : !s?.client_configured ? (
          <Notice
            tone="info"
            title="Falta configurar o acesso do app ao YouTube (uma vez)"
            actions={
              <Link to="/configuracoes#provedores-publish" className={buttonClass({ size: 'sm' })}>
                Abrir Configurações
              </Link>
            }
          >
            <ol className="mt-1 list-decimal space-y-1 pl-4">
              {SETUP_STEPS.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <p className="mt-1.5">Depois volte aqui e clique em Entrar com o Google. Enquanto isso, dá para colar o link do canal abaixo.</p>
          </Notice>
        ) : login && !failed ? (
          <div role="status" className="flex flex-col gap-2 rounded-lg border border-line bg-card p-3 text-[13px] text-muted">
            <p className="flex items-center gap-2 font-medium text-fg">
              <Spinner /> Esperando você terminar o login no Google…
            </p>
            <p>Na aba do Google, escolha a conta e o canal (contas de marca aparecem nessa tela) e autorize o acesso.</p>
            <div className="flex flex-wrap gap-2">
              <a href={login.auth_url} target="_blank" rel="noopener" className={buttonClass({ size: 'sm' })}>
                <ExternalLink size={14} aria-hidden="true" className="shrink-0" />
                Abrir o login de novo
                <span className="sr-only"> (abre em outra aba)</span>
              </a>
              <Button size="sm" variant="ghost" onClick={() => setLogin(null)}>
                Cancelar
              </Button>
            </div>
          </div>
        ) : (
          <>
            <p className="text-[13px] text-muted">
              Abre o login do Google numa nova aba. Escolha a conta e o canal: o app traz nome, @, descrição, foto, idioma e os vídeos recentes, e passa a
              publicar nesse canal.
            </p>
            {failed ? (
              <ErrorBox error={new Error(result?.error || 'O pedido de login expirou. Tente de novo.')} title="Não foi possível vincular" />
            ) : null}
            <ErrorBox error={start.error} title="Não foi possível abrir o login" onClose={() => start.reset()} />
            <Checkbox
              label="Trazer a receita do YouTube"
              description={
                hadRevenue
                  ? 'Este canal já traz a receita. Deixar desmarcada desliga a receita, que volta a ser lançada à mão em Dinheiro.'
                  : 'Opcional, para canal monetizado: a receita estimada entra sozinha em Dinheiro. Sem isso, nada muda e a receita segue lançada à mão.'
              }
              checked={revenue}
              onChange={setRevenue}
            />
            <Button variant="primary" icon={LogIn} className="self-start" busy={start.isPending} onClick={() => start.mutate()}>
              Entrar com o Google
            </Button>
          </>
        )}
      </section>

      <div aria-hidden="true" className="flex items-center gap-3 text-xs text-subtle">
        <span className="h-px flex-1 bg-line" />
        ou
        <span className="h-px flex-1 bg-line" />
      </div>

      <section aria-labelledby={pasteId} className="flex flex-col gap-2.5">
        <h3 id={pasteId} className="font-display text-[15px] font-semibold text-fg">
          Colar o link do canal
        </h3>
        <Field
          label="Link do canal"
          hint="Sem login: traz o nome, a foto e os títulos recentes. Por esse caminho o app não publica no canal."
          error={tried && urlMissing ? 'Cole o link do canal.' : undefined}
        >
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="youtube.com/@seucanal"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        <ErrorBox error={paste.error} title="Não foi possível ler o canal" onClose={() => paste.reset()} />
        <Button type="submit" icon={ClipboardPaste} className="self-start" busy={paste.isPending}>
          Buscar canal
        </Button>
      </section>

      {onManual ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4 text-[13px] text-muted">
          Prefere preencher tudo à mão?
          <Button size="sm" variant="ghost" icon={Plus} onClick={onManual}>
            Criar manualmente
          </Button>
        </div>
      ) : null}
    </Sheet>
  );
};

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

export const ChannelsPage: React.FC = () => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  // Volta da aba do login do Google: a lista confere de novo (o canal vinculado aparece sem recarregar).
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels'), refetchOnWindowFocus: true });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const [form, setForm] = useState<Form>(blank);
  const [initial, setInitial] = useState<string>('');
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [tried, setTried] = useState(false);
  /** Painel "Vincular canal do YouTube" aberto (target null: canal novo). */
  const [linking, setLinking] = useState<{ target: Channel | null } | null>(null);
  const [charactersOf, setCharactersOf] = useState<Channel | null>(null);
  /** Proposta da IA aplicada no formulário aberto (ainda não salva). */
  const [suggestion, setSuggestion] = useState<ChannelSuggestion | null>(null);

  const editingChannel = editing ? channels.data?.find((c) => c.id === editing) : undefined;

  const save = useMutation({
    mutationFn: () => {
      const body = bodyOf(form, editingChannel);
      return editing ? api.put(`/api/channels/${editing}`, body) : api.post('/api/channels', body);
    },
    onSuccess: () => {
      toast({ tone: 'good', title: editing ? `Canal “${form.name.trim()}” salvo` : `Canal “${form.name.trim()}” criado` });
      setForm(blank());
      setEditing(null);
      setOpen(false);
      setSuggestion(null);
      client.invalidateQueries({ queryKey: ['channels'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/channels/${id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['channels'] });
      client.invalidateQueries({ queryKey: ['youtube-status'] });
    },
    // O erro aparece num aviso (o cartão pode estar longe do topo da página).
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível apagar o canal', description: errorMessage(err) }),
  });
  const refresh = useMutation({
    mutationFn: (c: Channel) => api.post<{ channel: Channel; warnings: string[] }>(`/api/channels/${c.id}/youtube/refresh`),
    onSuccess: (data) =>
      toast({
        tone: data.warnings.length ? 'warn' : 'good',
        title: `Dados de “${data.channel.name}” atualizados`,
        description: data.warnings.join(' ') || undefined,
      }),
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível atualizar os dados do canal', description: errorMessage(err) }),
    // O nome pode mudar, e um token recusado aparece no cartão: recarrega nos dois casos.
    onSettled: () => client.invalidateQueries({ queryKey: ['channels'] }),
  });
  const unlink = useMutation({
    mutationFn: ({ c, forget }: { c: Channel; forget: boolean }) => api.del(`/api/channels/${c.id}/youtube${forget ? '?forget=true' : ''}`),
    onSuccess: (_data, { c, forget }) => {
      toast({ tone: 'good', title: forget ? `Link do YouTube removido de “${c.name}”` : `“${c.name}” desvinculado do YouTube` });
      client.invalidateQueries({ queryKey: ['channels'] });
      client.invalidateQueries({ queryKey: ['youtube-status'] });
    },
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível desvincular', description: errorMessage(err) }),
  });
  const suggest = useMutation({
    mutationFn: (c: Channel) => api.post<ChannelSuggestion>(`/api/channels/${c.id}/suggest`),
    onSuccess: (s, c) => void offerSuggestion(c, s),
    onError: (err) => toast({ tone: 'bad', title: 'A IA não conseguiu sugerir', description: errorMessage(err) }),
  });

  const styleName = (id: string | null) => {
    if (!id) return null;
    const found = styles.data?.find((s) => s.style.id === id)?.style.name;
    if (found) return found;
    return styles.isLoading ? 'Carregando…' : styles.error ? 'Estilo indisponível' : 'Estilo removido';
  };

  /** `base`: o que conta como "sem alterações" (a proposta da IA já abre como alteração não salva). */
  const openSheet = (next: Form, id: string | null, base?: Form) => {
    save.reset();
    setTried(false);
    setSuggestion(null);
    setForm(next);
    setInitial(JSON.stringify(base ?? next));
    setEditing(id);
    setOpen(true);
  };

  const applySuggestion = (c: Channel, s: ChannelSuggestion) => {
    const base = formOf(c);
    openSheet(
      {
        ...base,
        niche: s.niche || base.niche,
        persona: s.persona || base.persona,
        languages: s.languages.length ? s.languages : base.languages,
        default_style_id: s.default_style_id ?? base.default_style_id,
        identity: s.identity || base.identity,
      },
      c.id,
      base,
    );
    setSuggestion(s);
  };

  /** A resposta da IA leva alguns segundos: se o usuário abriu outro painel nesse meio-tempo, pergunta antes de trocar. */
  const offerSuggestion = async (c: Channel, s: ChannelSuggestion) => {
    if (dirty || linking) {
      const ok = await confirm({
        title: `Abrir a sugestão da IA para “${c.name}”?`,
        description: dirty
          ? 'O que você mudou no painel aberto ainda não foi salvo e será descartado.'
          : 'O painel de vincular canal será fechado.',
        confirmLabel: 'Abrir sugestão',
        cancelLabel: 'Agora não',
      });
      if (!ok) return;
      setLinking(null);
    }
    applySuggestion(c, s);
  };

  const onLinked =(channelId: string, name: string, created: boolean, warnings: string[] = []) => {
    setLinking(null);
    client.invalidateQueries({ queryKey: ['channels'] });
    client.invalidateQueries({ queryKey: ['youtube-status'] });
    client.invalidateQueries({ queryKey: ['channel-slots', channelId] });
    toast({
      tone: warnings.length ? 'warn' : 'good',
      title: created ? `Canal “${name}” criado com os dados do YouTube` : `Canal “${name}” atualizado com os dados do YouTube`,
      description: warnings.length
        ? warnings.join(' ')
        : 'Para sugerir nicho, persona e identidade, use “Preencher com IA” no cartão do canal.',
    });
  };

  const dirty = open && JSON.stringify(form) !== initial;

  // Sem useBlocker (BrowserRouter): ao menos recarregar ou fechar a janela pede confirmação com alterações pendentes.
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

  const closeSheet = async () => {
    if (save.isPending) return;
    if (dirty) {
      const ok = await confirm({
        title: 'Descartar as alterações?',
        description: suggestion
          ? 'A sugestão da IA ainda não foi salva neste canal.'
          : editing
            ? 'O que você mudou neste canal ainda não foi salvo.'
            : 'O canal novo ainda não foi salvo.',
        confirmLabel: 'Descartar',
        cancelLabel: 'Continuar editando',
        tone: 'danger',
      });
      if (!ok) return;
    }
    setOpen(false);
    setEditing(null);
    setSuggestion(null);
    setForm(blank());
  };

  const askDelete = async (c: Channel) => {
    const ok = await confirm({
      title: `Apagar o canal “${c.name}”?`,
      description: c.youtube
        ? 'A persona, os horários e o vínculo com o YouTube deste canal são apagados (o canal no YouTube continua lá). Os projetos continuam existindo, mas ficam sem canal. Não dá para desfazer.'
        : 'A persona e os horários de postagem deste canal são apagados. Os projetos continuam existindo, mas ficam sem canal. Não dá para desfazer.',
      confirmLabel: 'Apagar canal',
      tone: 'danger',
    });
    if (!ok) return;
    remove.mutate(c.id, {
      onSuccess: () => {
        toast({ tone: 'good', title: `Canal “${c.name}” apagado` });
        if (editing === c.id) {
          setOpen(false);
          setEditing(null);
          setForm(blank());
        }
      },
    });
  };

  const askUnlink = async (c: Channel) => {
    const ok = await confirm({
      title: `Desvincular “${c.name}” do YouTube?`,
      description:
        'O app apaga o acesso a este canal e deixa de publicar nele. O nome, a foto e os vídeos importados ficam. Para publicar de novo, vincule com o login do Google.',
      confirmLabel: 'Desvincular',
      tone: 'danger',
    });
    if (ok) unlink.mutate({ c, forget: false });
  };

  const askForget = async (c: Channel) => {
    const ok = await confirm({
      title: `Remover o link do YouTube de “${c.name}”?`,
      description: 'Os dados trazidos do YouTube (foto, descrição e vídeos recentes) saem do canal. Nicho, persona e horários ficam.',
      confirmLabel: 'Remover link',
      tone: 'danger',
    });
    if (ok) unlink.mutate({ c, forget: true });
  };

  const nameMissing = !form.name.trim();
  const badTime = form.posting.slots.some((s) => !validTime(s.time));
  const formOpsErrors = opsErrors(form);
  const badOps = Object.keys(formOpsErrors).length > 0;
  const badPronunciation = halfFilledPronunciation(form.pronunciation);
  const submit = () => {
    setTried(true);
    if (nameMissing || badTime || badOps || badPronunciation) {
      // Depois do render com os erros: leva o foco (e a rolagem) até o primeiro campo inválido.
      window.setTimeout(() => document.querySelector<HTMLElement>('dialog[open] [aria-invalid="true"]')?.focus(), 0);
      return;
    }
    if (save.isPending) return;
    save.mutate();
  };

  const list = channels.data ?? [];
  const userStyles = (styles.data ?? []).filter((s) => s.source === 'user');
  const presetStyles = (styles.data ?? []).filter((s) => s.source !== 'user');
  const extraLanguages = form.languages.slice(1);
  const knownStyle = !form.default_style_id || (styles.data ?? []).some((s) => s.style.id === form.default_style_id);

  const createActions = (
    <>
      <Button variant="primary" icon={SquarePlay} onClick={() => setLinking({ target: null })}>
        Vincular canal do YouTube
      </Button>
      <Button icon={Plus} onClick={() => openSheet(blank(), null)}>
        Criar manualmente
      </Button>
    </>
  );

  return (
    <Page>
      <PageHeader
        title="Canais"
        description={
          // Sem canais, quem explica é o estado vazio (uma explicação só por tela).
          channels.data && !list.length
            ? undefined
            : 'A persona do canal entra nos roteiros. Os horários de postagem viram sugestões de agendamento na Agenda e na Fila do YouTube. Canal vinculado ao YouTube recebe os envios dos projetos dele.'
        }
        actions={list.length || channels.error ? createActions : null}
      />

      {channels.isLoading ? (
        <div className="grid gap-3 lg:grid-cols-2" aria-hidden="true">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : null}
      <ErrorBox error={channels.error} title="Não foi possível carregar os canais" onRetry={() => channels.refetch()} />

      {channels.data && !list.length ? (
        <Empty icon={Tv} title="Nenhum canal ainda" action={createActions}>
          Vincule o canal que você já tem no YouTube: o app traz o nome, a foto e os vídeos recentes. Ou crie um canal à mão, com persona, idioma, estilo
          padrão e horários de postagem.
        </Empty>
      ) : null}

      {list.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {list.map((c) => (
            <ChannelCard
              key={c.id}
              channel={c}
              styleName={styleName(c.default_style_id)}
              editing={open && editing === c.id}
              deleting={remove.isPending && remove.variables === c.id}
              refreshing={refresh.isPending && refresh.variables?.id === c.id}
              suggesting={suggest.isPending && suggest.variables?.id === c.id}
              suggestDisabled={suggest.isPending && suggest.variables?.id !== c.id}
              onEdit={() => openSheet(formOf(c), c.id)}
              onDelete={() => void askDelete(c)}
              onLink={() => setLinking({ target: c })}
              onRefresh={() => refresh.mutate(c)}
              onUnlink={() => void askUnlink(c)}
              onForget={() => void askForget(c)}
              onSuggest={() => suggest.mutate(c)}
              onCharacters={() => setCharactersOf(c)}
            />
          ))}
        </div>
      ) : null}

      {charactersOf ? (
        <CharactersSheet target={{ kind: 'channel', id: charactersOf.id, name: charactersOf.name }} onClose={() => setCharactersOf(null)} />
      ) : null}

      {linking ? (
        <LinkSheet
          target={linking.target}
          onClose={() => setLinking(null)}
          onManual={
            linking.target
              ? undefined
              : () => {
                  setLinking(null);
                  openSheet(blank(), null);
                }
          }
          onLinked={onLinked}
        />
      ) : null}

      {open ? (
        <Sheet
          title={editing ? `Editar “${editingChannel?.name ?? form.name}”` : 'Novo canal'}
          description={dirty ? 'Alterações não salvas.' : editing ? 'As mudanças valem para os próximos roteiros e agendamentos.' : 'Preencha o básico; o resto pode ficar para depois.'}
          onRequestClose={() => void closeSheet()}
          onSubmit={submit}
          footer={
            <>
              {/* Erro do servidor perto do botão (o corpo do painel pode estar rolado para baixo). */}
              <ErrorBox error={save.error} title="Não foi possível salvar o canal" onClose={() => save.reset()} className="w-full" />
              {tried && (nameMissing || badTime || badOps || badPronunciation) ? (
                <p role="alert" className="w-full text-[13px] text-red-300">
                  {nameMissing
                    ? 'Dê um nome ao canal.'
                    : badTime
                      ? 'Preencha a hora de todos os horários (ou remova os vazios).'
                      : badOps
                        ? 'Corrija os campos da operação do canal.'
                        : 'Complete as linhas da pronúncia (termo e como falar) ou remova as incompletas.'}
                </p>
              ) : null}
              <Button onClick={() => void closeSheet()}>Cancelar</Button>
              <Button type="submit" variant="primary" busy={save.isPending}>
                {editing ? 'Salvar canal' : 'Criar canal'}
              </Button>
            </>
          }
        >
          {suggestion ? (
            <Notice tone="info" icon={Sparkles} title="Sugestão da IA: revise antes de salvar">
              {suggestion.reason ? `${suggestion.reason} ` : ''}Nicho, persona, idiomas, estilo e identidade vieram do canal do YouTube. Nada foi salvo ainda.
            </Notice>
          ) : null}
          <Field
            label="Nome"
            hint={editingChannel?.youtube ? '“Atualizar dados” traz de novo o nome do canal no YouTube.' : undefined}
            error={tried && nameMissing ? 'Dê um nome ao canal.' : undefined}
          >
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="ex.: Curiosidades do Mundo" />
          </Field>
          <Field label="Nicho">
            <Input value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} placeholder="ex.: ciência do dia a dia, finanças pessoais, história" />
          </Field>
          <Field label="Persona do narrador" hint="Vai para o Claude junto com cada pedido de roteiro deste canal.">
            <Textarea rows={3} value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} placeholder="ex.: narrador sóbrio, curioso, que sempre cita fontes" />
          </Field>
          <Field label="Identidade visual" hint="Vai para o Claude ao planejar as cenas: paleta, clima, tipo de imagem.">
            <Textarea
              rows={2}
              value={form.identity}
              onChange={(e) => setForm({ ...form, identity: e.target.value })}
              placeholder="ex.: tons escuros com vermelho, fotos antigas, muito contraste"
            />
          </Field>
          <ErrorBox error={styles.error} title="Não foi possível carregar os estilos" onRetry={() => styles.refetch()} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Idioma principal">
              <Select
                value={form.languages[0]}
                onChange={(e) => setForm({ ...form, languages: [e.target.value, ...extraLanguages.filter((l) => l !== e.target.value)] })}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Estilo padrão">
              <Select value={form.default_style_id ?? ''} onChange={(e) => setForm({ ...form, default_style_id: e.target.value || null })}>
                <option value="">Nenhum (escolher em cada projeto)</option>
                {!knownStyle && form.default_style_id ? (
                  <option value={form.default_style_id}>
                    {styles.isLoading ? 'Carregando estilos…' : styles.error ? 'Estilo atual (lista indisponível)' : 'Estilo removido (escolha outro)'}
                  </option>
                ) : null}
                {userStyles.length ? (
                  <optgroup label="Meus estilos">
                    {userStyles.map((s) => (
                      <option key={s.style.id} value={s.style.id}>
                        {s.style.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {presetStyles.length ? (
                  <optgroup label="Presets">
                    {presetStyles.map((s) => (
                      <option key={s.style.id} value={s.style.id}>
                        {s.style.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </Select>
            </Field>
          </div>
          {extraLanguages.length ? (
            <div className="-mt-2 flex flex-col gap-1.5">
              <div className="text-[13px] text-muted">Outros idiomas do canal</div>
              <ul className="flex flex-wrap gap-1.5">
                {extraLanguages.map((l) => (
                  <li key={l} className="inline-flex items-center gap-0.5 rounded-md border border-line-strong bg-raised py-0.5 pl-2 pr-0.5 text-xs text-muted">
                    <Globe size={14} aria-hidden="true" className="mr-1 shrink-0" />
                    {langLabel(l)}
                    <IconButton
                      size="sm"
                      label={`Remover o idioma ${langLabel(l)}`}
                      icon={X}
                      onClick={() => setForm({ ...form, languages: form.languages.filter((x, i) => i === 0 || x !== l) })}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="border-t border-line pt-4">
            <PostingEditor value={form.posting} onChange={(posting) => setForm({ ...form, posting })} />
            {tried && badTime ? <p className="mt-2 text-[13px] text-red-300">Preencha a hora de todos os horários (ou remova os vazios).</p> : null}
          </div>
          <div className="border-t border-line pt-4">
            <OpsFields form={form} setForm={setForm} errors={formOpsErrors} showErrors={tried} />
          </div>
          <div className="flex flex-col gap-4 border-t border-line pt-4">
            <PronunciationEditor
              value={form.pronunciation}
              languages={form.languages}
              invalid={tried}
              onChange={(pronunciation) => setForm({ ...form, pronunciation })}
            />
            <GlossaryEditor value={form.glossary} languages={form.languages} onChange={(glossary) => setForm({ ...form, glossary })} />
          </div>
          <div className="border-t border-line pt-4">
            <NoveltyEditor value={form.novelty} onChange={(novelty) => setForm({ ...form, novelty })} />
          </div>
        </Sheet>
      ) : null}
    </Page>
  );
};
