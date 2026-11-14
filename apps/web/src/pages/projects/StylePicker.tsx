import { Check } from 'lucide-react';
import React from 'react';
import type { StyleItem } from '../../api';
import { FAMILY_LABELS, label, STRUCTURE_LABELS } from '../../labels';
import { Badge } from '../../ui';
import { durationLabel } from './meta';
import { FormatGlyph } from './parts';

// A proporção não é fixa por grupo (um vídeo curto pode ser 9:16, 1:1 ou 4:5): cada cartão mostra a sua.
const GROUPS: { type: 'short' | 'long'; title: string }[] = [
  { type: 'short', title: 'Vídeos curtos (Shorts, Reels, TikTok)' },
  { type: 'long', title: 'Vídeos longos' },
];

/** Estilos como cartões, separados por tipo (curto ou longo). Um clique escolhe. */
export const StylePicker: React.FC<{
  styles: StyleItem[];
  value: string;
  onChange: (id: string) => void;
}> = ({ styles, value, onChange }) => (
  <div className="flex flex-col gap-4">
    {GROUPS.map((group) => {
      const items = styles.filter((s) => s.style.format.type === group.type);
      if (!items.length) return null;
      return (
        <div key={group.type} role="group" aria-label={group.title} className="flex flex-col gap-2">
          <div aria-hidden="true" className="text-[13px] font-medium text-muted">
            {group.title}
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {items.map((item) => {
              const s = item.style;
              const selected = s.id === value;
              return (
                <button
                  key={s.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onChange(s.id)}
                  className={`flex min-w-0 flex-col gap-1.5 rounded-lg border p-3 text-left transition-colors ${
                    selected ? 'border-accent/70 bg-accent/10' : 'border-line bg-canvas hover:border-line-strong hover:bg-raised'
                  }`}
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className="font-medium text-fg">{s.name}</span>
                      {item.source === 'user' ? <Badge>Meu</Badge> : null}
                    </span>
                    {selected ? <Check size={16} strokeWidth={2.5} aria-hidden="true" className="mt-0.5 shrink-0 text-accent" /> : null}
                  </span>
                  {s.description ? <span className="line-clamp-2 text-[13px] leading-snug text-muted">{s.description}</span> : null}
                  <span className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 pt-0.5 text-xs text-subtle">
                    <span className="inline-flex items-center gap-1">
                      <FormatGlyph aspect={s.format.aspect} className="scale-75" />
                      {s.format.aspect}
                    </span>
                    <span aria-hidden="true">·</span>
                    <span className="tabular-nums">{durationLabel(s.format.duration.target_s)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{label(FAMILY_LABELS, s.family)}</span>
                    {s.structure && s.structure !== 'none' ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span>{label(STRUCTURE_LABELS, s.structure)}</span>
                      </>
                    ) : null}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      );
    })}
  </div>
);
