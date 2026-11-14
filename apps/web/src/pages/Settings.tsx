import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, type Provider } from '../api';
import { Badge, Button, Card, ErrorBox, Input } from '../ui';

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
