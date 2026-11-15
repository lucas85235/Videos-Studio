// "Faixas do vídeo" no painel Trilha (ADR 0003, bloco G): uma trilha por trecho, cada uma com segmento de início,
// faixa ou clima, volume e "só a batida, em loop no compasso".
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Music, Plus, Power, Sparkles } from 'lucide-react';
import type React from 'react';
import { useMemo, useState } from 'react';
import { api, type LibraryMusic, MOOD_LABELS, type ProjectDetail, type Segment } from '../../../../api';
import { audioApi, type MusicBed, type MusicCueDoc, type MusicCuration } from '../../../../apiAudio';
import { label } from '../../../../labels';
import { Badge, Button, Checkbox, ErrorBox, Field, Input, Select, useToast } from '../../../../ui';
import { clock, db, useAudioJob } from './useAudioJob';

type MusicOp = { op: 'set_music'; cue_id?: string; start_segment?: string; mood?: string; volume_db?: number;
  enabled?: boolean; track?: string; loop?: boolean };

const segmentLabel = (seg: Segment, index: number, lang: string): string => {
  const text = (seg.text[lang] ?? Object.values(seg.text)[0] ?? '').trim();
  const short = text.length > 42 ? `${text.slice(0, 42)}…` : text;
  return seg.chapter ? `Capítulo: ${seg.chapter}` : `Trecho ${index + 1}${short ? ` · ${short}` : ''}`;
};

const curationLine = (c: MusicCuration | undefined): string | null => {
  if (!c) return null;
  if (!c.ok) return c.reason ?? 'sem trecho de loop';
  return `${db(c.bpm, 0)} bpm · loop de ${clock(c.loop_start_s)} (${c.loop_bars} compassos) · banda da voz ${db(c.voz_db)} dB`;
};

export const MusicTracks: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const lang = doc.primary_language;
  const client = useQueryClient();
  const { toast } = useToast();
  const library = useQuery({ queryKey: ['library-music'], queryFn: () => api.get<LibraryMusic>('/api/library/music') });
  const curation = useQuery({ queryKey: ['music-curation'], queryFn: audioApi.curation });
  const [curateJob, setCurateJob] = useState<string | null>(null);
  const [newSeg, setNewSeg] = useState('');
  const job = useAudioJob(curateJob, (j) => {
    setCurateJob(null);
    client.invalidateQueries({ queryKey: ['music-curation'] });
    if (j.status === 'done') toast({ tone: 'good', title: 'Faixas curadas', description: 'O trecho de loop de cada faixa está pronto.' });
    else toast({ tone: 'bad', title: 'A curadoria falhou', description: j.error ?? undefined });
  });

  const ops = useMutation({
    mutationFn: (v: { ops: MusicOp[]; message: string }) => api.post(`/api/projects/${doc.id}/ops`, v),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });
  const curate = useMutation({
    mutationFn: () => audioApi.curate([]),
    onSuccess: (j) => setCurateJob(j.id),
  });

  const segIndex = useMemo(() => new Map(doc.segments.map((s, i) => [s.id, i])), [doc.segments]);
  const cues = useMemo(
    () =>
      [...(doc.music as MusicCueDoc[])].sort(
        (a, b) => (a.start_segment ? (segIndex.get(a.start_segment) ?? -1) : -1) - (b.start_segment ? (segIndex.get(b.start_segment) ?? -1) : -1),
      ),
    [doc.music, segIndex],
  );
  const tracks = library.data?.tracks ?? [];
  const moods = library.data?.moods ?? Object.keys(MOOD_LABELS);
  const disabled = busy || ops.isPending;
  const send = (op: MusicOp, message: string) => ops.mutate({ ops: [op], message });
  const curating = Boolean(curateJob) || curate.isPending;
  const used = new Set(cues.filter((c) => c.enabled).map((c) => c.start_segment ?? ''));
  // faixa que começa num trecho apagado: o build a deixa fora do vídeo
  const lost = (c: MusicCueDoc) => Boolean(c.start_segment && !segIndex.has(c.start_segment));
  // a faixa de abertura: o build sempre a toca desde o começo (a mais cedo; no empate, a que vem depois na lista)
  const opening = useMemo(() => {
    let best: MusicCueDoc | null = null;
    let bestKey = Infinity;
    for (const c of doc.music as MusicCueDoc[]) {
      if (!c.enabled || (c.start_segment && !segIndex.has(c.start_segment))) continue;
      const k = c.start_segment ? (segIndex.get(c.start_segment) ?? -1) : -1;
      if (k <= bestKey) {
        best = c;
        bestKey = k;
      }
    }
    return best?.id ?? null;
  }, [doc.music, segIndex]);
  // a faixa que a última prévia usou em cada trilha (a escolhida pelo clima, quando não há faixa fixada)
  const beds = ((detail.outputs.preview?.music ?? null) as { beds?: MusicBed[] } | null)?.beds ?? [];
  const trackName = (path: string) => tracks.find((t) => t.path === path)?.name ?? path;

  return (
    <section aria-label="Faixas do vídeo" className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-fg">Faixas do vídeo</h3>
        {tracks.length ? (
          <Button size="sm" variant="ghost" icon={Sparkles} busy={curating} disabled={curating} onClick={() => curate.mutate()}>
            Curar as faixas
          </Button>
        ) : null}
      </div>
      {curating && job ? <p className="text-[13px] text-subtle">{job.message || 'Curando as faixas…'}</p> : null}
      {cues.length === 0 ? <p className="text-[13px] text-subtle">O vídeo usa a trilha do estilo.</p> : null}
      <ol className="flex flex-col gap-3">
        {cues.map((cue, i) => {
          const usedTrack = cue.track ?? beds.find((b) => b.cue_id === cue.id)?.track ?? null;
          const cur = usedTrack ? curation.data?.[usedTrack] : undefined;
          const info = curationLine(cur);
          const pending = usedTrack && !cur ? 'Esta faixa ainda não foi curada: use "Curar as faixas".' : null;
          const loopHint = cue.track
            ? (info ?? pending ?? undefined)
            : usedTrack
              ? `Na última prévia: ${trackName(usedTrack)}. ${info ?? pending ?? ''}`.trim()
              : cue.loop
                ? 'Vale para a faixa da biblioteca escolhida pelo clima. Atualize a prévia para ver qual foi.'
                : undefined;
          return (
            <li key={cue.id} className={`flex flex-col gap-2 rounded-md border border-line p-2.5 ${cue.enabled ? '' : 'opacity-60'}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-sm text-fg">
                  <Music size={14} aria-hidden="true" className="text-muted" />
                  Faixa {i + 1}
                  {cue.enabled ? null : <Badge>desligada</Badge>}
                  {cue.enabled && lost(cue) ? <Badge tone="warn">fora do vídeo</Badge> : null}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={Power}
                  disabled={disabled}
                  onClick={() => send({ op: 'set_music', cue_id: cue.id, enabled: !cue.enabled },
                    cue.enabled ? 'Faixa de música desligada' : 'Faixa de música ligada')}
                >
                  {cue.enabled ? 'Desligar' : 'Ligar'}
                </Button>
              </div>
              {cue.id === opening ? (
                <Field label="Começa em" hint="A primeira faixa sempre toca desde o começo do vídeo.">
                  <p className="text-sm text-fg">Começo do vídeo</p>
                </Field>
              ) : (
              <Field label="Começa em">
                <Select
                  disabled={disabled}
                  value={cue.start_segment ?? ''}
                  onChange={(e) => send({ op: 'set_music', cue_id: cue.id, start_segment: e.target.value },
                    'Início da faixa de música')}
                >
                  <option value="">Começo do vídeo</option>
                  {lost(cue) ? <option value={cue.start_segment ?? ''}>Trecho apagado: escolha outro</option> : null}
                  {doc.segments.map((s, k) => (
                    <option key={s.id} value={s.id}>{segmentLabel(s, k, lang)}</option>
                  ))}
                </Select>
              </Field>
              )}
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <Field label="Faixa da biblioteca">
                  <Select
                    disabled={disabled}
                    value={cue.track ?? ''}
                    onChange={(e) => send({ op: 'set_music', cue_id: cue.id, track: e.target.value },
                      e.target.value ? `Faixa fixada: ${e.target.value}` : 'Faixa pelo clima')}
                  >
                    <option value="">Pelo clima</option>
                    {tracks.map((t) => (
                      <option key={t.path} value={t.path}>{t.name}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Clima">
                  <Select
                    disabled={disabled}
                    value={cue.mood ?? ''}
                    onChange={(e) => send({ op: 'set_music', cue_id: cue.id, mood: e.target.value }, 'Clima da faixa')}
                  >
                    <option value="">O do estilo</option>
                    {moods.map((m) => (
                      <option key={m} value={m}>{label(MOOD_LABELS, m)}</option>
                    ))}
                  </Select>
                </Field>
              </div>
              <Field label="Volume (dB)" hint="Mais negativo = mais baixo. O padrão é a música de 14 a 19 dB abaixo da voz.">
                <Input
                  type="number"
                  step={1}
                  min={-60}
                  max={0}
                  disabled={disabled}
                  defaultValue={cue.volume_db ?? detail.style?.audio.music.volume_db ?? -22}
                  key={`${cue.id}-${cue.volume_db}`}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (e.target.value.trim() !== '' && Number.isFinite(v) && v !== cue.volume_db) send({ op: 'set_music', cue_id: cue.id, volume_db: v }, 'Volume da faixa');
                  }}
                />
              </Field>
              <Checkbox
                label="Só a batida, em loop no compasso"
                description={loopHint}
                checked={Boolean(cue.loop)}
                disabled={disabled}
                onChange={(on) => send({ op: 'set_music', cue_id: cue.id, loop: on }, on ? 'Trilha só na batida, em loop' : 'Trilha inteira')}
              />
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Trocar a trilha a partir de" className="min-w-0 flex-1">
          <Select disabled={disabled} value={newSeg} onChange={(e) => setNewSeg(e.target.value)}>
            <option value="">Escolha um trecho</option>
            {doc.segments.map((s, k) =>
              used.has(s.id) ? null : <option key={s.id} value={s.id}>{segmentLabel(s, k, lang)}</option>,
            )}
          </Select>
        </Field>
        <Button
          size="sm"
          icon={Plus}
          disabled={disabled || !newSeg}
          onClick={() => {
            // loop no compasso só com faixas na biblioteca (sem elas a faixa nova segue a trilha procedural)
            send({ op: 'set_music', start_segment: newSeg, ...(tracks.length > 0 ? { loop: true } : {}) },
              'Trilha nova neste segmento');
            setNewSeg('');
          }}
        >
          Trocar a trilha neste segmento
        </Button>
      </div>
      <p className="text-[13px] text-subtle">Depois de mudar as faixas, atualize a prévia.</p>
      <ErrorBox error={ops.error ?? curate.error ?? curation.error} />
    </section>
  );
};
