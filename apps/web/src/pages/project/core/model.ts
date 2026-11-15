// Regras puras da página do projeto: etapa atual, próxima ação, aba inicial, estimativas e textos.

import {
  JOB_LABELS,
  type Job,
  type LocalizedText,
  type ProjectDetail,
  type ProjectDoc,
  type Scene,
  type SceneCard,
  type StoryboardReview,
  type StyleSpec,
} from '../../../api';
import { langLabel } from '../../../labels';
import type { StepState } from '../../../ui';
import type { ProjectTab } from '../types';

export const isActive = (job: Job) => job.status === 'queued' || job.status === 'running';

/** Tipos de job que o endpoint /api/projects/{id}/jobs aceita (dá para tentar de novo pela página). */
export const RETRYABLE_KINDS = new Set(['script', 'storyboard', 'translate', 'metadata', 'preview', 'render', 'autopilot']);

/**
 * Jobs que leem o documento no início e gravam o documento inteiro no fim: uma edição salva no meio
 * seria desfeita quando eles terminam.
 */
export const DOC_WRITING_KINDS = new Set(['script', 'storyboard', 'translate', 'metadata', 'autopilot']);

/** Há um job em andamento que vai regravar o documento do projeto? */
export const docJobRunning = (detail: ProjectDetail) => detail.jobs.some((j) => isActive(j) && DOC_WRITING_KINDS.has(j.kind));

/** Motivo (visível) para não editar enquanto um job regrava o documento. */
export const DOC_BUSY_REASON = 'Aguarde o trabalho em andamento: ele vai gravar uma versão nova do roteiro e desfaria esta edição.';

/** Motivo (visível) para não editar quando o estilo do projeto foi removido (o servidor recusa as operações). */
export const STYLE_MISSING_REASON = 'O estilo deste projeto foi removido. Escolha outro estilo para editar.';

/** Nomes dos jobs para a tela (sem "Storyboard"/"Render"). */
export const JOB_TEXT: Record<string, string> = { ...JOB_LABELS, storyboard: 'Criação das cenas', render: 'Vídeo final', metadata: 'Título e descrição' };

/** Título da falha de um job, com artigo ("A prévia falhou"). */
const FAILURE_TITLES: Record<string, string> = {
  script: 'O roteiro falhou',
  storyboard: 'A criação das cenas falhou',
  translate: 'A tradução falhou',
  metadata: 'O título e a descrição falharam',
  preview: 'A prévia falhou',
  render: 'O vídeo final falhou',
  autopilot: 'O piloto automático falhou',
  publish_youtube: 'O envio ao YouTube falhou',
};

export const failureTitle = (kind: string) => FAILURE_TITLES[kind] ?? `${JOB_TEXT[kind] ?? 'A tarefa'} falhou`;

/** Jobs que gastam a cota de voz e de imagens (quando ela acaba, tentar de novo falha outra vez). */
export const USES_MEDIA_QUOTA = new Set(['preview', 'render', 'autopilot']);

/** Primeira linha não vazia da mensagem de erro de um job. */
export const errorFirstLine = (job: Job) => (job.error || job.message || '').split('\n').find((l) => l.trim())?.trim() ?? '';

/** Custo de um job, dito perto do botão que o dispara. */
export const jobCost = (kind: string): string | null => {
  if (['script', 'storyboard', 'translate', 'metadata'].includes(kind)) return 'Usa o Claude.';
  if (kind === 'preview') return 'Usa a cota de imagens (a narração da prévia sai da voz local, grátis).';
  if (kind === 'render') return 'Grava a narração com a voz do vídeo final: usa a cota ou os créditos dela.';
  if (kind === 'autopilot') return 'Usa o Claude, a cota de imagens e a da voz do vídeo final.';
  return null;
};

/* ------------------------------------------------------------------ */
/* Validade da prévia aprovada e do vídeo final                        */
/* ------------------------------------------------------------------ */

const jobSteps = (job: Job): string[] => {
  const steps = job.result?.steps;
  return Array.isArray(steps) ? steps.filter((s): s is string => typeof s === 'string') : [];
};

const finishedAt = (job: Job) => {
  const t = Date.parse(job.finished_at ?? '');
  return Number.isNaN(t) ? 0 : t;
};

const lastDoneAt = (detail: ProjectDetail, pred: (job: Job) => boolean) =>
  detail.jobs.filter((j) => j.status === 'done' && pred(j)).reduce((max, j) => Math.max(max, finishedAt(j)), 0);

export type Freshness = {
  /** Quando terminou a prévia mais recente (ms; 0 se ela não está na lista de jobs). */
  lastPreviewAt: number;
  /** Quando terminou o vídeo final mais recente (ms; 0 se ele não está na lista de jobs). */
  lastFinalAt: number;
  /** Existe vídeo final e ele é mais novo que a última prévia (o servidor nunca apaga o vídeo antigo). */
  finalFresh: boolean;
  /** A aprovação vale para a prévia atual (veio depois da última prévia, ou o vídeo final está em dia). */
  previewApprovedFresh: boolean;
};

/**
 * O servidor não limpa `preview_approved` nem o último vídeo final quando o roteiro é refeito ou uma prévia nova
 * é gerada: a validade sai dos horários dos jobs e das versões.
 */
export const freshness = (detail: ProjectDetail): Freshness => {
  const { doc, outputs } = detail;
  const hasScenes = doc.scenes.length > 0;
  const lastPreviewAt = lastDoneAt(detail, (j) => j.kind === 'preview' || (j.kind === 'autopilot' && jobSteps(j).includes('preview')));
  const lastFinalAt = lastDoneAt(detail, (j) => j.kind === 'render' || (j.kind === 'autopilot' && jobSteps(j).includes('render')));
  const finalFresh = hasScenes && Boolean(outputs.final) && (!lastPreviewAt || lastFinalAt >= lastPreviewAt);
  const approval = detail.versions.find((v) => v.message.startsWith('Prévia aprovada'));
  const approvedAt = approval?.created_at ? Date.parse(approval.created_at) : 0;
  const approvalAfterPreview = !lastPreviewAt || (!Number.isNaN(approvedAt) && approvedAt >= lastPreviewAt);
  const previewApprovedFresh = hasScenes && Boolean(outputs.preview) && doc.preview_approved && (finalFresh || approvalAfterPreview);
  return { lastPreviewAt, lastFinalAt, finalFresh, previewApprovedFresh };
};

/* ------------------------------------------------------------------ */
/* Etapas e próximo passo                                              */
/* ------------------------------------------------------------------ */

export type StepId = 'script' | 'scenes' | 'preview' | 'video' | 'publish';

export const STEP_LABELS: Record<StepId, string> = {
  script: 'Roteiro',
  scenes: 'Cenas',
  preview: 'Prévia',
  video: 'Vídeo',
  publish: 'Publicação',
};

const STEP_ORDER: StepId[] = ['script', 'scenes', 'preview', 'video', 'publish'];

export const STEP_TAB: Record<StepId, ProjectTab> = {
  script: 'script',
  scenes: 'scenes',
  preview: 'preview',
  video: 'output',
  publish: 'output',
};

/** Em que etapa cada tipo de job trabalha (para marcar a etapa com erro). */
const JOB_STEP: Record<string, StepId> = {
  script: 'script',
  storyboard: 'scenes',
  translate: 'scenes',
  metadata: 'publish',
  preview: 'preview',
  render: 'video',
  publish_youtube: 'publish',
};

export type NextAction =
  | { kind: 'job' }
  | { kind: 'generate_script' }
  | { kind: 'approve_script' }
  | { kind: 'continue' }
  | { kind: 'approve_scenes' }
  | { kind: 'preview' }
  | { kind: 'approve_preview' }
  | { kind: 'render' }
  | { kind: 'publish' }
  | { kind: 'done' };

export type NextStep = {
  current: StepId;
  steps: { id: StepId; label: string; state: StepState }[];
  action: NextAction;
  title: string;
  /** O que acontece, sem o custo (o custo vai em `cost`, perto do botão que gasta). */
  description: string;
  cost?: string;
};

/**
 * Paradas do piloto automático. Projeto sem a chave: roteiro e prévia valem true; as cenas valem false
 * (a parada das cenas é nova e só vem ligada nos projetos criados depois dela).
 */
export const gatesOf = (detail: ProjectDetail) => {
  const g = detail.project.settings.gates ?? {};
  return { script: g.script ?? true, storyboard: g.storyboard ?? false, preview: g.preview ?? true };
};

/**
 * As cenas esperam a conferência do usuário antes das imagens: há cenas, a parada está ligada e elas não foram
 * aprovadas. Vale também com uma prévia antiga (cenas refeitas ou estilo trocado depois dela): o piloto automático
 * para nas cenas do mesmo jeito.
 */
export const awaitingScenes = (detail: ProjectDetail) =>
  detail.doc.scenes.length > 0 && gatesOf(detail).storyboard && !detail.doc.storyboard_approved;

/**
 * Cenas dirigidas que perderam a direção de arte (o estilo foi trocado e as cenas não foram refeitas): o build recusa
 * gerar essas imagens com o prompt antigo (`scenes_without_direction` no build).
 */
export const directionMissing = (doc: ProjectDoc): number =>
  doc.art
    ? 0
    : doc.scenes.filter((s) => Boolean(s.visual.language) && sceneNeedsImage(s) && !s.visual.asset && s.visual.type !== 'stock').length;

/** Aviso (visível) quando as cenas dirigidas ficaram sem direção de arte. */
export const DIRECTION_MISSING_REASON = 'O estilo mudou: refaça as cenas antes de aprovar. Nenhuma imagem é gerada com a direção antiga.';

/** Falha que ainda é "a última coisa que aconteceu" no projeto (o job mais recente falhou). */
export const latestFailure = (detail: ProjectDetail): Job | null => {
  const latest = detail.jobs[0];
  return latest && latest.status === 'failed' ? latest : null;
};

/**
 * Próximo passo do projeto, calculado só a partir dos dados.
 * `showFailure: false` tira a marca de erro da trilha (a falha foi dispensada pelo usuário).
 */
export const nextStep = (
  detail: ProjectDetail,
  opts: {
    showFailure?: boolean;
    /** Imagens que a próxima prévia gera de fato (com o cache, GET /plan); sem ela, o título diz "até N". */
    plannedImages?: number;
  } = {},
): NextStep => {
  const { doc, outputs, publications } = detail;
  const gates = gatesOf(detail);
  const hasScript = doc.segments.length > 0;
  const hasScenes = doc.scenes.length > 0;
  const preview = hasScenes && Boolean(outputs.preview);
  const { finalFresh, previewApprovedFresh } = freshness(detail);
  const sceneCheck = awaitingScenes(detail);
  // Cenas esperando conferência: as etapas seguintes voltam a ficar por fazer (a prévia e o vídeo vão mudar).
  const scenesOk = hasScenes && !sceneCheck;

  const done: Record<StepId, boolean> = {
    script: hasScript && (doc.script_approved || hasScenes || !gates.script),
    scenes: scenesOk,
    preview: scenesOk && preview && (previewApprovedFresh || finalFresh),
    video: scenesOk && finalFresh,
    publish: scenesOk && finalFresh && publications.length > 0,
  };
  const current = STEP_ORDER.find((id) => !done[id]) ?? 'publish';

  const failure = opts.showFailure === false ? null : latestFailure(detail);
  const failedStep: StepId | null = failure ? (JOB_STEP[failure.kind] ?? current) : null;

  const steps = STEP_ORDER.map((id) => ({
    id,
    label: STEP_LABELS[id],
    state: (id === failedStep ? 'error' : done[id] ? 'done' : id === current ? 'current' : 'todo') as StepState,
  }));

  const toFinal = !gates.preview;
  // Com a parada das cenas, o piloto só cria as cenas e espera a conferência (não gasta imagens).
  const stopAtScenes = gates.storyboard;
  const pipelineNote = stopAtScenes
    ? 'Cria as cenas e para para você conferir antes de gerar as imagens.'
    : toFinal
      ? 'Cria as cenas, narra, monta a prévia e renderiza o vídeo final.'
      : 'Cria as cenas, narra e monta a prévia.';
  // A prévia narra com a voz local (grátis); a voz do vídeo final só é gravada ao renderizar.
  const localPreview = detail.style?.voice.preview?.local ?? true;
  const pipelineCost = stopAtScenes
    ? 'Usa o Claude.'
    : toFinal
      ? 'Usa o Claude, a cota de imagens e a da voz do vídeo final.'
      : localPreview
        ? 'Usa o Claude e a cota de imagens (a narração da prévia é local, grátis).'
        : 'Usa o Claude e a cota de voz e de imagens.';
  const mediaCost = localPreview ? 'Usa a cota de imagens (a narração da prévia é local, grátis).' : 'Usa a cota de voz e de imagens.';
  const renderCost = 'Usa a cota ou os créditos da voz do vídeo final.';

  const base = { current, steps };
  if (detail.jobs.some(isActive)) {
    return { ...base, action: { kind: 'job' }, title: 'Trabalhando no seu vídeo', description: 'Você pode sair desta página: o progresso continua.' };
  }
  if (!hasScript) {
    return {
      ...base,
      action: { kind: 'generate_script' },
      title: 'Comece pelo roteiro',
      description: 'O Claude escreve o roteiro a partir do tema e das orientações.',
      cost: 'Usa o Claude.',
    };
  }
  if (!doc.script_approved && !hasScenes && gates.script) {
    return {
      ...base,
      action: { kind: 'approve_script' },
      title: 'Revise e aprove o roteiro',
      description: `Leia o roteiro, ajuste o que quiser e aprove. ${pipelineNote}`,
      cost: pipelineCost,
    };
  }
  if (!hasScenes) {
    const next = stopAtScenes ? 'cenas' : toFinal ? 'cenas, prévia e vídeo final' : 'cenas e prévia';
    return {
      ...base,
      action: { kind: 'continue' },
      title: doc.script_approved ? `Roteiro aprovado. Próximo: ${next}` : `Roteiro pronto. Próximo: ${next}`,
      description: pipelineNote,
      cost: pipelineCost,
    };
  }
  if (sceneCheck) {
    // Com a conta do build, o número é exato; sem ela, o documento só dá o máximo (não sabe o que já está no cache).
    const exact = opts.plannedImages !== undefined;
    const newImages = exact ? opts.plannedImages! : sceneCounts(doc).newImages;
    const images = newImages === 1 ? '1 imagem nova' : `${newImages} imagens novas`;
    // Sem `cost`: fora da aba Cenas o botão só leva até lá; a cota já vai no título.
    return {
      ...base,
      action: { kind: 'approve_scenes' },
      title: newImages
        ? `Confira as cenas: ${exact ? '' : 'até '}${images}, ~${quotaMinutes(newImages)} min de cota`
        : 'Confira as cenas: nenhuma imagem nova',
      description: toFinal
        ? 'Veja a direção de arte, os cartões e os avisos antes de gastar a cota. Ao aprovar, o app gera as imagens, monta a prévia e renderiza o vídeo final.'
        : 'Veja a direção de arte, os cartões e os avisos antes de gastar a cota. Ao aprovar, o app gera as imagens e monta a prévia.',
    };
  }
  if (!preview) {
    return {
      ...base,
      action: { kind: 'preview' },
      title: 'Gere a prévia',
      description: localPreview
        ? 'Narra o roteiro com a voz local e gera as imagens que faltam, para você conferir o conteúdo.'
        : 'Narra o roteiro e gera as imagens que faltam.',
      cost: mediaCost,
    };
  }
  if (!finalFresh && !previewApprovedFresh) {
    return {
      ...base,
      action: { kind: 'approve_preview' },
      title: outputs.final ? 'Prévia nova: assista e aprove' : 'Assista à prévia e aprove',
      description: outputs.final
        ? 'O vídeo final que existe é de antes desta prévia. Ao aprovar, o app grava a narração com a voz do vídeo final e gera de novo o MP4, a thumbnail e as legendas.'
        : 'Ao aprovar, o app grava a narração com a voz do vídeo final e gera o MP4, a thumbnail e as legendas.',
      cost: renderCost,
    };
  }
  if (!finalFresh) {
    return {
      ...base,
      action: { kind: 'render' },
      title: 'Prévia aprovada: falta renderizar o vídeo final',
      description: 'Grava a narração com a voz do vídeo final e gera o MP4, a thumbnail e as legendas.',
      cost: renderCost,
    };
  }
  if (!publications.length) {
    return { ...base, action: { kind: 'publish' }, title: 'Vídeo pronto', description: 'Baixe o vídeo, gere os pacotes de publicação ou agende o envio.' };
  }
  return { ...base, action: { kind: 'done' }, title: 'Vídeo publicado ou agendado', description: 'Acompanhe as datas na Agenda.' };
};

/** Aba que abre quando a URL não diz qual (ex.: projeto com prévia pronta abre na prévia). */
export const defaultTab = (detail: ProjectDetail): ProjectTab => {
  const { doc, outputs } = detail;
  if (!doc.segments.length || (!doc.script_approved && !doc.scenes.length)) return 'script';
  if (!doc.scenes.length || awaitingScenes(detail)) return 'scenes';
  if (freshness(detail).finalFresh) return 'output';
  if (outputs.preview) return 'preview';
  return 'scenes';
};

/* ------------------------------------------------------------------ */
/* Cenas: imagem própria, reaproveitamento e cartões                   */
/* ------------------------------------------------------------------ */

/** Cenas sem imagem própria em tela cheia, desenhadas pelo renderer (CARD_KINDS de core/project/document.py). */
export const CARD_KINDS = new Set(['text', 'title', 'count', 'document', 'quote', 'stat', 'compare']);

/** Cota estimada de cada imagem nova (Codex), em segundos. */
export const IMAGE_QUOTA_SECONDS = 35;

/** Minutos de cota de imagens, arredondados para cima. */
export const quotaMinutes = (images: number) => Math.ceil((images * IMAGE_QUOTA_SECONDS) / 60);

/** A cena pede uma imagem gerada (a mesma regra de `scene_needs_image` no build). */
export const sceneNeedsImage = (scene: Scene): boolean => {
  const v = scene.visual;
  if (v.reuse_of) return false;
  if (v.type === 'none' || v.type === 'upload') return false;
  if (scene.kind === 'text' && !v.prompt) return false;
  return Boolean(v.prompt || v.query);
};

/**
 * Cena dona da imagem que `scene` mostra: ela mesma, ou o fim da cadeia de `reuse_of` em até `maxDepth` saltos.
 * null com referência solta (cena apagada), ciclo ou cadeia longa demais: a cena fica com o fundo animado.
 */
export const reuseRoot = (byId: ReadonlyMap<string, Scene>, scene: Scene, maxDepth = 3): Scene | null => {
  let current = scene;
  const seen = new Set([scene.id]);
  for (let i = 0; i <= maxDepth; i += 1) {
    const target = current.visual.reuse_of;
    if (!target) return current;
    const next = byId.get(target);
    if (!next || seen.has(next.id)) return null;
    seen.add(next.id);
    current = next;
  }
  return null;
};

/** Número de exibição de cada cena ("Cena 3"), na ordem da aba Cenas (trechos em ordem, partes em ordem). */
export const sceneNumbers = (doc: ProjectDoc): Map<string, number> => {
  const out = new Map<string, number>();
  let n = 0;
  for (const seg of doc.segments) {
    const scenes = doc.scenes.filter((s) => s.segment_id === seg.id).sort((a, b) => a.part - b.part);
    for (const s of scenes) out.set(s.id, (n += 1));
  }
  return out;
};

export type SceneCounts = { scenes: number; newImages: number; reused: number; cards: number };

const SHOT_ROTATION = ['wide', 'close', 'low_angle', 'medium', 'detail', 'aerial', 'over_shoulder', 'top_down', 'pov'];
const COMPOSITION_ROTATION = ['center', 'left_third', 'right_third', 'upper_half', 'lower_half'];

const stepIn = (sequence: string[], current: string | null | undefined, steps: number) => {
  const base = current ? Math.max(0, sequence.indexOf(current)) : 0;
  return sequence[(base + steps) % sequence.length];
};

/**
 * Plano, composição e linguagem que a imagem dirigida usa de fato: "Outra imagem" (variation) gira os três, como
 * `rotate_variant` em core/styles/visual.py.
 */
export const rotateVariant = (
  shot: string | null | undefined,
  composition: string | null | undefined,
  language: string | null | undefined,
  languages: string[],
  variation: number,
): { shot: string | null; composition: string | null; language: string | null } => {
  if (variation <= 0) return { shot: shot ?? null, composition: composition ?? null, language: language ?? null };
  let lang = language ?? null;
  const pool = languages.filter((l, i) => l && languages.indexOf(l) === i);
  if (variation >= 2 && pool.length > 1) {
    const base = lang && pool.includes(lang) ? pool.indexOf(lang) : 0;
    lang = pool[(base + Math.floor(variation / 2)) % pool.length];
  }
  return { shot: stepIn(SHOT_ROTATION, shot, variation), composition: stepIn(COMPOSITION_ROTATION, composition, variation), language: lang };
};

const translatableValue = (text: string) => /\p{L}/u.test(text) || /\d[.,]\d/.test(text);

/**
 * Cenas com texto de tela (sobreposição ou cartão) no idioma principal que ainda falta em `lang`: o piloto traduz só
 * esses textos antes da prévia (como `missing_screen_texts` em authoring.py).
 */
export const screenTextsMissing = (doc: ProjectDoc, lang: string): number => {
  const source = doc.primary_language;
  if (lang === source) return 0;
  const has = (t: LocalizedText | undefined, l: string) => Boolean((t?.[l] ?? '').trim());
  const missing = (t: LocalizedText | undefined, value = false) =>
    has(t, source) && !has(t, lang) && (!value || translatableValue(t![source]));
  return doc.scenes.filter((s) => {
    const card = s.card;
    if (missing(s.overlay_text)) return true;
    if (!card) return false;
    return (
      missing(card.value, true) ||
      missing(card.label) ||
      missing(card.headline) ||
      missing(card.stamp) ||
      (card.items ?? []).some((item) => missing(item.label) || missing(item.value, true))
    );
  }).length;
};

/** Contagem da curadoria sobre o documento atual (mesmas definições de settings.storyboard_review.counts). */
export const sceneCounts = (doc: ProjectDoc): SceneCounts => ({
  scenes: doc.scenes.length,
  newImages: doc.scenes.filter((s) => sceneNeedsImage(s) && !s.visual.asset).length,
  reused: doc.scenes.filter((s) => Boolean(s.visual.reuse_of)).length,
  cards: doc.scenes.filter((s) => CARD_KINDS.has(s.kind)).length,
});

const pickText = (texts: LocalizedText | undefined, lang: string, fallback: string): string =>
  (texts?.[lang] ?? '').trim() || (texts?.[fallback] ?? '').trim();

export type ResolvedCard = {
  value?: string;
  label?: string;
  items?: { label: string; value: string }[];
  highlight?: number;
  headline?: string;
  stamp?: string;
  source?: string;
};

/** Cartão no idioma `lang` (reserva: `fallback`), só com o que está preenchido (como `SceneCard.resolved`). */
export const resolveCard = (card: SceneCard | null | undefined, lang: string, fallback: string): ResolvedCard => {
  if (!card) return {};
  const out: ResolvedCard = {};
  const value = pickText(card.value, lang, fallback);
  if (value) out.value = value;
  const labelText = pickText(card.label, lang, fallback);
  if (labelText) out.label = labelText;
  const items = (card.items ?? [])
    .map((item) => ({ label: pickText(item.label, lang, fallback), value: pickText(item.value, lang, fallback) }))
    .filter((item) => item.label || item.value);
  if (items.length) {
    out.items = items;
    if (card.highlight != null && card.highlight >= 0 && card.highlight < items.length) out.highlight = card.highlight;
  }
  const headline = pickText(card.headline, lang, fallback);
  if (headline) out.headline = headline;
  const stamp = pickText(card.stamp, lang, fallback);
  if (stamp) out.stamp = stamp;
  if (card.source?.trim()) out.source = card.source.trim();
  return out;
};

/**
 * Revisão da curadoria gravada pelo job das cenas, ou null (projeto antigo ou formato inesperado).
 *
 * A revisão é um retrato das cenas daquele job: os avisos de cenas que não existem mais (versão restaurada, cena
 * juntada ou apagada) saem. `stale` diz que nenhuma cena da revisão existe mais (as cenas são de outra versão).
 */
export const storyboardReviewOf = (detail: ProjectDetail): (StoryboardReview & { stale: boolean }) | null => {
  const r = detail.project.settings.storyboard_review;
  if (!r || typeof r !== 'object' || !r.counts || typeof r.counts !== 'object' || !Array.isArray(r.issues)) return null;
  const ids = new Set(detail.doc.scenes.map((s) => s.id));
  const withScene = r.issues.filter((i) => i.scene_id);
  const stale = withScene.length > 0 && !withScene.some((i) => ids.has(i.scene_id!));
  return { ...r, issues: stale ? [] : r.issues.filter((i) => !i.scene_id || ids.has(i.scene_id)), stale };
};

/* ------------------------------------------------------------------ */
/* Duração e formato                                                   */
/* ------------------------------------------------------------------ */

export const countWords = (text: string) => text.split(/\s+/).filter(Boolean).length;

/** Palavras por minuto da narração (vem do estilo; o tipo StyleSpec ainda não declara o campo). */
export const wordsPerMinute = (style: StyleSpec | null, lang: string): number => {
  const script = style?.script as { words_per_minute?: Record<string, number> } | undefined;
  const wpm = script?.words_per_minute?.[lang];
  return typeof wpm === 'number' && wpm > 0 ? wpm : 150;
};

/** Duração estimada da narração em segundos (palavras, velocidade da voz e pausas entre trechos). */
export const estimateSeconds = (style: StyleSpec | null, lang: string, texts: string[]): number => {
  const words = texts.reduce((n, t) => n + countWords(t), 0);
  if (!words) return 0;
  const speed = style?.voice?.speed && style.voice.speed > 0 ? style.voice.speed : 1;
  const gap = style?.voice?.gap_s ?? 0.25;
  const filled = texts.filter((t) => t.trim()).length;
  return (words / (wordsPerMinute(style, lang) * speed)) * 60 + gap * Math.max(0, filled - 1);
};

export type DurationCheck = { tone: 'good' | 'accent' | 'bad'; message: string; ratio: number } | null;

/** Compara a duração estimada com a meta do estilo. */
export const checkDuration = (style: StyleSpec | null, seconds: number): DurationCheck => {
  const d = style?.format?.duration;
  if (!d || !d.target_s) return null;
  const ratio = seconds / d.target_s;
  // Passar da meta é um aviso (âmbar), não um bloqueio: nada impede gerar a prévia ou o vídeo.
  if (d.max_s != null && seconds > d.max_s) {
    return {
      tone: 'accent',
      ratio,
      message: `${formatDuration(seconds - d.max_s)} acima do máximo do estilo (${formatDuration(d.max_s)}). Corte alguns trechos.`,
    };
  }
  if (d.min_s != null && seconds < d.min_s) {
    return { tone: 'accent', ratio, message: `Abaixo do mínimo do estilo (${formatDuration(d.min_s)}).` };
  }
  return { tone: 'good', ratio, message: 'Dentro da duração do estilo.' };
};

/** "48 s", "2 min 05 s". */
export const formatDuration = (seconds: number) => {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return s ? `${m} min ${String(s).padStart(2, '0')} s` : `${m} min`;
};

export const isShort = (style: StyleSpec | null) => style?.format?.type === 'short' || style?.format?.aspect === '9:16';

/* ------------------------------------------------------------------ */
/* Falhas                                                              */
/* ------------------------------------------------------------------ */

/** Dica de como resolver uma falha, a partir da mensagem de erro. */
export const failureHint = (error: string | null | undefined): string => {
  const text = (error ?? '').toLowerCase();
  if (/interrompid|fechado durante/.test(text)) return 'O app foi fechado no meio do trabalho. Tente de novo: o que já estava pronto é reaproveitado.';
  if (/cota|quota|429|rate.?limit|resource.?exhausted/.test(text))
    return 'A cota grátis do provedor acabou por hoje. Tente de novo amanhã ou configure outro provedor em Configurações.';
  if (/claude|llm|anthropic/.test(text))
    return 'Confira se o Claude Code está com login feito (rode "claude auth login" no terminal) ou espere o limite do plano voltar.';
  if (/ffmpeg|remotion|chrom/.test(text)) return 'A montagem do vídeo falhou. Tente de novo; se continuar, confira as dependências em Configurações.';
  if (/rede|network|timeout|tempo esgotado|connection/.test(text)) return 'Parece um problema de conexão. Confira a internet e tente de novo.';
  return 'Tente de novo. Se continuar falhando, confira os provedores em Configurações.';
};

/* ------------------------------------------------------------------ */
/* Versões                                                             */
/* ------------------------------------------------------------------ */

/** Mensagem de versão sem ids técnicos: tira o provedor entre parênteses e troca códigos de idioma por nomes. */
export const humanizeVersionMessage = (message: string): string => {
  let text = message.replace(/\s*\([a-z0-9_.-]+\)\s*$/i, '');
  text = text
    .replace(/^Cenas planejadas \([a-z0-9_.-]+\)/i, 'Cenas planejadas')
    .replace(/^Storyboard gerado/, 'Cenas geradas')
    .replace(/^Segmento travado/, 'Trecho travado')
    .replace(/^Segmento destravado/, 'Trecho destravado')
    .replace(/^Metadados \(título, descrição, tags\)/, 'Título, descrição e tags gerados');
  text = text.replace(/^Tradução: (.+)$/, (_m, langs: string) => `Tradução: ${langs.split(/,\s*/).map((l) => langLabel(l)).join(', ')}`);
  // Fala do agente de edição ("Vou aumentar as legendas, escurecer 1 cena(s)…"): sem primeira pessoa e sem "(s)".
  text = text.replace(/(\d+)\s+(\p{L}+)\(s\)/gu, (_m, n: string, word: string) => (Number(n) === 1 ? `${n} ${word}` : `${n} ${word}s`));
  text = text.replace(/^Vou\s+/, '');
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export type MilestoneKind = 'approval' | 'script' | 'scenes' | 'restore' | 'edit';
export type Milestone = { kind: MilestoneKind; tone: 'good' | 'info'; label: string } | null;

/** Marco de uma versão (a tela mostra só o ícone e a cor; o rótulo vai para leitores de tela e para o title). */
export const versionMilestone = (message: string, author?: string): Milestone => {
  if (message.startsWith('Roteiro aprovado')) return { kind: 'approval', tone: 'good', label: 'Aprovação' };
  if (message.startsWith('Prévia aprovada')) return { kind: 'approval', tone: 'good', label: 'Aprovação' };
  if (message.startsWith('Cenas aprovadas')) return { kind: 'approval', tone: 'good', label: 'Aprovação' };
  if (message.startsWith('Roteiro gerado')) return { kind: 'script', tone: 'info', label: 'Roteiro novo' };
  if (message.startsWith('Storyboard gerado') || message.startsWith('Cenas planejadas')) return { kind: 'scenes', tone: 'info', label: 'Cenas novas' };
  if (message.startsWith('Voltou para a versão')) return { kind: 'restore', tone: 'info', label: 'Restauração' };
  if (author === 'ai-edit') return { kind: 'edit', tone: 'info', label: 'Edição' };
  return null;
};
