import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, type LibraryMusic, MOOD_LABELS, type Provider } from '../api';
import { Badge, Button, Card, ErrorBox, Input, formatBytes } from '../ui';

type Status = { data_dir: string; offline: boolean; renderer_version: string; video_encoder: string; nvenc: boolean; providers_available: Record<string, string[]> };
type Costs = { by_provider: { provider: string; kind: string; units: number; cost_usd: number; calls: number }[]; total_usd: number };

const KIND_LABEL: Record<string, string> = {
  llm: 'Roteiro e agente (LLM)',
  tts: 'Voz',
  image: 'Imagens',
  align: 'Alinhamento de palavras',
  publish: 'Publicação',
};

const ProviderCard: React.FC<{ p: Provider }> = ({ p }) => {
  const client = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => api.put<{ available: boolean; reason: string }>(`/api/providers/${p.id}/credentials`, { fields: values }),
    onSuccess: () => {
      setValues({});
      client.invalidateQueries({ queryKey: ['providers'] });
      client.invalidateQueries({ queryKey: ['status'] });
    },
  });
  const clear = useMutation({
    mutationFn: () => api.del(`/api/providers/${p.id}/credentials`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['providers'] }),
  });
  return (
    <Card className="flex flex-col gap-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold">{p.name}</span>
        {p.available ? <Badge tone="good">disponível</Badge> : <Badge>indisponível</Badge>}
        {p.free ? <Badge tone="info">grátis</Badge> : null}
        {p.local ? <Badge>local</Badge> : null}
        {!p.commercial_use ? <Badge tone="warn">só prévia</Badge> : null}
        {p.quota_exhausted_today ? <Badge tone="bad">cota esgotada hoje</Badge> : null}
      </div>
      {!p.available ? <div className="text-xs text-zinc-500">{p.reason}</div> : null}
      {p.notes ? <div className="text-xs text-zinc-400">{p.notes}</div> : null}
      {p.cost_note ? <div className="text-xs text-zinc-500">💲 {p.cost_note}</div> : null}
      {p.credential_fields.length ? (
        <div className="flex flex-col gap-1.5">
          {p.credentials?.from_env ? <div className="text-xs text-emerald-400">Usando a variável de ambiente {p.credentials.from_env}</div> : null}
          {p.credential_fields.map((f) => (
            <div key={f} className="flex items-center gap-2">
              <span className="w-20 text-xs text-zinc-500">{f}</span>
              <Input
                type="password"
                className="flex-1 text-xs"
                placeholder={p.credentials?.fields[f] || 'não configurado'}
                value={values[f] ?? ''}
                onChange={(e) => setValues({ ...values, [f]: e.target.value })}
              />
            </div>
          ))}
          <div className="flex gap-2">
            <Button className="text-xs" variant="primary" disabled={!Object.values(values).some(Boolean)} busy={save.isPending} onClick={() => save.mutate()}>Salvar chave</Button>
            {p.credentials?.configured && !p.credentials.from_env ? (
              <Button className="text-xs" variant="ghost" onClick={() => clear.mutate()}>Remover</Button>
            ) : null}
          </div>
          <ErrorBox error={save.error} />
        </div>
      ) : null}
    </Card>
  );
};

const MusicLibraryCard: React.FC = () => {
  const library = useQuery({ queryKey: ['library-music'], queryFn: () => api.get<LibraryMusic>('/api/library/music') });
  const open = useMutation({ mutationFn: () => api.post('/api/library/music/open') });
  const data = library.data;
  return (
    <Card className="mb-6 flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Biblioteca de músicas</span>
        <Badge tone={data?.tracks.length ? 'good' : 'neutral'}>{data?.tracks.length ?? 0} faixas</Badge>
        <div className="ml-auto flex gap-2">
          <Button className="text-xs" onClick={() => library.refetch()} busy={library.isFetching}>Atualizar</Button>
          <Button className="text-xs" onClick={() => open.mutate()} busy={open.isPending}>Abrir pasta</Button>
        </div>
      </div>
      <p className="text-xs text-zinc-400">
        Coloque arquivos de áudio (mp3, wav, m4a, ogg, flac) em <span className="break-all font-mono text-zinc-300">{data?.dir ?? '…'}</span>, de preferência em subpastas
        pelo clima: <i>terror</i>, <i>misterio</i>, <i>tensao</i>, <i>epico</i>, <i>calmo</i>, <i>pulso</i> (o resto conta como ambiente). Depois escolha
        <b> Fonte da trilha = biblioteca</b> no estilo. A faixa é repetida ou cortada para caber no vídeo, com fade e volume ajustados.
      </p>
      <p className="text-xs text-zinc-500">
        Fonte grátis: a Biblioteca de áudio do YouTube Studio (filtre por “Atribuição não necessária”). Use só faixas com licença para o seu uso; música de terceiros sem licença gera reivindicação de Content ID.
      </p>
      {data?.tracks.length ? (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {data.moods.filter((m) => data.counts[m]).map((m) => (
            <Badge key={m}>{MOOD_LABELS[m] ?? m}: {data.counts[m]}</Badge>
          ))}
        </div>
      ) : null}
      <ErrorBox error={library.error ?? open.error} />
    </Card>
  );
};

type CacheReport = {
  dry_run: boolean;
  sizes: Record<string, number>;
  total: number;
  removed: Record<string, number>;
  freed: Record<string, number>;
  freed_total: number;
  kept_in_use: number;
  labels: Record<string, string>;
};

const KIND_SIZE_LABEL: Record<string, string> = {
  tts: 'narrações',
  image: 'imagens',
  align: 'alinhamentos',
  music: 'trilhas',
  sfx: 'efeitos sonoros',
  mix: 'mixagens',
  chunk: 'blocos de render',
  final: 'vídeos finais',
  thumbnail: 'thumbnails',
  bundles: 'renderer',
  tmp: 'temporários',
};

const StorageCard: React.FC = () => {
  const client = useQueryClient();
  const report = useQuery({ queryKey: ['cache-report'], queryFn: () => api.get<CacheReport>('/api/maintenance/cache'), staleTime: 60_000 });
  const clean = useMutation({
    mutationFn: () => api.post<CacheReport>('/api/maintenance/cache/clean'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['cache-report'] }),
  });
  const data = report.data;
  const sizes = Object.entries(data?.sizes ?? {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  return (
    <Card className="mb-6 flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Armazenamento</span>
        {data ? <Badge>{formatBytes(data.total)} no cache</Badge> : null}
        {data?.freed_total ? <Badge tone="info">{formatBytes(data.freed_total)} podem ser liberados</Badge> : null}
        <div className="ml-auto flex gap-2">
          <Button className="text-xs" onClick={() => report.refetch()} busy={report.isFetching}>Recalcular</Button>
          <Button className="text-xs" variant="primary" disabled={!data?.freed_total} busy={clean.isPending} onClick={() => clean.mutate()}>Limpar agora</Button>
        </div>
      </div>
      <p className="text-xs text-zinc-400">
        A limpeza apaga blocos de render, vídeos finais e mixagens que nenhum projeto usa mais, falhas, sobras e versões antigas do renderer. Narrações e imagens
        nunca são apagadas (custam cota para refazer), e nada criado nas últimas 24 h é tocado. Os vídeos prontos em <i>outputs</i> ficam.
      </p>
      {sizes.length ? (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {sizes.map(([k, v]) => (
            <Badge key={k}>{KIND_SIZE_LABEL[k] ?? k}: {formatBytes(v)}</Badge>
          ))}
        </div>
      ) : null}
      {data && Object.keys(data.freed).length ? (
        <div className="grid gap-0.5 text-xs text-zinc-500">
          {Object.entries(data.freed).map(([k, v]) => (
            <div key={k}>{data.labels[k] ?? k}: {data.removed[k]} itens · {formatBytes(v)}</div>
          ))}
        </div>
      ) : null}
      {clean.data ? <div className="text-xs text-emerald-400">Liberados {formatBytes(clean.data.freed_total)}.</div> : null}
      <ErrorBox error={report.error ?? clean.error} />
    </Card>
  );
};

export const SettingsPage: React.FC = () => {
  const providers = useQuery({ queryKey: ['providers'], queryFn: () => api.get<Provider[]>('/api/providers') });
  const status = useQuery({ queryKey: ['status'], queryFn: () => api.get<Status>('/api/status') });
  const costs = useQuery({ queryKey: ['costs'], queryFn: () => api.get<Costs>('/api/costs') });
  const kinds = ['llm', 'tts', 'image', 'align', 'publish'];
  return (
    <div className="mx-auto max-w-6xl p-6">
      <h1 className="mb-1 text-xl font-bold">Configurações</h1>
      <p className="mb-6 text-sm text-zinc-500">
        As chaves ficam criptografadas no seu computador. Sem nenhuma chave, o app funciona em modo de prévia (voz do Windows, imagens procedurais, roteiro de exemplo). Para conteúdo real a custo zero,
        configure a chave grátis do <b>Gemini</b> (Google AI Studio): ela libera roteiro, agente de edição e voz.
      </p>
      {status.data ? (
        <Card className="mb-6 grid gap-2 text-sm md:grid-cols-4">
          <div><div className="text-xs text-zinc-500">Dados</div><div className="break-all text-xs">{status.data.data_dir}</div></div>
          <div><div className="text-xs text-zinc-500">Codificador de vídeo</div>{status.data.video_encoder} {status.data.nvenc ? <Badge tone="good">NVENC</Badge> : null}</div>
          <div><div className="text-xs text-zinc-500">Renderer</div><span className="font-mono text-xs">{status.data.renderer_version}</span></div>
          <div><div className="text-xs text-zinc-500">Gasto registrado</div>US$ {costs.data?.total_usd.toFixed(3) ?? '0.000'}</div>
        </Card>
      ) : null}
      <MusicLibraryCard />
      <StorageCard />
      {kinds.map((kind) => (
        <div key={kind} className="mb-6">
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">{KIND_LABEL[kind]}</h2>
            <span className="text-xs text-zinc-600">a ordem abaixo é a ordem de fallback padrão</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(providers.data ?? []).filter((p) => p.kind === kind).map((p) => <ProviderCard key={p.id} p={p} />)}
          </div>
        </div>
      ))}
      {costs.data?.by_provider.length ? (
        <Card>
          <div className="mb-2 text-sm font-semibold">Uso por provedor</div>
          <div className="grid gap-1 text-xs">
            {costs.data.by_provider.map((c) => (
              <div key={`${c.provider}-${c.kind}`} className="flex justify-between text-zinc-400">
                <span>{c.provider} ({c.kind})</span>
                <span>{c.calls} chamadas · {c.units} unidades · US$ {c.cost_usd.toFixed(4)}</span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
};
