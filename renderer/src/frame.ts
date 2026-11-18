// Reenquadramento de imagem de outra proporção (16:9 do longo no Short 9:16): onde recortar e onde fica o foco na tela.
import type { TimelineFrame } from './types';

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export type FrameLayout = {
  /** `objectPosition` do recorte (cover): a janela do vídeo centrada no foco, sem passar da borda da imagem. */
  position: string;
  /** O mesmo `objectPosition` em fração (0 a 1): converte retângulos do arquivo para a tela (`fx/math.ts`). */
  pos: [number, number];
  /** Onde o foco cai na tela (0 a 1): origem da escala da câmera. */
  origin: [number, number];
  /** Modo desfocado: caixa (0 a 1 da tela) da imagem inteira, por cima do fundo ampliado e desfocado. */
  front?: { left: number; top: number; width: number; height: number };
};

export const frameLayout = (frame: TimelineFrame, width: number, height: number): FrameLayout => {
  const video = width / height;
  const aspect = frame.aspect > 0 ? frame.aspect : video;
  const fx = clamp(frame.x, 0, 1);
  const fy = clamp(frame.y, 0, 1);
  let posX = 0.5;
  let posY = 0.5;
  let sx = fx;
  let sy = fy;
  if (aspect > video) {
    // Imagem mais larga que o vídeo: a janela é uma faixa vertical (fração `v` da largura).
    const v = video / aspect;
    const left = clamp(fx - v / 2, 0, 1 - v);
    posX = 1 - v > 1e-6 ? left / (1 - v) : 0.5;
    sx = (fx - left) / v;
  } else if (aspect < video) {
    const h = aspect / video;
    const top = clamp(fy - h / 2, 0, 1 - h);
    posY = 1 - h > 1e-6 ? top / (1 - h) : 0.5;
    sy = (fy - top) / h;
  }
  const position = `${(posX * 100).toFixed(3)}% ${(posY * 100).toFixed(3)}%`;
  const pos: [number, number] = [posX, posY];
  if (frame.mode !== 'blur') return { position, pos, origin: [sx, sy] };
  const fw = aspect >= video ? 1 : aspect / video;
  const fh = aspect >= video ? video / aspect : 1;
  const left = (1 - fw) / 2;
  const top = clamp((frame.anchor ?? 0.5) - fh / 2, 0, 1 - fh);
  return { position, pos, origin: [left + fx * fw, top + fy * fh], front: { left, top, width: fw, height: fh } };
};

/**
 * Caixa da imagem da cena no fundo (`ImageBox` de `fx/math.ts`): proporção do arquivo, recorte e, no modo desfocado,
 * a caixa da imagem inteira. Sem `frame` (proporção do arquivo desconhecida): null.
 */
export const sceneImageBox = (frame: TimelineFrame | undefined, width: number, height: number) => {
  if (!frame || !(frame.aspect > 0)) return null;
  const layout = frameLayout(frame, width, height);
  return { aspect: frame.aspect, pos: layout.pos, front: layout.front ?? null };
};
