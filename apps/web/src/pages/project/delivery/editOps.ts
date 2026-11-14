// Descrição em linguagem humana das operações propostas pelo agente de edição.
import { MOOD_LABELS, type ProjectDoc } from "../../../api";
import {
  CAMERA_LABELS,
  CAPTION_POSITION_LABELS,
  CAPTION_STYLE_LABELS,
  GRADE_LABELS,
  label,
  type LabelMap,
  langLabel,
  MUSIC_SOURCE_LABELS,
  SCENE_KIND_LABELS,
  TEXT_ANIMATION_LABELS,
  TRANSITION_LABELS,
} from "../../../labels";
import { segRef } from "./shared";

export const OP_LABELS: Record<string, string> = {
  update_segment_text: "Reescrever narração",
  add_segment: "Novo trecho de narração",
  delete_segment: "Remover trecho",
  move_segment: "Mover trecho",
  update_scene_prompt: "Nova descrição da imagem",
  regenerate_scene: "Outra versão da imagem",
  set_scene_motion: "Movimento de câmera",
  set_scene_transition: "Transição",
  set_scene_kind: "Tipo de cena",
  set_scene_text: "Texto na tela",
  set_scene_look: "Cor e luz da cena",
  split_scene: "Dividir cena",
  merge_scene_with_next: "Juntar com a próxima cena",
  set_style: "Ajuste de estilo",
  set_music: "Música",
  set_lock: "Travar",
  set_title: "Título do projeto",
  set_metadata: "Título e descrição",
  set_thumbnail: "Capa",
};

/** Caminhos comuns do estilo, em português. */
const STYLE_PATHS: Record<
  string,
  { label: string; values?: LabelMap; unit?: string }
> = {
  "edit.captions.size": { label: "Tamanho da legenda" },
  "edit.captions.color": { label: "Cor da legenda" },
  "edit.captions.highlight": { label: "Cor de destaque da legenda" },
  "edit.captions.position": {
    label: "Posição da legenda",
    values: CAPTION_POSITION_LABELS,
  },
  "edit.captions.style": {
    label: "Estilo da legenda",
    values: CAPTION_STYLE_LABELS,
  },
  "edit.captions.font": { label: "Fonte da legenda" },
  "edit.captions.weight": { label: "Peso da fonte da legenda" },
  "edit.captions.uppercase": { label: "Legenda em maiúsculas" },
  "edit.captions.max_words_per_line": {
    label: "Palavras por linha da legenda",
  },
  "edit.captions.stroke": { label: "Contorno da legenda" },
  "edit.captions.stroke_color": { label: "Cor do contorno da legenda" },
  "edit.captions.background": { label: "Fundo da legenda" },
  "edit.text_cards.animation": {
    label: "Animação dos textos",
    values: TEXT_ANIMATION_LABELS,
  },
  "edit.text_cards.color": { label: "Cor dos textos na tela" },
  "edit.text_cards.font": { label: "Fonte dos textos na tela" },
  "edit.zoom_strength": { label: "Força do zoom" },
  "edit.transition_seconds": { label: "Duração das transições", unit: "s" },
  "edit.shot_seconds": { label: "Duração das tomadas" },
  "edit.effects": { label: "Efeitos visuais" },
  "edit.camera": { label: "Movimentos de câmera" },
  "edit.transitions": { label: "Transições" },
  "edit.progress_bar": { label: "Barra de progresso" },
  "edit.progress_color": { label: "Cor da barra de progresso" },
  "edit.watermark": { label: "Marca-d’água" },
  "audio.music.volume_db": { label: "Volume da música", unit: "dB" },
  "audio.music.mood": { label: "Clima da música", values: MOOD_LABELS },
  "audio.music.source": {
    label: "Fonte da música",
    values: MUSIC_SOURCE_LABELS,
  },
  "audio.music.ducking": { label: "Abaixar a música durante a fala" },
  "audio.sfx.volume_db": { label: "Volume dos efeitos sonoros", unit: "dB" },
  "audio.sfx.auto": { label: "Efeitos sonoros automáticos" },
  "audio.loudness_lufs": { label: "Volume geral", unit: "LUFS" },
  "voice.speed": { label: "Velocidade da voz" },
  "voice.voice": { label: "Voz" },
  "voice.style_prompt": { label: "Jeito de narrar" },
  "voice.gap_s": { label: "Pausa entre falas", unit: "s" },
};

const LOOK_LABELS: Record<string, string> = {
  brightness: "brilho",
  contrast: "contraste",
  saturation: "saturação",
  warmth: "temperatura",
  blur: "desfoque",
};

const num = (v: number) =>
  v.toLocaleString("pt-BR", { maximumFractionDigits: 2 });

const formatValue = (
  v: unknown,
  values?: LabelMap,
  unit?: string,
): string | null => {
  if (v === null || v === undefined || v === "") return "padrão";
  if (typeof v === "boolean") return v ? "sim" : "não";
  if (typeof v === "number") return `${num(v)}${unit ? ` ${unit}` : ""}`;
  if (typeof v === "string") return values ? label(values, v) : v;
  return null; // objetos e listas: só nos detalhes técnicos
};

export type SceneRef = { index: number; id: string };

export type HumanOp = {
  label: string;
  /** Cena alvo (clicável no player). */
  scene?: SceneRef;
  /** Outro alvo em texto ("Segmento 3", "Volume da música"). */
  target?: string;
  /** Citação de texto (narração, descrição da imagem). */
  quote?: string;
  /** Novo valor ou resumo das mudanças. */
  change?: string;
  reason?: string;
};

export const describeOp = (
  op: Record<string, unknown>,
  ctx: {
    doc: ProjectDoc;
    sceneIndex: (id: string) => number;
    trackName: (path: string) => string;
  },
): HumanOp => {
  const kind = String(op.op ?? "");
  const out: HumanOp = {
    label: OP_LABELS[kind] ?? "Mudança",
    reason: typeof op.reason === "string" && op.reason ? op.reason : undefined,
  };
  const seg = (id: unknown) => segRef(ctx.doc, id);
  const multiLang = ctx.doc.languages.length > 1;
  const langNote =
    typeof op.lang === "string" && multiLang ? ` (${langLabel(op.lang)})` : "";

  if (typeof op.scene_id === "string" && op.scene_id) {
    const index = ctx.sceneIndex(op.scene_id);
    if (index >= 0) out.scene = { index, id: op.scene_id };
    else out.target = "Cena removida ou nova";
  }
  if (typeof op.segment_id === "string" && op.segment_id) {
    const s = seg(op.segment_id).nome;
    out.target = s.charAt(0).toUpperCase() + s.slice(1);
  }

  switch (kind) {
    case "update_segment_text":
    case "set_scene_text":
      out.quote = String(op.text ?? "");
      if (langNote) out.change = `Idioma${langNote}`;
      break;
    case "add_segment":
      out.quote = String(op.text ?? "");
      out.target = op.after_segment_id
        ? `Depois ${seg(op.after_segment_id).de}`
        : "No início";
      break;
    case "move_segment":
      out.change = op.after_segment_id
        ? `Para depois ${seg(op.after_segment_id).de}`
        : "Para o início";
      break;
    case "update_scene_prompt":
    case "split_scene":
      if (op.prompt) out.quote = String(op.prompt);
      break;
    case "set_scene_motion":
      out.change = op.motion
        ? label(CAMERA_LABELS, String(op.motion))
        : "Movimento padrão do estilo";
      break;
    case "set_scene_transition":
      out.change = op.transition
        ? label(TRANSITION_LABELS, String(op.transition))
        : "Transição padrão do estilo";
      break;
    case "set_scene_kind":
      out.change = `${label(SCENE_KIND_LABELS, String(op.kind ?? ""))}${typeof op.number === "number" ? ` · número ${op.number}` : ""}`;
      break;
    case "set_scene_look": {
      const parts = Object.entries(LOOK_LABELS)
        .filter(([k]) => typeof op[k] === "number")
        .map(([k, l]) => {
          const v = op[k] as number;
          return `${l} ${v > 0 ? "+" : ""}${num(v)}`;
        });
      if (typeof op.grade === "string" && op.grade)
        parts.push(`cor: ${label(GRADE_LABELS, op.grade)}`);
      out.change = parts.join(" · ") || undefined;
      break;
    }
    case "set_style": {
      const path = String(op.path ?? "");
      const known = STYLE_PATHS[path];
      out.target = known?.label ?? "Ajuste do estilo do vídeo";
      const v = formatValue(op.value, known?.values, known?.unit);
      out.change = v ?? "novo valor (veja os detalhes técnicos)";
      break;
    }
    case "set_music": {
      const parts: string[] = [];
      if (op.enabled === false) parts.push("desligar a música");
      if (op.enabled === true) parts.push("ligar a música");
      if (typeof op.mood === "string" && op.mood)
        parts.push(`clima: ${label(MOOD_LABELS, op.mood)}`);
      if (typeof op.volume_db === "number")
        parts.push(`volume ${num(op.volume_db)} dB`);
      if (typeof op.track === "string")
        parts.push(
          op.track ? `faixa: ${ctx.trackName(op.track)}` : "faixa automática",
        );
      if (op.start_segment)
        parts.push(`começa ${seg(op.start_segment).em}`);
      out.change = parts.join(" · ") || undefined;
      break;
    }
    case "set_lock": {
      const ids = Array.isArray(op.ids) ? op.ids : [];
      out.change = `${op.locked === false ? "Destravar" : "Travar"} ${ids.length} ${ids.length === 1 ? "item" : "itens"}`;
      if (op.locked === false) out.label = "Destravar";
      break;
    }
    case "set_title":
      out.quote = String(op.title ?? "");
      break;
    case "set_metadata":
      if (op.title) out.quote = String(op.title);
      out.change = `Idioma: ${langLabel(String(op.lang ?? ""))}`;
      break;
    case "set_thumbnail":
      if (op.text) out.quote = String(op.text);
      if (op.regenerate) out.change = "Gerar outra capa";
      break;
    default:
      break;
  }
  return out;
};
