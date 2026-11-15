import { Lightbulb, TriangleAlert } from 'lucide-react';
import React from 'react';
import type { AnalyticsResults, Suggestion } from '../../apiAnalytics';
import { Badge, Empty } from '../../ui';
import { fmtCount } from './format';

/** Sugestões tiradas dos resultados (só aparecem com 8 ou mais Shorts na idade mínima). */
export const Suggestions: React.FC<{ items: Suggestion[] }> = ({ items }) => {
  if (!items.length) return null;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((s, i) => (
        <li
          key={i}
          className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm ${
            s.kind === 'Alerta' ? 'border-amber-300/35 bg-amber-300/10' : 'border-line bg-raised'
          }`}
        >
          {s.kind === 'Alerta' ? (
            <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          ) : (
            <Lightbulb size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-accent" />
          )}
          <span className="text-fg">{s.text}</span>
        </li>
      ))}
    </ul>
  );
};

/** "O que funcionou": mediana de views dos Shorts por variável, com o número de vídeos em cada grupo. */
export const WhatWorked: React.FC<{ results: AnalyticsResults }> = ({ results }) => {
  const { shorts_n, overall_median, variables, calibration, longs, min_age_h } = results;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        Só Shorts com {min_age_h} h ou mais contam (as views ainda sobem nos primeiros dias).{' '}
        {shorts_n ? (
          <>
            São <strong className="text-fg">{shorts_n}</strong> Shorts, com mediana de{' '}
            <strong className="text-fg">{fmtCount(overall_median)}</strong> views.
          </>
        ) : (
          'Ainda não há nenhum.'
        )}
      </p>
      {variables.length ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {variables.map((v) => {
            const top = Math.max(...v.groups.map((g) => g.median ?? 0), 1);
            return (
              <div key={v.variable} className="rounded-lg border border-line p-3">
                <div className="mb-2 text-sm font-medium text-fg">{v.label}</div>
                <ul className="flex flex-col gap-1.5">
                  {v.groups.map((g) => (
                    <li key={g.value} className="flex flex-col gap-1 text-[13px]">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-muted" title={g.value}>
                          {g.value}
                        </span>
                        <span className="shrink-0 tabular-nums text-fg">
                          {fmtCount(g.median)} <span className="text-subtle">(n={g.n})</span>
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-raised" aria-hidden="true">
                        <div className="h-1.5 rounded-full bg-accent/70" style={{ width: `${((g.median ?? 0) / top) * 100}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      ) : shorts_n ? (
        <Empty title="Ainda sem comparação">Nenhuma variável tem 2 grupos diferentes entre os Shorts que já contam.</Empty>
      ) : null}
      {calibration.rho !== null ? (
        <p className="text-[13px] text-muted">
          Nota da ideia × views: correlação {String(calibration.rho).replace('.', ',')} (n={calibration.n}).
        </p>
      ) : null}
      {longs.length ? (
        <div className="flex flex-col gap-1.5">
          <div className="text-sm font-medium text-fg">Vídeos longos</div>
          <ul className="flex flex-col gap-1 text-[13px] text-muted">
            {longs.map((l) => (
              <li key={l.youtube_id} className="flex flex-wrap items-center gap-2">
                <span className="truncate text-fg">{l.title || l.youtube_id}</span>
                <Badge>{fmtCount(l.views)} views</Badge>
                <span>com {l.days ?? 0} dias</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
};
