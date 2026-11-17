/* Abas de sinais da pesquisa: Fora da curva (concorrentes e buscas), Meu canal e Público (comentários). */
import { ExternalLink, type LucideIcon, MessageCircleQuestion, TrendingUp, Tv, Users } from 'lucide-react';
import React, { useState } from 'react';
import {
  RESEARCH_MARKET_SHORT,
  type ResearchComment,
  type ResearchCompetitorVideo,
  type ResearchFormat,
  type ResearchKeywordVideo,
  type ResearchOwnFormat,
  type ResearchOwnVideo,
  type ResearchRun,
} from '../../apiResearch';
import { Badge, Card, Empty, SegmentedControl } from '../../ui';
import { agoText, decimal, fmtNum, ratioText } from '../radar/format';
import { Thumb } from '../radar/shared';
import { countFormats, type FormatFilter, FormatFilterControl, type MarketFilter, MarketFilterControl, multText } from './shared';

const VideoLink: React.FC<{ url: string; title: string }> = ({ url, title }) => (
  <a href={url} target="_blank" rel="noreferrer" className="line-clamp-2 text-sm font-medium text-fg hover:text-accent hover:underline">
    {title}
  </a>
);

const CompetitorCard: React.FC<{ v: ResearchCompetitorVideo }> = ({ v }) => (
  <Card className="flex flex-col gap-2 p-3">
    <Thumb id={v.id} alt="" />
    <VideoLink url={v.url} title={v.title} />
    <div className="text-xs text-muted">{v.channel}</div>
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={v.mult >= 5 ? 'good' : 'info'} title={`Views ÷ mediana do próprio canal no formato (${fmtNum(v.med)})`}>
        {multText(v.mult)} a mediana
      </Badge>
      <Badge>{fmtNum(v.views)} views</Badge>
      <Badge>{agoText(v.idade_d)}</Badge>
      {v.format === 'short' ? <Badge tone="accent">Short</Badge> : null}
    </div>
  </Card>
);

const KeywordCard: React.FC<{ v: ResearchKeywordVideo }> = ({ v }) => (
  <Card className="flex flex-col gap-2 p-3">
    <Thumb id={v.id} alt="" />
    <VideoLink url={v.url} title={v.title} />
    <div className="text-xs text-muted">
      {v.channel}
      {v.subs != null ? ` · ${fmtNum(v.subs)} inscritos` : ''}
    </div>
    <div className="flex flex-wrap items-center gap-1.5">
      {v.outlier ? <Badge tone="good">Fora da curva</Badge> : null}
      <Badge tone={v.ratio >= 1 ? 'info' : 'neutral'} title="Views ÷ inscritos (mínimo de 1.000)">
        {ratioText(v.ratio)}× inscritos
      </Badge>
      <Badge>{fmtNum(v.views)} views</Badge>
      <Badge>{agoText(v.idade_d)}</Badge>
      <Badge tone={v.market === 'us' ? 'accent' : 'neutral'} title={`Busca: "${v.term}"`}>
        {RESEARCH_MARKET_SHORT[v.market]} · {v.term}
      </Badge>
      {v.format === 'short' ? <Badge tone="accent">Short</Badge> : null}
    </div>
  </Card>
);

const Grid: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">{children}</div>
);

const SectionTitle: React.FC<{ icon: LucideIcon; children: React.ReactNode }> = ({
  icon: Icon,
  children,
}) => (
  <h3 className="flex items-center gap-2 font-display text-[15px] font-semibold text-fg">
    <Icon size={16} aria-hidden="true" className="text-muted" />
    {children}
  </h3>
);

/** Concorrentes e buscas, longos e Shorts separados (públicos diferentes). */
export const OutliersTab: React.FC<{ run: ResearchRun }> = ({ run }) => {
  const comp = run.signals.competitors;
  const kw = run.signals.keywords;
  const compItems = comp?.items ?? [];
  const kwItems = kw?.items ?? [];
  const all = [...compItems, ...kwItems];
  const counts = countFormats(all);
  const [fmt, setFmt] = useState<FormatFilter>(counts.long || !counts.short ? 'long' : 'short');
  const [market, setMarket] = useState<MarketFilter>('all');
  const compShown = compItems.filter((v) => v.format === fmt);
  const kwFmt = kwItems.filter((v) => v.format === fmt);
  const kwShown = kwFmt.filter((v) => market === 'all' || v.market === market);
  const mkCounts = { br: kwFmt.filter((v) => v.market === 'br').length, us: kwFmt.filter((v) => v.market === 'us').length };

  if (!comp && !kw) {
    return (
      <Empty icon={TrendingUp} title="Sem concorrentes nem buscas nesta rodada">
        Cadastre concorrentes e palavras-chave na aba Configuração (e tenha acesso à YouTube Data API).
      </Empty>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <FormatFilterControl value={fmt} onChange={setFmt} counts={counts} withAll={false} />
      <section className="flex flex-col gap-3" aria-label="Concorrentes fora da curva">
        <SectionTitle icon={Users}>Concorrentes fora da curva</SectionTitle>
        <p className="text-[13px] text-muted">Vídeos com 2× ou mais a mediana do próprio concorrente no mesmo formato, dentro da janela da rodada.</p>
        {compShown.length ? (
          <Grid>
            {compShown.map((v) => (
              <CompetitorCard key={v.id} v={v} />
            ))}
          </Grid>
        ) : (
          <p className="text-sm text-muted">{comp ? 'Nenhum vídeo fora da curva neste formato.' : 'Concorrentes não rodaram nesta rodada.'}</p>
        )}
        {comp?.channels.length ? (
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full text-left text-[13px]">
              <caption className="sr-only">Concorrentes lidos</caption>
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-medium">Canal</th>
                  <th className="px-3 py-2 font-medium">Inscritos</th>
                  <th className="px-3 py-2 font-medium">Vídeos lidos</th>
                  <th className="px-3 py-2 font-medium">Mediana longos</th>
                  <th className="px-3 py-2 font-medium">Mediana Shorts</th>
                </tr>
              </thead>
              <tbody>
                {comp.channels.map((c) => (
                  <tr key={c.id} className="border-b border-line last:border-0">
                    <td className="px-3 py-2">
                      <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-fg hover:text-accent">
                        {c.name}
                        <ExternalLink size={12} aria-hidden="true" />
                      </a>
                    </td>
                    <td className="px-3 py-2 tabular-nums">{fmtNum(c.subs)}</td>
                    <td className="px-3 py-2 tabular-nums">{c.n}</td>
                    <td className="px-3 py-2 tabular-nums">{fmtNum(c.med_long)}</td>
                    <td className="px-3 py-2 tabular-nums">{fmtNum(c.med_short)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
      <section className="flex flex-col gap-3" aria-label="Buscas por palavra-chave">
        <SectionTitle icon={TrendingUp}>Buscas por palavra-chave</SectionTitle>
        <p className="text-[13px] text-muted">
          Os vídeos mais vistos de cada busca na janela; "fora da curva" = acima do hit do mercado e com mais views que inscritos. O mercado EUA mostra
          ângulos que ainda não existem em português.
        </p>
        {kw?.searches.length ? (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {kw.searches.map((s) => (
              <Badge key={`${s.market}-${s.term}`} tone={s.n ? 'neutral' : 'warn'}>
                {RESEARCH_MARKET_SHORT[s.market]} · {s.term}: {s.n}
              </Badge>
            ))}
            {kw.pulados.length ? <Badge tone="warn">Fora do orçamento: {kw.pulados.join(', ')}</Badge> : null}
          </div>
        ) : null}
        {mkCounts.us ? <MarketFilterControl value={market} onChange={setMarket} counts={mkCounts} /> : null}
        {kwShown.length ? (
          <Grid>
            {kwShown.map((v) => (
              <KeywordCard key={`${v.market}-${v.id}`} v={v} />
            ))}
          </Grid>
        ) : (
          <p className="text-sm text-muted">{kw ? 'Nenhum vídeo neste formato.' : 'As buscas não rodaram nesta rodada.'}</p>
        )}
      </section>
    </div>
  );
};

const pct = (v: number | null | undefined) => (v == null ? '—' : `${decimal(v)}%`);

const OwnList: React.FC<{ title: string; rows: ResearchOwnVideo[] }> = ({ title, rows }) => (
  <div className="flex flex-col gap-2">
    <div className="text-xs font-medium uppercase tracking-wide text-muted">{title}</div>
    {rows.length ? (
      <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
        {rows.map((r) => (
          <li key={r.youtube_id} className="flex items-center gap-3 px-3 py-2">
            <Thumb id={r.youtube_id} alt="" className="w-20 shrink-0" />
            <div className="min-w-0 flex-1">
              <VideoLink url={r.url} title={r.title || r.youtube_id} />
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
                <span>{fmtNum(r.views)} views</span>
                <span>{fmtNum(r.vpd)}/dia</span>
                <span title="Retenção média">ret. {pct(r.pct_watched)}</span>
                <span title="CTR da miniatura">CTR {pct(r.ctr)}</span>
              </div>
            </div>
            <Badge tone={(r.mult ?? 0) >= 1.5 ? 'good' : (r.mult ?? 1) < 0.6 ? 'bad' : 'neutral'} title="Views por dia ÷ mediana do formato">
              {multText(r.mult)}
            </Badge>
          </li>
        ))}
      </ul>
    ) : (
      <p className="text-sm text-muted">Nenhum vídeo.</p>
    )}
  </div>
);

const OwnFormat: React.FC<{ label: string; part: ResearchOwnFormat | undefined }> = ({ label, part }) => (
  <Card className="flex flex-col gap-3 p-4">
    <div className="flex flex-wrap items-baseline gap-x-3">
      <h3 className="font-display text-[15px] font-semibold text-fg">{label}</h3>
      <span className="text-[13px] text-muted">
        {part?.n ? `${part.n} vídeos com métrica · mediana de ${fmtNum(part.med)} views por dia` : 'sem vídeos com métrica'}
      </span>
    </div>
    {part?.n ? (
      <div className="grid gap-4 lg:grid-cols-2">
        <OwnList title="Melhores" rows={part.top} />
        <OwnList title="Piores" rows={part.bottom} />
      </div>
    ) : null}
  </Card>
);

/** Desempenho do próprio canal, longos e Shorts separados. */
export const OwnTab: React.FC<{ run: ResearchRun; formats: ResearchFormat[] }> = ({ run, formats }) => {
  const own = run.signals.own;
  // Etapa pulada ou que falhou: sem dados (rodadas antigas gravavam {} na falha).
  if (!own || !Object.keys(own).length) {
    return (
      <Empty icon={Tv} title="O próprio canal não foi lido nesta rodada">
        A etapa foi pulada ou falhou; veja as lacunas na aba Sugestões.
      </Empty>
    );
  }
  const shown: ResearchFormat[] = formats.length ? formats : ['long', 'short'];
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-muted">
        Views por dia de cada vídeo ÷ a mediana do formato no canal (1× = típico). É o que a síntese usa para saber o que funciona aqui.
      </p>
      {shown.map((f) => (
        <OwnFormat key={f} label={f === 'long' ? 'Vídeos longos' : 'Shorts'} part={own[f]} />
      ))}
      {own.suggestions?.length ? (
        <Card className="flex flex-col gap-2 p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted">Sugestões do Desempenho</div>
          <ul className="flex flex-col gap-1.5 text-[13px]">
            {own.suggestions.map((s) => (
              <li key={s.text} className="flex items-start gap-2">
                <Badge>{s.kind}</Badge>
                <span className="text-fg">{s.text}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
};

type KindFilter = 'all' | ResearchComment['kind'];

/** Perguntas e pedidos dos comentários (os mais curtidos primeiro). */
export const AudienceTab: React.FC<{ run: ResearchRun }> = ({ run }) => {
  const items = run.signals.comments?.items ?? [];
  const [kind, setKind] = useState<KindFilter>('all');
  if (!items.length) {
    return (
      <Empty icon={MessageCircleQuestion} title="Nenhuma pergunta ou pedido">
        A caixa de comentários do canal precisa ter sido lida (Comentários) e ter perguntas ou pedidos na janela da rodada.
      </Empty>
    );
  }
  const shown = items.filter((c) => kind === 'all' || c.kind === kind);
  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl
        label="Tipo de comentário"
        size="sm"
        value={kind}
        onChange={setKind}
        options={[
          { id: 'all', label: 'Todos', count: items.length },
          { id: 'pedido', label: 'Pedidos', count: items.filter((c) => c.kind === 'pedido').length },
          { id: 'pergunta', label: 'Perguntas', count: items.filter((c) => c.kind === 'pergunta').length },
        ]}
      />
      <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
        {shown.map((c) => (
          <li key={c.id} className="flex flex-col gap-1 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge tone={c.kind === 'pedido' ? 'accent' : 'info'}>{c.kind === 'pedido' ? 'Pedido' : 'Pergunta'}</Badge>
              <span>{c.likes} curtidas</span>
              {c.video_title ? <span className="truncate">em "{c.video_title}"</span> : null}
              {c.url ? (
                <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                  Abrir
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
              ) : null}
            </div>
            <p className="whitespace-pre-line text-sm text-fg">{c.text}</p>
          </li>
        ))}
      </ul>
    </div>
  );
};
