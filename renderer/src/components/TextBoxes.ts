// Onde fica o texto grande dos cartões de texto (título, contagem, citação, texto cinético), em caixas de fração da
// tela: a mesma diagramação dos componentes de TextCards.tsx, estimada pela largura média da letra (`charEm`). Serve
// para o carimbo solto não cair em cima dele (`placeStamp`).
import type { SceneKind, TextCardStyle, TimelineScene } from '../types';
import { charEm } from './DataCards';
import { flowRows, KINETIC_CENTER_Y, kineticMarks, type Rect, scenePages } from './fx/math';

/** Fileiras centradas na tela, uma embaixo da outra, com o centro do bloco na altura `center` (fração; o meio). */
const stackRows = (rows: { width: number; height: number }[], gap: number, width: number, height: number, center = 0.5): Rect[] => {
  const total = rows.reduce((t, r) => t + r.height, 0) + gap * Math.max(0, rows.length - 1);
  let y = height * center - total / 2;
  return rows.map((r) => {
    const rect: Rect = [(width - r.width) / 2 / width, y / height, r.width / width, r.height / height];
    y += r.height + gap;
    return rect;
  });
};

/** Fileiras de um texto quebrado nas palavras em `maxWidth` com a letra `size`. */
const textRows = (text: string, em: number, size: number, maxWidth: number, lineHeight: number) => {
  const words = text.split(/\s+/).filter(Boolean);
  const rows = flowRows(words.map((w) => w.length * em * size), em * size * 0.55, maxWidth);
  return rows.map((r) => ({ width: Math.min(maxWidth, r.width), height: size * lineHeight }));
};

/**
 * Caixas do texto grande do cartão de texto da cena (vazio nos outros tipos). No texto cinético, as fileiras de todas
 * as páginas: o carimbo fica no mesmo lugar a cena inteira, então desvia de qualquer página.
 */
export const textCardRects = (
  kind: SceneKind,
  scene: TimelineScene,
  style: TextCardStyle,
  uppercase: boolean,
  width: number,
  height: number,
): Rect[] => {
  const em = charEm(style.font);
  if (kind === 'title' && scene.text.trim()) {
    // TitleCard: padding de 8%, letra de 10% da largura (7,5% no texto longo), traço de destaque embaixo.
    const size = width * (scene.text.length > 28 ? 0.075 : 0.1);
    const rows = textRows(scene.text, em, size, width * 0.84, 1.05);
    const block = Math.max(...rows.map((r) => r.width));
    rows.push({ width: block * 0.6, height: size * 0.25 + Math.max(3, width * 0.004) });
    return stackRows(rows, 0, width, height);
  }
  if (kind === 'count') {
    // CountCard: "#N" com 26% da largura e o nome do item embaixo.
    const big = width * 0.26;
    const label = scene.text.replace(/^#?\d+\s*[-–:.]?\s*/, '');
    const rows = [{ width: (String(scene.number ?? '').length + 1) * em * big, height: big * 0.9 }];
    if (label) rows.push(...textRows(label, em, width * 0.06, width, 1.2).map((r, i) => (i ? r : { ...r, height: r.height + width * 0.02 })));
    return stackRows(rows, 0, width, height);
  }
  if (kind === 'quote' && scene.text.trim()) {
    // QuoteCard: aspas grandes e o texto a 6,5% da largura, com padding de 10%.
    const rows = [{ width: width * 0.22 * 0.45, height: width * 0.22 * 0.6 }, ...textRows(scene.text, em, width * 0.065, width * 0.8, 1.2)];
    return stackRows(rows, 0, width, height);
  }
  if (kind === 'text') {
    // KineticText: palavras de 9,5% da largura (12% as de destaque), em até 92% da área sem as margens de 8%, com o
    // bloco centrado no centro óptico (`KINETIC_CENTER_Y`).
    const maxWidth = width * 0.84 * 0.92;
    const gapX = width * 0.025;
    return scenePages(scene).flatMap((page) => {
      const marks = kineticMarks(page);
      const sizes = page.map((_, i) => width * (marks[i] ? 0.12 : 0.095));
      const texts = page.map((w) => (uppercase ? w.text.toLocaleUpperCase() : w.text));
      const widths = texts.map((t, i) => t.length * em * sizes[i]);
      const rows = flowRows(widths, gapX, maxWidth).map((r, i, all) => {
        const end = i + 1 < all.length ? all[i + 1].start : page.length;
        return { width: Math.min(maxWidth, r.width), height: Math.max(...sizes.slice(r.start, end)) };
      });
      return stackRows(rows, width * 0.01, width, height, KINETIC_CENTER_Y);
    });
  }
  return [];
};
