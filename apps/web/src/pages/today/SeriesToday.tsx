import { CircleAlert, Repeat } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import type { SeriesToday } from '../../apiSeries';
import { Badge, Section } from '../../ui';
import { shortWhen } from './shared';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Séries ativas: estoque de episódios da série (em produção, esperando revisão, prontos) e a última rodada do piloto. */
export const SeriesSection: React.FC<{ items: SeriesToday[] }> = ({ items }) => (
  <Section
    title="Séries"
    icon={Repeat}
    description="Episódios à frente de cada série. O piloto cria os que faltam de manhã e para no roteiro esperando você."
    actions={
      <Link to="/series" className="text-[13px] font-medium text-accent hover:underline">
        Abrir Séries
      </Link>
    }
  >
    <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
      {items.map((s) => (
        <li key={s.id} className="flex flex-col gap-1 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="min-w-[8rem] flex-1 text-sm font-medium text-fg">
              {s.name} <span className="font-normal text-subtle">· {s.channel_name}</span>
            </span>
            <span className="text-[13px] text-muted">
              <span className="font-semibold tabular-nums text-fg">{s.open}</span> de {s.stock_target} no estoque
              {s.review ? ` · ${s.review} esperando revisão` : ''}
              {s.ready ? ` · ${plural(s.ready, 'pronto', 'prontos')}` : ''}
              {s.coverage_days ? ` · cobre ${plural(s.coverage_days, 'dia', 'dias')}` : ''}
            </span>
            {s.failed ? (
              <Badge tone="bad" icon={CircleAlert}>
                {plural(s.failed, 'falhou', 'falharam')}
              </Badge>
            ) : null}
            {s.stopped ? (
              <Badge tone="warn" icon={CircleAlert}>
                {plural(s.stopped, 'parado', 'parados')}
              </Badge>
            ) : null}
            {s.below ? (
              <Badge tone="warn" icon={CircleAlert}>
                Abaixo do estoque
              </Badge>
            ) : (
              <Badge tone="good">Em dia</Badge>
            )}
            {s.autopilot ? null : <Badge>Piloto desligado</Badge>}
          </div>
          {s.topics_left === 0 ? <p className="text-[13px] text-amber-200">A fila de temas acabou: ponha temas novos na série.</p> : null}
          {s.last_run_at ? (
            <p className="text-[13px] text-subtle">
              Última rodada {shortWhen(s.last_run_at)}: {s.last_run_message}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  </Section>
);
