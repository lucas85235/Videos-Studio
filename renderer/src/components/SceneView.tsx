import React from 'react';
import { AbsoluteFill, Sequence, useVideoConfig } from 'remotion';
import { imageFilter } from '../look';
import type { RenderStyle, SceneKind, TimelineScene } from '../types';
import { Captions, captionCenter, captionHalfHeight, captionTop } from './Captions';
import { type CardBand, type CardViewProps, CompareCard, Headline, headlineBox, SourceChip, Stamp, StatCard } from './DataCards';
import { EffectsLayer } from './EffectsLayer';
import { assetUrl, CreditChip, creditChipHalf, creditChipPlace, NewsCard } from './RealMedia';
import { CountCard, DocumentCard, KineticText, QuoteCard, TitleCard } from './TextCards';
import { TransitionIn } from './TransitionIn';
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
};

/** Quanto o fundo escurece em cada tipo. */
const DIM: Partial<Record<SceneKind, number>> = { title: 0.55, count: 0.55, quote: 0.55, text: 0.62, stat: 0.6, compare: 0.6 };
/** Desfoque do fundo (px a 1080): os cartões ficam sobre a imagem (em geral reaproveitada) desfocada. */
const BLUR: Partial<Record<SceneKind, number>> = { stat: 8, compare: 8, text: 6, title: 4 };
/** Sem imagem, o texto cinético fica sobre o fundo do estilo quase opaco (com imagem, só escurecida e desfocada). */
const SOLID_WITHOUT_IMAGE = new Set<SceneKind>(['text']);
/** Cartão grande no meio da tela: a legenda desce para baixo dele. */
const CARD_KINDS = new Set<SceneKind>(['title', 'count', 'stat', 'compare']);
/** O próprio cartão mostra as palavras, então não há legenda. */
const NO_CAPTIONS = new Set<SceneKind>(['text', 'quote']);
/** O painel do cartão já mostra a fonte e o carimbo; o cartão entra quando o número é dito (`reveal`). */
const PANEL_KINDS = new Set<SceneKind>(['stat', 'compare']);
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

// Medidas do `SourceChip` (mesma conta de DataCards), para o crédito e o cartão de notícia desviarem dele.
const sourceChipFont = (width: number, height: number) => Math.max(14, Math.min(width, height) * 0.028);
/** Meia altura em fração da altura da tela (linha 1,2 e respiro de 0,3 em cima e embaixo). */
const sourceChipHalf = (width: number, height: number) => (sourceChipFont(width, height) * 0.9) / height;
/** Largura prevista em px ("Fonte: X", respiro e borda de destaque), até os 70% de largura máxima do chip. */
const sourceChipWidth = (source: string, width: number, height: number) => {
  const size = sourceChipFont(width, height);
  return Math.min(width * 0.7, (source.length + 8) * 0.58 * size + size * 1.4);
};

const filled = (t: string | undefined) => Boolean(t?.trim());

/**
 * Tipo que a cena desenha de fato: número sem valor nem rótulo e comparação com menos de 2 itens (tipo trocado na aba
 * Cenas, sem cartão) viram imagem, ou a fala em destaque quando não há imagem, em vez de um painel vazio.
 */
const effectiveKind = (scene: TimelineScene, hasImage: boolean): SceneKind => {
  const card = scene.card;
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
  if (filled(card?.headline) || hasNews || PANEL_KINDS.has(kind)) return 'shown';
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
  const url = assetUrl(scene.src, assetBaseUrl);
  const layers = (scene.parallax?.layers ?? []).map((rel) => assetUrl(rel, assetBaseUrl)).filter((u): u is string => Boolean(u));
  const cards = style.textCards;
  const kind = effectiveKind(scene, Boolean(url));
  const Card = CARD_VIEWS[kind];
  const headline = scene.card?.headline?.trim();
  const stamp = scene.card?.stamp?.trim();
  const inPanel = PANEL_KINDS.has(kind);
  const headBox = headline ? headlineBox(headline, cards, lang, width, height) : null;
  // Mídia real: cartão de notícia (só na cena de imagem dona dela) e o crédito de quem fez a foto/imagem.
  const news = kind === 'image' && scene.news && (scene.news.outlet?.trim() || scene.news.headline?.trim()) ? scene.news : undefined;
  // A fonte solta igual ao veículo do cartão de notícia sai: o cartão já diz de onde veio. Sem nada na tela que ela
  // apoie, também sai; se só apoia o carimbo, entra junto com ele.
  const cardSource = scene.card?.source?.trim();
  const support = cardSource ? sourceSupport(scene, kind, Boolean(news)) : null;
  const source = cardSource && support && !(news && sameName(news.outlet, cardSource)) ? cardSource : undefined;
  const stampDelay = Math.round(scene.card?.stamp_from ?? STAMP_DELAY);
  const sourceFrom = support === 'stamp' ? Math.max(0, stampDelay - SOURCE_FADE_IN) : 0;
  const credit = scene.credit?.trim();
  const creditAt = credit ? creditChipPlace(width, height, headBox ? headBox.bottom / height : undefined) : null;
  // No vertical o chip fica no topo: a legenda de cima e o painel começam abaixo dele.
  const creditBottom = creditAt?.corner === 'top_left' ? creditAt.y + creditChipHalf(width, height) : null;

  const position = style.captions.position;
  const captionHalf = captionHalfHeight(style.captions, width, height);
  let captionAt: number | undefined;
  if (CARD_KINDS.has(kind) || news) captionAt = Math.max(0.76, captionTop(position));
  else if (headBox && position === 'top') captionAt = Math.max(0.25, headBox.bottom / height + captionHalf + 0.02);
  if (creditBottom !== null && position === 'top' && !CARD_KINDS.has(kind) && !news) {
    // Legenda de cima no vertical: desce para baixo do chip de crédito.
    const below = creditBottom + captionHalf + 0.02;
    if ((captionAt ?? captionTop(position)) < below) captionAt = below;
  }
  // No vertical, a legenda não desce para os 20% de baixo (nome do canal e descrição).
  const captionMid = captionCenter(style.captions, width, height, captionAt);
  if (captionAt !== undefined || captionMid !== captionTop(position)) captionAt = captionMid;
  // Fonte: no vertical fica acima da área da interface do Shorts e longe da legenda.
  const sourceY = width > height ? 0.925 : captionMid < 0.7 ? 0.76 : captionMid - 0.1;
  // Carimbo embaixo (sujeito no meio ou em cima): logo abaixo da legenda do meio, ou acima da legenda de baixo.
  const stampLowY = captionMid >= 0.65 ? 0.52 : captionMid <= 0.35 ? 0.66 : captionMid + captionHalf + 0.075;
  // Carimbo do lado (0,3): desce para baixo da legenda de cima quando a faixa do topo ou o crédito a empurram.
  const stampSideY = captionMid < 0.45 ? Math.max(0.3, captionMid + captionHalf + 0.075) : 0.3;
  // Faixa livre para o painel dos cartões de dado: abaixo da faixa do topo e acima da legenda (meia legenda de 2
  // linhas e uma margem), para o painel nunca passar por baixo de nenhuma das duas.
  const band: CardBand = {
    top: headBox ? headBox.bottom / height + 0.02 : 0.06,
    bottom: captionAt !== undefined && style.captions.style !== 'none' ? captionAt - captionHalf - 0.02 : 0.9,
  };
  if (creditBottom !== null) band.top = Math.max(band.top, creditBottom + 0.01);
  // O cartão de notícia não desce até a fonte solta (no vertical ela fica entre o painel e a legenda).
  if (news && source) band.bottom = Math.min(band.bottom, sourceY - sourceChipHalf(width, height) - 0.012);
  // No horizontal, crédito (à direita) e fonte solta (à esquerda) ficam na mesma linha: o crédito não cobre a fonte.
  const creditMaxW =
    creditAt?.corner === 'bottom_right' && source && !inPanel
      ? Math.max(width * 0.3, Math.min(width * 0.62, width * 0.88 - sourceChipWidth(source, width, height)))
      : undefined;

  // Cartão de dado entra quando o número é dito; dito logo no corte, entra parado ("pop"), sem contar.
  const reveal = inPanel && typeof scene.reveal === 'number' ? scene.reveal : undefined;
  const spokenAtCut = reveal !== undefined && reveal <= Math.round(fps * 0.3);
  const cardFrom = reveal !== undefined && !spokenAtCut ? Math.max(0, reveal - REVEAL_LEAD) : 0;
  const card = Card ? <Card scene={scene} style={cards} uppercase={style.captions.uppercase} lang={lang} band={band} pop={spokenAtCut} /> : null;
  const hideCaptions = NO_CAPTIONS.has(kind) || (kind === 'title' && titleEchoesSpeech(scene));
  const sourceChip = source && !inPanel ? <SourceChip source={source} style={cards} lang={lang} y={sourceY} /> : null;

  return (
    <AbsoluteFill style={{ backgroundColor: '#000', overflow: 'hidden' }}>
      {kind === 'document' ? (
        <DocumentCard url={url} scene={scene} style={cards} filter={imageFilter(style.effects, scene.look)} />
      ) : (
        <VisualLayer
          scene={scene}
          effects={style.effects}
          zoomStrength={style.zoomStrength}
          transitionFrames={style.transitionFrames}
          url={url}
          layers={layers}
          cards={cards}
          dim={news ? NEWS_DIM : (DIM[kind] ?? 0)}
          blur={news ? NEWS_BLUR : (BLUR[kind] ?? 0)}
          solid={SOLID_WITHOUT_IMAGE.has(kind)}
        />
      )}
      <EffectsLayer effects={style.effects} look={scene.look} seed={scene.seed} staticBase={staticBase} />
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
      {credit && creditAt ? <CreditChip text={credit} corner={creditAt.corner} y={creditAt.y} maxWidth={creditMaxW} /> : null}
      {hideCaptions ? null : <Captions lines={scene.captions} style={style.captions} topOverride={captionAt} lang={lang} />}
      <TransitionIn type={scene.transition} frames={style.transitionFrames} seed={scene.seed} />
    </AbsoluteFill>
  );
};
