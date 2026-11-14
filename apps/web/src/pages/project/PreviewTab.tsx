import { Player, type PlayerRef } from '@remotion/player';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type TimelineProps, VideoTimeline } from '@vstudio/renderer';
import React, { useMemo, useRef, useState } from 'react';
import { Audio } from 'remotion';
import { api, type EditProposal, fileUrl, type LibraryMusic, MOOD_LABELS, type ProjectDetail } from '../../api';
import { Badge, Button, Card, Empty, ErrorBox, PLAYBACK_RATES, Select, Spinner, Textarea, formatSeconds } from '../../ui';
import { useStartJob } from '../ProjectPage';

type Timeline = TimelineProps & { mix: string; duration: number; version: number; narration: unknown[]; chapters: { title: string; start: number }[] };

const PreviewComposition: React.FC<TimelineProps & { mixUrl?: string }> = ({ mixUrl, ...props }) => (
  <>
    <VideoTimeline {...props} />
    {mixUrl ? <Audio src={mixUrl} /> : null}
  </>
);

const OP_LABELS: Record<string, string> = {
  update_segment_text: 'reescrever segmento',
  add_segment: 'adicionar segmento',
  delete_segment: 'remover segmento',
  move_segment: 'mover segmento',
  update_scene_prompt: 'novo prompt de imagem',
  regenerate_scene: 'outra versão da imagem',
  set_scene_motion: 'movimento de câmera',
  set_scene_transition: 'transição',
  set_scene_kind: 'tipo de cena',
  set_scene_text: 'texto na tela',
  set_scene_look: 'cor/luz da cena',
  split_scene: 'dividir cena',
  merge_scene_with_next: 'juntar cenas',
  set_style: 'ajuste de estilo',
  set_music: 'música',
  set_lock: 'travar',
  set_title: 'título',
  set_metadata: 'metadados',
  set_thumbnail: 'thumbnail',
};

const describeOp = (op: Record<string, unknown>) => {
  const label = OP_LABELS[String(op.op)] ?? String(op.op);
  const target = (op.scene_id as string) || (op.segment_id as string) || (op.path as string) || '';
  const details = Object.entries(op)
    .filter(([k, v]) => !['op', 'scene_id', 'segment_id', 'path', 'reason'].includes(k) && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(', ');
  return { label, target, details, reason: String(op.reason ?? '') };
};

const EditChat: React.FC<{ projectId: string; currentSceneId: () => string | null; lang: string; busy: boolean }> = ({ projectId, currentSceneId, lang, busy }) => {
  const client = useQueryClient();
  const [instruction, setInstruction] = useState('');
  const [onlyCurrent, setOnlyCurrent] = useState(false);
  const [proposal, setProposal] = useState<EditProposal | null>(null);
  const [log, setLog] = useState<{ instruction: string; summary: string; applied: boolean }[]>([]);
  const propose = useMutation({
    mutationFn: () => {
      const scene = onlyCurrent ? currentSceneId() : null;
      return api.post<EditProposal>(`/api/projects/${projectId}/edit/propose`, {
        instruction,
        scope_scene_ids: scene ? [scene] : null,
        lang,
      });
    },
    onSuccess: (data) => setProposal(data),
  });
  const apply = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/edit/apply`, { ops: proposal!.ops, summary: proposal!.summary || instruction, rebuild: true }),
    onSuccess: () => {
      setLog((l) => [{ instruction, summary: proposal!.summary, applied: true }, ...l]);
      setProposal(null);
      setInstruction('');
      client.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });
  const discard = () => {
    if (proposal) setLog((l) => [{ instruction, summary: proposal.summary, applied: false }, ...l]);
    setProposal(null);
  };
  return (
    <Card className="flex flex-col gap-3">
      <div>
        <div className="text-sm font-semibold">Editar por instrução</div>
        <div className="text-xs text-zinc-500">Ex.: “deixa a cena 5 mais sombria”, “legenda maior”, “outra imagem na cena atual”, “música mais baixa”.</div>
      </div>
      <Textarea
        rows={3}
        value={instruction}
        placeholder="O que você quer mudar?"
        onChange={(e) => setInstruction(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && instruction.trim()) propose.mutate();
        }}
      />
      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={onlyCurrent} onChange={(e) => setOnlyCurrent(e.target.checked)} />
        Só a cena que está na tela agora
      </label>
      <Button variant="primary" busy={propose.isPending} disabled={!instruction.trim() || busy} onClick={() => propose.mutate()}>
        Propor mudança
      </Button>
      <ErrorBox error={propose.error ?? apply.error} />
      {proposal ? (
        <div className="flex flex-col gap-2 rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-sm">
          <div className="text-zinc-200">{proposal.summary || 'Nenhuma mudança proposta.'}</div>
          {proposal.ops.map((op, i) => {
            const d = describeOp(op);
            return (
              <div key={i} className="text-xs text-zinc-400">
                <Badge tone="info">{d.label}</Badge> <span className="font-mono text-zinc-500">{d.target}</span> {d.details}
                {d.reason ? <div className="pl-1 text-zinc-500">↳ {d.reason}</div> : null}
              </div>
            );
          })}
          {proposal.estimate ? (
            <div className="text-xs text-zinc-400">
              Vai gerar: {proposal.estimate.images_missing.length} imagem(ns) nova(s), {proposal.estimate.tts_chars} caracteres de narração ·
              custo estimado US$ {proposal.estimate.estimated_cost_usd.toFixed(3)} · ~{formatSeconds(proposal.estimate.estimated_seconds)}
            </div>
          ) : null}
          <div className="text-[11px] text-zinc-600">via {proposal.provider}</div>
          <div className="flex gap-2">
            <Button variant="primary" disabled={!proposal.ops.length} busy={apply.isPending} onClick={() => apply.mutate()}>
              Aplicar e atualizar prévia
            </Button>
            <Button variant="ghost" onClick={discard}>
              Descartar
            </Button>
          </div>
        </div>
      ) : null}
      {log.length ? (
        <div className="flex flex-col gap-1 text-xs">
          {log.slice(0, 8).map((entry, i) => (
            <div key={i} className="text-zinc-500">
              {entry.applied ? '✓' : '✗'} {entry.instruction} <span className="text-zinc-600">— {entry.summary}</span>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
};

const MusicCard: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const library = useQuery({ queryKey: ['library-music'], queryFn: () => api.get<LibraryMusic>('/api/library/music') });
  const cue = doc.music.find((c) => c.enabled) ?? doc.music[0];
  const used = detail.outputs.preview?.music;
  const source = detail.style?.audio.music.source;
  const pin = useMutation({
    mutationFn: (track: string) =>
      api.post(`/api/projects/${doc.id}/ops`, { ops: [{ op: 'set_music', track }], message: track ? `Trilha fixada: ${track}` : 'Trilha automática' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });
  const warnings = detail.outputs.preview?.warnings ?? [];
  return (
    <Card className="flex flex-col gap-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">Trilha</span>
        {cue && !cue.enabled ? <Badge>sem música</Badge> : null}
        {used ? (
          <Badge tone="info">
            {used.source === 'library' ? `biblioteca: ${used.track}` : `procedural (${MOOD_LABELS[used.mood ?? ''] ?? used.mood ?? '—'})`}
          </Badge>
        ) : null}
        {source ? <span className="text-zinc-500">fonte do estilo: {source === 'library' ? 'biblioteca' : source === 'none' ? 'nenhuma' : 'procedural'}</span> : null}
      </div>
      {library.data?.tracks.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-zinc-500">Faixa</span>
          <Select className="min-w-0 flex-1 text-xs" disabled={busy || pin.isPending} value={cue?.track ?? ''} onChange={(e) => pin.mutate(e.target.value)}>
            <option value="">{source === 'library' ? 'automática (pelo clima)' : 'automática (procedural, conforme o estilo)'}</option>
            {library.data.tracks.map((t) => (
              <option key={t.path} value={t.path}>{t.path} · {MOOD_LABELS[t.mood] ?? t.mood}</option>
            ))}
          </Select>
        </div>
      ) : (
        <div className="text-zinc-500">Para usar suas músicas, coloque faixas na biblioteca (Configurações → Biblioteca de músicas).</div>
      )}
      {warnings.map((w) => (
        <div key={w} className="text-amber-300/90">⚠ {w}</div>
      ))}
      <ErrorBox error={pin.error} />
    </Card>
  );
};

export const PreviewTab: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const startJob = useStartJob(doc.id);
  const [lang, setLang] = useState(doc.primary_language);
  const playerRef = useRef<PlayerRef>(null);
  const previewEntry = detail.outputs.preview?.langs?.[lang];
  const timeline = useQuery({
    queryKey: ['timeline', doc.id, lang, previewEntry?.timeline, detail.outputs.preview?.cost_usd, detail.project.current_version],
    queryFn: () => api.get<Timeline>(`/api/projects/${doc.id}/timeline?lang=${lang}`),
    retry: false,
  });
  const origin = window.location.origin;
  const inputProps = useMemo(() => {
    if (!timeline.data) return null;
    return {
      ...timeline.data,
      isChunk: false,
      assetBaseUrl: `${origin}/files/`,
      fontBaseUrl: origin,
      mixUrl: timeline.data.mix ? `${origin}${fileUrl(timeline.data.mix)}` : undefined,
    };
  }, [timeline.data, origin]);
  const currentSceneId = () => {
    const frame = playerRef.current?.getCurrentFrame() ?? 0;
    const scene = timeline.data?.scenes.find((s) => frame >= s.from && frame < s.from + s.durationInFrames);
    return scene?.id ?? null;
  };
  const stale = timeline.data && timeline.data.version !== detail.project.current_version;
  const vertical = (timeline.data?.height ?? 1920) > (timeline.data?.width ?? 1080);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Select value={lang} onChange={(e) => setLang(e.target.value)}>
            {doc.languages.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </Select>
          <Button variant="primary" disabled={busy || !doc.scenes.length} busy={startJob.isPending} onClick={() => startJob.mutate({ kind: 'preview' })}>
            {timeline.data ? 'Atualizar prévia' : 'Gerar prévia'}
          </Button>
          {busy ? <span className="flex items-center gap-2 text-xs text-zinc-400"><Spinner /> processando…</span> : null}
          {stale ? <Badge tone="warn">a prévia é da v{timeline.data!.version}; há mudanças novas</Badge> : null}
          {timeline.data ? <Badge>{formatSeconds(timeline.data.duration)} · {timeline.data.scenes.length} cenas</Badge> : null}
          {detail.outputs.preview?.preview_only_providers?.length ? (
            <Badge tone="warn">prévia com provedores de teste: {detail.outputs.preview.preview_only_providers.join(', ')}</Badge>
          ) : null}
        </div>
        {inputProps ? (
          <div className="flex justify-center rounded-xl border border-zinc-800 bg-black p-2">
            <Player
              ref={playerRef}
              component={PreviewComposition}
              inputProps={inputProps}
              durationInFrames={Math.max(1, inputProps.durationInFrames)}
              fps={inputProps.fps}
              compositionWidth={inputProps.width}
              compositionHeight={inputProps.height}
              controls
              showPlaybackRateControl={PLAYBACK_RATES}
              acknowledgeRemotionLicense
              style={vertical ? { height: '72vh', aspectRatio: `${inputProps.width} / ${inputProps.height}` } : { width: '100%', aspectRatio: `${inputProps.width} / ${inputProps.height}` }}
            />
          </div>
        ) : (
          <Empty>
            {timeline.isLoading ? 'Carregando…' : 'Gere a prévia para assistir ao vídeo aqui. A prévia não renderiza o MP4: é rápida e mostra as mudanças na hora.'}
          </Empty>
        )}
        {timeline.data?.chapters?.length ? (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {timeline.data.chapters.map((c: { title: string; start: number }) => (
              <button key={c.start} className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-400 hover:text-zinc-100" onClick={() => playerRef.current?.seekTo(Math.round(c.start * inputProps!.fps))}>
                {formatSeconds(c.start)} {c.title}
              </button>
            ))}
          </div>
        ) : null}
        <MusicCard detail={detail} busy={busy} />
        <ErrorBox error={startJob.error} />
      </div>
      <EditChat projectId={doc.id} currentSceneId={currentSceneId} lang={lang} busy={busy} />
    </div>
  );
};
