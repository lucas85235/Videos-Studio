import { ArrowRight, CircleSlash, CircleX, Palette, TriangleAlert, Tv } from 'lucide-react';
import React, { useId } from 'react';
import { Link } from 'react-router-dom';
import { type Job, type StyleItem } from '../../api';
import { Badge, Card, formatRelative, ProgressBar, Spinner } from '../../ui';
import { failureHint, JOB_TEXT } from '../project/core/model';
import { failedStepOf, formatLabel, projectLink, statusLabel, statusMeta, type ProjectListItem } from './meta';
import { FormatGlyph, StepTrack } from './parts';

const updatedLabel = (iso: string | null) => {
  if (!iso) return null;
  const rel = formatRelative(iso);
  if (!rel) return null;
  // Data no futuro (relógio dessincronizado): "em 5 min", "amanhã" não fazem sentido para "atualizado".
  if (/^(em |amanhã|agora)/.test(rel)) return 'Atualizado agora';
  return /^(há|ontem)/.test(rel) ? `Atualizado ${rel}` : `Atualizado em ${rel}`;
};

const fullDate = (iso: string) =>
  new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });

const jobLabel = (job: Job) => JOB_TEXT[job.kind] ?? 'Tarefa';

export const ProjectCard: React.FC<{
  project: ProjectListItem;
  style: StyleItem | undefined;
  /** Os estilos já carregaram (sem isso, um estilo ausente ainda não quer dizer "removido"). */
  stylesLoaded: boolean;
  /** A lista de estilos não carregou: não dá para saber o estilo nem o formato. */
  stylesFailed?: boolean;
  channelName: string | null;
  activeJob: Job | undefined;
  failedJob: Job | undefined;
  canceledJob?: Job;
}> = ({ project, style, stylesLoaded, stylesFailed, channelName, activeJob, failedJob, canceledJob }) => {
  const meta = statusMeta(project.status);
  const updated = updatedLabel(project.updated_at);
  const format = style?.style.format;
  const pct = activeJob ? Math.round(activeJob.progress * 100) : 0;
  const titleId = useId();
  const statusId = useId();

  // Com falha, o selo e o texto dizem o problema (não a etapa salva, que parece progresso).
  const failure = !activeJob && failedJob ? { step: failedStepOf(failedJob, project.status), hint: failureHint(failedJob.error) } : null;

  const styleText = style ? style.style.name : stylesFailed ? 'Estilo indisponível' : stylesLoaded ? 'Estilo removido' : '…';

  return (
    // Nome do link = título; descrição = etapa e próximo passo (não o cartão inteiro).
    <Link to={projectLink(project)} aria-labelledby={titleId} aria-describedby={statusId} className="block h-full rounded-xl">
      <Card interactive className="flex h-full flex-col gap-3">
        <div className="flex items-start justify-between gap-2">
          {format ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted">
              <FormatGlyph aspect={format.aspect} />
              {formatLabel(format)}
            </span>
          ) : (
            <span className="text-xs text-subtle">{stylesLoaded && !stylesFailed ? 'Formato desconhecido' : ''}</span>
          )}
          {failure ? (
            <Badge tone="bad" className="shrink-0">
              Falhou
            </Badge>
          ) : (
            <Badge tone={meta.tone} className="shrink-0">
              {statusLabel(project.status)}
            </Badge>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <h2 id={titleId} className="line-clamp-2 font-display text-[15px] font-semibold leading-snug text-fg [overflow-wrap:anywhere]">
            {project.title}
          </h2>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-subtle">
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Palette size={14} aria-hidden="true" className="shrink-0" />
              <span className="sr-only">Estilo: </span>
              <span className="truncate">{styleText}</span>
            </span>
            {channelName ? (
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <Tv size={14} aria-hidden="true" className="shrink-0" />
                <span className="sr-only">Canal: </span>
                <span className="truncate">{channelName}</span>
              </span>
            ) : null}
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2.5 border-t border-line pt-3">
          <span id={statusId} className="sr-only">
            {statusLabel(project.status)}.{' '}
            {activeJob
              ? `${jobLabel(activeJob)} ${activeJob.status === 'queued' ? 'na fila' : `em andamento, ${pct}%`}.`
              : failedJob
                ? `${jobLabel(failedJob)} falhou. ${failure?.hint ?? ''}`
                : canceledJob
                  ? `Tarefa cancelada: ${jobLabel(canceledJob)}.`
                  : `Próximo passo: ${meta.next}.`}
          </span>
          {activeJob ? (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-2 text-[13px]">
                <span className="flex min-w-0 items-center gap-1.5 text-fg">
                  <Spinner className="text-accent" />
                  <span className="truncate">
                    {jobLabel(activeJob)}
                    {activeJob.status === 'queued' ? ': na fila' : activeJob.message ? `: ${activeJob.message}` : ''}
                  </span>
                </span>
                {activeJob.status === 'running' ? <span className="shrink-0 tabular-nums text-muted">{pct}%</span> : null}
              </div>
              <ProgressBar
                value={activeJob.progress}
                indeterminate={activeJob.status === 'queued'}
                label={`${jobLabel(activeJob)} de ${project.title}`}
                valueText={activeJob.status === 'queued' ? 'Na fila' : undefined}
              />
            </div>
          ) : failedJob ? (
            <div aria-hidden="true" className="flex items-start gap-1.5 text-[13px] text-red-300">
              <CircleX size={14} className="mt-0.5 shrink-0" />
              <span>{failure?.hint}</span>
            </div>
          ) : canceledJob ? (
            <div aria-hidden="true" className="flex items-start gap-1.5 text-[13px] text-muted">
              <CircleSlash size={14} className="mt-0.5 shrink-0" />
              <span>Tarefa cancelada: {jobLabel(canceledJob)}. Abra o projeto para continuar.</span>
            </div>
          ) : (
            <div aria-hidden="true" className={`flex items-start gap-1.5 text-[13px] ${meta.waiting ? 'font-medium text-amber-200' : 'text-muted'}`}>
              {meta.waiting ? <TriangleAlert size={14} className="mt-0.5 shrink-0" /> : <ArrowRight size={14} className="mt-0.5 shrink-0" />}
              <span>{meta.next}</span>
            </div>
          )}
          <StepTrack step={meta.step} failed={failure?.step ?? null} />
          {updated && project.updated_at ? (
            <time dateTime={project.updated_at} title={fullDate(project.updated_at)} className="text-xs text-subtle">
              {updated}
            </time>
          ) : null}
        </div>
      </Card>
    </Link>
  );
};
