// Editor de um candidato do pacote: título, texto da thumb (com contador de palavras), promessa, template, fundo
// (biblioteca de frames com o tempo do trailer, upload, mídia do projeto, degradê), personagens recortados, pose do
// avatar e pílula. Salvar grava o candidato como "manual" e o servidor re-renderiza só a thumb dele.

import { Plus, Save, Trash2, TriangleAlert, Upload, X } from 'lucide-react';
import React, { useId, useRef, useState } from 'react';
import {
  THUMB_TEMPLATES,
  type BgLayer,
  type CutoutLayer,
  type PackageCandidate,
  type PresenterThumbLayer,
  type TextLayer,
  type ThumbLayers,
  type ThumbTemplate,
} from '../../../api';
import {
  localWarnings,
  PACKAGE_TITLE_MAX,
  syncTextLayer,
  TEMPLATE_LABELS,
  THUMB_WORDS_MAX,
  wordCount,
  type CandidatePatch,
  type ThumbkitItem,
  type UploadedThumbkitItem,
} from '../../../apiPackage';
import { Button, Checkbox, ErrorBox, Field, FieldGroup, Input, SegmentedControl, Select, Textarea } from '../../../ui';

/** Alvo do título curto (os títulos que estouram no nicho têm ~38 caracteres; o gerado vai até 55). */
const TITLE_TARGET = 50;

const BG_KINDS: { id: BgLayer['kind']; label: string }[] = [
  { id: 'frame', label: 'Frame da biblioteca' },
  { id: 'upload', label: 'Imagem enviada' },
  { id: 'media', label: 'Mídia do projeto' },
  { id: 'gradient', label: 'Degradê' },
];

const DEFAULT_PRESENTER_LAYER = (pose: string): PresenterThumbLayer => ({ pose, side: 'right', crop: 'busto', h: 1.5, outline_px: 10, flip: false });
const DEFAULT_CUTOUT = (asset: string): CutoutLayer => ({ asset, x: 0.7, y: 1, h: 0.9, flip: false });

/** Controle deslizante com rótulo e valor (mesmo visual do editor de estilos). */
const Range: React.FC<{
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display?: string;
  disabled?: boolean;
  onChange: (v: number) => void;
}> = ({ label, value, min, max, step, display, disabled, onChange }) => {
  const id = useId();
  const shown = display ?? String(value);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[13px] font-medium text-fg">
          {label}
        </label>
        <output htmlFor={id} className="text-[13px] tabular-nums text-muted">
          {shown}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-valuetext={shown}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-5 w-full cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-60"
      />
    </div>
  );
};

/** Grade de imagens da biblioteca (frames, recortes ou poses) para escolher uma. */
const PickGrid: React.FC<{
  label: string;
  items: { id: string; src: string | null; title: string; badge?: string }[];
  value: string | null;
  onPick: (id: string) => void;
  vertical?: boolean;
}> = ({ label, items, value, onPick, vertical }) => (
  <div role="radiogroup" aria-label={label} className={`grid gap-2 ${vertical ? 'grid-cols-[repeat(auto-fill,minmax(72px,1fr))]' : 'grid-cols-[repeat(auto-fill,minmax(110px,1fr))]'}`}>
    {items.map((it) => {
      const on = it.id === value;
      return (
        <button
          key={it.id}
          type="button"
          role="radio"
          aria-checked={on}
          title={it.title}
          onClick={() => onPick(it.id)}
          className={`relative overflow-hidden rounded-lg border bg-canvas text-left ${on ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-line-strong'}`}
        >
          <div className={vertical ? 'aspect-[2/3]' : 'aspect-video'}>
            {it.src ? <img src={it.src} alt="" loading="lazy" className={`h-full w-full ${vertical ? 'object-contain' : 'object-cover'}`} /> : null}
          </div>
          <div className="truncate px-1.5 py-1 text-[11px] text-muted">{it.title}</div>
          {it.badge ? <span className="absolute right-1 top-1 rounded bg-black/70 px-1 text-[10px] text-white">{it.badge}</span> : null}
        </button>
      );
    })}
  </div>
);

/** Pose oferecida no editor (id, imagem para a grade e emoções). */
export type EditorPose = { id: string; src: string | null; emotions: string[] };

export type EditorAvatar = {
  /** Avatar ligado para a thumb deste projeto (canal + projeto + estilo). */
  on: boolean;
  /** Poses boas para thumb (`thumb_ok`), menos as que o projeto desligou. */
  poses: EditorPose[];
};

/** Mídia e upload digitados sem prefixo ganham `media:`/`upload:` (é assim que o resolvedor de imagens os acha). */
const withRefPrefix = (bg: BgLayer): BgLayer => {
  const ref = bg.ref?.trim() || null;
  if (ref && (bg.kind === 'media' || bg.kind === 'upload') && !ref.includes(':')) return { ...bg, ref: `${bg.kind}:${ref}` };
  return { ...bg, ref };
};

/** O frame da biblioteca que um `ref` de fundo usa: o id do item (com ou sem `frame:`) ou `frame:<trailer>@<t>`. */
const frameOf = (frames: ThumbkitItem[], ref: string | null) => {
  if (!ref) return undefined;
  const bare = ref.startsWith('frame:') ? ref.slice(6) : ref;
  return frames.find((f) => f.id === bare) ?? frames.find((f) => f.video && bare.startsWith(`${f.video}@`));
};

export const CandidateEditor: React.FC<{
  candidate: PackageCandidate;
  isShort: boolean;
  /** Templates que o servidor oferece para este projeto (null: a tela decide pelo formato e pelo avatar). */
  templateOptions: string[] | null;
  allowEditorial: boolean;
  avatar: EditorAvatar;
  library: ThumbkitItem[];
  libraryLoading: boolean;
  canUpload: boolean;
  saving: boolean;
  saveError: unknown;
  disabledReason: string | null;
  onUpload: (file: File, kind: 'frame' | 'cutout') => Promise<UploadedThumbkitItem | null>;
  onSave: (patch: CandidatePatch) => void;
  onClose: () => void;
}> = ({ candidate, isShort, templateOptions, allowEditorial, avatar, library, libraryLoading, canUpload, saving, saveError, disabledReason, onUpload, onSave, onClose }) => {
  const [title, setTitle] = useState(candidate.title);
  const [thumbText, setThumbText] = useState(candidate.thumb_text);
  const [promise, setPromise] = useState(candidate.promise);
  const [concept, setConcept] = useState(candidate.concept);
  const [layers, setLayers] = useState<ThumbLayers>(candidate.layers);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const cutoutFileRef = useRef<HTMLInputElement>(null);

  const set = <K extends keyof ThumbLayers>(key: K, value: ThumbLayers[K]) => setLayers((l) => ({ ...l, [key]: value }));
  const setBg = (patch: Partial<BgLayer>) => setLayers((l) => ({ ...l, background: { ...l.background, ...patch } }));
  const setText = (patch: Partial<TextLayer>) =>
    setLayers((l) => ({ ...l, text: { words: thumbText, fill: 'yellow', pos: 'left', size: 0.3, ...(l.text ?? {}), ...patch } }));
  const setPresenter = (patch: Partial<PresenterThumbLayer> | null) =>
    setLayers((l) => ({ ...l, presenter: patch === null ? null : { ...(l.presenter ?? DEFAULT_PRESENTER_LAYER(avatar.poses[0]?.id ?? 'base')), ...patch } }));

  const frames = library.filter((i) => i.kind === 'frame');
  const cutouts = library.filter((i) => i.kind === 'cutout');
  const editorialFrames = frames.filter((f) => f.editorial);
  const pickedFrame = layers.background.kind === 'frame' ? frameOf(frames, layers.background.ref) : undefined;

  // Templates: os do servidor; sem eles, o avatar-reage só com o avatar ligado e o livre só no Short. O atual fica.
  const offered = (t: ThumbTemplate) => {
    if (templateOptions) return templateOptions.includes(t);
    if (isShort) return t === 'livre';
    return t !== 'livre' && (t !== 'avatar-reage' || avatar.on);
  };
  const templates = THUMB_TEMPLATES.filter((t) => t === layers.template || offered(t));

  const words = wordCount(thumbText);
  // Texto total visível: palavra grande + pílula + cartões (no máximo 3 palavras, ideal 1 a 2).
  const totalWords = words + wordCount(layers.pill ?? '') + layers.cards.reduce((n, c) => n + wordCount(c.label), 0);
  const warnings = localWarnings({ title, thumb_text: thumbText }, isShort);
  const dirty =
    title !== candidate.title ||
    thumbText !== candidate.thumb_text ||
    promise !== candidate.promise ||
    concept !== candidate.concept ||
    JSON.stringify(layers) !== JSON.stringify(candidate.layers);

  const upload = async (file: File | undefined, kind: 'frame' | 'cutout') => {
    if (!file) return;
    setUploadError(null);
    setUploading(true);
    try {
      const item = await onUpload(file, kind);
      if (!item) return;
      if (kind === 'frame') setBg({ kind: 'upload', ref: `upload:${item.path ?? item.file ?? item.id}`, t: null });
      else set('characters', [...layers.characters, DEFAULT_CUTOUT(item.id)]);
    } catch (e) {
      setUploadError(e);
    } finally {
      setUploading(false);
    }
  };

  const save = () =>
    onSave({
      title: title.trim(),
      thumb_text: thumbText.trim(),
      promise: promise.trim(),
      concept: concept.trim(),
      layers: syncTextLayer({ ...layers, background: withRefPrefix(layers.background) }, thumbText),
    });

  const bg = layers.background;
  const blocked = Boolean(disabledReason);

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 @3xl:grid-cols-2">
        <Field
          label="Título"
          hint={`${title.length} caracteres (alvo: até ${TITLE_TARGET}; máximo ${PACKAGE_TITLE_MAX}). Curto, uma ideia só; a thumb soma ao título, não repete.`}
          error={title.length > PACKAGE_TITLE_MAX ? 'Título longo demais.' : title.length > TITLE_TARGET ? 'Acima do alvo: título curto engaja mais.' : undefined}
        >
          <Input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field
          label="Texto da thumb"
          hint={isShort ? `${words} palavra(s). No Short o texto fica no centro seguro.` : `${totalWords}/${THUMB_WORDS_MAX} palavras visíveis, com pílula e cartões (ideal 1 a 2).`}
          error={!isShort && totalWords > THUMB_WORDS_MAX ? `Mais de ${THUMB_WORDS_MAX} palavras na thumb: some na miniatura pequena.` : undefined}
        >
          <Input value={thumbText} maxLength={60} onChange={(e) => setThumbText(e.target.value)} placeholder="ex.: 7 DIAS" />
        </Field>
        <Field label="Promessa" hint="O que o espectador ganha. O gancho do roteiro cumpre essa promessa nos primeiros segundos.">
          <Textarea rows={2} value={promise} onChange={(e) => setPromise(e.target.value)} />
        </Field>
        <Field label="Conceito da thumb" hint="Descrição para humanos: o que a imagem mostra.">
          <Textarea rows={2} value={concept} onChange={(e) => setConcept(e.target.value)} />
        </Field>
      </div>

      <div className="grid gap-4 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Field label="Template">
          <Select value={layers.template} onChange={(e) => set('template', e.target.value as ThumbTemplate)}>
            {templates.map((t) => (
              <option key={t} value={t}>
                {TEMPLATE_LABELS[t] ?? t}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Pílula (opcional)" hint="Desligada por padrão; conta nas 3 palavras. Selo de verificação (CONFIRMADO) não vai na thumb.">
          <Input value={layers.pill ?? ''} maxLength={30} onChange={(e) => set('pill', e.target.value.trim() ? e.target.value : null)} />
        </Field>
      </div>

      {/* Texto */}
      <FieldGroup legend="Texto">
        <div className="grid gap-3 @3xl:grid-cols-3">
          <SegmentedControl
            label="Cor do texto"
            size="sm"
            value={layers.text?.fill ?? 'yellow'}
            onChange={(fill) => setText({ fill })}
            options={[
              { id: 'yellow', label: 'Amarelo' },
              { id: 'white', label: 'Branco' },
              { id: 'red', label: 'Vermelho' },
              { id: 'cyan', label: 'Ciano' },
              { id: 'lime', label: 'Limão' },
              { id: 'orange', label: 'Laranja' },
              { id: 'vice', label: 'Vice' },
            ]}
          />
          <SegmentedControl
            label="Posição do texto"
            size="sm"
            value={layers.text?.pos ?? 'left'}
            onChange={(pos) => setText({ pos })}
            options={[
              { id: 'top', label: 'Topo' },
              { id: 'left', label: 'Esquerda' },
              { id: 'right', label: 'Direita' },
              { id: 'center', label: 'Centro' },
            ]}
          />
          <Range
            label="Tamanho"
            value={layers.text?.size ?? 0.3}
            min={0.1}
            max={0.7}
            step={0.01}
            display={`${Math.round((layers.text?.size ?? 0.3) * 100)}% da altura`}
            onChange={(size) => setText({ size })}
          />
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <Checkbox label="Risco vermelho sobre a palavra" checked={layers.strike} onChange={(v) => set('strike', v)} />
          {(layers.template === 'prova' || layers.template === 'objeto') && (
            <SegmentedControl
              label="Marcação na prova"
              size="sm"
              value={layers.mark ?? 'none'}
              onChange={(mark) => set('mark', mark === 'none' ? null : (mark as 'x' | 'circle'))}
              options={[
                { id: 'none', label: 'Nenhuma' },
                { id: 'x', label: 'X vermelho' },
                { id: 'circle', label: 'Círculo' },
              ]}
            />
          )}
          <Checkbox label="VI gigante atrás" checked={layers.big_vi} onChange={(v) => set('big_vi', v)} />
          <Checkbox label="Logo no canto" checked={layers.logo === 'corner'} onChange={(v) => set('logo', v ? 'corner' : 'none')} />
          <Checkbox label="Seta amarela" checked={Boolean(layers.arrow)} onChange={(v) => set('arrow', v ? { from_x: 0.35, from_y: 0.55, to_x: 0.55, to_y: 0.45, color: 'yellow' } : null)} />
        </div>
      </FieldGroup>

      {/* Fundo */}
      <FieldGroup legend="Fundo" hint={allowEditorial ? undefined : 'Material oficial desligado neste canal: frames de trailer, capa e logos marcados como oficiais não entram.'}>
        <SegmentedControl
          label="Tipo de fundo"
          size="sm"
          value={bg.kind === 'ai' ? 'gradient' : bg.kind}
          onChange={(kind) => setBg({ kind, ref: kind === bg.kind ? bg.ref : null, t: kind === 'frame' ? bg.t : null })}
          options={BG_KINDS.map((k) => ({ id: k.id, label: k.label }))}
        />
        {bg.kind === 'ai' ? <p className="text-[13px] text-amber-100">Fundo gerado por IA: desligado (sem cota). Escolha outro tipo.</p> : null}
        {bg.kind === 'frame' ? (
          frames.length ? (
            <>
              <PickGrid
                label="Frames da biblioteca"
                value={pickedFrame?.id ?? null}
                onPick={(id) => {
                  const item = frames.find((f) => f.id === id);
                  setBg({ ref: id, t: item?.t ?? null, focus_x: item?.focus_x ?? bg.focus_x });
                }}
                items={frames.map((f) => ({
                  id: f.id,
                  src: f.src,
                  title: [f.id, ...f.tags].join(' · '),
                  badge: f.editorial ? (allowEditorial ? 'oficial' : 'oficial: bloqueado') : undefined,
                }))}
              />
              {!allowEditorial && pickedFrame?.editorial ? (
                <p className="flex gap-1.5 text-[13px] text-amber-100">
                  <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" /> Este frame é material oficial e o canal não libera material oficial nas thumbs.
                </p>
              ) : null}
              {pickedFrame?.video ? (
                // Outro instante do mesmo trailer: o fundo vira `frame:<trailer>@<segundos>` (o servidor extrai e guarda).
                <Range
                  label={`Tempo no trailer (${pickedFrame.video})`}
                  value={bg.t ?? pickedFrame.t ?? 0}
                  min={0}
                  max={Math.max(1, Math.ceil(pickedFrame.duration_s ?? 180))}
                  step={0.5}
                  display={`${(bg.t ?? pickedFrame.t ?? 0).toFixed(1)} s`}
                  onChange={(t) => setBg({ t, ref: `frame:${pickedFrame.video}@${t}` })}
                />
              ) : null}
              {editorialFrames.length && allowEditorial ? (
                <p className="text-xs text-subtle">{editorialFrames.length} frame(s) oficiais na biblioteca.</p>
              ) : null}
            </>
          ) : (
            <p className="text-[13px] text-subtle">
              {libraryLoading ? 'Carregando a biblioteca…' : 'A biblioteca de thumbs do canal está vazia (ou o servidor ainda não tem a rota). Use o id do frame abaixo ou envie uma imagem.'}
            </p>
          )
        ) : null}
        {bg.kind === 'upload' ? (
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={(e) => void upload(e.target.files?.[0], 'frame')} />
            <Button size="sm" icon={Upload} busy={uploading} disabled={!canUpload} onClick={() => fileRef.current?.click()}>
              Enviar imagem
            </Button>
            {bg.ref ? <span className="truncate text-xs text-muted">{bg.ref}</span> : null}
          </div>
        ) : null}
        {bg.kind !== 'gradient' ? (
          <Field label="Referência" hint={bg.kind === 'frame' ? 'Id do frame da biblioteca ou frame:<trailer>@<segundos>.' : bg.kind === 'media' ? 'Id da mídia do projeto.' : 'Caminho do arquivo enviado.'}>
            <Input value={bg.ref ?? ''} onChange={(e) => setBg({ ref: e.target.value.trim() ? e.target.value : null })} />
          </Field>
        ) : (
          <Field label="Degradê (opcional)" hint="Nome ou cores do degradê; vazio usa o pôr do sol do canal.">
            <Input value={bg.ref ?? ''} onChange={(e) => setBg({ ref: e.target.value.trim() ? e.target.value : null })} />
          </Field>
        )}
        <div className="grid gap-3 @3xl:grid-cols-4">
          <Range label="Zoom" value={bg.zoom} min={1} max={4} step={0.05} display={`${bg.zoom.toFixed(2)}×`} onChange={(zoom) => setBg({ zoom })} />
          <Range label="Assunto na horizontal" value={bg.focus_x} min={0} max={1} step={0.01} display={`${Math.round(bg.focus_x * 100)}%`} onChange={(focus_x) => setBg({ focus_x })} />
          <Range label="Escurecer" value={bg.darken} min={0} max={1} step={0.05} display={`${Math.round(bg.darken * 100)}%`} onChange={(darken) => setBg({ darken })} />
          <Range label="Desfoque" value={bg.blur} min={0} max={60} step={1} display={`${bg.blur} px`} onChange={(blur) => setBg({ blur })} />
        </div>
      </FieldGroup>

      {/* Personagens recortados */}
      <FieldGroup
        legend="Personagens recortados"
        hint="PNG com fundo transparente. Personagens oficiais (Jason, Lucia) nunca são gerados por IA: só recortes de material oficial."
      >
        {layers.characters.map((ch, i) => (
          <div key={`${ch.asset}-${i}`} className="grid items-end gap-3 rounded-lg border border-line p-3 @3xl:grid-cols-[minmax(0,1.2fr)_repeat(3,minmax(0,1fr))_auto]">
            <Field label="Recorte">
              <Select
                value={ch.asset}
                onChange={(e) => set('characters', layers.characters.map((c, j) => (j === i ? { ...c, asset: e.target.value } : c)))}
              >
                {[ch.asset, ...cutouts.map((c) => c.id).filter((id) => id !== ch.asset)].map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </Select>
            </Field>
            <Range label="Horizontal" value={ch.x} min={-0.5} max={1.5} step={0.01} display={`${Math.round(ch.x * 100)}%`} onChange={(x) => set('characters', layers.characters.map((c, j) => (j === i ? { ...c, x } : c)))} />
            <Range label="Altura" value={ch.h} min={0.2} max={2.5} step={0.05} display={`${Math.round(ch.h * 100)}%`} onChange={(h) => set('characters', layers.characters.map((c, j) => (j === i ? { ...c, h } : c)))} />
            <Checkbox label="Espelhar" checked={ch.flip} onChange={(flip) => set('characters', layers.characters.map((c, j) => (j === i ? { ...c, flip } : c)))} />
            <Button size="sm" variant="ghost" icon={Trash2} onClick={() => set('characters', layers.characters.filter((_, j) => j !== i))}>
              Tirar
            </Button>
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          {cutouts.length ? (
            <Select
              aria-label="Adicionar recorte"
              value=""
              onChange={(e) => {
                if (e.target.value) set('characters', [...layers.characters, DEFAULT_CUTOUT(e.target.value)]);
              }}
              className="max-w-64"
            >
              <option value="">Adicionar recorte…</option>
              {cutouts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id}
                </option>
              ))}
            </Select>
          ) : (
            <span className="text-[13px] text-subtle">Precisa de recorte: a biblioteca não tem PNG recortado. Sem recorte, o personagem vem dentro do frame do fundo.</span>
          )}
          <input ref={cutoutFileRef} type="file" accept="image/png" className="sr-only" onChange={(e) => void upload(e.target.files?.[0], 'cutout')} />
          <Button size="sm" variant="ghost" icon={Plus} busy={uploading} disabled={!canUpload} onClick={() => cutoutFileRef.current?.click()}>
            Enviar PNG recortado
          </Button>
        </div>
      </FieldGroup>

      {/* Avatar */}
      <FieldGroup
        legend="Avatar do apresentador"
        hint={avatar.on ? 'Busto grande, vazando por baixo, vende mais que a pose inteira pequena.' : 'Avatar desligado para a thumb deste projeto (canal ou ajuste do projeto).'}
      >
        {avatar.on && avatar.poses.length ? (
          <>
            <PickGrid
              label="Pose do avatar"
              vertical
              value={layers.presenter?.pose ?? null}
              onPick={(pose) => setPresenter({ pose })}
              items={avatar.poses.map((p) => ({ id: p.id, src: p.src, title: [p.id, ...p.emotions].join(' · ') }))}
            />
            {layers.presenter ? (
              <div className="grid items-end gap-3 @3xl:grid-cols-4">
                <SegmentedControl
                  label="Lado do avatar"
                  size="sm"
                  value={layers.presenter.side}
                  onChange={(side) => setPresenter({ side })}
                  options={[
                    { id: 'left', label: 'Esquerda' },
                    { id: 'right', label: 'Direita' },
                  ]}
                />
                <SegmentedControl
                  label="Corte do avatar"
                  size="sm"
                  value={layers.presenter.crop}
                  onChange={(crop) => setPresenter({ crop })}
                  options={[
                    { id: 'busto', label: 'Busto' },
                    { id: 'meio', label: 'Meio corpo' },
                  ]}
                />
                <Range label="Altura" value={layers.presenter.h} min={0.3} max={2.5} step={0.05} display={`${Math.round(layers.presenter.h * 100)}%`} onChange={(h) => setPresenter({ h })} />
                <div className="flex flex-wrap items-center gap-3">
                  <Checkbox label="Espelhar" checked={layers.presenter.flip} onChange={(flip) => setPresenter({ flip })} />
                  <Button size="sm" variant="ghost" icon={X} onClick={() => setPresenter(null)}>
                    Sem avatar
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        ) : layers.presenter ? (
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
            Este candidato usa a pose “{layers.presenter.pose}”, mas o avatar está desligado.
            <Button size="sm" variant="ghost" icon={X} onClick={() => setPresenter(null)}>
              Tirar o avatar
            </Button>
          </div>
        ) : null}
      </FieldGroup>

      {/* Cartões do numero-lista */}
      {layers.template === 'numero-lista' ? (
        <FieldGroup legend="Cartões (até 3)" hint="Miniatura + rótulo curto empilhados ao lado do número.">
          {layers.cards.map((card, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Input
                aria-label={`Rótulo do cartão ${i + 1}`}
                value={card.label}
                maxLength={30}
                onChange={(e) => set('cards', layers.cards.map((c, j) => (j === i ? { ...c, label: e.target.value } : c)))}
                className="max-w-56"
              />
              <Select
                aria-label={`Imagem do cartão ${i + 1}`}
                value={card.asset ?? ''}
                onChange={(e) => set('cards', layers.cards.map((c, j) => (j === i ? { ...c, asset: e.target.value || null } : c)))}
                className="max-w-56"
              >
                <option value="">Sem imagem</option>
                {library.map((it) => (
                  <option key={it.id} value={it.id}>
                    {it.id}
                  </option>
                ))}
              </Select>
              <Button size="sm" variant="ghost" icon={Trash2} onClick={() => set('cards', layers.cards.filter((_, j) => j !== i))}>
                Tirar
              </Button>
            </div>
          ))}
          {layers.cards.length < 3 ? (
            <Button size="sm" variant="ghost" icon={Plus} className="self-start" onClick={() => set('cards', [...layers.cards, { asset: null, label: '' }])}>
              Adicionar cartão
            </Button>
          ) : null}
        </FieldGroup>
      ) : null}

      {warnings.length ? (
        <ul className="flex flex-col gap-1 rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-[13px] text-amber-100">
          {warnings.map((w) => (
            <li key={w} className="flex gap-1.5">
              <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
              {w}
            </li>
          ))}
        </ul>
      ) : null}
      <ErrorBox error={uploadError} title="Não foi possível enviar a imagem" onClose={() => setUploadError(null)} />
      <ErrorBox error={saveError} title="Não foi possível salvar o candidato" />

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
        {disabledReason ? <span className="mr-auto text-[13px] text-subtle">{disabledReason}</span> : null}
        <Button variant="ghost" onClick={onClose}>
          {dirty ? 'Descartar' : 'Fechar'}
        </Button>
        <Button variant="primary" icon={Save} busy={saving} disabled={!dirty || blocked || !title.trim() || title.length > PACKAGE_TITLE_MAX} onClick={save}>
          Salvar e montar a thumb
        </Button>
      </div>
    </div>
  );
};
