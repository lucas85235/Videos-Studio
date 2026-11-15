// "Página com marca-texto" (prova na tela, ADR 0003, bloco I): captura a página com os trechos marcados e põe a
// imagem na cena. Mostra o estado da última captura, os trechos achados e o ajuste do enquadramento.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Highlighter, Lock, RotateCcw } from 'lucide-react';
import React, { useId, useState } from 'react';
import { fileUrl } from '../../../api';
import { visualApi, type CapturePhrase, type PageCapture } from '../../../apiVisual';
import { isActiveJob } from '../../../shell/data';
import { Badge, Button, ErrorBox, Input, Select, Textarea } from '../../../ui';
import { useJobDone } from './useJobDone';

const MAX_PHRASES = 6;
const STATUS: Record<PageCapture['status'], { tone: 'good' | 'warn' | 'bad'; text: string }> = {
  ok: { tone: 'good', text: 'Todos os trechos marcados' },
  parcial: { tone: 'warn', text: 'Faltou trecho' },
  falhou: { tone: 'bad', text: 'Falhou' },
};

type View = [number, number, number, number];

const pct = (v: number) => Math.round(v * 100);

/** Janela do trecho sobre a página inteira, com 4 campos em % e "travar". */
const ViewEditor: React.FC<{ capture: PageCapture; index: number; phrase: CapturePhrase; busy: boolean; onSave: (view: View | null, lock: boolean) => void }> = ({
  capture,
  index,
  phrase,
  busy,
  onSave,
}) => {
  const id = useId();
  const [view, setView] = useState<View>(phrase.view ?? [0, 0, 1, 1]);
  const names = ['Esquerda', 'Topo', 'Direita', 'Base'];
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line p-2">
      <div className="relative overflow-hidden rounded border border-line">
        <img src={fileUrl(capture.image)} alt={`Página capturada: ${capture.title}`} className="block w-full" />
        <div
          aria-hidden="true"
          className="absolute border-2 border-accent bg-accent/10"
          style={{ left: `${view[0] * 100}%`, top: `${view[1] * 100}%`, width: `${(view[2] - view[0]) * 100}%`, height: `${(view[3] - view[1]) * 100}%` }}
        />
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        {names.map((name, k) => (
          <div key={name} className="flex flex-col gap-0.5">
            <label htmlFor={`${id}-${k}`} className="text-[11px] text-subtle">
              {name} (%)
            </label>
            <Input
              id={`${id}-${k}`}
              type="number"
              min={0}
              max={100}
              value={pct(view[k])}
              onChange={(e) => {
                const next = [...view] as View;
                next[k] = Math.min(1, Math.max(0, Number(e.target.value) / 100));
                setView(next);
              }}
              className="text-[13px]"
            />
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" icon={Lock} busy={busy} onClick={() => onSave(view, true)}>
          Travar este enquadramento
        </Button>
        <Button size="sm" variant="ghost" icon={RotateCcw} disabled={busy} onClick={() => onSave(null, false)}>
          Automático
        </Button>
      </div>
      <p className="text-xs text-subtle">Trecho {index + 1}: a prova passa a mostrar esta janela. Travado, ele não muda quando você volta outro trecho desta captura para o automático. Uma nova captura recalcula tudo.</p>
    </div>
  );
};

export const ProofCapture: React.FC<{ projectId: string; sceneId: string; locked: boolean }> = ({ projectId, sceneId, locked }) => {
  const client = useQueryClient();
  const urlId = useId();
  const phrasesId = useId();
  const focusId = useId();
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [focus, setFocus] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const caps = useQuery({ queryKey: ['visual-capabilities'], queryFn: visualApi.capabilities, staleTime: 60_000 });
  const captures = useQuery({ queryKey: ['captures', projectId], queryFn: () => visualApi.captures(projectId) });
  const last = (captures.data ?? []).find((c) => c.scene_id === sceneId) ?? null;

  const phrases = text
    .split('\n')
    .map((p) => p.trim())
    .filter(Boolean);
  const tooMany = phrases.length > MAX_PHRASES;

  const job = useJobDone(jobId, async (j) => {
    setJobId(null);
    if (j.status === 'failed') setJobError(j.error ?? 'A captura falhou.');
    await client.invalidateQueries({ queryKey: ['captures', projectId] });
    await client.invalidateQueries({ queryKey: ['project', projectId] });
  });
  const running = Boolean(jobId) && (!job || isActiveJob(job));

  const start = useMutation({
    mutationFn: () => visualApi.captureProof(projectId, sceneId, { url: url.trim(), phrases, focus: Math.min(focus, phrases.length - 1) }),
    onSuccess: (j) => {
      setJobError(null);
      setJobId(j.id);
    },
  });
  const rebake = useMutation({
    mutationFn: (v: { capture: string; i: number; view: View | null; lock: boolean }) => visualApi.setPhraseView(v.capture, v.i, v.view, v.lock),
    onSuccess: async () => {
      setEditing(null);
      await client.invalidateQueries({ queryKey: ['captures', projectId] });
      await client.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });

  const browser = caps.data?.browser;
  const canSubmit = !locked && !running && /^https?:\/\/\S+$/i.test(url.trim()) && phrases.length > 0 && !tooMany;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-line p-2.5">
      <div className="flex items-center gap-1.5">
        <Highlighter size={14} aria-hidden="true" className="text-subtle" />
        <span className="text-xs font-medium text-fg">Página com marca-texto</span>
      </div>
      <p className="text-xs text-subtle">Fotografa a página real com os trechos marcados em amarelo e põe na cena (prova na tela).</p>
      {browser && !browser.ok ? <p className="text-[13px] text-amber-200">{browser.message}</p> : null}
      <form
        noValidate
        className="flex flex-col gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) start.mutate();
        }}
      >
        <label htmlFor={urlId} className="text-xs font-medium text-subtle">
          Link da página
        </label>
        <Input id={urlId} type="url" value={url} placeholder="https://…" disabled={locked || browser?.ok === false} onChange={(e) => setUrl(e.target.value)} className="text-[13px]" />
        <label htmlFor={phrasesId} className="text-xs font-medium text-subtle">
          Trechos para marcar (um por linha, até {MAX_PHRASES}, copiados da página)
        </label>
        <Textarea id={phrasesId} rows={3} value={text} disabled={locked || browser?.ok === false} onChange={(e) => setText(e.target.value)} className="text-[13px]" />
        {tooMany ? <p className="text-xs text-red-300">No máximo {MAX_PHRASES} trechos.</p> : null}
        {phrases.length > 1 ? (
          <>
            <label htmlFor={focusId} className="text-xs font-medium text-subtle">
              Trecho de foco (fica no meio da tela)
            </label>
            <Select id={focusId} value={String(Math.min(focus, phrases.length - 1))} onChange={(e) => setFocus(Number(e.target.value))} className="text-[13px]">
              {phrases.slice(0, MAX_PHRASES).map((p, i) => (
                <option key={i} value={i}>
                  {i + 1}. {p.slice(0, 60)}
                </option>
              ))}
            </Select>
          </>
        ) : null}
        <div>
          <Button size="sm" type="submit" icon={Highlighter} disabled={!canSubmit || browser?.ok === false} busy={running || start.isPending}>
            Capturar e marcar
          </Button>
        </div>
      </form>
      {running && job ? <p className="text-xs text-muted">{job.message || 'Capturando…'}</p> : null}
      <ErrorBox error={start.error ?? rebake.error ?? (jobError ? new Error(jobError) : null)} title="A captura não deu certo" onClose={() => setJobError(null)} />

      {last ? (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={STATUS[last.status].tone}>{STATUS[last.status].text}</Badge>
            <span className="truncate text-xs text-subtle" title={last.url}>
              {last.domain}
            </span>
          </div>
          {last.status === 'falhou' && last.error ? <p className="text-[13px] text-red-300">{last.error}</p> : null}
          {last.proof ? <img src={fileUrl(last.proof)} alt={`Prova: ${last.title}`} className="w-full rounded border border-line" /> : null}
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {last.phrases.map((p, i) => (
              <li key={i} className="flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <Badge tone={p.view ? 'good' : 'warn'}>{p.view ? 'Achado' : p.found ? 'Fora da tela' : 'Não achado'}</Badge>
                  {p.lock ? <Badge icon={Lock}>Travado</Badge> : null}
                  <span className="min-w-0 flex-1 truncate text-muted" title={p.text}>
                    {p.text}
                  </span>
                  {last.image ? (
                    <Button size="sm" variant="ghost" disabled={locked || rebake.isPending} onClick={() => setEditing(editing === i ? null : i)}>
                      Ajustar enquadramento
                    </Button>
                  ) : null}
                </div>
                {editing === i && last.image ? (
                  <ViewEditor
                    capture={last}
                    index={i}
                    phrase={p}
                    busy={rebake.isPending}
                    onSave={(view, lock) => rebake.mutate({ capture: last.id, i, view, lock })}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
};
