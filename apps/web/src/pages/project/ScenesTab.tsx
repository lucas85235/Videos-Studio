import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FileText,
  ImageOff,
  LayoutGrid,
  LayoutTemplate,
  Lock,
  LockOpen,
  MonitorPlay,
  RefreshCw,
  Repeat2,
  RotateCcw,
  Save,
  TriangleAlert,
  Type,
} from 'lucide-react';
import React, { memo, useCallback, useId, useMemo, useState } from 'react';
import { api, ApiError, fileUrl, type ProjectDetail, type ProjectDoc, type Scene, type StoryboardIssue, type Version } from '../../api';
import {
  CAMERA_LABELS,
  CHART_LABELS,
  COMPOSITION_LABELS,
  FOCUS_LABELS,
  label,
  REVIEW_SEVERITY_LABELS,
  SCENE_KIND_LABELS,
  SHOT_LABELS,
  TRANSITION_LABELS,
  VISUAL_LANGUAGE_LABELS,
} from '../../labels';
import { Badge, Button, Card, Empty, ErrorBox, IconButton, Select, Skeleton, Spinner, Textarea, useConfirm } from '../../ui';
import { useJobStarting, useStartJob } from '../ProjectPage';
import { promptDrafts, sceneChanges, useKeyedStore, useKeyedValue } from './core/drafts';
import { ImageDialog } from './core/ImageDialog';
import { shownMedia } from './core/media';
import { tabOwnsAction } from './core/NextStep';
import {
  awaitingScenes,
  CARD_KINDS,
  DOC_BUSY_REASON,
  docJobRunning,
  isActive,
  isShort,
  nextStep,
  resolveCard,
  reuseRoot,
  rotateVariant,
  sceneNeedsImage,
  sceneNumbers,
  storyboardReviewOf,
  STYLE_MISSING_REASON,
} from './core/model';
import { PresenterChip } from './PresenterChip';
import { RealMediaFinder, RealMediaPanel } from './RealMediaPanel';
import { StoryboardReview } from './StoryboardReview';
import { SceneVideoControl, VideoClipsPanel } from './VideoClipsPanel';
import type { TabNavigation } from './types';

const MOTIONS = ['', 'ken_burns_in', 'ken_burns_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down', 'punch_zoom', 'zoom_to', 'drift', 'static', 'parallax'];
const TRANSITIONS = ['', 'cut', 'flash', 'fade_black', 'glitch', 'light_leak', 'zoom_blur', 'film_burn', 'static_burst'];
const KINDS = ['image', 'stat', 'compare', 'chart', 'map', 'text', 'title', 'count', 'document', 'quote'];
const AUTO = 'Automática';
const BUILD_KINDS = new Set(['preview', 'autopilot', 'render']);
const BUSY_REASON = 'Aguarde o trabalho em andamento terminar.';

/** Versões que não mudam o que a prévia mostra (aprovações, título, briefing, travas, sugestão de mídia real). */
const NON_VISUAL_VERSION =
  /^(Prévia aprovada|Roteiro aprovado|Cenas aprovadas|Título editado|Briefing editado|Trecho (des)?travado|Segmento (des)?travado)|: (des)?travada$|: sugestão de mídia real (dispensada|editada)$/;

/** Id do cartão da cena na página (os avisos da curadoria e o "Reaproveita a imagem da cena N" levam até ele). */
const sceneDomId = (sceneId: string) => `cena-${sceneId}`;

/** Destaque passageiro da cena de destino (o foco posto pelo código depois de um clique não mostra :focus-visible). */
const FLASH_CLASSES = ['outline-2', 'outline-offset-2', 'outline-accent'];
const flashTimers = new WeakMap<HTMLElement, number>();

/** Rola até o cartão da cena, põe o foco nele (sem rolar de novo) e o destaca por um instante. */
const showScene = (sceneId: string) => {
  const el = document.getElementById(sceneDomId(sceneId));
  if (!el) return;
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
  el.focus({ preventScroll: true });
  el.classList.add(...FLASH_CLASSES);
  window.clearTimeout(flashTimers.get(el));
  flashTimers.set(el, window.setTimeout(() => el.classList.remove(...FLASH_CLASSES), 1600));
};

type TimelineLite = { scenes: { id: string; src: string | null; durationInFrames: number }[]; fps: number };

const formatSecs = (s: number) => `${s.toFixed(1).replace('.', ',')} s`;

/** Select com rótulo visível pequeno (ligado ao campo). */
const LabeledSelect: React.FC<{
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  children: React.ReactNode;
}> = ({ label: text, value, disabled, onChange, children }) => {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-subtle">
        {text}
      </label>
      <Select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="w-full min-w-0 text-[13px]">
        {children}
      </Select>
    </div>
  );
};

const SceneThumb: React.FC<{
  scene: Scene;
  index: number;
  src: string | null;
  primaryLang: string;
  short: boolean;
  loading: boolean;
  /** Número da cena dona da imagem (cena que reaproveita); undefined quando a cena tem imagem própria. */
  reuseNumber: number | null | undefined;
  /** Arquivo da mídia real que a cena mostra (dela ou da cena reaproveitada): aparece na hora, sem esperar a prévia. */
  mediaAsset: string | null;
  onOpen: (src: string) => void;
}> = ({ scene, index, src, primaryLang, short, loading, reuseNumber, mediaAsset, onOpen }) => {
  const box = `relative overflow-hidden rounded-lg border border-line bg-canvas ${short ? 'aspect-[9/16] w-28 shrink-0' : 'aspect-video w-full'}`;
  // A cena-chave de vídeo IA mostra a imagem (o primeiro quadro do clipe), como a cena de imagem.
  const isImage = scene.kind === 'image' || scene.kind === 'video';
  const videoBadge =
    scene.kind === 'video' ? (
      <span className="pointer-events-none absolute right-1 top-1">
        <Badge tone="accent" title="Vídeo IA: a imagem é o primeiro quadro do clipe">
          Vídeo IA
        </Badge>
      </span>
    ) : null;
  const realBadge = (
    <span className="pointer-events-none absolute left-1 top-1">
      <Badge tone="info" title="Mídia real (no lugar da imagem de IA)">
        Real
      </Badge>
    </span>
  );
  if (mediaAsset && isImage) {
    return (
      <button type="button" className={`${box} group`} onClick={() => onOpen(mediaAsset)} aria-label={`Ampliar a mídia real da cena ${index}`}>
        <img src={fileUrl(mediaAsset)} alt="" className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" />
        {realBadge}
      </button>
    );
  }
  if (src && !(mediaAsset && !isImage)) {
    return (
      <button type="button" className={`${box} group`} onClick={() => onOpen(src)} aria-label={`Ampliar a imagem da cena ${index}`}>
        <img src={fileUrl(src)} alt="" className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" />
        {videoBadge}
      </button>
    );
  }
  const overlay = scene.overlay_text?.[primaryLang] ?? Object.values(scene.overlay_text ?? {})[0] ?? '';
  if (!isImage) {
    const card = resolveCard(scene.card, primaryLang, primaryLang);
    const big = card.value || (card.items?.length ? card.items.map((i) => i.value || i.label).join(' × ') : '') || overlay || scene.number;
    return (
      <div className={`${box} flex flex-col items-center justify-center gap-1 p-2 text-center`}>
        {/* Cartão com mídia real: a foto é o fundo (escurecida), o texto fica por cima. */}
        {mediaAsset ? <img src={fileUrl(mediaAsset)} alt="" loading="lazy" className="absolute inset-0 h-full w-full object-cover opacity-25" /> : null}
        <Type size={16} aria-hidden="true" className="relative text-subtle" />
        {big != null && big !== '' ? <span className={`relative line-clamp-4 font-display font-bold text-fg ${short ? 'text-sm' : 'text-lg'}`}>{big}</span> : null}
        {card.label ? <span className="relative line-clamp-2 text-xs text-muted">{card.label}</span> : null}
        <span className="relative text-xs text-subtle">{label(SCENE_KIND_LABELS, scene.kind)}</span>
        {mediaAsset ? realBadge : null}
      </div>
    );
  }
  if (loading) return <Skeleton className={`${box} rounded-lg`} />;
  if (reuseNumber !== undefined) {
    const text = reuseNumber ? `Imagem da cena ${reuseNumber}` : 'Fundo animado';
    return (
      <div role="img" aria-label={text} title={text} className={`${box} flex flex-col items-center justify-center gap-1 p-2 text-center`}>
        <Repeat2 size={18} aria-hidden="true" className="text-subtle" />
        <span className="text-xs text-subtle">{text}</span>
      </div>
    );
  }
  return (
    // Só o ícone: a barra do topo da aba já diz que as imagens aparecem depois da prévia (não repete em cada cena).
    <div role="img" aria-label="Sem imagem ainda" title="Sem imagem ainda" className={`${box} flex items-center justify-center p-2`}>
      <ImageOff size={18} aria-hidden="true" className="text-subtle" />
    </div>
  );
};

type SceneCardProps = {
  scene: Scene;
  index: number;
  src: string | null;
  seconds: number | null;
  projectId: string;
  primaryLang: string;
  short: boolean;
  changed: boolean;
  thumbLoading: boolean;
  /** Motivo (visível) para não editar agora: job que regrava o documento, estilo removido. */
  blockedReason: string | null;
  /** Cena que reaproveita: número e id da cena dona da imagem (null: a imagem não existe mais). */
  reuseNumber: number | null | undefined;
  reuseRootId: string | null;
  /** Avisos da revisão da curadoria sobre esta cena (sem os já corrigidos). */
  issues: StoryboardIssue[] | undefined;
  /** Linguagens da direção de arte ("Outra imagem" alterna entre elas). */
  artLanguages: string[] | undefined;
  /** Arquivo da mídia real que a cena mostra (dela ou da cena reaproveitada), ou null. */
  mediaAsset: string | null;
  /** Documento inteiro (o painel de mídia real usa as fontes do roteiro e o idioma principal). */
  doc: ProjectDoc;
  onOpenImage: (src: string, index: number, prompt: string) => void;
};

/** Linha "rótulo: valor" do resumo do cartão. */
const CardLine: React.FC<{ term: string; children: React.ReactNode }> = ({ term, children }) => (
  <>
    <dt className="text-xs text-subtle">{term}</dt>
    <dd className="m-0 min-w-0 break-words text-[13px] text-fg">{children}</dd>
  </>
);

/** Resumo do cartão e das sobreposições da cena (só leitura). */
const CardSummary: React.FC<{ scene: Scene; primaryLang: string }> = ({ scene, primaryLang }) => {
  const card = resolveCard(scene.card, primaryLang, primaryLang);
  const { items } = card;
  if (!card.value && !card.label && !items?.length && !card.headline && !card.stamp && !card.source) return null;
  return (
    <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 rounded-lg border border-line bg-canvas px-2.5 py-2">
      {card.value ? (
        <CardLine term="Número">
          <span className="font-display font-bold">{card.value}</span>
        </CardLine>
      ) : null}
      {card.label ? <CardLine term="Rótulo">{card.label}</CardLine> : null}
      {scene.kind === 'chart' ? <CardLine term="Gráfico">{label(CHART_LABELS, card.chart ?? 'bar')}</CardLine> : null}
      {items?.length ? (
        <CardLine term="Itens">
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {items.map((item, i) => (
              <li key={i} className={i === card.highlight ? 'font-semibold text-accent' : undefined}>
                {[item.label, item.value].filter(Boolean).join(': ')}
                {item.region && item.region !== item.label ? <span className="text-subtle"> ({item.region})</span> : null}
                {i === card.highlight ? <span className="sr-only"> (em destaque)</span> : null}
              </li>
            ))}
          </ul>
        </CardLine>
      ) : null}
      {card.headline ? <CardLine term="Faixa">{card.headline}</CardLine> : null}
      {card.stamp ? <CardLine term="Carimbo">{card.stamp}</CardLine> : null}
      {card.source ? <CardLine term="Fonte">{card.source}</CardLine> : null}
    </dl>
  );
};

const SceneCardImpl: React.FC<SceneCardProps> = ({
  scene,
  index,
  src,
  seconds,
  projectId,
  primaryLang,
  short,
  changed,
  thumbLoading,
  blockedReason,
  reuseNumber,
  reuseRootId,
  issues,
  artLanguages,
  mediaAsset,
  doc,
  onOpenImage,
}) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  // Só a entrada desta cena: digitar num prompt não re-renderiza os outros cartões.
  const draft = useKeyedValue(promptDrafts, projectId, scene.id);
  const server = scene.visual.prompt;
  const prompt = draft?.value ?? server;
  const conflict = draft && draft.base !== server;
  const promptId = useId();

  const run = useMutation({
    mutationFn: ({ ops, message }: { ops: Record<string, unknown>[]; message: string; mark?: boolean; savedPrompt?: string }) =>
      api.post(`/api/projects/${projectId}/ops`, { ops, message }),
    onSuccess: async (_r, vars) => {
      if (vars.mark) sceneChanges.set(projectId, scene.id, Date.now());
      // Espera o documento novo antes de tirar o rascunho (senão a caixa mostra o prompt antigo por um instante).
      await client.invalidateQueries({ queryKey: ['project', projectId] });
      if (vars.savedPrompt !== undefined && promptDrafts.get(projectId)[scene.id]?.value === vars.savedPrompt) promptDrafts.set(projectId, scene.id, null);
    },
  });
  const blocked = Boolean(blockedReason);
  const disabled = scene.locked || run.isPending || blocked;
  const name = `Cena ${index}`;

  const setPrompt = (value: string) =>
    promptDrafts.set(projectId, scene.id, value === server ? null : { value, base: draft?.base ?? server });
  const savePrompt = () => {
    if (!draft || disabled) return;
    run.mutate({ ops: [{ op: 'update_scene_prompt', scene_id: scene.id, prompt: draft.value }], message: `${name}: prompt editado`, mark: true, savedPrompt: draft.value });
  };
  const discardPrompt = async (title: string) => {
    const ok = await confirm({
      title,
      description: 'O prompt que você digitou nesta cena some e volta o que está salvo. Não dá para desfazer.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (ok) promptDrafts.set(projectId, scene.id, null);
  };

  // Número, Comparação, Gráfico e Mapa só com os dados do cartão (sem eles, o renderer não tem o que desenhar).
  const cardData = resolveCard(scene.card, primaryLang, primaryLang);
  const itemCount = cardData.items?.length ?? 0;
  const offered = KINDS.filter(
    (k) =>
      (k !== 'stat' || Boolean(cardData.value)) &&
      (k !== 'compare' || itemCount >= 2) &&
      (k !== 'chart' || itemCount >= 2) &&
      (k !== 'map' || Boolean(cardData.items?.some((i) => i.region))),
  );
  const kinds = offered.includes(scene.kind) ? offered : [...offered, scene.kind];
  const selects = (
    <>
      <LabeledSelect
        label="Tipo"
        value={scene.kind}
        disabled={disabled}
        onChange={(v) => run.mutate({ ops: [{ op: 'set_scene_kind', scene_id: scene.id, kind: v }], message: `${name}: tipo → ${label(SCENE_KIND_LABELS, v)}`, mark: true })}
      >
        {kinds.map((k) => (
          <option key={k} value={k}>
            {label(SCENE_KIND_LABELS, k)}
          </option>
        ))}
      </LabeledSelect>
      <LabeledSelect
        label="Câmera"
        value={scene.motion ?? ''}
        disabled={disabled}
        onChange={(v) =>
          run.mutate({
            ops: [{ op: 'set_scene_motion', scene_id: scene.id, motion: v || null }],
            message: `${name}: câmera → ${v ? label(CAMERA_LABELS, v) : 'automática'}`,
            mark: true,
          })
        }
      >
        {MOTIONS.map((m) => (
          <option key={m} value={m}>
            {m ? label(CAMERA_LABELS, m) : AUTO}
          </option>
        ))}
      </LabeledSelect>
      <LabeledSelect
        label="Transição de entrada"
        value={scene.transition_in ?? ''}
        disabled={disabled}
        onChange={(v) =>
          run.mutate({
            ops: [{ op: 'set_scene_transition', scene_id: scene.id, transition: v || null }],
            message: `${name}: transição → ${v ? label(TRANSITION_LABELS, v) : 'automática'}`,
            mark: true,
          })
        }
      >
        {TRANSITIONS.map((t) => (
          <option key={t} value={t}>
            {t ? label(TRANSITION_LABELS, t) : AUTO}
          </option>
        ))}
      </LabeledSelect>
    </>
  );

  const header = (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[13px] font-medium text-fg">{name}</span>
      {seconds !== null ? <span className="text-xs tabular-nums text-subtle">{formatSecs(seconds)}</span> : null}
      {scene.visual.variation ? <Badge title="Quantas vezes a imagem foi sorteada de novo">Variação {scene.visual.variation}</Badge> : null}
      {changed ? <Badge tone="info">Mudou</Badge> : null}
      {draft ? <Badge tone="warn">Não salvo</Badge> : null}
      {scene.locked ? (
        <Badge tone="warn" icon={Lock}>
          Travada
        </Badge>
      ) : null}
      {/* Nome fixo + aria-pressed: o estado vem do "pressionado"; o selo "Travada" mostra o estado na tela. */}
      <IconButton
        size="sm"
        className={`ml-auto ${scene.locked ? 'text-amber-200' : ''}`}
        label={`Travar a ${name.toLowerCase()}`}
        icon={scene.locked ? Lock : LockOpen}
        aria-pressed={scene.locked}
        busy={run.isPending && run.variables?.ops[0]?.op === 'set_lock'}
        disabled={(run.isPending && run.variables?.ops[0]?.op !== 'set_lock') || blocked}
        onClick={() =>
          run.mutate({ ops: [{ op: 'set_lock', ids: [scene.id], locked: !scene.locked }], message: `${name}: ${scene.locked ? 'destravada' : 'travada'}` })
        }
      />
    </div>
  );

  const promptBlock = (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={promptId} className="text-xs font-medium text-subtle">
          {scene.visual.media ? 'Prompt de reserva (vale se você tirar a mídia real)' : 'Prompt da imagem'}
        </label>
        {draft && !disabled ? <span className="text-xs text-subtle">Ctrl+Enter salva</span> : null}
      </div>
      <Textarea
        id={promptId}
        rows={3}
        value={prompt}
        disabled={scene.locked}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            savePrompt();
          }
        }}
        className="field-sizing-content min-h-20 text-[13px]"
      />
      {conflict ? (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="shrink-0 text-amber-200" />
          <span className="min-w-0 flex-1">O prompt mudou no servidor enquanto você editava.</span>
          <Button size="sm" variant="ghost" onClick={() => void discardPrompt('Usar o prompt do servidor?')}>
            Usar o do servidor
          </Button>
        </div>
      ) : null}
    </div>
  );

  const reuses = reuseNumber !== undefined;
  const isCard = CARD_KINDS.has(scene.kind);
  const ownImage = sceneNeedsImage(scene);
  const visualType = scene.visual.type;
  // O prompt só vale para imagem própria: some na cena que reaproveita e no cartão sem fundo (fica se houver rascunho).
  // Uma cena de imagem sem fundo (cartão que virou imagem) mostra o prompt: salvar pede a imagem.
  const showPrompt = Boolean(draft) || (!reuses && (visualType !== 'none' || !isCard));
  // "Outra imagem" só sorteia de novo a imagem de IA própria (reaproveitar e cartão não geram imagem).
  const canRegenerate = visualType === 'ai_image' && !reuses && ownImage;
  // Os valores que a imagem usa de fato: com "Outra imagem", o plano, a composição e a linguagem giram.
  const variant = rotateVariant(scene.visual.shot, scene.visual.composition, scene.visual.language, artLanguages ?? [], scene.visual.variation);
  const directed =
    ownImage && scene.visual.language
      ? [label(VISUAL_LANGUAGE_LABELS, variant.language), label(SHOT_LABELS, variant.shot), label(COMPOSITION_LABELS, variant.composition)].filter(
          Boolean,
        )
      : [];

  const visualInfo =
    isCard && !ownImage ? (
      <div className="flex flex-col items-start gap-1.5">
        <Badge tone="info" icon={LayoutTemplate}>
          Cartão (sem imagem nova)
        </Badge>
        {reuses ? null : (
          <span className="text-xs text-subtle">{scene.visual.media?.asset ? 'Fundo: a mídia real anexada.' : 'Fundo animado nas cores do estilo.'}</span>
        )}
      </div>
    ) : null;

  const reuseInfo = reuses ? (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[13px] text-muted">
      <Repeat2 size={14} aria-hidden="true" className="shrink-0 text-subtle" />
      {reuseNumber && reuseRootId ? (
        <button type="button" className="rounded-sm underline underline-offset-2 hover:text-fg" onClick={() => showScene(reuseRootId)}>
          Reaproveita a imagem da cena {reuseNumber}
        </button>
      ) : (
        <span className="text-amber-200">A imagem reaproveitada não existe mais: a cena usa o fundo animado.</span>
      )}
      {scene.focus && reuseNumber ? <span className="text-subtle">· foco {label(FOCUS_LABELS, scene.focus).toLowerCase()}</span> : null}
    </div>
  ) : null;

  const issueList = issues?.length ? (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {issues.map((issue, i) => (
        <li key={`${issue.code}-${i}`} className={`flex items-start gap-1.5 text-[13px] ${issue.severity === 'grave' ? 'text-red-200' : 'text-amber-100'}`}>
          <TriangleAlert size={14} aria-hidden="true" className={`mt-0.5 shrink-0 ${issue.severity === 'grave' ? 'text-red-300' : 'text-amber-200'}`} />
          <span className="min-w-0">
            <span className="sr-only">{label(REVIEW_SEVERITY_LABELS, issue.severity) || 'Aviso'}: </span>
            {issue.message}
          </span>
        </li>
      ))}
    </ul>
  ) : null;

  const buttons =
    showPrompt || canRegenerate || blockedReason ? (
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap gap-1.5 empty:hidden">
          {showPrompt ? (
            <Button size="sm" icon={Save} disabled={disabled || !draft} busy={run.isPending && run.variables?.savedPrompt !== undefined} onClick={savePrompt}>
              Salvar prompt
            </Button>
          ) : null}
          {draft ? (
            <Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => void discardPrompt('Desfazer a edição do prompt?')}>
              Desfazer
            </Button>
          ) : null}
          {canRegenerate ? (
            <Button
              size="sm"
              variant="ghost"
              icon={RefreshCw}
              disabled={disabled}
              onClick={() => run.mutate({ ops: [{ op: 'regenerate_scene', scene_id: scene.id }], message: `${name}: outra imagem`, mark: true })}
            >
              Outra imagem
            </Button>
          ) : null}
        </div>
        {blockedReason ? (
          <p className="text-[13px] text-amber-200">{blockedReason}</p>
        ) : canRegenerate ? (
          <p className="text-xs text-subtle">
            {scene.visual.language
              ? 'Imagem nova (prompt salvo ou "Outra imagem", que também troca o plano) é gerada na próxima prévia e usa a cota de imagens.'
              : 'Imagem nova (prompt salvo ou "Outra imagem") é gerada na próxima prévia e usa a cota de imagens.'}
          </p>
        ) : null}
      </div>
    ) : null;

  const thumb = (
    <SceneThumb
      scene={scene}
      index={index}
      src={src}
      primaryLang={primaryLang}
      short={short}
      loading={thumbLoading}
      reuseNumber={reuseNumber}
      mediaAsset={mediaAsset}
      onOpen={(s) => onOpenImage(s, index, s === mediaAsset ? (scene.visual.media?.title ?? '') : prompt)}
    />
  );

  return (
    <Card
      id={sceneDomId(scene.id)}
      tabIndex={-1}
      className={`@container flex min-w-0 flex-col gap-3 p-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${draft ? 'border-amber-300/45!' : ''}`}
    >
      {short ? (
        <div className="flex gap-3">
          {thumb}
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            {header}
            {selects}
          </div>
        </div>
      ) : (
        <>
          {thumb}
          {header}
        </>
      )}
      {issueList}
      {visualInfo}
      {reuseInfo}
      <CardSummary scene={scene} primaryLang={primaryLang} />
      <RealMediaPanel scene={scene} index={index} doc={doc} projectId={projectId} disabled={scene.locked || run.isPending} blockedReason={blockedReason} />
      <SceneVideoControl
        projectId={projectId}
        scene={scene}
        index={index}
        disabled={disabled}
        hasMedia={Boolean(mediaAsset) || Boolean(scene.visual.media)}
      />
      <PresenterChip projectId={projectId} doc={doc} scene={scene} short={short} disabled={disabled} />
      {directed.length ? <p className="text-xs text-subtle">{directed.join(' · ')}</p> : null}
      {showPrompt ? promptBlock : null}
      {short ? null : <div className="grid grid-cols-1 gap-2 @md:grid-cols-3">{selects}</div>}
      {buttons}
      <ErrorBox error={run.error} title="Não foi possível alterar a cena" onClose={() => run.reset()} />
    </Card>
  );
};

const SceneCard = memo(SceneCardImpl);

/** Quando terminou (ou começou) a última prévia/render concluída, em ms. */
const lastBuildOf = (detail: ProjectDetail) =>
  detail.jobs
    .filter((j) => j.status === 'done' && BUILD_KINDS.has(j.kind))
    .map((j) => Date.parse(j.started_at ?? j.created_at ?? ''))
    .filter((t) => !Number.isNaN(t))
    .reduce((a, b) => Math.max(a, b), 0);

/** Houve mudança no servidor (outra aba, agente da prévia, versão restaurada) depois do início da última prévia? */
const changedOnServerSince = (versions: Version[], since: number) =>
  since > 0 &&
  versions.some((v) => {
    const t = v.created_at ? Date.parse(v.created_at) : NaN;
    return !Number.isNaN(t) && t > since && v.author !== 'ai' && !NON_VISUAL_VERSION.test(v.message);
  });

export const ScenesTab: React.FC<{ detail: ProjectDetail; busy: boolean } & TabNavigation> = ({ detail, busy, goTo }) => {
  const { doc, style } = detail;
  const startJob = useStartJob(doc.id);
  const starting = useJobStarting(doc.id);
  const changes = useKeyedStore(sceneChanges, doc.id);
  const [zoom, setZoom] = useState<{ src: string; title: string; caption: string } | null>(null);
  const lastBuild = lastBuildOf(detail);
  const hasPreview = Boolean(detail.outputs.preview);
  const timeline = useQuery({
    // O caminho do timeline.json é sempre o mesmo: a hora da última prévia entra na chave para buscar as imagens novas.
    queryKey: ['timeline', doc.id, doc.primary_language, detail.outputs.preview?.langs?.[doc.primary_language]?.timeline, lastBuild],
    queryFn: () => api.get<TimelineLite>(`/api/projects/${doc.id}/timeline?lang=${doc.primary_language}`),
    enabled: hasPreview,
    retry: false,
    // Mantém as miniaturas antigas na tela enquanto as novas chegam.
    placeholderData: (prev) => prev,
  });
  const onOpenImage = useCallback(
    (src: string, index: number, prompt: string) => setZoom({ src: fileUrl(src), title: `Cena ${index}`, caption: prompt }),
    [],
  );
  const activeJobs = detail.jobs.filter(isActive);
  const action = nextStep(detail).action;
  // Número de exibição, dona da imagem reaproveitada e avisos da revisão de cada cena.
  const numbers = useMemo(() => sceneNumbers(doc), [doc]);
  const scenesById = useMemo(() => new Map(doc.scenes.map((s) => [s.id, s])), [doc]);
  const reuse = useMemo(() => {
    const out = new Map<string, { number: number | null; rootId: string | null }>();
    for (const s of doc.scenes) {
      // Cena com mídia real mostra a mídia dela: o `reuse_of` fica de reserva.
      if (!s.visual.reuse_of || s.visual.media) continue;
      const root = reuseRoot(scenesById, s);
      const number = root && root.id !== s.id ? (numbers.get(root.id) ?? null) : null;
      out.set(s.id, { number, rootId: number ? root!.id : null });
    }
    return out;
  }, [doc, numbers, scenesById]);
  const review = storyboardReviewOf(detail);
  const issuesByScene = useMemo(() => {
    const out = new Map<string, StoryboardIssue[]>();
    for (const issue of review?.issues ?? []) {
      if (!issue.scene_id || issue.severity === 'corrigido') continue;
      out.set(issue.scene_id, [...(out.get(issue.scene_id) ?? []), issue]);
    }
    return out;
  }, [review]);

  if (!doc.scenes.length) {
    const creating = activeJobs.find((j) => j.kind === 'storyboard' || (j.kind === 'autopilot' && doc.segments.length));
    if (creating) {
      return (
        <Card className="flex flex-col gap-4" role="status">
          <div className="flex items-start gap-3">
            <Spinner className="mt-1 text-accent" />
            <div className="min-w-0">
              <h2 className="font-display text-[15px] font-semibold text-fg">Criando as cenas…</h2>
              <p className="text-[13px] text-muted">{creating.message || 'Dividindo o roteiro em cenas e escrevendo o prompt de cada imagem.'}</p>
            </div>
          </div>
          <div className="grid gap-3 @3xl:grid-cols-2 @6xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-48 rounded-xl" />
            ))}
          </div>
        </Card>
      );
    }
    const gateScript = detail.project.settings.gates?.script ?? true;
    const needsApproval = doc.segments.length > 0 && !doc.script_approved && gateScript;
    const create = (
      <Button
        variant={needsApproval ? 'ghost' : 'secondary'}
        icon={LayoutGrid}
        busy={startJob.isPending}
        disabled={(busy || starting) && !startJob.isPending}
        title={busy ? BUSY_REASON : undefined}
        onClick={() => {
          if (!starting) startJob.mutate({ kind: 'storyboard' });
        }}
      >
        {needsApproval ? 'Criar as cenas sem aprovar' : 'Criar só as cenas'}
      </Button>
    );
    // A ação principal (âmbar) fica no banner "Próximo passo": aqui só caminhos alternativos.
    return (
      <Empty
        icon={LayoutGrid}
        title={!doc.segments.length ? 'Gere o roteiro primeiro' : needsApproval ? 'Aprove o roteiro primeiro' : 'Ainda não há cenas'}
        action={
          !doc.segments.length ? (
            goTo ? (
              <Button icon={FileText} onClick={() => goTo('script')}>
                Ir para o roteiro
              </Button>
            ) : null
          ) : (
            <div className="flex flex-col items-center gap-1.5">
              <div className="flex flex-wrap justify-center gap-2">
                {needsApproval && goTo ? (
                  <Button icon={FileText} onClick={() => goTo('script')}>
                    Revisar e aprovar o roteiro
                  </Button>
                ) : null}
                {create}
              </div>
              <span className="text-[13px] text-subtle">
                {busy
                  ? BUSY_REASON
                  : needsApproval
                    ? 'Criar as cenas agora pula a sua revisão do roteiro. Usa o Claude.'
                    : action.kind === 'continue'
                      ? 'Só divide o roteiro em cenas, sem narrar nem gerar imagens. Usa o Claude.'
                      : 'Usa o Claude.'}
              </span>
            </div>
          )
        }
      >
        {!doc.segments.length
          ? 'As cenas saem do roteiro: gere e aprove o roteiro antes.'
          : 'As cenas dividem o roteiro em partes e trazem o prompt de cada imagem. Depois você ajusta câmera, transição e imagem de cada uma.'}
        <ErrorBox error={startJob.error} className="mt-3 text-left" />
      </Empty>
    );
  }

  const short = isShort(style);
  const byId = new Map((timeline.data?.scenes ?? []).map((s) => [s.id, s]));
  const fps = timeline.data?.fps ?? 30;

  // Cenas alteradas (nesta sessão) depois do início da última prévia/render concluída.
  const changedIds = new Set(Object.entries(changes).filter(([, t]) => t > lastBuild).map(([id]) => id));
  const changedCount = doc.scenes.filter((s) => changedIds.has(s.id)).length;
  // Versões dos ramos A/B não mudam a prévia da principal.
  const serverChanged = changedOnServerSince(
    detail.versions.filter((v) => !v.branch || v.branch === 'main'),
    lastBuild,
  );
  const timelineMissing = timeline.error instanceof ApiError && timeline.error.status === 404;
  const noPreview = !hasPreview || timelineMissing;
  const timelineError = timeline.error && !timelineMissing ? timeline.error : null;
  const timelineLoading = hasPreview && timeline.isLoading;
  const previewRunning = activeJobs.some((j) => BUILD_KINDS.has(j.kind));
  // Cenas que a última prévia não tem (cenas refeitas pelo Claude, cuja versão é da IA e não conta em serverChanged).
  const newScenes = timeline.data && !timelineMissing ? doc.scenes.filter((s) => !byId.has(s.id)).length : 0;
  // Cenas esperando a conferência: a aprovação (no painel acima) é que gera as imagens, não o "Gerar prévia".
  const awaiting = awaitingScenes(detail);
  const showBuild = !awaiting && !previewRunning && !timelineLoading && (noPreview || changedCount > 0 || newScenes > 0 || serverChanged);
  const blockedReason = !style ? STYLE_MISSING_REASON : docJobRunning(detail) ? DOC_BUSY_REASON : null;

  let counter = 0;
  return (
    <div className="flex flex-col gap-6">
      <StoryboardReview detail={detail} busy={busy} numbers={numbers} onShowScene={showScene} />
      <VideoClipsPanel projectId={doc.id} disabled={Boolean(blockedReason) || busy} onShowScene={showScene} />
      <RealMediaFinder detail={detail} busy={busy} blockedReason={blockedReason} />
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-card/95 px-3 py-2.5 backdrop-blur-md">
        <MonitorPlay size={16} aria-hidden="true" className="shrink-0 text-muted" />
        <div className="min-w-0 flex-1 text-sm" role="status">
          {previewRunning ? (
            <span className="text-fg">Gerando a prévia… as imagens novas aparecem quando terminar.</span>
          ) : awaiting ? (
            <span className="text-fg">As imagens novas são geradas quando você aprovar as cenas.</span>
          ) : timelineLoading ? (
            <Skeleton className="h-5 w-64 max-w-full" />
          ) : noPreview ? (
            <span className="text-fg">As imagens aparecem depois da primeira prévia.</span>
          ) : changedCount ? (
            <span className="text-fg">
              {changedCount === 1 ? '1 cena alterada' : `${changedCount} cenas alteradas`} desde a última prévia.
            </span>
          ) : newScenes ? (
            <span className="text-fg">{newScenes === 1 ? '1 cena nova' : `${newScenes} cenas novas`} ainda sem prévia.</span>
          ) : serverChanged ? (
            <span className="text-fg">Há mudanças desde a última prévia.</span>
          ) : (
            <span className="text-muted">Mudanças aqui só refazem a cena alterada na próxima prévia.</span>
          )}
          {showBuild ? <span className="block text-[13px] text-subtle">A prévia usa a cota de voz e de imagens só no que mudou.</span> : null}
        </div>
        {showBuild ? (
          <Button
            size="sm"
            // Âmbar só quando a prévia é o próximo passo do projeto: a aba Cenas é dona da ação e a faixa
            // "Próximo passo" omite o botão dela (tabOwnsAction), então este é o único primário da tela.
            variant={tabOwnsAction(action, 'scenes') ? 'primary' : 'secondary'}
            icon={MonitorPlay}
            busy={startJob.isPending}
            disabled={(busy || starting) && !startJob.isPending}
            title={busy ? BUSY_REASON : undefined}
            onClick={() => {
              if (!starting) startJob.mutate({ kind: 'preview' });
            }}
          >
            {noPreview ? 'Gerar prévia' : 'Atualizar a prévia'}
          </Button>
        ) : null}
        <ErrorBox error={timelineError} title="Não foi possível carregar as imagens da prévia" onRetry={() => void timeline.refetch()} className="basis-full" />
        <ErrorBox error={startJob.error} title="Não foi possível gerar a prévia" className="basis-full" />
      </div>

      {doc.segments.map((seg, sIndex) => {
        const scenes = doc.scenes.filter((s) => s.segment_id === seg.id).sort((a, b) => a.part - b.part);
        if (!scenes.length) return null;
        const text = seg.text[doc.primary_language] ?? '';
        return (
          <section key={seg.id} aria-label={`Trecho ${sIndex + 1}`}>
            <div className="mb-2 flex items-baseline gap-2 text-[13px]">
              <span className="shrink-0 font-medium text-muted">Trecho {sIndex + 1}</span>
              <span className="line-clamp-2 text-subtle" title={text}>
                {text}
              </span>
            </div>
            <div className={`grid grid-cols-1 gap-3 @3xl:grid-cols-2 ${short ? '@7xl:grid-cols-3' : '@6xl:grid-cols-3'}`}>
              {scenes.map((scene) => {
                counter += 1;
                const t = byId.get(scene.id);
                const r = reuse.get(scene.id);
                return (
                  <SceneCard
                    key={scene.id}
                    scene={scene}
                    index={counter}
                    src={t?.src ?? null}
                    seconds={t ? t.durationInFrames / fps : null}
                    projectId={doc.id}
                    primaryLang={doc.primary_language}
                    short={short}
                    changed={changedIds.has(scene.id)}
                    thumbLoading={timelineLoading}
                    blockedReason={blockedReason}
                    reuseNumber={r ? r.number : undefined}
                    reuseRootId={r?.rootId ?? null}
                    issues={issuesByScene.get(scene.id)}
                    artLanguages={doc.art?.languages}
                    mediaAsset={shownMedia(scenesById, scene)?.asset ?? null}
                    doc={doc}
                    onOpenImage={onOpenImage}
                  />
                );
              })}
            </div>
          </section>
        );
      })}
      {zoom ? <ImageDialog src={zoom.src} title={zoom.title} caption={zoom.caption} onClose={() => setZoom(null)} /> : null}
    </div>
  );
};
