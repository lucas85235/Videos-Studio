import { continueRender, delayRender, staticFile } from 'remotion';
import fonts from '../../assets/fonts/fonts.json';

type FontInfo = { file: string; weights: [number, number]; license: string };
const FONTS = fonts as unknown as Record<string, FontInfo>;

const loaded = new Map<string, Promise<void>>();

const fontUrl = (file: string, base: string | null | undefined) =>
  base ? `${base.replace(/\/$/, '')}/fonts/${file}` : staticFile(`fonts/${file}`);

export const fontStack = (family: string) => `"${family}", "Noto Sans", "Segoe UI", Arial, sans-serif`;

/** Carrega as fontes locais (OFL) antes do primeiro quadro. Funciona no render e no Player. */
export const ensureFonts = (families: string[], base?: string | null) => {
  if (typeof document === 'undefined' || typeof FontFace === 'undefined') {
    return;
  }
  const wanted = Array.from(new Set([...families, 'Noto Sans'])).filter((f) => FONTS[f]);
  const key = `${base ?? ''}|${wanted.sort().join(',')}`;
  if (loaded.has(key)) {
    return;
  }
  const handle = delayRender(`Carregando fontes: ${wanted.join(', ')}`);
  const promise = Promise.all(
    wanted.map(async (family) => {
      const info = FONTS[family];
      const face = new FontFace(family, `url(${fontUrl(info.file, base)})`, {
        weight: `${info.weights[0]} ${info.weights[1]}`,
      });
      try {
        await face.load();
        (document.fonts as unknown as { add: (f: FontFace) => void }).add(face);
      } catch (err) {
        console.warn('fonte não carregou', family, err);
      }
    }),
  ).then(() => {
    continueRender(handle);
  });
  loaded.set(key, promise);
};
