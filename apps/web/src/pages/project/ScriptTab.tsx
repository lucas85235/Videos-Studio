import { useMutation, useQueryClient } from '@tanstack/react-query';
import React, { useEffect, useState } from 'react';
import { api, type ProjectDetail } from '../../api';
import { Badge, Button, Card, Empty, ErrorBox, Select, Textarea } from '../../ui';
import { useStartJob } from '../ProjectPage';

const ROLE_LABEL: Record<string, string> = { hook: 'gancho', body: 'corpo', chapter: 'capítulo', item: 'item', cta: 'chamada' };

export const ScriptTab: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const startJob = useStartJob(doc.id);
  const [lang, setLang] = useState(doc.primary_language);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => setDrafts({}), [detail.project.current_version, lang]);

  const changed = Object.keys(drafts).length > 0;
  const save = useMutation({
    mutationFn: () => {
      const next = structuredClone(doc);
      for (const seg of next.segments) {
        if (drafts[seg.id] !== undefined) seg.text[lang] = drafts[seg.id];
      }
      return api.put(`/api/projects/${doc.id}/doc`, { doc: next, message: 'Roteiro editado à mão' });
    },
    onSuccess: () => {
      setDrafts({});
      client.invalidateQueries({ queryKey: ['project', doc.id] });
    },
  });
  const approve = useMutation({
    mutationFn: () => api.post(`/api/projects/${doc.id}/approve`, { stage: 'script', continue_pipeline: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });
  const lock = useMutation({
    mutationFn: ({ id, locked }: { id: string; locked: boolean }) =>
      api.post(`/api/projects/${doc.id}/ops`, { ops: [{ op: 'set_lock', ids: [id], locked }], message: locked ? 'Segmento travado' : 'Segmento destravado' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });

  const words = doc.segments.reduce((n, s) => n + (s.text[lang] ?? '').split(/\s+/).filter(Boolean).length, 0);

  if (!doc.segments.length) {
    return (
      <Empty>
        <div className="mb-3">Ainda não há roteiro.</div>
        <Button variant="primary" busy={startJob.isPending} disabled={busy} onClick={() => startJob.mutate({ kind: 'script' })}>
          Gerar roteiro
        </Button>
      </Empty>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
      <div className="flex flex-col gap-3">
        {doc.segments.map((seg, index) => (
          <Card key={seg.id} className="p-3">
            <div className="mb-2 flex items-center gap-2 text-xs text-zinc-400">
              <span className="font-mono text-zinc-500">S{index + 1}</span>
              <Badge tone={seg.role === 'hook' ? 'warn' : seg.role === 'cta' ? 'info' : 'neutral'}>{ROLE_LABEL[seg.role] ?? seg.role}</Badge>
              {seg.chapter ? <Badge>{seg.chapter}</Badge> : null}
              {seg.speaker !== 'narrator' ? <Badge tone="info">voz: {seg.speaker}</Badge> : null}
              <button className="ml-auto text-zinc-500 hover:text-zinc-200" onClick={() => lock.mutate({ id: seg.id, locked: !seg.locked })}>
                {seg.locked ? '🔒 travado' : '🔓 travar'}
              </button>
            </div>
            <Textarea
              className="w-full"
              rows={Math.max(2, Math.ceil((drafts[seg.id] ?? seg.text[lang] ?? '').length / 95))}
              value={drafts[seg.id] ?? seg.text[lang] ?? ''}
              disabled={seg.locked}
              placeholder={lang !== doc.primary_language ? 'Sem tradução ainda' : ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [seg.id]: e.target.value }))}
            />
          </Card>
        ))}
      </div>
      <div className="flex flex-col gap-3">
        <Card className="flex flex-col gap-3 text-sm">
          <Select value={lang} onChange={(e) => setLang(e.target.value)}>
            {doc.languages.map((l) => (
              <option key={l} value={l}>
                {l}
                {l === doc.primary_language ? ' (principal)' : ''}
              </option>
            ))}
          </Select>
          <div className="text-xs text-zinc-500">{words} palavras · ~{Math.round((words / 150) * 60)} s de narração</div>
          <Button variant="primary" disabled={!changed} busy={save.isPending} onClick={() => save.mutate()}>
            Salvar alterações
          </Button>
          {!doc.script_approved ? (
            <Button busy={approve.isPending} disabled={busy || changed} onClick={() => approve.mutate()}>
              ✓ Aprovar roteiro e continuar
            </Button>
          ) : (
            <Badge tone="good">Roteiro aprovado</Badge>
          )}
          <Button variant="ghost" disabled={busy} onClick={() => window.confirm('Gerar um roteiro novo substitui o atual (dá para voltar em Versões).') && startJob.mutate({ kind: 'script' })}>
            Gerar outro roteiro
          </Button>
          {doc.languages.length > 1 ? (
            <Button variant="ghost" disabled={busy} onClick={() => startJob.mutate({ kind: 'translate' })}>
              Traduzir para os outros idiomas
            </Button>
          ) : null}
          <Button variant="ghost" disabled={busy} onClick={() => startJob.mutate({ kind: 'storyboard' })}>
            {doc.scenes.length ? 'Refazer storyboard' : 'Gerar storyboard'}
          </Button>
          <ErrorBox error={save.error ?? approve.error ?? startJob.error} />
        </Card>
        {doc.sources.length ? (
          <Card className="text-xs">
            <div className="mb-2 font-semibold text-zinc-300">Fontes</div>
            {doc.sources.map((s) => (
              <div key={s.title} className="mb-1">
                {s.url ? <a className="text-sky-400 hover:underline" href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
                {s.note ? <span className="text-zinc-500"> · {s.note}</span> : null}
              </div>
            ))}
          </Card>
        ) : null}
      </div>
    </div>
  );
};
