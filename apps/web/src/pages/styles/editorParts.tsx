// Controles do Editor de estilo: grupos de opções, faixas, pesos, efeitos, cores e prévias.

import { Check, CircleX, TriangleAlert, type LucideIcon } from 'lucide-react';
import React, { useEffect, useId, useState } from 'react';
import type { StyleSpec } from '../../api';
import { EFFECT_LABELS, GRADE_LABELS, label, type LabelMap } from '../../labels';
import { Badge, FieldGroup, Input, Select } from '../../ui';
import { HEX_COLOR, fontStack, formatFixed, formatNumber } from './shared';

/* ------------------------------------------------------------------ */
/* Grupo de opções exclusivas (botões com aria-pressed num fieldset)   */
/* ------------------------------------------------------------------ */

export function Segmented<T extends string>({
  legend,
  hint,
  options,
  value,
  onChange,
}: {
  legend: React.ReactNode;
  hint?: React.ReactNode;
  options: { id: T; label: React.ReactNode; icon?: LucideIcon }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <FieldGroup legend={legend} hint={hint}>
      <div className="flex w-fit max-w-full flex-wrap gap-0.5 rounded-lg border border-line bg-canvas p-0.5">
        {options.map((o) => {
          const Icon = o.icon;
          const on = o.id === value;
          return (
            <button
              key={o.id}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(o.id)}
              className={`inline-flex min-h-7 items-center gap-1.5 rounded-md px-2.5 py-1 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                on ? 'bg-accent/15 text-accent ring-1 ring-inset ring-accent/50' : 'text-muted enabled:hover:text-fg'
              }`}
            >
              {Icon ? <Icon size={14} aria-hidden="true" /> : null}
              {o.label}
            </button>
          );
        })}
      </div>
    </FieldGroup>
  );
}

/* ------------------------------------------------------------------ */
/* Faixa (range) com o valor formatado                                 */
/* ------------------------------------------------------------------ */

export const RangeField: React.FC<{
  label: React.ReactNode;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  hint?: React.ReactNode;
  onChange: (value: number) => void;
}> = ({ label: text, value, min, max, step, display, hint, onChange }) => {
  const id = useId();
  const hintId = `${id}-dica`;
  const lo = Math.min(min, Number.isFinite(value) ? value : min);
  const hi = Math.max(max, Number.isFinite(value) ? value : max);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} data-field-label="" className="text-sm font-medium text-fg">
          {text}
        </label>
        <output htmlFor={id} className="text-sm tabular-nums text-muted">
          {display}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={lo}
        max={hi}
        step={step}
        value={Number.isFinite(value) ? value : min}
        aria-valuetext={display}
        aria-describedby={hint ? hintId : undefined}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-5 w-full cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-60"
      />
      {hint ? (
        <p id={hintId} className="text-[13px] leading-snug text-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Pesos de sorteio (câmera, transições)                               */
/* ------------------------------------------------------------------ */

const BAR_TONES = ['bg-fg/80', 'bg-fg/55', 'bg-fg/35', 'bg-muted/50', 'bg-fg/20', 'bg-muted/30'];

export const WeightsEditor: React.FC<{
  legend: string;
  hint: string;
  options: string[];
  labels: LabelMap;
  value: Record<string, number>;
  onChange: (value: Record<string, number>) => void;
  /** Pode ficar tudo desligado (o texto diz o que vale nesse caso). */
  allowEmpty?: string;
}> = ({ legend, hint, options, labels, value, onChange, allowEmpty }) => {
  const errorId = useId();
  const keys = [...options, ...Object.keys(value).filter((k) => !options.includes(k))];
  const weight = (k: string) => (typeof value[k] === 'number' && Number.isFinite(value[k]) ? value[k] : 0);
  const total = keys.reduce((sum, k) => sum + Math.max(0, weight(k)), 0);
  const pct = (k: string) => (total > 0 ? Math.round((Math.max(0, weight(k)) / total) * 100) : 0);
  const active = keys.filter((k) => weight(k) > 0);
  const set = (k: string, v: number) => {
    // O backend recusa um estilo sem nenhuma opção ligada: a última não pode ser zerada.
    if (!allowEmpty && v <= 0 && active.length === 1 && active[0] === k) return;
    const next = { ...value, [k]: v };
    if (!next[k]) delete next[k];
    onChange(next);
  };
  return (
    <FieldGroup legend={legend} hint={allowEmpty ? hint : `${hint} Pelo menos uma opção precisa ficar ligada.`}>
      <div className="flex flex-col gap-2">
        {active.length ? (
          <div className="flex h-2 w-full gap-px overflow-hidden rounded-full bg-line" aria-hidden="true">
            {active.map((k, i) => (
              <span key={k} className={`h-full ${BAR_TONES[i % BAR_TONES.length]}`} style={{ width: `${pct(k)}%` }} title={`${label(labels, k)}: ${pct(k)}%`} />
            ))}
          </div>
        ) : allowEmpty ? (
          <p className="text-[13px] leading-snug text-muted">{allowEmpty}</p>
        ) : (
          <p id={errorId} className="flex items-start gap-1.5 text-[13px] leading-snug text-red-300">
            <CircleX size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            Ligue pelo menos uma opção: sem nenhuma, o estilo não pode ser salvo.
          </p>
        )}
        <ul className="grid gap-x-6 gap-y-1.5 xl:grid-cols-2">
          {keys.map((k) => {
            const v = weight(k);
            const name = label(labels, k);
            const locked = !allowEmpty && v > 0 && active.length === 1;
            return (
              <li key={k} className="grid grid-cols-[minmax(7rem,1fr)_minmax(5rem,9rem)_4.5rem] items-center gap-2 text-[13px]">
                <span data-field-label="" className={`truncate ${v > 0 ? 'text-fg' : 'text-subtle'}`} title={name}>
                  {name}
                </span>
                <input
                  type="range"
                  min={0}
                  max={Math.max(10, v)}
                  step={0.5}
                  value={v}
                  aria-label={`Peso de ${name}`}
                  aria-valuetext={v > 0 ? `${formatNumber(v)}, ${pct(k)}% das cenas${locked ? ', única opção ligada' : ''}` : 'desligado'}
                  aria-describedby={active.length || allowEmpty ? undefined : errorId}
                  title={locked ? 'Única opção ligada: ligue outra antes de desligar esta.' : undefined}
                  onChange={(e) => set(k, Number(e.target.value))}
                  className="h-5 w-full cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-60"
                />
                <span className="text-right tabular-nums text-muted">{v > 0 ? `${pct(k)}%` : 'desligado'}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </FieldGroup>
  );
};

/* ------------------------------------------------------------------ */
/* Efeitos: chips liga/desliga com intensidade                         */
/* ------------------------------------------------------------------ */

export const EFFECT_TYPES = ['grain', 'vignette', 'grade', 'scanlines', 'chromatic', 'vhs', 'light_leak', 'film_scratches', 'halftone', 'duotone', 'sepia', 'blur_edges', 'flicker', 'dust'];
export const GRADES = ['neutral', 'vivid', 'warm-soft', 'clean-cool', 'cold-teal-dark', 'warm-sepia', 'noir', 'bleach-bypass', 'vhs-faded', 'blood-red', 'night-green'];
/** Efeitos que deixam a renderização bem mais lenta (USO.md). */
const HEAVY_EFFECTS = new Set(['vhs', 'blur_edges', 'chromatic']);

type Effect = StyleSpec['edit']['effects'][number];

export const EffectsEditor: React.FC<{ value: Effect[]; onChange: (value: Effect[]) => void; disabled?: boolean }> = ({ value, onChange, disabled }) => {
  const types = [...EFFECT_TYPES, ...value.map((e) => e.type).filter((t) => !EFFECT_TYPES.includes(t))];
  const find = (type: string) => value.find((e) => e.type === type);
  const toggle = (type: string) => {
    if (find(type)) onChange(value.filter((e) => e.type !== type));
    else onChange([...value, type === 'grade' ? { type, amount: 0.6, preset: 'neutral' } : { type, amount: 0.5 }]);
  };
  // Atualiza no lugar (a ordem dos efeitos é a ordem da cadeia de filtros).
  const patch = (type: string, changes: Partial<Effect>) => onChange(value.map((e) => (e.type === type ? { ...e, ...changes } : e)));
  const heavyOn = value.filter((e) => HEAVY_EFFECTS.has(e.type));

  return (
    <FieldGroup
      legend="Efeitos de imagem"
      hint="Ligue um efeito e ajuste a intensidade. Os marcados com “Renderização lenta” deixam a renderização final bem mais demorada."
    >
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {types.map((type) => {
          const effect = find(type);
          const on = Boolean(effect);
          const name = label(EFFECT_LABELS, type);
          return (
            <li
              key={type}
              className={`flex flex-col gap-2 rounded-lg border p-2 transition-colors ${
                on ? (disabled ? 'border-line-strong bg-raised' : 'border-accent/50 bg-accent/10') : 'border-line bg-canvas'
              } ${disabled ? 'opacity-70' : ''}`}
            >
              <button
                type="button"
                aria-pressed={on}
                onClick={() => toggle(type)}
                className="flex min-h-7 w-full items-center gap-2 rounded-md px-1 text-left text-[13px] disabled:cursor-not-allowed"
              >
                <span
                  aria-hidden="true"
                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                    on ? (disabled ? 'border-muted bg-muted text-canvas' : 'border-accent bg-accent text-on-accent') : 'border-line-strong bg-canvas'
                  }`}
                >
                  {on ? <Check size={12} strokeWidth={3} /> : null}
                </span>
                <span className={`min-w-0 flex-1 font-medium ${on ? 'text-fg' : 'text-muted'}`}>{name}</span>
                {HEAVY_EFFECTS.has(type) ? (
                  <Badge tone="warn" className="shrink-0">
                    Renderização lenta
                  </Badge>
                ) : null}
              </button>
              {effect ? (
                <div className="flex flex-col gap-2 px-1 pb-0.5">
                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={effect.amount}
                      aria-label={`Intensidade de ${name}`}
                      aria-valuetext={formatFixed(effect.amount)}
                      onChange={(e) => patch(type, { amount: Number(e.target.value) })}
                      className="h-5 min-w-0 flex-1 cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-60"
                    />
                    <span className="w-9 text-right text-[13px] tabular-nums text-muted">{formatFixed(effect.amount)}</span>
                  </div>
                  {type === 'grade' ? (
                    <Select aria-label="Tipo de gradação de cor" value={effect.preset ?? 'neutral'} onChange={(e) => patch(type, { preset: e.target.value })}>
                      {[...GRADES, ...(effect.preset && !GRADES.includes(effect.preset) ? [effect.preset] : [])].map((g) => (
                        <option key={g} value={g}>
                          {label(GRADE_LABELS, g)}
                        </option>
                      ))}
                    </Select>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {heavyOn.length >= 2 ? (
        <p className="flex items-start gap-1.5 text-[13px] text-amber-200">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          {heavyOn.length} efeitos pesados ligados ({heavyOn.map((e) => label(EFFECT_LABELS, e.type)).join(', ')}): a renderização final vai demorar mais.
        </p>
      ) : null}
    </FieldGroup>
  );
};

/* ------------------------------------------------------------------ */
/* Cor: seletor + código hexadecimal                                   */
/* ------------------------------------------------------------------ */

const toPickerValue = (hex: string) => {
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) return hex;
  if (/^#[0-9a-fA-F]{8}$/.test(hex)) return hex.slice(0, 7);
  if (/^#[0-9a-fA-F]{3}$/.test(hex)) return `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`;
  return '#000000';
};

export const ColorField: React.FC<{ label: string; value: string; onChange: (value: string) => void }> = ({ label: text, value, onChange }) => {
  const id = useId();
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const invalid = !HEX_COLOR.test(draft);
  const errorId = `${id}-erro`;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={`${id}-hex`} data-field-label="" className="text-sm font-medium text-fg">
        {text}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${text}: escolher no seletor`}
          value={toPickerValue(value)}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="h-8 w-10 shrink-0 cursor-pointer rounded-lg border border-line-input bg-canvas p-0.5 disabled:cursor-not-allowed disabled:opacity-60"
        />
        <Input
          id={`${id}-hex`}
          value={draft}
          invalid={invalid}
          aria-describedby={invalid ? errorId : undefined}
          spellCheck={false}
          maxLength={9}
          onChange={(e) => {
            const next = e.target.value.trim();
            setDraft(next);
            if (HEX_COLOR.test(next)) onChange(next.toUpperCase());
          }}
          onBlur={() => setDraft(value)}
          className="w-28 font-mono text-[13px] uppercase"
        />
      </div>
      {invalid ? (
        <p id={errorId} className="flex items-start gap-1.5 text-[13px] leading-snug text-red-300">
          <CircleX size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          Use o formato #RRGGBB (ex.: #FFD400). Ao sair do campo, volta a cor anterior.
        </p>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Prévias ao vivo                                                     */
/* ------------------------------------------------------------------ */

const SAMPLE = 'Ninguém sabia o que havia atrás daquela porta';
const FRAME_BG = 'linear-gradient(160deg, #3b3542 0%, #1d1c24 50%, #0b0b0e 100%)';

const frameSize = (style: StyleSpec) => {
  const vertical = style.format.type === 'short';
  const width = vertical ? 200 : 360;
  const [w] = String(style.format.resolution ?? '').split('x').map(Number);
  const resWidth = Number.isFinite(w) && w > 0 ? w : vertical ? 1080 : 1920;
  return { vertical, width, scale: width / resWidth };
};

export const CaptionPreview: React.FC<{ style: StyleSpec }> = ({ style }) => {
  const c = style.edit.captions;
  const { vertical, width, scale } = frameSize(style);
  const words = SAMPLE.split(' ').slice(0, Math.max(1, c.max_words_per_line));
  const shownWords = c.style === 'word' ? [words[Math.min(1, words.length - 1)]] : words;
  const highlightIndex = c.style === 'karaoke' ? Math.min(1, shownWords.length - 1) : c.style === 'word' ? 0 : -1;
  const justify = c.position === 'top' ? 'justify-start' : c.position === 'bottom' ? 'justify-end' : 'justify-center';
  const strokePx = Math.max(0, c.stroke * scale);
  return (
    <figure className="m-0 flex flex-col gap-1.5">
      <div
        role="img"
        aria-label={c.style === 'none' ? 'Prévia: sem legenda' : `Prévia da legenda: ${shownWords.join(' ')}`}
        className={`relative overflow-hidden rounded-lg border border-line-strong ${vertical ? 'aspect-[9/16]' : 'aspect-video'}`}
        style={{ width, maxWidth: '100%', background: FRAME_BG }}
      >
        <div className={`absolute inset-x-[6%] bottom-[12%] top-[10%] flex flex-col items-center ${justify}`}>
        {c.style === 'none' ? (
          <span className="rounded-md bg-black/50 px-2 py-1 text-xs text-muted">Sem legenda</span>
        ) : (
          <span
            className="text-center leading-tight"
            style={{
              fontFamily: fontStack(c.font),
              fontWeight: c.weight,
              fontSize: Math.max(12, c.size * scale),
              textTransform: c.uppercase ? 'uppercase' : 'none',
              WebkitTextStroke: strokePx ? `${strokePx}px ${c.stroke_color}` : undefined,
              paintOrder: 'stroke fill',
              background: c.background ?? undefined,
              padding: c.background ? '0.1em 0.3em' : undefined,
              borderRadius: c.background ? 4 : undefined,
            }}
          >
            {shownWords.map((w, i) => (
              <React.Fragment key={`${w}-${i}`}>
                {i ? ' ' : ''}
                <span style={{ color: i === highlightIndex ? c.highlight : c.color }}>{w}</span>
              </React.Fragment>
            ))}
          </span>
        )}
        </div>
        {style.edit.progress_bar ? (
          <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-1 bg-black/40">
            <span className="block h-full w-2/5" style={{ background: style.edit.progress_color }} />
          </span>
        ) : null}
      </div>
      <figcaption className="text-xs text-subtle">Prévia aproximada, no formato do vídeo.</figcaption>
    </figure>
  );
};

export const TextCardPreview: React.FC<{ style: StyleSpec }> = ({ style }) => {
  const t = style.edit.text_cards;
  const { vertical, width } = frameSize(style);
  return (
    <div
      role="img"
      aria-label="Prévia do cartão de texto"
      className={`flex flex-col items-center justify-center gap-2 overflow-hidden rounded-lg border border-line-strong px-4 text-center ${
        vertical ? 'aspect-[9/16]' : 'aspect-video'
      }`}
      style={{ width, maxWidth: '100%', background: t.background }}
    >
      <span className="text-sm" style={{ fontFamily: fontStack(t.font), color: t.accent }}>
        CAPÍTULO 1
      </span>
      <span className="text-2xl leading-tight" style={{ fontFamily: fontStack(t.font), color: t.color }}>
        O começo de tudo
      </span>
      <span className="h-1 w-12 rounded-full" style={{ background: t.accent }} />
    </div>
  );
};

export const ThumbnailPreview: React.FC<{ style: StyleSpec }> = ({ style }) => {
  const t = style.thumbnail;
  const layout =
    t.template === 'texto-centro'
      ? 'items-center justify-center text-center'
      : t.template === 'texto-baixo'
        ? 'items-center justify-end pb-[6%] text-center'
        : 'items-start justify-center pl-[6%] text-left';
  return (
    <div
      role="img"
      aria-label="Prévia da thumbnail"
      className={`flex aspect-video w-[300px] max-w-full flex-col overflow-hidden rounded-lg border border-line-strong ${layout}`}
      style={{ background: FRAME_BG }}
    >
      {t.template === 'sem-texto' ? (
        <span className="m-auto rounded-md bg-black/50 px-2 py-1 text-xs text-muted">Sem texto</span>
      ) : (
        <span className={`leading-none ${t.template === 'texto-grande-esquerda' ? 'max-w-[60%] text-3xl' : 'text-2xl'}`} style={{ fontFamily: fontStack(t.font) }}>
          <span style={{ color: t.color }}>O SEGREDO </span>
          <span style={{ color: t.accent }}>REVELADO</span>
        </span>
      )}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Índice de seções                                                    */
/* ------------------------------------------------------------------ */

export type EditorSection = { id: string; label: string; icon: LucideIcon };

/** Id do bloco de cada seção no DOM (alvo do índice e dos links #secao-…). */
export const sectionDomId = (id: string) => `secao-${id}`;

export const SectionNav: React.FC<{ sections: EditorSection[]; active: string; onGo: (id: string) => void }> = ({ sections, active, onGo }) => (
  <nav aria-label="Seções do estilo">
    <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
      {sections.map((s) => {
        const Icon = s.icon;
        const on = s.id === active;
        return (
          <li key={s.id} className="shrink-0">
            <a
              href={`#${sectionDomId(s.id)}`}
              aria-current={on ? 'location' : undefined}
              onClick={(e) => {
                e.preventDefault();
                onGo(s.id);
              }}
              className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[13px] transition-colors ${
                on ? 'bg-raised font-medium text-fg' : 'text-muted hover:bg-raised hover:text-fg'
              }`}
            >
              <Icon size={16} aria-hidden="true" className={on ? 'text-accent' : ''} />
              {s.label}
            </a>
          </li>
        );
      })}
    </ul>
  </nav>
);
