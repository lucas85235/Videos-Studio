import { useMutation, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, fileUrl, type ProjectDetail } from '../../api';
import { Badge, Button, Card, Empty, ErrorBox, Select, formatSeconds } from '../../ui';
import { useStartJob } from '../ProjectPage';

const PLATFORMS = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'tiktok', label: 'TikTok' },
  { id: 'instagram', label: 'Instagram Reels' },
  { id: 'facebook', label: 'Facebook Reels' },
  { id: 'threads', label: 'Threads' },
  { id: 'x', label: 'X' },
];

type PackageItem = { id: string; platform: string; folder: string; upload_url: string; caption: string; blocked: boolean };

export const OutputTab: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const startJob = useStartJob(doc.id);
  const final = detail.outputs.final;
  const [lang, setLang] = useState(doc.primary_language);
  const [platforms, setPlatforms] = useState<string[]>(['youtube', 'tiktok', 'instagram']);
  const [pkg, setPkg] = useState<PackageItem[] | null>(null);
  const makePackage = useMutation({
    mutationFn: () => api.post<{ items: PackageItem[] }>(`/api/projects/${doc.id}/publish/package`, { lang, platforms }),
    onSuccess: (data) => {
      setPkg(data.items);
      client.invalidateQueries({ queryKey: ['project', doc.id] });
    },
  });
  const entry = final?.langs?.[lang];
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
            <video key={entry.video} src={fileUrl(entry.video)} controls className="max-h-[70vh] w-full rounded-lg bg-black" />
            <div className="flex flex-wrap gap-2 text-xs">
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
            Upload automático: YouTube pela API oficial e TikTok/Instagram pelo Buffer estão no backlog (precisam das suas contas). Nunca por robô de navegador: os termos das redes proíbem.
          </div>
        </Card>
      </div>
    </div>
  );
};
