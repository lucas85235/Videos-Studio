import type { TimelineCard, TimelineProps, TimelineScene } from './types';

/** Props de exemplo (Remotion Studio e testes de fumaça). */
export const sampleProps: TimelineProps = {
  fps: 30,
  width: 1080,
  height: 1920,
  durationInFrames: 570,
  isChunk: false,
  lang: 'pt-BR',
  assetBaseUrl: null,
  fontBaseUrl: null,
  style: {
    family: 'still_cinema',
    families: ['still_cinema', 'analog'],
    effects: [
      { type: 'grade', amount: 0.7, preset: 'cold-teal-dark' },
      { type: 'grain', amount: 0.35 },
      { type: 'vignette', amount: 0.55 },
    ],
    captions: {
      style: 'karaoke',
      font: 'Montserrat',
      weight: 900,
      size: 84,
      color: '#FFFFFF',
      highlight: '#FFD400',
      stroke: 8,
      stroke_color: '#000000',
      position: 'center',
      max_words_per_line: 3,
      uppercase: true,
      background: null,
    },
    textCards: { font: 'Bebas Neue', color: '#F2F2F2', accent: '#C9A227', background: '#07080B', animation: 'fade_up' },
    zoomStrength: 0.14,
    transitionFrames: 9,
    progressBar: true,
    progressColor: '#FFD400',
  },
  scenes: [
    {
      id: 'scn_a',
      kind: 'title',
      from: 0,
      durationInFrames: 60,
      src: null,
      motion: 'ken_burns_in',
      transition: 'cut',
      seed: 1,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: 'O FAROL ESQUECIDO',
      number: null,
      captions: [],
      words: [],
    },
    {
      id: 'scn_b',
      kind: 'image',
      from: 60,
      durationInFrames: 90,
      src: null,
      motion: 'punch_zoom',
      transition: 'flash',
      seed: 2,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: '',
      number: null,
      captions: [
        {
          from: 0,
          to: 45,
          words: [
            { text: 'Ninguém', from: 0, to: 12 },
            { text: 'sabe', from: 12, to: 22 },
            { text: 'explicar', from: 22, to: 40 },
          ],
        },
      ],
      words: [],
      card: { headline: 'O mistério do farol', source: 'Arquivo Naval', stamp: '1900', stamp_at: 'left', stamp_from: 14 },
    },
    {
      id: 'scn_c',
      kind: 'stat',
      from: 150,
      durationInFrames: 90,
      src: null,
      motion: 'static',
      transition: 'cut',
      seed: 3,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: '',
      number: null,
      captions: [
        {
          from: 0,
          to: 50,
          words: [
            { text: 'quatrocentos', from: 0, to: 18 },
            { text: 'e', from: 18, to: 22 },
            { text: 'vinte', from: 22, to: 34 },
          ],
        },
      ],
      words: [],
      card: { value: '420 MI', label: 'navios desaparecidos desde 1900', source: 'Marinha', stamp: 'Oficial' },
      reveal: 12,
    },
    {
      id: 'scn_d',
      kind: 'compare',
      from: 240,
      durationInFrames: 90,
      src: null,
      motion: 'static',
      transition: 'flash',
      seed: 4,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: '',
      number: null,
      captions: [],
      words: [],
      card: {
        label: 'Naufrágios por século',
        items: [
          { label: 'Século XIX', value: '1.200' },
          { label: 'Século XX', value: '310' },
        ],
        highlight: 0,
      },
    },
    {
      // Foto real (mídia anexada): o src chega enquadrado pelo backend; o crédito vai na descrição, não na tela.
      id: 'scn_e',
      kind: 'image',
      from: 330,
      durationInFrames: 105,
      src: null,
      motion: 'ken_burns_in',
      transition: 'cut',
      seed: 5,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: '',
      number: null,
      captions: [
        {
          from: 0,
          to: 50,
          words: [
            { text: 'O', from: 0, to: 6 },
            { text: 'farol', from: 6, to: 20 },
            { text: 'de', from: 20, to: 26 },
            { text: 'verdade', from: 26, to: 46 },
          ],
        },
      ],
      words: [],
      focus: 'right',
    },
    {
      // Notícia anexada: cartão desenhado sobre a própria imagem da notícia, desfocada.
      id: 'scn_f',
      kind: 'image',
      from: 435,
      durationInFrames: 135,
      src: null,
      motion: 'drift',
      transition: 'flash',
      seed: 6,
      look: { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null },
      text: '',
      number: null,
      captions: [
        {
          from: 0,
          to: 60,
          words: [
            { text: 'Até', from: 0, to: 10 },
            { text: 'a', from: 10, to: 14 },
            { text: 'imprensa', from: 14, to: 34 },
            { text: 'noticiou', from: 34, to: 56 },
          ],
        },
      ],
      words: [],
      news: {
        outlet: 'Jornal do Litoral',
        headline: 'Farol abandonado volta a acender sozinho depois de 40 anos apagado',
        date: '14 nov. 2026',
        quoted: true,
      },
    },
  ],
};

const blank = { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null };
const dataScene = (id: string, n: number, kind: 'chart' | 'map', card: TimelineCard): TimelineScene => ({
  id,
  kind,
  from: n * 90,
  durationInFrames: 90,
  src: null,
  motion: 'static',
  transition: n ? 'fade_black' : 'cut',
  seed: 10 + n,
  look: blank,
  text: '',
  number: null,
  captions: [
    {
      from: 0,
      to: 60,
      words: [
        { text: 'Os', from: 0, to: 10 },
        { text: 'números', from: 10, to: 30 },
        { text: 'mostram', from: 30, to: 55 },
      ],
    },
  ],
  words: [],
  card,
});

/** Família de dados e geografia (16:9): barras, linha, rosca, pizza e mapas (países e estados dos EUA). */
export const sampleDataProps: TimelineProps = {
  ...sampleProps,
  width: 1920,
  height: 1080,
  durationInFrames: 540,
  style: {
    ...sampleProps.style,
    family: 'data_geo',
    families: ['data_geo', 'motion_graphics'],
    effects: [{ type: 'vignette', amount: 0.08 }],
    captions: {
      ...sampleProps.style.captions,
      style: 'line',
      font: 'IBM Plex Sans',
      weight: 600,
      size: 44,
      position: 'bottom',
      uppercase: false,
      max_words_per_line: 8,
    },
    textCards: { font: 'Oswald', color: '#F1F5F9', accent: '#F4B942', background: '#0B2233', animation: 'slide' },
    progressBar: false,
  },
  scenes: [
    dataScene('dg_bar', 0, 'chart', {
      label: 'Exportações de soja (MI t)',
      chart: 'bar',
      items: [
        { label: 'Brasil', value: '101', amount: 101 },
        { label: 'EUA', value: '46', amount: 46 },
        { label: 'Paraguai', value: '7', amount: 7 },
        { label: 'Argentina', value: '4,2', amount: 4.2 },
      ],
      highlight: 0,
      source: 'USDA',
    }),
    dataScene('dg_line', 1, 'chart', {
      label: 'População do Brasil (MI)',
      chart: 'line',
      items: ['1960:72', '1970:95', '1980:121', '1991:147', '2000:170', '2010:191', '2022:203'].map((p) => {
        const [label, value] = p.split(':');
        return { label, value, amount: Number(value) };
      }),
      source: 'IBGE',
    }),
    dataScene('dg_donut', 2, 'chart', {
      label: 'Matriz elétrica',
      chart: 'donut',
      items: [
        { label: 'Hidrelétrica', value: '58%', amount: 58 },
        { label: 'Eólica', value: '14%', amount: 14 },
        { label: 'Solar', value: '9%', amount: 9 },
        { label: 'Outras', value: '19%', amount: 19 },
      ],
      highlight: 0,
    }),
    dataScene('dg_pie', 3, 'chart', {
      label: 'Onde se joga',
      chart: 'pie',
      items: [
        { label: 'Celular', value: '48%', amount: 48 },
        { label: 'Console', value: '28%', amount: 28 },
        { label: 'PC', value: '24%', amount: 24 },
      ],
    }),
    dataScene('dg_map', 4, 'map', {
      label: 'Onde fica o Pantanal',
      items: [
        { label: 'Brasil', value: '62%', amount: 62, region: 'BRA' },
        { label: 'Bolívia', value: '20%', amount: 20, region: 'BOL' },
        { label: 'Paraguai', value: '18%', amount: 18, region: 'PRY' },
      ],
      highlight: 0,
      source: 'WWF',
    }),
    dataScene('dg_states', 5, 'map', {
      label: 'Os maiores estados',
      items: [
        { label: 'Texas', value: '696 MIL KM²', region: 'US-TX' },
        { label: 'Califórnia', value: '424 MIL KM²', region: 'US-CA' },
      ],
      highlight: 0,
    }),
  ],
};

// --- edição (ADR 0004): planos, eventos e clipe ---------------------------------------------------------------------

/** Imagem de exemplo embutida (SVG próprio, sem arquivo nem rede). */
const svg = (w: number, h: number, body: string) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`)}`;

/** "Captura" de página desenhada: barras de texto cinza numa página clara (o marca-texto cai na 3ª linha). */
const SAMPLE_PAGE = svg(
  1920,
  1080,
  '<rect width="1920" height="1080" fill="#f4f1ea"/><rect width="1920" height="90" fill="#1b2a3a"/>' +
    '<rect x="160" y="160" width="1200" height="64" rx="6" fill="#222"/>' +
    [260, 330, 400, 470, 540, 610, 680, 750].map((y, i) => `<rect x="160" y="${y}" width="${1300 - (i % 3) * 180}" height="34" rx="4" fill="#9aa0a6"/>`).join('') +
    '<rect x="1500" y="160" width="300" height="420" rx="10" fill="#c9d3dd"/>',
);

/** Paisagem desenhada (o B-roll do acervo no exemplo). */
const SAMPLE_PHOTO = svg(
  1920,
  1080,
  '<defs><linearGradient id="c" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff9a5a"/><stop offset="1" stop-color="#3b2a6b"/></linearGradient></defs>' +
    '<rect width="1920" height="1080" fill="url(#c)"/><circle cx="1350" cy="430" r="140" fill="#ffe08a"/>' +
    '<path d="M0 820 L420 520 L760 760 L1100 480 L1500 800 L1920 600 L1920 1080 L0 1080 Z" fill="#1d1534"/>',
);

/** Alvo do teste de quadro: quadrado branco de 200 px no meio de um quadro preto (o punch aumenta o quadrado). */
export const SAMPLE_TARGET = svg(1920, 1080, '<rect width="1920" height="1080" fill="#000"/><rect x="860" y="440" width="200" height="200" fill="#fff"/>');

/**
 * Clipe de exemplo (relativo ao `assetBaseUrl`): o `renderer/scripts/fx-stills.mjs` gera com o ffmpeg (testsrc) numa
 * pasta temporária e serve; no Remotion Studio sem esse servidor o plano de clipe fica sem vídeo.
 */
export const SAMPLE_CLIP = 'samples/fx-clip.mp4';

/** Cena de medida (`fx_target`): punch no quadro 20 (relativo à cena), com escala 1,2. */
export const SAMPLE_PUNCH = { scene: 'fx_target', at: 20, scale: 1.2 };

const fxBlank = { brightness: 0, contrast: 0, saturation: 0, warmth: 0, blur: 0, grade: null };

/** Cena com todos os planos (próprio, acervo com enquadramento, clipe, jump cut) e todos os tipos de evento. */
const fxShowcase: TimelineScene = {
  id: 'fx_show',
  kind: 'image',
  from: 0,
  durationInFrames: 180,
  src: SAMPLE_PAGE,
  motion: 'static',
  transition: 'cut',
  seed: 21,
  look: fxBlank,
  text: '',
  number: null,
  prov: 'capture',
  clean: true,
  captions: [
    {
      from: 0,
      to: 48,
      words: [
        { text: 'A', from: 0, to: 6 },
        { text: 'notícia', from: 6, to: 22, emph: 'color' },
        { text: 'confirmou', from: 22, to: 46, emph: 'pop' },
      ],
    },
    {
      from: 50,
      to: 118,
      words: [
        { text: 'e', from: 50, to: 56 },
        { text: 'ninguém', from: 56, to: 80 },
        { text: 'acreditou', from: 80, to: 116, emph: 'emoji', emoji: '😱' },
      ],
    },
    {
      from: 120,
      to: 178,
      words: [
        { text: 'até', from: 120, to: 132 },
        { text: 'agora', from: 132, to: 176 },
      ],
    },
  ],
  words: [],
  shots: [
    { from: 0, dur: 50, prov: 'capture', framing: { scale: 1, x: 0.5, y: 0.5 }, motion: 'none' },
    { from: 50, dur: 40, prov: 'free', src: SAMPLE_PHOTO, framing: { scale: 1.3, x: 0.65, y: 0.4 }, motion: 'push', easing: 'outCubic' },
    {
      from: 90,
      dur: 30,
      prov: 'official',
      video: SAMPLE_CLIP,
      clipIn: 0.5,
      speed: 1.5,
      hold: true,
      seconds: 1.5,
      framing: { scale: 1, x: 0.5, y: 0.5 },
      motion: 'none',
    },
    { from: 120, dur: 60, prov: 'capture', framing: { scale: 1.25, x: 0.35, y: 0.45 }, motion: 'drift', easing: 'snap' },
  ],
  fx: [
    // Captura: marca-texto na 3ª linha, círculo e seta; depois a câmera vai até o trecho (tudo solta no corte do 50).
    { type: 'highlight', from: 8, dur: 0, params: { rect: [160 / 1920, 400 / 1080, 1300 / 1920, 34 / 1080] } },
    { type: 'circle', from: 14, dur: 0, params: { rect: [1500 / 1920, 160 / 1080, 300 / 1920, 420 / 1080] } },
    { type: 'arrow', from: 18, dur: 0, params: { x: 0.8, y: 0.35, side: 'left' } },
    { type: 'zoom_rect', from: 32, dur: 0, params: { rect: [150 / 1920, 380 / 1080, 700 / 1920, 80 / 1080] } },
    { type: 'counter', from: 4, dur: 0, params: { n: 1, total: 3 } },
    { type: 'sfx', from: 8, dur: 0, params: { lib_id: 'shutter' } },
    // B-roll: punch na palavra, tremor, PiP e queda da trilha antes do whip.
    { type: 'punch', from: 60, dur: 0, params: { scale: 1.25 } },
    { type: 'shake', from: 70, dur: 12, params: { amount: 1 } },
    { type: 'pip', from: 54, dur: 30, params: { src: SAMPLE_PAGE, side: 'left' } },
    { type: 'music_drop', from: 84, dur: 12, params: {} },
    // Clipe: whip no corte, câmera lenta e meme de canto (emoji, sem arquivo de terceiros).
    { type: 'whip', from: 90, dur: 8, params: { side: 'right' } },
    { type: 'speed', from: 96, dur: 15, params: { rate: 0.5 } },
    { type: 'meme', from: 100, dur: 18, params: { emoji: '🤯', variant: 'corner', side: 'right' } },
    // Jump cut: clarão, congela e texto que pula.
    { type: 'flash', from: 120, dur: 6, params: {} },
    { type: 'freeze', from: 130, dur: 24, params: {} },
    { type: 'text_pop', from: 132, dur: 30, params: { text: 'ATÉ AGORA', emoji: '👀' } },
  ],
};

/** Meme "congela + zoom" sem arquivo (emoji) sobre a imagem com Ken Burns. */
const fxFreezeZoom: TimelineScene = {
  id: 'fx_meme',
  kind: 'image',
  from: 180,
  durationInFrames: 75,
  src: SAMPLE_PHOTO,
  motion: 'ken_burns_in',
  transition: 'flash',
  seed: 22,
  look: fxBlank,
  text: '',
  number: null,
  prov: 'ai',
  captions: [
    {
      from: 0,
      to: 60,
      words: [
        { text: 'Só', from: 0, to: 12 },
        { text: 'que', from: 12, to: 20 },
        { text: 'não', from: 20, to: 44, emph: 'pop' },
      ],
    },
  ],
  words: [],
  fx: [{ type: 'meme', from: 22, dur: 30, params: { emoji: '🙃', variant: 'freeze_zoom' } }],
};

/** Medida: alvo parado (sem câmera), cena limpa, punch no quadro 20. */
const fxTarget: TimelineScene = {
  id: SAMPLE_PUNCH.scene,
  kind: 'image',
  from: 255,
  durationInFrames: 45,
  src: SAMPLE_TARGET,
  motion: 'none',
  transition: 'cut',
  seed: 23,
  look: fxBlank,
  text: '',
  number: null,
  prov: 'graphic',
  clean: true,
  captions: [],
  words: [],
  fx: [{ type: 'punch', from: SAMPLE_PUNCH.at, dur: 0, params: { scale: SAMPLE_PUNCH.scale } }],
};

/** Edição (16:9): subplanos, câmera por eventos, eventos de tela, legenda com ênfase e um clipe. */
export const sampleFxProps: TimelineProps = {
  ...sampleProps,
  width: 1920,
  height: 1080,
  durationInFrames: 300,
  style: {
    ...sampleProps.style,
    family: 'motion_graphics',
    families: ['motion_graphics'],
    effects: [
      { type: 'grade', amount: 0.5, preset: 'vivid' },
      { type: 'grain', amount: 0.3 },
      { type: 'vignette', amount: 0.4 },
    ],
    captions: { ...sampleProps.style.captions, size: 72, position: 'bottom', max_words_per_line: 4 },
    textCards: { font: 'Anton', color: '#FFFFFF', accent: '#FFD400', background: '#111111', animation: 'pop' },
    progressBar: false,
  },
  scenes: [fxShowcase, fxFreezeZoom, fxTarget],
};
