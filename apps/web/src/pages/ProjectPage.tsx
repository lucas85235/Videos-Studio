import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CircleCheck,
  Clapperboard,
  Clock,
  FileText,
  Film,
  History,
  LayoutGrid,
  MonitorPlay,
  MoreHorizontal,
  Palette,
  Pencil,
  Star,
  Trash2,
  TriangleAlert,
  X,
  Check,
} from 'lucide-react';
import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError, type Job, type ProjectDetail } from '../api';
import { usePageTitle } from '../documentTitle';
import { langLabel } from '../labels';
import { useSeenFailures } from '../shell/data';
import { formatLabel, statusMeta } from './projects/meta';
import {
  Badge,
  Button,
  buttonClass,
  Empty,
  ErrorBox,
  IconButton,
  Input,
  Loading,
  Page,
  PageHeader,
  Skeleton,
  Tabs,
  tabPanelProps,
  useConfirm,
  useToast,
} from '../ui';
import { AutomationMenu } from './project/core/Automation';
import {
  fieldDrafts,
  isOrphanScriptDraft,
  restoreNotices,
  scriptDrafts,
  useDraftReconcile,
  useKeyedStore,
  useKeyedValue,
  useUnsavedCount,
} from './project/core/drafts';
import { useDismissedFailures } from './project/core/dismissedFailures';
import {
  defaultTab,
  DOC_BUSY_REASON,
  docJobRunning,
  estimateSeconds,
  formatDuration,
  freshness,
  isActive,
  latestFailure,
} from './project/core/model';
import { NextStepBanner } from './project/core/NextStep';
import { Popover, PopoverItem } from './project/core/Popover';
import { OutputTab } from './project/OutputTab';
import { ScenesTab } from './project/ScenesTab';
import { ScriptTab } from './project/ScriptTab';
import { PROJECT_TAB_SLUGS, type ProjectTab } from './project/types';
import { VersionsTab } from './project/VersionsTab';

// A prévia traz o Player do Remotion e o renderer: só carrega quando a aba é aberta.
const PreviewTab = lazy(() => import('./project/PreviewTab').then((m) => ({ default: m.PreviewTab })));

export const useProject = (projectId: string) =>
  useQuery({ queryKey: ['project', projectId], queryFn: () => api.get<ProjectDetail>(`/api/projects/${projectId}`) });

const startJobKey = (projectId: string) => ['startJob', projectId];

export const useStartJob = (projectId: string) => {
  const client = useQueryClient();
  return useMutation({
    // Mesma chave em todas as instâncias (banner e abas): dá para saber se QUALQUER botão está iniciando um job.
    mutationKey: startJobKey(projectId),
    mutationFn: ({ kind, payload }: { kind: string; payload?: Record<string, unknown> }) =>
      api.post<Job>(`/api/projects/${projectId}/jobs`, { kind, payload: payload ?? {} }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', projectId] }),
  });
};

/** Algum botão do projeto está iniciando um job agora (até o refetch trazer o job ativo)? */
export const useJobStarting = (projectId: string) => useIsMutating({ mutationKey: startJobKey(projectId) }) > 0;

/** Chave das aprovações (banner e abas usam a mesma, para não aprovar duas vezes). */
export const approveKey = (projectId: string, stage: 'script' | 'preview') => ['approve', projectId, stage];
export const useApproving = (projectId: string, stage: 'script' | 'preview') => useIsMutating({ mutationKey: approveKey(projectId, stage) }) > 0;

const TAB_BY_SLUG = Object.fromEntries(Object.entries(PROJECT_TAB_SLUGS).map(([tab, slug]) => [slug, tab as ProjectTab])) as Record<string, ProjectTab>;

const tabFromSlug = (slug: string | null): ProjectTab | undefined =>
  slug && Object.prototype.hasOwnProperty.call(TAB_BY_SLUG, slug) ? TAB_BY_SLUG[slug] : undefined;

/**
 * Rótulo de aba com versão curta abaixo de `sm` (a barra de abas não corta a palavra no celular).
 * O nome acessível é sempre o completo: a versão curta é aria-hidden e a completa fica sr-only no celular.
 */
const tabLabel = (full: string, short: string) => (
  <>
    <span aria-hidden="true" className="sm:hidden">
      {short}
    </span>
    <span className="sr-only sm:not-sr-only">{full}</span>
  </>
);

const truncate = (text: string, max = 48) => (text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text);

/** Devolve o foco ao lápis visível (há um no cabeçalho e outro na linha do celular). */
const focusPencil = () =>
  window.requestAnimationFrame(() => {
    const el = Array.from(document.querySelectorAll<HTMLElement>('[data-title-pencil]')).find((e) => e.offsetParent !== null);
    el?.focus();
  });

/** Título com edição no lugar (lápis → campo; Enter salva, Esc cancela). O texto digitado fica no store de rascunhos. */
const TitleEditor: React.FC<{
  projectId: string;
  title: string;
  onSave: (title: string) => void;
  onCancel: () => void;
  busy: boolean;
  blockedReason: string | null;
  error: unknown;
}> = ({ projectId, title, onSave, onCancel, busy, blockedReason, error }) => {
  const draft = useKeyedValue(fieldDrafts, projectId, 'title');
  const value = draft?.value ?? title;
  const clean = value.trim();
  const setValue = (next: string) => fieldDrafts.set(projectId, 'title', next === title ? null : { value: next, base: draft?.base ?? title });
  return (
    <form
      className="flex max-w-none flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        // Enter com o salvamento em curso não manda de novo (o botão ocupado só bloqueia o clique).
        if (busy || blockedReason) return;
        if (clean && clean !== title) onSave(clean);
        else onCancel();
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          autoFocus
          aria-label="Título do vídeo"
          value={value}
          maxLength={200}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault();
              onCancel();
            }
          }}
          className="min-w-0 flex-1 basis-72 font-display text-lg font-bold"
        />
        <Button type="submit" variant="primary" size="sm" icon={Check} busy={busy} disabled={!clean || Boolean(blockedReason)}>
          Salvar título
        </Button>
        <Button size="sm" variant="ghost" icon={X} onClick={onCancel}>
          Cancelar
        </Button>
      </div>
      {blockedReason ? <p className="text-[13px] text-amber-200">{blockedReason} O texto digitado continua aqui.</p> : null}
      <ErrorBox error={error} title="Não foi possível salvar o título" />
    </form>
  );
};

const PageSkeleton: React.FC = () => (
  <Page width="wide">
    <Loading label="Carregando o projeto…" className="sr-only" />
    <Skeleton className="mb-3 h-4 w-40" />
    <Skeleton className="mb-3 h-8 w-2/3" />
    <Skeleton className="mb-6 h-5 w-80" />
    <Skeleton className="mb-6 h-36 w-full rounded-xl" />
    <Skeleton className="mb-4 h-10 w-full" />
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <Skeleton className="h-64 rounded-xl" />
      <Skeleton className="h-48 rounded-xl" />
    </div>
  </Page>
);

const UnsavedDot: React.FC = () => (
  <span className="inline-flex items-center gap-1 text-xs text-amber-200">
    <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-amber-300" />
    <span className="sr-only">(</span>não salvo<span className="sr-only">)</span>
  </span>
);

export const ProjectPage: React.FC = () => {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const project = useProject(projectId);
  const startJob = useStartJob(projectId);
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  // 'vulto.falhasVistas' só apaga o aviso da barra lateral (o JobsDock marca ao abrir o projeto);
  // a falha continua na página até o usuário dispensar aqui.
  const { markSeen } = useSeenFailures();
  const { dismissed, dismiss } = useDismissedFailures();
  const [searchParams, setSearchParams] = useSearchParams();
  const [editingTitle, setEditingTitle] = useState(false);
  const [pinnedTab, setPinnedTab] = useState<{ id: string; tab: ProjectTab } | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const unsaved = useUnsavedCount(projectId);
  const fields = useKeyedStore(fieldDrafts, projectId);
  const scriptBucket = useKeyedStore(scriptDrafts, projectId);
  const restoreNotice = useKeyedValue(restoreNotices, projectId, 'last');
  const approvingScript = useApproving(projectId, 'script');
  const approvingPreview = useApproving(projectId, 'preview');
  const starting = useJobStarting(projectId);

  // Rascunhos de cenas que deixaram de existir (cenas refeitas, versão restaurada, junção pelo agente) saem com aviso.
  useDraftReconcile(project.data?.doc, (count) =>
    toast({
      tone: 'warn',
      title: count === 1 ? '1 prompt não salvo foi descartado' : `${count} prompts não salvos foram descartados`,
      description: 'As cenas em que você editava o prompt não existem mais nesta versão do projeto.',
    }),
  );

  // Trocar de projeto (mesma instância da rota) fecha o editor de título.
  useEffect(() => setEditingTitle(false), [projectId]);
  const titleEditing = editingTitle || Boolean(fields.title);
  // O <h1> não recebe texto puro (span com tamanho próprio, ou o editor): o título da aba do navegador é registrado aqui.
  usePageTitle(project.data?.doc.title ?? null);

  const invalidate = () => client.invalidateQueries({ queryKey: ['project', projectId] });
  const gates = useMutation({
    mutationFn: (patch: Record<string, boolean>) => api.patch(`/api/projects/${projectId}/settings`, { gates: patch }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/projects/${projectId}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['projects'] });
      navigate('/');
    },
  });
  const approveScript = useMutation({
    mutationKey: approveKey(projectId, 'script'),
    mutationFn: () => api.post(`/api/projects/${projectId}/approve`, { stage: 'script', continue_pipeline: true }),
    onSuccess: invalidate,
  });
  const approvePreview = useMutation({
    mutationKey: approveKey(projectId, 'preview'),
    mutationFn: () => api.post(`/api/projects/${projectId}/approve`, { stage: 'preview', continue_pipeline: true }),
    // Espera o refetch: até o job de render aparecer, o botão continua ocupado (um 2º clique faria um 2º render).
    onSuccess: async () => {
      await invalidate();
      toast({ tone: 'good', title: 'Prévia aprovada', description: 'O vídeo final está sendo renderizado.' });
    },
  });
  const rename = useMutation({
    mutationFn: ({ title, detail }: { title: string; detail: ProjectDetail }) =>
      detail.style
        ? api.post(`/api/projects/${projectId}/ops`, { ops: [{ op: 'set_title', title }], message: 'Título editado' })
        : // Sem o estilo, o servidor recusa as operações: grava o documento inteiro (o commit também atualiza o nome na lista).
          api.put(`/api/projects/${projectId}/doc`, { doc: { ...detail.doc, title }, message: 'Título editado' }),
    onSuccess: async () => {
      await Promise.all([invalidate(), client.invalidateQueries({ queryKey: ['projects'] })]);
      fieldDrafts.set(projectId, 'title', null);
      setEditingTitle(false);
      focusPencil();
    },
  });

  const data = project.data;
  const urlTab = tabFromSlug(searchParams.get('aba'));

  // Sem ?aba, a aba inicial sai do estado do projeto e fica fixa (não pula de aba quando o estado muda).
  useEffect(() => {
    if (data && !urlTab && pinnedTab?.id !== projectId) setPinnedTab({ id: projectId, tab: defaultTab(data) });
  }, [data, urlTab, pinnedTab, projectId]);

  // No celular a barra de abas rola na horizontal: a aba ativa entra na vista (sem rolar a página na vertical).
  const loaded = Boolean(data);
  const activeTabKey = urlTab ?? pinnedTab?.tab;
  useEffect(() => {
    const list = tabsRef.current?.querySelector<HTMLElement>('[role=tablist]');
    const selected = list?.querySelector<HTMLElement>('[role=tab][aria-selected=true]');
    if (!list || !selected || list.scrollWidth <= list.clientWidth) return;
    const left = selected.getBoundingClientRect().left - list.getBoundingClientRect().left + list.scrollLeft;
    const right = left + selected.offsetWidth;
    if (left < list.scrollLeft) list.scrollLeft = Math.max(0, left - 16);
    else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth + 16;
  }, [loaded, projectId, activeTabKey]);

  if (project.isLoading) return <PageSkeleton />;
  // Só troca a página pelo erro quando não há dados: uma falha num refetch de fundo não desmonta as abas.
  if (!data) {
    const notFound = project.error instanceof ApiError && project.error.status === 404;
    return (
      <Page width="narrow">
        <PageHeader title={notFound ? 'Projeto não encontrado' : 'Não foi possível abrir o projeto'} breadcrumb={[{ label: 'Projetos', to: '/' }]} />
        {notFound ? (
          <Empty
            icon={Clapperboard}
            title="Este projeto não existe mais"
            action={
              <Link to="/" className={buttonClass({ variant: 'primary' })}>
                Ver projetos
              </Link>
            }
          >
            Ele pode ter sido apagado. Os seus outros vídeos estão na lista de projetos.
          </Empty>
        ) : (
          <ErrorBox error={project.error ?? 'Projeto não encontrado'} onRetry={() => project.refetch()} />
        )}
      </Page>
    );
  }

  const { doc, project: row, warnings, style } = data;
  const active = data.jobs.filter(isActive);
  const busy = active.length > 0;
  const docBusy = docJobRunning(data);
  // Só os rascunhos de trechos que existem bloqueiam a aprovação; os órfãos ficam listados na aba Roteiro.
  const liveScriptDrafts = Object.keys(scriptBucket).filter((key) => !isOrphanScriptDraft(doc, key)).length;
  const { finalFresh, previewApprovedFresh, lastPreviewAt } = freshness(data);
  const restoreActive = Boolean(restoreNotice && restoreNotice.at > lastPreviewAt);
  const g = row.settings.gates ?? {};
  const pendingGates = gates.isPending ? (gates.variables ?? {}) : {};
  const gateValues = { script: pendingGates.script ?? g.script ?? true, preview: pendingGates.preview ?? g.preview ?? true };
  const failure = latestFailure(data);
  const showFailure = failure && !dismissed.includes(failure.id) ? failure : null;

  const tab: ProjectTab = urlTab ?? (pinnedTab?.id === projectId ? pinnedTab.tab : defaultTab(data));
  const setTab = (next: ProjectTab) =>
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set('aba', PROJECT_TAB_SLUGS[next]);
        return p;
      },
      { replace: true },
    );
  const goTo = (next: ProjectTab) => {
    setTab(next);
    // 'start': ao trocar de aba pela faixa, o conteúdo novo (ex.: o player da prévia) sobe para o topo da tela.
    window.requestAnimationFrame(() => tabsRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  };

  const start = (kind: string, payload?: Record<string, unknown>) => {
    if (starting) return;
    startJob.mutate({ kind, payload });
  };

  const openTitleEditor = () => {
    rename.reset();
    setEditingTitle(true);
  };
  const cancelTitle = () => {
    rename.reset();
    fieldDrafts.set(projectId, 'title', null);
    setEditingTitle(false);
    focusPencil();
  };

  const askDelete = async () => {
    const ok = await confirm({
      title: `Apagar “${truncate(doc.title, 60)}”?`,
      description:
        'O projeto, o roteiro, as cenas e o histórico de versões somem do app. Não dá para desfazer. Os arquivos já gerados (imagens, narração, vídeos) continuam no cache do disco.',
      confirmLabel: 'Apagar projeto',
      tone: 'danger',
    });
    if (ok) remove.mutate();
  };

  const retryFailure = async (job: Job) => {
    if (job.kind === 'script' && doc.segments.length) {
      const ok = await confirm({
        title: 'Gerar outro roteiro?',
        description: (
          <>
            Gera um roteiro novo do zero: substitui <strong className="text-fg">todos</strong> os trechos (inclusive os travados), apaga as cenas e tira a
            aprovação. O título e as fontes também são trocados pelos do novo roteiro.
            {unsaved.script ? ' Suas alterações não salvas no roteiro ficam guardadas para você copiar ou descartar.' : ''} Dá para voltar em Versões. Usa o
            Claude.
          </>
        ),
        confirmLabel: 'Gerar outro roteiro',
        tone: 'danger',
      });
      if (!ok) return;
    } else if (job.kind === 'storyboard' && doc.scenes.length) {
      const ok = await confirm({
        title: 'Refazer as cenas?',
        description: `Cria as cenas de novo a partir do roteiro: substitui as cenas atuais e os ajustes feitos nelas.${
          unsaved.prompts ? ` ${unsaved.prompts === 1 ? 'O prompt não salvo se perde' : `Os ${unsaved.prompts} prompts não salvos se perdem`}.` : ''
        } Dá para voltar em Versões. Usa o Claude.`,
        confirmLabel: 'Refazer cenas',
      });
      if (!ok) return;
    }
    start(job.kind, job.payload);
  };

  const dismissFailure = () => {
    if (!failure) return;
    dismiss(failure.id);
    markSeen(failure.id);
  };

  // Duração: a do vídeo/prévia, se existir; senão a estimada pelo roteiro.
  const built = data.outputs.final?.langs?.[doc.primary_language] ?? data.outputs.preview?.langs?.[doc.primary_language];
  const estimate = estimateSeconds(
    style,
    doc.primary_language,
    doc.segments.map((s) => s.text[doc.primary_language] ?? ''),
  );
  const fmt = style?.format ? formatLabel(style.format) : null;
  const status = statusMeta(row.status);

  const meta = (
    <>
      <Badge tone={status.tone}>{status.label}</Badge>
      {style ? (
        <Link to={`/estilos/${doc.style_id}`} className="rounded-md" title="Abrir o estilo">
          <Badge icon={Palette} className="hover:border-[#4a4a56] hover:text-fg">
            {style.name}
            {fmt ? ` · ${fmt}` : ''}
          </Badge>
        </Link>
      ) : (
        <Badge tone="warn" icon={TriangleAlert}>
          Estilo removido
        </Badge>
      )}
      <span className="inline-flex flex-wrap items-center gap-1.5">
        {doc.languages.map((code) => (
          <Badge key={code} title={code === doc.primary_language ? 'Idioma principal' : 'Tradução'}>
            {code === doc.primary_language ? <Star size={12} aria-hidden="true" className="shrink-0" /> : null}
            {langLabel(code)}
            {code === doc.primary_language ? <span className="sr-only"> (principal)</span> : null}
          </Badge>
        ))}
      </span>
      {built?.duration ? (
        <span
          className="inline-flex items-center gap-1 tabular-nums"
          title={data.outputs.final?.langs?.[doc.primary_language] ? 'Duração do vídeo gerado' : 'Duração da prévia gerada'}
        >
          <Clock size={14} aria-hidden="true" />
          {formatDuration(built.duration)}
        </span>
      ) : estimate ? (
        <span className="inline-flex items-center gap-1 tabular-nums" title="Duração estimada pelo número de palavras do roteiro">
          <Clock size={14} aria-hidden="true" />~{formatDuration(estimate)}
          <span className="sr-only"> (estimada)</span>
        </span>
      ) : null}
    </>
  );

  const renameReason = docBusy ? DOC_BUSY_REASON : null;

  const headerActions = (where: 'header' | 'row') =>
    titleEditing ? null : (
      <>
        <IconButton label="Renomear o vídeo" icon={Pencil} data-title-pencil="" onClick={openTitleEditor} />
        <AutomationMenu
          detail={data}
          gates={gateValues}
          busy={busy}
          onGate={(gate, value) => gates.mutate({ [gate]: value })}
          gatePending={gates.isPending}
          gateError={gates.error}
          onAutopilot={() => start('autopilot', {})}
          autopilotBusy={startJob.isPending && startJob.variables?.kind === 'autopilot'}
        />
        <Popover label="Mais ações" icon={MoreHorizontal} iconOnly variant="ghost" align={where === 'row' ? 'left' : 'right'} panelClassName="w-72 p-1.5">
          {(close) => (
            <div className="flex flex-col">
              <PopoverItem
                icon={MonitorPlay}
                label={data.outputs.preview ? 'Gerar a prévia de novo' : 'Gerar prévia'}
                hint="Usa a cota de voz e de imagens no que mudou."
                disabled={busy || starting || !doc.scenes.length}
                disabledReason={busy || starting ? 'Aguarde o trabalho em andamento terminar.' : 'Crie as cenas primeiro.'}
                onClick={() => {
                  close();
                  start('preview');
                }}
              />
              <PopoverItem
                icon={Film}
                label={data.outputs.final ? 'Renderizar de novo' : 'Renderizar vídeo final'}
                hint={`${previewApprovedFresh ? '' : 'Sem passar pela aprovação da prévia. '}Pode usar a cota de voz e de imagens.`}
                disabled={busy || starting || !doc.scenes.length}
                disabledReason={busy || starting ? 'Aguarde o trabalho em andamento terminar.' : 'Crie as cenas primeiro.'}
                onClick={() => {
                  close();
                  start('render');
                }}
              />
              <div className="my-1 border-t border-line" />
              <PopoverItem
                icon={Trash2}
                label="Apagar projeto"
                danger
                disabled={busy}
                disabledReason="Cancele ou aguarde o trabalho em andamento antes de apagar."
                onClick={() => {
                  close();
                  void askDelete();
                }}
              />
            </div>
          )}
        </Popover>
      </>
    );

  const scriptUnsaved = unsaved.script > 0 || Boolean(fields.topic || fields.brief);
  const scriptBadge = scriptUnsaved ? (
    <UnsavedDot />
  ) : doc.script_approved ? (
    <>
      <CircleCheck size={14} aria-hidden="true" className="text-emerald-400" />
      <span className="sr-only">(aprovado)</span>
    </>
  ) : null;

  const countBadge = (n: number, label: string) => (
    <span className="rounded-md bg-raised px-1.5 text-xs tabular-nums text-muted">
      <span aria-hidden="true">{n}</span>
      <span className="sr-only">{label}</span>
    </span>
  );

  const tabs = [
    { id: 'script', label: 'Roteiro', icon: FileText, badge: scriptBadge },
    {
      id: 'scenes',
      label: 'Cenas',
      icon: LayoutGrid,
      badge:
        unsaved.prompts > 0 ? (
          <UnsavedDot />
        ) : doc.scenes.length ? (
          countBadge(doc.scenes.length, `(${doc.scenes.length} cenas)`)
        ) : null,
    },
    {
      id: 'preview',
      label: tabLabel('Prévia e edição', 'Prévia'),
      icon: MonitorPlay,
      badge:
        data.outputs.preview && doc.scenes.length && !previewApprovedFresh && !finalFresh ? (
          <span className="inline-flex items-center">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-accent" />
            <span className="sr-only">(aguardando sua revisão)</span>
          </span>
        ) : null,
    },
    {
      id: 'output',
      label: tabLabel('Vídeo e publicação', 'Vídeo'),
      icon: Film,
      badge: finalFresh ? (
        <>
          <CircleCheck size={14} aria-hidden="true" className="text-emerald-400" />
          <span className="sr-only">(vídeo pronto)</span>
        </>
      ) : null,
    },
    {
      id: 'versions',
      label: 'Versões',
      icon: History,
      badge: <span className="text-xs tabular-nums text-subtle">v{row.current_version}</span>,
    },
  ];

  return (
    <Page width="wide">
      <PageHeader
        // Em edição, o <h1> fica só para leitores de tela e o formulário vai fora dele (no lugar da descrição).
        title={
          titleEditing ? (
            <span className="sr-only">{doc.title}</span>
          ) : (
            // Menor no celular: o título longo não empurra o conteúdo da aba para fora da primeira tela.
            <span className="block text-xl leading-snug sm:text-2xl sm:leading-tight">{doc.title}</span>
          )
        }
        description={
          titleEditing ? (
            <TitleEditor
              projectId={projectId}
              title={doc.title}
              busy={rename.isPending}
              blockedReason={renameReason}
              error={rename.error}
              onSave={(title) => rename.mutate({ title, detail: data })}
              onCancel={cancelTitle}
            />
          ) : undefined
        }
        // Só "Projetos": o título do vídeo já é o <h1> logo abaixo (repeti-lo gastava uma linha no celular).
        breadcrumb={[{ label: 'Projetos', to: '/' }]}
        meta={meta}
        actions={titleEditing ? null : <div className="hidden items-center gap-2 sm:flex">{headerActions('header')}</div>}
      />
      {titleEditing ? null : <div className="-mt-3 mb-5 flex flex-wrap items-center gap-2 sm:hidden">{headerActions('row')}</div>}

      {project.error ? (
        <ErrorBox
          className="mb-6"
          error={project.error}
          title="Não foi possível atualizar o projeto"
          onRetry={() => project.refetch()}
        />
      ) : null}

      <NextStepBanner
        detail={data}
        tab={tab}
        goTo={goTo}
        scriptDrafts={liveScriptDrafts}
        restoreNotice={restoreActive}
        startJob={(kind) => start(kind)}
        startBusy={starting}
        approveScript={() => {
          if (!approvingScript) approveScript.mutate();
        }}
        approveScriptBusy={approvingScript}
        approvePreview={() => {
          if (!approvingPreview) approvePreview.mutate();
        }}
        approvePreviewBusy={approvingPreview}
        failure={showFailure}
        onRetry={() => {
          if (showFailure) void retryFailure(showFailure);
        }}
        retryBusy={startJob.isPending}
        onDismissFailure={dismissFailure}
      />

      {startJob.error || approveScript.error || approvePreview.error || remove.error ? (
        <ErrorBox
          className="mb-6"
          error={startJob.error ?? approveScript.error ?? approvePreview.error ?? remove.error}
          title={remove.error ? 'Não foi possível apagar o projeto' : 'Não foi possível iniciar'}
          onClose={() => {
            startJob.reset();
            approveScript.reset();
            approvePreview.reset();
            remove.reset();
          }}
        />
      ) : null}

      {warnings.length ? (
        <div role="note" className="mb-6 flex gap-3 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm">
          <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          <ul className="flex flex-col gap-1 text-amber-100">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div ref={tabsRef} className="scroll-mt-4">
        <Tabs
          tabs={tabs}
          active={tab}
          onChange={(id) => setTab(id as ProjectTab)}
          ariaLabel="Etapas do projeto"
          idPrefix="projeto"
          // "Versões" é consulta, não etapa: fica à direita.
          className="[&>[role=tab]:last-child]:ml-auto"
        />
      </div>
      <div
        {...tabPanelProps(tab, 'projeto')}
        className="rounded-lg pt-5 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
      >
        {tab === 'script' ? <ScriptTab detail={data} busy={busy} goTo={goTo} /> : null}
        {tab === 'scenes' ? <ScenesTab detail={data} busy={busy} goTo={goTo} /> : null}
        {tab === 'preview' ? (
          <Suspense fallback={<Loading label="Carregando o player…" />}>
            <PreviewTab detail={data} busy={busy} goTo={goTo} />
          </Suspense>
        ) : null}
        {tab === 'output' ? <OutputTab detail={data} busy={busy} goTo={goTo} /> : null}
        {tab === 'versions' ? <VersionsTab detail={data} busy={busy} goTo={goTo} /> : null}
      </div>
    </Page>
  );
};
