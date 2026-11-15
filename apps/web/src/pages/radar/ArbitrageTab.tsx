/* Abas "Arbitragem EUA → Brasil" e "Método" do radar. */
import { ArrowDown, ArrowUp } from 'lucide-react';
import React, { useState } from 'react';
import type { RadarArbitrage, RadarPanel } from '../../apiRadar';
import { Badge, Empty, Section } from '../../ui';
import { fmtNum } from './format';

type Col = { k: string; label: string; v: (a: RadarArbitrage) => number | string; f: (a: RadarArbitrage) => React.ReactNode };

const TONE = { 'Aberto em PT': 'good', 'Concorrido em PT': 'bad', Morno: 'neutral' } as const;

const COLS: Col[] = [
  { k: 'nome', label: 'Nicho', v: (a) => a.nome, f: (a) => <span className="font-semibold text-fg">{a.nome}</span> },
  { k: 'leitura', label: 'Leitura', v: (a) => a.leitura, f: (a) => <Badge tone={TONE[a.leitura]}>{a.leitura}</Badge> },
  { k: 'score', label: 'Espaço em PT', v: (a) => a.score, f: (a) => a.score.toLocaleString('pt-BR') },
  { k: 'en_med', label: 'EUA: mediana', v: (a) => a.en.med, f: (a) => fmtNum(a.en.med) },
  { k: 'en_hit', label: 'EUA: canais novos com hit', v: (a) => a.en.new_hit, f: (a) => a.en.new_hit },
  { k: 'pt_n10k', label: 'PT: vídeos 10 mil+', v: (a) => a.pt.n10k, f: (a) => `${a.pt.n10k}/${a.pt.n}` },
  { k: 'pt_med', label: 'PT: mediana', v: (a) => a.pt.med, f: (a) => fmtNum(a.pt.med) },
  { k: 'pt_best', label: 'PT: melhor vídeo', v: (a) => a.pt.best, f: (a) => fmtNum(a.pt.best) },
];

export const ArbitrageTab: React.FC<{ data: RadarPanel; onNiche: (name: string) => void }> = ({ data, onNiche }) => {
  const [sort, setSort] = useState<{ k: string; d: 1 | -1 }>({ k: 'score', d: -1 });
  const col = COLS.find((c) => c.k === sort.k) ?? COLS[2];
  const rows = [...data.arb].sort((a, b) => {
    const va = col.v(a);
    const vb = col.v(b);
    return (typeof va === 'string' && typeof vb === 'string' ? va.localeCompare(vb) : Number(va) - Number(vb)) * sort.d;
  });
  return (
    <Section
      title="Arbitragem EUA → Brasil"
      description="O que funciona nos EUA e ainda tem pouca gente fazendo em português. Demanda: mediana de views do nicho nos EUA no mês. Oferta em PT: vídeos em português do mês com 10 mil+ views. Aberto em PT = demanda de 100 mil+ e até 5 vídeos de 10 mil+ em português; Concorrido em PT = 15 ou mais."
    >
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse text-sm">
            <thead>
              <tr>
                {COLS.map((c) => (
                  <th key={c.k} className="whitespace-nowrap border-b border-line px-2.5 py-2 text-left text-xs font-medium text-muted">
                    <button
                      type="button"
                      className={`inline-flex items-center gap-1 ${sort.k === c.k ? 'text-accent' : 'hover:text-fg'}`}
                      onClick={() => setSort((s) => ({ k: c.k, d: s.k === c.k ? (s.d === 1 ? -1 : 1) : -1 }))}
                    >
                      {c.label}
                      {sort.k === c.k ? sort.d < 0 ? <ArrowDown size={12} aria-hidden="true" /> : <ArrowUp size={12} aria-hidden="true" /> : null}
                    </button>
                  </th>
                ))}
                <th className="border-b border-line" />
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="hover:bg-raised/50">
                  {COLS.map((c) => (
                    <td key={c.k} className={`border-b border-line px-2.5 py-2 ${c.k === 'nome' || c.k === 'leitura' ? '' : 'font-mono text-xs'}`}>
                      {c.f(a)}
                    </td>
                  ))}
                  <td className="border-b border-line px-2.5 py-2">
                    <button type="button" className="whitespace-nowrap text-xs text-sky-300 hover:underline" onClick={() => onNiche(`${a.nome} · BR`)}>
                      Ver em PT →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="Sem arbitragem nesta coleta">Precisa da coleta nos dois mercados (EUA e Brasil).</Empty>
      )}
    </Section>
  );
};

export const MethodTab: React.FC<{ data: RadarPanel }> = ({ data }) => (
  <Section title="Como os dados foram coletados" description={data.meta.description}>
    <ol className="ml-5 flex max-w-prose list-decimal flex-col gap-1.5 text-sm text-muted">
      {data.meta.method.map((t) => (
        <li key={t}>{t}</li>
      ))}
    </ol>
    <h3 className="mt-2 text-sm font-semibold text-fg">Limites</h3>
    <ul className="ml-5 flex max-w-prose list-disc flex-col gap-1.5 text-sm text-muted">
      {data.meta.limits.map((t) => (
        <li key={t}>{t}</li>
      ))}
    </ul>
    {data.meta.skipped.length ? (
      <>
        <h3 className="mt-2 text-sm font-semibold text-fg">Ficou de fora desta coleta</h3>
        <p className="text-sm text-muted">{data.meta.skipped.join(', ')}</p>
      </>
    ) : null}
  </Section>
);
