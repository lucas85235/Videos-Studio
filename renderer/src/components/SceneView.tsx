import React from 'react';
import { AbsoluteFill, interpolate, Sequence, useCurrentFrame, useVideoConfig } from 'remotion';
import { sceneImageBox } from '../frame';
import { imageFilter } from '../look';
import type { RenderStyle, SceneKind, SceneTreatment, ShotEasing, ShotFraming, TimelineScene } from '../types';
import { CameraRig, TimeWarp } from './CameraRig';
import { type CaptionReserve, CaptionScrim, Captions, captionCenter, captionHalfHeight, captionTop, SCRIM_ALPHA_TEXT } from './Captions';
import { ChartCard, chartRows } from './ChartCard';
import {
  type CardBand,
  type CardViewProps,
  CompareCard,
  Headline,
  headlineBox,
  headlineRect,
  SourceChip,
  Stamp,
  StatCard,
  stampGeom,
  stampPreferred,
} from './DataCards';
import { EffectsLayer } from './EffectsLayer';
import { counterRect, FxAnchored, FxLayer } from './fx/FxLayer';
import {
  ANCHORED_FX,
  bandOverlap,
  cameraFx,
  cardBackdropOff,
  cardDim,
  cutsOf,
  entryFrames,
  fxRects,
  type ImageBox,
  isEasing,
  placeBand,
  placeStamp,
  pRects,
  type Rect,
  rectThroughFraming,
  screenFrame,
  screenInnerMax,
  type ShotSlot,
  shotSlots,
  spaceAt,
  STAMP_GAP,
  STAMP_SAFE,
  stampEntryScale,
  type StampSpot,
  textInScreen,
  type TextLayer,
  textOnScreen,
} from './fx/math';
import { MapCard, mapItems } from './MapCard';
import { PresenterLayer, presenterRect } from './PresenterLayer';
import { assetUrl, NewsCard } from './RealMedia';
import { ClipLayer } from './ClipLayer';
import { textCardRects } from './TextBoxes';
import { CountCard, DocumentCard, KineticText, QuoteCard, TitleCard } from './TextCards';
import { TransitionIn } from './TransitionIn';
import { ShotStack, shotScene } from './ShotStack';
import { focusXY, isTreatment, sceneScreenVariant, TreatmentLayer, treatmentKeep } from './Treatments';
import { keepOnScreen, VisualLayer } from './VisualLayer';

type Props = {
  scene: TimelineScene;
  style: RenderStyle;
  assetBaseUrl: string | null | undefined;
  staticBase?: string | null;
  lang?: string;
};

// Mora em RealMedia.tsx (o cartão de notícia também usa) e continua exportado daqui.
export { assetUrl };

/** Cartão desenhado sobre o fundo, por tipo de cena (image e video não têm). */
const CARD_VIEWS: Partial<Record<SceneKind, React.FC<CardViewProps>>> = {
  title: TitleCard,
  count: CountCard,
  quote: QuoteCard,
  text: KineticText,
  stat: StatCard,
  compare: CompareCard,
  chart: ChartCard,
  map: MapCard,
};

/** Quanto o fundo escurece em cada tipo. */
const DIM: Partial<Record<SceneKind, number>> = { title: 0.55, count: 0.55, quote: 0.55, text: 0.62, stat: 0.6, compare: 0.6, chart: 0.62 };
/** Desfoque do fundo (px a 1080): os cartões ficam sobre a imagem (em geral reaproveitada) desfocada. */
const BLUR: Partial<Record<SceneKind, number>> = { stat: 8, compare: 8, chart: 8, text: 6, title: 4 };
/** Sem imagem, o cartão tipográfico fica sobre o fundo com textura do estilo, clareado (`VisualLayer` com `solid`; com
 * imagem, ela só escurecida e desfocada). */
const SOLID_WITHOUT_IMAGE = new Set<SceneKind>(['text']);
/** Cartão grande no meio da tela (ou o mapa, que ocupa a tela): a legenda desce para baixo dele. */
const CARD_KINDS = new Set<SceneKind>(['title', 'count', 'stat', 'compare', 'chart', 'map']);
/** O próprio cartão mostra as palavras, então não há legenda (no cartão tipográfico com frase própria, `scene.text`,
 * a fala continua na legenda). */
const NO_CAPTIONS = new Set<SceneKind>(['text', 'quote']);
/** O painel do cartão já mostra a fonte e o carimbo; o cartão entra quando o número é dito (`reveal`). */
const PANEL_KINDS = new Set<SceneKind>(['stat', 'compare', 'chart']);
/** O cartão começa estes quadros antes do número dito: o painel já está entrando quando o impacto bate. */
const REVEAL_LEAD = 3;
/** Tempo (s) em que o fundo do cartão de dado escurece e desfoca quando o cartão entra depois do corte. */
const BACKDROP_RAMP_S = 0.4;
const CLAMP = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
/** Cartão de notícia: fundo (a própria imagem da notícia) como o dos cartões de dado. */
const NEWS_DIM = 0.6;
const NEWS_BLUR = 8;
/** Cartões que mostram o texto da própria cena em destaque: a fonte solta apoia esse texto. */
const TEXT_KINDS = new Set<SceneKind>(['title', 'count', 'quote', 'text', 'document']);
/** Quadro em que o `SourceChip` começa a aparecer (ele entra nos quadros 8–16) e quando o carimbo entra por padrão. */
const SOURCE_FADE_IN = 8;
const STAMP_DELAY = 6;

// Medidas do `SourceChip` (mesma conta de DataCards), para o cartão de notícia desviar dele.
const sourceChipFont = (width: number, height: number) => Math.max(14, Math.min(width, height) * 0.028);
/** Meia altura em fração da altura da tela (linha 1,2 e respiro de 0,3 em cima e embaixo). */
const sourceChipHalf = (width: number, height: number) => (sourceChipFont(width, height) * 0.9) / height;

const filled = (t: string | undefined) => Boolean(t?.trim());

/**
 * Tipo que a cena desenha de fato: número sem valor nem rótulo, comparação com menos de 2 itens, gráfico sem números e
 * mapa sem região (tipo trocado na aba
 * Cenas, sem cartão) viram imagem, ou a fala em destaque quando não há imagem, em vez de um painel vazio.
 */
const effectiveKind = (scene: TimelineScene, hasImage: boolean): SceneKind => {
  const card = scene.card;
  // Gráfico com menos de 2 números vira comparação (que tem a sua própria reserva); mapa sem região conhecida, imagem.
  if (scene.kind === 'chart' && chartRows(scene, 'en').length < 2) return effectiveKind({ ...scene, kind: 'compare' }, hasImage);
  if (scene.kind === 'map' && !mapItems(scene, 'en').length) return hasImage ? 'image' : 'text';
  const empty =
    (scene.kind === 'stat' && !filled(card?.value) && !filled(card?.label) && scene.number == null && !filled(scene.text)) ||
    (scene.kind === 'compare' && (card?.items ?? []).filter((it) => filled(it.label) || filled(it.value)).length < 2);
  if (!empty) return scene.kind;
  return hasImage ? 'image' : 'text';
};

/**
 * O que a fonte solta ("Fonte: X") apoia na tela. 'shown': algo que já está lá no corte (faixa do topo, cartão de
 * notícia, painel do dado ou o texto grande da própria cena); 'stamp': só o carimbo, e a fonte entra junto com ele;
 * null: nada (só a fonte, sobre a imagem), e a fonte sai — sozinha ela parece crédito da imagem.
 */
const sourceSupport = (scene: TimelineScene, kind: SceneKind, hasNews: boolean): 'shown' | 'stamp' | null => {
  const card = scene.card;
  if (filled(card?.headline) || hasNews || PANEL_KINDS.has(kind) || kind === 'map') return 'shown';
  if (TEXT_KINDS.has(kind) && (filled(scene.text) || (kind === 'text' && scene.words.length > 0) || (kind === 'count' && scene.number != null))) {
    return 'shown';
  }
  return filled(card?.stamp) ? 'stamp' : null;
};

const tokens = (text: string) =>
  text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);

/** Mesmo nome sem caixa, acento nem pontuação ("Rockstar Newswire" = "rockstar newswire"). */
const sameName = (a: string | undefined, b: string) => {
  const left = tokens(a ?? '').join(' ');
  return Boolean(left) && left === tokens(b).join(' ');
};

/** O título só repete a fala da cena ("EM 24 HORAS" sobre a legenda "EM 24 HORAS"): a legenda sai. */
const titleEchoesSpeech = (scene: TimelineScene) => {
  const title = tokens(scene.text);
  const spoken = scene.captions.flatMap((l) => l.words.flatMap((w) => tokens(w.text)));
  if (!title.length || !spoken.length) return false;
  const inTitle = new Set(title);
  const said = new Set(spoken);
  return title.every((t) => said.has(t)) && spoken.filter((t) => inTitle.has(t)).length >= spoken.length * 0.75;
};

type ImageTextContext = {
  kind: SceneKind;
  news: boolean;
  clip: boolean;
  treatment: SceneTreatment | undefined;
  slots: ShotSlot[];
  sceneBox: ImageBox | null;
  zoom: number;
  width: number;
  height: number;
};

/**
 * Letras da imagem da cena na tela (fração), na cena inteira: `text_rects` da cena e dos planos com imagem própria,
 * pelo recorte, pelo movimento de câmera e pelo enquadramento de cada plano; no tratamento "tela", pela área da
 * imagem dentro da janela/celular. Documento, cartão de notícia (fundo escuro atrás do cartão) e clipe: nada.
 */
const imageTextOnScreen = (scene: TimelineScene, ctx: ImageTextContext): Rect[] => {
  const { kind, slots, sceneBox, zoom, width, height } = ctx;
  if (kind === 'document' || ctx.news || ctx.clip) return [];
  const own = pRects({ rects: scene.text_rects });
  const hasShotText = slots.some((s) => s.shot?.src && !s.shot.video && s.shot.text_rects?.length);
  if (!own.length && !hasShotText) return [];
  const aspect = width / height;
  const focus = focusXY(scene.focus);
  // Visual da própria cena num trecho: no tratamento "tela" a imagem fica dentro da moldura; senão, com câmera.
  const selfRects = (framing: ShotFraming | null, motion: string) => {
    if (ctx.treatment === 'screen') {
      const variant = sceneScreenVariant(scene, width, height);
      const { media } = screenFrame(variant, width, height);
      const smax = screenInnerMax(scene.motion, zoom, variant === 'feed');
      return textInScreen(own, sceneBox?.aspect ?? aspect, media, focus, smax, width, height)
        .map((r) => rectThroughFraming(r, framing))
        .filter((r): r is Rect => r !== null);
    }
    return textOnScreen([{ rects: own, box: sceneBox, framing, motion }], zoom, focus, aspect);
  };
  if (!slots.length) return selfRects(null, scene.motion);
  const out: Rect[] = [];
  for (const slot of slots) {
    const shot = slot.shot;
    if (shot?.video) continue;
    if (shot?.src) {
      // Imagem de outra origem: já vem no tamanho do vídeo, com o foco no centro.
      const layer: TextLayer = { rects: pRects({ rects: shot.text_rects }), box: null, framing: shot.framing ?? null, motion: shot.motion || scene.motion };
      out.push(...textOnScreen([layer], zoom, [0.5, 0.5], aspect));
    } else {
      out.push(...selfRects(shot?.framing ?? null, shot?.motion || scene.motion));
    }
  }
  return out;
};

/** Uma cena: fundo com câmera + texturas + cartão + sobreposições + legendas + transição de entrada. */
export const SceneView: React.FC<Props> = ({ scene, style, assetBaseUrl, staticBase, lang = 'pt-BR' }) => {
  const { fps, width, height } = useVideoConfig();
  // Quadro real da cena (fora do TimeWarp): o relógio das anotações presas à imagem.
  const frame = useCurrentFrame();
  // Fundo de cartão preto ou liso demais (`bg_luma`, `bg_detail`): o fundo do estilo no lugar da imagem (nunca preto
  // liso nem mancha cinza atrás do texto).
  const url = cardBackdropOff(scene.bg_luma, scene.bg_detail) ? null : assetUrl(scene.src, assetBaseUrl);
  const layers = (scene.parallax?.layers ?? []).map((rel) => assetUrl(rel, assetBaseUrl)).filter((u): u is string => Boolean(u));
  // Vídeo IA só na cena-chave (kind video) com clipe pronto; sem ele, a imagem com câmera, como as outras. Clipe REAL
  // da cena (timeline com `prov` de nível real): o build manda o proxy já cortado na entrada em `scene.video` (com
  // `video_seconds`) e ele toca em qualquer tipo, também no plano `self` do ShotStack. Os subplanos de clipe chegam
  // em `shots` com `clipIn` e `seconds`.
  const realClip = scene.prov !== undefined && scene.prov !== 'ai';
  const clipUrl = scene.kind === 'video' || realClip ? assetUrl(scene.video, assetBaseUrl) : null;
  const cards = style.textCards;
  // Transição de entrada (desfoque, flash, fade) curta: a do estilo, até ENTRY_MAX_S.
  const entry = entryFrames(style.transitionFrames, fps);
  const kind = effectiveKind(scene, Boolean(url));
  const Card = CARD_VIEWS[kind];
  const headline = scene.card?.headline?.trim();
  const stamp = scene.card?.stamp?.trim();
  const inPanel = PANEL_KINDS.has(kind);
  const headBox = headline ? headlineBox(headline, cards, lang, width, height) : null;
  // Mídia real: cartão de notícia (só na cena de imagem dona dela). O crédito da imagem vai na descrição, não na tela.
  const news = kind === 'image' && scene.news && (scene.news.outlet?.trim() || scene.news.headline?.trim()) ? scene.news : undefined;
  // A fonte solta igual ao veículo do cartão de notícia sai: o cartão já diz de onde veio. Sem nada na tela que ela
  // apoie, também sai; se só apoia o carimbo, entra junto com ele.
  const cardSource = scene.card?.source?.trim();
  const support = cardSource ? sourceSupport(scene, kind, Boolean(news)) : null;
  const source = cardSource && support && !(news && sameName(news.outlet, cardSource)) ? cardSource : undefined;
  const stampDelay = Math.round(scene.card?.stamp_from ?? STAMP_DELAY);
  const sourceFrom = support === 'stamp' ? Math.max(0, stampDelay - SOURCE_FADE_IN) : 0;
  // Variação de família (lousa, colagem, tela): só na cena de imagem com imagem e sem cartão de notícia.
  const treatment = kind === 'image' && url && !news && isTreatment(scene.treatment) ? scene.treatment : undefined;

  const position = style.captions.position;
  const captionHalf = captionHalfHeight(style.captions, width, height);
  let captionAt: number | undefined;
  if (CARD_KINDS.has(kind) || news) captionAt = Math.max(0.76, captionTop(position));
  else if (headBox && position === 'top') captionAt = Math.max(0.25, headBox.bottom / height + captionHalf + 0.02);
  // No vertical, a legenda não desce para os 20% de baixo (nome do canal e descrição).
  const captionMid = captionCenter(style.captions, width, height, captionAt);
  if (captionAt !== undefined || captionMid !== captionTop(position)) captionAt = captionMid;
  // Fonte: no vertical fica acima da área da interface do Shorts e longe da legenda.
  const sourceY = width > height ? 0.925 : captionMid < 0.7 ? 0.76 : captionMid - 0.1;
  // Carimbo embaixo (sujeito no meio ou em cima): logo abaixo da legenda do meio, ou acima da legenda de baixo.
  const stampLowY = captionMid >= 0.65 ? 0.52 : captionMid <= 0.35 ? 0.66 : captionMid + captionHalf + 0.075;
  // Carimbo do lado (0,3): desce para baixo da legenda de cima quando a faixa do topo a empurra.
  const stampSideY = captionMid < 0.45 ? Math.max(0.3, captionMid + captionHalf + 0.075) : 0.3;
  // Faixa livre para o painel dos cartões de dado: abaixo da faixa do topo e acima da legenda (meia legenda de 2
  // linhas e uma margem), para o painel nunca passar por baixo de nenhuma das duas.
  const band: CardBand = {
    top: headBox ? headBox.bottom / height + 0.02 : 0.06,
    bottom: captionAt !== undefined && style.captions.style !== 'none' ? captionAt - captionHalf - 0.02 : 0.9,
  };
  // O cartão de notícia não desce até a fonte solta (no vertical ela fica entre o painel e a legenda).
  if (news && source) band.bottom = Math.min(band.bottom, sourceY - sourceChipHalf(width, height) - 0.012);

  // Cartão de dado entra quando o número é dito (dito logo no corte, entra junto com a cena). Número dito na cena
  // entra parado ("pop"), sem contar: a contagem passaria por valores que a fala e a legenda não dizem no mesmo quadro.
  const reveal = inPanel && typeof scene.reveal === 'number' ? scene.reveal : undefined;
  const spokenAtCut = reveal !== undefined && reveal <= Math.round(fps * 0.3);
  // Sem imagem de fundo, o cartão entra no corte: esperar o número deixaria o fundo do estilo vazio na tela (o QA
  // achou 3 s de gradiente sem nada antes do "2013").
  const cardFrom = reveal !== undefined && !spokenAtCut && url ? Math.max(0, reveal - REVEAL_LEAD) : 0;
  const avatarRect = scene.presenter ? presenterRect(scene.presenter, width, height, band.top) : null;
  const card = Card ? (
    <Card scene={scene} style={cards} uppercase={style.captions.uppercase} lang={lang} band={band} pop={reveal !== undefined} avoid={avatarRect} />
  ) : null;
  const hideCaptions =
    (NO_CAPTIONS.has(kind) && !(kind === 'text' && filled(scene.text))) || (kind === 'title' && titleEchoesSpeech(scene));
  const sourceChip = source && !inPanel ? <SourceChip source={source} style={cards} lang={lang} y={sourceY} /> : null;

  // Edição (ADR 0004): planos, câmera por eventos, tempo do fundo e eventos de tela. Cena sem `shots` nem `fx`
  // desenha a mesma árvore de antes (cada peça devolve o fundo como está).
  const fx = scene.fx;
  const slots = shotSlots(scene.shots, scene.durationInFrames);
  const cuts = cutsOf(slots);
  // A camada presa à imagem recebe o quadro REAL da cena (o `frame` daqui, de fora do TimeWarp) e a caixa da imagem
  // do contêiner dela (converte o `rect` do arquivo para o recorte).
  const overlayAt = (fx ?? []).some((e) => ANCHORED_FX.has(e.type))
    ? (box: ImageBox | null) => <FxAnchored fx={fx} frame={frame} frames={scene.durationInFrames} cuts={cuts} seed={scene.seed} box={box} />
    : undefined;
  // Cartão que entra depois do corte: até ele entrar, o fundo fica nítido (a captura da fonte é lida antes de virar
  // fundo desfocado) e escurece/desfoca em `BACKDROP_RAMP_S` quando o cartão chega.
  const backdrop = cardFrom > 0 ? interpolate(frame, [cardFrom, cardFrom + Math.max(1, Math.round(fps * BACKDROP_RAMP_S))], [0, 1], CLAMP) : 1;
  // O fundo do cartão escurece só até o brilho mínimo (`cardDim`): nunca vira preto liso atrás do texto.
  const dim = news ? NEWS_DIM : cardDim(DIM[kind] ?? 0, scene.bg_luma) * backdrop;
  const blur = news ? NEWS_BLUR : (BLUR[kind] ?? 0) * backdrop;
  const image = (sub: TimelineScene, imageUrl: string | null, overlay?: React.ReactNode, easing?: ShotEasing) => (
    <VisualLayer
      scene={sub}
      effects={style.effects}
      zoomStrength={style.zoomStrength}
      transitionFrames={entry}
      url={imageUrl}
      layers={sub === scene ? layers : []}
      cards={cards}
      dim={dim}
      blur={blur}
      solid={SOLID_WITHOUT_IMAGE.has(kind)}
      easing={easing}
      overlay={overlay}
    />
  );
  const withOverlay = (node: React.ReactNode, overlay: React.ReactNode) =>
    overlay ? (
      <>
        {node}
        {overlay}
      </>
    ) : (
      node
    );
  // Fundo que não é a imagem com câmera (documento, vídeo, lousa/colagem/tela), ou null quando é.
  const otherBackground = (overlay?: React.ReactNode): React.ReactNode =>
    kind === 'document' ? (
      withOverlay(<DocumentCard url={url} scene={scene} style={cards} filter={imageFilter(style.effects, scene.look)} />, overlay)
    ) : clipUrl ? (
      <ClipLayer scene={scene} effects={style.effects} url={clipUrl} overlay={overlay} />
    ) : treatment && url ? (
      withOverlay(
        <TreatmentLayer
          treatment={treatment}
          scene={scene}
          effects={style.effects}
          zoomStrength={style.zoomStrength}
          transitionFrames={entry}
          url={url}
          cards={cards}
        />,
        overlay,
      )
    ) : null;
  const sceneBackground = (overlay?: React.ReactNode) => otherBackground(overlay) ?? image(scene, url, overlay);
  // Caixa da imagem da própria cena (só a imagem com câmera usa o `frame`; documento, clipe e tratamentos: null).
  const ownImage = kind !== 'document' && !clipUrl && !(treatment && url) && Boolean(url);
  const sceneBox = ownImage ? sceneImageBox(scene.frame, width, height) : null;
  // O `zoom_rect` vai para a câmera já na tela (arquivo → recorte → enquadramento do plano em que ele começa).
  const camFx = cameraFx(fx, (at) => spaceAt(slots, at, sceneBox), width / height);
  // A manchete da captura na tela neste quadro (já com o movimento da imagem, o zoom de dentro da moldura e o
  // enquadramento do plano): a câmera por eventos também não a corta.
  const camKeep = (): Rect | null => {
    if (!(fx ?? []).length) return null;
    const motion = { fps, width, height, zoomStrength: style.zoomStrength, transitionFrames: entry, blur, hasImage: true };
    if (!slots.length) {
      if (ownImage) return keepOnScreen(scene, frame, motion);
      return treatment && url ? treatmentKeep(scene, treatment, width, height, style.zoomStrength, frame) : null;
    }
    const i = slots.findIndex((sl) => frame >= sl.from && frame < sl.to);
    const slot = slots[i];
    if (!slot || slot.shot?.video || (!slot.shot?.src && !ownImage)) return null;
    const onScreen = keepOnScreen(shotScene(scene, slot, i), frame - slot.from, { ...motion, easing: isEasing(slot.shot?.easing) ? slot.shot?.easing : undefined });
    return onScreen ? rectThroughFraming(onScreen, slot.shot?.framing) : null;
  };
  const captionsShown = !hideCaptions && style.captions.style !== 'none';

  // Letras da própria imagem na tela (manchete da captura, logo, letreiro: `text_rects` do build), pelo mesmo caminho
  // da imagem (recorte, câmera, enquadramento do plano, janela do tratamento "tela"). O carimbo e a faixa do topo
  // desviam delas e a tarja da legenda escurece mais sobre elas.
  const imageText = imageTextOnScreen(scene, {
    kind,
    news: Boolean(news),
    clip: Boolean(clipUrl),
    treatment: treatment && url ? treatment : undefined,
    slots,
    sceneBox,
    zoom: style.zoomStrength,
    width,
    height,
  });
  // Faixa do topo na cena de imagem: desce para a altura livre mais perto quando no topo ela cobriria a manchete ou o
  // logo da imagem (nunca sobre a legenda nem o avatar). Cartões e notícia ficam como sempre (o painel mora embaixo).
  let headY: number | undefined;
  if (headline && kind === 'image' && !news && position !== 'top' && imageText.length) {
    const top = headlineRect(headline, cards, lang, width, height);
    const half = top[3] / 2;
    const hard: Rect[] = captionsShown ? [[0, captionMid - captionHalf, 1, captionHalf * 2]] : [];
    if (avatarRect) hard.push(avatarRect);
    const lo = STAMP_SAFE + half;
    const hi = (captionsShown ? captionMid - captionHalf - STAMP_GAP : 1 - STAMP_SAFE) - half;
    const y = placeBand(top[1] + half, top[2], half, hard, imageText, lo, hi);
    // O avatar (já posto abaixo da faixa) e o contador ficam onde estavam: `band.top` não muda, para o avatar
    // desenhado ser o mesmo retângulo que a faixa e o carimbo evitam.
    if (Math.abs(y - (top[1] + half)) > 1e-6) headY = y;
  }

  // Carimbo solto: no lugar da composição da imagem quando está livre; senão, desvia de todo texto conhecido na tela
  // (o do cartão de texto, a faixa do topo, o cartão de notícia, a legenda, o avatar, o contador, a fonte e o trecho
  // marcado da captura) e, quando há lugar, das letras da própria imagem.
  let stampSpot: StampSpot | undefined;
  let stampScale: number | undefined;
  if (stamp && !inPanel) {
    const obstacles: Rect[] = textCardRects(kind, scene, cards, style.captions.uppercase, width, height);
    if (headline) obstacles.push(headlineRect(headline, cards, lang, width, height, headY));
    if (sourceChip) {
      const chipFont = sourceChipFont(width, height);
      const chipW = Math.min(0.7, ((source ?? '').length + 8) * chipFont * 0.56 / width + (chipFont * 1.4) / width);
      const chipHalf = sourceChipHalf(width, height);
      obstacles.push([0.06, sourceY - chipHalf, chipW, chipHalf * 2]);
    }
    if (news) {
      const newsW = width > height ? 0.52 : 0.86;
      obstacles.push([(1 - newsW) / 2, band.top, newsW, Math.max(0, band.bottom - band.top)]);
    }
    if (captionsShown) obstacles.push([0, captionMid - captionHalf, 1, captionHalf * 2]);
    if (avatarRect) obstacles.push(avatarRect);
    for (const e of fx ?? []) {
      const counter = e.type === 'counter' ? counterRect(e.params ?? {}, width, height, band.top) : null;
      if (counter) obstacles.push(counter);
      if (!ANCHORED_FX.has(e.type)) continue;
      const space = spaceAt(slots, Math.round(e.from), sceneBox);
      for (const r of fxRects(e.params, space.box, width / height)) {
        const onScreen = rectThroughFraming(r, space.framing);
        if (onScreen) obstacles.push(onScreen);
      }
    }
    const geom = stampGeom(stamp, cards, lang, width, height);
    stampSpot = placeStamp(stampPreferred(scene.card?.stamp_at, stampLowY, stampSideY), geom, obstacles, imageText);
    stampScale = stampEntryScale(stampSpot, geom);
  }
  // A legenda deixa livre a faixa do avatar enquanto ele está na tela (a ponta dela encostava no recorte).
  const captionReserve: CaptionReserve | undefined =
    avatarRect && scene.presenter
      ? {
          side: scene.presenter.side === 'left' ? 'left' : 'right',
          width: avatarRect[2] + 0.02,
          from: Math.round(scene.presenter.from),
          to: Math.round(scene.presenter.from + scene.presenter.dur),
        }
      : undefined;
  // Fundo de imagem em brilho cheio (foto, captura, logo, tratamento): a legenda ganha uma tarja escura por baixo, para
  // as letras não se misturarem com o texto e o logo da imagem. Cartões já escurecem o fundo; estilo com caixa própria
  // (`captions.background`) já tem a dele. O cartão que entra depois do corte deixa o fundo nítido até chegar: a tarja
  // fica até lá e some junto com o escurecimento (`backdrop`), sem pular de um quadro para o outro.
  const scrimOpacity = news ? 0 : (DIM[kind] ?? 0) === 0 ? 1 : 1 - backdrop;
  const scrim = captionsShown && !style.captions.background && scrimOpacity > 0 && Boolean(url || clipUrl);
  // Legenda sobre letras da imagem (logo, corpo de matéria): a tarja escurece mais, as letras de trás somem.
  const scrimAlpha = bandOverlap(captionMid - captionHalf, captionMid + captionHalf, imageText) > 0.002 ? SCRIM_ALPHA_TEXT : undefined;

  const background = slots.length ? (
    <ShotStack
      scene={scene}
      slots={slots}
      fx={fx}
      cuts={cuts}
      effects={style.effects}
      assetBaseUrl={assetBaseUrl}
      renderImage={image}
      selfBackground={otherBackground()}
      sceneBox={sceneBox}
      overlayAt={overlayAt}
    />
  ) : (
    <TimeWarp fx={fx} frames={scene.durationInFrames} cuts={cuts}>
      {sceneBackground(overlayAt?.(sceneBox))}
    </TimeWarp>
  );

  return (
    <AbsoluteFill style={{ backgroundColor: '#000', overflow: 'hidden' }}>
      <CameraRig fx={camFx} frames={scene.durationInFrames} cuts={cuts} seed={scene.seed} id={`${scene.id}-${scene.seed}`} keep={camKeep()}>
        {background}
      </CameraRig>
      <EffectsLayer effects={style.effects} look={scene.look} seed={scene.seed} staticBase={staticBase} clean={scene.clean === true} />
      <FxLayer
        scene={scene}
        fx={fx}
        style={style}
        assetBaseUrl={assetBaseUrl}
        cuts={cuts}
        captionMid={captionMid}
        captionHalf={captionHalf}
        captionsShown={captionsShown}
        minTop={band.top}
        lang={lang}
        imageText={imageText}
      />
      {card && cardFrom > 0 ? (
        <Sequence from={cardFrom} layout="none" name="Cartão">
          {card}
        </Sequence>
      ) : (
        card
      )}
      {news ? <NewsCard news={news} style={cards} lang={lang} band={band} assetBaseUrl={assetBaseUrl} /> : null}
      {headline ? <Headline text={headline} style={cards} lang={lang} y={headY} /> : null}
      {scrim ? <CaptionScrim center={captionMid} half={captionHalf} opacity={scrimOpacity} alpha={scrimAlpha} /> : null}
      {stamp && stampSpot ? <Stamp text={stamp} style={cards} lang={lang} delay={stampDelay} spot={stampSpot} entryScale={stampScale} /> : null}
      {sourceChip && sourceFrom > 0 ? (
        <Sequence from={sourceFrom} layout="none" name="Fonte">
          {sourceChip}
        </Sequence>
      ) : (
        sourceChip
      )}
      {scene.presenter ? (
        <PresenterLayer presenter={scene.presenter} words={scene.words} assetBaseUrl={assetBaseUrl} minTop={band.top} />
      ) : null}
      {/* A transição de entrada (flash, fade, luz) passa por baixo da legenda: a legenda fica sempre opaca. */}
      <TransitionIn type={scene.transition} frames={entry} seed={scene.seed} />
      {hideCaptions ? null : <Captions lines={scene.captions} style={style.captions} topOverride={captionAt} lang={lang} reserve={captionReserve} />}
    </AbsoluteFill>
  );
};
