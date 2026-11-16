import { Player } from '@remotion/player';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type TimelineProps, VideoTimeline } from '@vstudio/renderer';
import {
  ArrowUpCircle,
  Clapperboard,
  Columns2,
  ExternalLink,
  GitBranch,
  GitMerge,
  Link2,
  MonitorPlay,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Audio } from 'remotion';
import { api, ApiError, fileUrl, type AbBranch, type AbCompare, type AbOverview, type AbReading, type EditProposal, type ProjectDetail } from '../../api';
import { langLabel } from '../../labels';
import { Badge, Button, Checkbox, Empty, ErrorBox, Field, Input, PLAYBACK_RATES, Section, Select, Skeleton, Textarea, useConfirm, useToast } from '../../ui';
import { describeOp } from './delivery/editOps';

/* Ramos A/B de versões ("versão B do gancho"): criar, editar por instrução, comparar lado a lado, gerar e escolher. */

type Timeline = TimelineProps & { mix: string; duration: number; version: number };

const STATUS_LABEL: Record<AbBranch['status'], string> = {
  principal: 'Principal',
  aberto: 'Aberto',
  mesclado: 'Mesclado na A',
  promovido: 'Virou a principal',
  descartado: 'Descartado',
};

const SEGMENT_STATUS: Record<string, { label: string; tone: 'info' | 'good' | 'bad' | 'neutral' }> = {
  mudou: { label: 'Mudou', tone: 'info' },
  novo: { label: 'Só na B', tone: 'good' },
  saiu: { label: 'Só na A', tone: 'bad' },
  igual: { label: 'Igual', tone: 'neutral' },
};

const abKey = (projectId: string) => ['ab', projectId];

/** Chave que muda quando um job do projeto termina (a lista de ramos recarrega com a prévia nova). */
const jobsToken = (detail: ProjectDetail) => detail.jobs.map((j) => `${j.id}:${j.status}`).join(',');

const useAb = (detail: ProjectDetail) =>
  useQuery({
    queryKey: [...abKey(detail.doc.id), jobsToken(detail), detail.project.current_version],
    queryFn: () => api.get<AbOverview>(`/api/projects/${detail.doc.id}/ab`),
  });

const useRefresh = (projectId: string) => {
  const client = useQueryClient();
  return async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['project', projectId] }),
      client.invalidateQueries({ queryKey: abKey(projectId) }),
      client.invalidateQueries({ queryKey: ['ab-compare', projectId] }),
      client.invalidateQueries({ queryKey: ['ab-timeline', projectId] }),
    ]);
  };
};

/** Prévia gerada é da cabeça atual da versão? */
const previewState = (branch: AbBranch): 'ok' | 'velha' | 'nenhuma' => {
  const p = branch.last_preview;
  if (!p || !p.langs || !Object.keys(p.langs).length) return 'nenhuma';
  return p.version === branch.head ? 'ok' : 'velha';
};

const finalReady = (branch: AbBranch, lang: string) => {
  const f = branch.last_final;
  return Boolean(f?.langs?.[lang]?.video) && f?.version === branch.head;
};

/* ------------------------------------------------------------------------------------------ */
/* Seção principal                                                                            */
/* ------------------------------------------------------------------------------------------ */

export const AbBranches: React.FC<{ detail: ProjectDetail; busy: boolean; createFrom: number | null; onCreated: () => void }> = ({
  detail,
  busy,
  createFrom,
  onCreated,
}) => {
  const projectId = detail.doc.id;
  const ab = useAb(detail);
  const refresh = useRefresh(projectId);
  const { toast } = useToast();
  const [note, setNote] = useState('');
  const [creating, setCreating] = useState(false);
  const from = createFrom ?? detail.project.current_version;
  const formOpen = creating || createFrom !== null;

  const create = useMutation({
    mutationFn: () => api.post<AbBranch>(`/api/projects/${projectId}/ab/branches`, { from_number: from, note: note.trim() }),
    onSuccess: async (branch) => {
      setNote('');
      setCreating(false);
      onCreated();
      await refresh();
      toast({ tone: 'good', title: `Versão ${branch.letter} criada`, description: `Ela começa igual à v${branch.base}. A principal não muda.` });
    },
  });

  const open = ab.data?.branches.filter((b) => b.status === 'aberto') ?? [];
  const closed = ab.data?.branches.filter((b) => b.status !== 'aberto') ?? [];

  return (
    <Section
      title="Versões A/B"
      icon={GitBranch}
      description="Teste outra versão (um gancho diferente, outro título) sem mexer na principal. Compare lado a lado, gere as duas e escolha a vencedora."
      actions={
        formOpen ? null : (
          <Button size="sm" icon={GitBranch} onClick={() => setCreating(true)} disabled={!detail.doc.segments.length}>
            Criar versão B
          </Button>
        )
      }
    >
      {formOpen ? (
        <form
          className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label={`Nova versão a partir da v${from}`} hint="Diga o que vai testar, para lembrar depois (opcional).">
            <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Ex.: gancho com pergunta" autoFocus />
          </Field>
          <ErrorBox error={create.error} title="Não foi possível criar a versão" />
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="primary" icon={GitBranch} busy={create.isPending}>
              Criar
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setCreating(false);
                onCreated();
              }}
            >
              Cancelar
            </Button>
          </div>
        </form>
      ) : null}

      <ErrorBox error={ab.error} title="Não foi possível carregar as versões" onRetry={() => ab.refetch()} />
      {ab.isLoading ? <Skeleton className="h-24 w-full" /> : null}
      {ab.data && !open.length && !closed.length && !formOpen ? (
        <p className="text-[13px] text-subtle">Nenhuma versão B ainda. Você também pode criar uma a partir de qualquer versão do histórico.</p>
      ) : null}
      {ab.data
        ? open.map((b) => (
            <BranchCard
              key={b.name}
              detail={detail}
              main={ab.data.main}
              branch={b}
              reading={ab.data.published.find((r) => r.branch === b.name) ?? null}
              busy={busy}
            />
          ))
        : null}
      {closed.length ? (
        <details className="text-[13px] text-muted">
          <summary className="cursor-pointer select-none">Versões fechadas ({closed.length})</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {closed.map((b) => (
              <li key={b.name} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-fg">Versão {b.letter}</span>
                {b.note ? <span>({b.note})</span> : null}
                <Badge tone={b.status === 'descartado' ? 'neutral' : 'good'}>{STATUS_LABEL[b.status]}</Badge>
                <span className="text-subtle">v{b.base} → v{b.head}</span>
                {ab.data?.published.find((r) => r.branch === b.name) ? <ReadingLine reading={ab.data.published.find((r) => r.branch === b.name)!} /> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Section>
  );
};

/* ------------------------------------------------------------------------------------------ */
/* Cartão de um ramo aberto                                                                   */
/* ------------------------------------------------------------------------------------------ */

const BranchCard: React.FC<{ detail: ProjectDetail; main: AbBranch; branch: AbBranch; reading: AbReading | null; busy: boolean }> = ({
  detail,
  main,
  branch,
  reading,
  busy,
}) => {
  const projectId = detail.doc.id;
  const refresh = useRefresh(projectId);
  const confirm = useConfirm();
  const { toast } = useToast();
  const [comparing, setComparing] = useState(false);
  const [lang, setLang] = useState(detail.doc.primary_language);
  const base = `/api/projects/${projectId}/ab`;
  const L = branch.letter;

  const build = useMutation({
    mutationFn: (mode: 'preview' | 'final') => api.post(`${base}/build`, { branches: ['main', branch.name], mode }),
    onSuccess: async (_r, mode) => {
      await refresh();
      toast({ tone: 'info', title: mode === 'final' ? `Renderizando A e ${L}` : `Gerando a prévia de A e ${L}`, description: 'Acompanhe no painel de tarefas.' });
    },
  });
  const promote = useMutation({
    mutationFn: () => api.post<{ version: number }>(`${base}/${branch.name}/promote`),
    onSuccess: async (r) => {
      await refresh();
      toast({ tone: 'good', title: `A versão ${L} virou a principal (v${r.version})`, description: 'A principal antiga continua no histórico.' });
    },
  });
  const discard = useMutation({
    mutationFn: () => api.post(`${base}/${branch.name}/discard`),
    onSuccess: refresh,
  });
  const publish = useMutation({
    mutationFn: () => api.post(`${base}/${branch.name}/publish/youtube`, { lang, privacy: 'private' }),
    onSuccess: async () => {
      await refresh();
      toast({ tone: 'info', title: `Enviando a versão ${L} ao YouTube (privado)`, description: 'Quando A e B estiverem no YouTube, o experimento abre em Desempenho.' });
    },
  });

  const pState = previewState(branch);
  const mainState = previewState(main);
  const bFinal = finalReady(branch, lang);
  const disabledTitle = busy ? 'Aguarde o trabalho em andamento terminar.' : undefined;

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-line bg-canvas p-3" aria-label={`Versão ${L}`}>
      <header className="flex flex-wrap items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-sky-400/15 font-display text-sm font-semibold text-sky-300" aria-hidden="true">
          {L}
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-fg">
            Versão {L}
            {branch.note ? <span className="font-normal text-muted"> · {branch.note}</span> : null}
          </div>
          <div className="text-[13px] text-subtle">
            Saiu da v{branch.base}; está na v{branch.head}. A principal (A) está na v{main.head}.
          </div>
        </div>
        <Badge tone={pState === 'ok' ? 'good' : pState === 'velha' ? 'warn' : 'neutral'}>
          {pState === 'ok' ? 'Prévia em dia' : pState === 'velha' ? 'Prévia desatualizada' : 'Sem prévia'}
        </Badge>
      </header>

      <InstructionBox detail={detail} branch={branch} busy={busy} />

      <div className="flex flex-wrap gap-2">
        <Button size="sm" icon={Columns2} variant={comparing ? 'primary' : 'secondary'} aria-pressed={comparing} onClick={() => setComparing((v) => !v)}>
          Comparar A × {L}
        </Button>
        <Button
          size="sm"
          icon={MonitorPlay}
          busy={build.isPending && build.variables === 'preview'}
          disabled={busy || build.isPending}
          title={disabledTitle}
          onClick={() => build.mutate('preview')}
        >
          {pState === 'ok' && mainState === 'ok' ? `Gerar prévia de A e ${L} de novo` : `Gerar prévia de A e ${L}`}
        </Button>
        <Button
          size="sm"
          icon={Clapperboard}
          busy={build.isPending && build.variables === 'final'}
          disabled={busy || build.isPending}
          title={disabledTitle}
          onClick={async () => {
            const ok = await confirm({
              title: `Renderizar A e ${L}?`,
              description: 'Grava o vídeo final das duas versões com a voz final (usa a cota de voz só no que ainda não foi gravado; o que é igual nas duas é reaproveitado).',
              confirmLabel: 'Renderizar as duas',
            });
            if (ok) build.mutate('final');
          }}
        >
          Renderizar A e {L}
        </Button>
      </div>
      <ErrorBox error={build.error} title="Não foi possível gerar as versões" onClose={() => build.reset()} />

      {comparing ? <ComparePanel detail={detail} branch={branch} lang={lang} setLang={setLang} busy={busy} /> : null}

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-subtle">Publicar as duas e medir</h4>
        <p className="text-[13px] text-muted">
          Publique a A pela aba Vídeo e publicação e a {L} aqui. Com as duas no YouTube, o Vulto abre um experimento em{' '}
          <Link to="/desempenho" className="text-accent underline-offset-2 hover:underline">
            Desempenho
          </Link>{' '}
          com a variável “versão”.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            icon={Upload}
            busy={publish.isPending}
            disabled={!bFinal || busy || publish.isPending}
            title={bFinal ? disabledTitle : `Renderize a versão ${L} primeiro.`}
            onClick={() => publish.mutate()}
          >
            Enviar {L} ao YouTube (privado)
          </Button>
          <LinkVideo projectId={projectId} branch={branch} />
        </div>
        <ErrorBox error={publish.error} title="Não foi possível enviar" onClose={() => publish.reset()} />
        {reading ? <ReadingLine reading={reading} /> : null}
      </div>

      <div className="flex flex-col gap-2 border-t border-line pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-subtle">Escolher a vencedora</h4>
        <p className="text-[13px] text-muted">
          Para levar só algumas partes da {L} para a A, abra a comparação e marque os trechos. Ou troque a principal inteira.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            icon={ArrowUpCircle}
            busy={promote.isPending}
            disabled={busy || promote.isPending}
            title={disabledTitle}
            onClick={async () => {
              const ok = await confirm({
                title: `Tornar a versão ${L} a principal?`,
                description: `Cria uma versão nova na principal com o conteúdo da ${L} (v${branch.head}). A principal de agora continua no histórico e dá para voltar a ela.`,
                confirmLabel: `Usar a ${L}`,
              });
              if (ok) promote.mutate();
            }}
          >
            {L} vence: virar a principal
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={Trash2}
            busy={discard.isPending}
            disabled={discard.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: `Descartar a versão ${L}?`,
                description: 'A principal fica como está. As versões do ramo continuam no histórico.',
                confirmLabel: 'Descartar',
                tone: 'danger',
              });
              if (ok) discard.mutate();
            }}
          >
            A vence: descartar a {L}
          </Button>
        </div>
        <ErrorBox error={promote.error ?? discard.error} title="Não foi possível concluir" onClose={() => (promote.reset(), discard.reset())} />
      </div>
    </article>
  );
};

/* ------------------------------------------------------------------------------------------ */
/* Edição por instrução no ramo                                                               */
/* ------------------------------------------------------------------------------------------ */

const InstructionBox: React.FC<{ detail: ProjectDetail; branch: AbBranch; busy: boolean }> = ({ detail, branch, busy }) => {
  const projectId = detail.doc.id;
  const refresh = useRefresh(projectId);
  const { toast } = useToast();
  const [instruction, setInstruction] = useState('');
  const [proposal, setProposal] = useState<EditProposal | null>(null);
  const base = `/api/projects/${projectId}/ab/${branch.name}`;
  const L = branch.letter;

  const propose = useMutation({
    mutationFn: () => api.post<EditProposal>(`${base}/edit/propose`, { instruction: instruction.trim() }),
    onSuccess: setProposal,
  });
  const apply = useMutation({
    mutationFn: (p: EditProposal) => api.post<{ version: number; notice: string | null }>(`${base}/edit/apply`, { ops: p.ops, summary: p.summary, rebuild: true }),
    onSuccess: async (r) => {
      setProposal(null);
      setInstruction('');
      await refresh();
      toast({ tone: 'good', title: `Versão ${L} atualizada (v${r.version})`, description: r.notice ?? 'A prévia dela está sendo gerada.' });
    },
  });

  const sceneIndex = (id: string) => detail.doc.scenes.findIndex((s) => s.id === id);
  const items = proposal ? proposal.ops.map((op) => describeOp(op, { doc: detail.doc, sceneIndex, trackName: (p) => p })) : [];

  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (instruction.trim().length >= 2) propose.mutate();
        }}
      >
        <Field label={`O que muda na versão ${L}?`} hint="Só a versão B muda. Ex.: “refaz o gancho com uma pergunta”, “título mais curto”.">
          <Textarea rows={2} value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={4000} />
        </Field>
        <div>
          <Button type="submit" size="sm" icon={Sparkles} busy={propose.isPending} disabled={instruction.trim().length < 2 || propose.isPending}>
            Propor mudança
          </Button>
        </div>
      </form>
      <ErrorBox error={propose.error} title="O agente não conseguiu propor" onClose={() => propose.reset()} />
      {proposal ? (
        <div className="flex flex-col gap-2 rounded-lg border border-sky-400/30 bg-sky-400/5 p-3 text-sm">
          <p className="text-fg">{proposal.summary || 'Mudanças propostas:'}</p>
          {items.length ? (
            <ul className="list-disc pl-5 text-[13px] text-muted">
              {items.map((it, i) => (
                <li key={i}>
                  <span className="text-fg">{it.label}</span>
                  {it.target ? ` · ${it.target}` : ''}
                  {it.change ? ` · ${it.change}` : ''}
                  {it.quote ? <q className="block text-subtle">{it.quote}</q> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-muted">O agente não achou o que mudar. Tente explicar de outro jeito.</p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="primary"
              icon={Sparkles}
              busy={apply.isPending}
              disabled={!items.length || apply.isPending || busy}
              title={busy ? 'Aguarde o trabalho em andamento terminar.' : undefined}
              onClick={() => apply.mutate(proposal)}
            >
              Aplicar na versão {L}
            </Button>
            <Button size="sm" variant="ghost" icon={X} onClick={() => setProposal(null)}>
              Descartar proposta
            </Button>
          </div>
          <ErrorBox error={apply.error} title="Não foi possível aplicar" onClose={() => apply.reset()} />
        </div>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------------------------------ */
/* Comparação lado a lado                                                                     */
/* ------------------------------------------------------------------------------------------ */

const TimelineComposition: React.FC<TimelineProps & { mixUrl?: string }> = ({ mixUrl, ...props }) => (
  <>
    <VideoTimeline {...props} />
    {mixUrl ? <Audio src={mixUrl} /> : null}
  </>
);

const SidePlayer: React.FC<{ projectId: string; branch: AbBranch; lang: string }> = ({ projectId, branch, lang }) => {
  const path = branch.is_main ? `/api/projects/${projectId}/timeline?lang=${encodeURIComponent(lang)}` : `/api/projects/${projectId}/ab/${branch.name}/timeline?lang=${encodeURIComponent(lang)}`;
  const timeline = useQuery({
    queryKey: ['ab-timeline', projectId, branch.name, lang, branch.last_preview?.version ?? null],
    queryFn: () => api.get<Timeline>(path),
    retry: false,
  });
  const origin = window.location.origin;
  const t = timeline.data;
  const inputProps = useMemo(
    () => (t ? { ...t, isChunk: false, assetBaseUrl: `${origin}/files/`, fontBaseUrl: origin, mixUrl: t.mix ? `${origin}${fileUrl(t.mix)}` : undefined } : null),
    [t, origin],
  );
  const stale = t && t.version !== branch.head;
  return (
    <figure className="flex min-w-0 flex-col gap-1.5">
      <figcaption className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-fg">Versão {branch.letter}</span>
        {t ? <span className="text-[13px] text-subtle">prévia da v{t.version}</span> : null}
        {stale ? <Badge tone="warn">desatualizada</Badge> : null}
      </figcaption>
      {inputProps ? (
        <div className="flex justify-center rounded-lg border border-line bg-black p-1">
          <Player
            component={TimelineComposition}
            inputProps={inputProps}
            durationInFrames={Math.max(1, inputProps.durationInFrames)}
            fps={inputProps.fps}
            compositionWidth={inputProps.width}
            compositionHeight={inputProps.height}
            controls
            showPlaybackRateControl={PLAYBACK_RATES}
            acknowledgeRemotionLicense
            style={{ width: '100%', maxHeight: '60vh', aspectRatio: `${inputProps.width} / ${inputProps.height}` }}
          />
        </div>
      ) : timeline.isLoading ? (
        <Skeleton className="aspect-video w-full" />
      ) : timeline.error instanceof ApiError && timeline.error.status === 404 ? (
        <Empty icon={MonitorPlay} title="Sem prévia">
          Use “Gerar prévia de A e {branch.is_main ? 'B' : branch.letter}”.
        </Empty>
      ) : (
        <ErrorBox error={timeline.error} title="Não foi possível carregar a prévia" onRetry={() => timeline.refetch()} />
      )}
    </figure>
  );
};

const ComparePanel: React.FC<{ detail: ProjectDetail; branch: AbBranch; lang: string; setLang: (l: string) => void; busy: boolean }> = ({
  detail,
  branch,
  lang,
  setLang,
  busy,
}) => {
  const projectId = detail.doc.id;
  const refresh = useRefresh(projectId);
  const confirm = useConfirm();
  const { toast } = useToast();
  const L = branch.letter;
  const diff = useQuery({
    queryKey: ['ab-compare', projectId, branch.name, branch.head, detail.project.current_version, lang],
    queryFn: () => api.get<AbCompare>(`/api/projects/${projectId}/ab/compare?b=${encodeURIComponent(branch.name)}&lang=${encodeURIComponent(lang)}`),
  });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showSame, setShowSame] = useState(false);
  const toggle = (id: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const merge = useMutation({
    mutationFn: () => {
      const parts = [...picked].filter((k) => k === 'titulo' || k === 'estilo');
      const segment_ids = [...picked].filter((k) => k !== 'titulo' && k !== 'estilo');
      return api.post<{ version: number }>(`/api/projects/${projectId}/ab/${branch.name}/merge`, { segment_ids, parts });
    },
    onSuccess: async (r) => {
      setPicked(new Set());
      await refresh();
      toast({ tone: 'good', title: `Partes da ${L} levadas para a A (v${r.version})`, description: 'Gere a prévia de novo para ver o resultado.' });
    },
  });

  const d = diff.data;
  const changed = d?.segments.filter((s) => s.status !== 'igual') ?? [];
  const same = d?.segments.filter((s) => s.status === 'igual') ?? [];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
      {detail.doc.languages.length > 1 ? (
        <Field label="Idioma">
          <Select value={lang} onChange={(e) => setLang(e.target.value)} className="w-fit">
            {detail.doc.languages.map((code) => (
              <option key={code} value={code}>
                {langLabel(code)}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}
      {d ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <SidePlayer projectId={projectId} branch={d.a} lang={lang} />
          <SidePlayer projectId={projectId} branch={d.b} lang={lang} />
        </div>
      ) : null}
      <ErrorBox error={diff.error} title="Não foi possível comparar" onRetry={() => diff.refetch()} />
      {diff.isLoading ? <Skeleton className="h-32 w-full" /> : null}
      {d ? (
        <>
          <p className="text-[13px] text-muted">
            {d.changed ? `${d.changed} diferença${d.changed > 1 ? 's' : ''} entre A e ${L}.` : `A e ${L} estão iguais por enquanto.`} Marque o que deve ir para a A.
          </p>
          <ol className="flex flex-col divide-y divide-line">
            {d.parts
              .filter((p) => p.changed)
              .map((p) => (
                <li key={p.key} className="grid grid-cols-1 gap-2 py-2 md:grid-cols-[auto_1fr_1fr]">
                  <Checkbox label={p.label} checked={picked.has(p.key)} onChange={(on) => toggle(p.key, on)} />
                  <DiffText side="A" text={p.a} />
                  <DiffText side={L} text={p.b} />
                </li>
              ))}
            {changed.map((s) => (
              <li key={s.id} className="grid grid-cols-1 gap-2 py-2 md:grid-cols-[auto_1fr_1fr]">
                <div className="flex flex-col gap-1">
                  <Checkbox label={`Trecho ${s.index}${s.role === 'hook' ? ' (gancho)' : ''}`} checked={picked.has(s.id)} onChange={(on) => toggle(s.id, on)} />
                  <Badge tone={SEGMENT_STATUS[s.status].tone} className="w-fit">
                    {s.status === 'novo' ? `Só na ${L}` : SEGMENT_STATUS[s.status].label}
                  </Badge>
                </div>
                <DiffText side="A" text={s.a} />
                <div className="flex flex-col gap-1">
                  <DiffText side={L} text={s.b} />
                  {s.scene_changes.length ? (
                    <ul className="text-[12px] text-subtle">
                      {s.scene_changes.map((c) => (
                        <li key={c.id}>
                          Cena {c.status === 'nova' ? 'nova' : c.status === 'saiu' ? 'que saiu' : 'mudou'}: {c.b ?? c.a}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
          {same.length ? (
            <button type="button" className="w-fit text-[13px] text-muted underline-offset-2 hover:underline" onClick={() => setShowSame((v) => !v)}>
              {showSame ? 'Esconder' : 'Mostrar'} {same.length} trecho{same.length > 1 ? 's' : ''} igua{same.length > 1 ? 'is' : 'l'}
            </button>
          ) : null}
          {showSame ? (
            <ol className="flex flex-col gap-1 text-[13px] text-subtle">
              {same.map((s) => (
                <li key={s.id}>
                  {s.index}. {s.a}
                </li>
              ))}
            </ol>
          ) : null}
          <div>
            <Button
              size="sm"
              variant="primary"
              icon={GitMerge}
              busy={merge.isPending}
              disabled={!picked.size || merge.isPending || busy}
              title={busy ? 'Aguarde o trabalho em andamento terminar.' : picked.size ? undefined : 'Marque pelo menos um trecho.'}
              onClick={async () => {
                const ok = await confirm({
                  title: `Levar ${picked.size} parte${picked.size > 1 ? 's' : ''} da ${L} para a A?`,
                  description: `Cria uma versão nova na principal com essas partes. A versão ${L} fica fechada como “mesclada”.`,
                  confirmLabel: 'Mesclar',
                });
                if (ok) merge.mutate();
              }}
            >
              Mesclar escolhidos na A
            </Button>
          </div>
          <ErrorBox error={merge.error} title="Não foi possível mesclar" onClose={() => merge.reset()} />
        </>
      ) : null}
    </div>
  );
};

const DiffText: React.FC<{ side: string; text: string | null }> = ({ side, text }) => (
  <div className="min-w-0 rounded-md bg-raised/60 px-2 py-1.5 text-[13px]">
    <span className="mr-1.5 font-semibold text-subtle">{side}</span>
    {text ? <span className="text-fg">{text}</span> : <span className="italic text-subtle">não existe</span>}
  </div>
);

/* ------------------------------------------------------------------------------------------ */
/* Publicação: vídeo já publicado e números                                                   */
/* ------------------------------------------------------------------------------------------ */

const LinkVideo: React.FC<{ projectId: string; branch: AbBranch }> = ({ projectId, branch }) => {
  const refresh = useRefresh(projectId);
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [video, setVideo] = useState('');
  const [which, setWhich] = useState<string>(branch.name);
  const link = useMutation({
    mutationFn: () => api.post<{ versao: string; experiments_created: string[] }>(`/api/projects/${projectId}/ab/link`, { branch: which, video: video.trim() }),
    onSuccess: async (r) => {
      setVideo('');
      setOpen(false);
      await refresh();
      toast({
        tone: 'good',
        title: `Vídeo ligado à versão ${r.versao}`,
        description: r.experiments_created.length ? 'Experimento aberto em Desempenho.' : 'Ligue também o vídeo da outra versão para abrir o experimento.',
      });
    },
  });
  if (!open)
    return (
      <Button size="sm" variant="ghost" icon={Link2} onClick={() => setOpen(true)}>
        Ligar vídeo já publicado
      </Button>
    );
  return (
    <form
      className="flex w-full flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (video.trim()) link.mutate();
      }}
    >
      <Field label="Versão" className="w-28">
        <Select value={which} onChange={(e) => setWhich(e.target.value)}>
          <option value="main">A</option>
          <option value={branch.name}>{branch.letter}</option>
        </Select>
      </Field>
      <Field label="Link do vídeo no YouTube" className="min-w-0 flex-1 basis-56">
        <Input value={video} onChange={(e) => setVideo(e.target.value)} placeholder="https://youtu.be/…" />
      </Field>
      <Button type="submit" size="sm" variant="primary" busy={link.isPending} disabled={!video.trim()}>
        Ligar
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
        Cancelar
      </Button>
      <ErrorBox error={link.error} title="Não foi possível ligar o vídeo" className="basis-full" />
    </form>
  );
};

const views = (n: number | null) => (n === null ? '—' : n.toLocaleString('pt-BR'));

const ReadingLine: React.FC<{ reading: AbReading }> = ({ reading }) => (
  <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
    <span>
      Views: A {views(reading.a.views)} × {reading.letter} {views(reading.b.views)}
    </span>
    {reading.direct ? <Badge tone={reading.direct.young ? 'neutral' : 'info'}>{reading.direct.text}</Badge> : null}
    {reading.experiment?.evaluation ? <span className="text-subtle">Experimento: {reading.experiment.evaluation.text}</span> : null}
    {[...reading.a.videos.map((v) => ['A', v] as const), ...reading.b.videos.map((v) => [reading.letter, v] as const)].map(([side, v]) =>
      v.url ? (
        <a key={v.publication_id} href={v.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent underline-offset-2 hover:underline">
          {side} <ExternalLink size={12} aria-hidden="true" />
        </a>
      ) : null,
    )}
  </div>
);
