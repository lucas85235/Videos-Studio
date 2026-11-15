import { CircleAlert, ExternalLink, History, Package } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import { PLATFORM_LABELS } from '../../api';
import type { StockInfo, Yesterday } from '../../apiOps';
import { Badge, Section } from '../../ui';
import { dur, shortWhen } from './shared';

/** Estoque por canal: prontos (renderizados e não enviados) + agendados, contra o mínimo do canal. */
export const StockSection: React.FC<{ items: StockInfo[] }> = ({ items }) => (
  <Section title="Estoque" icon={Package} description="Vídeos prontos ou agendados à frente, por canal. O mínimo se configura em Canais.">
    {items.length === 0 ? (
      <p className="text-sm text-muted">
        Nenhum canal com estoque mínimo. Defina em{' '}
        <Link to="/canais" className="text-accent hover:underline">
          Canais
        </Link>
        .
      </p>
    ) : (
      <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
        {items.map((s) => (
          <li key={s.channel_id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
            <span className="min-w-[8rem] flex-1 text-sm font-medium text-fg">{s.channel_name}</span>
            <span className="text-[13px] text-muted">
              <span className="font-semibold tabular-nums text-fg">{s.total}</span> de {s.min_stock} {s.format_label}
              {s.scheduled ? ` (${s.ready} prontos, ${s.scheduled} agendados)` : ''}
            </span>
            {s.below ? (
              <Badge tone="warn" icon={CircleAlert}>
                Abaixo do mínimo
              </Badge>
            ) : (
              <Badge tone="good">Em dia</Badge>
            )}
          </li>
        ))}
      </ul>
    )}
  </Section>
);

/** Ontem: o que saiu e os jobs que falharam. */
export const YesterdaySection: React.FC<{ data: Yesterday }> = ({ data }) => (
  <Section
    title="Ontem"
    icon={History}
    description={data.jobs ? `${data.jobs} ${data.jobs === 1 ? 'tarefa' : 'tarefas'} no app, ${dur(data.seconds)} de trabalho.` : 'Nenhuma tarefa rodou ontem.'}
  >
    <div className="flex flex-col gap-1.5">
      <h3 className="text-sm font-semibold text-fg">Publicados</h3>
      {data.published.length === 0 ? (
        <p className="text-sm text-muted">Nada saiu ontem.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {data.published.map((p, i) => (
            <li key={`${p.project_id}-${i}`} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="w-24 shrink-0 text-[13px] tabular-nums text-subtle">{shortWhen(p.at)}</span>
              <Link to={`/projetos/${p.project_id}`} className="min-w-0 flex-1 truncate text-fg hover:underline">
                {p.title}
              </Link>
              <Badge>{PLATFORM_LABELS[p.platform] ?? p.platform}</Badge>
              {p.url ? (
                <a href={p.url} target="_blank" rel="noreferrer" aria-label={`Abrir post: ${p.title}`} className="text-muted hover:text-fg">
                  <ExternalLink size={14} aria-hidden="true" />
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
    <div className="flex flex-col gap-1.5">
      <h3 className="text-sm font-semibold text-fg">Falharam</h3>
      {data.failed.length === 0 ? (
        <p className="text-sm text-muted">Nenhuma tarefa falhou.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {data.failed.map((f) => (
            <li key={f.id} className="flex flex-col gap-0.5 rounded-lg border border-red-400/30 bg-red-400/5 px-3 py-2 text-sm">
              <span className="text-fg">
                {f.label}
                {f.project_id ? (
                  <>
                    {' · '}
                    <Link to={`/projetos/${f.project_id}`} className="hover:underline">
                      {f.project_title ?? f.project_id}
                    </Link>
                  </>
                ) : null}
              </span>
              {f.error ? <span className="break-words text-[13px] text-muted">{f.error}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  </Section>
);
