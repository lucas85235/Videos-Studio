// Contas puras da edição no renderer (sem React nem Remotion): planos, câmera, tempo e duração dos eventos.
// Ficam fora dos componentes para o teste rodar no Node (`backend/tests/test_renderer_fx.py`): só `import type` aqui.
//
// Quadros relativos ao início da cena, como no contrato resolvido de `types.ts` (ADR 0004, fundação).
import type { FxType, ShotEasing, ShotFraming, TimelineFx, TimelineShot } from '../../types';

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

/** `transform` CSS (origem 0 0) do enquadramento; null no quadro inteiro. */
export const framingTransform = (framing: ShotFraming | null | undefined) => {
  const { scale, left, top } = framingBox(framing);
  if (scale <= 1.0001) return null;
  return `translate(${-left * scale * 100}%, ${-top * scale * 100}%) scale(${scale})`;
};

// --- eventos ---------------------------------------------------------------------------------------------------------

/**
 * Duração padrão (s) do evento que vem sem `dur` (o backend manda `dur_ms` None como 0). 0 = até o próximo corte
 * (câmera e anotações presas à imagem) ou até o fim da cena (contador).
 */
export const FX_DEFAULT_SECONDS: Record<FxType, number> = {
  punch: 0,
  shake: 0.4,
  zoom_rect: 0,
  freeze: 0.8,
  flash: 0.2,
  text_pop: 0.9,
  meme: 1.0,
  sfx: 0.3,
  highlight: 0,
  arrow: 1.5,
  circle: 1.5,
  counter: 0,
  music_drop: 0.4,
  speed: 1.0,
  whip: 0.27,
  pip: 2.5,
};

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

/** Janela `[from, to)` do evento na cena, com a duração padrão do tipo quando vem sem `dur`. */
export const fxSpan = (fx: TimelineFx, ctx: FxContext): Span => {
  const last = Math.max(0, ctx.frames - 1);
  const from = clamp(Math.round(num(fx.from, 0)), 0, last);
  const given = num(fx.dur, 0);
  const seconds = FX_DEFAULT_SECONDS[fx.type] ?? 1;
  let dur: number;
  if (given > 0) dur = given;
  else if (seconds > 0) dur = seconds * ctx.fps;
  else dur = (ENDS_AT_CUT.has(fx.type) ? nextCut(from, ctx.cuts, ctx.frames) : ctx.frames) - from;
  let to = Math.min(ctx.frames, from + Math.max(1, Math.round(dur)));
  if (ENDS_AT_CUT.has(fx.type) && fx.type !== 'whip') to = Math.min(to, nextCut(from, ctx.cuts, ctx.frames));
  return { from, to: Math.max(from + 1, to) };
};

// --- câmera ----------------------------------------------------------------------------------------------------------

/**
 * Câmera do fundo num quadro. `scale` e `x`/`y` (fração do quadro, origem no canto de cima à esquerda) fazem o
 * `translate(x) scale(scale)`; `rot` em graus (no centro) e `blur` em px (desfoque horizontal do whip).
 */
export type Camera = { scale: number; x: number; y: number; rot: number; blur: number };

export const IDENTITY: Camera = { scale: 1, x: 0, y: 0, rot: 0, blur: 0 };

export type CameraContext = FxContext & { width: number; height: number; seed: number };

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
      const amount = clamp(pNum(fx.params, 'amount', 1), 0, 3);
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
