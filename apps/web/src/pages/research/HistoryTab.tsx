/* Aba Histórico: rodadas anteriores do canal, abrir uma antiga e baixar ou copiar o relatório em Markdown. */
import { useMutation, useQuery } from '@tanstack/react-query';
import { Copy, Download, FolderOpen, History } from 'lucide-react';
import React from 'react';
import {
  RESEARCH_MARKET_SHORT,
  RESEARCH_STATUS_LABELS,
  researchApi,
  researchReportDownloadUrl,
  type ResearchRunSummary,
} from '../../apiResearch';
import { Badge, type BadgeTone, Button, buttonClass, Empty, ErrorBox, Skeleton, useToast } from '../../ui';
import { runDateText } from './shared';

const STATUS_TONE: Record<string, BadgeTone> = { ok: 'good', parcial: 'warn', cancelada: 'neutral', falhou: 'bad' };

const CopyReport: React.FC<{ runId: string }> = ({ runId }) => {
  const { toast } = useToast();
  const copy = useMutation({
    mutationFn: async () => {
      const r = await researchApi.report(runId);
      await navigator.clipboard.writeText(r.markdown);
      return r;
    },
    onSuccess: () => toast({ tone: 'good', title: 'Relatório copiado', description: 'Em Markdown, pronto para colar.' }),
    onError: (e) => toast({ tone: 'bad', title: 'Não deu para copiar o relatório', description: e instanceof Error ? e.message : String(e) }),
  });
  return (
    <Button size="sm" variant="ghost" icon={Copy} busy={copy.isPending} onClick={() => copy.mutate()}>
      Copiar
    </Button>
  );
};

export const HistoryTab: React.FC<{ channelId: string; openRunId: string | null; onOpen: (run: ResearchRunSummary) => void }> = ({
  channelId,
  openRunId,
  onOpen,
}) => {
  const runs = useQuery({ queryKey: ['research', 'runs', channelId], queryFn: () => researchApi.runs(channelId, 50) });
  if (runs.isLoading) return <Skeleton className="h-40" />;
  if (runs.error) return <ErrorBox error={runs.error} onRetry={() => runs.refetch()} />;
  const list = runs.data ?? [];
  if (!list.length) {
    return (
      <Empty icon={History} title="Nenhuma rodada ainda">
        Use "Pesquisar agora" no cartão acima.
      </Empty>
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
      {list.map((r) => {
        const open = r.id === openRunId;
        const markets = (r.params.markets ?? []).map((m) => RESEARCH_MARKET_SHORT[m] ?? m).join(' + ');
        return (
          <li key={r.id} className={`flex flex-wrap items-center gap-3 px-3 py-2.5 ${open ? 'bg-raised' : ''}`}>
            <div className="min-w-[14rem] flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm text-fg">
                <span className="font-medium">{runDateText(r.created_at)}</span>
                <Badge tone={STATUS_TONE[r.status] ?? 'neutral'}>{RESEARCH_STATUS_LABELS[r.status] ?? r.status}</Badge>
                {r.params.weekly ? <Badge title="Rodada da rotina semanal">Semanal {r.params.weekly}</Badge> : null}
                {open ? <Badge tone="accent">Aberta</Badge> : null}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                {r.n_ideas} {r.n_ideas === 1 ? 'ideia' : 'ideias'} ({r.n_saved} {r.n_saved === 1 ? 'salva' : 'salvas'}) · {r.units} unidades de cota
                {markets ? ` · ${markets}` : ''}
                {r.lacunas.length ? ` · ${r.lacunas.length} ${r.lacunas.length === 1 ? 'lacuna' : 'lacunas'}` : ''}
                {r.error ? ` · ${r.error}` : ''}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button size="sm" icon={FolderOpen} disabled={open} onClick={() => onOpen(r)}>
                Abrir
              </Button>
              <a href={researchReportDownloadUrl(r.id)} download className={buttonClass({ size: 'sm', variant: 'ghost' })}>
                <Download size={14} aria-hidden="true" />
                Baixar relatório
              </a>
              <CopyReport runId={r.id} />
            </div>
          </li>
        );
      })}
    </ul>
  );
};
