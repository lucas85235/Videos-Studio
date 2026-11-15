// Sugestões de corte (job `shorts_suggest`): cada uma pode ser carregada no formulário.
import { Sparkles, TriangleAlert } from 'lucide-react';
import React from 'react';
import type { Job } from '../../../../api';
import type { ShortSuggestion, ShortsOfProject } from '../../../../apiShorts';
import { Badge, Button, formatRelative } from '../../../../ui';
import { JobProgress } from '../shared';
import { fmtSeconds } from './model';

export const Suggestions: React.FC<{
  data: ShortsOfProject;
  job: Job | undefined;
  busy: boolean;
  onSuggest: () => void;
  onUse: (s: ShortSuggestion) => void;
}> = ({ data, job, busy, onSuggest, onUse }) => {
  const sug = data.suggestions;
  const items = sug && (!data.lang || sug.lang === data.lang) ? sug.items : [];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium text-fg">Sugestões de corte</div>
        <Button size="sm" icon={Sparkles} busy={busy} disabled={Boolean(job)} onClick={onSuggest}>
          {items.length ? 'Sugerir de novo' : 'Sugerir cortes'}
        </Button>
      </div>
      {job ? <JobProgress job={job} title="Sugestão de Shorts" /> : null}
      {items.length ? (
        <ul className="flex flex-col gap-1.5">
          {items.map((s) => (
            <li key={s.segment_ids.join('-')} className="flex flex-wrap items-start gap-3 rounded-lg border border-line px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-fg">{s.title}</div>
                {s.hook ? <div className="text-[13px] text-muted">Gancho: {s.hook}</div> : null}
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                  <span className="font-mono">{fmtSeconds(s.duration_s)}</span>
                  <span>· {s.segment_ids.length} {s.segment_ids.length === 1 ? 'segmento' : 'segmentos'}</span>
                  {s.tipo !== 'normal' ? <Badge tone="info">{s.tipo}</Badge> : null}
                  {s.warnings.length ? (
                    <Badge tone="warn" icon={TriangleAlert} title={s.warnings.join('\n')}>
                      {s.warnings.length} {s.warnings.length === 1 ? 'aviso' : 'avisos'}
                    </Badge>
                  ) : null}
                </div>
                {s.reason ? <div className="mt-0.5 text-[12px] text-muted">{s.reason}</div> : null}
              </div>
              <Button size="sm" variant="ghost" onClick={() => onUse(s)}>
                Usar
              </Button>
            </li>
          ))}
        </ul>
      ) : !job ? (
        <p className="text-[13px] text-muted">
          Peça sugestões para ver de 3 a 5 trechos de 15 a 30 s, ou marque os segmentos à mão.
        </p>
      ) : null}
      {sug?.created_at && items.length ? (
        <div className="text-[12px] text-muted">Sugeridas {formatRelative(sug.created_at)}</div>
      ) : null}
    </div>
  );
};
