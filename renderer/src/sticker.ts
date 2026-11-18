// Contorno de adesivo compartilhado: avatar do apresentador (PresenterLayer) e memes (fx/FxLayer).

/** Contorno branco de adesivo (4 drop-shadows) e a sombra, com `outline` em px a 1080 de altura. */
export const stickerFilter = (outline: number, scale: number) => {
  const o = Math.max(0, Math.round(outline * scale * 10) / 10);
  const shadow = `drop-shadow(0 ${Math.round(26 * scale)}px ${Math.round(30 * scale)}px rgba(0,0,0,0.45))`;
  if (!o) return shadow;
  return `drop-shadow(${o}px 0 0 #fff) drop-shadow(-${o}px 0 0 #fff) drop-shadow(0 ${o}px 0 #fff) drop-shadow(0 -${o}px 0 #fff) ${shadow}`;
};
