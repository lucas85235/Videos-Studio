// Contas puras da edição no renderer (sem React nem Remotion): planos, câmera, tempo e duração dos eventos.
// Ficam fora dos componentes para o teste rodar no Node (`backend/tests/test_renderer_fx.py`): só `import type` aqui.
//
// Quadros relativos ao início da cena, como no contrato resolvido de `types.ts` (ADR 0004, fundação).
import type { CaptionWord, FxType, Prov, ShotEasing, ShotFraming, TimelineFx, TimelineScene, TimelineShot } from '../../types';

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const clamp01 = (v: number) => clamp(v, 0, 1);
const num = (v: unknown, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

// --- curvas ----------------------------------------------------------------------------------------------------

export const easeInOutCubic = (t: number) => {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
};

/** Curvas do movimento do plano (`TimelineShot.easing`); `snap` chega em ~22% do plano e segura. */
export const EASE: Record<ShotEasing, (t: number) => number> = {
  linear: (t) => clamp01(t),
  outCubic: (t) => 1 - (1 - clamp01(t)) ** 3,
  inOutQuad: (t) => {
    const x = clamp01(t);
    return x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2;
  },
  snap: (t) => 1 - (1 - clamp01(t / 0.22)) ** 3,
};

export const isEasing = (v: unknown): v is ShotEasing => typeof v === 'string' && Object.prototype.hasOwnProperty.call(EASE, v);

/** Número de 0 a 1, estável, a partir de um texto (fase do tremor, lado da rotação). */
export const hash01 = (text: string) => {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
};

// --- parâmetros dos eventos (chaves de `FxEvent.params` no backend) -------------------------------------------------

export type FxParams = Record<string, unknown>;

export const pNum = (params: FxParams | undefined, key: string, fallback: number) => num(params?.[key], fallback);
export const pStr = (params: FxParams | undefined, key: string, fallback = '') => {
  const v = params?.[key];
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : fallback;
};
export const pBool = (params: FxParams | undefined, key: string, fallback: boolean) => {
  const v = params?.[key];
  return typeof v === 'boolean' ? v : fallback;
};

/** Retângulo [x, y, w, h] de 0 a 1 no quadro (o visual como enquadrado para o vídeo). */
export type Rect = [number, number, number, number];

const toRect = (vals: number[]): Rect | null => {
  if (vals.length < 4 || vals.slice(0, 4).some((v) => !Number.isFinite(v))) return null;
  const x = clamp01(vals[0]);
  const y = clamp01(vals[1]);
  const w = clamp(vals[2], 0, 1 - x);
  const h = clamp(vals[3], 0, 1 - y);
  return w > 0 && h > 0 ? [x, y, w, h] : null;
};

/**
 * Retângulos do evento: `rect` ([x, y, w, h]) ou `rects` — lista achatada de 4·n números (o `FxParam` do backend só
 * aceita lista de números) ou lista de listas.
 */
export const pRects = (params: FxParams | undefined): Rect[] => {
  const out: Rect[] = [];
  for (const key of ['rect', 'rects']) {
    const v = params?.[key];
    if (!Array.isArray(v)) continue;
    if (v.every((x) => Array.isArray(x))) {
      for (const r of v as unknown[][]) {
        const rect = toRect(r.map((x) => Number(x)));
        if (rect) out.push(rect);
      }
    } else {
      const flat = v.map((x) => Number(x));
      for (let i = 0; i + 3 < flat.length; i += 4) {
        const rect = toRect(flat.slice(i, i + 4));
        if (rect) out.push(rect);
      }
    }
  }
  return out;
};

// --- planos --------------------------------------------------------------------------------------------------------

/** Janela de um plano na cena: `[from, to)`. `shot` null: o visual da própria cena antes do primeiro plano. */
export type ShotSlot = { shot: TimelineShot | null; index: number; from: number; to: number };

/**
 * Planos em ordem, sem buraco nem sobreposição: cada um vai até o próximo (ou o fim da cena). O `dur` do contrato não
 * deixa buraco preto; plano com o mesmo `from` vale o último. Começando depois do quadro 0, o trecho anterior é o
 * visual da própria cena. Sem planos: lista vazia (a cena desenha o caminho de sempre).
 */
export const shotSlots = (shots: TimelineShot[] | undefined, frames: number): ShotSlot[] => {
  if (!shots?.length || frames <= 0) return [];
  const byFrom = new Map<number, { shot: TimelineShot; index: number }>();
  shots.forEach((shot, index) => {
    const from = clamp(Math.round(num(shot.from, 0)), 0, frames - 1);
    byFrom.set(from, { shot, index });
  });
  const starts = [...byFrom.keys()].sort((a, b) => a - b);
  const slots: ShotSlot[] = [];
  if (starts[0] > 0) slots.push({ shot: null, index: -1, from: 0, to: starts[0] });
  starts.forEach((from, i) => {
    const { shot, index } = byFrom.get(from)!;
    slots.push({ shot, index, from, to: i + 1 < starts.length ? starts[i + 1] : frames });
  });
  return slots;
};

/** Quadros de corte dentro da cena (início de cada plano depois do 0). */
export const cutsOf = (slots: ShotSlot[]) => slots.filter((s) => s.from > 0).map((s) => s.from);

export const nextCut = (at: number, cuts: number[], frames: number) => cuts.find((c) => c > at) ?? frames;

/** Enquadramento do plano: escala e canto de cima à esquerda da janela (0 a 1), sem passar da borda. */
export const framingBox = (framing: ShotFraming | null | undefined) => {
  const scale = clamp(num(framing?.scale, 1), 1, 4);
  const x = clamp01(num(framing?.x, 0.5));
  const y = clamp01(num(framing?.y, 0.5));
  const win = 1 / scale;
  return { scale, left: clamp(x - win / 2, 0, 1 - win), top: clamp(y - win / 2, 0, 1 - win) };
};

/**
 * Enquadramento do plano que mantém a manchete guardada (`keep`, fração do contêiner: a da captura ou o maior bloco de
 * letras da imagem) inteira dentro da janela, com margem: a janela anda até contê-la e, quando ela não cabe, a escala
 * desce até caber (nunca abaixo de 1). QA: o plano 1,25x da página de pré-venda cortava "PRE-ORDER" na borda direita
 * ("PRE-ORDEI"). Sem manchete ou no quadro inteiro, o mesmo enquadramento.
 */
export const keptFraming = (framing: ShotFraming | null | undefined, keep: Rect | null, margin = 0.03): ShotFraming | null | undefined => {
  if (!framing || !keep) return framing;
  const { scale } = framingBox(framing);
  if (scale <= 1.0001) return framing;
  const s = Math.max(1, Math.min(scale, 1 / (Math.max(keep[2], keep[3]) + 2 * margin)));
  const win = 1 / s;
  const axis = (want: number, a: number, len: number) => {
    const lo = a + len + margin - win;
    const hi = a - margin;
    const start = lo <= hi ? clamp(want - win / 2, lo, hi) : a + len / 2 - win / 2;
    return clamp(start, 0, 1 - win) + win / 2;
  };
  const x = axis(num(framing.x, 0.5), keep[0], keep[2]);
  const y = axis(num(framing.y, 0.5), keep[1], keep[3]);
  if (Math.abs(s - scale) < 1e-6 && Math.abs(x - framingBox(framing).left - win / 2) < 1e-6 && Math.abs(y - framingBox(framing).top - win / 2) < 1e-6) {
    return framing;
  }
  return { ...framing, scale: Math.round(s * 1e4) / 1e4, x: Math.round(x * 1e4) / 1e4, y: Math.round(y * 1e4) / 1e4 };
};

/** `transform` CSS (origem 0 0) do enquadramento; null no quadro inteiro. */
export const framingTransform = (framing: ShotFraming | null | undefined) => {
  const { scale, left, top } = framingBox(framing);
  if (scale <= 1.0001) return null;
  return `translate(${-left * scale * 100}%, ${-top * scale * 100}%) scale(${scale})`;
};

// --- espaço dos retângulos ----------------------------------------------------------------------------------------
//
// O backend manda `rect` (`MediaRef.rects`/`PoolItem.rects`) no espaço do ARQUIVO original (0 a 1 da imagem inteira).
// Só o renderer sabe o recorte final: o `cover` na proporção do vídeo (com o `objectPosition` do foco), a caixa da
// imagem inteira no modo desfocado e o enquadramento do plano. `params.space = 'frame'` diz que o retângulo já está
// no quadro do fundo (timeline escrita à mão) e pula a conversão.

/**
 * Como o arquivo aparece no contêiner do fundo: proporção (largura ÷ altura), posição do recorte do `cover` (0 a 1,
 * o `objectPosition`) e, no modo desfocado, a caixa (0 a 1 do contêiner) da imagem inteira. Null: desconhecido
 * (o retângulo vale como está, como se o arquivo tivesse a proporção do vídeo).
 */
export type ImageBox = {
  aspect: number;
  pos: [number, number];
  front?: { left: number; top: number; width: number; height: number } | null;
};

/** Caixa de um plano com imagem ou clipe próprio (`cover` centrado, sem foco): precisa do `aspect` do plano. */
export const shotBox = (shot: TimelineShot | null | undefined): ImageBox | null => {
  const aspect = num(shot?.aspect, 0);
  return aspect > 0 ? { aspect, pos: [0.5, 0.5] } : null;
};

const clipRect = (x: number, y: number, w: number, h: number): Rect | null => {
  const x0 = Math.max(0, x);
  const y0 = Math.max(0, y);
  const x1 = Math.min(1, x + w);
  const y1 = Math.min(1, y + h);
  return x1 - x0 > 1e-4 && y1 - y0 > 1e-4 ? [x0, y0, x1 - x0, y1 - y0] : null;
};

/** Retângulo do arquivo no contêiner do fundo (antes do enquadramento do plano); null quando cai fora do recorte. */
export const rectToContainer = (r: Rect, box: ImageBox | null | undefined, videoAspect: number): Rect | null => {
  const [x, y, w, h] = r;
  if (!box || !(box.aspect > 0) || !(videoAspect > 0)) return clipRect(x, y, w, h);
  const f = box.front;
  if (f) return clipRect(f.left + x * f.width, f.top + y * f.height, w * f.width, h * f.height);
  if (box.aspect > videoAspect) {
    // Mais larga que o vídeo: aparece uma faixa vertical (fração `v` da largura).
    const v = videoAspect / box.aspect;
    const left = (1 - v) * clamp01(box.pos[0]);
    return clipRect((x - left) / v, y, w / v, h);
  }
  if (box.aspect < videoAspect) {
    // Mais alta (captura de página): aparece uma faixa horizontal.
    const v = box.aspect / videoAspect;
    const top = (1 - v) * clamp01(box.pos[1]);
    return clipRect(x, (y - top) / v, w, h / v);
  }
  return clipRect(x, y, w, h);
};

/** Retângulo do contêiner na tela depois do enquadramento do plano (escala e janela de `framingBox`). */
export const rectThroughFraming = (r: Rect, framing: ShotFraming | null | undefined): Rect | null => {
  const { scale, left, top } = framingBox(framing);
  return clipRect((r[0] - left) * scale, (r[1] - top) * scale, r[2] * scale, r[3] * scale);
};

/** Retângulos do evento no contêiner do fundo (anotações presas à imagem). */
export const fxRects = (params: FxParams | undefined, box: ImageBox | null | undefined, videoAspect: number): Rect[] => {
  const rects = pRects(params);
  if (pStr(params, 'space') === 'frame') return rects;
  return rects.map((r) => rectToContainer(r, box, videoAspect)).filter((r): r is Rect => r !== null);
};

/** O que está no fundo num quadro da cena: a caixa da imagem e o enquadramento do plano. */
export type FxSpace = { box: ImageBox | null; framing: ShotFraming | null };

/** Caixa da imagem de um plano: a do arquivo dele (imagem ou clipe próprio) ou, no plano `self`, a da cena. */
export const slotBox = (slot: ShotSlot, sceneBox: ImageBox | null): ImageBox | null =>
  slot.shot?.video || slot.shot?.src ? shotBox(slot.shot) : sceneBox;

/** Caixa e enquadramento do fundo no quadro `frame` da cena (sem planos: a imagem da cena, quadro inteiro). */
export const spaceAt = (slots: ShotSlot[], frame: number, sceneBox: ImageBox | null): FxSpace => {
  const slot = slots.find((s) => frame >= s.from && frame < s.to) ?? slots[slots.length - 1];
  if (!slot) return { box: sceneBox, framing: null };
  return { box: slotBox(slot, sceneBox), framing: slot.shot?.framing ?? null };
};

/**
 * Eventos com o retângulo do `zoom_rect` já na TELA (o CameraRig trabalha na tela): arquivo → contêiner do plano em
 * que o evento começa → enquadramento desse plano. Zoom cujo retângulo cai fora do recorte fica sem retângulo (não
 * mexe na câmera). Sem `zoom_rect`, devolve a mesma lista.
 */
export const cameraFx = (
  fxs: TimelineFx[] | undefined,
  spaceAt: (frame: number) => FxSpace,
  videoAspect: number,
): TimelineFx[] | undefined => {
  if (!fxs?.some((fx) => fx.type === 'zoom_rect')) return fxs;
  return fxs.map((fx) => {
    if (fx.type !== 'zoom_rect') return fx;
    const rect = pRects(fx.params)[0];
    if (!rect) return fx;
    const space = spaceAt(Math.round(num(fx.from, 0)));
    const inContainer = pStr(fx.params, 'space') === 'frame' ? rect : rectToContainer(rect, space.box, videoAspect);
    const onScreen = inContainer ? rectThroughFraming(inContainer, space.framing) : null;
    const { rect: _r, rects: _rs, ...rest } = fx.params ?? {};
    return { ...fx, params: onScreen ? { ...rest, rect: onScreen, space: 'frame' } : { ...rest, space: 'frame' } };
  });
};

// --- clipe do plano -------------------------------------------------------------------------------------------------

/** O clipe nunca fica mais lento que isto para cobrir a cena; o que faltar, o último quadro segura. */
export const MIN_CLIP_RATE = 0.5;

/**
 * Teto do trecho de material oficial (espelho de `SourcePolicy.official_max_clip_s`, ADR 0004 §4): plano `official`
 * com `video` e sem `seconds` nunca toca mais que isto (rede de segurança; quem manda é o `seconds` do backend).
 */
export const OFFICIAL_MAX_CLIP_S = 4.0;

/**
 * Segundos úteis do clipe do plano a partir de `clipIn`: o `seconds` do backend; sem ele, o teto do oficial; sem os
 * dois, desconhecido (o clipe toca até o fim do plano).
 */
export const shotSeconds = (shot: TimelineShot | null | undefined): number | undefined => {
  const given = num(shot?.seconds, 0);
  if (given > 0) return given;
  return shot?.prov === 'official' ? OFFICIAL_MAX_CLIP_S : undefined;
};

/**
 * Velocidade e quantos quadros da cena o clipe toca. Clipe mais longo que a cena: velocidade normal, corta no fim.
 * Mais curto: fica mais lento (até MIN_CLIP_RATE) e, se ainda faltar, congela no último quadro.
 */
export const clipTiming = (clipSeconds: number | undefined, sceneFrames: number, fps: number) => {
  if (!clipSeconds || clipSeconds <= 0) return { rate: 1, playFrames: sceneFrames };
  const clipFrames = Math.max(1, Math.floor(clipSeconds * fps));
  if (clipFrames >= sceneFrames) return { rate: 1, playFrames: sceneFrames };
  const rate = Math.max(MIN_CLIP_RATE, clipFrames / sceneFrames);
  // Um quadro de folga: o último quadro do arquivo às vezes não decodifica.
  const playFrames = Math.min(sceneFrames, Math.max(1, Math.floor(clipFrames / rate) - 1));
  return { rate, playFrames };
};

/**
 * Velocidade e quadros tocados de um clipe com velocidade pedida. `hold`: na velocidade pedida e congela no fim do
 * trecho; sem `hold`: a conta do `clipTiming` no tempo do clipe já acelerado (só desacelera para cobrir).
 */
export const shotClipTiming = (clipSeconds: number | undefined, frames: number, fps: number, speed = 1, hold = false) => {
  const base = speed > 0 ? speed : 1;
  if (hold) {
    if (!clipSeconds || clipSeconds <= 0) return { rate: base, playFrames: frames };
    const clipFrames = Math.max(1, Math.floor((clipSeconds * fps) / base));
    return { rate: base, playFrames: Math.min(frames, Math.max(1, clipFrames - 1)) };
  }
  const { rate, playFrames } = clipTiming(clipSeconds ? clipSeconds / base : clipSeconds, frames, fps);
  return { rate: rate * base, playFrames };
};

// --- eventos ---------------------------------------------------------------------------------------------------------

/**
 * Duração padrão (s) do evento que chega sem `dur` (ou com 0). Espelha o `FX_DEFAULT_MS` do backend
 * (`core/edit_resolve.py`), que sempre preenche `dur` quando `dur_ms` é None: a tabela daqui só vale para timeline
 * escrita à mão. O teste `test_fx_defaults_match_backend` confere as duas.
 */
export const FX_DEFAULT_SECONDS: Record<FxType, number> = {
  punch: 0.6,
  shake: 0.4,
  zoom_rect: 1.5,
  freeze: 0.7,
  flash: 0.15,
  text_pop: 1.1,
  meme: 1.0,
  sfx: 0.5,
  highlight: 1.4,
  arrow: 1.4,
  circle: 1.4,
  counter: 1.5,
  music_drop: 0.45,
  speed: 1.0,
  whip: 0.25,
  pip: 2.0,
};

/**
 * Seguram até o próximo corte (ADR 0004, 3.9: o punch "sobe, segura e solta no próximo corte"). O `dur` que vem do
 * backend vale como mínimo: quando o corte cai até HOLD_TO_CUT_S depois do fim dele, o efeito segura até o corte e
 * solta seco; com o corte mais longe (ou cena sem planos e longa), solta no fim do `dur` com rampa curta.
 */
const HOLDS_TO_CUT = new Set<FxType>(['punch', 'zoom_rect', 'highlight', 'arrow', 'circle']);
export const HOLD_TO_CUT_S = 1.5;

/** Só áudio (mixado no FFmpeg a partir de `timeline.sfx` e `timeline.music_drops`): o renderer não desenha. */
export const AUDIO_FX = new Set<FxType>(['sfx', 'music_drop']);
/** Mexem na câmera do fundo (CameraRig). */
export const CAMERA_FX = new Set<FxType>(['punch', 'shake', 'zoom_rect', 'whip']);
/** Presos à imagem: andam com a câmera e o enquadramento (dentro do fundo). */
export const ANCHORED_FX = new Set<FxType>(['highlight', 'arrow', 'circle']);
/** Por cima do fundo, na tela (FxLayer). */
export const SCREEN_FX = new Set<FxType>(['text_pop', 'meme', 'flash', 'freeze', 'counter', 'pip']);
/** Terminam no próximo corte: a imagem muda e o efeito não vale mais. */
const ENDS_AT_CUT = new Set<FxType>([...CAMERA_FX, ...ANCHORED_FX]);

/** Meme na variante "congela + zoom": congela o fundo e dá um punch. */
export const isFreezeZoomMeme = (fx: TimelineFx) => fx.type === 'meme' && pStr(fx.params, 'variant') === 'freeze_zoom';

export type FxContext = { fps: number; frames: number; cuts: number[] };
export type Span = { from: number; to: number };

/**
 * Janela `[from, to)` do evento na cena. `dur` como o backend manda (sempre preenchido); ausente ou 0, a duração
 * padrão do tipo. Câmera e anotações presas à imagem nunca passam do próximo corte; as de HOLDS_TO_CUT seguram até
 * ele quando ele cai logo depois (ver HOLD_TO_CUT_S).
 */
export const fxSpan = (fx: TimelineFx, ctx: FxContext): Span => {
  const last = Math.max(0, ctx.frames - 1);
  const from = clamp(Math.round(num(fx.from, 0)), 0, last);
  const given = num(fx.dur, 0);
  const dur = given > 0 ? given : (FX_DEFAULT_SECONDS[fx.type] ?? 1) * ctx.fps;
  let to = Math.min(ctx.frames, from + Math.max(1, Math.round(dur)));
  if (ENDS_AT_CUT.has(fx.type) && fx.type !== 'whip') {
    const cut = nextCut(from, ctx.cuts, ctx.frames);
    if (HOLDS_TO_CUT.has(fx.type) && cut - to <= Math.round(HOLD_TO_CUT_S * ctx.fps)) to = cut;
    to = Math.min(to, cut);
  }
  return { from, to: Math.max(from + 1, to) };
};

/** Evento preso à imagem visível num quadro da cena: o quadro local (`f`, 0 no gatilho) e se ele termina num corte. */
export type AnchoredItem = { fx: TimelineFx; index: number; span: Span; f: number; len: number; last: boolean };

/**
 * Marca-texto, seta e círculo visíveis no quadro REAL da cena (não o tempo do fundo depois de um congelamento ou de
 * uma rampa): a anotação entra na palavra-gatilho mesmo depois de um `freeze`.
 */
export const anchoredAt = (fxs: TimelineFx[] | undefined, frame: number, ctx: FxContext): AnchoredItem[] => {
  const out: AnchoredItem[] = [];
  (fxs ?? []).forEach((fx, index) => {
    if (!ANCHORED_FX.has(fx.type)) return;
    const span = fxSpan(fx, ctx);
    if (frame < span.from || frame >= span.to) return;
    const last = span.to >= ctx.frames || ctx.cuts.includes(span.to);
    out.push({ fx, index, span, f: frame - span.from, len: span.to - span.from, last });
  });
  return out;
};

// --- tela ------------------------------------------------------------------------------------------------------------

/** Faixa da legenda na tela (fração da altura, de cima para baixo), com uma margem; null quando não há legenda. */
export type Band = [number, number];

/**
 * Onde fica o meme de canto (fração da altura): `bottom` é a distância da borda de baixo e `height` a altura do
 * adesivo. Embaixo, do lado pedido; com legenda visível em cima dessa faixa, ele sobe para acima da legenda (ou desce
 * para baixo dela, o que couber maior) e encolhe se precisar: o adesivo nunca cobre a fala.
 */
export const cornerStickerBox = (portrait: boolean, caption: Band | null) => {
  const height = portrait ? 0.28 : 0.42;
  const bottom = portrait ? 0.24 : 0.06;
  const top = 1 - bottom - height;
  if (!caption || caption[1] <= top || caption[0] >= 1 - bottom) return { bottom, height };
  const ceiling = 0.06;
  const above = Math.max(0, caption[0] - ceiling);
  const below = Math.max(0, 1 - bottom - caption[1]);
  if (above >= below) return { bottom: 1 - caption[0], height: Math.min(height, above) };
  return { bottom, height: Math.min(height, below) };
};

/**
 * Fileiras de itens que quebram como o `flex-wrap` centrado (palavras do texto cinético, do título): `widths` em px,
 * `gap` entre itens da mesma fileira. Devolve a largura de cada fileira e o índice do 1º item dela. Item mais largo que
 * `maxWidth` fica sozinho na fileira (como no navegador).
 */
export const flowRows = (widths: number[], gap: number, maxWidth: number): { width: number; start: number }[] => {
  const rows: { width: number; start: number }[] = [];
  widths.forEach((w, i) => {
    const row = rows[rows.length - 1];
    if (row && row.width + gap + w <= maxWidth + 1e-6) row.width += gap + w;
    else rows.push({ width: w, start: i });
  });
  return rows;
};

// --- carimbo solto -----------------------------------------------------------------------------------------------------
//
// O carimbo girado (BOATO, CONFIRMADO) fica num canto da tela. Antes ele ia sempre para a mesma altura e caía em
// cima do título, da palavra do texto cinético ou do trecho marcado da captura ("SEM PC, DEMU UU BETA"). Agora ele vai
// para o primeiro lugar livre de todo texto conhecido na tela, dentro da área segura.

/** Área segura do carimbo (fração da tela): a caixa dele nunca chega a menos disto das bordas de cima e de baixo. */
export const STAMP_SAFE = 0.05;
/** Respiro entre o carimbo e o texto que ele desvia (fração da altura). */
export const STAMP_GAP = 0.015;

export type StampSide = 'left' | 'right';
/**
 * Lugar do carimbo: o lado (a borda dele fica a 7% da borda da tela, como sempre) e a altura do centro. Quando nenhum
 * dos dois cantos tem lugar livre, `x` é o centro (fração da largura) num ponto livre de qualquer lugar da tela e
 * `scale` (< 1) o tamanho menor que coube.
 */
export type StampSpot = { side: StampSide; y: number; x?: number; scale?: number };
/** Medidas do carimbo na tela (fração): centro horizontal de cada lado e meia caixa já com a rotação. */
export type StampGeom = { cx: Record<StampSide, number>; halfW: number; halfH: number };

/** Centro horizontal (fração) e escala do carimbo no lugar `spot`. */
const stampCenter = (spot: StampSpot, g: StampGeom) => ({ cx: spot.x ?? g.cx[spot.side], s: spot.scale ?? 1 });

/** Caixa [x, y, w, h] (fração da tela) do carimbo girado no lugar `spot`. */
export const stampRect = (spot: StampSpot, g: StampGeom): Rect => {
  const { cx, s } = stampCenter(spot, g);
  return [cx - g.halfW * s, spot.y - g.halfH * s, g.halfW * 2 * s, g.halfH * 2 * s];
};

const overlapArea = (a: Rect, b: Rect) => {
  const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
  return w > 1e-6 && h > 1e-6 ? w * h : 0;
};

/** Custo de uma caixa: sobreposição com texto da TELA (título, legenda, avatar: nunca) pesa muito mais que com letra
 * da IMAGEM (manchete da captura, logo: evita quando dá; a detecção pode errar e a tela pode não ter lugar livre). */
export const HARD_WEIGHT = 1000;

/**
 * Peso de tempo de uma caixa de letras da imagem: a fração da cena em que ela está na tela (o plano dela). O plano
 * reenquadrado de 0,9 s numa cena de 5 s pesa menos que o quadro inteiro que fica o resto do tempo. Sem peso: 1.
 */
const RECT_TIME = new WeakMap<Rect, number>();
export const timeWeighted = (rects: Rect[], share: number): Rect[] => {
  const w = clamp(share, 0, 1);
  if (w < 1) for (const r of rects) RECT_TIME.set(r, w);
  return rects;
};
const timeOf = (o: Rect) => RECT_TIME.get(o) ?? 1;

/** Peso da letra da imagem pela altura do bloco: cobrir a manchete ou o logo (bloco alto, letra grande) é pior que
 * cobrir uma linha do corpo da matéria. */
const softWeight = (o: Rect) => (1 + Math.min(3, o[3] * 10)) * timeOf(o);

export const boxCost = (box: Rect, hard: Rect[], soft: Rect[]) =>
  hard.reduce((sum, o) => sum + overlapArea(box, o) * timeOf(o), 0) * HARD_WEIGHT +
  soft.reduce((sum, o) => sum + overlapArea(box, o) * softWeight(o), 0);

/** Sobreposição (área, fração da tela) com texto duro abaixo da qual a caixa só encosta nele. */
const TOUCH = 2e-4;

/** Fração da área de `box` coberta pelas caixas `rects` (somadas; pode passar de 1 com caixas sobrepostas). */
export const coveredShare = (box: Rect, rects: Rect[]) =>
  box[2] * box[3] > 0 ? rects.reduce((sum, o) => sum + overlapArea(box, o), 0) / (box[2] * box[3]) : 0;

const uniqSorted = (vals: number[]) => [...new Set(vals.map((v) => Math.round(v * 1e4) / 1e4))];

/** Um lugar na tela (centro, fração) e o custo dele (`boxCost`; 0 = livre). */
export type FreeSpot = { x: number; y: number; cost: number };

/**
 * Lugar em 2D (centro, fração) de uma caixa de meia largura `hw` e meia altura `hh`: o preferido quando livre; senão o
 * ponto livre mais perto dele, encostado (com respiro) na borda de algum texto, em qualquer x de `xr` e y de `yr`
 * (limites do CENTRO); sem ponto livre, o de menor custo (e, empatado, o mais perto do preferido). `hard`: texto da
 * tela e a manchete da imagem (nunca coberto quando há saída); `soft`: as outras letras da imagem.
 */
export const placeFree = (
  pref: [number, number],
  hw: number,
  hh: number,
  hard: Rect[],
  soft: Rect[],
  xr: [number, number],
  yr: [number, number],
  gap = STAMP_GAP,
): FreeSpot => {
  const fx = (v: number) => clamp(v, xr[0], Math.max(xr[0], xr[1]));
  const fy = (v: number) => clamp(v, yr[0], Math.max(yr[0], yr[1]));
  const px = fx(pref[0]);
  const py = fy(pref[1]);
  const xs = [px, xr[0], xr[1]];
  const ys = [py, yr[0], yr[1]];
  for (const o of [...hard, ...soft]) {
    xs.push(o[0] - hw - gap, o[0] + o[2] + hw + gap);
    ys.push(o[1] - hh - gap, o[1] + o[3] + hh + gap);
  }
  const box = (x: number, y: number): Rect => [x - hw, y - hh, hw * 2, hh * 2];
  let best: FreeSpot & { d: number } = { x: px, y: py, cost: boxCost(box(px, py), hard, soft), d: 0 };
  if (best.cost === 0) return { x: px, y: py, cost: 0 };
  for (const x of uniqSorted(xs.map(fx))) {
    for (const y of uniqSorted(ys.map(fy))) {
      const cost = boxCost(box(x, y), hard, soft);
      const d = Math.hypot(x - px, y - py);
      if (cost < best.cost - 1e-9 || (Math.abs(cost - best.cost) <= 1e-9 && d < best.d)) best = { x, y, cost, d };
    }
  }
  return { x: best.x, y: best.y, cost: best.cost };
};

/** Margem lateral mínima (fração da largura) do carimbo fora dos dois cantos de sempre. */
const STAMP_SIDE_MIN = 0.03;
/** Tamanhos que o carimbo tenta, nesta ordem, quando os cantos de sempre não têm lugar livre. */
export const STAMP_SCALES = [1, 0.82, 0.66] as const;

/**
 * Onde o carimbo solto fica: o primeiro lugar de `preferred` (o da composição da imagem, como antes) que não toca
 * nenhum texto conhecido e cabe na área segura; senão, o lugar livre mais perto da altura preferida, logo acima ou
 * abaixo de algum texto, de qualquer lado (o lado preferido primeiro). Sem lugar livre nos dois cantos, um ponto livre
 * em qualquer lugar da tela (`x`), em tamanho cheio ou menor (`scale`, até 2/3): a captura cheia de texto deixava o
 * carimbo só com os dois cantos, ambos sobre letras, e ele pousava sobre a manchete ("CONFIRMADO" sobre "downloads").
 * Sem lugar livre nem assim, o de menor sobreposição.
 * `obstacles`: texto desenhado pela tela (título, legenda, faixa, avatar, contador, fonte) e a manchete da imagem,
 * nunca cobertos quando há outra saída; `soft`: letras da própria imagem (logo, letreiro, corpo: `text_rects` da
 * timeline), que o carimbo evita sempre que há lugar. Sem texto no caminho, o lugar de sempre (o quadro não muda).
 */
export const placeStamp = (preferred: StampSpot[], g: StampGeom, obstacles: Rect[], soft: Rect[] = []): StampSpot => {
  const lo = STAMP_SAFE + g.halfH;
  const hi = 1 - STAMP_SAFE - g.halfH;
  const fit = (y: number) => (lo <= hi ? clamp(y, lo, hi) : 0.5);
  const first = preferred[0] ?? { side: 'right' as StampSide, y: 0.3 };
  const other: StampSide = first.side === 'right' ? 'left' : 'right';
  const ys = [first.y, lo, hi];
  for (const o of [...obstacles, ...soft]) ys.push(o[1] - g.halfH - STAMP_GAP, o[1] + o[3] + g.halfH + STAMP_GAP);
  const near = [...new Set(ys.map(fit))].sort((a, b) => Math.abs(a - first.y) - Math.abs(b - first.y));
  const candidates: StampSpot[] = [
    ...preferred.map((p) => ({ side: p.side, y: fit(p.y) })),
    ...near.flatMap((y) => [
      { side: first.side, y },
      { side: other, y },
    ]),
  ];
  let best = candidates[0];
  let bestCost = Infinity;
  for (const spot of candidates) {
    const c = boxCost(stampRect(spot, g), obstacles, soft);
    if (c === 0) return spot;
    if (c < bestCost - 1e-9) {
      best = spot;
      bestCost = c;
    }
  }
  // Nenhum canto livre: qualquer ponto da tela, do tamanho cheio ao menor. Sem ponto livre em nenhum tamanho, o que
  // cobre a menor PARTE do carimbo (custo pela área dele): o menor cobre menos letra quando a tela é toda texto.
  let fallback: StampSpot | null = null;
  let fallbackScore = bestCost;
  for (const s of STAMP_SCALES) {
    const hw = g.halfW * s;
    const hh = g.halfH * s;
    const spot = placeFree(
      [g.cx[first.side], fit(first.y)],
      hw,
      hh,
      obstacles,
      soft,
      [STAMP_SIDE_MIN + hw, 1 - STAMP_SIDE_MIN - hw],
      [STAMP_SAFE + hh, 1 - STAMP_SAFE - hh],
    );
    const found: StampSpot = { side: spot.x < 0.5 ? 'left' : 'right', y: spot.y, x: spot.x, ...(s < 1 ? { scale: s } : {}) };
    if (spot.cost === 0) return found;
    const score = spot.cost / (s * s);
    if (score < fallbackScore - 1e-9) {
      fallback = found;
      fallbackScore = score;
    }
  }
  return fallback ?? best;
};

/** Escala de entrada do carimbo (ele entra grande, 2,2 → 1). No lugar perto da borda, só o que cabe na tela: o
 * carimbo nunca sai cortado na borda enquanto encolhe. */
export const STAMP_ENTRY_SCALE = 2.2;
export const stampEntryScale = (spot: StampSpot, g: StampGeom) => {
  const { cx, s } = stampCenter(spot, g);
  const hw = g.halfW * s;
  const hh = g.halfH * s;
  const room = Math.min(cx / hw, (1 - cx) / hw, spot.y / hh, (1 - spot.y) / hh);
  return clamp(room, 1, STAMP_ENTRY_SCALE);
};

/**
 * Altura (centro, fração da tela) de uma faixa centrada na horizontal (a faixa do topo, "GAMESBEAT · 13/10/2026"):
 * a de sempre (`preferred`) quando ela não toca letra da imagem (`soft`) nem texto da tela (`hard`); senão, a altura
 * livre mais perto dela entre `lo` e `hi` (centro); sem altura livre, a de menor sobreposição. `w`/`half`: largura e
 * meia altura da faixa.
 */
export const placeBand = (preferred: number, w: number, half: number, hard: Rect[], soft: Rect[], lo: number, hi: number): number => {
  const x = 0.5 - w / 2;
  const box = (y: number): Rect => [x, y - half, w, half * 2];
  const first = clamp(preferred, lo, Math.max(lo, hi));
  if (boxCost(box(first), hard, soft) === 0 || hi <= lo) return first;
  const ys = [first, lo, hi];
  for (const o of [...hard, ...soft]) ys.push(o[1] - half - STAMP_GAP, o[1] + o[3] + half + STAMP_GAP);
  const candidates = [...new Set(ys.filter((y) => y >= lo - 1e-9 && y <= hi + 1e-9))].sort(
    (a, b) => Math.abs(a - first) - Math.abs(b - first),
  );
  let best = first;
  let bestCost = boxCost(box(first), hard, soft);
  for (const y of candidates) {
    const c = boxCost(box(y), hard, soft);
    if (c === 0) return y;
    if (c < bestCost - 1e-9) {
      best = y;
      bestCost = c;
    }
  }
  return best;
};

/** Margem lateral (fração da largura) dos selos da tela (faixa do topo fora do centro, contador). */
export const CHIP_SIDE = 0.04;

/**
 * Lugar (centro, fração) da faixa do topo ("GAMESBEAT · 13/10/2026", "GOLPE 1 · AGO 2026"): centrada, na altura de
 * sempre ou na livre mais perto (`placeBand`); quando nenhuma altura centrada é livre (a coluna de texto da captura
 * ocupa o meio de cima a baixo), o ponto livre mais perto fora da coluna, de lado. Sem ponto livre, o de menor
 * sobreposição, com a manchete da imagem como obstáculo duro: a faixa nunca desce para cima da byline e do corpo só
 * porque cobre um pouco menos.
 */
export const placeChip = (preferred: number, w: number, half: number, hard: Rect[], soft: Rect[], lo: number, hi: number) => {
  const y = placeBand(preferred, w, half, hard, soft, lo, hi);
  const centered: Rect = [0.5 - w / 2, y - half, w, half * 2];
  const cost = boxCost(centered, hard, soft);
  if (cost === 0 || hi <= lo) return { x: 0.5, y };
  const hw = w / 2;
  const first = clamp(preferred, lo, hi);
  const spot = placeFree([0.5, first], hw, half, hard, soft, [CHIP_SIDE + hw, 1 - CHIP_SIDE - hw], [lo, hi]);
  if (spot.cost === 0) return { x: spot.x, y: spot.y };
  // Sem lugar livre: o topo de sempre quando ali ela só cobre letra da imagem e sair dele não ganha muito (o menor
  // custo, sozinho, levava a faixa para o buraco da detecção, no meio do corpo da matéria).
  const home: Rect = [0.5 - hw, first - half, w, half * 2];
  const homeCost = boxCost(home, hard, soft);
  if (boxCost(home, hard, []) < TOUCH * HARD_WEIGHT && Math.min(spot.cost, cost) > homeCost * CHIP_MOVE_GAIN) return { x: 0.5, y: first };
  return spot.cost < cost - 1e-9 ? { x: spot.x, y: spot.y } : { x: 0.5, y };
};

/** Sem lugar livre, a faixa só sai do topo de sempre quando cobre menos que esta fração do que cobria lá. */
const CHIP_MOVE_GAIN = 0.5;

/**
 * Caixa (fração da tela) do selo do canto (contador "3/5"): o canto pedido (`rect`, no topo) quando livre; senão o
 * ponto livre mais perto dele entre o topo seguro e a legenda (`yr`, limites do centro); sem ponto livre, o de menor
 * sobreposição. QA: o "3/5" encostava em "PRE-ORDER" no topo da página.
 */
export const placeCorner = (rect: Rect, hard: Rect[], soft: Rect[], yr: [number, number]): Rect => {
  if (boxCost(rect, hard, soft) === 0) return rect;
  const hw = rect[2] / 2;
  const hh = rect[3] / 2;
  const spot = placeFree([rect[0] + hw, rect[1] + hh], hw, hh, hard, soft, [CHIP_SIDE * 0.5 + hw, 1 - CHIP_SIDE * 0.5 - hw], yr);
  return spot.cost < boxCost(rect, hard, soft) - 1e-9 ? [spot.x - hw, spot.y - hh, rect[2], rect[3]] : rect;
};

/** Fração máxima do texto que pula que pode ficar sobre letras da imagem; acima disso ele não entra (a legenda já diz
 * a palavra): "VERSÃO" gigante cobria os resultados da busca que eram a prova. */
export const POP_MAX_ON_TEXT = 0.12;

/** Caixa do texto que pula (fração) com centro na altura `y`: largura `textW` e corpo `size` em px. */
export const textPopRect = (y: number, textW: number, size: number, width: number, height: number): Rect => {
  const w = Math.min(0.96, (textW * 1.08) / width);
  const half = (size * 0.62) / height;
  return [0.5 - w / 2, y - half, w, half * 2];
};

/** Altura (centro, fração) do texto que pula desviando das letras da imagem (`placeBand`): largura `textW` e corpo
 * `size` em px; a legenda (centro `avoid`, meia altura `captionHalf`) e o texto da tela (`screen`: faixa do topo,
 * carimbo, contador, a manchete da imagem) são obstáculos duros. */
export const textPopY = (
  y: number,
  textW: number,
  size: number,
  width: number,
  height: number,
  imageText: Rect[],
  avoid: number | null,
  captionHalf: number,
  screen: Rect[] = [],
) => {
  const w = Math.min(0.96, (textW * 1.08) / width);
  const half = (size * 0.62) / height;
  const hard: Rect[] = avoid !== null ? [[0, avoid - captionHalf, 1, captionHalf * 2], ...screen] : [...screen];
  const lo = Math.max(0.15, STAMP_SAFE + half);
  const hi = Math.min(0.85, (avoid !== null && avoid > 0.5 ? avoid - captionHalf - 0.02 : 1 - STAMP_SAFE) - half);
  return hi > lo ? placeBand(y, w, half, hard, imageText, lo, hi) : y;
};

/**
 * O texto que pula entra no lugar `rect`? Não quando cobre texto da tela ou a manchete da imagem (`hard`) ou mais que
 * `POP_MAX_ON_TEXT` de letras da imagem (`soft`): sobre a captura cheia de texto não há lugar para palavra gigante.
 */
export const textPopFits = (rect: Rect, hard: Rect[], soft: Rect[]) =>
  coveredShare(rect, hard) === 0 && coveredShare(rect, soft) <= POP_MAX_ON_TEXT;

/** Área (fração da tela) das caixas `rects` coberta pela faixa horizontal [top, bottom]. */
export const bandOverlap = (top: number, bottom: number, rects: Rect[]) =>
  rects.reduce((sum, r) => sum + overlapArea([0, top, 1, bottom - top], r), 0);

// --- letras da imagem na tela -------------------------------------------------------------------------------------
//
// O backend detecta onde há letras no arquivo que o renderer recebe (`text_rects`: manchete da captura, logo, letreiro)
// em fração da imagem. Aqui elas vão para a TELA pelo mesmo caminho da imagem: recorte (`rectToContainer`), movimento
// de câmera do `VisualLayer` (a caixa vira o envelope de todo o movimento: o carimbo fica parado enquanto a imagem
// anda) e enquadramento do plano; no tratamento "tela", pela janela ou celular onde a imagem aparece.

export type MotionEnvelope = { smin: number; smax: number; dx: number; dy: number };

/** Movimento do `VisualLayer` (mesma tabela de lá) resumido: escala mínima e máxima e o deslocamento máximo (% do
 * quadro) em x e y. Só para o envelope; não desenha nada. */
export const motionEnvelope = (motion: string | undefined, z: number): MotionEnvelope => {
  const zz = Math.max(0, num(z, 0));
  switch (motion) {
    case 'parallax':
    case 'ken_burns_in':
    case 'ken_burns_out':
      return { smin: 1 + zz * 0.15, smax: 1 + zz, dx: zz * 12, dy: zz * 10 };
    case 'pan_left':
    case 'pan_right':
      return { smin: 1 + zz, smax: 1 + zz, dx: zz * 30, dy: 0 };
    case 'pan_up':
    case 'pan_down':
      return { smin: 1 + zz, smax: 1 + zz, dx: 0, dy: zz * 30 };
    case 'punch_zoom':
      return { smin: 1 + zz * 0.1, smax: 1 + zz * 0.85, dx: 0, dy: 0 };
    case 'drift':
      return { smin: 1 + zz * 0.55, smax: 1 + zz * 0.55, dx: zz * 14, dy: zz * 10 };
    case 'zoom_to':
      return { smin: 1 + zz * 0.2, smax: 1 + Math.min(0.35, 2.5 * zz), dx: 0, dy: 0 };
    case 'push':
      return { smin: 1 + zz * 0.1, smax: 1 + zz * 0.75, dx: 0, dy: 0 };
    case 'none':
      return { smin: 1, smax: 1, dx: 0, dy: 0 };
    default:
      return { smin: 1 + zz * 0.1, smax: 1 + zz * 0.1, dx: 0, dy: 0 };
  }
};

const boundsOf = (points: [number, number][]): Rect | null => {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const y0 = Math.min(...ys);
  return clipRect(x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0);
};

/** Caixa que cobre o retângulo `r` (contêiner) em todo o movimento: escala na origem `origin` e deslocamento dentro da
 * sobra da escala (como o `VisualLayer`, que nunca mostra a borda). */
export const rectThroughMotion = (r: Rect, env: MotionEnvelope, origin: [number, number]): Rect | null => {
  const [ox, oy] = origin;
  const points: [number, number][] = [];
  for (const s of [env.smin, env.smax]) {
    const room = ((Math.max(1, s) - 1) / Math.max(1, s)) * 100;
    const tx = Math.min(env.dx, room) / 100;
    const ty = Math.min(env.dy, room) / 100;
    for (const dx of [-tx, tx]) {
      for (const dy of [-ty, ty]) {
        points.push([ox + s * (r[0] - ox + dx), oy + s * (r[1] - oy + dy)]);
        points.push([ox + s * (r[0] + r[2] - ox + dx), oy + s * (r[1] + r[3] - oy + dy)]);
      }
    }
  }
  return boundsOf(points);
};

/** Uma fonte de letras da imagem num trecho da cena: caixas no arquivo, a caixa da imagem (recorte), o enquadramento
 * do plano e o movimento dele. */
export type TextLayer = { rects: Rect[]; box: ImageBox | null; framing: ShotFraming | null; motion: string | undefined };

/** Letras da imagem na tela (fração), somando todos os trechos: o carimbo e a faixa ficam a cena inteira. */
export const textOnScreen = (layers: TextLayer[], z: number, origin: [number, number], videoAspect: number): Rect[] => {
  const out: Rect[] = [];
  for (const layer of layers) {
    const env = motionEnvelope(layer.motion, z);
    for (const r of layer.rects) {
      const inBox = rectToContainer(r, layer.box, videoAspect);
      const moved = inBox ? rectThroughMotion(inBox, env, origin) : null;
      const shown = moved ? rectThroughFraming(moved, layer.framing) : null;
      if (shown) out.push(shown);
    }
  }
  return out;
};

/** Moldura do tratamento "tela" (px): o aparelho, o conteúdo e a área da imagem (no feed, o post). */
export type ScreenVariantId = 'window' | 'phone' | 'feed';
export type PxRect = { x: number; y: number; w: number; h: number };
export const screenFrame = (variant: ScreenVariantId, width: number, height: number) => {
  const unit = Math.min(width, height);
  const vertical = height > width;
  let device: PxRect;
  let content: PxRect;
  if (variant === 'window') {
    const w = vertical ? width * 0.9 : width * 0.74;
    const h = vertical ? w * 0.82 : height * 0.78;
    device = { x: (width - w) / 2, y: (height - h) / 2 - (vertical ? height * 0.06 : 0), w, h };
    const bar = unit * 0.062;
    content = { x: device.x, y: device.y + bar, w, h: h - bar };
  } else {
    // Celular (também moldura do feed): ocupa a altura no horizontal e ~80% no vertical.
    const h = vertical ? height * 0.78 : height * 0.88;
    const w = h * 0.49;
    device = { x: (width - w) / 2, y: (height - h) / 2 - (vertical ? height * 0.05 : 0), w, h };
    const bezel = w * 0.035;
    content = { x: device.x + bezel, y: device.y + bezel, w: w - 2 * bezel, h: h - 2 * bezel };
  }
  let media = content;
  if (variant === 'feed') {
    const header = content.h * 0.12;
    const mediaH = Math.min(content.w * 1.1, content.h * 0.58);
    media = { x: content.x, y: content.y + header + content.h * 0.035, w: content.w, h: mediaH };
  }
  return { device, content, media };
};

/**
 * Material real: a captura, o oficial e as fotos (do canal, livres, de banco) mostram o que é, e a tela que os emoldura
 * é a PROVA. Cursor de mouse por cima entregava gravação de tela crua (QA: cursor sobre o logo da key art, sobre
 * "THEFT" e sobre a manchete da matéria).
 */
const PROOF_PROVS: ReadonlySet<Prov> = new Set<Prov>(['own', 'official', 'capture', 'free', 'stock']);
/** Respiro (fração do arquivo) em volta das letras que o alvo do cursor não pode tocar. */
const CURSOR_TEXT_GAP = 0.03;

/**
 * O cursor do tratamento "tela" aparece? Nunca sobre material real (`prov`); na janela e no celular, só quando o
 * alvo (o foco, `target`, fração do arquivo) não cai sobre letras da imagem (`textRects`, fração do arquivo): o
 * cursor nunca pousa sobre manchete ou logo. No post de feed o alvo é o coração (fora da imagem).
 */
export const screenCursorShown = (prov: Prov | undefined, variant: ScreenVariantId, target: [number, number], textRects: Rect[]) => {
  if (prov !== undefined && PROOF_PROVS.has(prov)) return false;
  if (variant === 'feed') return true;
  const [x, y] = target;
  const g = CURSOR_TEXT_GAP;
  return !textRects.some((r) => x >= r[0] - g && x <= r[0] + r[2] + g && y >= r[1] - g && y <= r[1] + r[3] + g);
};

/** Escala máxima da imagem dentro da tela: o movimento lento (`innerScale` do `Screen`) e o zoom depois do clique. */
export const screenInnerMax = (motion: string | undefined, z: number, feed: boolean) => {
  const zz = Math.max(0, num(z, 0)) * 0.6;
  return (motion === 'static' ? 1 + zz * 0.1 : 1 + zz) * (feed ? 1 : 1.12);
};

/** Letras da imagem (fração do arquivo, proporção `aspect`) na tela, dentro da área `media` (px) do tratamento
 * "tela": `cover` com o foco `focus` e escala de 1 a `smax` no foco. */
export const textInScreen = (
  rects: Rect[],
  aspect: number,
  media: PxRect,
  focus: [number, number],
  smax: number,
  width: number,
  height: number,
): Rect[] => {
  if (!(media.w > 0) || !(media.h > 0)) return [];
  const boxAspect = media.w / media.h;
  const a = aspect > 0 ? aspect : width / height;
  const dispW = a > boxAspect ? media.h * a : media.w;
  const dispH = a > boxAspect ? media.h : media.w / a;
  const offX = (media.w - dispW) * focus[0];
  const offY = (media.h - dispH) * focus[1];
  const ox = media.w * focus[0];
  const oy = media.h * focus[1];
  const out: Rect[] = [];
  for (const r of rects) {
    const points: [number, number][] = [];
    for (const s of [1, Math.max(1, smax)]) {
      points.push([ox + s * (offX + r[0] * dispW - ox), oy + s * (offY + r[1] * dispH - oy)]);
      points.push([ox + s * (offX + (r[0] + r[2]) * dispW - ox), oy + s * (offY + (r[1] + r[3]) * dispH - oy)]);
    }
    // Só o que fica dentro da área da imagem aparece.
    const x0 = Math.max(0, Math.min(...points.map((p) => p[0])));
    const y0 = Math.max(0, Math.min(...points.map((p) => p[1])));
    const x1 = Math.min(media.w, Math.max(...points.map((p) => p[0])));
    const y1 = Math.min(media.h, Math.max(...points.map((p) => p[1])));
    if (x1 - x0 < 1 || y1 - y0 < 1) continue;
    const shown = clipRect((media.x + x0) / width, (media.y + y0) / height, (x1 - x0) / width, (y1 - y0) / height);
    if (shown) out.push(shown);
  }
  return out;
};

// --- fundo do cartão -------------------------------------------------------------------------------------------------

/**
 * Brilho médio mínimo do fundo do cartão depois do escurecimento: o cartão escurece a imagem para o texto aparecer,
 * mas a imagem nunca vira preto liso (a preta ou lisa demais sai, `cardBackdropOff`).
 */
export const CARD_BG_FLOOR = 0.18;
/** O escurecimento do cartão nunca desce disto (o texto branco continua legível sobre fundo claro). */
export const CARD_DIM_MIN = 0.3;

/**
 * Brilho médio abaixo do qual a imagem de fundo do cartão é preta: o cartão fica com o fundo do estilo. Espelho de
 * `timeline.CARD_BG_MIN_LUMA` no backend.
 */
export const CARD_BG_MIN_LUMA = 0.05;
/**
 * Detalhe (bordas da imagem reduzida) abaixo do qual a imagem de fundo do cartão é lisa: escurecida e desfocada, vira
 * uma mancha sem nada (a página quase preta da Rockstar, o canto cinza de um Xbox). O cartão fica com o fundo do estilo.
 * A imagem escura mas cheia de forma (a arte em neon do VI) fica, só escurece menos (`cardDim`). Espelho de
 * `timeline.CARD_BG_MIN_DETAIL` no backend.
 */
export const CARD_BG_MIN_DETAIL = 0.09;

/** O cartão desenha o fundo do estilo no lugar da imagem preta (`bg_luma`) ou lisa (`bg_detail`) demais. */
export const cardBackdropOff = (luma: number | undefined, detail?: number) =>
  (typeof luma === 'number' && luma < CARD_BG_MIN_LUMA) || (typeof detail === 'number' && detail < CARD_BG_MIN_DETAIL);

/** Escurecimento do fundo do cartão (`dim` do tipo) limitado pelo brilho da imagem (`bg_luma`); sem ele, o do tipo. */
export const cardDim = (dim: number, luma: number | undefined) => {
  if (!(dim > 0) || typeof luma !== 'number' || !(luma > 0)) return dim;
  return Math.min(dim, Math.max(CARD_DIM_MIN, 1 - CARD_BG_FLOOR / luma));
};

// --- transição de entrada --------------------------------------------------------------------------------------------

/** Teto da transição de entrada (desfoque, flash, fade): mais longa, ela vira quadro morto sem nada nítido. */
export const ENTRY_MAX_S = 0.4;

/** Quadros da transição de entrada: os do estilo, até ENTRY_MAX_S (o estilo aceita até 2 s). */
export const entryFrames = (transitionFrames: number, fps: number) =>
  Math.max(1, Math.min(Math.round(num(transitionFrames, 1)), Math.round(ENTRY_MAX_S * fps)));

// --- câmera ----------------------------------------------------------------------------------------------------------

/**
 * Câmera do fundo num quadro. `scale` e `x`/`y` (fração do quadro, origem no canto de cima à esquerda) fazem o
 * `translate(x) scale(scale)`; `rot` em graus (no centro) e `blur` em px (desfoque horizontal do whip).
 */
export type Camera = { scale: number; x: number; y: number; rot: number; blur: number };

export const IDENTITY: Camera = { scale: 1, x: 0, y: 0, rot: 0, blur: 0 };

/** `keep`: o trecho (fração da tela, no quadro) que a câmera nunca corta, a manchete da captura (`keepTransform`). */
export type CameraContext = FxContext & { width: number; height: number; seed: number; keep?: Rect | null };

/** Quadros da subida do punch (2 a 3 quadros) e da soltura quando ela não cai num corte. */
export const PUNCH_RISE = 3;
const PUNCH_RELEASE = 4;
const ZOOM_OUT = 10;
/** Deslocamento máximo do whip (fração da largura) e desfoque máximo (fração da largura). */
const WHIP_SHIFT = 0.08;
const WHIP_BLUR = 0.05;

const isCut = (frame: number, ctx: FxContext) => frame >= ctx.frames || ctx.cuts.includes(frame);

/** Escala do punch (1 = sem punch) num quadro: sobe em 3 quadros, segura e solta no próximo corte (seco) ou em 4. */
export const punchScale = (fx: TimelineFx, frame: number, ctx: FxContext) => {
  const span = fxSpan(fx, ctx);
  if (frame < span.from) return 1;
  const peak = clamp(pNum(fx.params, 'scale', isFreezeZoomMeme(fx) ? 1.25 : 1.2), 1, 2);
  let k: number;
  if (frame < span.to) k = EASE.outCubic((frame - span.from + 1) / PUNCH_RISE);
  else if (isCut(span.to, ctx)) return 1;
  else k = 1 - EASE.outCubic((frame - span.to + 1) / PUNCH_RELEASE);
  return k <= 0 ? 1 : 1 + (peak - 1) * k;
};

const zoomRectAt = (fx: TimelineFx, frame: number, ctx: FxContext) => {
  const rect = pRects(fx.params)[0];
  if (!rect) return null;
  const span = fxSpan(fx, ctx);
  if (frame < span.from) return null;
  const [rx, ry, rw, rh] = rect;
  const fill = clamp(pNum(fx.params, 'fill', 0.8), 0.3, 1);
  const target = clamp(Math.min(1 / Math.max(rw, 1e-3), 1 / Math.max(rh, 1e-3)) * fill, 1, clamp(pNum(fx.params, 'max', 2.5), 1, 4));
  const travel = clamp(Math.round((span.to - span.from) / 3), 6, 18);
  let q: number;
  if (frame < span.to) q = easeInOutCubic((frame - span.from + 1) / travel);
  else if (isCut(span.to, ctx)) return null;
  else q = 1 - easeInOutCubic((frame - span.to + 1) / ZOOM_OUT);
  if (q <= 0) return null;
  return { scale: 1 + (target - 1) * q, cx: 0.5 + (rx + rw / 2 - 0.5) * q, cy: 0.5 + (ry + rh / 2 - 0.5) * q };
};

/** A borda da imagem nunca aparece: o deslocamento fica dentro do que a escala deixa. */
const cover = (t: number, scale: number) => clamp(t, 1 - scale, 0);

// --- trecho que nunca sai do quadro (manchete da captura) ------------------------------------------------------------
//
// Zoom, pan, punch e o zoom no retângulo, cada um na sua camada, cortavam a primeira letra da manchete da captura
// ("rand Theft Auto VI's PC", "ites falsos"): a captura vem com a manchete encostada na borda esquerda (3% do arquivo) e
// qualquer escala de 1,1 em volta do centro já tira 4,5% de cada lado. O `keep` (a manchete, `[x, y, w, h]`) fica
// inteiro na tela com `KEEP_MARGIN` em volta: a camada desloca o que precisar e, se a escala não deixa a manchete
// caber, a escala desce até ela caber (a coluna inteira).

/** Margem mínima (fração do quadro) entre o trecho guardado e a borda da tela. */
export const KEEP_MARGIN = 0.03;

/**
 * Um eixo de `p' = o + s·(p + t − o)` (a escala `s` em volta da origem `o`, depois do deslocamento `t`, tudo em
 * fração do contêiner): a escala e o deslocamento mais perto dos pedidos em que o trecho `[a, b]` fica em
 * `[m, 1 − m]` e a borda da imagem nunca aparece. A margem de cada lado não passa da distância do trecho à borda do
 * próprio arquivo (a imagem não tem mais que isso). Escala ≤ 1 (fora do caso) volta como veio.
 */
export const keepAxis = (a: number, b: number, s: number, o: number, t: number, margin = KEEP_MARGIN) => {
  if (!(s > 1) || !(b > a)) return { s, t };
  const ml = clamp(margin, 0, Math.max(0, a));
  const mr = clamp(margin, 0, Math.max(0, 1 - b));
  const sc = Math.max(1, Math.min(s, (1 - ml - mr) / (b - a)));
  const lo = Math.max((ml - o) / sc + o - a, -(1 - o) * (sc - 1) / sc);
  const hi = Math.min((1 - mr - o) / sc + o - b, (o * (sc - 1)) / sc);
  return { s: sc, t: lo <= hi ? clamp(t, lo, hi) : (lo + hi) / 2 };
};

/** Escala única dos dois eixos e deslocamento de cada um (o `keepAxis` em x e em y, com a menor escala). */
export const keepTransform = (keep: Rect | null | undefined, s: number, origin: [number, number], t: [number, number], margin = KEEP_MARGIN) => {
  if (!keep || !(s > 1)) return { s, t };
  const sx = keepAxis(keep[0], keep[0] + keep[2], s, origin[0], t[0], margin).s;
  const sy = keepAxis(keep[1], keep[1] + keep[3], s, origin[1], t[1], margin).s;
  const sc = Math.min(sx, sy);
  const x = keepAxis(keep[0], keep[0] + keep[2], sc, origin[0], t[0], margin);
  const y = keepAxis(keep[1], keep[1] + keep[3], sc, origin[1], t[1], margin);
  return { s: sc, t: [x.t, y.t] as [number, number] };
};

/** Onde o trecho `r` (fração do contêiner) cai depois de `p' = o + s·(p + t − o)`. */
export const rectThrough = (r: Rect, s: number, origin: [number, number], t: [number, number]): Rect => [
  origin[0] + s * (r[0] + t[0] - origin[0]),
  origin[1] + s * (r[1] + t[1] - origin[1]),
  r[2] * s,
  r[3] * s,
];

/** União dos retângulos (a manchete de várias linhas); null sem nenhum. */
export const unionRect = (rects: Rect[]): Rect | null => {
  if (!rects.length) return null;
  const x0 = Math.min(...rects.map((r) => r[0]));
  const y0 = Math.min(...rects.map((r) => r[1]));
  const x1 = Math.max(...rects.map((r) => r[0] + r[2]));
  const y1 = Math.max(...rects.map((r) => r[1] + r[3]));
  return [x0, y0, x1 - x0, y1 - y0];
};

export const cameraAt = (frame: number, fxs: TimelineFx[] | undefined, ctx: CameraContext): Camera => {
  const list = fxs ?? [];
  let scale = 1;
  let x = 0;
  let y = 0;
  // Zoom no retângulo: o mais recente que ainda vale.
  for (const fx of list) {
    if (fx.type !== 'zoom_rect') continue;
    const z = zoomRectAt(fx, frame, ctx);
    if (!z) continue;
    scale = z.scale;
    x = cover(0.5 - z.scale * z.cx, z.scale);
    y = cover(0.5 - z.scale * z.cy, z.scale);
  }
  // Punch: escala em torno do ponto (`x`/`y` do evento, centro por padrão), por cima do zoom.
  for (const fx of list) {
    if (fx.type !== 'punch' && !isFreezeZoomMeme(fx)) continue;
    const s = punchScale(fx, frame, ctx);
    if (s === 1) continue;
    const ox = clamp01(pNum(fx.params, 'x', 0.5));
    const oy = clamp01(pNum(fx.params, 'y', 0.5));
    x = ox * (1 - s) + s * x;
    y = oy * (1 - s) + s * y;
    scale *= s;
  }
  scale = Math.min(scale, 4);
  x = cover(x, scale);
  y = cover(y, scale);
  // A manchete da captura (onde ela está na tela neste quadro, já com o movimento da imagem) fica inteira: a câmera
  // (origem 0 0) é `p' = 0 + s·(p + x/s)`.
  if (ctx.keep && scale > 1) {
    const kept = keepTransform(ctx.keep, scale, [0, 0], [x / scale, y / scale]);
    scale = kept.s;
    x = kept.t[0] * scale;
    y = kept.t[1] * scale;
  }

  // Tremor com decaimento e whip: deslocam o quadro; uma sobra de escala esconde a borda.
  const minSide = Math.min(ctx.width, ctx.height);
  let sx = 0;
  let sy = 0;
  let rot = 0;
  let whip = 0;
  let blur = 0;
  for (const fx of list) {
    if (fx.type === 'shake') {
      const span = fxSpan(fx, ctx);
      if (frame < span.from || frame >= span.to) continue;
      const t = frame - span.from;
      const u = t / Math.max(1, span.to - span.from);
      const decay = Math.exp(-3 * u) * (1 - u);
      // `strength` é a chave do planejador (0,6 a 1,2); `amount` fica como nome antigo.
      const amount = clamp(pNum(fx.params, 'strength', pNum(fx.params, 'amount', 1)), 0, 3);
      const amp = amount * 0.02 * minSide * decay;
      const phase = hash01(`${ctx.seed}-shake-${span.from}`) * Math.PI * 2;
      const sec = t / ctx.fps;
      sx += amp * Math.sin(Math.PI * 2 * 11 * sec + phase);
      sy += amp * 0.75 * Math.sin(Math.PI * 2 * 13.3 * sec + phase * 1.7);
      rot += 0.8 * amount * decay * Math.sin(Math.PI * 2 * 7 * sec + phase * 0.6);
    } else if (fx.type === 'whip') {
      // O corte é o `from`: metade antes (sai borrando para um lado) e metade depois (entra do outro).
      const span = fxSpan(fx, ctx);
      const half = Math.max(2, Math.round((span.to - span.from) / 2));
      const dir = pStr(fx.params, 'side') === 'left' ? -1 : 1;
      let k = 0;
      if (frame >= span.from - half && frame < span.from) {
        k = (frame - (span.from - half) + 1) / half;
        whip += -dir * WHIP_SHIFT * k * k;
      } else if (frame >= span.from && frame < span.from + half) {
        k = 1 - (frame - span.from) / half;
        whip += dir * WHIP_SHIFT * k * k;
      }
      blur = Math.max(blur, k * WHIP_BLUR * ctx.width);
    }
  }
  const rad = (Math.abs(rot) * Math.PI) / 180;
  const spare = 1 + 2 * Math.max(Math.abs(sx) / ctx.width, Math.abs(sy) / ctx.height, Math.abs(whip)) + rad * 1.2;
  if (spare > 1) {
    x = 0.5 * (1 - spare) + spare * x;
    y = 0.5 * (1 - spare) + spare * y;
    scale *= spare;
  }
  return { scale, x: x + sx / ctx.width + whip, y: y + sy / ctx.height, rot, blur };
};

/** Caixa `r` (fração da tela) pela câmera por eventos `cam` (origem 0 0: p' = x + s·p), cortada na tela. */
export const rectThroughCamera = (r: Rect, cam: Camera): Rect | null =>
  clipRect(cam.x + r[0] * cam.scale, cam.y + r[1] * cam.scale, r[2] * cam.scale, r[3] * cam.scale);

export const isIdentity = (cam: Camera) =>
  Math.abs(cam.scale - 1) < 1e-6 && Math.abs(cam.x) < 1e-6 && Math.abs(cam.y) < 1e-6 && Math.abs(cam.rot) < 1e-6 && cam.blur < 0.05;

// --- tempo (congelar e rampa de velocidade) ----------------------------------------------------------------------------

/** Trecho em que o tempo do fundo anda a `rate` (0 = congelado). */
export type TimeSegment = Span & { rate: number };

export const timeSegments = (fxs: TimelineFx[] | undefined, ctx: FxContext): TimeSegment[] => {
  const segs = (fxs ?? [])
    .filter((fx) => fx.type === 'freeze' || fx.type === 'speed' || isFreezeZoomMeme(fx))
    .map((fx) => ({ ...fxSpan(fx, ctx), rate: fx.type === 'speed' ? clamp(pNum(fx.params, 'rate', 0.4), 0, 4) : 0 }))
    .sort((a, b) => a.from - b.from);
  const out: TimeSegment[] = [];
  for (const seg of segs) {
    if (out.length && seg.from < out[out.length - 1].to) continue; // sobreposto: vale o primeiro
    out.push(seg);
  }
  return out;
};

/** Quadro do fundo (tempo do material) para o quadro da cena, depois dos congelamentos e rampas. */
export const remapFrame = (frame: number, segs: TimeSegment[]) => {
  let t = 0;
  let prev = 0;
  for (const seg of segs) {
    if (frame < seg.from) return t + (frame - prev);
    t += seg.from - prev;
    if (frame < seg.to) return t + (frame - seg.from) * seg.rate;
    t += (seg.to - seg.from) * seg.rate;
    prev = seg.to;
  }
  return t + (frame - prev);
};

// ---------------------------------------------------------------------------------------------
// Cartão tipográfico (`KineticText` em TextCards.tsx; as caixas do texto em TextBoxes.ts)

/**
 * Cartão tipográfico (a fala na tela, sem imagem ou sobre a imagem desfocada): nunca legenda palavra a palavra. A
 * frase da cena (`scene.text`, a ideia curta do cartão) entra inteira de uma vez; sem ela, a fala vai em sintagmas de
 * `PAGE_MIN` a `PAGE_MAX` palavras, cada um inteiro desde o começo dele e na tela até o próximo entrar (a 1ª página
 * desde o corte): nunca um quadro vazio nem de uma palavra solta ("e", "que", "logado.").
 */
export const PAGE_MIN = 3;
export const PAGE_MAX = 7;
/** Altura (fração da tela) do centro do bloco de texto: o centro óptico, um pouco acima do meio, fixo a cena inteira. */
export const KINETIC_CENTER_Y = 0.45;
/** Palavras acesas por página (a palavra-chave, não meia frase colorida). */
const MAX_MARKS = 2;

const CLAUSE_END = /[.!?…:;,]["'”’»)]*$/u;
const SENTENCE_END = /[.!?…]["'”’»)]*$/u;

/** Palavra curta de ligação (artigo, preposição, conjunção: "a", "com", "de", "que"): nunca fecha uma página. */
const isLink = (text: string) => /^[\p{Ll}]{1,3}$/u.test(text);

/**
 * Divide um trecho longo (sem pontuação) em pedaços do mesmo tamanho, de até `PAGE_MAX` palavras; a palavra de
 * ligação no fim de um pedaço passa para o seguinte (até 2, sem o pedaço ficar com menos de `PAGE_MIN` nem o
 * seguinte passar de `PAGE_MAX`): "dependendo do site até com a" vira "dependendo do site até".
 */
const balanced = (chunk: CaptionWord[]): CaptionWord[][] => {
  const parts = Math.ceil(chunk.length / PAGE_MAX);
  const size = Math.ceil(chunk.length / parts);
  const out: CaptionWord[][] = [];
  for (let i = 0; i < chunk.length; i += size) out.push(chunk.slice(i, i + size));
  for (let k = 0; k + 1 < out.length; k += 1) {
    for (let moved = 0; moved < 2; moved += 1) {
      const piece = out[k];
      const last = piece[piece.length - 1];
      if (piece.length <= PAGE_MIN || out[k + 1].length >= PAGE_MAX || !isLink(last.text)) break;
      out[k + 1].unshift(piece.pop() as CaptionWord);
    }
  }
  return out;
};

/**
 * Páginas do cartão tipográfico: a fala cortada na pontuação (frase e oração), juntando orações da mesma frase até
 * `PAGE_MAX` palavras; a página com menos de `PAGE_MIN` palavras se junta com a vizinha (a anterior; na primeira, a
 * seguinte). Fala de uma ou duas palavras é uma página só.
 */
export const kineticPages = (words: CaptionWord[]): CaptionWord[][] => {
  const clauses: CaptionWord[][] = [];
  let current: CaptionWord[] = [];
  for (const w of words) {
    current.push(w);
    if (CLAUSE_END.test(w.text)) {
      clauses.push(current);
      current = [];
    }
  }
  if (current.length) clauses.push(current);
  const pages: CaptionWord[][] = [];
  for (const clause of clauses.flatMap((c) => (c.length > PAGE_MAX ? balanced(c) : [c]))) {
    const last = pages[pages.length - 1];
    if (last && last.length + clause.length <= PAGE_MAX && !SENTENCE_END.test(last[last.length - 1].text)) {
      last.push(...clause);
    } else {
      pages.push([...clause]);
    }
  }
  let i = 0;
  while (i < pages.length) {
    if (pages.length > 1 && pages[i].length < PAGE_MIN) {
      if (i > 0) {
        pages[i - 1].push(...pages[i]);
        pages.splice(i, 1);
      } else {
        pages[1].unshift(...pages[0]);
        pages.splice(0, 1);
      }
      continue;
    }
    i += 1;
  }
  return pages;
};

const core = (text: string) => text.replace(/[^\p{L}\p{N}]/gu, '');

/** Palavras do cartão: a frase da cena inteira (desde o corte) ou, sem ela, a fala com o tempo de cada palavra. */
export const kineticWords = (scene: TimelineScene): CaptionWord[] => {
  const phrase = scene.text?.trim();
  if (phrase) return phrase.split(/\s+/).map((t) => ({ text: t, from: 0, to: scene.durationInFrames }));
  return scene.words;
};

/** As páginas do cartão da cena (a frase da cena é sempre uma página só). */
export const scenePages = (scene: TimelineScene): CaptionWord[][] => {
  const words = kineticWords(scene);
  if (!words.length) return [];
  return scene.text?.trim() ? [words] : kineticPages(words);
};

/**
 * A palavra-chave acesa da página: a marcada no storyboard (`emph`); senão número ou palavra longa; no máximo
 * `MAX_MARKS`, as mais longas; sem nenhuma, a mais longa (página de mais de uma palavra).
 */
export const kineticMarks = (shown: CaptionWord[]) => {
  const emph = shown.map((w) => Boolean(w.emph));
  const candidates = emph.some(Boolean) ? emph : shown.map((w) => core(w.text).length >= 7 || /\p{N}/u.test(w.text));
  const ranked = shown
    .map((w, i) => ({ i, len: core(w.text).length }))
    .filter((c) => candidates[c.i])
    .sort((a, b) => b.len - a.len || a.i - b.i)
    .slice(0, MAX_MARKS);
  const marked = shown.map((_, i) => ranked.some((c) => c.i === i));
  if (!marked.some(Boolean) && shown.length > 1) {
    const longest = shown.reduce((best, w, i) => (core(w.text).length > core(shown[best].text).length ? i : best), 0);
    marked[longest] = true;
  }
  return marked;
};

/** A página na tela no quadro `frame`: a última que já começou (a 1ª desde o corte, antes mesmo da fala). */
export const pageAt = (pages: CaptionWord[][], frame: number) => {
  let index = 0;
  pages.forEach((page, i) => {
    if (i > 0 && page.length && page[0].from <= frame) index = i;
  });
  return index;
};
