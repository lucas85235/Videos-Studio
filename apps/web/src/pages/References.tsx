// Página Referências (ADR 0003, bloco I): vídeos para estudar ritmo, planos e cores. Envio do arquivo ou link
// (yt-dlp, opcional), análise em planos, folhas de contato e paleta.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Film, Link2, Pencil, RefreshCw, Trash2, Upload, X } from 'lucide-react';
import React, { useEffect, useId, useRef, useState } from 'react';
import { visualApi, type ReferenceVideo } from '../apiVisual';
import { isActiveJob, useJobs } from '../shell/data';
import { Badge, Button, Empty, ErrorBox, Input, Page, PageHeader, Section, Skeleton, useConfirm, useToast } from '../ui';
import { ReferenceDetail } from './references/ReferenceDetail';

const REFS_KEY = ['references'] as const;
const STATUS: Record<ReferenceVideo['status'], { tone: 'neutral' | 'good' | 'bad'; text: string }> = {
  novo: { tone: 'neutral', text: 'Não analisado' },
  pronto: { tone: 'good', text: 'Analisado' },
  falhou: { tone: 'bad', text: 'Falhou' },
};

/** Nome da referência editável no lugar do título (fora do h2, via `titleEditor` do Section): Enter salva, Esc cancela. */
const RenameTitle: React.FC<{ reference: ReferenceVideo; busy: boolean; onSave: (title: string) => void; onCancel: () => void }> = ({
  reference,
  busy,
  onSave,
  onCancel,
}) => {
  const [draft, setDraft] = useState(reference.title);
  const inputId = useId();
  useEffect(() => {
    const input = document.getElementById(inputId);
    if (input instanceof HTMLInputElement) {
      input.focus();
      input.select();
    }
  }, [inputId]);
  const clean = draft.trim();
  const save = () => {
    if (!clean || busy) return;
    if (clean === reference.title) onCancel();
    else onSave(clean);
  };
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <Input
        id={inputId}
        aria-label="Novo nome da referência"
        value={draft}
        maxLength={300}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            save();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            onCancel();
          }
        }}
        className="min-w-0 flex-1 basis-56 py-1 font-sans font-normal"
      />
      <Button size="sm" variant="primary" icon={Check} busy={busy} disabled={!clean} onClick={save}>
        Salvar
      </Button>
      <Button size="sm" variant="ghost" icon={X} disabled={busy} onClick={onCancel}>
        Cancelar
      </Button>
    </span>
  );
};

export const ReferencesPage: React.FC = () => {
  const client = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const linkId = useId();
  const titleId = useId();
  const [link, setLink] = useState('');
  const [title, setTitle] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);

  const caps = useQuery({ queryKey: ['visual-capabilities'], queryFn: visualApi.capabilities, staleTime: 60_000 });
  const jobs = useJobs();
  const active = (jobs.data ?? []).filter((j) => j.kind === 'reference_shots' && isActiveJob(j));
  const refs = useQuery({ queryKey: REFS_KEY, queryFn: visualApi.references, refetchInterval: active.length ? 3000 : false });
  const refresh = () => client.invalidateQueries({ queryKey: REFS_KEY });
  // O job da análise não tem projeto, então os eventos não atualizam a lista: busca de novo quando um termina.
  const activeIds = active.map((j) => j.id).join(',');
  const seen = useRef<string>('');
  useEffect(() => {
    const before = seen.current ? seen.current.split(',') : [];
    seen.current = activeIds;
    const now = new Set(activeIds ? activeIds.split(',') : []);
    if (before.some((id) => !now.has(id))) void client.invalidateQueries({ queryKey: REFS_KEY });
  }, [activeIds, client]);

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      if (title.trim()) form.append('title', title.trim());
      return visualApi.uploadReference(form);
    },
    onSuccess: async () => {
      setTitle('');
      await refresh();
      toast({ tone: 'info', title: 'Vídeo enviado: analisando os planos' });
    },
  });
  const byLink = useMutation({
    mutationFn: () => visualApi.referenceFromLink(link.trim(), title.trim() || undefined),
    onSuccess: async () => {
      setLink('');
      setTitle('');
      await refresh();
      toast({ tone: 'info', title: 'Baixando e analisando o vídeo' });
    },
  });
  const analyze = useMutation({ mutationFn: (id: string) => visualApi.analyzeReference(id), onSuccess: () => refresh() });
  const remove = useMutation({ mutationFn: (id: string) => visualApi.deleteReference(id), onSuccess: () => refresh() });
  const rename = useMutation({
    mutationFn: (vars: { id: string; title: string }) => visualApi.renameReference(vars.id, vars.title),
    onSuccess: async (ref) => {
      setRenaming(null);
      await refresh();
      toast({ tone: 'good', title: 'Referência renomeada', description: `Agora se chama “${ref.title}”.` });
    },
  });

  const ytdlp = caps.data?.ytdlp;
  const runningFor = (id: string) => active.find((j) => j.payload?.reference_id === id);

  return (
    <Page>
      <PageHeader title="Referências" description="Vídeos para estudar ritmo, planos e cores. Servem para estudar, não para reusar." />
      <Section title="Adicionar vídeo" icon={Upload} description="Envie o arquivo ou cole o link. O app corta em planos e mede o ritmo e as cores.">
        <div className="flex flex-col gap-1">
          <label htmlFor={titleId} className="text-xs font-medium text-subtle">
            Nome (opcional)
          </label>
          <Input id={titleId} value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} className="max-w-md text-[13px]" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            className="hidden"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = '';
            }}
          />
          <Button icon={Upload} busy={upload.isPending} onClick={() => fileRef.current?.click()}>
            Enviar arquivo
          </Button>
          <span className="text-xs text-subtle">MP4, MOV, MKV ou WEBM.</span>
        </div>
        <form
          className="flex flex-col gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (link.trim()) byLink.mutate();
          }}
        >
          <label htmlFor={linkId} className="text-xs font-medium text-subtle">
            Link do vídeo (YouTube e outros)
          </label>
          <div className="flex max-w-xl gap-1.5">
            <Input
              id={linkId}
              type="url"
              value={link}
              placeholder="https://…"
              disabled={ytdlp?.ok === false}
              onChange={(e) => setLink(e.target.value)}
              className="min-w-0 flex-1 text-[13px]"
            />
            <Button type="submit" icon={Link2} disabled={!link.trim() || ytdlp?.ok === false} busy={byLink.isPending}>
              Baixar
            </Button>
          </div>
          {ytdlp && !ytdlp.ok ? <p className="text-[13px] text-amber-200">{ytdlp.message}</p> : null}
        </form>
        <ErrorBox error={upload.error ?? byLink.error} title="Não foi possível adicionar o vídeo" />
      </Section>

      <div className="mt-4 flex flex-col gap-3">
        {refs.isLoading ? <Skeleton className="h-24 rounded-xl" /> : null}
        <ErrorBox error={refs.error ?? analyze.error ?? remove.error ?? rename.error} title="Algo deu errado" onRetry={() => void refs.refetch()} />
        {refs.data && !refs.data.length ? (
          <Empty icon={Film} title="Nenhuma referência ainda">
            Envie um vídeo que você quer estudar: o app mostra os planos, o ritmo dos cortes e as cores.
          </Empty>
        ) : null}
        {(refs.data ?? []).map((ref) => {
          const job = runningFor(ref.id);
          const st = STATUS[ref.status];
          return (
            <Section
              key={ref.id}
              title={ref.title}
              titleEditor={
                renaming === ref.id ? (
                  <RenameTitle
                    reference={ref}
                    busy={rename.isPending}
                    onSave={(t) => rename.mutate({ id: ref.id, title: t })}
                    onCancel={() => {
                      setRenaming(null);
                      rename.reset();
                    }}
                  />
                ) : undefined
              }
              icon={Film}
              description={
                <span className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={job ? 'info' : st.tone}>{job ? job.message || 'Analisando…' : st.text}</Badge>
                  {ref.source_url ? (
                    <a href={ref.source_url} target="_blank" rel="noreferrer" className="truncate text-xs text-subtle underline-offset-2 hover:underline">
                      {ref.source_url}
                    </a>
                  ) : null}
                </span>
              }
              actions={
                <>
                  {ref.status === 'pronto' ? (
                    <Button size="sm" variant="ghost" onClick={() => setOpen(open === ref.id ? null : ref.id)}>
                      {open === ref.id ? 'Fechar' : 'Ver planos'}
                    </Button>
                  ) : null}
                  {renaming === ref.id ? null : (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Pencil}
                      aria-label={`Renomear “${ref.title}”`}
                      onClick={() => {
                        rename.reset();
                        setRenaming(ref.id);
                      }}
                    >
                      Renomear
                    </Button>
                  )}
                  <Button size="sm" icon={RefreshCw} disabled={Boolean(job)} busy={analyze.isPending && analyze.variables === ref.id} onClick={() => analyze.mutate(ref.id)}>
                    Analisar
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Trash2}
                    disabled={Boolean(job)}
                    busy={remove.isPending && remove.variables === ref.id}
                    onClick={async () => {
                      if (await confirm({ title: 'Apagar a referência?', description: 'O vídeo e as folhas de contato saem da biblioteca.', confirmLabel: 'Apagar', tone: 'danger' }))
                        remove.mutate(ref.id);
                    }}
                  >
                    Apagar
                  </Button>
                </>
              }
            >
              {ref.error && ref.status === 'falhou' ? <p className="text-[13px] text-red-300">{ref.error}</p> : null}
              {ref.status === 'pronto' && open === ref.id ? (
                <ReferenceDetail reference={ref} />
              ) : ref.status === 'pronto' ? (
                <p className="text-[13px] text-muted">
                  {ref.stats.cuts_per_min ?? 0} cortes por minuto, plano mediano de {ref.stats.median_s ?? 0} s.
                </p>
              ) : null}
            </Section>
          );
        })}
      </div>
    </Page>
  );
};
