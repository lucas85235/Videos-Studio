/* Painel "Checagem de fatos" da aba Roteiro (ADR 0003, bloco F).
   Cada data, número, nome, preço e citação conferidos numa fonte, com o trecho literal da página conferido pelo app.
   Regra do canal: checagem completa antes de gastar com voz paga. */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ClipboardCopy, Globe, GlobeLock, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import type { Job, ProjectDetail } from '../../../api';
import { factcheckApi, type FactClaim } from '../../../apiFactcheck';
import { Badge, Button, ErrorBox, formatRelative, Loading, Section, useToast } from '../../../ui';
import { isActive, JobProgress, useLiveJob } from '../delivery/shared';
import { ClaimCard } from './ClaimCard';
import { factcheckKey, plural, useFactCheck } from './factcheck';

const NO_WEB = 'Sem busca web: só as fontes do roteiro e as que o modelo indicar';

export const FactCheckPanel: React.FC<{ detail: ProjectDetail; lang?: string; busy?: boolean }> = ({ detail, lang: langProp, busy = false }) => {
  const { doc, project } = detail;
  const projectId = project.id;
  const lang = langProp && doc.languages.includes(langProp) ? langProp : doc.primary_language;
  const client = useQueryClient();
  const { toast } = useToast();
  const q = useFactCheck(projectId, lang, project.current_version);
  const [started, setStarted] = useState<Job | undefined>();

  const serverJob = q.data?.job && isActive(q.data.job) ? q.data.job : undefined;
  const pending = started ?? serverJob ?? detail.jobs.find((j) => j.kind === 'factcheck' && isActive(j));
  const live = useLiveJob(pending);
  const running = live && isActive(live) ? live : undefined;
  useEffect(() => {
    if (live && !isActive(live)) {
      setStarted(undefined);
      void client.invalidateQueries({ queryKey: factcheckKey(projectId) });
      if (live.status === 'failed') toast({ tone: 'bad', title: 'A checagem de fatos falhou', description: live.error ?? undefined });
    }
  }, [live?.status]);

  const run = useMutation({
    mutationFn: (segmentIds?: string[]) => factcheckApi.run(projectId, { lang, ...(segmentIds ? { segment_ids: segmentIds } : {}) }),
    onSuccess: (job) => setStarted(job),
  });

  const copy = useMutation({
    mutationFn: async () => {
      const res = await fetch(factcheckApi.sourcesUrl(projectId, lang));
      if (!res.ok) throw new Error(`Não consegui montar a lista de fontes (erro ${res.status}).`);
      const text = await res.text();
      if (!navigator.clipboard) throw new Error('A área de transferência não está disponível nesta janela.');
      await navigator.clipboard.writeText(text);
    },
    onSuccess: () => toast({ tone: 'good', title: 'Fontes copiadas', description: 'Tabela fala → fonte em Markdown.' }),
  });

  const hasText = doc.segments.some((s) => (s.text[lang] ?? '').trim());
  if (!hasText) return null;

  const data = q.data;
  const s = data?.summary;
  const claims = data?.claims ?? [];
  const checkedOnce = Boolean(data?.checked_at) || claims.length > 0;
  const needsWork = s ? s.open > 0 || s.unchecked > 0 || s.stale > 0 : false;
  const blocked = busy && !running ? 'Aguarde o trabalho em andamento terminar.' : undefined;

  // Afirmações agrupadas na ordem do roteiro; as de trechos apagados vão para o fim. Depois da 1ª checagem, os trechos
  // novos ou editados sem afirmação também aparecem, para checar um a um sem refazer o roteiro todo.
  const unchecked = new Set(checkedOnce ? (s?.unchecked_ids ?? []) : []);
  const groups: { id: string; index: number | null; locked: boolean; unchecked: boolean; claims: FactClaim[] }[] = [];
  doc.segments.forEach((seg, i) => {
    const list = claims.filter((c) => c.segment_id === seg.id);
    if (list.length || unchecked.has(seg.id)) {
      groups.push({ id: seg.id, index: i + 1, locked: seg.locked, unchecked: unchecked.has(seg.id), claims: list });
    }
  });
  const known = new Set(doc.segments.map((seg) => seg.id));
  const orphans = claims.filter((c) => !known.has(c.segment_id));
  if (orphans.length) groups.push({ id: '__apagados', index: null, locked: true, unchecked: false, claims: orphans });

  return (
    <Section
      title="Checagem de fatos"
      icon={ShieldCheck}
      description="Cada data, número, nome, preço e citação conferidos numa fonte, com o trecho achado na página."
      actions={
        <Button
          size="sm"
          variant={checkedOnce ? 'secondary' : 'primary'}
          icon={ShieldCheck}
          busy={run.isPending || Boolean(running)}
          disabled={Boolean(blocked)}
          title={blocked}
          onClick={() => run.mutate(undefined)}
        >
          {checkedOnce ? 'Checar de novo' : 'Checar fatos'}
        </Button>
      }
    >
      {data ? (
        data.web.available ? (
          <Badge tone="info" icon={Globe} title={data.web.reason} className="w-fit">
            Busca web ligada (Claude)
          </Badge>
        ) : (
          <p className="flex items-start gap-1.5 text-[13px] text-subtle" title={data.web.reason}>
            <GlobeLock size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            {NO_WEB}
          </p>
        )
      ) : null}

      {running ? <JobProgress job={running} title="Checagem de fatos" /> : null}
      <ErrorBox error={run.error} title="Não foi possível iniciar a checagem" />
      {q.isLoading ? <Loading /> : null}
      <ErrorBox error={q.error} title="Não foi possível carregar a checagem" onRetry={() => void q.refetch()} />

      {s && checkedOnce ? (
        <div className="flex flex-wrap gap-1.5" aria-label="Resumo da checagem">
          <Badge tone="good">{s.ok} ok</Badge>
          {s.corrigir ? <Badge tone="warn">{s.corrigir} corrigir</Badge> : null}
          {s.sem_fonte ? <Badge tone="bad">{s.sem_fonte} sem fonte</Badge> : null}
          {s.boato ? <Badge className="border-violet-400/35! bg-violet-400/10! text-violet-300!">{s.boato} boato</Badge> : null}
          {s.open ? <Badge tone="warn">{plural(s.open, 'aberta', 'abertas')}</Badge> : null}
          {s.quote_missing ? <Badge tone="bad">{plural(s.quote_missing, 'citação não achada', 'citações não achadas')}</Badge> : null}
          {s.stale ? <Badge tone="warn">{plural(s.stale, 'desatualizada', 'desatualizadas')}</Badge> : null}
          {s.unchecked ? <Badge>{plural(s.unchecked, 'trecho sem checagem', 'trechos sem checagem')}</Badge> : null}
          {s.applied ? <Badge tone="good">{plural(s.applied, 'corrigida', 'corrigidas')}</Badge> : null}
        </div>
      ) : null}
      {data?.checked_at ? (
        <p className="text-xs text-subtle">
          Última checagem {formatRelative(data.checked_at)}
          {data.last_web ? ' (com busca web)' : ''}.
        </p>
      ) : null}

      {s && needsWork ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-[13px] text-amber-100" role="note">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          <span>
            Checagem completa antes de gastar com voz paga.
            {s.open ? ` Falta resolver ${plural(s.open, 'afirmação', 'afirmações')}.` : ''}
            {s.unchecked ? ` ${plural(s.unchecked, 'trecho ainda não foi checado', 'trechos ainda não foram checados')} (marcados abaixo).` : ''}
          </span>
        </div>
      ) : null}

      {data && checkedOnce && !claims.length ? <p className="text-[13px] text-muted">Nenhuma afirmação checável encontrada.</p> : null}

      {groups.map((g) => (
        <div key={g.id} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h3 className="text-[13px] font-medium text-muted">{g.index ? `Trecho ${g.index}` : 'Trechos apagados'}</h3>
            {g.unchecked ? <Badge>{g.claims.length ? 'mudou desde a checagem' : 'ainda não checado'}</Badge> : null}
            {g.index ? (
              <Button
                size="sm"
                variant="ghost"
                icon={RefreshCw}
                className="ml-auto"
                disabled={Boolean(blocked) || Boolean(running)}
                busy={run.isPending && run.variables?.[0] === g.id}
                onClick={() => run.mutate([g.id])}
                title="Checa só este trecho (substitui as afirmações dele)"
              >
                Checar o trecho
              </Button>
            ) : null}
          </div>
          {g.claims.length ? (
            <ul className="flex flex-col gap-2">
              {g.claims.map((c) => (
                <ClaimCard key={c.id} claim={c} projectId={projectId} locked={g.locked} busy={busy} />
              ))}
            </ul>
          ) : null}
        </div>
      ))}

      {claims.length ? (
        <div className="-mx-1 border-t border-line pt-2">
          <Button size="sm" variant="ghost" icon={ClipboardCopy} busy={copy.isPending} onClick={() => copy.mutate()}>
            Copiar fontes
          </Button>
          <ErrorBox error={copy.error} title="Não foi possível copiar" />
        </div>
      ) : null}
    </Section>
  );
};
