import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, type StyleSpec } from '../api';
import { Badge, Button, Card, ErrorBox, Field, Input, Select, Tabs, Textarea } from '../ui';

type Detail = { style: StyleSpec; preset: boolean; warnings: string[] };

const EFFECT_TYPES = ['grain', 'vignette', 'grade', 'scanlines', 'chromatic', 'vhs', 'light_leak', 'film_scratches', 'halftone', 'duotone', 'sepia', 'blur_edges', 'flicker', 'dust'];
const GRADES = ['neutral', 'cold-teal-dark', 'warm-sepia', 'noir', 'bleach-bypass', 'vhs-faded', 'blood-red', 'night-green'];
const FONTS = ['Montserrat', 'Bebas Neue', 'Cinzel', 'Special Elite', 'VT323', 'Oswald', 'Anton', 'Noto Sans', 'IBM Plex Sans'];
const CAMERA = ['ken_burns_in', 'ken_burns_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down', 'punch_zoom', 'drift', 'static'];
const TRANSITIONS = ['cut', 'flash', 'fade_black', 'glitch', 'light_leak', 'zoom_blur', 'film_burn', 'static_burst'];
const MOODS = ['dark ambient drone', 'tension', 'horror', 'mystery', 'epic', 'pulse', 'calm'];
const VOICES = ['Charon', 'Algenib', 'Orus', 'Puck', 'Kore', 'Fenrir', 'Aoede', 'Enceladus', 'Iapetus', 'Gacrux', 'Schedar', 'Sadaltager'];

const Weights: React.FC<{ options: string[]; value: Record<string, number>; onChange: (v: Record<string, number>) => void }> = ({ options, value, onChange }) => (
  <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
    {options.map((o) => (
      <label key={o} className="flex items-center justify-between gap-2 rounded border border-zinc-800 px-2 py-1 text-xs">
        <span className="text-zinc-400">{o}</span>
        <input
          type="number"
          min={0}
          max={10}
          className="w-12 rounded bg-zinc-950 px-1 text-right"
          value={value[o] ?? 0}
          onChange={(e) => {
            const next = { ...value, [o]: Number(e.target.value) };
            if (!next[o]) delete next[o];
            onChange(next);
          }}
        />
      </label>
    ))}
  </div>
);

export const StyleEditorPage: React.FC = () => {
  const { styleId = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ['style', styleId], queryFn: () => api.get<Detail>(`/api/styles/${styleId}`) });
  const [style, setStyle] = useState<StyleSpec | null>(null);
  const [json, setJson] = useState('');
  const [tab, setTab] = useState('form');
  const [dupId, setDupId] = useState('');
  useEffect(() => {
    if (detail.data) {
      setStyle(detail.data.style);
      setJson(JSON.stringify(detail.data.style, null, 2));
    }
  }, [detail.data]);

  const save = useMutation({
    mutationFn: (s: StyleSpec) => api.put<{ style: StyleSpec; warnings: string[] }>(`/api/styles/${styleId}`, { style: s }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['style', styleId] });
      client.invalidateQueries({ queryKey: ['styles'] });
    },
  });
  const duplicate = useMutation({
    mutationFn: () => api.post<{ style: StyleSpec }>(`/api/styles/${styleId}/duplicate`, { new_id: dupId, name: `${style?.name} (cópia)` }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ['styles'] });
      navigate(`/estilos/${data.style.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/styles/${styleId}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['styles'] });
      navigate('/estilos');
    },
  });

  if (!style || !detail.data) return <div className="p-6 text-zinc-500">{detail.error ? <ErrorBox error={detail.error} /> : 'Carregando…'}</div>;
  const preset = detail.data.preset;
  const set = (mutate: (s: StyleSpec) => void) => {
    const next = structuredClone(style);
    mutate(next);
    setStyle(next);
    setJson(JSON.stringify(next, null, 2));
  };
  const effect = (type: string) => style.edit.effects.find((e) => e.type === type);
  const setEffect = (type: string, amount: number | null, preset?: string) =>
    set((s) => {
      const rest = s.edit.effects.filter((e) => e.type !== type);
      s.edit.effects = amount === null ? rest : [...rest, { type, amount, ...(type === 'grade' ? { preset: preset ?? effect('grade')?.preset ?? 'neutral' } : {}) }];
    });
  const submitJson = () => {
    try {
      const parsed = JSON.parse(json) as StyleSpec;
      save.mutate(parsed);
    } catch (err) {
      save.reset();
      alert(`JSON inválido: ${String(err)}`);
    }
  };

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-1 text-xs text-zinc-500">
        <Link to="/estilos" className="hover:text-zinc-300">Estilos</Link> / {style.id}
      </div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">{style.name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="id da cópia (ex.: meu-estilo)" value={dupId} onChange={(e) => setDupId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))} />
          <Button disabled={dupId.length < 2} busy={duplicate.isPending} onClick={() => duplicate.mutate()}>Duplicar</Button>
          {!preset ? (
            <>
              <Button variant="primary" busy={save.isPending} onClick={() => (tab === 'json' ? submitJson() : save.mutate(style))}>Salvar</Button>
              <Button variant="ghost" onClick={() => window.confirm('Apagar este estilo?') && remove.mutate()}>Apagar</Button>
            </>
          ) : (
            <Badge>preset (duplique para editar)</Badge>
          )}
        </div>
      </div>
      {detail.data.warnings.map((w) => (
        <div key={w} className="mb-1 text-xs text-amber-300/80">⚠ {w}</div>
      ))}
      <ErrorBox error={save.error ?? duplicate.error ?? remove.error} />
      <Tabs tabs={[{ id: 'form', label: 'Formulário' }, { id: 'json', label: 'JSON completo' }]} active={tab} onChange={setTab} />
      <fieldset disabled={preset} className="pt-4">
        {tab === 'json' ? (
          <Textarea className="h-[70vh] w-full font-mono text-xs" value={json} onChange={(e) => setJson(e.target.value)} />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="flex flex-col gap-3">
              <div className="text-sm font-semibold">Identidade e formato</div>
              <Field label="Nome"><Input value={style.name} onChange={(e) => set((s) => { s.name = e.target.value; })} /></Field>
              <Field label="Descrição"><Textarea rows={2} value={style.description} onChange={(e) => set((s) => { s.description = e.target.value; })} /></Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Tipo">
                  <Select value={style.format.type} onChange={(e) => set((s) => {
                    s.format.type = e.target.value as 'short' | 'long';
                    s.format.aspect = s.format.type === 'short' ? '9:16' : '16:9';
                    s.format.resolution = s.format.type === 'short' ? '1080x1920' : '1920x1080';
                  })}>
                    <option value="short">Short 9:16</option>
                    <option value="long">Longo 16:9</option>
                  </Select>
                </Field>
                <Field label="Duração alvo (s)"><Input type="number" value={style.format.duration.target_s} onChange={(e) => set((s) => { s.format.duration.target_s = Number(e.target.value); })} /></Field>
              </div>
              <Field label="Estrutura">
                <Select value={style.structure} onChange={(e) => set((s) => { s.structure = e.target.value; })}>
                  {['none', 'top10', 'iceberg', 'tierlist', 'quiz', 'timeline', 'history_in_n', 'pov', 'series', 'would_you_rather'].map((o) => <option key={o}>{o}</option>)}
                </Select>
              </Field>
              <Field label="Tom do roteiro"><Textarea rows={2} value={style.script.tone} onChange={(e) => set((s) => { s.script.tone = e.target.value; })} /></Field>
              <Field label="Etapas do roteiro (separadas por vírgula)">
                <Input value={style.script.structure.join(', ')} onChange={(e) => set((s) => { s.script.structure = e.target.value.split(',').map((x) => x.trim()).filter(Boolean); })} />
              </Field>
              <Field label="Chamada final (CTA)"><Input value={style.script.cta ?? ''} onChange={(e) => set((s) => { s.script.cta = e.target.value || null; })} /></Field>
            </Card>
            <Card className="flex flex-col gap-3">
              <div className="text-sm font-semibold">Voz e arte</div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Voz (Gemini)">
                  <Select value={style.voice.voice ?? 'Charon'} onChange={(e) => set((s) => { s.voice.voice = e.target.value; })}>
                    {VOICES.map((v) => <option key={v}>{v}</option>)}
                  </Select>
                </Field>
                <Field label="Velocidade"><Input type="number" step={0.05} value={style.voice.speed} onChange={(e) => set((s) => { s.voice.speed = Number(e.target.value); })} /></Field>
              </div>
              <Field label="Interpretação da voz"><Textarea rows={2} value={style.voice.style_prompt ?? ''} onChange={(e) => set((s) => { s.voice.style_prompt = e.target.value || null; })} /></Field>
              <Field label="Bíblia de arte (estilo das imagens)"><Textarea rows={3} value={style.art.bible} onChange={(e) => set((s) => { s.art.bible = e.target.value; })} /></Field>
              <Field label="Evitar nas imagens (vírgulas)"><Input value={style.art.banned.join(', ')} onChange={(e) => set((s) => { s.art.banned = e.target.value.split(',').map((x) => x.trim()).filter(Boolean); })} /></Field>
              <label className="flex items-center gap-2 text-xs text-zinc-400">
                <input type="checkbox" checked={style.art.photoreal} onChange={(e) => set((s) => { s.art.photoreal = e.target.checked; })} />
                Arte fotorrealista (liga o rótulo de conteúdo sintético)
              </label>
            </Card>
            <Card className="flex flex-col gap-3 md:col-span-2">
              <div className="text-sm font-semibold">Edição</div>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                <Field label="Cena mín. (s)"><Input type="number" step={0.1} value={style.edit.shot_seconds.min} onChange={(e) => set((s) => { s.edit.shot_seconds.min = Number(e.target.value); })} /></Field>
                <Field label="Cena máx. (s)"><Input type="number" step={0.1} value={style.edit.shot_seconds.max} onChange={(e) => set((s) => { s.edit.shot_seconds.max = Number(e.target.value); })} /></Field>
                <Field label="Força do zoom"><Input type="number" step={0.01} value={style.edit.zoom_strength} onChange={(e) => set((s) => { s.edit.zoom_strength = Number(e.target.value); })} /></Field>
                <Field label="Transição (s)"><Input type="number" step={0.05} value={style.edit.transition_seconds} onChange={(e) => set((s) => { s.edit.transition_seconds = Number(e.target.value); })} /></Field>
              </div>
              <Field label="Movimentos de câmera (peso de sorteio por cena)"><Weights options={CAMERA} value={style.edit.camera} onChange={(v) => set((s) => { s.edit.camera = v; })} /></Field>
              <Field label="Transições (peso)"><Weights options={TRANSITIONS} value={style.edit.transitions} onChange={(v) => set((s) => { s.edit.transitions = v; })} /></Field>
              <Field label="Efeitos (intensidade 0–1; vazio = desligado)">
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                  {EFFECT_TYPES.map((t) => (
                    <label key={t} className="flex items-center justify-between gap-2 rounded border border-zinc-800 px-2 py-1 text-xs">
                      <span className="text-zinc-400">{t}</span>
                      <input
                        type="number"
                        min={0}
                        max={1}
                        step={0.05}
                        className="w-14 rounded bg-zinc-950 px-1 text-right"
                        value={effect(t)?.amount ?? ''}
                        onChange={(e) => setEffect(t, e.target.value === '' ? null : Number(e.target.value))}
                      />
                    </label>
                  ))}
                </div>
              </Field>
              {effect('grade') ? (
                <Field label="Gradação de cor">
                  <Select value={effect('grade')?.preset ?? 'neutral'} onChange={(e) => setEffect('grade', effect('grade')?.amount ?? 0.6, e.target.value)}>
                    {GRADES.map((g) => <option key={g}>{g}</option>)}
                  </Select>
                </Field>
              ) : null}
            </Card>
            <Card className="flex flex-col gap-3">
              <div className="text-sm font-semibold">Legendas</div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Estilo">
                  <Select value={style.edit.captions.style} onChange={(e) => set((s) => { s.edit.captions.style = e.target.value; })}>
                    <option value="karaoke">karaokê</option>
                    <option value="word">palavra a palavra</option>
                    <option value="line">linha</option>
                    <option value="none">sem legenda</option>
                  </Select>
                </Field>
                <Field label="Fonte">
                  <Select value={style.edit.captions.font} onChange={(e) => set((s) => { s.edit.captions.font = e.target.value; })}>
                    {FONTS.map((f) => <option key={f}>{f}</option>)}
                  </Select>
                </Field>
                <Field label="Tamanho"><Input type="number" value={style.edit.captions.size} onChange={(e) => set((s) => { s.edit.captions.size = Number(e.target.value); })} /></Field>
                <Field label="Palavras por linha"><Input type="number" value={style.edit.captions.max_words_per_line} onChange={(e) => set((s) => { s.edit.captions.max_words_per_line = Number(e.target.value); })} /></Field>
                <Field label="Cor"><Input type="color" value={style.edit.captions.color} onChange={(e) => set((s) => { s.edit.captions.color = e.target.value; })} /></Field>
                <Field label="Destaque"><Input type="color" value={style.edit.captions.highlight} onChange={(e) => set((s) => { s.edit.captions.highlight = e.target.value; })} /></Field>
                <Field label="Posição">
                  <Select value={style.edit.captions.position} onChange={(e) => set((s) => { s.edit.captions.position = e.target.value; })}>
                    <option value="top">topo</option>
                    <option value="center">centro</option>
                    <option value="bottom">base</option>
                  </Select>
                </Field>
                <label className="flex items-center gap-2 pt-6 text-xs text-zinc-400">
                  <input type="checkbox" checked={style.edit.captions.uppercase} onChange={(e) => set((s) => { s.edit.captions.uppercase = e.target.checked; })} />
                  MAIÚSCULAS
                </label>
              </div>
            </Card>
            <Card className="flex flex-col gap-3">
              <div className="text-sm font-semibold">Cartões de texto, áudio e barra</div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Fonte dos cartões">
                  <Select value={style.edit.text_cards.font} onChange={(e) => set((s) => { s.edit.text_cards.font = e.target.value; })}>
                    {FONTS.map((f) => <option key={f}>{f}</option>)}
                  </Select>
                </Field>
                <Field label="Animação">
                  <Select value={style.edit.text_cards.animation} onChange={(e) => set((s) => { s.edit.text_cards.animation = e.target.value; })}>
                    {['fade_up', 'typewriter', 'pop', 'slide', 'flicker'].map((a) => <option key={a}>{a}</option>)}
                  </Select>
                </Field>
                <Field label="Cor de destaque"><Input type="color" value={style.edit.text_cards.accent} onChange={(e) => set((s) => { s.edit.text_cards.accent = e.target.value; })} /></Field>
                <Field label="Fundo"><Input type="color" value={style.edit.text_cards.background} onChange={(e) => set((s) => { s.edit.text_cards.background = e.target.value; })} /></Field>
                <Field label="Fonte da trilha" hint="biblioteca = suas faixas em Configurações">
                  <Select value={style.audio.music.source} onChange={(e) => set((s) => { s.audio.music.source = e.target.value; })}>
                    <option value="procedural">procedural (gerada)</option>
                    <option value="library">biblioteca (suas faixas)</option>
                    <option value="none">sem música</option>
                  </Select>
                </Field>
                <Field label="Clima da trilha">
                  <Select value={style.audio.music.mood} onChange={(e) => set((s) => { s.audio.music.mood = e.target.value; })}>
                    {MOODS.map((m) => <option key={m}>{m}</option>)}
                  </Select>
                </Field>
                <Field label="Volume da trilha (dB)"><Input type="number" value={style.audio.music.volume_db} onChange={(e) => set((s) => { s.audio.music.volume_db = Number(e.target.value); })} /></Field>
              </div>
              <label className="flex items-center gap-2 text-xs text-zinc-400">
                <input type="checkbox" checked={style.audio.sfx.auto} onChange={(e) => set((s) => { s.audio.sfx.auto = e.target.checked; })} />
                Efeitos sonoros automáticos nas transições
              </label>
              <label className="flex items-center gap-2 text-xs text-zinc-400">
                <input type="checkbox" checked={style.edit.progress_bar} onChange={(e) => set((s) => { s.edit.progress_bar = e.target.checked; })} />
                Barra de progresso
              </label>
            </Card>
          </div>
        )}
      </fieldset>
    </div>
  );
};
