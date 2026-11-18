import React, { useState } from 'react';
import { AbsoluteFill, Img, interpolate, OffthreadVideo, Sequence, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../../fonts';
import { stickerFilter } from '../../sticker';
import type { RenderStyle, TimelineFx, TimelineScene } from '../../types';
import { EMOJI_STACK } from '../Captions';
import { charEm, fitSize, withAlpha } from '../DataCards';
import { assetUrl } from '../RealMedia';
import {
  anchoredAt,
  type Band,
  cornerStickerBox,
  EASE,
  type FxContext,
  type FxParams,
  fxRects,
  fxSpan,
  hash01,
  type ImageBox,
  pBool,
  pNum,
  pStr,
  type Rect,
  SCREEN_FX,
} from './math';

// Eventos dentro da cena (ADR 0004, `scene.fx`), já em quadros relativos à cena. Parâmetros lidos de `params` (as
// chaves do `FxEvent.params` do backend; ausente vale o padrão). Este bloco é o contrato de chaves com o planejador
// (`edit_plan.py`) e com o `timeline-fx` (`edit_resolve.resolve_fx`):
//
//   text_pop   text (1 a 3 palavras; vazio: a palavra dita no gatilho), color, emoji, y (0..1), scale, font
//   meme       src (png com alfa) | video (webm com alfa) | emoji, variant full|corner|freeze_zoom, side, outline.
//              O planejador grava `lib_id` (id da `meme_library`) e `tag`: quem traduz `lib_id` em `src`/`video`
//              (caminho relativo ao assetBaseUrl, `library/memes/<arquivo>`) é o timeline-fx. Meme sem mídia
//              desenhável não some calado: avisa no console (a conferência lê o log do render).
//   flash      color, amount (opacidade inicial, 0..1)
//   freeze     flash (bool: clarão de 3 quadros na entrada). O congelamento em si é o TimeWarp do fundo.
//   counter    text ("3/10") | n e total, side left|right
//   pip        src | video, clip_in (s), side left|right, at top|bottom, size (fração da largura), aspect
//   highlight  rect [x,y,w,h] | rects (4·n números), color, dark (bool)      (presos à imagem: FxAnchored)
//   arrow      rect | x e y (alvo), side left|right|top|bottom, color
//   circle     rect | x, y e r (fração do menor lado), color
//   punch      scale, x e y (ponto fixo na tela)                              (câmera: CameraRig)
//   shake      strength (do planejador, 0,6 a 1,2; `amount` é o nome antigo)
//   zoom_rect  rect, fill, max
//   speed      rate
//
// `rect` vem no espaço do ARQUIVO (0 a 1 da imagem inteira, como `MediaRef.rects`); o renderer converte para o
// recorte (`fxRects`/`cameraFx` em math.ts). `params.space = 'frame'` diz que ele já está no quadro do fundo. `x`/`y`
// já estão no quadro. punch, shake, zoom_rect e whip ficam no CameraRig; freeze e speed também mexem no tempo do
// fundo (TimeWarp); sfx e music_drop são só áudio (mixados no FFmpeg).

const clampOpts = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
/** Quadros da saída dos elementos que somem antes do fim da cena. */
const EXIT = 5;

const exitK = (f: number, len: number, frames = EXIT) => interpolate(f, [len - frames, len], [0, 1], clampOpts);

/** Palavra dita no quadro `at` da cena (texto padrão do `text_pop`). */
const wordAt = (scene: TimelineScene, at: number) => {
  const words = scene.words.length ? scene.words : scene.captions.flatMap((l) => l.words);
  const hit = words.find((w) => at >= w.from && at < w.to) ?? words.find((w) => w.from >= at);
  return (hit?.text ?? '').replace(/[.,;:!?…"“”]+$/u, '');
};

type ScreenProps = {
  scene: TimelineScene;
  fx: TimelineFx[] | undefined;
  style: RenderStyle;
  assetBaseUrl: string | null | undefined;
  cuts: number[];
  /** Centro e meia altura da legenda (fração da altura) e se ela aparece: o texto que pula e o meme desviam dela. */
  captionMid: number;
  captionHalf: number;
  captionsShown: boolean;
  /** Topo livre (abaixo da faixa do topo), para o contador. */
  minTop: number;
  lang: string;
};

/** Eventos de tela por cima do fundo e das texturas, abaixo do cartão e das legendas. Sem evento de tela: nada. */
export const FxLayer: React.FC<ScreenProps> = ({ scene, fx, style, assetBaseUrl, cuts, captionMid, captionHalf, captionsShown, minTop, lang }) => {
  const { fps } = useVideoConfig();
  const ctx: FxContext = { fps, frames: scene.durationInFrames, cuts };
  const items = (fx ?? []).filter((e) => SCREEN_FX.has(e.type));
  if (!items.length) return null;
  // Faixa da legenda com uma margem (o meme de canto sobe para fora dela).
  const captionBand: Band | null = captionsShown ? [captionMid - captionHalf - 0.02, captionMid + captionHalf + 0.02] : null;
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      {items.map((e, i) => {
        const span = fxSpan(e, ctx);
        const len = span.to - span.from;
        const params = (e.params ?? {}) as FxParams;
        let node: React.ReactNode = null;
        switch (e.type) {
          case 'text_pop':
            node = (
              <TextPop
                params={params}
                len={len}
                text={pStr(params, 'text') || wordAt(scene, span.from)}
                style={style}
                avoid={captionsShown ? captionMid : null}
                seed={`${scene.seed}-${span.from}`}
                lang={lang}
              />
            );
            break;
          case 'meme':
            node = (
              <Meme
                params={params}
                len={len}
                assetBaseUrl={assetBaseUrl}
                seed={`${scene.seed}-${span.from}`}
                caption={captionBand}
                where={`${scene.id}@${span.from}`}
              />
            );
            break;
          case 'flash':
            node = <Flash color={pStr(params, 'color', '#FFFFFF')} amount={pNum(params, 'amount', 0.85)} len={len} />;
            break;
          case 'freeze':
            node = pBool(params, 'flash', true) ? <Flash color="#FFFFFF" amount={0.55} len={Math.min(len, 4)} /> : null;
            break;
          case 'counter':
            node = <Counter params={params} len={len} style={style} minTop={minTop} last={span.to >= scene.durationInFrames} />;
            break;
          case 'pip':
            node = <Pip params={params} len={len} assetBaseUrl={assetBaseUrl} last={span.to >= scene.durationInFrames} />;
            break;
          default:
            node = null;
        }
        return node ? (
          <Sequence key={`${e.type}-${i}`} from={span.from} durationInFrames={len} layout="none" name={`fx ${e.type}`}>
            {node}
          </Sequence>
        ) : null;
      })}
    </AbsoluteFill>
  );
};

// --- texto que pula ------------------------------------------------------------------------------------------------

const TextPop: React.FC<{
  params: FxParams;
  len: number;
  text: string;
  style: RenderStyle;
  avoid: number | null;
  seed: string;
  lang: string;
}> = ({ params, len, text, style, avoid, seed, lang }) => {
  const f = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const words = text.trim().split(/\s+/).filter(Boolean).slice(0, 3);
  if (!words.length) return null;
  const upper = pBool(params, 'upper', true);
  const emoji = pStr(params, 'emoji').trim();
  const label = upper ? words.join(' ').toLocaleUpperCase(lang) : words.join(' ');
  const font = pStr(params, 'font', style.textCards.font);
  const portrait = height > width;
  const maxWidth = width * (portrait ? 0.86 : 0.72);
  const size = fitSize(label + (emoji ? '  ' : ''), font, maxWidth, portrait ? width * 0.19 : height * 0.2, height * 0.05) * Math.min(1.4, Math.max(0.5, pNum(params, 'scale', 1)));
  // Altura: o pedido, ou acima do meio; desvia da legenda (o texto nunca cobre a fala).
  let y = pNum(params, 'y', portrait ? 0.4 : 0.42);
  if (avoid !== null && Math.abs(y - avoid) < 0.17) y = avoid > 0.5 ? avoid - 0.22 : avoid + 0.22;
  y = Math.min(0.85, Math.max(0.15, y));
  // Bounce: mola frouxa (passa de 1 e volta), rotação de ±4° pela semente, que assenta um pouco.
  const pop = spring({ frame: f, fps, config: { damping: 7, stiffness: 210, mass: 0.55 } });
  const out = exitK(f, len, 4);
  const tilt = (hash01(seed) > 0.5 ? 1 : -1) * (2.5 + 1.5 * hash01(`${seed}-r`));
  const rot = tilt + (1 - Math.min(1, pop)) * -tilt * 2;
  const scale = interpolate(pop, [0, 1], [0.35, 1]) * (1 + 0.12 * out);
  const color = pStr(params, 'color', style.captions.highlight);
  const stroke = Math.max(3, size * 0.055);
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: y * height,
        display: 'flex',
        justifyContent: 'center',
        transform: `translateY(-50%) scale(${scale}) rotate(${rot}deg)`,
        opacity: 1 - out,
      }}
    >
      <span
        style={{
          fontFamily: fontStack(font),
          fontWeight: 900,
          fontSize: size,
          lineHeight: 1,
          color,
          WebkitTextStroke: `${stroke}px #000`,
          paintOrder: 'stroke fill',
          textShadow: `0 ${size * 0.06}px ${size * 0.12}px rgba(0,0,0,0.6)`,
          letterSpacing: '0.01em',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
        {emoji ? <span style={{ fontFamily: EMOJI_STACK, WebkitTextStroke: '0px transparent', marginLeft: size * 0.2 }}>{emoji}</span> : null}
      </span>
    </div>
  );
};

// --- meme ----------------------------------------------------------------------------------------------------------

/** Avisos de meme sem mídia já dados (um por meme e cena, não um por quadro). */
const warnedMemes = new Set<string>();

/** Meme que chegou sem nada para desenhar: avisa uma vez no console (o log do render vai para a conferência). */
export const warnMemeWithoutMedia = (params: FxParams, where: string) => {
  const libId = pStr(params, 'lib_id');
  const key = `${where}:${libId}`;
  if (warnedMemes.has(key)) return;
  warnedMemes.add(key);
  console.warn(
    libId
      ? `[fx] meme "${libId}" (${where}) sem src/video: o lib_id da meme_library não foi resolvido em arquivo; o meme não aparece`
      : `[fx] meme (${where}) sem src, video nem emoji: nada para desenhar`,
  );
};

const Meme: React.FC<{
  params: FxParams;
  len: number;
  assetBaseUrl: string | null | undefined;
  seed: string;
  /** Faixa da legenda visível (fração da altura), ou null: o adesivo de canto não a cobre. */
  caption: Band | null;
  /** Cena e quadro do evento, para o aviso de meme sem mídia. */
  where: string;
}> = ({ params, len, assetBaseUrl, seed, caption, where }) => {
  const f = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  // Arquivo que sumiu (meme apagado da biblioteca, pose refeita): some o meme, não o render inteiro (o Remotion
  // cancela o render no 404 de <Img>/<OffthreadVideo> sem onError). O backend já tira o caminho que não existe.
  const [broken, setBroken] = useState(false);
  const src = broken ? null : assetUrl(pStr(params, 'src') || null, assetBaseUrl);
  const video = broken ? null : assetUrl(pStr(params, 'video') || null, assetBaseUrl);
  const emoji = pStr(params, 'emoji').trim();
  if (!src && !video && !emoji) {
    // "Meme sem meme" vem como freeze + punch + text_pop, não aqui. O congelamento e o punch do `freeze_zoom` (TimeWarp
    // e CameraRig) acontecem mesmo assim; só o adesivo falta.
    warnMemeWithoutMedia(params, where);
    return null;
  }
  const variant = pStr(params, 'variant', 'corner');
  const scale = height / 1080;
  const out = exitK(f, len);
  const media = (h: number, filter?: string) =>
    video ? (
      <OffthreadVideo
        src={video}
        muted
        transparent
        onError={() => setBroken(true)}
        style={{ height: h, width: 'auto', display: 'block', filter }}
      />
    ) : src ? (
      <Img src={src} maxRetries={1} onError={() => setBroken(true)} style={{ height: h, width: 'auto', display: 'block', filter }} />
    ) : (
      <span style={{ fontFamily: EMOJI_STACK, fontSize: h * 0.8, lineHeight: 1, display: 'block', filter }}>{emoji}</span>
    );

  if (variant === 'full') {
    // Corte para a reação: a tela inteira é o meme (sem contorno), sobre o fundo escurecido.
    const enter = interpolate(f, [0, 3], [1.08, 1], clampOpts);
    return (
      <AbsoluteFill
        style={{ backgroundColor: 'rgba(0,0,0,0.86)', alignItems: 'center', justifyContent: 'center', opacity: 1 - out }}
      >
        <div style={{ transform: `scale(${enter})` }}>{media(height * 0.92)}</div>
      </AbsoluteFill>
    );
  }
  const pop = spring({ frame: f, fps, config: { damping: 11, stiffness: 190, mass: 0.6 } });
  const outline = pNum(params, 'outline', 6);
  const filter = stickerFilter(outline, scale);
  const s = interpolate(pop, [0, 1], [0.2, 1]) * (1 - 0.4 * out);
  if (variant === 'freeze_zoom') {
    // O fundo congela e dá zoom (TimeWarp + CameraRig); a reação entra no meio, como adesivo.
    return (
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', opacity: 1 - out }}>
        <div style={{ transform: `scale(${s}) rotate(${(hash01(seed) - 0.5) * 8}deg)` }}>{media(height * 0.5, filter)}</div>
      </AbsoluteFill>
    );
  }
  // Canto: adesivo embaixo, do lado pedido, inclinado para o centro; acima (ou abaixo) da legenda, nunca em cima dela.
  const left = pStr(params, 'side', 'right') === 'left';
  const place = cornerStickerBox(height > width, caption);
  const h = height * place.height;
  const tilt = (left ? 1 : -1) * (4 + 4 * hash01(seed));
  return (
    <div
      style={{
        position: 'absolute',
        [left ? 'left' : 'right']: width * 0.04,
        bottom: height * place.bottom,
        transform: `scale(${s}) rotate(${tilt * (2 - Math.min(1, pop))}deg)`,
        transformOrigin: left ? '0% 100%' : '100% 100%',
        opacity: 1 - out,
      }}
    >
      {media(h, filter)}
    </div>
  );
};

// --- clarão --------------------------------------------------------------------------------------------------------

const Flash: React.FC<{ color: string; amount: number; len: number }> = ({ color, amount, len }) => {
  const f = useCurrentFrame();
  const a = Math.min(1, Math.max(0, amount)) * (1 - EASE.outCubic((f + 1) / Math.max(1, len)));
  return <AbsoluteFill style={{ backgroundColor: color, opacity: a }} />;
};

// --- contador ------------------------------------------------------------------------------------------------------

/** Texto do contador ("2/5"): `text`, ou `n`/`total`. */
const counterText = (params: FxParams) => {
  const n = pNum(params, 'n', NaN);
  const total = pNum(params, 'total', NaN);
  return pStr(params, 'text') || (Number.isFinite(n) ? (Number.isFinite(total) ? `${n}/${total}` : `${n}`) : '');
};

/** Caixa [x, y, w, h] (fração da tela) do contador no canto de cima, para o carimbo solto desviar dele. */
export const counterRect = (params: FxParams, width: number, height: number, minTop: number): Rect | null => {
  const text = counterText(params);
  if (!text) return null;
  const size = Math.min(width, height) * 0.06;
  const w = (text.length * 0.62 + 0.84) * size;
  const side = width * (height > width ? 0.06 : 0.04);
  const x = pStr(params, 'side', 'right') === 'left' ? side : width - side - w;
  return [x / width, Math.max(minTop, 0.05) + (size * 0.2) / height, w / width, (size * 1.34) / height];
};

const Counter: React.FC<{ params: FxParams; len: number; style: RenderStyle; minTop: number; last: boolean }> = ({
  params,
  len,
  style,
  minTop,
  last,
}) => {
  const f = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const text = counterText(params);
  if (!text) return null;
  const cards = style.textCards;
  const left = pStr(params, 'side', 'right') === 'left';
  const size = Math.min(width, height) * 0.06;
  const pop = spring({ frame: f, fps, config: { damping: 10, stiffness: 220, mass: 0.5 } });
  const out = last ? 0 : exitK(f, len);
  return (
    <div
      style={{
        position: 'absolute',
        top: Math.max(minTop, 0.05) * height + size * 0.2,
        [left ? 'left' : 'right']: width * (height > width ? 0.06 : 0.04),
        transform: `scale(${interpolate(pop, [0, 1], [0.3, 1]) * (1 - 0.3 * out)})`,
        transformOrigin: left ? '0% 0%' : '100% 0%',
        opacity: 1 - out,
        padding: `${size * 0.12}px ${size * 0.42}px`,
        borderRadius: size * 0.3,
        backgroundColor: cards.accent,
        color: cards.background,
        fontFamily: fontStack(cards.font),
        fontWeight: 900,
        fontSize: size,
        lineHeight: 1.1,
        boxShadow: `0 ${size * 0.12}px ${size * 0.4}px ${withAlpha('#000000', 0.45)}`,
      }}
    >
      {text}
    </div>
  );
};

// --- imagem dentro da imagem -----------------------------------------------------------------------------------------

const Pip: React.FC<{ params: FxParams; len: number; assetBaseUrl: string | null | undefined; last: boolean }> = ({
  params,
  len,
  assetBaseUrl,
  last,
}) => {
  const f = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const [broken, setBroken] = useState(false); // arquivo que sumiu: some o inserto, não o render
  const src = broken ? null : assetUrl(pStr(params, 'src') || null, assetBaseUrl);
  const video = broken ? null : assetUrl(pStr(params, 'video') || null, assetBaseUrl);
  if (!src && !video) return null;
  const left = pStr(params, 'side', 'right') === 'left';
  const bottom = pStr(params, 'at', 'top') === 'bottom';
  const portrait = height > width;
  const w = width * Math.min(0.7, Math.max(0.15, pNum(params, 'size', portrait ? 0.46 : 0.32)));
  const h = w / Math.max(0.3, pNum(params, 'aspect', 16 / 9));
  const minSide = Math.min(width, height);
  const border = Math.max(3, minSide * 0.006);
  const enter = spring({ frame: f, fps, config: { damping: 14, stiffness: 200, mass: 0.6 } });
  const out = last ? 0 : exitK(f, len, 6);
  const slide = (1 - enter + out) * (w + width * 0.06) * (left ? -1 : 1);
  const clipIn = pNum(params, 'clip_in', 0);
  return (
    <div
      style={{
        position: 'absolute',
        [left ? 'left' : 'right']: width * 0.04,
        [bottom ? 'bottom' : 'top']: height * (portrait ? (bottom ? 0.26 : 0.12) : 0.07),
        width: w,
        height: h,
        transform: `translateX(${slide}px) rotate(${(left ? -1 : 1) * 1.5}deg)`,
        border: `${border}px solid #fff`,
        borderRadius: border * 2.5,
        overflow: 'hidden',
        backgroundColor: '#000',
        boxShadow: `0 ${minSide * 0.02}px ${minSide * 0.05}px rgba(0,0,0,0.55)`,
      }}
    >
      {video ? (
        <OffthreadVideo
          src={video}
          muted
          trimBefore={clipIn > 0 ? Math.round(clipIn * fps) : undefined}
          onError={() => setBroken(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      ) : (
        <Img src={src!} maxRetries={1} onError={() => setBroken(true)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      )}
    </div>
  );
};

// --- presos à imagem -------------------------------------------------------------------------------------------------

type AnchoredProps = {
  fx: TimelineFx[] | undefined;
  /**
   * Quadro REAL da cena, vindo de fora do TimeWarp: dentro dele o relógio é o do fundo (parado no `freeze`, lento no
   * `speed`) e a anotação entraria atrasada pelo trecho congelado, fora da palavra-gatilho.
   */
  frame: number;
  frames: number;
  cuts: number[];
  seed: number;
  /** Caixa da imagem neste contêiner (converte o `rect` do arquivo para o recorte); null: o retângulo vale como está. */
  box: ImageBox | null;
};

/**
 * Marca-texto, seta e círculo, desenhados DENTRO do fundo (no mesmo transform da imagem): andam com o Ken Burns, o
 * enquadramento do plano e a câmera de eventos, e ficam embaixo das texturas. O tempo deles é o da cena (`frame`),
 * não o do fundo: o desenho entra no gatilho mesmo depois de um congelamento.
 */
export const FxAnchored: React.FC<AnchoredProps> = ({ fx, frame, frames, cuts, seed, box }) => {
  const { fps, width, height } = useVideoConfig();
  const ctx: FxContext = { fps, frames, cuts };
  const items = anchoredAt(fx, frame, ctx);
  if (!items.length) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      {items.map(({ fx: e, index, span, f, len, last }) => {
        const params = (e.params ?? {}) as FxParams;
        const rects = fxRects(params, box, width / height);
        return e.type === 'highlight' ? (
          <Highlight key={`${e.type}-${index}`} params={params} rects={rects} f={f} len={len} last={last} />
        ) : (
          <Annot
            key={`${e.type}-${index}`}
            kind={e.type === 'arrow' ? 'arrow' : 'circle'}
            params={params}
            rect={rects[0]}
            f={f}
            len={len}
            last={last}
            seed={`${seed}-${span.from}`}
          />
        );
      })}
    </AbsoluteFill>
  );
};

/** Marca-texto varrendo os retângulos um depois do outro (porte do `WebShot` do canais, com câmera e cor do evento). */
const Highlight: React.FC<{ params: FxParams; rects: Rect[]; f: number; len: number; last: boolean }> = ({ params, rects, f, len, last }) => {
  if (!rects.length) return null;
  const dark = pBool(params, 'dark', false);
  const color = pStr(params, 'color', dark ? 'rgba(255,214,60,0.42)' : 'rgba(255,226,70,0.62)');
  const out = last ? 0 : exitK(f, len, 4);
  return (
    <AbsoluteFill style={{ opacity: 1 - out }}>
      {rects.map(([x, y, w, h], j) => {
        const st = j * 9;
        const p = EASE.outCubic(interpolate(f, [st, st + 10], [0, 1], clampOpts));
        const padX = 0.004;
        const padY = h * 0.08;
        return (
          <div
            key={j}
            style={{
              position: 'absolute',
              left: `${(x - padX) * 100}%`,
              top: `${(y - padY) * 100}%`,
              width: `${(w + padX * 2) * p * 100}%`,
              height: `${(h + padY * 2) * 100}%`,
              backgroundColor: color,
              mixBlendMode: dark ? 'normal' : 'multiply',
              borderRadius: 4,
              boxShadow: dark ? '0 0 0 2px rgba(255,214,60,0.9)' : undefined,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

const center = (r: Rect) => [r[0] + r[2] / 2, r[1] + r[3] / 2] as const;

/** Seta e círculo de destaque em SVG, desenhados pelo traço (`stroke-dashoffset`), com a ponta da seta entrando depois. */
/** `rect`: o alvo já no contêiner (`fxRects`); sem ele, `x`/`y` do evento. `f`: quadro desde o gatilho. */
const Annot: React.FC<{
  kind: 'arrow' | 'circle';
  params: FxParams;
  rect: Rect | undefined;
  f: number;
  len: number;
  last: boolean;
  seed: string;
}> = ({ kind, params, rect, f, len, last, seed }) => {
  const { width, height } = useVideoConfig();
  const minSide = Math.min(width, height);
  const [cxF, cyF] = rect ? center(rect) : [pNum(params, 'x', 0.5), pNum(params, 'y', 0.5)];
  const cx = cxF * width;
  const cy = cyF * height;
  const color = pStr(params, 'color', '#FF2D2D');
  const stroke = Math.max(4, minSide * 0.011);
  const out = last ? 0 : exitK(f, len, 4);
  const draw = EASE.outCubic(interpolate(f, [0, kind === 'arrow' ? 8 : 11], [0, 1], clampOpts));
  let d: string;
  let length: number;
  let head: React.ReactNode = null;
  if (kind === 'circle') {
    // Elipse à mão: um pouco mais de uma volta, raio com ondulação leve e começo sorteado.
    const rx = rect ? (rect[2] * width) / 2 * 1.22 + minSide * 0.012 : pNum(params, 'r', 0.12) * minSide;
    const ry = rect ? (rect[3] * height) / 2 * 1.35 + minSide * 0.012 : pNum(params, 'r', 0.12) * minSide;
    const start = -Math.PI * (0.4 + 0.3 * hash01(seed));
    const phase = hash01(`${seed}-w`) * Math.PI * 2;
    const steps = 56;
    const turns = 1.1;
    const pts: [number, number][] = [];
    for (let i = 0; i <= steps; i += 1) {
      const t = start + (i / steps) * turns * Math.PI * 2;
      const wob = 1 + 0.035 * Math.sin(3 * t + phase) + 0.02 * (i / steps);
      pts.push([cx + Math.cos(t) * rx * wob, cy + Math.sin(t) * ry * wob]);
    }
    d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
    length = pts.reduce((acc, p, i) => (i ? acc + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0), 0);
  } else {
    // Seta: vem de fora (lado pedido, ou o lado de baixo oposto ao alvo) e para na borda do alvo, com leve curva.
    const side = pStr(params, 'side') || (cxF > 0.5 ? 'left' : 'right');
    const dirs: Record<string, [number, number]> = { left: [-0.85, 0.53], right: [0.85, 0.53], top: [0.4, -0.92], bottom: [0.4, 0.92] };
    const [ux, uy] = dirs[side] ?? dirs.left;
    const gap = rect ? Math.max((rect[2] * width) / 2, (rect[3] * height) / 2) * 0.9 + minSide * 0.02 : minSide * 0.03;
    const tipX = cx + ux * gap;
    const tipY = cy + uy * gap;
    const reach = minSide * 0.24;
    const sx = tipX + ux * reach;
    const sy = tipY + uy * reach;
    const bend = reach * 0.18;
    const qx = (sx + tipX) / 2 - uy * bend;
    const qy = (sy + tipY) / 2 + ux * bend;
    d = `M${sx.toFixed(1)} ${sy.toFixed(1)} Q${qx.toFixed(1)} ${qy.toFixed(1)} ${tipX.toFixed(1)} ${tipY.toFixed(1)}`;
    length = reach * 1.08;
    // Ponta: duas abas a ±28° da direção de chegada (do controle para a ponta).
    const ang = Math.atan2(tipY - qy, tipX - qx);
    const wing = stroke * 4.2;
    const a1 = ang + Math.PI - 0.5;
    const a2 = ang + Math.PI + 0.5;
    const grow = interpolate(f, [6, 10], [0, 1], clampOpts);
    head = grow > 0 ? (
      <path
        d={`M${(tipX + Math.cos(a1) * wing * grow).toFixed(1)} ${(tipY + Math.sin(a1) * wing * grow).toFixed(1)} L${tipX.toFixed(1)} ${tipY.toFixed(1)} L${(
          tipX +
          Math.cos(a2) * wing * grow
        ).toFixed(1)} ${(tipY + Math.sin(a2) * wing * grow).toFixed(1)}`}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ) : null;
  }
  return (
    <AbsoluteFill style={{ opacity: 1 - out }}>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        style={{ position: 'absolute', inset: 0, overflow: 'visible', filter: `drop-shadow(0 ${stroke * 0.5}px ${stroke}px rgba(0,0,0,0.5))` }}
      >
        <path
          d={d}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - draw)}
        />
        {head}
      </svg>
    </AbsoluteFill>
  );
};
