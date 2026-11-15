/* Aba "Vídeos fora da curva": views ÷ inscritos do canal (mínimo de 1.000 inscritos), com "Virar ideia". */
import { TriangleAlert } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import type { Channel } from '../../api';
import type { RadarPanel } from '../../apiRadar';
import { Button, Card, Checkbox, Empty, Select } from '../../ui';
import { AGE_OPTIONS } from './ChannelsTab';
import { agoText, ageText, fmtNum, ratioText, watchUrl } from './format';
import { IdeaButton, Thumb } from './shared';

const PAGE = 48;
const MIN_VIEWS = [
  { v: 50000, label: '50 mil+ views' },
  { v: 100000, label: '100 mil+ views' },
  { v: 500000, label: '500 mil+ views' },
  { v: 1000000, label: '1 mi+ views' },
];

export const OutliersTab: React.FC<{ data: RadarPanel; channels: Channel[]; onOpen: (id: string) => void }> = ({ data, channels, onOpen }) => {
  const [niche, setNiche] = useState('');
  const [age, setAge] = useState(12);
  const [minViews, setMinViews] = useState(100000);
  const [sort, setSort] = useState<'ratio' | 'views' | 'new'>('ratio');
  const [hideFlags, setHideFlags] = useState(true);
  const [shown, setShown] = useState(PAGE);
  const byId = useMemo(() => new Map(data.channels.map((c) => [c.id, c])), [data.channels]);
  const nicheNames = useMemo(() => data.niches.map((n) => n.name).sort((a, b) => a.localeCompare(b)), [data.niches]);
  const list = useMemo(
    () =>
      data.videos
        .map((v) => ({ v, c: v.c ? byId.get(v.c) : undefined }))
        .filter((o) => o.c && o.v.v >= minViews && (o.c.ageM ?? 999) <= age && (!niche || o.v.n.includes(niche)) && (!hideFlags || !o.c.flag))
        .map((o) => ({ v: o.v, c: o.c!, r: o.v.v / Math.max(o.c!.subs || 0, 1000) }))
        .sort((x, y) => (sort === 'views' ? y.v.v - x.v.v : sort === 'new' ? (x.v.a ?? 999) - (y.v.a ?? 999) : y.r - x.r)),
    [data.videos, byId, minViews, age, niche, hideFlags, sort],
  );
  const reset = () => setShown(PAGE);
  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-wrap items-center gap-2">
        <Select aria-label="Nicho" value={niche} onChange={(e) => (setNiche(e.target.value), reset())}>
          <option value="">Todos os nichos</option>
          {nicheNames.map((n) => (
            <option key={n}>{n}</option>
          ))}
        </Select>
        <Select aria-label="Idade do canal" value={age} onChange={(e) => (setAge(Number(e.target.value)), reset())}>
          {AGE_OPTIONS.map((o) => (
            <option key={o.v} value={o.v}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select aria-label="Views mínimas" value={minViews} onChange={(e) => (setMinViews(Number(e.target.value)), reset())}>
          {MIN_VIEWS.map((o) => (
            <option key={o.v} value={o.v}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select aria-label="Ordenar" value={sort} onChange={(e) => (setSort(e.target.value as typeof sort), reset())}>
          <option value="ratio">Ordenar: views ÷ inscritos</option>
          <option value="views">Mais views</option>
          <option value="new">Mais recentes</option>
        </Select>
        <Checkbox label="Esconder alertas" checked={hideFlags} onChange={(v) => (setHideFlags(v), reset())} />
        <span className="ml-auto font-mono text-xs text-muted">{list.length} vídeos</span>
      </Card>
      {list.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
          {list.slice(0, shown).map(({ v, c, r }) => (
            <Card key={v.id} className="flex flex-col gap-2 !p-0">
              <a href={watchUrl(v.id)} target="_blank" rel="noreferrer" className="relative block">
                <Thumb id={v.id} alt={v.t} className="rounded-b-none rounded-t-xl" />
                <span
                  className="absolute left-2 top-2 rounded bg-accent px-1.5 py-0.5 font-mono text-[11px] font-semibold text-on-accent"
                  title="views do vídeo ÷ inscritos do canal (mínimo de 1.000)"
                >
                  {ratioText(r)}× inscritos
                </span>
                <span className="absolute bottom-2 right-2 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[11px] text-white">{v.l} min</span>
              </a>
              <div className="flex flex-1 flex-col gap-1.5 px-3 pb-3">
                <a href={watchUrl(v.id)} target="_blank" rel="noreferrer" className="text-sm font-semibold leading-snug hover:text-accent">
                  {v.t}
                </a>
                <p className="text-xs text-muted">
                  <b className="font-mono text-fg">{fmtNum(v.v)}</b> views · {agoText(v.a)} · {v.n.join(', ')}
                </p>
                <button type="button" className="text-left text-xs text-sky-300 hover:underline" onClick={() => onOpen(c.id)}>
                  {c.name} · {fmtNum(c.subs)} inscritos · canal de {ageText(c.ageM)}
                </button>
                {c.flag ? (
                  <p className="flex items-start gap-1.5 rounded-md border border-red-400/35 bg-red-400/10 px-2 py-1 text-xs text-red-300">
                    <TriangleAlert size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                    {c.flag}
                  </p>
                ) : null}
                <div className="mt-auto">
                  <IdeaButton runId={data.meta.run_id} channels={channels} youtubeId={v.id} />
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Empty title="Nenhum vídeo com esses filtros">Diminua as views mínimas ou aumente a idade do canal.</Empty>
      )}
      {list.length > shown ? (
        <Button className="self-center" onClick={() => setShown((s) => s + PAGE)}>
          Mostrar mais
        </Button>
      ) : null}
    </div>
  );
};
