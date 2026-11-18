import React from 'react';
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from 'remotion';
import { sceneImageBox } from '../frame';
import { imageFilter } from '../look';
import type { RenderStyle, SceneKind, ShotEasing, TimelineScene } from '../types';
import { CameraRig, TimeWarp } from './CameraRig';
import { Captions, captionCenter, captionHalfHeight, captionTop } from './Captions';
import { ChartCard, chartRows } from './ChartCard';
import { type CardBand, type CardViewProps, CompareCard, Headline, headlineBox, SourceChip, Stamp, StatCard } from './DataCards';
import { EffectsLayer } from './EffectsLayer';
import { FxAnchored, FxLayer } from './fx/FxLayer';
import { ANCHORED_FX, cameraFx, cutsOf, type ImageBox, shotSlots, spaceAt } from './fx/math';
import { MapCard, mapItems } from './MapCard';
import { PresenterLayer } from './PresenterLayer';
import { assetUrl, NewsCard } from './RealMedia';
import { ClipLayer } from './ClipLayer';
import { CountCard, DocumentCard, KineticText, QuoteCard, TitleCard } from './TextCards';
import { TransitionIn } from './TransitionIn';
import { ShotStack } from './ShotStack';
import { isTreatment, TreatmentLayer } from './Treatments';
import { VisualLayer } from './VisualLayer';

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
/** Sem imagem, o texto cinético fica sobre o fundo do estilo quase opaco (com imagem, só escurecida e desfocada). */
const SOLID_WITHOUT_IMAGE = new Set<SceneKind>(['text']);
/** Cartão grande no meio da tela (ou o mapa, que ocupa a tela): a legenda desce para baixo dele. */
const CARD_KINDS = new Set<SceneKind>(['title', 'count', 'stat', 'compare', 'chart', 'map']);
/** O próprio cartão mostra as palavras, então não há legenda. */
const NO_CAPTIONS = new Set<SceneKind>(['text', 'quote']);
/** O painel do cartão já mostra a fonte e o carimbo; o cartão entra quando o número é dito (`reveal`). */
const PANEL_KINDS = new Set<SceneKind>(['stat', 'compare', 'chart']);
/** O cartão começa estes quadros antes do número dito: o painel já está entrando quando o impacto bate. */
const REVEAL_LEAD = 3;
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

/** Uma cena: fundo com câmera + texturas + cartão + sobreposições + legendas + transição de entrada. */
export const SceneView: React.FC<Props> = ({ scene, style, assetBaseUrl, staticBase, lang = 'pt-BR' }) => {
  const { fps, width, height } = useVideoConfig();
  // Quadro real da cena (fora do TimeWarp): o relógio das anotações presas à imagem.
  const frame = useCurrentFrame();
  const url = assetUrl(scene.src, assetBaseUrl);
  const layers = (scene.parallax?.layers ?? []).map((rel) => assetUrl(rel, assetBaseUrl)).filter((u): u is string => Boolean(u));
  // Vídeo IA só na cena-chave (kind video) com clipe pronto; sem ele, a imagem com câmera, como as outras. Clipe REAL
  // da cena (timeline com `prov` de nível real): o build manda o proxy já cortado na entrada em `scene.video` (com
  // `video_seconds`) e ele toca em qualquer tipo, também no plano `self` do ShotStack. Os subplanos de clipe chegam
  // em `shots` com `clipIn` e `seconds`.
  const realClip = scene.prov !== undefined && scene.prov !== 'ai';
  const clipUrl = scene.kind === 'video' || realClip ? assetUrl(scene.video, assetBaseUrl) : null;
  const cards = style.textCards;
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

  // Cartão de dado entra quando o número é dito; dito logo no corte, entra parado ("pop"), sem contar.
  const reveal = inPanel && typeof scene.reveal === 'number' ? scene.reveal : undefined;
  const spokenAtCut = reveal !== undefined && reveal <= Math.round(fps * 0.3);
  const cardFrom = reveal !== undefined && !spokenAtCut ? Math.max(0, reveal - REVEAL_LEAD) : 0;
  const card = Card ? <Card scene={scene} style={cards} uppercase={style.captions.uppercase} lang={lang} band={band} pop={spokenAtCut} /> : null;
  const hideCaptions = NO_CAPTIONS.has(kind) || (kind === 'title' && titleEchoesSpeech(scene));
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
  const dim = news ? NEWS_DIM : (DIM[kind] ?? 0);
  const blur = news ? NEWS_BLUR : (BLUR[kind] ?? 0);
  const image = (sub: TimelineScene, imageUrl: string | null, overlay?: React.ReactNode, easing?: ShotEasing) => (
    <VisualLayer
      scene={sub}
      effects={style.effects}
      zoomStrength={style.zoomStrength}
      transitionFrames={style.transitionFrames}
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
          transitionFrames={style.transitionFrames}
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
      <CameraRig fx={camFx} frames={scene.durationInFrames} cuts={cuts} seed={scene.seed} id={`${scene.id}-${scene.seed}`}>
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
        captionsShown={!hideCaptions && style.captions.style !== 'none'}
        minTop={band.top}
        lang={lang}
      />
      {card && cardFrom > 0 ? (
        <Sequence from={cardFrom} layout="none" name="Cartão">
          {card}
        </Sequence>
      ) : (
        card
      )}
      {news ? <NewsCard news={news} style={cards} lang={lang} band={band} assetBaseUrl={assetBaseUrl} /> : null}
      {headline ? <Headline text={headline} style={cards} lang={lang} /> : null}
      {stamp && !inPanel ? (
        <Stamp text={stamp} style={cards} lang={lang} delay={stampDelay} at={scene.card?.stamp_at} lowY={stampLowY} sideY={stampSideY} />
      ) : null}
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
      {hideCaptions ? null : <Captions lines={scene.captions} style={style.captions} topOverride={captionAt} lang={lang} />}
      <TransitionIn type={scene.transition} frames={style.transitionFrames} seed={scene.seed} />
    </AbsoluteFill>
  );
};
