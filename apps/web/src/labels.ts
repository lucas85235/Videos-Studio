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
