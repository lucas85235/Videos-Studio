// Rótulos em português para os ids técnicos que aparecem na interface.
// Use `label(MAPA, id)`: devolve o rótulo, ou o próprio id quando ele não está no mapa.

import { LANGUAGES } from './api';

export type LabelMap = Record<string, string>;

/** Rótulo de um id técnico (ou o próprio id, se não houver rótulo). */
export const label = (map: LabelMap, id: string | null | undefined): string => (id == null || id === '' ? '' : (map[id] ?? id));

/** Nome do idioma a partir do código (pt-BR → Português (Brasil)). */
export const langLabel = (code: string | null | undefined): string => {
  if (!code) return '';
  return LANGUAGES.find((l) => l.code === code)?.label ?? LANGUAGES.find((l) => l.code.toLowerCase() === code.toLowerCase())?.label ?? code;
};

export const CAMERA_LABELS: LabelMap = {
  ken_burns_in: 'Aproximar devagar',
  ken_burns_out: 'Afastar devagar',
  pan_left: 'Panorâmica para a esquerda',
  pan_right: 'Panorâmica para a direita',
  pan_up: 'Panorâmica para cima',
  pan_down: 'Panorâmica para baixo',
  punch_zoom: 'Zoom de impacto',
  drift: 'Deriva suave',
  static: 'Parada',
  zoom_to: 'Zoom no detalhe',
  parallax: 'Parallax 2.5D (profundidade)',
};

export const TRANSITION_LABELS: LabelMap = {
  cut: 'Corte seco',
  flash: 'Clarão',
  fade_black: 'Fade para preto',
  glitch: 'Glitch',
  light_leak: 'Vazamento de luz',
  zoom_blur: 'Zoom com desfoque',
  film_burn: 'Película queimada',
  static_burst: 'Chiado de TV',
};

export const SCENE_KIND_LABELS: LabelMap = {
  image: 'Imagem',
  text: 'Tipografia cinética',
  title: 'Título',
  count: 'Contagem',
  document: 'Documento',
  quote: 'Citação',
  video: 'Vídeo',
  stat: 'Número',
  compare: 'Comparação',
};

/* Curadoria visual: os mesmos ids de backend/studio/core/styles/visual.py. */

export const GENRE_LABELS: LabelMap = {
  noticia: 'Notícia',
  games: 'Games',
  tecnologia: 'Tecnologia',
  curiosidades: 'Curiosidades',
  explicador: 'Explicativo',
  lista: 'Lista e ranking',
  historia: 'História',
  biografia: 'Biografia',
  financas: 'Finanças',
  misterio: 'Mistério',
  terror: 'Terror',
  true_crime: 'True crime',
  narrativa: 'Narrativa',
  motivacional: 'Motivacional',
};

export const VISUAL_LANGUAGE_LABELS: LabelMap = {
  key_art: 'Pôster ilustrado',
  editorial: 'Ilustração editorial',
  render_3d: 'Render 3D',
  flat_vector: 'Vetor plano',
  isometric: 'Isométrico',
  ui_mockup: 'Maquete de interface',
  painting: 'Pintura',
  archival: 'Arquivo antigo',
  collage: 'Colagem',
  photo: 'Foto realista',
};

export const SHOT_LABELS: LabelMap = {
  wide: 'Plano aberto',
  medium: 'Plano médio',
  close: 'Close',
  detail: 'Detalhe',
  aerial: 'Aéreo',
  low_angle: 'De baixo para cima',
  over_shoulder: 'Por cima do ombro',
  pov: 'Ponto de vista',
  top_down: 'De cima',
};

export const COMPOSITION_LABELS: LabelMap = {
  center: 'Centro',
  left_third: 'Terço esquerdo',
  right_third: 'Terço direito',
  upper_half: 'Metade de cima',
  lower_half: 'Metade de baixo',
};

export const FOCUS_LABELS: LabelMap = {
  center: 'Centro',
  top: 'Em cima',
  bottom: 'Embaixo',
  left: 'Esquerda',
  right: 'Direita',
  top_left: 'Em cima à esquerda',
  top_right: 'Em cima à direita',
  bottom_left: 'Embaixo à esquerda',
  bottom_right: 'Embaixo à direita',
};

/* Mídia real: os mesmos ids de RealKind, MediaSource e MediaFit em core/project/document.py. */

export const REAL_KIND_LABELS: LabelMap = {
  photo: 'Foto real',
  news: 'Notícia',
  official: 'Material oficial',
};

export const MEDIA_SOURCE_LABELS: LabelMap = {
  commons: 'Wikimedia Commons',
  openverse: 'Openverse',
  pexels: 'Pexels',
  link: 'Link',
  upload: 'Arquivo enviado',
};

/** `auto` é a escolha pela proporção da foto (MediaRef.fit null). */
export const MEDIA_FIT_LABELS: LabelMap = {
  auto: 'Automático',
  cover: 'Preencher a tela',
  contain: 'Imagem inteira (fundo desfocado)',
};

export const APPROACH_LABELS: LabelMap = {
  literal: 'Literal',
  misto: 'Misto',
  alegorico: 'Alegórico',
};

export const STYLE_FIT_LABELS: LabelMap = {
  ok: 'Combina',
  fraco: 'Combina pouco',
  errado: 'Não combina',
};

export const REVIEW_SEVERITY_LABELS: LabelMap = {
  grave: 'Precisa de atenção',
  aviso: 'Aviso',
  corrigido: 'Corrigido automaticamente',
};

export const EFFECT_LABELS: LabelMap = {
  grain: 'Granulação',
  vignette: 'Vinheta',
  grade: 'Gradação de cor',
  scanlines: 'Linhas de varredura',
  chromatic: 'Aberração cromática',
  vhs: 'VHS',
  light_leak: 'Vazamento de luz',
  film_scratches: 'Arranhões de película',
  halftone: 'Retícula',
  duotone: 'Duas cores',
  sepia: 'Sépia',
  blur_edges: 'Bordas desfocadas',
  flicker: 'Tremulação',
  dust: 'Poeira',
};

export const GRADE_LABELS: LabelMap = {
  neutral: 'Neutra',
  vivid: 'Cores vivas',
  'warm-soft': 'Quente suave',
  'clean-cool': 'Fria e limpa',
  'cold-teal-dark': 'Fria azul-petróleo',
  'warm-sepia': 'Sépia quente',
  noir: 'Noir (preto e branco)',
  'bleach-bypass': 'Lavada (bleach bypass)',
  'vhs-faded': 'VHS desbotado',
  'blood-red': 'Vermelho sangue',
  'night-green': 'Visão noturna',
};

export const STRUCTURE_LABELS: LabelMap = {
  none: 'Livre',
  top10: 'Top 10',
  iceberg: 'Iceberg',
  tierlist: 'Ranking por níveis',
  quiz: 'Quiz',
  timeline: 'Linha do tempo',
  history_in_n: 'História em N minutos',
  pov: 'Ponto de vista (POV)',
  series: 'Série em episódios',
  would_you_rather: 'O que você prefere?',
};

export const ROLE_LABELS: LabelMap = {
  hook: 'Gancho',
  body: 'Corpo',
  chapter: 'Capítulo',
  item: 'Item',
  cta: 'Chamada final',
};

export const CREDENTIAL_FIELD_LABELS: LabelMap = {
  api_key: 'Chave de API',
  client_id: 'ID do cliente (OAuth)',
  client_secret: 'Segredo do cliente (OAuth)',
  contact: 'Seu contato (site ou e-mail)',
  voice_id: 'ID da voz',
  token: 'Token de acesso',
  access_token: 'Token de acesso',
  refresh_token: 'Token de renovação',
  account_id: 'ID da conta',
  user: 'Usuário',
  username: 'Usuário',
  profile: 'Perfil',
  base_url: 'Endereço do servidor',
  model: 'Modelo',
};

export const PROVIDER_KIND_LABELS: LabelMap = {
  llm: 'Roteiro (IA de texto)',
  tts: 'Voz',
  image: 'Imagens',
  align: 'Sincronia das legendas',
  publish: 'Publicação',
};

/**
 * Nome curto de cada provedor, para espaços apertados (barra lateral, resumos, painel de voz). O nome completo,
 * que vem do backend, continua nos cartões de Configurações e na dica (title) dos nomes curtos.
 * "De teste" é o termo único para o que serve só para conferir (nunca "prévia", que é a etapa 3 do vídeo).
 */
export const PROVIDER_SHORT: LabelMap = {
  claude_code: 'Claude',
  mock_llm: 'Roteiro offline',
  gemini_tts: 'Gemini',
  google_chirp3hd: 'Chirp 3 HD',
  elevenlabs: 'ElevenLabs',
  qwen3_tts_local: 'Qwen3 local',
  windows_sapi: 'Voz do Windows',
  mock_tts: 'Voz de teste',
  codex_cli: 'Codex',
  pexels: 'Pexels',
  placeholder: 'Imagens de teste',
};

/** Nome curto do provedor: do mapa; senão, o nome completo sem o trecho entre parênteses; senão, o id. */
export const providerShort = (id: string | null | undefined, fullName?: string | null): string => {
  if (id && PROVIDER_SHORT[id]) return PROVIDER_SHORT[id];
  const stripped = fullName?.replace(/\s*\([^)]*\)/g, '').trim();
  return stripped || fullName || id || '';
};

export const FAMILY_LABELS: LabelMap = {
  still_cinema: 'Cinema de imagens paradas',
  motion_graphics: 'Motion graphics editorial',
  data_geo: 'Dados e geografia',
  illustrated_2d: 'Ilustrado 2D',
  handmade: 'Textura artesanal',
  three_d: '3D e simulação',
  analog: 'Analógico e atmosfera',
  screen_ui: 'Interface de tela',
  ai_video: 'Vídeo IA',
};

export const FORMAT_TYPE_LABELS: LabelMap = {
  short: 'Vídeo curto (vertical)',
  long: 'Vídeo longo (horizontal)',
};

export const CAPTION_STYLE_LABELS: LabelMap = {
  karaoke: 'Karaokê',
  word: 'Palavra a palavra',
  line: 'Linha',
  none: 'Sem legenda',
};

export const CAPTION_POSITION_LABELS: LabelMap = {
  top: 'Topo',
  center: 'Centro',
  bottom: 'Base',
};

export const TEXT_ANIMATION_LABELS: LabelMap = {
  fade_up: 'Surgir de baixo',
  typewriter: 'Máquina de escrever',
  pop: 'Pulo',
  slide: 'Deslizar',
  flicker: 'Tremular',
};

export const MUSIC_SOURCE_LABELS: LabelMap = {
  procedural: 'Gerada pelo app',
  library: 'Suas faixas',
  none: 'Sem música',
};

export const THUMBNAIL_TEMPLATE_LABELS: LabelMap = {
  'texto-grande-esquerda': 'Texto grande à esquerda',
  'texto-centro': 'Texto no centro',
  'texto-baixo': 'Texto embaixo',
  'sem-texto': 'Sem texto',
};

export const PUBLISH_DRIVER_LABELS: LabelMap = {
  youtube_native: 'YouTube (envio direto)',
  buffer: 'Buffer',
  manual_helper: 'Assistente manual',
  uploadpost: 'Upload-Post',
};

/** Título do aviso quando um job termina bem. */
export const JOB_DONE_LABELS: LabelMap = {
  script: 'Roteiro pronto',
  storyboard: 'Storyboard pronto',
  translate: 'Tradução pronta',
  metadata: 'Títulos e descrição prontos',
  preview: 'Prévia pronta',
  render: 'Vídeo renderizado',
  autopilot: 'Piloto automático concluído',
  publish_youtube: 'Vídeo enviado ao YouTube',
};
