// Painel "Shorts deste vídeo" na aba Vídeo final (ADR 0002, bloco B).
// No longo com prévia ou vídeo: escolhe trechos, confere as regras ao vivo, pede sugestões e cria o Short (projeto
// filho que reaproveita a narração e as imagens). No Short cortado: faixa "Cortado de <longo>" e o enquadramento
// vertical das imagens 16:9. Short sem pai: nada.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Scissors } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Job, ProjectDetail } from '../../../api';
import { shortsApi, type ShortSuggestion } from '../../../apiShorts';
import { ErrorBox, Loading, Section, useToast } from '../../../ui';
import { ChildrenList } from './shorts/ChildrenList';
import { FrameEditor } from './shorts/FrameEditor';
import { indexesOf, rangesFrom, shortDuration } from './shorts/model';
import { ParentBanner } from './shorts/ParentBanner';
import { SegmentList } from './shorts/SegmentList';
import { type ShortDraft, ShortForm } from './shorts/ShortForm';
import { Suggestions } from './shorts/Suggestions';
import { isActive, useDeliveryLang, useLiveJob } from './shared';

const EMPTY_DRAFT: ShortDraft = { title: '', hook: '', tipo: 'normal' };

export const ShortsFromLong: React.FC<{ detail: ProjectDetail }> = ({ detail }) => {
  const { project, style } = detail;
  const isChild = Boolean(project.parent_id);
  const hasOutput = Boolean(detail.outputs.preview || detail.outputs.final);
  const isLong = style?.format.type === 'long';
  if (isChild) return <ChildPanel detail={detail} />;
  if (!isLong || !hasOutput) return null;
  return <LongPanel detail={detail} />;
};

const ChildPanel: React.FC<{ detail: ProjectDetail }> = ({ detail }) => {
  const q = useQuery({
    queryKey: ['shorts', detail.project.id, null],
    queryFn: () => shortsApi.list(detail.project.id),
  });
  if (!q.data?.parent) return null;
  return (
    <>
      <ParentBanner data={q.data} />
      <FrameEditor projectId={detail.project.id} />
    </>
  );
};

const LongPanel: React.FC<{ detail: ProjectDetail }> = ({ detail }) => {
  const projectId = detail.project.id;
  const [lang] = useDeliveryLang(detail.doc);
  const client = useQueryClient();
  const { toast } = useToast();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ['shorts', projectId, lang],
    queryFn: () => shortsApi.list(projectId, lang),
  });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [draft, setDraft] = useState<ShortDraft>(EMPTY_DRAFT);
  const [startedJob, setStartedJob] = useState<Job | undefined>();

  // Job de sugestão em andamento (o recém-pedido ou um que já estava na fila).
  const pending = startedJob ?? detail.jobs.find((j) => j.kind === 'shorts_suggest' && isActive(j));
  const liveJob = useLiveJob(pending);
  const running = liveJob && isActive(liveJob) ? liveJob : undefined;
  useEffect(() => {
    if (liveJob && !isActive(liveJob)) {
      setStartedJob(undefined);
      void client.invalidateQueries({ queryKey: ['shorts', projectId] });
      if (liveJob.status === 'failed') toast({ tone: 'bad', title: 'A sugestão de Shorts falhou', description: liveJob.error ?? undefined });
    }
  }, [liveJob?.status]);

  const suggest = useMutation({
    mutationFn: () => shortsApi.suggest(projectId, lang),
    onSuccess: (job) => setStartedJob(job),
  });

  const data = q.data;
  const ranges = useMemo(() => (data ? rangesFrom(data, selected) : []), [data, selected]);
  const seconds = data ? shortDuration(data, selected) : 0;

  const create = useMutation({
    mutationFn: () => shortsApi.create(projectId, { ranges, title: draft.title.trim(), hook: draft.hook.trim(), tipo: draft.tipo, lang }),
    onSuccess: async (res) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['shorts', projectId] }),
        client.invalidateQueries({ queryKey: ['projects'] }),
      ]);
      setSelected(new Set());
      setDraft(EMPTY_DRAFT);
      toast({
        tone: res.warnings.length ? 'warn' : 'good',
        title: 'Short criado',
        description: res.warnings.length ? res.warnings.join(' · ') : 'Narração e imagens reaproveitadas do vídeo longo.',
        action: { label: 'Abrir', onClick: () => navigate(`/projetos/${res.project_id}`) },
      });
    },
  });

  const toggle = (index: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const applySuggestion = (s: ShortSuggestion) => {
    if (!data) return;
    setSelected(indexesOf(data, s.segment_ids));
    setDraft({ title: s.title, hook: s.hook, tipo: s.tipo });
  };

  return (
    <Section
      icon={Scissors}
      title="Shorts deste vídeo"
      description="Corte trechos do vídeo longo em Shorts verticais. A narração e as imagens que o longo já tem são reaproveitadas."
    >
      {q.isLoading ? <Loading /> : null}
      {q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> : null}
      {data?.blocker ? <p className="text-sm text-muted">{data.blocker}.</p> : null}
      {data && !data.blocker ? (
        <>
          {data.stale ? (
            <p className="text-[13px] text-amber-200">
              O projeto mudou depois da última {data.source === 'final' ? 'renderização' : 'prévia'}: as durações podem estar
              diferentes. Gere a prévia de novo para conferir.
            </p>
          ) : null}
          {data.voice_missing?.length ? (
            <p className="text-[13px] text-amber-200">
              A narração da prévia é da voz de teste: as durações são estimadas, e o render final do Short vai gravar a voz
              do canal nos trechos escolhidos. Renderize o vídeo longo antes para medir com a voz final.
            </p>
          ) : null}
          {data.images_redo?.length ? (
            <p className="text-[13px] text-muted">
              {data.images_redo.length === 1 ? 'Uma imagem do longo é de teste' : `${data.images_redo.length} imagens do longo são de teste`}: se
              entrarem no Short, ele gera imagem nova no lugar.
            </p>
          ) : null}
          <p className="text-[13px] text-muted">
            As imagens do longo são 16:9: no Short vertical cada uma é recortada em volta de um ponto de foco sugerido. No
            Short dá para mudar o foco, usar a imagem inteira com fundo desfocado ou gerar a imagem vertical.
          </p>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="flex min-w-0 flex-col gap-2">
              <div className="text-sm font-medium text-fg">Segmentos ({data.source === 'final' ? 'do vídeo final' : 'da prévia'})</div>
              <SegmentList data={data} selected={selected} onToggle={toggle} />
            </div>
            <div className="flex min-w-0 flex-col gap-4">
              <ShortForm
                data={data}
                channelId={detail.project.channel_id}
                seconds={seconds}
                rangesCount={ranges.length}
                draft={draft}
                onDraft={setDraft}
                creating={create.isPending}
                onCreate={() => create.mutate()}
                onClear={() => {
                  setSelected(new Set());
                  setDraft(EMPTY_DRAFT);
                }}
              />
              {create.isError ? <ErrorBox error={create.error} title="Não deu para criar o Short" onClose={() => create.reset()} /> : null}
            </div>
          </div>
          <Suggestions data={data} job={running} busy={suggest.isPending} onSuggest={() => suggest.mutate()} onUse={applySuggestion} />
          {suggest.isError ? <ErrorBox error={suggest.error} onClose={() => suggest.reset()} /> : null}
        </>
      ) : null}
      {data ? <ChildrenList items={data.children} /> : null}
    </Section>
  );
};
