/* Aba "Canais" do radar: filtros do painel do canais, cartões com as views recentes e a gaveta do canal. */
import { ExternalLink, TriangleAlert, X } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import type { Channel } from '../../api';
import type { RadarPanel, RadarPanelChannel } from '../../apiRadar';
import { Badge, Button, Card, Checkbox, Empty, IconButton, Input, Select } from '../../ui';
import { agoText, ageText, fmtNum, VERDICT_RANK, VERDICT_TONE, watchUrl } from './format';
import { IdeaButton, RecentBars, Thumb } from './shared';

export type ChannelFilters = {
  q: string;
  niche: string;
  age: number;
  sort: 'rec' | 'vpm' | 'med' | 'rate' | 'views' | 'subs' | 'young';
  withHit: boolean;
  hideFlags: boolean;
  onlyVerdict: boolean;
};

export const DEFAULT_CHANNEL_FILTERS: ChannelFilters = {
  q: '',
  niche: '',
  age: 18,
  sort: 'rec',
  withHit: true,
  hideFlags: true,
  onlyVerdict: false,
};

export const AGE_OPTIONS = [
  { v: 3, label: 'Canal com até 3 meses' },
  { v: 6, label: 'Até 6 meses' },
  { v: 12, label: 'Até 12 meses' },
  { v: 18, label: 'Até 18 meses' },
  { v: 999, label: 'Qualquer idade' },
];
const PAGE = 48;

const hitOf = (data: RadarPanel, c: RadarPanelChannel) =>
  c.niches.some((n) => n.endsWith(' · EUA')) || !c.niches.length ? data.meta.hit_us : data.meta.hit_br;

const ChannelCard: React.FC<{ c: RadarPanelChannel; data: RadarPanel; onOpen: () => void }> = ({ c, data, onOpen }) => {
  const best = data.videos.find((v) => v.id === c.best);
  const hit = hitOf(data, c);
  return (
    <Card interactive className="flex flex-col gap-2.5 !p-0">
      <button type="button" className="relative block text-left" onClick={onOpen} aria-label={`Ver o canal ${c.name}`}>
        <Thumb id={c.best} alt={best?.t ?? ''} className="rounded-b-none rounded-t-xl" />
        {c.verdict ? (
          <span className="absolute left-2 top-2">
            <Badge tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Badge>
          </span>
        ) : null}
        <span className="absolute bottom-2 right-2 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[11px] text-white">
          {fmtNum(best?.v)} · {best?.l ?? '?'} min
        </span>
      </button>
      <div className="flex flex-1 flex-col gap-2 px-3 pb-3">
        <div>
          <button type="button" className="text-left text-sm font-semibold text-fg hover:text-accent" onClick={onOpen}>
            {c.name}
          </button>
          <p className="text-xs text-muted">
            {c.country || 'País não informado'} · criado há {ageText(c.ageM)} · {c.niches.join(', ')}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-1.5 text-center">
          {[
            [fmtNum(c.vpm), 'views/mês'],
            [fmtNum(c.subs), 'inscritos'],
            [fmtNum(c.videos), 'vídeos'],
          ].map(([v, l]) => (
            <div key={l} className="rounded-md bg-raised px-1.5 py-1" title={l === 'views/mês' ? 'views totais ÷ meses desde a criação do canal' : undefined}>
              <b className="block font-mono text-[13px]">{v}</b>
              <span className="text-[11px] text-subtle">{l}</span>
            </div>
          ))}
        </div>
        {c.latest ? (
          <div className="flex items-end gap-2.5">
            <RecentBars channel={c} hit={hit} />
            <p className="text-[11px] leading-tight text-muted">
              <b className="font-mono text-fg">
                {c.hits}/{c.settled}
              </b>{' '}
              com {fmtNum(hit)}+
              <br />
              mediana <b className="font-mono text-fg">{fmtNum(c.med)}</b>
              <br />
              {c.up30} vídeos em 30 dias
            </p>
          </div>
        ) : null}
        {c.flag ? (
          <p className="flex items-start gap-1.5 rounded-md border border-red-400/35 bg-red-400/10 px-2 py-1 text-xs text-red-300">
            <TriangleAlert size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
            {c.flag}
          </p>
        ) : null}
      </div>
    </Card>
  );
};

/** Gaveta com os detalhes do canal: veredito, números, vídeos recentes e "Virar ideia". */
export const ChannelDrawer: React.FC<{ data: RadarPanel; id: string; channels: Channel[]; onClose: () => void }> = ({
  data,
  id,
  channels,
  onClose,
}) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const c = data.channels.find((x) => x.id === id);
  if (!c) return null;
  const hit = hitOf(data, c);
  const vids = c.latest ?? c.sample.map((v) => data.videos.find((x) => x.id === v)).filter((x) => x != null);
  const top = [...vids].filter((x) => x.v).sort((a, b) => (b.v ?? 0) - (a.v ?? 0))[0];
  const joined = c.joined ? c.joined.split('-').reverse().join('/') : '—';
  const stats: [string, string][] = [
    [fmtNum(c.subs), 'inscritos'],
    [fmtNum(c.views), 'views totais'],
    [fmtNum(c.videos), 'vídeos'],
    [fmtNum(c.vpm), 'views por mês de vida'],
    [c.latest ? fmtNum(c.med) : '—', 'mediana recente (7+ dias)'],
    [c.latest ? `${c.hits}/${c.settled}` : '—', `recentes com ${fmtNum(hit)}+`],
    [c.latest ? String(c.up30) : '—', 'vídeos em 30 dias'],
    [c.typLen ? `${Math.round(c.typLen)} min` : '—', 'duração típica'],
  ];
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Fechar" className="absolute inset-0 bg-black/55" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label={c.name} className="relative h-full w-full max-w-[600px] overflow-y-auto border-l border-line-strong bg-card">
        <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-line bg-card px-4 py-3">
          <div className="min-w-0 flex-1">
            <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-lg font-semibold text-fg hover:text-accent">
              {c.name}
              <ExternalLink size={14} aria-hidden="true" />
            </a>
            <p className="text-xs text-muted">
              {c.country || 'País não informado'} · criado em {joined} ({ageText(c.ageM)}) · {c.niches.join(', ')}
            </p>
          </div>
          <IconButton label="Fechar" icon={X} onClick={onClose} />
        </header>
        <div className="flex flex-col gap-4 px-4 py-4">
          {c.verdict ? (
            <div className="rounded-lg border border-line-strong bg-raised px-3 py-2.5">
              <Badge tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Badge>
              <p className="mt-1.5 text-sm font-semibold text-fg">{c.format}</p>
              <p className="text-sm text-muted">{c.risk}</p>
            </div>
          ) : null}
          {c.flag ? <p className="rounded-md border border-red-400/35 bg-red-400/10 px-2 py-1.5 text-xs text-red-300">{c.flag}</p> : null}
          <IdeaButton runId={data.meta.run_id} channels={channels} channelYoutubeId={c.id} />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {stats.map(([v, l]) => (
              <div key={l} className="rounded-lg bg-raised px-2 py-1.5">
                <b className="block font-mono text-sm">{v}</b>
                <span className="text-[11px] text-subtle">{l}</span>
              </div>
            ))}
          </div>
          {top ? (
            <a href={watchUrl(top.id)} target="_blank" rel="noreferrer" className="flex flex-col gap-1.5">
              <Thumb id={top.id} size="hq" className="rounded-lg" />
              <span className="text-xs text-muted">
                Maior alcance: <b className="text-fg">{top.t}</b> · {fmtNum(top.v)} views
              </span>
            </a>
          ) : null}
          {c.latest ? (
            <div>
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Views dos últimos vídeos (antigo → novo)</h4>
              <RecentBars channel={c} hit={hit} width={540} height={70} />
            </div>
          ) : null}
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">{c.latest ? 'Últimos vídeos' : 'Vídeos deste canal na amostra'}</h4>
            <ul className="flex flex-col divide-y divide-line">
              {vids.map((x) => (
                <li key={x.id} className="grid grid-cols-[96px_minmax(0,1fr)_auto] items-center gap-2.5 py-1.5">
                  <Thumb id={x.id} />
                  <a href={watchUrl(x.id)} target="_blank" rel="noreferrer" className="text-xs leading-snug hover:text-accent">
                    {x.t}
                  </a>
                  <span className="whitespace-nowrap text-right font-mono text-[11px] text-muted">
                    <b className={`block ${(x.v ?? 0) >= hit ? 'text-emerald-300' : 'text-fg'}`}>{fmtNum(x.v)}</b>
                    {agoText(x.a)} · {x.l ?? '?'} min
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </aside>
    </div>
  );
};

export const ChannelsTab: React.FC<{
  data: RadarPanel;
  filters: ChannelFilters;
  setFilters: (f: ChannelFilters) => void;
  onOpen: (id: string) => void;
}> = ({ data, filters: f, setFilters, onOpen }) => {
  const [shown, setShown] = useState(PAGE);
  const set = (patch: Partial<ChannelFilters>) => {
    setShown(PAGE);
    setFilters({ ...f, ...patch });
  };
  const titles = useMemo(() => new Map(data.videos.map((v) => [v.id, v.t])), [data.videos]);
  const nicheNames = useMemo(() => data.niches.map((n) => n.name).sort((a, b) => a.localeCompare(b)), [data.niches]);
  const list = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    const L = data.channels.filter(
      (c) =>
        (c.ageM ?? 999) <= f.age &&
        (!f.niche || c.niches.includes(f.niche)) &&
        (!f.withHit || c.bestViews >= 1e5) &&
        (!f.hideFlags || !c.flag) &&
        (!f.onlyVerdict || c.verdict) &&
        (!q ||
          [c.name, ...c.sample.map((id) => titles.get(id) ?? ''), ...(c.latest ?? []).map((x) => x.t)].join(' ').toLowerCase().includes(q)),
    );
    const key: Record<Exclude<ChannelFilters['sort'], 'rec'>, (c: RadarPanelChannel) => number> = {
      vpm: (c) => c.vpm ?? 0,
      med: (c) => c.med ?? -1,
      rate: (c) => (c.settled ? (c.hits ?? 0) / c.settled : -1),
      views: (c) => c.views ?? 0,
      subs: (c) => c.subs ?? 0,
      young: (c) => -(c.ageM ?? 999),
    };
    const rank = (c: RadarPanelChannel) => (c.verdict ? VERDICT_RANK[c.verdict] : 3);
    if (f.sort === 'rec') return L.sort((x, y) => rank(x) - rank(y) || (y.vpm ?? 0) - (x.vpm ?? 0));
    const k = key[f.sort];
    return L.sort((x, y) => k(y) - k(x));
  }, [data.channels, f, titles]);

  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-wrap items-center gap-2">
        <Input type="search" aria-label="Buscar" placeholder="Buscar canal ou título…" value={f.q} onChange={(e) => set({ q: e.target.value })} className="min-w-[200px] flex-1" />
        <Select aria-label="Nicho" value={f.niche} onChange={(e) => set({ niche: e.target.value })}>
          <option value="">Todos os nichos</option>
          {nicheNames.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </Select>
        <Select aria-label="Idade do canal" value={f.age} onChange={(e) => set({ age: Number(e.target.value) })}>
          {AGE_OPTIONS.map((o) => (
            <option key={o.v} value={o.v}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select aria-label="Ordenar" value={f.sort} onChange={(e) => set({ sort: e.target.value as ChannelFilters['sort'] })}>
          <option value="rec">Ordenar: recomendados primeiro</option>
          <option value="vpm">Views por mês de vida</option>
          <option value="med">Mediana dos vídeos recentes</option>
          <option value="rate">% de vídeos com hit</option>
          <option value="views">Views totais</option>
          <option value="subs">Inscritos</option>
          <option value="young">Mais novos primeiro</option>
        </Select>
        <Checkbox label="Com vídeo de 100 mil+" checked={f.withHit} onChange={(v) => set({ withHit: v })} />
        <Checkbox label="Esconder alertas" checked={f.hideFlags} onChange={(v) => set({ hideFlags: v })} />
        <Checkbox label="Só com veredito" checked={f.onlyVerdict} onChange={(v) => set({ onlyVerdict: v })} />
        <span className="ml-auto font-mono text-xs text-muted">
          {list.length} {list.length === 1 ? 'canal' : 'canais'}
        </span>
      </Card>
      <p className="text-xs text-muted">
        Barrinhas: views dos últimos vídeos, do mais antigo ao mais novo. Verde passou do hit; cinza-escuro saiu há menos de 7 dias. Clique num canal
        para ver os vídeos.
      </p>
      {list.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
          {list.slice(0, shown).map((c) => (
            <ChannelCard key={c.id} c={c} data={data} onOpen={() => onOpen(c.id)} />
          ))}
        </div>
      ) : (
        <Empty title="Nenhum canal com esses filtros">Troque a idade, o nicho ou desligue "Com vídeo de 100 mil+".</Empty>
      )}
      {list.length > shown ? (
        <Button className="self-center" onClick={() => setShown((s) => s + PAGE)}>
          Mostrar mais
        </Button>
      ) : null}
    </div>
  );
};
