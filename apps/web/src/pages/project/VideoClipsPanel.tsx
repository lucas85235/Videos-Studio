// Vídeo IA nas cenas-chave (aba Cenas): o painel do projeto (cenas marcadas, custo, gerar com confirmação) e o
// controle de cada cartão de cena (marcar, movimento, duração). Vídeo IA é pago: nada é gerado sem confirmar o custo,
// e a prévia só usa os clipes que já existem.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clapperboard, Film, Sparkles, X } from 'lucide-react';
import React, { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Job, type Scene, type VideoStatus } from '../../api';
import { Badge, Button, Card, ErrorBox, Select, Skeleton, Textarea, useConfirm } from '../../ui';
import { sceneChanges } from './core/drafts';

/** Durações oferecidas (o Kling 2.5 aceita 5 ou 10 s; o backend arredonda para o que o modelo aceita). */
const SECONDS = [5, 10];

export const videoStatusKey = (projectId: string) => ['project', projectId, 'video'] as const;

const usd = (value: number) => `US$ ${value.toFixed(2).replace('.', ',')}`;

const STATUS_BADGE: Record<string, { tone: 'good' | 'warn' | 'bad'; text: string }> = {
  ready: { tone: 'good', text: 'Clipe pronto' },
  missing: { tone: 'warn', text: 'Falta gerar' },
  blocked: { tone: 'bad', text: 'Não dá para animar' },
};

const useVideoStatus = (projectId: string, enabled = true) =>
  useQuery({
    queryKey: videoStatusKey(projectId),
    queryFn: () => api.get<VideoStatus>(`/api/projects/${projectId}/video`),
    enabled,
    // Enquanto o job roda, acompanha o progresso.
    refetchInterval: (q) => (q.state.data?.job ? 4000 : false),
  });

/** Painel do projeto: o que está marcado, o custo do que falta e o botão de gerar (com confirmação do valor). */
export const VideoClipsPanel: React.FC<{ projectId: string; disabled?: boolean; onShowScene: (sceneId: string) => void }> = ({
  projectId,
  disabled,
  onShowScene,
}) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const status = useVideoStatus(projectId);
  const refresh = () => client.invalidateQueries({ queryKey: ['project', projectId] });
  const suggest = useMutation({
    mutationFn: () => api.post<{ scene_ids: string[] }>(`/api/projects/${projectId}/video/suggest`, { apply: true }),
    onSuccess: (r) => {
      for (const id of r.scene_ids) sceneChanges.set(projectId, id, Date.now());
      return refresh();
    },
  });
  const generate = useMutation({
    mutationFn: (cost: number) => api.post<{ job: Job }>(`/api/projects/${projectId}/video/generate`, { confirm_cost_usd: cost }),
    onSuccess: refresh,
  });

  const data = status.data;
  if (status.isLoading) return <Skeleton className="h-24 rounded-xl" />;
  if (status.error) return <ErrorBox error={status.error} title="Não foi possível ver o vídeo IA" onRetry={() => void status.refetch()} />;
  if (!data || data.max_scenes <= 0) return null;
  const marked = data.scenes;
  // Sem nada marcado e sem sugestão, o painel fica fora (o controle de cada cena continua no cartão).
  if (!marked.length && !data.suggestions.length) return null;

  const job = data.job;
  const askGenerate = async () => {
    const worst = data.max_cost_usd;
    const ok = await confirm({
      title: `Gerar ${data.missing === 1 ? '1 clipe' : `${data.missing} clipes`} de vídeo IA?`,
      description: (
        <>
          Custo estimado: <strong>{usd(data.estimated_cost_usd)}</strong> no {data.provider_name}
          {worst > data.estimated_cost_usd + 0.004 ? `, até ${usd(worst)} se ele falhar e o próximo modelo assumir` : ''}. É cobrado na
          sua conta do fal.ai e não passa desse valor. A imagem de cada cena é gerada antes, se ainda não existir.
        </>
      ),
      confirmLabel: `Gerar (até ${usd(worst)})`,
      tone: 'primary',
    });
    if (ok) generate.mutate(worst);
  };

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Clapperboard size={16} aria-hidden="true" className="text-muted" />
        <h2 className="font-display text-[15px] font-semibold text-fg">Vídeo IA nas cenas-chave</h2>
        <Badge tone={marked.length >= data.max_scenes ? 'warn' : 'neutral'} title="Cenas marcadas e o limite do estilo">
          {marked.length}/{data.max_scenes}
        </Badge>
      </div>
      <p className="text-[13px] text-muted">
        A imagem da cena vira o primeiro quadro de um clipe curto. É pago por segundo, então fica para poucos momentos. A prévia e o
        vídeo final só usam os clipes já gerados; sem clipe, a cena mostra a imagem com movimento de câmera.
      </p>
      {marked.length ? (
        <ul className="flex flex-col gap-1.5">
          {marked.map((item) => {
            const badge = STATUS_BADGE[item.status];
            return (
              <li key={item.scene_id} className="flex flex-wrap items-center gap-2 text-[13px]">
                <button type="button" className="font-medium text-fg underline-offset-2 hover:underline" onClick={() => onShowScene(item.scene_id)}>
                  {item.label}
                </button>
                <Badge tone={badge.tone}>{badge.text}</Badge>
                <span className="text-subtle">
                  {item.clip_seconds ?? item.seconds} s{item.cost_usd ? ` · ${usd(item.cost_usd)}` : ''}
                </span>
                {item.detail ? <span className="basis-full text-xs text-subtle">{item.detail}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-[13px] text-fg">Nenhuma cena marcada.</p>
      )}
      {!data.available && data.missing > 0 ? (
        <p className="text-[13px] text-amber-200">
          Nenhum gerador de vídeo IA disponível. Cole a chave do fal.ai em{' '}
          <Link to="/configuracoes#provedores-video" className="underline">
            Configurações
          </Link>
          .
        </p>
      ) : null}
      {job ? (
        <p className="text-[13px] text-fg" role="status">
          {job.message || 'Gerando os clipes…'} ({Math.round(job.progress * 100)}%)
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {data.suggestions.length && marked.length < data.max_scenes ? (
          <Button size="sm" icon={Sparkles} busy={suggest.isPending} disabled={disabled} onClick={() => suggest.mutate()}>
            Marcar as sugeridas ({data.suggestions.length})
          </Button>
        ) : null}
        {data.missing > 0 && data.available && !job ? (
          <Button size="sm" variant="primary" icon={Film} busy={generate.isPending} disabled={disabled} onClick={() => void askGenerate()}>
            Gerar {data.missing === 1 ? '1 clipe' : `${data.missing} clipes`} · {usd(data.estimated_cost_usd)}
          </Button>
        ) : null}
      </div>
      <ErrorBox error={suggest.error} title="Não foi possível marcar as sugeridas" onClose={() => suggest.reset()} />
      <ErrorBox error={generate.error} title="Não foi possível gerar os clipes" onClose={() => generate.reset()} />
    </Card>
  );
};

/** Controle do cartão da cena: marcar como cena-chave, movimento e duração do clipe. */
export const SceneVideoControl: React.FC<{ projectId: string; scene: Scene; index: number; disabled?: boolean; hasMedia: boolean }> = ({
  projectId,
  scene,
  index,
  disabled,
  hasMedia,
}) => {
  const client = useQueryClient();
  const promptId = useId();
  const marked = scene.kind === 'video';
  const saved = scene.clip?.prompt ?? '';
  const [prompt, setPrompt] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (body: { enabled: boolean; prompt?: string; seconds?: number }) =>
      api.put(`/api/projects/${projectId}/scenes/${scene.id}/video`, body),
    onSuccess: async () => {
      sceneChanges.set(projectId, scene.id, Date.now());
      await client.invalidateQueries({ queryKey: ['project', projectId] });
      setPrompt(null);
    },
  });
  if (!(scene.kind === 'image' || marked) || (hasMedia && !marked)) return null;
  const off = disabled || run.isPending;
  if (!marked) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-subtle">
        {scene.video_hint ? (
          <span className="min-w-0 flex-1" title={scene.video_hint}>
            Sugestão: vídeo IA ({scene.video_hint})
          </span>
        ) : null}
        <Button size="sm" variant="ghost" icon={Film} disabled={off} busy={run.isPending} onClick={() => run.mutate({ enabled: true })}>
          Animar com vídeo IA
        </Button>
        <ErrorBox error={run.error} title={`Não foi possível marcar a Cena ${index}`} onClose={() => run.reset()} className="basis-full" />
      </div>
    );
  }
  const value = prompt ?? saved;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-accent/30 bg-accent/5 p-2.5">
      <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
        <Film size={14} aria-hidden="true" className="text-accent" /> Vídeo IA
        <Button size="sm" variant="ghost" icon={X} className="ml-auto" disabled={off} onClick={() => run.mutate({ enabled: false })}>
          Tirar
        </Button>
      </div>
      <label htmlFor={promptId} className="text-xs text-muted">
        Movimento (em inglês; vazio usa a sugestão ou um avanço lento da câmera)
      </label>
      <Textarea
        id={promptId}
        rows={2}
        maxLength={600}
        value={value}
        disabled={off}
        placeholder={scene.video_hint || 'slow push-in, smoke drifting'}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Duração do clipe"
          value={String(scene.clip?.seconds ?? 5)}
          disabled={off}
          onChange={(e) => run.mutate({ enabled: true, seconds: Number(e.target.value) })}
        >
          {SECONDS.map((s) => (
            <option key={s} value={s}>
              {s} s
            </option>
          ))}
        </Select>
        {prompt !== null && prompt !== saved ? (
          <Button size="sm" disabled={off} busy={run.isPending} onClick={() => run.mutate({ enabled: true, prompt })}>
            Salvar movimento
          </Button>
        ) : null}
      </div>
      <ErrorBox error={run.error} title={`Não foi possível mudar o vídeo IA da Cena ${index}`} onClose={() => run.reset()} />
    </div>
  );
};
