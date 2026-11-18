// Grade dos candidatos do pacote: a thumb (prévia 1280×720, ou 1080×1920 no Short) com o título embaixo, a nota da
// checagem de legibilidade e os avisos. Escolha do candidato da capa (rádio) e dos que entram no A/B (caixa).

import { Copy, ImageOff, Pencil, Trash2, TriangleAlert } from 'lucide-react';
import React from 'react';
import { fileUrl, type PackageCandidate } from '../../../api';
import { candidateWarnings, layersHash, TEMPLATE_LABELS, wordCount } from '../../../apiPackage';
import { Badge, IconButton, Spinner } from '../../../ui';

const pct = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? `${Math.round(v * 100)}%` : null);
const fixed = (v: unknown, digits = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : null);

/** Métricas da checagem que cabem no cartão (as que existirem no `score`). */
const scoreChips = (score: PackageCandidate['score']) =>
  [
    score.legib_168 != null ? { label: 'Leitura a 168 px', value: fixed(score.legib_168) } : null,
    score.text_h != null ? { label: 'Altura do texto', value: pct(score.text_h) } : null,
    score.face_h != null ? { label: 'Rosto', value: pct(score.face_h) } : null,
    score.luminance != null ? { label: 'Brilho', value: pct(score.luminance) } : null,
  ].filter((c): c is { label: string; value: string } => Boolean(c && c.value));

const CandidateCard: React.FC<{
  candidate: PackageCandidate;
  index: number;
  chosen: boolean;
  approved: boolean;
  editing: boolean;
  locked: boolean;
  rendering: boolean;
  isShort: boolean;
  disabled: boolean;
  onChoose: () => void;
  onApprove: (on: boolean) => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}> = ({ candidate: c, index, chosen, approved, editing, locked, rendering, isShort, disabled, onChoose, onApprove, onEdit, onDuplicate, onDelete }) => {
  const warnings = candidateWarnings(c, isShort);
  const chips = scoreChips(c.score ?? {});
  const words = wordCount(c.thumb_text);
  const name = `Candidato ${index + 1}`;
  return (
    <li
      className={`flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card ${
        chosen ? 'border-accent/70 ring-1 ring-accent/40' : editing ? 'border-line-strong' : 'border-line'
      }`}
    >
      <div className={`relative bg-canvas ${isShort ? 'mx-auto aspect-[9/16] w-1/2' : 'aspect-video w-full'}`}>
        {c.image ? (
          <img src={`${fileUrl(c.image)}?v=${layersHash(c)}`} alt={`Thumb do ${name.toLowerCase()}: ${c.concept || c.thumb_text}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-3 text-center text-[13px] text-subtle">
            {rendering ? <Spinner /> : <ImageOff size={20} aria-hidden="true" />}
            <span>{rendering ? 'Montando a prévia…' : 'Sem prévia ainda'}</span>
            {c.concept ? <span className="line-clamp-3 text-xs">{c.concept}</span> : null}
          </div>
        )}
        {rendering && c.image ? (
          <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-xs text-white">
            <Spinner className="h-3 w-3" /> atualizando
          </span>
        ) : null}
        <span className="absolute left-2 top-2 rounded-md bg-black/70 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-white">{index + 1}</span>
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="text-sm font-semibold leading-snug text-fg">{c.title || <span className="text-subtle">(sem título)</span>}</div>
        <div className="flex flex-wrap items-center gap-1.5">
          {c.thumb_text ? (
            <Badge tone={words > 3 && !isShort ? 'warn' : 'neutral'} title={`${words} palavra(s) na thumb`}>
              “{c.thumb_text}”
            </Badge>
          ) : null}
          <Badge>{TEMPLATE_LABELS[c.layers?.template] ?? c.layers?.template}</Badge>
          {c.source === 'manual' ? <Badge tone="info">Editado</Badge> : null}
        </div>
        {c.promise ? (
          <p className="text-[13px] leading-snug text-muted">
            <span className="text-subtle">Promessa: </span>
            {c.promise}
          </p>
        ) : null}
        {chips.length ? (
          <dl className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-subtle">
            {chips.map((chip) => (
              <div key={chip.label} className="flex gap-1">
                <dt>{chip.label}</dt>
                <dd className="tabular-nums text-muted">{chip.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {warnings.length ? (
          <ul className="flex flex-col gap-1 rounded-lg border border-amber-300/30 bg-amber-300/10 p-2 text-xs text-amber-100">
            {warnings.map((w) => (
              <li key={w} className="flex gap-1.5">
                <TriangleAlert size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
                {w}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
            <label className={`inline-flex items-center gap-1.5 ${locked || disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'}`}>
              <input type="radio" name="pacote-capa" checked={chosen} disabled={locked || disabled} onChange={onChoose} className="h-4 w-4 accent-accent" />
              <span className="text-fg">Capa</span>
              <span className="sr-only">: {name}</span>
            </label>
            <label className={`inline-flex items-center gap-1.5 ${locked || disabled || chosen ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'}`}>
              <input
                type="checkbox"
                checked={approved || chosen}
                disabled={locked || disabled || chosen}
                onChange={(e) => onApprove(e.target.checked)}
                className="h-4 w-4 rounded accent-accent"
              />
              <span className="text-fg">A/B</span>
              <span className="sr-only">: {name}</span>
            </label>
          </div>
          {locked ? null : (
            <div className="flex items-center gap-0.5">
              <IconButton label={`Editar o ${name.toLowerCase()}`} icon={Pencil} size="sm" disabled={disabled} onClick={onEdit} />
              <IconButton label={`Duplicar o ${name.toLowerCase()}`} icon={Copy} size="sm" disabled={disabled} onClick={onDuplicate} />
              <IconButton label={`Apagar o ${name.toLowerCase()}`} icon={Trash2} size="sm" disabled={disabled} onClick={onDelete} />
            </div>
          )}
        </div>
      </div>
    </li>
  );
};

export const CandidateGrid: React.FC<{
  candidates: PackageCandidate[];
  chosenId: string | null;
  approvedIds: Set<string>;
  editingId: string | null;
  locked: boolean;
  /** Ids com re-render em andamento (ou todos, com `renderingAll`). */
  rendering: Set<string>;
  renderingAll: boolean;
  isShort: boolean;
  disabled: boolean;
  onChoose: (id: string) => void;
  onApprove: (id: string, on: boolean) => void;
  onEdit: (id: string) => void;
  onDuplicate: (id: string) => void;
  onDelete: (c: PackageCandidate) => void;
}> = ({ candidates, chosenId, approvedIds, editingId, locked, rendering, renderingAll, isShort, disabled, ...actions }) => (
  <ul className={`grid gap-4 ${isShort ? 'grid-cols-[repeat(auto-fill,minmax(220px,1fr))]' : 'grid-cols-[repeat(auto-fill,minmax(300px,1fr))]'}`}>
    {candidates.map((c, i) => (
      <CandidateCard
        key={c.id}
        candidate={c}
        index={i}
        chosen={c.id === chosenId}
        approved={approvedIds.has(c.id)}
        editing={c.id === editingId}
        locked={locked}
        rendering={renderingAll || rendering.has(c.id)}
        isShort={isShort}
        disabled={disabled}
        onChoose={() => actions.onChoose(c.id)}
        onApprove={(on) => actions.onApprove(c.id, on)}
        onEdit={() => actions.onEdit(c.id)}
        onDuplicate={() => actions.onDuplicate(c.id)}
        onDelete={() => actions.onDelete(c)}
      />
    ))}
  </ul>
);
