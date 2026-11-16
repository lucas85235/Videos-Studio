// Peças do YouTube usadas em mais de uma tela: medidor de cota e escolha de playlist.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import React, { useState } from 'react';
import {
  api,
  type YouTubeAccountStatus,
  type YouTubePlaylist,
  type YouTubePlaylists,
  type YouTubeQuota,
} from './api';
import { Button, ErrorBox, Field, Input, ProgressBar, Select } from './ui';

const number = new Intl.NumberFormat('pt-BR');

export const useYouTubeQuota = () =>
  useQuery({
    queryKey: ['youtube-quota'],
    queryFn: () => api.get<YouTubeQuota>('/api/youtube/quota'),
    refetchInterval: 60_000,
  });

const resetTime = (iso: string) => {
  try {
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
};

const METHOD_LABELS: Record<string, string> = {
  'videos.insert': 'Envio de vídeo',
  'thumbnails.set': 'Capa',
  'captions.insert': 'Legendas',
  'playlists.list': 'Listar playlists',
  'playlists.insert': 'Criar playlist',
  'playlistItems.list': 'Vídeos recentes',
  'playlistItems.insert': 'Pôr na playlist',
  'channels.list': 'Dados do canal',
};

/**
 * Cota da YouTube Data API usada hoje. A cota é do projeto do Google Cloud (vale para todas as contas) e zera à
 * meia-noite do horário do Pacífico. `details` mostra o gasto por tipo de chamada.
 */
export const QuotaMeter: React.FC<{ quota: YouTubeQuota; details?: boolean; className?: string }> = ({
  quota,
  details,
  className = '',
}) => {
  // Sem envios de vídeo, nada sobe (vermelho); sem a cota geral, o vídeo ainda sobe (amarelo).
  const level = quota.uploads_over ? 'over' : quota.level === 'over' ? 'near' : quota.level;
  const tone = level === 'over' ? 'bad' : level === 'near' ? 'warn' : 'accent';
  const textTone = level === 'over' ? 'text-red-300' : level === 'near' ? 'text-amber-200' : 'text-subtle';
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
        <span className="text-muted">Cota do YouTube hoje</span>
        <span className="tabular-nums text-fg">
          {number.format(quota.used)} de {number.format(quota.limit)} unidades · {quota.uploads} de {quota.upload_limit}{' '}
          envios
        </span>
      </div>
      <ProgressBar
        value={Math.max(quota.used / quota.limit, quota.uploads / quota.upload_limit)}
        tone={tone}
        label="Cota do YouTube usada hoje"
        valueText={`${number.format(quota.used)} de ${number.format(quota.limit)} unidades`}
      />
      <p className={`text-xs ${textTone}`}>
        {quota.message ? `${quota.message} ` : ''}
        Zera às {resetTime(quota.resets_at)} (meia-noite no Pacífico). Cada envio com capa e legendas gasta cerca de{' '}
        {quota.publish_estimate} unidades.
      </p>
      {details && quota.by_method.length ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-subtle">
          {quota.by_method.map((m) => (
            <li key={m.method} className="tabular-nums">
              {METHOD_LABELS[m.method] ?? m.method}: {m.calls}× ({number.format(m.units)})
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
};

/** Medidor que busca a cota sozinho (some se a busca falhar: o envio ainda funciona sem ele). */
export const YouTubeQuotaLine: React.FC<{ details?: boolean; className?: string }> = ({ details, className }) => {
  const quota = useYouTubeQuota();
  return quota.data ? <QuotaMeter quota={quota.data} details={details} className={className} /> : null;
};

const PRIVACY_LABELS: Record<string, string> = { public: 'pública', unlisted: 'não listada', private: 'privada' };

export const playlistLabel = (p: YouTubePlaylist) =>
  `${p.title || '(sem nome)'}${p.privacy && p.privacy !== 'public' ? ` · ${PRIVACY_LABELS[p.privacy] ?? p.privacy}` : ''}${
    p.count != null ? ` · ${p.count} vídeos` : ''
  }`;

const NEW = '__nova';

/**
 * Escolha de playlist do canal do YouTube. `scope` diz de qual conta: projeto, canal do Vulto ou conta escolhida.
 * `extra` são opções fixas no topo (ex.: "Padrão do canal", "Nenhuma"). Listar gasta 1 unidade de cota por página;
 * criar, 50. Com `lazy`, a lista só é buscada quando a pessoa abre a escolha (não gasta cota a cada visita).
 */
export const PlaylistPicker: React.FC<{
  scope: { project_id?: string; channel_id?: string; account?: string };
  value: string;
  onChange: (id: string, title: string) => void;
  extra: { value: string; label: string }[];
  label?: string;
  disabled?: boolean;
  className?: string;
  lazy?: boolean;
}> = ({ scope, value, onChange, extra, label = 'Playlist', disabled, className = '', lazy = false }) => {
  const client = useQueryClient();
  const [opened, setOpened] = useState(!lazy);
  const params = new URLSearchParams(Object.entries(scope).filter(([, v]) => Boolean(v)) as [string, string][]);
  const key = ['youtube-playlists', params.toString()];
  const list = useQuery({
    queryKey: key,
    queryFn: () => api.get<YouTubePlaylists>(`/api/youtube/playlists?${params}`),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
    enabled: opened,
  });
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [privacy, setPrivacy] = useState('public');
  const create = useMutation({
    mutationFn: () => api.post<YouTubePlaylist>('/api/youtube/playlists', { ...scope, title: title.trim(), privacy }),
    onSuccess: (p) => {
      client.setQueryData<YouTubePlaylists>(key, (old) =>
        old ? { ...old, playlists: [...old.playlists, p].sort((a, b) => a.title.localeCompare(b.title)) } : old,
      );
      client.invalidateQueries({ queryKey: ['youtube-quota'] });
      setCreating(false);
      setTitle('');
      onChange(p.id, p.title);
    },
  });
  const items = list.data?.playlists ?? [];
  // A playlist escolhida pode não estar na lista (apagada no YouTube ou lista ainda carregando).
  const known = extra.some((o) => o.value === value) || items.some((p) => p.id === value);
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <Field label={label} hint={list.isFetching ? 'Carregando as playlists do canal…' : undefined}>
        <Select
          value={creating ? NEW : value}
          disabled={disabled}
          onFocus={() => setOpened(true)}
          onMouseDown={() => setOpened(true)}
          onChange={(e) => {
            const id = e.target.value;
            if (id === NEW) {
              setCreating(true);
              return;
            }
            setCreating(false);
            onChange(id, items.find((p) => p.id === id)?.title ?? '');
          }}
        >
          {extra.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
          {!known && value ? <option value={value}>Playlist escolhida antes</option> : null}
          {items.map((p) => (
            <option key={p.id} value={p.id}>
              {playlistLabel(p)}
            </option>
          ))}
          {list.data ? <option value={NEW}>+ Nova playlist…</option> : null}
        </Select>
      </Field>
      {list.data?.truncated ? (
        <p className="text-xs text-subtle">
          O canal tem mais de {list.data.max ?? items.length} playlists: só as primeiras aparecem aqui. Para usar outra,
          escolha-a no YouTube Studio depois do envio.
        </p>
      ) : null}
      {list.error ? (
        <ErrorBox error={list.error} title="Não foi possível ler as playlists" onRetry={() => list.refetch()} />
      ) : null}
      {creating ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim()) create.mutate();
          }}
        >
          <Field label="Nome da nova playlist" className="min-w-48 flex-1">
            <Input value={title} maxLength={150} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </Field>
          <Field label="Visibilidade">
            <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
              <option value="public">Pública</option>
              <option value="unlisted">Não listada</option>
              <option value="private">Privada</option>
            </Select>
          </Field>
          <Button type="submit" size="sm" icon={Plus} busy={create.isPending} disabled={!title.trim()}>
            Criar
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setCreating(false)}>
            Cancelar
          </Button>
          {create.error ? (
            <ErrorBox error={create.error} title="Playlist não criada" onClose={() => create.reset()} className="w-full" />
          ) : null}
        </form>
      ) : null}
    </div>
  );
};

/**
 * Configurações › Publicação: a cota de hoje com o gasto por chamada, as contas conectadas e o limite diário
 * (para quem pediu aumento de cota ao Google).
 */
export const YouTubeQuotaSettings: React.FC = () => {
  const client = useQueryClient();
  const quota = useYouTubeQuota();
  const status = useQuery({
    queryKey: ['youtube-status'],
    queryFn: () => api.get<YouTubeAccountStatus>('/api/youtube/status'),
  });
  const [limit, setLimit] = useState('');
  const [uploadLimit, setUploadLimit] = useState('');
  const save = useMutation({
    mutationFn: (body: { limit?: number | null; upload_limit?: number | null }) =>
      api.put<YouTubeQuota>('/api/youtube/quota', body),
    onSuccess: (data) => {
      client.setQueryData(['youtube-quota'], data);
      setLimit('');
      setUploadLimit('');
    },
  });
  const accounts = status.data?.accounts ?? [];
  const parsed = Number(limit.replace(/\D/g, ''));
  const parsedUploads = Number(uploadLimit.replace(/\D/g, ''));
  const changes: { limit?: number; upload_limit?: number } = {
    ...(parsed > 0 ? { limit: parsed } : {}),
    ...(parsedUploads > 0 ? { upload_limit: parsedUploads } : {}),
  };
  const custom = quota.data?.custom_limit || quota.data?.custom_upload_limit;
  return (
    <article className="flex flex-col gap-3 rounded-xl border border-line bg-card p-4">
      <h3 className="font-display text-[15px] font-semibold text-fg">Cota e contas do YouTube</h3>
      {quota.data ? (
        <QuotaMeter quota={quota.data} details />
      ) : quota.error ? (
        <ErrorBox error={quota.error} title="Não foi possível ler a cota" onRetry={() => quota.refetch()} />
      ) : null}
      <p className="text-xs text-subtle">
        A cota é do seu projeto no Google Cloud e vale para todas as contas abaixo. O app conta só as chamadas que ele
        mesmo fez; se o Google avisar que a cota acabou, aquele tipo de cota fica esgotado até zerar. Sem a cota geral o
        vídeo ainda sobe (só a capa, as legendas e a playlist falham); sem os envios do dia, nada sobe.
      </p>
      {accounts.length ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted">Contas que podem publicar</span>
          <ul className="flex flex-col gap-1 text-[13px]">
            {accounts.map((a) => (
              <li key={a.channel_id || 'global'} className="flex flex-wrap gap-x-2 text-fg">
                <span>{a.channel_title || a.channel_id || 'Canal sem nome'}</span>
                <span className="text-subtle">
                  {a.global ? 'conta da aba de publicação' : 'vinculada em Canais'}
                  {a.vulto_channels.length ? ` · ${a.vulto_channels.join(', ')}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-[13px] text-muted">Nenhuma conta conectada ainda. Vincule os canais em Canais.</p>
      )}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (Object.keys(changes).length) save.mutate(changes);
        }}
      >
        <Field
          label="Limite diário de unidades"
          hint={`Deixe ${number.format(10_000)} (o padrão do Google), a não ser que o Google tenha aumentado a cota do seu projeto.`}
          className="min-w-48"
        >
          <Input
            inputMode="numeric"
            placeholder={quota.data ? number.format(quota.data.limit) : '10.000'}
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </Field>
        <Field
          label="Envios de vídeo por dia"
          hint="O padrão do Google é 100 (cota própria do envio de vídeo)."
          className="min-w-40"
        >
          <Input
            inputMode="numeric"
            placeholder={quota.data ? number.format(quota.data.upload_limit) : '100'}
            value={uploadLimit}
            onChange={(e) => setUploadLimit(e.target.value)}
          />
        </Field>
        <Button type="submit" size="sm" busy={save.isPending} disabled={!Object.keys(changes).length}>
          Salvar limites
        </Button>
        {custom ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => save.mutate({ limit: null, upload_limit: null })}
          >
            Voltar ao padrão
          </Button>
        ) : null}
      </form>
      {save.error ? <ErrorBox error={save.error} title="Limite não salvo" onClose={() => save.reset()} /> : null}
    </article>
  );
};
