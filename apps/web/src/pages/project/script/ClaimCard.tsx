/* Uma afirmação da checagem de fatos: veredito, trecho → correção, fonte, citação conferida e as ações. */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Check, CircleHelp, ExternalLink, EyeOff, Pencil, RefreshCw, RotateCcw, TriangleAlert, Wand2, X } from 'lucide-react';
import React, { useState } from 'react';
import { factcheckApi, type FactClaim, type FactClaimPatch, type FactVerdict } from '../../../apiFactcheck';
import { Badge, Button, ErrorBox, Field, Input, Select, Textarea, useToast } from '../../../ui';
import { factcheckKey, VERDICT_INFO, VerdictBadge } from './factcheck';

const QuoteStatus: React.FC<{ claim: FactClaim }> = ({ claim }) => {
  if (!claim.quote) return null;
  const { quote_found: found } = claim;
  return (
    <div className="flex flex-col gap-1">
      <blockquote className="border-l-2 border-line-strong pl-2 text-[13px] italic text-muted [overflow-wrap:anywhere]">“{claim.quote}”</blockquote>
      <div className="flex flex-wrap items-center gap-1.5">
        {found === true ? (
          <Badge tone="good" icon={Check}>
            Citação achada
          </Badge>
        ) : found === false ? (
          <Badge tone="bad" icon={X}>
            Citação não achada
          </Badge>
        ) : (
          <Badge icon={CircleHelp}>Não conferida</Badge>
        )}
        {claim.check_note ? <span className="text-xs text-subtle">{claim.check_note}</span> : null}
      </div>
    </div>
  );
};

type EditState = { verdict: FactVerdict; source_title: string; url: string; quote: string; correction: string; note: string };

const editStateOf = (c: FactClaim): EditState => ({
  verdict: c.verdict,
  source_title: c.source_title,
  url: c.url,
  quote: c.quote,
  correction: c.correction,
  note: c.note,
});

const EditForm: React.FC<{ claim: FactClaim; projectId: string; onDone: () => void }> = ({ claim, projectId, onDone }) => {
  const client = useQueryClient();
  const [form, setForm] = useState<EditState>(() => editStateOf(claim));
  const set = <K extends keyof EditState>(key: K, value: EditState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const save = useMutation({
    mutationFn: async () => {
      const patch: FactClaimPatch = {};
      (Object.keys(form) as (keyof EditState)[]).forEach((k) => {
        if (form[k] !== claim[k]) (patch as Record<string, string>)[k] = form[k];
      });
      const saved = await factcheckApi.update(projectId, claim.id, patch);
      // Link ou trecho novo: confere de novo na hora.
      const relink = (patch.url !== undefined || patch.quote !== undefined) && saved.url && saved.quote;
      return relink ? factcheckApi.verify(projectId, claim.id) : saved;
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: factcheckKey(projectId) });
      onDone();
    },
  });
  return (
    <form
      className="flex flex-col gap-2 rounded-lg border border-line bg-canvas/40 p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="Veredito">
        <Select value={form.verdict} onChange={(e) => set('verdict', e.target.value as FactVerdict)}>
          {(Object.keys(VERDICT_INFO) as FactVerdict[]).map((v) => (
            <option key={v} value={v}>
              {VERDICT_INFO[v].label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Fonte">
        <Input value={form.source_title} maxLength={300} onChange={(e) => set('source_title', e.target.value)} placeholder="Nome do veículo ou documento" />
      </Field>
      <Field label="Link da fonte">
        <Input value={form.url} type="url" onChange={(e) => set('url', e.target.value)} placeholder="https://" />
      </Field>
      <Field label="Citação da fonte" hint="Copie o texto exato da página, de 5 a 30 palavras (aspas curvas e retas contam).">
        <Textarea value={form.quote} rows={2} onChange={(e) => set('quote', e.target.value)} />
      </Field>
      <Field label="Correção" hint="Texto novo para o trecho do roteiro.">
        <Textarea value={form.correction} rows={2} onChange={(e) => set('correction', e.target.value)} />
      </Field>
      <Field label="Nota">
        <Textarea value={form.note} rows={2} onChange={(e) => set('note', e.target.value)} />
      </Field>
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancelar
        </Button>
        <Button size="sm" variant="primary" type="submit" busy={save.isPending}>
          Salvar
        </Button>
      </div>
      <ErrorBox error={save.error} title="Não foi possível salvar" />
    </form>
  );
};

export const ClaimCard: React.FC<{ claim: FactClaim; projectId: string; locked: boolean; busy: boolean }> = ({ claim, projectId, locked, busy }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: factcheckKey(projectId) });

  const verify = useMutation({ mutationFn: () => factcheckApi.verify(projectId, claim.id), onSuccess: refresh });
  const setStatus = useMutation({
    mutationFn: (status: 'aberta' | 'ignorada') => factcheckApi.update(projectId, claim.id, { status }),
    onSuccess: refresh,
  });
  const apply = useMutation({
    mutationFn: () => factcheckApi.apply(projectId, claim.id),
    onSuccess: async (res) => {
      await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['project', projectId] })]);
      toast({ tone: 'good', title: 'Correção aplicada', description: `O roteiro ganhou a versão ${res.version}.` });
    },
  });

  const open = claim.status === 'aberta';
  const canApply = open && claim.verdict !== 'ok' && Boolean(claim.correction.trim()) && Boolean(claim.excerpt);
  const applyBlocked = locked ? 'O trecho está travado: destrave para aplicar.' : busy ? 'Aguarde o trabalho em andamento terminar.' : claim.stale ? 'O trecho mudou desde a checagem: confira antes de aplicar.' : undefined;
  const dim = claim.status !== 'aberta';

  return (
    <li className={`flex flex-col gap-2 rounded-lg border border-line p-2.5 ${dim ? 'opacity-70' : ''}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <VerdictBadge verdict={claim.verdict} />
        {claim.status === 'aplicada' ? (
          <Badge tone="good" icon={Check}>
            Aplicada
          </Badge>
        ) : null}
        {claim.status === 'ignorada' ? <Badge icon={EyeOff}>Ignorada</Badge> : null}
        {claim.stale ? (
          <Badge tone="warn" icon={TriangleAlert} title="O texto do trecho mudou depois da checagem: cheque o trecho de novo.">
            Desatualizada
          </Badge>
        ) : null}
      </div>
      <p className="text-sm text-fg [overflow-wrap:anywhere]">{claim.claim}</p>

      {claim.excerpt ? (
        <div className="flex flex-col gap-1 text-[13px]">
          <span className={claim.correction && claim.status !== 'aplicada' ? 'text-muted line-through decoration-red-300/60' : 'text-muted'}>
            «{claim.excerpt}»
          </span>
          {claim.correction ? (
            <span className="flex items-start gap-1 text-fg">
              <ArrowRight size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
              <span className="sr-only">Correção:</span>
              <span className="[overflow-wrap:anywhere]">{claim.correction}</span>
            </span>
          ) : null}
        </div>
      ) : claim.correction ? (
        <p className="text-[13px] text-fg">
          <span className="text-muted">Sugestão: </span>
          {claim.correction}
        </p>
      ) : null}

      {/^https?:\/\//i.test(claim.url) ? (
        <a className="inline-flex items-start gap-1 text-[13px] text-sky-300 hover:underline [overflow-wrap:anywhere]" href={claim.url} target="_blank" rel="noreferrer">
          {claim.source_title || claim.url}
          <ExternalLink size={12} aria-hidden="true" className="mt-1 shrink-0" />
          <span className="sr-only">(abre em nova aba)</span>
        </a>
      ) : claim.source_title || claim.url ? (
        <span className="text-[13px] text-fg [overflow-wrap:anywhere]">{claim.source_title || claim.url}</span>
      ) : (
        <span className="text-[13px] text-red-300">Sem link da fonte</span>
      )}
      <QuoteStatus claim={claim} />
      {claim.note ? <p className="text-xs text-subtle [overflow-wrap:anywhere]">{claim.note}</p> : null}

      {editing ? (
        <EditForm claim={claim} projectId={projectId} onDone={() => setEditing(false)} />
      ) : (
        <div className="-mx-1 flex flex-wrap gap-1">
          {canApply ? (
            <Button
              size="sm"
              variant="secondary"
              icon={Wand2}
              busy={apply.isPending}
              disabled={Boolean(applyBlocked)}
              title={applyBlocked ?? 'Troca o trecho pela correção no roteiro (dá para desfazer pelas versões).'}
              onClick={() => apply.mutate()}
            >
              Aplicar correção
            </Button>
          ) : null}
          {claim.url && claim.quote ? (
            <Button size="sm" variant="ghost" icon={RefreshCw} busy={verify.isPending} onClick={() => verify.mutate()} title="Abre a página de novo e procura a citação">
              Reconferir
            </Button>
          ) : null}
          {claim.status !== 'aplicada' ? (
            <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditing(true)}>
              Editar
            </Button>
          ) : null}
          {open ? (
            <Button size="sm" variant="ghost" icon={EyeOff} busy={setStatus.isPending} onClick={() => setStatus.mutate('ignorada')}>
              Ignorar
            </Button>
          ) : claim.status === 'ignorada' ? (
            <Button size="sm" variant="ghost" icon={RotateCcw} busy={setStatus.isPending} onClick={() => setStatus.mutate('aberta')}>
              Reabrir
            </Button>
          ) : null}
        </div>
      )}
      <ErrorBox error={apply.error ?? verify.error ?? setStatus.error} title="Não foi possível concluir" />
    </li>
  );
};
