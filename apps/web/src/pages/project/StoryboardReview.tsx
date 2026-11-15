// Painel da curadoria no topo da aba Cenas: direção de arte, contagem, avisos da revisão e a aprovação das cenas.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronUp, CircleCheck, Info, Palette, RotateCw, Sparkles, TriangleAlert } from 'lucide-react';
import React, { useId, useState } from 'react';
import { api, type ArtDirection, type ProjectDetail, type StoryboardIssue, type StyleItem, type StyleSpec } from '../../api';
import {
  APPROACH_LABELS,
  GENRE_LABELS,
  GRADE_LABELS,
  label,
  REVIEW_SEVERITY_LABELS,
  VISUAL_LANGUAGE_LABELS,
} from '../../labels';
import { Badge, type BadgeTone, Button, ErrorBox, useConfirm, useToast } from '../../ui';
import { approveKey, useJobStarting, useStartJob } from '../ProjectPage';
import { useUnsavedCount } from './core/drafts';
import {
  awaitingScenes,
  DIRECTION_MISSING_REASON,
  directionMissing,
  DOC_BUSY_REASON,
  docJobRunning,
  gatesOf,
  quotaMinutes,
  sceneCounts,
  STYLE_MISSING_REASON,
  storyboardReviewOf,
} from './core/model';
import { usePreviewPlan } from './core/usePreviewPlan';

const BUSY_REASON = 'Aguarde o trabalho em andamento terminar.';

const plural = (n: number, one: string, many: string) => (n === 1 ? `1 ${one}` : `${n} ${many}`);

/** Cor CSS de um nome da paleta ("teal", "#FF2E88", "hot pink" → hotpink, "sunset orange" → orange), ou null. */
const swatchColor = (name: string): string | null => {
  const supports = (c: string) => typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('color', c);
  const raw = name.trim();
  if (!raw || /^(inherit|initial|unset|revert|currentcolor|transparent)$/i.test(raw)) return null;
  if (supports(raw)) return raw;
  const words = raw.toLowerCase().split(/[\s_-]+/).filter(Boolean);
  if (supports(words.join(''))) return words.join('');
  for (let i = words.length - 1; i >= 0; i -= 1) if (supports(words[i])) return words[i];
  return null;
};

const SEVERITY_TONE: Record<string, BadgeTone> = { grave: 'bad', aviso: 'warn', corrigido: 'info' };

/** "Short 9:16" ou "vídeo longo 16:9", para avisar quando a troca de estilo mudaria o formato. */
const formatText = (format: StyleSpec['format']) =>
  `${format.type === 'short' ? 'Short' : 'vídeo longo'} ${format.aspect || (format.type === 'short' ? '9:16' : '16:9')}`;
const sameFormat = (a: StyleSpec['format'], b: StyleSpec['format']) => a.type === b.type && a.aspect === b.aspect;

const IssueRow: React.FC<{ issue: StoryboardIssue; sceneNumber: number | undefined; onShowScene: (sceneId: string) => void }> = ({
  issue,
  sceneNumber,
  onShowScene,
}) => (
  <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
    <Badge tone={SEVERITY_TONE[issue.severity] ?? 'warn'} className="shrink-0">
      {label(REVIEW_SEVERITY_LABELS, issue.severity) || 'Aviso'}
    </Badge>
    <span className="min-w-0 flex-1 basis-60 text-fg">{issue.message}</span>
    {issue.scene_id && sceneNumber ? (
      <button type="button" className="shrink-0 rounded-sm text-muted underline underline-offset-2 hover:text-fg" onClick={() => onShowScene(issue.scene_id!)}>
        Ver a cena {sceneNumber}
      </button>
    ) : null}
  </li>
);

/** Linha "rótulo: conteúdo" da direção (lista de definições). */
const Row: React.FC<{ term: string; children: React.ReactNode }> = ({ term, children }) => (
  <div className="flex min-w-0 flex-col gap-0.5 @md:flex-row @md:gap-3">
    <dt className="shrink-0 text-xs font-medium text-subtle @md:w-24 @md:pt-px">{term}</dt>
    <dd className="m-0 min-w-0 flex-1 text-[13px] text-fg">{children}</dd>
  </div>
);

const Direction: React.FC<{ art: ArtDirection }> = ({ art }) => {
  const genre = label(GENRE_LABELS, art.genre);
  const palette = art.palette.filter((c) => c.trim()).slice(0, 3);
  const avoid = art.avoid.filter((a) => a.trim());
  const entities = art.entities.filter((e) => e.name.trim() || e.id.trim());
  return (
    <div className="@container flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-fg">Direção de arte deste vídeo</h3>
        {art.source === 'user' ? <Badge tone="info">Definida por você</Badge> : null}
      </div>
      <dl className="m-0 flex flex-col gap-2">
        {genre || art.approach ? (
          <Row term="Gênero">
            {[genre, art.approach ? `abordagem ${label(APPROACH_LABELS, art.approach).toLowerCase()}` : ''].filter(Boolean).join(' · ')}
          </Row>
        ) : null}
        {art.world.trim() ? (
          <Row term="Mundo">
            <span lang="en">{art.world}</span>
          </Row>
        ) : null}
        {art.languages.length ? (
          <Row term="Linguagens">
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
              {art.languages.map((id, i) => (
                <li key={id}>
                  <Badge tone={i === 0 ? 'accent' : 'neutral'} title={i === 0 ? 'Linguagem principal' : undefined}>
                    {label(VISUAL_LANGUAGE_LABELS, id)}
                    {i === 0 ? <span className="sr-only"> (principal)</span> : null}
                  </Badge>
                </li>
              ))}
            </ul>
          </Row>
        ) : null}
        {palette.length || art.grade ? (
          <Row term="Cores">
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {palette.map((name) => {
                const color = swatchColor(name);
                return (
                  <span key={name} className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="h-4 w-4 shrink-0 rounded border border-line-strong"
                      style={
                        color
                          ? { background: color }
                          : { backgroundImage: 'repeating-linear-gradient(45deg, #34343e 0 3px, transparent 3px 6px)' }
                      }
                    />
                    <span lang="en">{name}</span>
                  </span>
                );
              })}
              {art.grade ? <span className="text-muted">gradação {label(GRADE_LABELS, art.grade).toLowerCase()}</span> : null}
            </span>
          </Row>
        ) : null}
        {entities.length ? (
          <Row term="Recorrentes">
            {entities.map((e, i) => (
              <React.Fragment key={e.id || i}>
                {i > 0 ? ', ' : ''}
                <span title={e.look || undefined}>{e.name || e.id}</span>
              </React.Fragment>
            ))}
          </Row>
        ) : null}
        {avoid.length ? (
          <Row term="Evitar">
            <span lang="en">{avoid.join(', ')}</span>
          </Row>
        ) : null}
      </dl>
    </div>
  );
};

export const StoryboardReview: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  /** Número de exibição de cada cena ("Cena 3"). */
  numbers: ReadonlyMap<string, number>;
  onShowScene: (sceneId: string) => void;
}> = ({ detail, busy, numbers, onShowScene }) => {
  const { doc, style } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const startJob = useStartJob(doc.id);
  const starting = useJobStarting(doc.id);
  const unsaved = useUnsavedCount(doc.id);
  const headingId = useId();
  const bodyId = useId();

  const art = doc.art ?? null;
  const review = storyboardReviewOf(detail);
  const awaiting = awaitingScenes(detail);
  const gates = gatesOf(detail);
  const toFinal = !gates.preview;
  const hasPreview = Boolean(detail.outputs.preview);
  const docBusy = docJobRunning(detail);
  const counts = sceneCounts(doc);
  const [open, setOpen] = useState<boolean | null>(null);
  // Aberto enquanto as cenas esperam a conferência ou ainda não há imagens; depois, fica recolhido.
  const expanded = open ?? (awaiting || !hasPreview);

  // A conta do build (com o cache): quantas imagens a próxima prévia gera de fato.
  const plan = usePreviewPlan(detail);
  // Cenas dirigidas sem direção (estilo trocado e cenas não refeitas): não entram na conta nem podem ser aprovadas.
  const lost = directionMissing(doc);
  const missing = plan.data ? plan.data.images_missing.length : Math.max(0, counts.newImages - lost);
  const ready = plan.data ? Math.max(0, counts.newImages - lost - missing) : 0;

  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles'), enabled: Boolean(art?.suggested_style) });
  const styleOf = (id: string) => styles.data?.find((s) => s.style.id === id)?.style;
  const styleName = (id: string) => styleOf(id)?.name ?? id;

  const invalidate = () => client.invalidateQueries({ queryKey: ['project', doc.id] });

  const approve = useMutation({
    mutationKey: approveKey(doc.id, 'storyboard'),
    mutationFn: () => api.post(`/api/projects/${doc.id}/approve`, { stage: 'storyboard', continue_pipeline: true }),
    // Espera o refetch: até o job aparecer, o botão continua ocupado (um 2º clique faria um 2º piloto).
    onSuccess: async () => {
      await invalidate();
      toast({
        tone: 'good',
        title: 'Cenas aprovadas',
        description: toFinal ? 'Gerando as imagens, a prévia e o vídeo final.' : 'Gerando as imagens e a prévia.',
      });
    },
  });

  const switchStyle = useMutation({
    mutationFn: ({ styleId, name }: { styleId: string; name: string }) =>
      // Sozinha na chamada: o set_style de outras ops validaria contra o estilo antigo.
      api.post(`/api/projects/${doc.id}/ops`, { ops: [{ op: 'set_style_id', style_id: styleId }], message: `Estilo trocado para ${name}` }),
    onSuccess: async () => {
      await Promise.all([invalidate(), client.invalidateQueries({ queryKey: ['projects'] })]);
      // Só refaz as cenas se a troca deu certo.
      startJob.mutate({ kind: 'storyboard' });
    },
  });

  const blockedReason = !style ? STYLE_MISSING_REASON : docBusy ? DOC_BUSY_REASON : busy || starting ? BUSY_REASON : null;
  const working = startJob.isPending || switchStyle.isPending || approve.isPending;
  // Aprovar gera as imagens dos prompts salvos: rascunho de prompt viraria imagem jogada fora ao salvar.
  const unsavedReason = unsaved.prompts
    ? unsaved.prompts === 1
      ? 'Salve ou desfaça o prompt não salvo primeiro.'
      : `Salve ou desfaça os ${unsaved.prompts} prompts não salvos primeiro.`
    : null;
  const approveBlocked = blockedReason ?? (lost ? DIRECTION_MISSING_REASON : unsavedReason);

  const promptNote = unsaved.prompts
    ? ` ${unsaved.prompts === 1 ? 'O prompt não salvo se perde' : `Os ${unsaved.prompts} prompts não salvos se perdem`}.`
    : '';
  const imagesNote = gates.storyboard
    ? 'Nenhuma imagem é gerada agora: as cenas novas esperam a sua conferência.'
    : 'Nenhuma imagem é gerada agora: as que faltarem saem na próxima prévia.';

  const redo = async () => {
    if (blockedReason || working) return;
    const ok = await confirm({
      title: 'Refazer as cenas?',
      description: `Cria as cenas de novo a partir do roteiro, com direção de arte, cartões e revisão. Substitui as cenas atuais e os ajustes feitos nelas; as cenas travadas (e as outras do mesmo trecho) ficam.${promptNote} ${imagesNote} Dá para voltar em Versões. Usa o Claude.`,
      confirmLabel: 'Refazer as cenas',
    });
    if (!ok) return;
    switchStyle.reset();
    startJob.mutate({ kind: 'storyboard' });
  };

  const suggested = art?.suggested_style && art.suggested_style !== doc.style_id ? art.suggested_style : null;
  // A troca de estilo nunca muda o formato do vídeo (Short para longo, 9:16 para 16:9): o servidor só sugere estilos
  // do mesmo formato, mas uma revisão antiga pode ter sugerido outro.
  const suggestedSpec = suggested ? styleOf(suggested) : undefined;
  const otherFormat = Boolean(style && suggestedSpec && !sameFormat(style.format, suggestedSpec.format));
  const askSwitch = async () => {
    if (!suggested || otherFormat || blockedReason || working) return;
    const name = styleName(suggested);
    const ok = await confirm({
      title: `Trocar para ${name} e refazer as cenas?`,
      description: `O projeto passa a usar o estilo ${name} (legendas, cartões, efeitos e câmera dele; os ajustes do projeto, como a voz escolhida, continuam) e as cenas são criadas de novo com uma direção de arte nova. O roteiro e o formato${style ? ` (${formatText(style.format)})` : ''} não mudam. Os ajustes feitos nas cenas atuais se perdem, menos os das cenas travadas e dos trechos delas.${promptNote} ${imagesNote} Dá para voltar em Versões. Usa o Claude.`,
      confirmLabel: 'Trocar e refazer',
    });
    if (ok) switchStyle.mutate({ styleId: suggested, name });
  };

  const issues = review?.issues ?? [];
  const attention = issues.filter((i) => i.severity !== 'corrigido');
  const fixed = issues.filter((i) => i.severity === 'corrigido');
  const graves = issues.filter((i) => i.severity === 'grave').length;
  // Os graves primeiro (a ordem do resto fica a da revisão).
  const ordered = [...attention].sort((a, b) => (a.severity === 'grave' ? 0 : 1) - (b.severity === 'grave' ? 0 : 1));

  const parts = [
    plural(counts.scenes, 'cena', 'cenas'),
    missing
      ? `${plural(missing, 'imagem nova', 'imagens novas')} (~${quotaMinutes(missing)} min de cota)${ready ? `, ${ready} já ${ready === 1 ? 'pronta' : 'prontas'}` : ''}`
      : ready
        ? `nenhuma imagem nova (${ready} já ${ready === 1 ? 'pronta' : 'prontas'})`
        : 'nenhuma imagem nova',
    // Projeto antigo (sem direção): não mostra "0 reaproveitadas · 0 cartões".
    counts.reused || art ? plural(counts.reused, 'reaproveitada', 'reaproveitadas') : '',
    counts.cards || art ? `${plural(counts.cards, 'cartão', 'cartões')} (sem cota)` : '',
  ].filter(Boolean);

  const fit = art?.style_fit ?? 'ok';
  const hasDetails = Boolean(art) || Boolean(review);

  return (
    <section
      aria-labelledby={headingId}
      className={`rounded-xl border bg-card ${awaiting ? 'border-accent/45' : 'border-line'}`}
    >
      <header className="@container flex flex-wrap items-start gap-x-3 gap-y-2 px-4 py-3">
        <Sparkles size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1 basis-64">
          <h2 id={headingId} className="font-display text-[15px] font-semibold leading-6 text-fg">
            {awaiting ? 'Confira as cenas antes das imagens' : 'Curadoria das cenas'}
          </h2>
          <p className="mt-0.5 text-[13px] leading-snug text-muted">{parts.join(' · ')}</p>
          {lost && !awaiting ? (
            <p className="mt-1 flex items-start gap-1.5 text-[13px] text-amber-200">
              <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />O estilo mudou: refaça as cenas para gerar as imagens com a
              direção de arte nova.
            </p>
          ) : null}
          {review?.counts.budget ? (
            <p className="text-xs text-subtle">Limite de imagens novas deste vídeo: {review.counts.budget}.</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {hasDetails ? (
            <Button
              size="sm"
              variant="ghost"
              icon={expanded ? ChevronUp : ChevronDown}
              aria-expanded={expanded}
              aria-controls={bodyId}
              onClick={() => setOpen(!expanded)}
            >
              {expanded ? 'Esconder a direção' : 'Ver a direção e os avisos'}
            </Button>
          ) : null}
          <Button
            size="sm"
            icon={RotateCw}
            busy={startJob.isPending && startJob.variables?.kind === 'storyboard' && !switchStyle.isSuccess}
            disabled={Boolean(blockedReason) || (working && !startJob.isPending)}
            title={blockedReason ?? 'Cria as cenas de novo a partir do roteiro. Usa o Claude.'}
            onClick={() => void redo()}
          >
            Refazer as cenas
          </Button>
        </div>
      </header>

      {hasDetails ? (
        <div id={bodyId} hidden={!expanded} className="flex flex-col gap-4 border-t border-line px-4 py-3">
          {art ? <Direction art={art} /> : null}

          {art && fit !== 'ok' ? (
            <div role="note" className="flex gap-3 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm">
              <Palette size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <p className="font-medium text-fg">
                  {fit === 'errado'
                    ? `O estilo ${style?.name ?? doc.style_id} não combina com este assunto.`
                    : `O estilo ${style?.name ?? doc.style_id} combina pouco com este assunto.`}
                </p>
                {art.style_note.trim() ? <p className="text-[13px] text-amber-100">{art.style_note}</p> : null}
                {suggested && otherFormat && suggestedSpec ? (
                  <p className="text-xs text-subtle">
                    O estilo sugerido ({styleName(suggested)}) é de outro formato ({formatText(suggestedSpec.format)}): trocar mudaria o formato do
                    vídeo. Escolha um estilo {style ? formatText(style.format) : 'do mesmo formato'} em Estilo, se quiser trocar.
                  </p>
                ) : suggested ? (
                  <div className="flex flex-col items-start gap-1">
                    <Button
                      size="sm"
                      icon={RotateCw}
                      busy={switchStyle.isPending || (switchStyle.isSuccess && startJob.isPending)}
                      disabled={Boolean(blockedReason) || (working && !switchStyle.isPending)}
                      title={blockedReason ?? undefined}
                      onClick={() => void askSwitch()}
                    >
                      Trocar para {styleName(suggested)} e refazer as cenas
                    </Button>
                    <span className="text-xs text-subtle">Usa o Claude. Nenhuma imagem é gerada.</span>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {review ? (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium text-fg">Revisão da curadoria</h3>
              {review.stale ? (
                <p className="flex items-center gap-1.5 text-[13px] text-muted">
                  <Info size={14} aria-hidden="true" className="shrink-0" />
                  A revisão é de outra versão das cenas (restaurada ou refeita à mão). Refaça as cenas para revisar de novo.
                </p>
              ) : ordered.length ? (
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                  {ordered.map((issue, i) => (
                    <IssueRow
                      key={`${issue.code}-${issue.scene_id ?? 'video'}-${i}`}
                      issue={issue}
                      sceneNumber={issue.scene_id ? numbers.get(issue.scene_id) : undefined}
                      onShowScene={onShowScene}
                    />
                  ))}
                </ul>
              ) : (
                <p className="flex items-center gap-1.5 text-[13px] text-emerald-300">
                  <CircleCheck size={14} aria-hidden="true" className="shrink-0" />
                  Nenhum problema encontrado.
                </p>
              )}
              {fixed.length ? (
                <details className="text-[13px] text-muted">
                  <summary className="w-fit rounded-sm hover:text-fg">
                    {plural(fixed.length, 'ajuste feito', 'ajustes feitos')} automaticamente
                  </summary>
                  <ul className="m-0 mt-1.5 flex list-none flex-col gap-1.5 p-0">
                    {fixed.map((issue, i) => (
                      <IssueRow
                        key={`${issue.code}-${issue.scene_id ?? 'video'}-${i}`}
                        issue={issue}
                        sceneNumber={issue.scene_id ? numbers.get(issue.scene_id) : undefined}
                        onShowScene={onShowScene}
                      />
                    ))}
                  </ul>
                </details>
              ) : null}
              {review.retried ? (
                <p className="flex items-center gap-1.5 text-xs text-subtle">
                  <Info size={12} aria-hidden="true" className="shrink-0" />O Claude refez as cenas uma vez para corrigir problemas graves.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {awaiting ? (
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-t border-line px-4 py-3">
          <div className="min-w-0 flex-1 basis-64 text-[13px] leading-snug">
            {lost ? (
              <p className="mb-1 flex items-start gap-1.5 font-medium text-amber-200">
                <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                {DIRECTION_MISSING_REASON}
              </p>
            ) : null}
            <p className="text-muted">
              {missing
                ? `Ao aprovar, o app gera ${plural(missing, 'imagem nova', 'imagens novas')} (~${quotaMinutes(missing)} min da cota de imagens) e monta a prévia${toFinal ? ' e o vídeo final' : ''}.`
                : `Nenhuma imagem nova: ao aprovar, o app monta a prévia${toFinal ? ' e o vídeo final' : ''} com as imagens que já existem.`}
            </p>
            {graves ? (
              <p className="mt-1 flex items-start gap-1.5 text-amber-200">
                <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                {graves === 1 ? 'Há 1 aviso que precisa de atenção.' : `Há ${graves} avisos que precisam de atenção.`} Dá para ajustar as cenas, refazer
                ou aprovar assim mesmo.
              </p>
            ) : null}
          </div>
          <div className="flex flex-col items-end gap-1">
            <Button
              variant="primary"
              icon={Check}
              busy={approve.isPending}
              disabled={Boolean(approveBlocked) || (working && !approve.isPending)}
              title={approveBlocked ?? undefined}
              onClick={() => {
                if (!approve.isPending && !approveBlocked) approve.mutate();
              }}
            >
              Aprovar cenas e gerar imagens
            </Button>
            {approveBlocked && approveBlocked !== DIRECTION_MISSING_REASON ? (
              <p className="text-[13px] text-amber-200">{approveBlocked}</p>
            ) : null}
          </div>
        </div>
      ) : null}

      {approve.error || switchStyle.error || startJob.error ? (
        <div className="border-t border-line px-4 py-3">
          <ErrorBox
            error={approve.error ?? switchStyle.error ?? startJob.error}
            title={approve.error ? 'Não foi possível aprovar as cenas' : switchStyle.error ? 'Não foi possível trocar o estilo' : 'Não foi possível refazer as cenas'}
            onClose={() => {
              approve.reset();
              switchStyle.reset();
              startJob.reset();
            }}
          />
        </div>
      ) : null}
    </section>
  );
};
