import React from 'react';
import { AbsoluteFill, useVideoConfig } from 'remotion';
import { imageFilter } from '../look';
import type { RenderStyle, SceneKind, TimelineScene } from '../types';
import { Captions, captionTop } from './Captions';
import { type CardBand, type CardViewProps, CompareCard, Headline, headlineBox, SourceChip, Stamp, StatCard } from './DataCards';
import { EffectsLayer } from './EffectsLayer';
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

export const assetUrl = (src: string | null, base: string | null | undefined) => {
  if (!src) return null;
  if (/^(https?:|data:|blob:)/.test(src)) return src;
  const clean = src.replace(/\\/g, '/').replace(/^\/+/, '');
  return `${(base ?? '/files/').replace(/\/?$/, '/')}${clean}`;
};

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
const DIM: Partial<Record<SceneKind, number>> = { title: 0.55, count: 0.55, quote: 0.55, text: 0.7, stat: 0.6, compare: 0.6 };
/** Desfoque do fundo (px a 1080): os cartões de dado ficam sobre a imagem reaproveitada desfocada. */
const BLUR: Partial<Record<SceneKind, number>> = { stat: 8, compare: 8 };
/** Cartão grande no meio da tela: a legenda desce para baixo dele. */
const CARD_KINDS = new Set<SceneKind>(['title', 'count', 'stat', 'compare']);
/** O próprio cartão mostra as palavras, então não há legenda. */
const NO_CAPTIONS = new Set<SceneKind>(['text', 'quote']);
/** O painel do cartão já mostra a fonte e o carimbo. */
const PANEL_KINDS = new Set<SceneKind>(['stat', 'compare']);

/**
 * Tipo que a cena desenha de fato: número sem valor nem rótulo e comparação com menos de 2 itens (tipo trocado na aba
 * Cenas, sem cartão) viram imagem, ou a fala em destaque quando não há imagem, em vez de um painel vazio.
 */
const effectiveKind = (scene: TimelineScene, hasImage: boolean): SceneKind => {
  const card = scene.card;
  const filled = (t: string | undefined) => Boolean(t?.trim());
  const empty =
    (scene.kind === 'stat' && !filled(card?.value) && !filled(card?.label) && scene.number == null && !filled(scene.text)) ||
    (scene.kind === 'compare' && (card?.items ?? []).filter((it) => filled(it.label) || filled(it.value)).length < 2);
  if (!empty) return scene.kind;
  return hasImage ? 'image' : 'text';
};

/** Uma cena: fundo com câmera + texturas + cartão + sobreposições + legendas + transição de entrada. */
export const SceneView: React.FC<Props> = ({ scene, style, assetBaseUrl, staticBase, lang = 'pt-BR' }) => {
  const { width, height } = useVideoConfig();
  const url = assetUrl(scene.src, assetBaseUrl);
  const cards = style.textCards;
  const kind = effectiveKind(scene, Boolean(url));
  const Card = CARD_VIEWS[kind];
  const headline = scene.card?.headline?.trim();
  const stamp = scene.card?.stamp?.trim();
  const source = scene.card?.source?.trim();
  const inPanel = PANEL_KINDS.has(kind);

  const position = style.captions.position;
  let captionAt: number | undefined;
  if (CARD_KINDS.has(kind)) captionAt = Math.max(0.76, captionTop(position));
  else if (headline && position === 'top') captionAt = 0.25;
  // Fonte: no vertical fica acima da área da interface do Shorts e longe da legenda.
  const captionCenter = captionAt ?? captionTop(position);
  const sourceY = width > height ? 0.925 : captionCenter < 0.7 ? 0.76 : captionCenter - 0.1;
  // Faixa livre para o painel dos cartões de dado: abaixo da faixa do topo e acima da legenda (meia legenda de 2
  // linhas e uma margem), para o painel nunca passar por baixo de nenhuma das duas.
  const captionFont = style.captions.size * (width / 1080);
  const captionHalf = style.captions.style === 'none' ? 0 : (captionFont * 1.12) / height;
  const band: CardBand = {
    top: headline ? headlineBox(headline, cards, lang, width, height).bottom / height + 0.02 : 0.06,
    bottom: captionAt !== undefined && style.captions.style !== 'none' ? captionAt - captionHalf - 0.02 : 0.9,
  };

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
          cards={cards}
          dim={DIM[kind] ?? 0}
          blur={BLUR[kind] ?? 0}
        />
      )}
      <EffectsLayer effects={style.effects} look={scene.look} seed={scene.seed} staticBase={staticBase} />
      {Card ? <Card scene={scene} style={cards} uppercase={style.captions.uppercase} lang={lang} band={band} /> : null}
      {headline ? <Headline text={headline} style={cards} lang={lang} /> : null}
      {stamp && !inPanel ? <Stamp text={stamp} style={cards} lang={lang} /> : null}
      {source && !inPanel ? <SourceChip source={source} style={cards} lang={lang} y={sourceY} /> : null}
      {NO_CAPTIONS.has(kind) ? null : <Captions lines={scene.captions} style={style.captions} topOverride={captionAt} />}
      <TransitionIn type={scene.transition} frames={style.transitionFrames} seed={scene.seed} />
    </AbsoluteFill>
  );
};
