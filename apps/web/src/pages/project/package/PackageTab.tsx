// Aba "Título e thumb" (o pacote): candidatos de título + thumb gerados antes do roteiro, a prévia do feed ao lado
// dos concorrentes, o editor, a escolha da capa e dos aprovados para o A/B, o aviso de material oficial e o switch
// do avatar do projeto. Aprovar trava o par escolhido e segue para o roteiro (que cumpre a promessa da thumb).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ImagePlus, Lock, LockOpen, Plus, RefreshCw, ShieldAlert, Sparkles, Type, Wand2 } from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, fileUrl, type Channel, type PackageCandidate, type ProjectDetail } from '../../../api';
import {
  DEFAULT_EDITORIAL_NOTICE,
  effectiveEditorial,
  EMPTY_PACKAGE,
  normalizePackageState,
  normalizeReference,
  PACKAGE_JOB_KINDS,
  packageApi,
  packageQueryKey,
  type CandidatePatch,
  type PackageReference,
} from '../../../apiPackage';
import { presenterApi, presenterQueryKey } from '../../../apiPresenter';
import { researchApi } from '../../../apiResearch';
import { Button, Empty, ErrorBox, Section, useConfirm, useToast } from '../../../ui';
import { awaitingPackage, DOC_BUSY_REASON, docJobRunning, estimateSeconds, isActive } from '../core/model';
import type { TabNavigation } from '../types';
import { CandidateEditor, type EditorPose } from './CandidateEditor';
import { CandidateGrid } from './CandidateGrid';
import { FeedPreview } from './FeedPreview';
import { EditorialSwitch } from './EditorialSwitch';
import { avatarOnInThumb, DEFAULT_OVERRIDE, PresenterSwitch } from './PresenterSwitch';

/** Servidor sem as rotas do pacote (versão antiga): a tela mostra o que está no documento e avisa. */
const routeMissing = (error: unknown) => error instanceof ApiError && (error.status === 404 || error.status === 405);

/** Outliers da última pesquisa do canal (vídeos longos, maior multiplicador primeiro), quando o pacote não traz. */
const useResearchReferences = (channelId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['package-research-refs', channelId],
    enabled: Boolean(channelId) && enabled,
    staleTime: 10 * 60_000,
    retry: false,
    queryFn: async (): Promise<PackageReference[]> => {
      const state = await researchApi.state(channelId!);
      const runId = state.last_run?.id;
      if (!runId) return [];
      const run = await researchApi.getRun(runId);
      const competitors = (run.signals.competitors?.items ?? []).filter((v) => v.format !== 'short').sort((a, b) => (b.mult ?? 0) - (a.mult ?? 0));
      const keywords = (run.signals.keywords?.items ?? []).filter((v) => v.format !== 'short' && v.outlier).sort((a, b) => b.ratio - a.ratio);
      const seen = new Set<string>();
      return [...competitors, ...keywords]
        .map((v) => normalizeReference(v))
        .filter((r): r is PackageReference => r !== null && !seen.has(r.id) && Boolean(seen.add(r.id)))
        .slice(0, 6);
    },
  });

export const PackageTab: React.FC<{ detail: ProjectDetail; busy: boolean } & TabNavigation> = ({ detail, busy, goTo }) => {
  const { doc, project, style } = detail;
  const projectId = project.id;
  const channelId = doc.channel_id ?? project.channel_id;
  const isShort = style?.format.type === 'short';
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();

  const docPackage = doc.package ?? EMPTY_PACKAGE;
  const packageJobs = detail.jobs.filter((j) => isActive(j) && (PACKAGE_JOB_KINDS.has(j.kind) || j.kind === 'autopilot'));
  const packageJobRunning = packageJobs.length > 0;
  const generating = packageJobs.some((j) => j.kind === 'package');
  const renderingIds = new Set(packageJobs.flatMap((j) => (Array.isArray(j.payload?.candidate_ids) ? (j.payload.candidate_ids as string[]) : [])));
  const renderingAll = generating || packageJobs.some((j) => j.kind === 'package_thumbs' && !Array.isArray(j.payload?.candidate_ids));

  const pkgQuery = useQuery({
    queryKey: packageQueryKey(projectId),
    queryFn: () => packageApi.get(projectId, docPackage, channelId),
    retry: (count, error) => !routeMissing(error) && count < 2,
    refetchInterval: packageJobRunning ? 3_000 : false,
  });
  // Quando o job termina, relê o pacote (as prévias e as notas chegam no fim).
  const wasRunning = useRef(packageJobRunning);
  useEffect(() => {
    if (wasRunning.current && !packageJobRunning) void client.invalidateQueries({ queryKey: packageQueryKey(projectId) });
    wasRunning.current = packageJobRunning;
  }, [packageJobRunning, client, projectId]);

  const missingRoutes = routeMissing(pkgQuery.error);
  // O documento do projeto é a fonte quando a rota falta (e enquanto o GET não volta).
  const state = pkgQuery.data ?? normalizePackageState(docPackage, docPackage, channelId);
  const pkg = state.package;
  const { candidates, locked } = pkg;

  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels'), enabled: Boolean(channelId) });
  const channel = channels.data?.find((c) => c.id === channelId) ?? null;
  const presenter = useQuery({
    queryKey: presenterQueryKey(channelId ?? '-'),
    queryFn: () => presenterApi.get(channelId!),
    enabled: Boolean(channelId),
    retry: false,
  });
  const researchRefs = useResearchReferences(channelId, !pkgQuery.isLoading && state.references.length === 0);
  const references = state.references.length ? state.references : (researchRefs.data ?? []);
  const referencesSource = state.references.length ? 'package' : researchRefs.data?.length ? 'research' : null;

  const allowEditorial = effectiveEditorial(state, style, channel);
  const override = doc.presenter ?? DEFAULT_OVERRIDE;
  const channelPresenter = presenter.data?.presenter ?? null;
  const styleAllowsAvatar = (style?.thumbnail.presenter ?? true) && style?.edit.presenter !== 'off';
  // Avatar na thumb: o servidor já aplica canal + projeto + estilo (GET do pacote); sem ele, a tela calcula pelo canal.
  const poseImage = (id: string) => {
    const pose = channelPresenter?.poses.find((p) => p.id === id);
    return pose ? fileUrl(pose.render_file ?? pose.file) : null;
  };
  const avatarThumb = state.presenter
    ? state.presenter.enabled && state.presenter.poses.length > 0
    : avatarOnInThumb(channelPresenter, override, styleAllowsAvatar) && !(isShort && !channelPresenter?.use_in_shorts && override.thumb == null);
  const avatarPoses: EditorPose[] = state.presenter
    ? state.presenter.poses.map((p) => ({ id: p.id, src: poseImage(p.id), emotions: p.emotions }))
    : (channelPresenter?.poses ?? [])
        .filter((p) => p.thumb_ok && !override.poses_off.includes(p.id))
        .map((p) => ({ id: p.id, src: fileUrl(p.render_file ?? p.file), emotions: p.emotions }));

  // Escolha local (capa e A/B), gravada no servidor a cada clique.
  const serverApproved = useMemo(() => new Set(candidates.filter((c) => c.approved).map((c) => c.id)), [candidates]);
  const [choice, setChoice] = useState<{ chosen: string | null; approved: Set<string> } | null>(null);
  const chosenId = choice?.chosen ?? pkg.chosen_id;
  const approvedIds = choice?.approved ?? serverApproved;
  const serverKey = `${pkg.chosen_id}|${[...serverApproved].sort().join(',')}`;
  useEffect(() => setChoice(null), [serverKey]);

  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = candidates.find((c) => c.id === editingId) ?? null;

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: packageQueryKey(projectId) });
    void client.invalidateQueries({ queryKey: ['project', projectId] });
  };
  const onError = (title: string) => (error: unknown) => toast({ tone: 'bad', title, description: error instanceof Error ? error.message : undefined });

  const generate = useMutation({
    mutationFn: (input: { n_titles?: number; n_thumbs?: number; append?: boolean }) => packageApi.generate(projectId, input),
    onSuccess: invalidate,
    onError: onError('Não foi possível gerar o pacote'),
  });
  const render = useMutation({
    mutationFn: (ids?: string[]) => packageApi.renderThumbs(projectId, ids),
    onSuccess: invalidate,
    onError: onError('Não foi possível montar as thumbs'),
  });
  const saveChoice = useMutation({
    mutationFn: (next: { chosen: string | null; approved: Set<string> }) => packageApi.setChoice(projectId, next.chosen, [...next.approved]),
    onSuccess: invalidate,
    onError: (error) => {
      setChoice(null);
      onError('Não foi possível salvar a escolha')(error);
    },
  });
  const saveCandidate = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: CandidatePatch }) => packageApi.putCandidate(projectId, id, patch),
    onSuccess: () => {
      setEditingId(null);
      invalidate();
      toast({ tone: 'good', title: 'Candidato salvo', description: 'A thumb dele está sendo montada de novo.' });
    },
  });
  const createCandidate = useMutation({
    mutationFn: (body: CandidatePatch & { from_id?: string }) => packageApi.createCandidate(projectId, body),
    onSuccess: (id) => {
      invalidate();
      if (id) setEditingId(id);
    },
    onError: onError('Não foi possível criar o candidato'),
  });
  const removeCandidate = useMutation({
    mutationFn: (id: string) => packageApi.deleteCandidate(projectId, id),
    onSuccess: invalidate,
    onError: onError('Não foi possível apagar o candidato'),
  });
  const approve = useMutation({
    mutationFn: () => packageApi.approve(projectId),
    onSuccess: () => {
      invalidate();
      toast({ tone: 'good', title: 'Pacote aprovado', description: 'O roteiro vai ser escrito para cumprir a promessa da thumb.' });
      goTo?.('script');
    },
    onError: onError('Não foi possível aprovar o pacote'),
  });
  const unlock = useMutation({
    mutationFn: () => packageApi.unlock(projectId),
    onSuccess: invalidate,
    onError: onError('Não foi possível destravar o pacote'),
  });

  const docBusy = docJobRunning(detail);
  const anyPending = generate.isPending || render.isPending || approve.isPending || unlock.isPending || createCandidate.isPending || removeCandidate.isPending;
  const disabledReason = missingRoutes
    ? 'O servidor ainda não tem as rotas do pacote: atualize o Vulto.'
    : docBusy
      ? DOC_BUSY_REASON
      : packageJobRunning
        ? 'Aguarde: os candidatos estão sendo gerados ou montados.'
        : null;
  const blocked = Boolean(disabledReason) || busy;

  const updateChoice = (next: { chosen: string | null; approved: Set<string> }) => {
    setChoice(next);
    saveChoice.mutate(next);
  };
  const choose = (id: string) => updateChoice({ chosen: id, approved: new Set(approvedIds) });
  const toggleApproved = (id: string, on: boolean) => {
    const approved = new Set(approvedIds);
    if (on) approved.add(id);
    else approved.delete(id);
    updateChoice({ chosen: chosenId, approved });
  };
  const askDelete = async (c: PackageCandidate) => {
    const ok = await confirm({
      title: 'Apagar este candidato?',
      description: `“${c.title || c.thumb_text || 'sem título'}” some do pacote${c.id === chosenId ? ' e deixa de ser a capa escolhida' : ''}.`,
      confirmLabel: 'Apagar candidato',
      tone: 'danger',
    });
    if (ok) {
      if (editingId === c.id) setEditingId(null);
      removeCandidate.mutate(c.id);
    }
  };
  const askApprove = async () => {
    if (doc.segments.length) {
      const ok = await confirm({
        title: 'Aprovar o pacote com o roteiro já escrito?',
        description: 'O roteiro atual não é reescrito: o gancho foi escrito antes deste título e desta thumb. Confira se os primeiros segundos cumprem a promessa.',
        confirmLabel: 'Aprovar o pacote',
      });
      if (!ok) return;
    }
    approve.mutate();
  };
  const askUnlock = async () => {
    const ok = await confirm({
      title: 'Destravar o título e a thumb?',
      description: doc.segments.length
        ? 'O gancho do roteiro foi escrito para o par aprovado. Se trocar o título ou a thumb, confira se o começo do vídeo ainda cumpre a promessa.'
        : 'Você pode trocar a escolha e aprovar de novo.',
      confirmLabel: 'Destravar',
    });
    if (ok) unlock.mutate();
  };

  const titleN = state.title_candidates ?? style?.thumbnail.title_candidates ?? 5;
  const thumbN = isShort ? 1 : (state.thumb_candidates ?? style?.thumbnail.thumb_candidates ?? 3);
  const durationS =
    detail.outputs.final?.langs?.[doc.primary_language]?.duration ||
    estimateSeconds(style, doc.primary_language, doc.segments.map((s) => s.text[doc.primary_language] ?? '')) ||
    style?.format.duration.target_s ||
    null;
  const waiting = awaitingPackage(detail);
  const chosen = candidates.find((c) => c.id === chosenId) ?? null;

  const editorialNotice = allowEditorial ? (
    <div role="note" className="flex gap-3 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-[13px] text-amber-100">
      <ShieldAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
      <p>
        <strong className="font-semibold">Material oficial liberado neste canal.</strong> {state.editorial_notice || DEFAULT_EDITORIAL_NOTICE}
      </p>
    </div>
  ) : null;

  const header = (
    <div className="flex flex-col gap-3">
      {missingRoutes ? (
        <div role="note" className="rounded-lg border border-line-strong bg-raised p-3 text-[13px] text-muted">
          O servidor ainda não tem a etapa de título e thumb. A aba mostra o que está salvo no projeto; gerar, editar e aprovar ficam
          disponíveis depois de atualizar o Vulto.
        </div>
      ) : null}
      {editorialNotice}
      {state.warnings.length ? (
        <ul className="flex flex-col gap-1 rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-[13px] text-amber-100">
          {state.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {channelId ? (
        <div className="flex flex-col gap-3 rounded-lg border border-line bg-card p-3">
          <EditorialSwitch projectId={projectId} channelId={channelId} allowed={allowEditorial} disabled={busy || docBusy} />
          <PresenterSwitch
            projectId={projectId}
            channel={channelPresenter}
            channelLoading={presenter.isLoading}
            value={override}
            isShort={isShort}
            disabled={busy || docBusy}
          />
        </div>
      ) : null}
    </div>
  );

  if (!candidates.length) {
    return (
      <div className="@container flex flex-col gap-4">
        {header}
        <Empty
          icon={Sparkles}
          title={waiting ? 'Comece pelo título e pela thumb' : 'Título e thumb (opcional)'}
          action={
            <Button
              variant="primary"
              icon={Wand2}
              busy={generate.isPending || generating}
              disabled={blocked}
              title={disabledReason ?? undefined}
              onClick={() => generate.mutate({})}
            >
              {generating ? 'Gerando o pacote…' : 'Gerar pacote'}
            </Button>
          }
        >
          <p>
            {isShort
              ? 'No Short o pacote propõe só o título e o texto da capa (1 thumb vertical). Não trava o fluxo.'
              : `O Claude propõe ${titleN} títulos e ${thumbN} thumbs a partir do tema e dos vídeos que mais performam na pesquisa do canal. As thumbs são montadas no computador, sem cota de imagens.`}
          </p>
          {doc.segments.length ? (
            <p className="mt-2 text-subtle">O roteiro já existe: gerar o pacote não o reescreve (o gancho foi escrito antes).</p>
          ) : null}
          {disabledReason ? <p className="mt-2 text-subtle">{disabledReason}</p> : null}
        </Empty>
        <ErrorBox error={pkgQuery.error && !missingRoutes ? pkgQuery.error : null} title="Não foi possível carregar o pacote" onRetry={() => pkgQuery.refetch()} />
      </div>
    );
  }

  return (
    <div className="@container flex flex-col gap-5">
      {header}
      <ErrorBox error={pkgQuery.error && !missingRoutes ? pkgQuery.error : null} title="Não foi possível atualizar o pacote" onRetry={() => pkgQuery.refetch()} />

      <Section
        icon={Type}
        title={locked ? 'Título e thumb aprovados' : 'Candidatos'}
        description={
          locked
            ? 'O roteiro e a descrição usam este título e este texto. Destrave para trocar.'
            : 'Escolha a capa (o vídeo sai com ela) e marque os que entram no teste A/B da publicação.'
        }
        actions={
          locked ? (
            <Button size="sm" icon={LockOpen} busy={unlock.isPending} disabled={blocked} onClick={() => void askUnlock()}>
              Destravar
            </Button>
          ) : (
            <>
              <Button size="sm" variant="ghost" icon={Type} disabled={blocked} busy={generate.isPending && generate.variables?.n_thumbs === 1} onClick={() => generate.mutate({ n_titles: titleN, n_thumbs: 1, append: true })}>
                Gerar mais títulos
              </Button>
              {isShort ? null : (
                <Button size="sm" variant="ghost" icon={ImagePlus} disabled={blocked} busy={generate.isPending && generate.variables?.n_titles === 1} onClick={() => generate.mutate({ n_titles: 1, n_thumbs: thumbN, append: true })}>
                  Gerar mais thumbs
                </Button>
              )}
              <Button size="sm" variant="ghost" icon={RefreshCw} disabled={blocked} busy={render.isPending} onClick={() => render.mutate(undefined)}>
                Montar as thumbs de novo
              </Button>
              <Button size="sm" variant="ghost" icon={Plus} disabled={blocked} busy={createCandidate.isPending} onClick={() => createCandidate.mutate({ title: doc.title, thumb_text: '' })}>
                Novo candidato
              </Button>
            </>
          )
        }
      >
        <CandidateGrid
          candidates={candidates}
          chosenId={chosenId}
          approvedIds={approvedIds}
          editingId={editingId}
          locked={locked}
          rendering={renderingIds}
          renderingAll={renderingAll}
          isShort={isShort}
          disabled={blocked || anyPending}
          onChoose={choose}
          onApprove={toggleApproved}
          onEdit={(id) => setEditingId(id)}
          onDuplicate={(id) => createCandidate.mutate({ from_id: id })}
          onDelete={(c) => void askDelete(c)}
        />
        {locked ? (
          <p className="inline-flex items-center gap-1.5 text-[13px] text-muted">
            <Lock size={14} aria-hidden="true" /> Aprovado{chosen ? `: “${chosen.title}”` : ''}. {approvedIds.size ? `${approvedIds.size + (chosen && !approvedIds.has(chosen.id) ? 1 : 0)} no A/B.` : ''}
          </p>
        ) : (
          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-3">
            <span className="mr-auto text-[13px] text-muted">
              {chosen ? `Capa: “${chosen.title}”.` : 'Escolha a capa (o rádio “Capa”) para aprovar.'}
              {approvedIds.size ? ` ${[...approvedIds].filter((id) => id !== chosenId).length} outro(s) no A/B.` : ''}
            </span>
            <Button variant="primary" icon={Check} busy={approve.isPending} disabled={blocked || !chosenId || saveChoice.isPending} title={disabledReason ?? undefined} onClick={() => void askApprove()}>
              {doc.segments.length ? 'Aprovar o pacote' : 'Aprovar e escrever roteiro'}
            </Button>
          </div>
        )}
      </Section>

      {editing && !locked ? (
        <Section title={`Editar o candidato ${candidates.indexOf(editing) + 1}`} description="Salvar marca o candidato como editado e monta a thumb dele de novo (sem cota de imagens).">
          <CandidateEditor
            key={editing.id}
            candidate={editing}
            isShort={isShort}
            templateOptions={state.templates}
            allowEditorial={allowEditorial}
            avatar={{ on: avatarThumb, poses: avatarPoses }}
            library={state.assets}
            libraryLoading={pkgQuery.isLoading}
            canUpload={Boolean(channelId) && !missingRoutes}
            saving={saveCandidate.isPending}
            saveError={saveCandidate.error}
            disabledReason={disabledReason}
            onUpload={async (file, kind) => {
              const item = await packageApi.uploadThumbkit(channelId!, file, kind);
              void client.invalidateQueries({ queryKey: packageQueryKey(projectId) });
              return item;
            }}
            onSave={(patch) => saveCandidate.mutate({ id: editing.id, patch })}
            onClose={() => {
              saveCandidate.reset();
              setEditingId(null);
            }}
          />
        </Section>
      ) : null}

      <Section title="Como fica no feed" description="168, 246 e 360 px: os tamanhos em que a thumb é vista, ao lado das que mais performam no nicho.">
        <FeedPreview
          candidates={candidates}
          chosenId={chosenId}
          references={references}
          referencesSource={referencesSource}
          referencesLoading={researchRefs.isLoading && researchRefs.fetchStatus !== 'idle'}
          channelName={channel?.name ?? 'Seu canal'}
          durationS={durationS}
          isShort={isShort}
        />
      </Section>
    </div>
  );
};
