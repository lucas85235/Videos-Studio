import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useRef, useState } from 'react';
import { api, type ChannelSlots, fileUrl, formatWhen, type ProjectDetail, toLocalInput } from '../../api';
import { Badge, Button, Card, Empty, ErrorBox, Select, SpeedButtons, formatSeconds } from '../../ui';
import { useStartJob } from '../ProjectPage';

// Um vídeo novo volta para 1x ao carregar; `defaultPlaybackRate` mantém a escolha.
const applyRate = (video: HTMLVideoElement, rate: number) => {
  video.defaultPlaybackRate = rate;
  video.playbackRate = rate;
};

const PLATFORMS = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'tiktok', label: 'TikTok' },
  { id: 'instagram', label: 'Instagram Reels' },
  { id: 'facebook', label: 'Facebook Reels' },
  { id: 'threads', label: 'Threads' },
  { id: 'x', label: 'X' },
];

type PackageItem = { id: string; platform: string; folder: string; upload_url: string; caption: string; blocked: boolean };
type YouTubeStatus = { client_configured: boolean; connected: boolean; channel_title: string };

/** Próximos horários livres do canal do projeto (configurados em Canais). */
const useChannelSlots = (channelId: string | null) =>
  useQuery({
    queryKey: ['channel-slots', channelId],
    queryFn: () => api.get<ChannelSlots>(`/api/channels/${channelId}/slots?count=3`),
    enabled: Boolean(channelId),
  });

const SlotChips: React.FC<{ channelId: string | null; onPick: (localValue: string) => void }> = ({ channelId, onPick }) => {
  const slots = useChannelSlots(channelId);
  if (!channelId || !slots.data) return null;
  if (!slots.data.configured) {
    return <div className="text-[11px] text-zinc-600">Dica: configure os horários de postagem do canal em Canais para ter sugestões aqui.</div>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      <span className="text-zinc-500">Horários livres do canal:</span>
      {slots.data.slots.map((iso) => (
        <button key={iso} type="button" className="rounded-md border border-zinc-700 px-1.5 py-0.5 text-zinc-300 hover:border-amber-400" onClick={() => onPick(toLocalInput(iso))}>
          {formatWhen(iso)}
        </button>
      ))}
    </div>
  );
};

const YouTubeCard: React.FC<{ projectId: string; channelId: string | null; lang: string; otherLangs: string[]; hasVideo: boolean; busy: boolean }> = ({
  projectId,
  channelId,
  lang,
  otherLangs,
  hasVideo,
  busy,
}) => {
  const client = useQueryClient();
  const status = useQuery({ queryKey: ['youtube-status'], queryFn: () => api.get<YouTubeStatus>('/api/youtube/status'), refetchInterval: 10_000 });
  const [privacy, setPrivacy] = useState('private');
  const [when, setWhen] = useState('');
  const [localize, setLocalize] = useState(false);
  const connect = useMutation({
    mutationFn: () => api.post<{ auth_url: string }>('/api/youtube/connect'),
    onSuccess: (data) => window.open(data.auth_url, '_blank', 'noopener'),
  });
  const publish = useMutation({
    mutationFn: () =>
      api.post(`/api/projects/${projectId}/publish/youtube`, {
        lang,
        privacy,
        publish_at: privacy === 'scheduled' && when ? new Date(when).toISOString() : null,
        localize: localize && otherLangs.length > 0,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['project', projectId] });
      client.invalidateQueries({ queryKey: ['agenda'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });
  const s = status.data;
  return (
    <Card className="flex flex-col gap-2 text-sm">
      <div className="font-semibold">YouTube (API oficial, grátis)</div>
      {!s ? null : !s.client_configured ? (
        <div className="text-xs text-zinc-500">
          Configure o cliente OAuth (client_id e client_secret) em Configurações › Publicação. Passo a passo em docs/USO.md.
        </div>
      ) : !s.connected ? (
        <Button busy={connect.isPending} onClick={() => connect.mutate()}>Conectar canal do YouTube</Button>
      ) : (
        <>
          <div className="text-xs text-zinc-400">Canal: <span className="text-zinc-200">{s.channel_title || 'conectado'}</span></div>
          <div className="flex flex-wrap gap-2">
            <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)} className="text-xs">
              <option value="private">privado</option>
              <option value="unlisted">não listado</option>
              <option value="public">público agora</option>
              <option value="scheduled">agendado</option>
            </Select>
            {privacy === 'scheduled' ? <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 text-xs" /> : null}
          </div>
          <SlotChips
            channelId={channelId}
            onPick={(value) => {
              setPrivacy('scheduled');
              setWhen(value);
            }}
          />
          {otherLangs.length ? (
            <label className="flex items-start gap-2 text-xs text-zinc-400">
              <input type="checkbox" className="mt-0.5" checked={localize} onChange={(e) => setLocalize(e.target.checked)} />
              <span>
                Incluir título e descrição traduzidos ({otherLangs.join(', ')}). O YouTube mostra a versão no idioma de quem assiste.
                Deixe desmarcado se cada idioma vai ganhar o próprio vídeo ou canal.
              </span>
            </label>
          ) : null}
          <Button variant="primary" disabled={!hasVideo || busy || (privacy === 'scheduled' && !when)} busy={publish.isPending} onClick={() => publish.mutate()}>
            Enviar ao YouTube
          </Button>
          <div className="text-[11px] text-zinc-600">Envia vídeo, thumbnail e legendas; o agendamento fica no próprio YouTube (o notebook pode estar desligado).</div>
        </>
      )}
      <ErrorBox error={connect.error ?? publish.error} />
    </Card>
  );
};

export const OutputTab: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const startJob = useStartJob(doc.id);
  const final = detail.outputs.final;
  const [lang, setLang] = useState(doc.primary_language);
  const [platforms, setPlatforms] = useState<string[]>(['youtube', 'tiktok', 'instagram']);
  const [pkg, setPkg] = useState<PackageItem[] | null>(null);
  const [planned, setPlanned] = useState('');
  const makePackage = useMutation({
    mutationFn: () =>
      api.post<{ items: PackageItem[] }>(`/api/projects/${doc.id}/publish/package`, {
        lang,
        platforms,
        planned_at: planned ? new Date(planned).toISOString() : null,
      }),
    onSuccess: (data) => {
      setPkg(data.items);
      client.invalidateQueries({ queryKey: ['project', doc.id] });
      client.invalidateQueries({ queryKey: ['agenda'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });
  const entry = final?.langs?.[lang];
  const videoRef = useRef<HTMLVideoElement>(null);
  const [rate, setRate] = useState(1);
  const meta = doc.metadata[lang];
  const chapters = entry?.chapters?.map((c) => `${formatSeconds(c.start)} ${c.title}`) ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={lang} onChange={(e) => setLang(e.target.value)}>
            {doc.languages.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </Select>
          <Button variant="primary" disabled={busy || !doc.scenes.length} onClick={() => startJob.mutate({ kind: 'render' })}>
            {entry?.video ? 'Renderizar de novo' : 'Renderizar vídeo final'}
          </Button>
          {final ? (
            <span className="text-xs text-zinc-500">
              gerado: {Object.entries(final.generated).map(([k, v]) => `${v} ${k}`).join(', ') || 'nada novo'} · reaproveitado:{' '}
              {Object.entries(final.reused).map(([k, v]) => `${v} ${k}`).join(', ') || '—'} · custo US$ {final.cost_usd.toFixed(3)}
            </span>
          ) : null}
        </div>
        {final?.preview_only_providers?.length ? (
          <ErrorBox
            error={`Este render usou provedores só de prévia (${final.preview_only_providers.join(', ')}). Não publique num canal monetizado: configure voz e imagens comerciais em Configurações e renderize de novo.`}
          />
        ) : null}
        {entry?.video ? (
          <Card className="flex flex-col gap-3">
            <video
              ref={videoRef}
              key={entry.video}
              src={fileUrl(entry.video)}
              controls
              onLoadedMetadata={(e) => applyRate(e.currentTarget, rate)}
              className="max-h-[70vh] w-full rounded-lg bg-black"
            />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <SpeedButtons value={rate} onChange={(r) => { setRate(r); if (videoRef.current) applyRate(videoRef.current, r); }} />
              <a className="text-sky-400 hover:underline" href={fileUrl(entry.video)} download>Baixar MP4</a>
              <a className="text-sky-400 hover:underline" href={fileUrl(entry.srt)} download>Legendas (.srt)</a>
              {entry.thumbnail ? <a className="text-sky-400 hover:underline" href={fileUrl(entry.thumbnail)} download>Thumbnail</a> : null}
              <Badge>{formatSeconds(entry.duration)}</Badge>
            </div>
            {entry.thumbnail ? <img src={fileUrl(entry.thumbnail)} className="max-h-64 self-start rounded-lg border border-zinc-800" /> : null}
          </Card>
        ) : (
          <Empty>O vídeo final ainda não foi renderizado neste idioma. A prévia (aba 3) é instantânea; o render gera o MP4 em blocos e reaproveita o que não mudou.</Empty>
        )}
      </div>
      <div className="flex flex-col gap-3">
        <Card className="flex flex-col gap-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-semibold">Título, descrição e tags</span>
            <Button className="text-xs" disabled={busy || !doc.segments.length} onClick={() => startJob.mutate({ kind: 'metadata', payload: { langs: [lang], chapters } })}>
              {meta ? 'Gerar de novo' : 'Gerar'}
            </Button>
          </div>
          {meta ? (
            <div className="flex flex-col gap-2 text-xs">
              <div className="text-zinc-100">{meta.title}</div>
              <div className="max-h-48 overflow-y-auto whitespace-pre-wrap text-zinc-400">{meta.description}</div>
              <div className="text-zinc-500">{meta.tags.join(', ')}</div>
              <div className="text-sky-300">{meta.hashtags.join(' ')}</div>
              {doc.thumbnail.text[lang] ? <div className="text-zinc-500">Texto da thumbnail: {doc.thumbnail.text[lang]}</div> : null}
            </div>
          ) : (
            <div className="text-xs text-zinc-500">Gere os metadados depois de renderizar (os capítulos entram na descrição).</div>
          )}
        </Card>
        <YouTubeCard
          projectId={doc.id}
          channelId={doc.channel_id}
          lang={lang}
          otherLangs={doc.languages.filter((l) => l !== lang && doc.metadata[l]?.title)}
          hasVideo={Boolean(entry?.video)}
          busy={busy}
        />
        {detail.publications.filter((p) => p.driver === 'youtube_native').length ? (
          <Card className="flex flex-col gap-1 text-xs">
            <div className="mb-1 text-sm font-semibold">Enviados</div>
            {detail.publications
              .filter((p) => p.driver === 'youtube_native')
              .map((p) => (
                <div key={p.id} className="flex items-center justify-between gap-2">
                  <span className="text-zinc-400">{p.language} · {p.status}</span>
                  {p.url ? <a className="text-sky-400 hover:underline" href={p.url} target="_blank" rel="noreferrer">{p.url}</a> : null}
                </div>
              ))}
          </Card>
        ) : null}
        <Card className="flex flex-col gap-3 text-sm">
          <div className="font-semibold">Publicar (assistente manual, custo zero)</div>
          <div className="text-xs text-zinc-500">
            O app monta uma pasta por rede com o vídeo, a legenda adaptada, a capa e um checklist (incluindo o rótulo de IA). Você envia pela página oficial e agenda lá.
          </div>
          <div className="flex flex-wrap gap-1.5">
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPlatforms((prev) => (prev.includes(p.id) ? prev.filter((x) => x !== p.id) : [...prev, p.id]))}
                className={`rounded-md border px-2 py-1 text-xs ${platforms.includes(p.id) ? 'border-amber-400 bg-amber-400/10 text-amber-200' : 'border-zinc-700 text-zinc-400'}`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
            <span>Planejar para (opcional)</span>
            <input type="datetime-local" value={planned} onChange={(e) => setPlanned(e.target.value)} className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs" />
          </div>
          <SlotChips channelId={doc.channel_id} onPick={setPlanned} />
          <Button variant="primary" disabled={!entry?.video || !platforms.length} busy={makePackage.isPending} onClick={() => makePackage.mutate()}>
            Preparar pacote de publicação
          </Button>
          <ErrorBox error={makePackage.error ?? startJob.error} />
          {pkg ? (
            <div className="flex flex-col gap-2">
              {pkg.map((item) => (
                <div key={item.id} className="rounded-lg border border-zinc-800 p-2 text-xs">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-semibold text-zinc-200">{item.platform}</span>
                    {item.blocked ? <Badge tone="bad">bloqueado</Badge> : <Badge tone="good">pronto</Badge>}
                  </div>
                  <div className="mb-1 break-all text-zinc-500">{item.folder}</div>
                  <div className="flex gap-2">
                    <a className="text-sky-400 hover:underline" href={item.upload_url} target="_blank" rel="noreferrer">Abrir página de upload</a>
                    <button className="text-sky-400 hover:underline" onClick={() => navigator.clipboard.writeText(item.caption)}>Copiar legenda</button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <div className="text-[11px] text-zinc-600">
            Os pacotes aparecem na Agenda até você marcar como postados. Envio automático para TikTok/Instagram pelo Buffer está no backlog (precisa da sua conta).
            Nunca por robô de navegador: os termos das redes proíbem.
          </div>
        </Card>
      </div>
    </div>
  );
};
