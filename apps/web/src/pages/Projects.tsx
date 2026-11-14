import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Clapperboard, Plus, Search, SearchX, X } from 'lucide-react';
import React, { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type Channel, type Job, type StyleItem } from '../api';
import { isActiveJob, useJobs, useSeenFailures } from '../shell/data';
import { Button, buttonClass, Empty, ErrorBox, IconButton, Input, Page, PageHeader, Skeleton } from '../ui';
import { bucketOf, FILTERS, isFilter, normalize, PIPELINE_STEPS, type ProjectFilter, type ProjectListItem } from './projects/meta';
import { ProjectCard } from './projects/ProjectCard';

const SEARCH_ID = 'projetos-busca';

const jobTime = (job: Job) => Date.parse(job.created_at ?? '') || 0;

const focusSearch = () => window.setTimeout(() => document.getElementById(SEARCH_ID)?.focus(), 0);

const NewProjectLink: React.FC<{ variant: 'primary' | 'secondary' }> = ({ variant }) => (
  <Link to="/novo" className={buttonClass({ variant })}>
    <Plus size={16} strokeWidth={2} aria-hidden="true" className="shrink-0" />
    Novo projeto
  </Link>
);

export const ProjectsPage: React.FC = () => {
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<ProjectListItem[]>('/api/projects') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const jobs = useJobs();
  const { seen } = useSeenFailures();

  const [params, setParams] = useSearchParams();
  const rawFilter = params.get('filtro');
  const filter: ProjectFilter = isFilter(rawFilter) ? rawFilter : 'todos';

  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (value) p.set(key, value);
        else p.delete(key);
        return p;
      },
      { replace: true },
    );

  // O texto da busca vive no estado local: a URL só espelha. (O BrowserRouter aplica a mudança de
  // endereço numa transição; um campo controlado direto por ?q= perde teclas e pula o cursor.)
  const [query, setQuery] = useState(() => params.get('q') ?? '');
  const changeQuery = (value: string) => {
    setQuery(value);
    setParam('q', value || null);
  };
  const deferredQuery = useDeferredValue(query);
  // A URL mudou por fora (ex.: link da barra lateral para a lista sem ?q=): só copia com o campo fora de foco,
  // para uma transição atrasada nunca sobrescrever o que está sendo digitado.
  const urlQuery = params.get('q') ?? '';
  useEffect(() => {
    if (document.activeElement?.id !== SEARCH_ID) setQuery(urlQuery);
  }, [urlQuery]);

  const styleById = useMemo(() => new Map((styles.data ?? []).map((s) => [s.style.id, s])), [styles.data]);
  const channelById = useMemo(() => new Map((channels.data ?? []).map((c) => [c.id, c.name])), [channels.data]);

  // Por projeto: a tarefa em andamento (rodando antes de na fila) e a última tarefa, se ela falhou
  // (e o usuário ainda não viu a falha) ou foi cancelada.
  const jobsByProject = useMemo(() => {
    const map = new Map<string, { active?: Job; failed?: Job; canceled?: Job }>();
    const sorted = [...(jobs.data ?? [])].sort((a, b) => jobTime(b) - jobTime(a));
    for (const job of sorted) {
      if (!job.project_id) continue;
      const entry = map.get(job.project_id) ?? {};
      const isFirst = !map.has(job.project_id);
      if (isActiveJob(job) && (!entry.active || (entry.active.status === 'queued' && job.status === 'running'))) entry.active = job;
      if (isFirst && job.status === 'failed' && !seen.includes(job.id)) entry.failed = job;
      if (isFirst && job.status === 'canceled') entry.canceled = job;
      map.set(job.project_id, entry);
    }
    return map;
  }, [jobs.data, seen]);

  const list = useMemo(() => projects.data ?? [], [projects.data]);
  const rows = useMemo(
    () =>
      list.map((p) => {
        const j = jobsByProject.get(p.id);
        const style = styleById.get(p.style_id);
        const channelName = p.channel_id ? (channelById.get(p.channel_id) ?? null) : null;
        return {
          project: p,
          style,
          channelName,
          activeJob: j?.active,
          failedJob: j?.failed,
          canceledJob: j?.canceled,
          bucket: bucketOf(p, j?.active, j?.failed, j?.canceled),
          haystack: normalize([p.title, style?.style.name ?? '', channelName ?? ''].join(' ')),
        };
      }),
    [list, jobsByProject, styleById, channelById],
  );

  const counts = useMemo(() => {
    const c: Record<ProjectFilter, number> = { todos: rows.length, esperando: 0, producao: 0, prontos: 0 };
    for (const r of rows) c[r.bucket] += 1;
    return c;
  }, [rows]);
  const runningCount = rows.filter((r) => r.activeJob).length;

  // Cada palavra precisa aparecer, em qualquer ordem ("segredo farol" acha "O segredo do farol").
  const needle = normalize(deferredQuery);
  const terms = needle.split(/\s+/).filter(Boolean);
  const visible = rows.filter((r) => (filter === 'todos' || r.bucket === filter) && terms.every((t) => r.haystack.includes(t)));
  const filtering = filter !== 'todos' || terms.length > 0;

  const hasProjects = list.length > 0;
  const firstLoad = projects.isLoading;
  // Vazio de fato (a lista carregou e não tem nada): o estado vazio já traz o próprio botão.
  const reallyEmpty = projects.isSuccess && !hasProjects;

  // As contagens por etapa ficam só nos filtros; aqui, só o que eles não mostram (tarefas rodando agora).
  const meta = hasProjects && runningCount ? (
    <span className="tabular-nums">
      {runningCount} {runningCount === 1 ? 'projeto com tarefa rodando' : 'projetos com tarefa rodando'}
    </span>
  ) : null;

  const showAll = () => {
    setQuery('');
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.delete('q');
        p.delete('filtro');
        return p;
      },
      { replace: true },
    );
    focusSearch();
  };

  return (
    <Page>
      <PageHeader
        title="Projetos"
        description="Cada projeto é um vídeo, em um ou mais idiomas. Abra um projeto para continuar de onde parou."
        meta={meta}
        actions={
          reallyEmpty ? null : (
            <>
              {/* No celular a barra lateral fica numa gaveta: aqui é a ação principal. Com a barra à vista, o âmbar fica só nela. */}
              <span className="md:hidden">
                <NewProjectLink variant="primary" />
              </span>
              <span className="hidden md:block">
                <NewProjectLink variant="secondary" />
              </span>
            </>
          )
        }
      />

      {projects.error ? (
        <ErrorBox
          error={projects.error}
          title="Não foi possível carregar os projetos"
          onRetry={() => projects.refetch()}
          className="mb-4"
        />
      ) : null}

      {firstLoad ? (
        <div role="status" className="grid grid-cols-1 gap-3 @2xl:grid-cols-2 @5xl:grid-cols-3">
          <span className="sr-only">Carregando projetos…</span>
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-[196px] rounded-xl" />
          ))}
        </div>
      ) : null}

      {reallyEmpty ? (
        <Empty
          icon={Clapperboard}
          title="Seu primeiro vídeo começa aqui"
          action={
            <Link to="/novo" className={buttonClass({ variant: 'primary' })}>
              <Plus size={16} strokeWidth={2} aria-hidden="true" className="shrink-0" />
              Criar o primeiro vídeo
            </Link>
          }
        >
          <p>Você escreve o tema e aprova cada etapa. Nada que gaste cota de voz ou imagens acontece antes da sua aprovação do roteiro.</p>
          <ol className="mt-4 flex flex-wrap items-center justify-center gap-x-2 gap-y-2 text-[13px]">
            {['Tema', ...PIPELINE_STEPS.filter((s) => s !== 'Cenas')].map((step, i, all) => (
              <li key={step} className="flex items-center gap-2">
                <span className="flex items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-fg">
                  <span className="tabular-nums text-subtle">{i + 1}</span>
                  {step}
                </span>
                {i < all.length - 1 ? <ChevronRight size={14} aria-hidden="true" className="text-subtle" /> : null}
              </li>
            ))}
          </ol>
        </Empty>
      ) : null}

      {hasProjects ? (
        <>
          <div className="mb-4 flex flex-col gap-3 @4xl:flex-row @4xl:items-center @4xl:justify-between">
            <div role="group" aria-label="Filtrar projetos por etapa" className="flex flex-wrap gap-1.5">
              {FILTERS.map((f) => {
                const pressed = filter === f.id;
                const highlight = f.id === 'esperando' && counts.esperando > 0;
                return (
                  <button
                    key={f.id}
                    type="button"
                    aria-pressed={pressed}
                    title={f.hint}
                    onClick={() => setParam('filtro', f.id === 'todos' ? null : f.id)}
                    className={`inline-flex min-h-8 items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                      pressed
                        ? 'border-accent/60 bg-accent/15 text-fg'
                        : 'border-line bg-card text-muted hover:border-line-strong hover:text-fg'
                    }`}
                  >
                    {f.label}
                    <span
                      className={`min-w-5 rounded-md border px-1.5 text-center text-xs tabular-nums leading-5 ${
                        highlight ? 'border-amber-300/35 bg-amber-300/10 font-semibold text-amber-200' : 'border-transparent bg-raised text-muted'
                      }`}
                    >
                      {counts[f.id]}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="relative w-full @4xl:max-w-xs">
              <Search size={16} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
              <Input
                id={SEARCH_ID}
                type="search"
                value={query}
                onChange={(e) => changeQuery(e.target.value)}
                placeholder="Buscar por título, estilo ou canal"
                aria-label="Buscar projetos"
                className="w-full pl-8 pr-8 [&::-webkit-search-cancel-button]:hidden"
              />
              {query ? (
                <IconButton
                  label="Limpar busca"
                  icon={X}
                  size="sm"
                  onClick={() => {
                    changeQuery('');
                    focusSearch();
                  }}
                  className="absolute right-1 top-1/2 -translate-y-1/2"
                />
              ) : null}
            </div>
          </div>

          {styles.error || channels.error ? (
            <ErrorBox
              error={styles.error ?? channels.error}
              title={
                styles.error && channels.error
                  ? 'Não foi possível carregar os estilos e os canais'
                  : styles.error
                    ? 'Não foi possível carregar os estilos'
                    : 'Não foi possível carregar os canais'
              }
              onRetry={() => {
                if (styles.error) styles.refetch();
                if (channels.error) channels.refetch();
              }}
              className="mb-4"
            />
          ) : null}

          <p role="status" className="sr-only">
            {filtering ? `${visible.length} ${visible.length === 1 ? 'projeto encontrado' : 'projetos encontrados'}` : ''}
          </p>

          {visible.length ? (
            <ul className="grid grid-cols-1 gap-3 @2xl:grid-cols-2 @5xl:grid-cols-3">
              {visible.map((r) => (
                <li key={r.project.id} className="min-w-0">
                  <ProjectCard
                    project={r.project}
                    style={r.style}
                    stylesLoaded={styles.isSuccess}
                    stylesFailed={styles.isError}
                    channelName={r.channelName}
                    activeJob={r.activeJob}
                    failedJob={r.failedJob}
                    canceledJob={r.canceledJob}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <Empty
              icon={SearchX}
              title={terms.length ? 'Nenhum projeto com essa busca' : 'Nenhum projeto neste filtro'}
              action={
                <Button variant="secondary" onClick={showAll}>
                  Mostrar todos os projetos
                </Button>
              }
            >
              {filter === 'esperando' && !terms.length ? 'Nada esperando por você agora.' : 'Tente outro termo ou outro filtro.'}
            </Empty>
          )}
        </>
      ) : null}
    </Page>
  );
};
