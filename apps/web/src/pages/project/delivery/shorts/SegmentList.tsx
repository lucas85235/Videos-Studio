// Lista dos segmentos do longo para escolher os trechos do Short (marca e desmarca; os trechos saem sozinhos).
import React from 'react';
import type { ShortsOfProject } from '../../../../apiShorts';
import { fmtSeconds } from './model';

export const SegmentList: React.FC<{
  data: ShortsOfProject;
  selected: Set<number>;
  onToggle: (index: number) => void;
}> = ({ data, selected, onToggle }) => (
  <ol className="flex max-h-[420px] flex-col gap-1 overflow-y-auto rounded-lg border border-line p-1" aria-label="Segmentos do vídeo">
    {data.segments.map((seg) => {
      const on = selected.has(seg.index);
      const disabled = seg.duration_s == null;
      return (
        <li key={seg.id}>
          {seg.chapter_start && seg.chapter ? (
            <div className="px-2 pb-0.5 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">{seg.chapter}</div>
          ) : null}
          <label
            className={`flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors ${
              on ? 'bg-accent/10 ring-1 ring-accent/40' : 'hover:bg-raised'
            } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
          >
            <input
              type="checkbox"
              className="mt-1 shrink-0"
              checked={on}
              disabled={disabled}
              onChange={() => onToggle(seg.index)}
            />
            <span className="w-6 shrink-0 pt-px text-right font-mono text-[12px] text-muted">{seg.index + 1}</span>
            <span className="min-w-0 flex-1 text-fg">{seg.text}</span>
            <span className="shrink-0 pt-px font-mono text-[12px] text-muted">
              {seg.duration_s == null ? 'sem narração' : fmtSeconds(seg.duration_s)}
            </span>
          </label>
        </li>
      );
    })}
  </ol>
);
