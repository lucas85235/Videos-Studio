import { Film } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import type { AnalyticsVideo } from '../../apiAnalytics';
import { Badge, Empty } from '../../ui';
import { fmtAge, fmtCount, fmtDate, fmtPct } from './format';

const Th: React.FC<{ children: React.ReactNode; right?: boolean }> = ({ children, right }) => (
  <th scope="col" className={`whitespace-nowrap px-3 py-2 text-[13px] font-medium text-muted ${right ? 'text-right' : 'text-left'}`}>
    {children}
  </th>
);

const Td: React.FC<{ children: React.ReactNode; right?: boolean; className?: string }> = ({ children, right, className = '' }) => (
  <td className={`px-3 py-2 align-top ${right ? 'text-right tabular-nums' : ''} ${className}`}>{children}</td>
);

/** Tabela de vídeos: última métrica de cada um. Vazio aparece como "—" (número que ainda não chegou). */
export const VideosTable: React.FC<{ videos: AnalyticsVideo[] }> = ({ videos }) => {
  if (!videos.length) {
    return (
      <Empty icon={Film} title="Nenhum vídeo ainda">
        Os vídeos enviados ao YouTube pelo Vulto e os vídeos recentes dos canais vinculados aparecem aqui depois da primeira coleta.
      </Empty>
    );
  }
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[860px] border-collapse text-sm">
        <thead className="border-b border-line">
          <tr>
            <Th>Vídeo</Th>
            <Th>Publicado</Th>
            <Th right>Idade</Th>
            <Th right>Views</Th>
            <Th right>Views/dia</Th>
            <Th right>Impressões</Th>
            <Th right>CTR</Th>
            <Th right>Retenção</Th>
            <Th right>Likes</Th>
          </tr>
        </thead>
        <tbody>
          {videos.map((v) => {
            const m = v.metric;
            return (
              <tr key={v.youtube_id} className="border-b border-line/60 last:border-0">
                <Td className="max-w-[320px]">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <a
                      href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.youtube_id)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="truncate font-medium text-fg hover:underline"
                      title={v.title ?? v.youtube_id}
                    >
                      {v.title || v.youtube_id}
                    </a>
                    <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-subtle">
                      <Badge tone={v.format === 'short' ? 'info' : 'neutral'}>{v.format === 'short' ? 'Short' : 'Longo'}</Badge>
                      {v.project_id ? (
                        <Link to={`/projetos/${v.project_id}`} className="hover:text-fg hover:underline">
                          Projeto
                        </Link>
                      ) : null}
                    </div>
                  </div>
                </Td>
                <Td className="whitespace-nowrap text-muted">{fmtDate(v.published_at)}</Td>
                <Td right>{fmtAge(m?.age_hours)}</Td>
                <Td right>{fmtCount(m?.views)}</Td>
                <Td right>{fmtCount(v.views_per_day)}</Td>
                <Td right>{fmtCount(m?.impressions)}</Td>
                <Td right>{fmtPct(m?.ctr)}</Td>
                <Td right>{fmtPct(m?.pct_watched)}</Td>
                <Td right>{fmtCount(m?.likes)}</Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
