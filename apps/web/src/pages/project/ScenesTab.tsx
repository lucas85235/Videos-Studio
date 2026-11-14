import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, fileUrl, type ProjectDetail, type Scene } from '../../api';
import { Badge, Button, Card, Empty, ErrorBox, Select, Textarea } from '../../ui';
import { useStartJob } from '../ProjectPage';

const MOTIONS = ['', 'ken_burns_in', 'ken_burns_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down', 'punch_zoom', 'drift', 'static'];
const TRANSITIONS = ['', 'cut', 'flash', 'fade_black', 'glitch', 'light_leak', 'zoom_blur', 'film_burn', 'static_burst'];
const KINDS = ['image', 'text', 'title', 'count', 'document', 'quote'];
const KIND_LABEL: Record<string, string> = {
  image: 'imagem',
  text: 'tipografia cinética',
  title: 'título',
  count: 'contagem',
  document: 'documento',
  quote: 'citação',
};

type TimelineLite = { scenes: { id: string; src: string | null; durationInFrames: number }[]; fps: number };

const SceneCard: React.FC<{ scene: Scene; index: number; src: string | null; seconds: number | null; projectId: string }> = ({
  scene,
  index,
  src,
  seconds,
  projectId,
}) => {
  const client = useQueryClient();
  const [prompt, setPrompt] = useState(scene.visual.prompt);
  const run = useMutation({
    mutationFn: (ops: Record<string, unknown>[]) => api.post(`/api/projects/${projectId}/ops`, { ops, message: `Cena C${index}` }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', projectId] }),
  });
  const disabled = scene.locked || run.isPending;
  return (
    <Card className="flex flex-col gap-2 p-3">
      <div className="relative aspect-video overflow-hidden rounded-lg bg-zinc-950">
        {src ? <img src={fileUrl(src)} className="h-full w-full object-cover" loading="lazy" /> : <div className="flex h-full items-center justify-center text-xs text-zinc-600">sem imagem ainda</div>}
        <div className="absolute left-2 top-2 flex gap-1">
          <Badge>C{index}</Badge>
          {seconds !== null ? <Badge>{seconds.toFixed(1)} s</Badge> : null}
          {scene.visual.variation ? <Badge tone="info">var {scene.visual.variation}</Badge> : null}
        </div>
        {scene.locked ? <div className="absolute right-2 top-2"><Badge tone="warn">travada</Badge></div> : null}
      </div>
      <Textarea rows={3} value={prompt} disabled={disabled} onChange={(e) => setPrompt(e.target.value)} className="text-xs" />
      <div className="grid grid-cols-3 gap-1.5">
        <Select value={scene.kind} disabled={disabled} onChange={(e) => run.mutate([{ op: 'set_scene_kind', scene_id: scene.id, kind: e.target.value }])} className="text-xs">
          {KINDS.map((k) => (
            <option key={k} value={k}>{KIND_LABEL[k]}</option>
          ))}
        </Select>
        <Select value={scene.motion ?? ''} disabled={disabled} onChange={(e) => run.mutate([{ op: 'set_scene_motion', scene_id: scene.id, motion: e.target.value || null }])} className="text-xs">
          {MOTIONS.map((m) => (
            <option key={m} value={m}>{m || 'câmera auto'}</option>
          ))}
        </Select>
        <Select
          value={scene.transition_in ?? ''}
          disabled={disabled}
          onChange={(e) => run.mutate([{ op: 'set_scene_transition', scene_id: scene.id, transition: e.target.value || null }])}
          className="text-xs"
        >
          {TRANSITIONS.map((t) => (
            <option key={t} value={t}>{t || 'transição auto'}</option>
          ))}
        </Select>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button className="text-xs" disabled={disabled || prompt === scene.visual.prompt} onClick={() => run.mutate([{ op: 'update_scene_prompt', scene_id: scene.id, prompt }])}>
          Salvar prompt
        </Button>
        <Button className="text-xs" disabled={disabled} onClick={() => run.mutate([{ op: 'regenerate_scene', scene_id: scene.id }])}>
          Outra imagem
        </Button>
        <Button className="text-xs" variant="ghost" disabled={run.isPending} onClick={() => run.mutate([{ op: 'set_lock', ids: [scene.id], locked: !scene.locked }])}>
          {scene.locked ? 'Destravar' : 'Travar'}
        </Button>
      </div>
      <ErrorBox error={run.error} />
    </Card>
  );
};

export const ScenesTab: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const startJob = useStartJob(doc.id);
  const timeline = useQuery({
    queryKey: ['timeline', doc.id, doc.primary_language, detail.outputs.preview?.langs?.[doc.primary_language]?.timeline],
    queryFn: () => api.get<TimelineLite>(`/api/projects/${doc.id}/timeline?lang=${doc.primary_language}`),
    retry: false,
  });
  if (!doc.scenes.length) {
    return (
      <Empty>
        <div className="mb-3">Sem cenas ainda. O storyboard divide o roteiro em cenas e escreve o prompt de cada imagem.</div>
        <Button variant="primary" disabled={busy || !doc.segments.length} onClick={() => startJob.mutate({ kind: 'storyboard' })}>
          Gerar storyboard
        </Button>
      </Empty>
    );
  }
  const byId = new Map((timeline.data?.scenes ?? []).map((s) => [s.id, s]));
  const fps = timeline.data?.fps ?? 30;
  let counter = 0;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
        <span>Mudanças aqui só regeneram a cena alterada. Depois clique em “Gerar prévia”.</span>
        {!timeline.data ? <Badge tone="warn">gere a prévia para ver as imagens</Badge> : null}
      </div>
      {doc.segments.map((seg, sIndex) => (
        <div key={seg.id}>
          <div className="mb-2 text-xs text-zinc-500">
            <span className="font-mono">S{sIndex + 1}</span> · {(seg.text[doc.primary_language] ?? '').slice(0, 140)}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {doc.scenes
              .filter((s) => s.segment_id === seg.id)
              .sort((a, b) => a.part - b.part)
              .map((scene) => {
                counter += 1;
                const t = byId.get(scene.id);
                return (
                  <SceneCard
                    key={`${scene.id}-${detail.project.current_version}`}
                    scene={scene}
                    index={counter}
                    src={t?.src ?? null}
                    seconds={t ? t.durationInFrames / fps : null}
                    projectId={doc.id}
                  />
                );
              })}
          </div>
        </div>
      ))}
    </div>
  );
};
