import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, FileText, Film, LayoutGrid, MonitorPlay, Play, RotateCw, Settings, TriangleAlert, Upload, Wand2, X } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import { api, type Job, type ProjectDetail } from '../../../api';
import { readinessOf, useProviders } from '../../../shell/data';
import { Button, buttonClass, ErrorBox, formatRelative, ProgressBar, Stepper } from '../../../ui';
import type { ProjectTab } from '../types';
import {
  awaitingScenes,
  errorFirstLine,
  failureHint,
  failureTitle,
  gatesOf,
  isActive,
  JOB_TEXT,
  jobCost,
  nextStep,
  RETRYABLE_KINDS,
  STEP_TAB,
  USES_MEDIA_QUOTA,
  type NextAction,
  type StepId,
} from './model';
import { usePreviewPlan } from './usePreviewPlan';

type StartJob = (kind: string) => void;

/** Linha de um job em andamento: mensagem, progresso e Cancelar. */
const JobProgress: React.FC<{ job: Job }> = ({ job }) => {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api.post(`/api/jobs/${job.id}/cancel`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', job.project_id] }),
  });
  const live = useQuery({ queryKey: ['job', job.id], queryFn: () => api.get<Job>(`/api/jobs/${job.id}`), initialData: job, refetchInterval: 5_000 });
  const j = live.data ?? job;
  const kind = JOB_TEXT[j.kind] ?? 'Tarefa';
  const queued = j.status === 'queued';
  const pct = Math.round(j.progress * 100);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-fg">{queued ? `${kind}: na fila` : `${kind} em andamento`}</div>
          {j.message ? <div className="truncate text-[13px] text-muted" title={j.message}>{j.message}</div> : null}
        </div>
        <span className="text-[13px] tabular-nums text-muted">{queued ? 'Na fila' : `${pct}%`}</span>
        <Button size="sm" variant="secondary" icon={X} busy={cancel.isPending} onClick={() => cancel.mutate()}>
          Cancelar
        </Button>
      </div>
      <ProgressBar value={j.progress} indeterminate={queued || j.progress <= 0} label={`Progresso: ${kind}`} valueText={queued ? 'Na fila' : undefined} />
      <ErrorBox error={cancel.error} title="Não foi possível cancelar" />
    </div>
  );
};

/**
 * A aba aberta é a dona desta ação (mostra o botão principal com o contexto dela)? Então a faixa "Próximo passo"
 * omite o botão: uma ação, um lugar, um primário. Regra única, usada pela faixa e pelas abas.
 *  - Roteiro: gerar e aprovar o roteiro;
 *  - Cenas: aprovar as cenas (painel da curadoria) e gerar a prévia;
 *  - Prévia: gerar e aprovar a prévia;
 *  - Vídeo e publicação: ir para a publicação.
 */
export const tabOwnsAction = (action: NextAction, tab: ProjectTab): boolean => {
  switch (tab) {
    case 'script':
      return action.kind === 'generate_script' || action.kind === 'approve_script';
    case 'scenes':
      return action.kind === 'preview' || action.kind === 'approve_scenes';
    case 'preview':
      return action.kind === 'preview' || action.kind === 'approve_preview';
    case 'output':
      // A aba de vídeo tem o próprio botão de renderizar: a faixa não repete a ação aqui.
      return action.kind === 'publish' || action.kind === 'render';
    default:
      return false;
  }
};

/**
 * A aba aberta tem agora um primário próprio, de outra ação (salvar o roteiro editado, gerar a prévia depois de
 * voltar uma versão)? Então o botão da faixa fica secundário, para não haver dois âmbares na tela.
 */
const tabHasOtherPrimary = (tab: ProjectTab, scriptDrafts: number, restoreNotice: boolean) =>
  (tab === 'script' && scriptDrafts > 0) || (tab === 'versions' && restoreNotice);

/** Aviso de cota de voz/imagens esgotada (tentar agora falharia de novo), ou null. Com `voiceMatters` falso
 *  (prévia narrada pela voz local), só a cota de imagens conta. */
const useMediaQuotaNote = (voiceMatters = true): string | null => {
  const providers = useProviders();
  const voice = voiceMatters && readinessOf(providers.data, 'tts').quotaExhausted;
  const images = readinessOf(providers.data, 'image').quotaExhausted;
  if (voice && images) return 'A cota de voz e a de imagens acabaram hoje: tentar agora deve falhar de novo.';
  if (voice) return 'A cota de voz acabou hoje: tentar agora deve falhar de novo.';
  if (images) return 'A cota de imagens acabou hoje: tentar agora deve falhar de novo.';
  return null;
};

/** Falha do job mais recente, no lugar do próximo passo: motivo, como resolver, Tentar de novo e Dispensar. */
const FailureBody: React.FC<{
  job: Job;
  quotaNote: string | null;
  retryVariant: 'primary' | 'secondary';
  onRetry: () => void;
  retryBusy: boolean;
  retryDisabled: boolean;
  onDismiss: () => void;
}> = ({ job, quotaNote, retryVariant, onRetry, retryBusy, retryDisabled, onDismiss }) => {
  const message = (job.error || job.message || '').trim();
  const firstLine = errorFirstLine(job);
  const rest = firstLine ? message.slice(message.indexOf(firstLine) + firstLine.length).trim() : '';
  const canRetry = RETRYABLE_KINDS.has(job.kind);
  const cost = jobCost(job.kind);
  const quota = USES_MEDIA_QUOTA.has(job.kind) ? quotaNote : null;
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 flex-1 basis-80">
        <div className="text-xs font-semibold uppercase tracking-wider text-subtle">Próximo passo</div>
        <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
          <h2 id="proximo-passo-titulo" className="font-display text-[17px] font-semibold text-fg">
            {failureTitle(job.kind)}
          </h2>
          {job.finished_at ? <span className="text-[13px] text-subtle">{formatRelative(job.finished_at)}</span> : null}
        </div>
        <p className="mt-0.5 text-[13px] leading-snug text-muted">{failureHint(message)}</p>
        {firstLine ? <p className="mt-1 break-words text-[13px] text-red-200">{firstLine}</p> : null}
        {rest ? (
          <details className="mt-1 text-[13px] text-muted">
            <summary className="w-fit rounded-sm hover:text-fg">Detalhes técnicos</summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-2 font-mono text-xs text-muted">
              {rest}
            </pre>
          </details>
        ) : null}
      </div>
      <div className="flex flex-col items-end gap-1">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" icon={X} onClick={onDismiss}>
            Dispensar
          </Button>
          <Link to="/configuracoes" className={buttonClass({ variant: 'ghost' })}>
            <Settings size={16} aria-hidden="true" />
            Configurações
          </Link>
          {canRetry ? (
            <Button
              variant={retryVariant}
              icon={RotateCw}
              busy={retryBusy}
              disabled={retryDisabled}
              title={retryDisabled ? 'Aguarde o trabalho em andamento terminar' : undefined}
              onClick={onRetry}
            >
              Tentar de novo
            </Button>
          ) : null}
        </div>
        {canRetry && cost ? <p className="text-[13px] text-subtle">{cost}</p> : null}
        {canRetry && quota ? (
          <p className="flex items-center gap-1 text-[13px] text-amber-200">
            <TriangleAlert size={14} aria-hidden="true" className="shrink-0" />
            {quota}
          </p>
        ) : null}
      </div>
    </div>
  );
};

/** Banner "Próximo passo": etapa atual, trilha de 5 etapas e uma única ação principal (ou a falha mais recente). */
export const NextStepBanner: React.FC<{
  detail: ProjectDetail;
  tab: ProjectTab;
  goTo: (tab: ProjectTab) => void;
  scriptDrafts: number;
  restoreNotice: boolean;
  startJob: StartJob;
  startBusy: boolean;
  approveScript: () => void;
  approveScriptBusy: boolean;
  approvePreview: () => void;
  approvePreviewBusy: boolean;
  /** Falha do job mais recente que o usuário ainda não dispensou (ou null). */
  failure: Job | null;
  onRetry: () => void;
  retryBusy: boolean;
  onDismissFailure: () => void;
}> = ({
  detail,
  tab,
  goTo,
  scriptDrafts,
  restoreNotice,
  startJob,
  startBusy,
  approveScript,
  approveScriptBusy,
  approvePreview,
  approvePreviewBusy,
  failure,
  onRetry,
  retryBusy,
  onDismissFailure,
}) => {
  // Cenas esperando a conferência: o título diz quantas imagens a aprovação gera de fato (com o cache).
  const plan = usePreviewPlan(detail, awaitingScenes(detail));
  const step = nextStep(detail, { showFailure: Boolean(failure), plannedImages: plan.data?.images_missing.length });
  const active = detail.jobs.filter(isActive);
  const { action } = step;
  const toFinal = detail.project.settings.gates?.preview === false;
  // Com a parada das cenas, aprovar o roteiro ou continuar só cria as cenas (as imagens esperam a conferência).
  const stopAtScenes = gatesOf(detail).storyboard;
  const owned = tabOwnsAction(action, tab);
  const main = tabHasOtherPrimary(tab, scriptDrafts, restoreNotice) ? 'secondary' : 'primary';
  const showFailure = failure && !active.length ? failure : null;
  // Prévia com a voz local: a cota da voz de API não impede tentar de novo.
  const localPreview = detail.style?.voice.preview?.local ?? true;
  const quotaNote = useMediaQuotaNote(!(localPreview && showFailure?.kind === 'preview'));

  let primary: React.ReactNode = null;
  let secondary: React.ReactNode = null;
  let note: React.ReactNode = null;
  let usesMedia = false;
  switch (action.kind) {
    case 'generate_script':
      primary = (
        <Button variant={main} icon={Wand2} busy={startBusy} onClick={() => startJob('script')}>
          Gerar roteiro
        </Button>
      );
      break;
    case 'approve_script': {
      const blocked = scriptDrafts > 0;
      primary = (
        <Button variant={main} icon={Check} busy={approveScriptBusy} disabled={blocked} onClick={approveScript}>
          {stopAtScenes ? 'Aprovar e criar as cenas' : toFinal ? 'Aprovar e gerar o vídeo' : 'Aprovar e gerar a prévia'}
        </Button>
      );
      if (tab !== 'script') {
        secondary = (
          <Button variant="ghost" icon={FileText} onClick={() => goTo('script')}>
            Ler o roteiro
          </Button>
        );
      }
      if (blocked) note = 'Salve ou descarte as alterações do roteiro primeiro.';
      usesMedia = !stopAtScenes;
      break;
    }
    case 'continue':
      primary = (
        <Button variant={main} icon={Play} busy={startBusy} onClick={() => startJob('autopilot')}>
          {stopAtScenes ? 'Continuar: criar as cenas' : toFinal ? 'Continuar: cenas, prévia e vídeo' : 'Continuar: cenas e prévia'}
        </Button>
      );
      usesMedia = !stopAtScenes;
      break;
    case 'approve_scenes':
      // Fora da aba Cenas, só leva até lá: aprovar sem ver as cenas desfaria o sentido da parada.
      primary = (
        <Button variant={main} icon={LayoutGrid} onClick={() => goTo('scenes')}>
          Conferir as cenas
        </Button>
      );
      break;
    case 'preview':
      primary = (
        <Button variant={main} icon={MonitorPlay} busy={startBusy} onClick={() => startJob('preview')}>
          Gerar prévia
        </Button>
      );
      if (tab !== 'scenes') {
        secondary = (
          <Button variant="ghost" icon={LayoutGrid} onClick={() => goTo('scenes')}>
            Revisar cenas
          </Button>
        );
      }
      usesMedia = true;
      break;
    case 'approve_preview':
      primary = (
        <Button variant={main} icon={Check} busy={approvePreviewBusy} onClick={approvePreview}>
          Aprovar prévia e renderizar
        </Button>
      );
      if (tab !== 'preview') {
        secondary = (
          <Button variant="ghost" icon={MonitorPlay} onClick={() => goTo('preview')}>
            Abrir prévia
          </Button>
        );
      }
      usesMedia = true;
      break;
    case 'render':
      primary = (
        <Button variant={main} icon={Film} busy={startBusy} onClick={() => startJob('render')}>
          Renderizar vídeo final
        </Button>
      );
      usesMedia = true;
      break;
    case 'publish':
      primary = (
        <Button variant={main} icon={Upload} onClick={() => goTo('output')}>
          Ir para Vídeo e publicação
        </Button>
      );
      break;
    case 'done':
      primary = (
        <Link to="/agenda" className={buttonClass({ variant: 'secondary' })}>
          Abrir a Agenda
        </Link>
      );
      if (tab !== 'output') {
        secondary = (
          <Button variant="ghost" icon={Upload} onClick={() => goTo('output')}>
            Vídeo e publicação
          </Button>
        );
      }
      break;
    case 'job':
      break;
  }
  // A aba aberta é a dona da ação: ela mostra o botão (com o custo); a faixa fica só com o título e a descrição.
  if (owned) {
    primary = null;
    note = null;
  }
  const cost = !owned && primary && step.cost ? step.cost : null;
  const quota = !owned && primary && usesMedia ? quotaNote : null;

  const accent = showFailure ? 'border-l-red-400' : action.kind === 'done' ? 'border-l-emerald-400' : 'border-l-accent';

  return (
    <section aria-labelledby="proximo-passo-titulo" className={`mb-6 rounded-xl border border-l-4 border-line bg-card p-4 ${accent}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Stepper steps={step.steps} onSelect={(id) => goTo(STEP_TAB[id as StepId])} className="-ml-1.5" />
      </div>
      {showFailure ? (
        <FailureBody
          job={showFailure}
          quotaNote={quotaNote}
          // Se a aba aberta já tem o primário (ex.: Gerar prévia em Cenas), Tentar de novo fica secundário.
          retryVariant={owned || main === 'secondary' ? 'secondary' : 'primary'}
          onRetry={onRetry}
          retryBusy={retryBusy}
          retryDisabled={startBusy}
          onDismiss={onDismissFailure}
        />
      ) : (
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1 basis-60 sm:basis-80">
            <div className="text-xs font-semibold uppercase tracking-wider text-subtle">Próximo passo</div>
            <h2 id="proximo-passo-titulo" className="mt-0.5 font-display text-[17px] font-semibold text-fg">
              {step.title}
            </h2>
            {/* No celular a descrição fica em 2 linhas (o texto inteiro vai no title): o conteúdo da aba sobe. */}
            <p className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-muted sm:line-clamp-none" title={step.description}>
              {step.description}
            </p>
          </div>
          {primary || secondary ? (
            <div className="flex flex-col items-end gap-1">
              <div className="flex flex-wrap items-center justify-end gap-2">
                {secondary}
                {primary}
              </div>
              {note ? <p className="text-[13px] text-amber-200">{note}</p> : cost ? <p className="text-[13px] text-subtle">{cost}</p> : null}
              {quota ? (
                <p className="flex items-center gap-1 text-[13px] text-amber-200">
                  <TriangleAlert size={14} aria-hidden="true" className="shrink-0" />
                  {quota}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
      {active.length ? (
        <div className="mt-4 flex flex-col gap-4 border-t border-line pt-4">
          {active.map((job) => (
            <JobProgress key={job.id} job={job} />
          ))}
        </div>
      ) : null}
    </section>
  );
};
