/* Aba "Visão geral" do radar: números, mapa de oportunidade, por onde começar, ranking, RPM e monetização. */
import { ArrowDown, ArrowUp } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import type { Channel } from '../../api';
import type { RadarMarket, RadarPanel, RadarPanelNiche } from '../../apiRadar';
import { Badge, Card, Section, SegmentedControl } from '../../ui';
import { ageText, decimal, fmtNum, VERDICT_TONE } from './format';
import { IdeaButton, Thumb } from './shared';

const Kpis: React.FC<{ data: RadarPanel }> = ({ data }) => {
  const newHits = data.channels.filter((c) => c.ageM != null && c.ageM <= 18 && c.bestViews >= 1e5).length;
  const items: [number, string][] = [
    [data.meta.videos, 'vídeos do último mês'],
    [data.meta.channels, 'canais lidos'],
    [newHits, 'canais com até 18 meses e vídeo de 100 mil+ no mês'],
    [data.niches.length, 'nichos comparados'],
    [data.meta.searches, 'buscas feitas'],
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {items.map(([n, label]) => (
        <Card key={label} className="flex flex-col gap-1">
          <span className="font-mono text-2xl font-semibold tabular-nums text-fg">{n.toLocaleString('pt-BR')}</span>
          <span className="text-xs text-muted">{label}</span>
        </Card>
      ))}
    </div>
  );
};

const median = (a: number[]) => [...a].sort((p, q) => p - q)[Math.floor(a.length / 2)] ?? 1;
const shareColor = (s: number) => {
  const t = Math.max(0, Math.min(1, (s - 0.05) / 0.47));
  const a = [70, 82, 104];
  const z = [54, 211, 153];
  return `rgb(${a.map((c, i) => Math.round(c + (z[i] - c) * t)).join(',')})`;
};

/** Mapa log × log: x = mediana de inscritos, y = mediana de views; raio pelos canais novos com hit, cor pela % de
 * vídeos de canais novos. Em cima e à esquerda: muita demanda e concorrentes menores. */
const OpportunityMap: React.FC<{ niches: RadarPanelNiche[]; onNiche: (name: string) => void }> = ({ niches, onNiche }) => {
  if (!niches.length) return <p className="text-sm text-muted">Nenhum nicho com vídeo aceito neste mercado.</p>;
  const W = 640;
  const H = 360;
  const P = { l: 66, r: 18, t: 16, b: 42 };
  const lx = Math.log10;
  const xs = niches.map((n) => n.subs);
  const ys = niches.map((n) => n.med);
  const x0 = lx(Math.min(...xs)) - 0.15;
  const x1 = lx(Math.max(...xs)) + 0.15;
  const y0 = lx(Math.min(...ys)) - 0.15;
  const y1 = lx(Math.max(...ys)) + 0.15;
  const X = (v: number) => P.l + ((lx(v) - x0) / (x1 - x0)) * (W - P.l - P.r);
  const Y = (v: number) => H - P.b - ((lx(v) - y0) / (y1 - y0)) * (H - P.t - P.b);
  const mx = X(median(xs));
  const my = Y(median(ys));
  const ticks = (a: number, b: number) => {
    const out: number[] = [];
    for (let e = Math.floor(a); e <= Math.ceil(b); e++)
      for (const m of [1, 3]) {
        const v = m * 10 ** e;
        if (lx(v) >= a && lx(v) <= b) out.push(v);
      }
    return out;
  };
  const pts = niches.map((n) => ({ n, x: X(n.subs), y: Y(n.med), r: 6 + n.newCh * 1.4 })).sort((a, b) => b.r - a.r);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label="Mapa de nichos: concorrência versus demanda">
      <rect x={P.l} y={P.t} width={Math.max(0, mx - P.l)} height={Math.max(0, my - P.t)} fill="#36d399" opacity={0.05} />
      {ticks(x0, x1).map((v) => (
        <g key={`x${v}`}>
          <line x1={X(v)} x2={X(v)} y1={P.t} y2={H - P.b} className="stroke-zinc-800" />
          <text x={X(v)} y={H - P.b + 14} className="fill-zinc-500 font-mono" fontSize={10} textAnchor="middle">
            {fmtNum(v)}
          </text>
        </g>
      ))}
      {ticks(y0, y1).map((v) => (
        <g key={`y${v}`}>
          <line x1={P.l} x2={W - P.r} y1={Y(v)} y2={Y(v)} className="stroke-zinc-800" />
          <text x={P.l - 6} y={Y(v) + 3} className="fill-zinc-500 font-mono" fontSize={10} textAnchor="end">
            {fmtNum(v)}
          </text>
        </g>
      ))}
      <line x1={mx} x2={mx} y1={P.t} y2={H - P.b} className="stroke-zinc-600" strokeDasharray="3 4" />
      <line x1={P.l} x2={W - P.r} y1={my} y2={my} className="stroke-zinc-600" strokeDasharray="3 4" />
      <text x={P.l + 6} y={P.t + 13} fill="#36d399" fontSize={10.5} fontWeight={600}>
        Muita demanda, concorrentes menores
      </text>
      <text x={(P.l + W - P.r) / 2} y={H - 6} className="fill-zinc-400" fontSize={10.5} textAnchor="middle">
        Concorrência: mediana de inscritos dos canais que aparecem →
      </text>
      <text transform={`translate(13 ${(P.t + H - P.b) / 2}) rotate(-90)`} className="fill-zinc-400" fontSize={10.5} textAnchor="middle">
        Demanda: mediana de views no mês →
      </text>
      {pts.map((p) => (
        <g key={p.n.name} className="cursor-pointer" onClick={() => onNiche(p.n.name)}>
          <circle cx={p.x} cy={p.y} r={p.r} fill={shareColor(p.n.newShare)} fillOpacity={0.85} stroke="#0d0f13" strokeWidth={1.5}>
            <title>{`${p.n.name} · mediana ${fmtNum(p.n.med)} views · ${Math.round(p.n.newShare * 100)}% dos vídeos de canais novos · ${p.n.newCh} canais novos com hit (sem alerta)`}</title>
          </circle>
          <text x={p.x + p.r + 4} y={p.y + 4} className="pointer-events-none fill-zinc-200" fontSize={10.5}>
            {p.n.name.replace(/ · (EUA|BR)$/, '')}
          </text>
        </g>
      ))}
    </svg>
  );
};

type SortKey = 'name' | 'med' | 'h100' | 'newShare' | 'newCh' | 'bestNew' | 'subs' | 'len' | 'rpm';
const COLS: { k: SortKey; label: string }[] = [
  { k: 'name', label: 'Nicho' },
  { k: 'med', label: 'Mediana de views' },
  { k: 'h100', label: 'Vídeos com hit' },
  { k: 'newShare', label: '% de canais novos' },
  { k: 'newCh', label: 'Canais novos com hit' },
  { k: 'bestNew', label: 'Melhor vídeo de canal novo' },
  { k: 'subs', label: 'Inscritos (mediana)' },
  { k: 'len', label: 'Duração' },
  { k: 'rpm', label: 'RPM da categoria' },
];

const NicheTable: React.FC<{ niches: RadarPanelNiche[]; onNiche: (name: string) => void }> = ({ niches, onNiche }) => {
  const [sort, setSort] = useState<{ k: SortKey; d: 1 | -1 }>({ k: 'newCh', d: -1 });
  const rows = [...niches].sort((a, b) => {
    const va = a[sort.k];
    const vb = b[sort.k];
    const c = typeof va === 'string' && typeof vb === 'string' ? va.localeCompare(vb) : Number(va) - Number(vb);
    return c * sort.d || b.med - a.med;
  });
  const maxMed = Math.max(...niches.map((n) => n.med), 1);
  const maxShare = Math.max(...niches.map((n) => n.newShare), 0.01);
  return (
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
          {rows.map((n) => (
            <tr key={n.name} className="hover:bg-raised/50">
              <td className="border-b border-line px-2.5 py-2">
                <div className="font-semibold text-fg">{n.name}</div>
                <div className="text-xs text-subtle">{n.q.join(' · ')}</div>
              </td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">
                {fmtNum(n.med)}
                <div className="mt-1 h-1.5 max-w-[120px] overflow-hidden rounded bg-line">
                  <i className="block h-full rounded bg-accent" style={{ width: `${(n.med / maxMed) * 100}%` }} />
                </div>
              </td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">
                {n.h100}/{n.n}
              </td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">
                {Math.round(n.newShare * 100)}%
                <div className="mt-1 h-1.5 max-w-[120px] overflow-hidden rounded bg-line">
                  <i className="block h-full rounded bg-emerald-400" style={{ width: `${(n.newShare / maxShare) * 100}%` }} />
                </div>
              </td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">{n.newCh}</td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">{fmtNum(n.bestNew)}</td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">{fmtNum(n.subs)}</td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">{decimal(n.len)} min</td>
              <td className="border-b border-line px-2.5 py-2 font-mono text-xs">
                US$ {decimal(n.rpm, 2)}
                {n.small ? '*' : ''}
                <div className="font-sans text-subtle">{n.cat}</div>
              </td>
              <td className="border-b border-line px-2.5 py-2">
                <button type="button" className="whitespace-nowrap text-xs text-sky-300 hover:underline" onClick={() => onNiche(n.name)}>
                  Ver canais →
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export const Overview: React.FC<{
  data: RadarPanel;
  channels: Channel[];
  onNiche: (name: string) => void;
  onChannel: (id: string) => void;
}> = ({ data, channels, onNiche, onChannel }) => {
  const [market, setMarket] = useState<RadarMarket>(data.niches.some((n) => n.mk === 'us') ? 'us' : 'br');
  const niches = useMemo(() => data.niches.filter((n) => n.mk === market), [data.niches, market]);
  const byId = useMemo(() => new Map(data.channels.map((c) => [c.id, c])), [data.channels]);
  const recs = data.recs.filter((r) => r.mk === market);
  const hit = market === 'us' ? data.meta.hit_us : data.meta.hit_br;
  const maxRpm = Math.max(...data.markets.map((m) => m.rpm), 1);
  return (
    <div className="flex flex-col gap-4">
      <SegmentedControl
        label="Mercado"
        value={market}
        onChange={setMarket}
        options={[
          { id: 'us', label: 'EUA · inglês' },
          { id: 'br', label: 'Brasil · português' },
        ]}
      />
      <Kpis data={data} />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,1fr)]">
        <Section
          title="Mapa de oportunidade"
          description="Cada bolha é um nicho. Em cima e à esquerda: muita view e concorrentes menores. Clique numa bolha para ver os canais."
        >
          <OpportunityMap niches={niches} onNiche={onNiche} />
          <p className="text-xs text-muted">
            Tamanho: canais com até 12 meses e um vídeo acima do hit, sem alerta. Cor: % dos vídeos vindos de canais com até 12 meses
            (mais verde, mais aberto).
          </p>
        </Section>
        <Section title="Por onde começar" description="Formatos que se repetem em canais novos, não só um viral isolado.">
          {recs.length ? (
            <ul className="flex flex-col divide-y divide-line">
              {recs.map((r) => {
                const c = byId.get(r.id);
                if (!c) return null;
                const top = [...(c.latest ?? [])].filter((x) => x.v).sort((a, b) => (b.v ?? 0) - (a.v ?? 0))[0];
                return (
                  <li key={r.id} className="grid grid-cols-[108px_minmax(0,1fr)] gap-3 py-2.5">
                    <button type="button" onClick={() => onChannel(c.id)} aria-label={`Ver o canal ${c.name}`}>
                      <Thumb id={top ? top.id : c.best} alt="" />
                    </button>
                    <div className="flex min-w-0 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="font-mono text-accent">{r.n}</span>
                        {c.verdict ? <Badge tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Badge> : null}
                      </div>
                      <button type="button" className="text-left text-sm font-semibold text-fg hover:text-accent" onClick={() => onChannel(c.id)}>
                        {r.t}
                      </button>
                      <p className="text-xs text-muted">
                        <b className="text-fg">{c.name}</b>: criado há {ageText(c.ageM)}, {c.videos} vídeos, {fmtNum(c.views)} de views.{' '}
                        {c.hits}/{c.settled} vídeos recentes acima de {fmtNum(hit)}.
                      </p>
                      <IdeaButton runId={data.meta.run_id} channels={channels} channelYoutubeId={c.id} />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-muted">Nenhum canal novo com formato que se repete nesta coleta.</p>
          )}
          <div className="border-t border-line pt-3">
            <h3 className="mb-2 text-xs font-semibold text-red-300">Evitar no primeiro canal</h3>
            <div className="flex flex-wrap gap-1.5">
              {data.avoid.map(([what, why]) => (
                <Badge key={what} tone="bad" title={why}>
                  {what}
                </Badge>
              ))}
            </div>
          </div>
        </Section>
      </div>
      <Section
        title="Ranking de nichos"
        description={`Vídeos do último mês em ${market === 'us' ? 'inglês, buscados como um espectador dos EUA' : 'português, buscados como um espectador do Brasil'}. Hit = ${fmtNum(hit)}+ views. Clique no título da coluna para ordenar.`}
      >
        <NicheTable niches={niches} onNiche={onNiche} />
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section
          title="RPM por país da audiência"
          description="US$ por mil views, amostra de canais entre out/2025 e out/2026. Por isso o canal é em inglês."
        >
          <div className="flex flex-col gap-2">
            {data.markets.map((m) => (
              <div key={m.country} className="grid grid-cols-[110px_minmax(0,1fr)_64px] items-center gap-2.5 text-xs">
                <span>{m.country}</span>
                <div className="h-2 overflow-hidden rounded bg-line">
                  <i className="block h-full rounded bg-sky-400" style={{ width: `${(m.rpm / maxRpm) * 100}%` }} />
                </div>
                <span className="text-right font-mono">US$ {decimal(m.rpm, 2)}</span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted">
            RPM é o que o criador recebe por mil views, já sem a parte do YouTube.{' '}
            <a className="text-sky-300 hover:underline" href={data.rpm_source} target="_blank" rel="noreferrer">
              O que é RPM
            </a>{' '}
            ·{' '}
            <a className="text-sky-300 hover:underline" href={data.markets_source} target="_blank" rel="noreferrer">
              Fonte dos números
            </a>
          </p>
        </Section>
        <Section title="Para monetizar" description="O que o YouTube exige e onde canais dark costumam errar.">
          <ul className="flex flex-col divide-y divide-line text-sm">
            {data.facts.map((f) => (
              <li key={f.label} className="py-2 text-muted">
                <b className="text-fg">{f.label}:</b> {f.text}
                {f.url ? (
                  <>
                    {' '}
                    <a className="text-xs text-sky-300 hover:underline" href={f.url} target="_blank" rel="noreferrer">
                      fonte
                    </a>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  );
};
