/* Peças comuns das abas do radar: miniatura, barrinhas das views recentes e "Virar ideia". */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Lightbulb } from 'lucide-react';
import React, { useState } from 'react';
import type { Channel } from '../../api';
import { radarApi, type RadarPanelChannel } from '../../apiRadar';
import { Button, Select, useToast } from '../../ui';
import { fmtNum, thumbUrl } from './format';

/** Miniatura do i.ytimg.com que some quando não carrega. */
export const Thumb: React.FC<{ id: string | null | undefined; alt?: string; className?: string; size?: 'mq' | 'hq' }> = ({
  id,
  alt = '',
  className = '',
  size = 'mq',
}) => (
  <div className={`aspect-video overflow-hidden rounded-md bg-raised ${className}`}>
    {id ? (
      <img
        loading="lazy"
        src={thumbUrl(id, size)}
        alt={alt}
        className="h-full w-full object-cover"
        onError={(e) => {
          e.currentTarget.style.visibility = 'hidden';
        }}
      />
    ) : null}
  </div>
);

/** Barrinhas das views dos últimos vídeos (antigo → novo): verde acima do hit, cinza-escuro com menos de 7 dias. */
export const RecentBars: React.FC<{ channel: RadarPanelChannel; hit: number; width?: number; height?: number }> = ({
  channel,
  hit,
  width = 120,
  height = 34,
}) => {
  const list = (channel.latest ?? []).filter((x) => x.v != null).slice(0, 20).reverse();
  if (!list.length) return null;
  const lo = 3;
  const hi = Math.max(6, Math.log10(Math.max(...list.map((x) => x.v ?? 0))));
  const bw = width / list.length;
  const y = (v: number) => height - ((Math.max(lo, Math.log10(Math.max(v, 1))) - lo) / (hi - lo)) * (height - 2);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Views dos últimos vídeos">
      {list.map((x, i) => {
        const v = x.v ?? 0;
        const top = y(v);
        const fresh = x.a < 7;
        return (
          <rect
            key={x.id}
            x={i * bw + 1}
            y={top}
            width={Math.max(1.5, bw - 2)}
            height={height - top}
            rx={1}
            className={fresh ? 'fill-zinc-700' : v >= hit ? 'fill-emerald-400' : 'fill-zinc-500'}
          >
            <title>{`${x.t}: ${fmtNum(v)} views${fresh ? ' (menos de 7 dias)' : ''}`}</title>
          </rect>
        );
      })}
      <line x1={0} x2={width} y1={y(hit)} y2={y(hit)} className="stroke-amber-300" strokeDasharray="2 3" opacity={0.7} />
    </svg>
  );
};

/** "Virar ideia": escolhe o canal do Vulto e cria a ideia (vídeo fora da curva ou canal para modelar). */
export const IdeaButton: React.FC<{
  runId: number;
  channels: Channel[];
  youtubeId?: string;
  channelYoutubeId?: string;
}> = ({ runId, channels, youtubeId, channelYoutubeId }) => {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState('');
  const { toast } = useToast();
  const qc = useQueryClient();
  const make = useMutation({
    mutationFn: () =>
      radarApi.createIdea({ run_id: runId, youtube_id: youtubeId, channel_youtube_id: channelYoutubeId, channel_id: target || null }),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ['ideas'] });
      setOpen(false);
      toast({
        tone: out.created ? 'good' : 'info',
        title: out.created ? 'Ideia criada' : 'Essa ideia já existe',
        description: out.idea.title,
        action: { label: 'Ver ideias', to: '/ideias' },
      });
    },
    onError: (e) => toast({ tone: 'bad', title: 'Não deu para criar a ideia', description: e instanceof Error ? e.message : String(e) }),
  });
  if (!open)
    return (
      <Button size="sm" variant="ghost" icon={Lightbulb} onClick={() => setOpen(true)}>
        Virar ideia
      </Button>
    );
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select aria-label="Canal da ideia" value={target} onChange={(e) => setTarget(e.target.value)} className="min-w-0 max-w-[12rem] text-xs">
        <option value="">Sem canal</option>
        {channels.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </Select>
      <Button size="sm" variant="primary" busy={make.isPending} onClick={() => make.mutate()}>
        Criar
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
        Cancelar
      </Button>
    </div>
  );
};
