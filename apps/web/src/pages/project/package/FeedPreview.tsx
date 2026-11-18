// Prévia do feed do YouTube: a thumb do candidato no tamanho em que o público a vê (168, 246 e 360 px), com o
// selo de duração, ao lado das thumbs dos vídeos que mais performaram na pesquisa do canal (as referências).
// É a comparação que faltava: a thumb se destaca entre as vencedoras do nicho?

import { ExternalLink } from 'lucide-react';
import React, { useState } from 'react';
import { fileUrl, type PackageCandidate } from '../../../api';
import { layersHash, type PackageReference } from '../../../apiPackage';
import { Select } from '../../../ui';

/** Larguras do feed: barra lateral ("a seguir"), busca/celular e a grade da página inicial. */
export const FEED_SIZES = [
  { width: 168, label: '168 px', hint: 'Barra lateral (a seguir)' },
  { width: 246, label: '246 px', hint: 'Busca e celular' },
  { width: 360, label: '360 px', hint: 'Página inicial' },
] as const;

/** Quantas referências entram em cada linha (3 a 6, como pede a especificação). */
const REF_LIMIT = 6;

/** "11:24" ou "1:02:03". */
export const durationBadge = (seconds: number | null | undefined) => {
  if (!seconds || seconds <= 0) return null;
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
};

const compactViews = (n: number | null) => {
  if (n == null) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mi de visualizações`;
  if (n >= 1_000) return `${Math.round(n / 1_000).toLocaleString('pt-BR')} mil visualizações`;
  return `${n} visualizações`;
};

type FeedItem = {
  key: string;
  img: string | null;
  title: string;
  channel: string;
  meta: string;
  duration: string | null;
  mine: boolean;
  href?: string;
};

/** Cartão do feed numa largura. Em 168 px é a linha da barra lateral (thumb à esquerda, título à direita). */
const FeedCard: React.FC<{ item: FeedItem; width: number; vertical: boolean }> = ({ item, width, vertical }) => {
  const sidebar = width <= 168;
  const aspect = vertical ? '9 / 16' : '16 / 9';
  const titleSize = sidebar ? 'text-[12px] leading-4' : width <= 246 ? 'text-[13px] leading-[18px]' : 'text-[14px] leading-5';
  const thumb = (
    <div className="relative shrink-0 overflow-hidden rounded-lg bg-[#272727]" style={{ width, aspectRatio: aspect }}>
      {item.img ? (
        <img src={item.img} alt="" loading="lazy" className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full w-full items-center justify-center p-2 text-center text-[11px] text-[#aaa]">sem prévia</div>
      )}
      {item.duration ? (
        <span className="absolute bottom-1 right-1 rounded bg-black/80 px-1 text-[11px] font-medium leading-4 text-white tabular-nums">{item.duration}</span>
      ) : null}
      {item.mine ? <span className="absolute inset-0 rounded-lg ring-2 ring-inset ring-accent" aria-hidden="true" /> : null}
    </div>
  );
  const text = (
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className={`line-clamp-2 font-medium text-[#f1f1f1] ${titleSize}`} title={item.title}>
        {item.title || <span className="text-[#aaa]">(sem título)</span>}
      </div>
      <div className="truncate text-[11px] leading-4 text-[#aaa]">{item.channel}</div>
      {item.meta ? <div className="truncate text-[11px] leading-4 text-[#aaa]">{item.meta}</div> : null}
    </div>
  );
  const body = sidebar ? (
    <div className="flex gap-2" style={{ width: width + 170 }}>
      {thumb}
      {text}
    </div>
  ) : (
    <div className="flex flex-col gap-2" style={{ width }}>
      {thumb}
      {text}
    </div>
  );
  return (
    <li className="shrink-0" aria-label={item.mine ? `Seu candidato: ${item.title}` : `Referência: ${item.title}`}>
      {item.href ? (
        <a href={item.href} target="_blank" rel="noreferrer noopener" className="block rounded-lg">
          {body}
        </a>
      ) : (
        body
      )}
    </li>
  );
};

export const FeedPreview: React.FC<{
  candidates: PackageCandidate[];
  chosenId: string | null;
  references: PackageReference[];
  /** De onde vieram as referências (pacote ou última pesquisa), para dizer na tela. */
  referencesSource: 'package' | 'research' | null;
  referencesLoading?: boolean;
  channelName: string;
  /** Duração do vídeo (segundos) para o selo. */
  durationS: number | null;
  isShort: boolean;
}> = ({ candidates, chosenId, references, referencesSource, referencesLoading, channelName, durationS, isShort }) => {
  const withImage = candidates.filter((c) => c.image);
  const fallback = candidates.find((c) => c.id === chosenId) ?? withImage[0] ?? candidates[0] ?? null;
  const [pickedId, setPickedId] = useState<string | null>(null);
  const shown = candidates.find((c) => c.id === pickedId) ?? fallback;
  if (!shown) return null;

  const refs = references.slice(0, REF_LIMIT);
  const mine: FeedItem = {
    key: `mine-${shown.id}`,
    img: shown.image ? `${fileUrl(shown.image)}?v=${layersHash(shown)}` : null,
    title: shown.title,
    channel: channelName,
    meta: 'agora',
    duration: durationBadge(durationS),
    mine: true,
  };
  const others: FeedItem[] = refs.map((r) => ({
    key: r.id,
    img: r.thumb_url,
    title: r.title,
    channel: r.channel,
    meta: [compactViews(r.views), r.mult ? `${r.mult.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}× a mediana` : ''].filter(Boolean).join(' · '),
    duration: durationBadge(r.dur),
    mine: false,
    href: r.url,
  }));
  // O candidato entra em 2º lugar: no feed real ele nunca está sozinho nem sempre no começo.
  const row = [...others.slice(0, 1), mine, ...others.slice(1)];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-prose text-[13px] text-muted">
          {refs.length
            ? `Seu candidato (contorno âmbar) entre ${refs.length} vídeos que mais performaram ${
                referencesSource === 'research' ? 'na última pesquisa do canal' : 'nas referências do pacote'
              }. Ele chama atenção nos três tamanhos?`
            : referencesLoading
              ? 'Carregando as thumbs dos concorrentes…'
              : 'Sem referências: rode a pesquisa do canal para comparar com as thumbs dos concorrentes.'}
        </p>
        {candidates.length > 1 ? (
          <label className="flex items-center gap-2 text-[13px] text-muted">
            Candidato
            <Select value={shown.id} onChange={(e) => setPickedId(e.target.value)} className="max-w-64">
              {candidates.map((c, i) => (
                <option key={c.id} value={c.id}>
                  {i + 1}. {c.title || '(sem título)'}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
      </div>
      {/* Fundo e cores do tema escuro do YouTube: a thumb é vista assim, não no cartão do app. */}
      <div className="flex flex-col gap-5 rounded-xl bg-[#0f0f0f] p-4">
        {FEED_SIZES.map((size) => (
          <section key={size.width} aria-label={`Feed em ${size.label}: ${size.hint}`} className="flex flex-col gap-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-[#aaa]">
              {size.label} · {size.hint}
            </div>
            <ul className={`flex gap-3 overflow-x-auto pb-1 ${size.width <= 168 ? 'flex-wrap' : ''}`}>
              {row.map((item) => (
                <FeedCard key={`${size.width}-${item.key}`} item={item} width={size.width} vertical={isShort && item.mine} />
              ))}
            </ul>
          </section>
        ))}
        {refs.length ? (
          <p className="flex items-center gap-1 text-[11px] text-[#aaa]">
            <ExternalLink size={12} aria-hidden="true" /> As referências abrem o vídeo no YouTube.
          </p>
        ) : null}
      </div>
    </div>
  );
};
