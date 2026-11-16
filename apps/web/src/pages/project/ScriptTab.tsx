import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  CircleCheck,
  Copy,
  ExternalLink,
  FileText,
  Languages,
  LayoutGrid,
  Lock,
  LockOpen,
  RotateCcw,
  Save,
  ShieldAlert,
  Trash2,
  TriangleAlert,
  Wand2,
} from 'lucide-react';
import React, { useState } from 'react';
import { api, type ProjectDetail, type Segment } from '../../api';
import { label, langLabel, ROLE_LABELS } from '../../labels';
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBox,
  Field,
  Input,
  ProgressBar,
  Section,
  Select,
  Skeleton,
  Spinner,
  Textarea,
  useConfirm,
} from '../../ui';
import { approveKey, useApproving, useJobStarting, useStartJob } from '../ProjectPage';
import { useFactCheck } from './script/factcheck';
import { FactCheckPanel } from './script/FactCheckPanel';
import { LessonsUsedPanel } from './script/LessonsUsedPanel';
import {
  fieldDrafts,
  isOrphanScriptDraft,
  parseScriptDraftKey,
  promptDrafts,
  scriptDraftKey,
  scriptDrafts,
  useKeyedStore,
  type Draft,
} from './core/drafts';
import {
  checkDuration,
  DOC_BUSY_REASON,
  docJobRunning,
  estimateSeconds,
  formatDuration,
  gatesOf,
  isActive,
  screenTextsMissing,
  STYLE_MISSING_REASON,
} from './core/model';
import type { TabNavigation } from './types';

const BUSY_REASON = 'Aguarde o trabalho em andamento terminar.';
const SAVE_BLOCKED = 'Aguarde o trabalho em andamento: ele regravaria o roteiro e apagaria esta edição. Seu texto continua guardado aqui.';
const WRITING_BLOCKED = 'Um roteiro novo está sendo gerado e vai substituir este. Seu texto continua guardado aqui.';

/* ------------------------------------------------------------------ */
/* Tema e orientações                                                  */
/* ------------------------------------------------------------------ */

const BriefCard: React.FC<{ detail: ProjectDetail; defaultOpen?: boolean }> = ({ detail, defaultOpen }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const fields = useKeyedStore(fieldDrafts, doc.id);
  const topicDraft = fields.topic;
  const briefDraft = fields.brief;
  const dirty = Boolean(topicDraft || briefDraft);
  const docBusy = docJobRunning(detail);
  const setField = (key: 'topic' | 'brief', value: string, server: string) =>
    fieldDrafts.set(doc.id, key, value === server ? null : { value, base: fields[key]?.base ?? server });
  const clear = (snapshot?: { topic?: string; brief?: string }) =>
    fieldDrafts.update(doc.id, (current) => {
      const next = { ...current };
      for (const key of ['topic', 'brief'] as const) {
        if (!snapshot || (snapshot[key] !== undefined && next[key]?.value === snapshot[key])) delete next[key];
      }
      return next;
    });
  const save = useMutation({
    mutationFn: (snapshot: { topic?: string; brief?: string }) =>
      api.put(`/api/projects/${doc.id}/doc`, {
        doc: { ...doc, topic: snapshot.topic ?? doc.topic, brief: snapshot.brief ?? doc.brief },
        message: 'Briefing editado',
      }),
    // Espera o documento novo chegar antes de tirar o rascunho (senão o campo mostra por um instante o texto antigo).
    onSuccess: async (_r, snapshot) => {
      await client.invalidateQueries({ queryKey: ['project', doc.id] });
      clear(snapshot);
    },
  });
  const discard = async () => {
    const ok = await confirm({
      title: 'Descartar as alterações do tema e das orientações?',
      description: 'O tema e as orientações voltam para o que está salvo. Não dá para desfazer.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (ok) {
      save.reset();
      clear();
    }
  };
  return (
    <details open={defaultOpen || dirty || undefined} className="group rounded-xl border border-line bg-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 font-display text-[15px] font-semibold text-fg hover:bg-raised [&::-webkit-details-marker]:hidden">
        Tema e orientações
        {dirty ? <Badge tone="warn">Não salvo</Badge> : null}
        <ChevronDown size={16} aria-hidden="true" className="ml-auto shrink-0 text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="flex flex-col gap-3 border-t border-line p-4">
        <Field label="Tema">
          <Input value={topicDraft?.value ?? doc.topic} onChange={(e) => setField('topic', e.target.value, doc.topic)} />
        </Field>
        <Field label="Orientações" hint="O que o roteiro deve cobrir, o tom, o que evitar. Vale para o próximo roteiro gerado.">
          <Textarea
            rows={4}
            value={briefDraft?.value ?? doc.brief}
            onChange={(e) => setField('brief', e.target.value, doc.brief)}
            className="field-sizing-content min-h-24"
          />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            icon={Save}
            disabled={!dirty || docBusy}
            busy={save.isPending}
            onClick={() => save.mutate({ topic: topicDraft?.value, brief: briefDraft?.value })}
          >
            Salvar
          </Button>
          {dirty ? (
            <Button size="sm" variant="ghost" onClick={() => void discard()}>
              Descartar
            </Button>
          ) : null}
        </div>
        {dirty && docBusy ? <p className="text-[13px] text-amber-200">{SAVE_BLOCKED}</p> : null}
        <ErrorBox error={save.error} title="Não foi possível salvar o tema e as orientações" />
      </div>
    </details>
  );
};

/* ------------------------------------------------------------------ */
/* Trecho do roteiro                                                   */
/* ------------------------------------------------------------------ */

const SegmentCard: React.FC<{
  seg: Segment;
  index: number;
  lang: string;
  primary: boolean;
  draft: Draft | undefined;
  hasAnyDraft: boolean;
  seconds: number;
  hookMax: number | null;
  readOnly: boolean;
  onDraft: (value: string) => void;
  onUseServer: () => void;
  onKeepMine: () => void;
  onLock: () => void;
  lockBusy: boolean;
  lockBlocked: string | null;
  /** Afirmações abertas da checagem de fatos neste trecho. */
  factOpen?: number;
}> = ({ seg, index, lang, primary, draft, hasAnyDraft, seconds, hookMax, readOnly, onDraft, onUseServer, onKeepMine, onLock, lockBusy, lockBlocked, factOpen = 0 }) => {
  const server = seg.text[lang] ?? '';
  const value = draft?.value ?? server;
  const conflict = draft && draft.base !== server;
  const longHook = seg.role === 'hook' && hookMax != null && seconds > hookMax + 0.5;
  const lockTitle = lockBlocked
    ? lockBlocked
    : seg.locked
      ? 'Travado: o agente de edição não altera este trecho e a edição manual fica bloqueada. Gerar outro roteiro e a tradução automática substituem até os trechos travados.'
      : hasAnyDraft
        ? 'Salve as alterações deste trecho antes de travar'
        : 'Travar: o agente de edição deixa de alterar este trecho e a edição manual fica bloqueada. A tradução automática também substitui o texto traduzido.';
  return (
    <Card className={`flex flex-col gap-2 p-3 ${draft ? 'border-amber-300/45!' : ''}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-medium text-muted">Trecho {index}</span>
        {/* Papel do trecho é informação, não aviso: âmbar só para "Gancho longo" e "Não salvo". */}
        <Badge tone={seg.role === 'cta' ? 'info' : 'neutral'}>{label(ROLE_LABELS, seg.role)}</Badge>
        {seg.chapter ? <Badge>{seg.chapter}</Badge> : null}
        {seg.speaker !== 'narrator' ? <Badge tone="info">Voz: {seg.speaker}</Badge> : null}
        {seconds > 0 ? <span className="text-xs tabular-nums text-subtle">~{formatDuration(seconds)}</span> : null}
        {longHook ? (
          <Badge tone="warn" icon={TriangleAlert} title={`O estilo pede um gancho de até ${formatDuration(hookMax ?? 0)}`}>
            Gancho longo
          </Badge>
        ) : null}
        {factOpen ? (
          <Badge tone="warn" icon={ShieldAlert} title="Afirmações da checagem de fatos ainda abertas neste trecho (veja o painel Checagem de fatos)">
            {factOpen === 1 ? '1 fato a resolver' : `${factOpen} fatos a resolver`}
          </Badge>
        ) : null}
        {seg.locked ? <Badge icon={Lock}>Travado</Badge> : null}
        {draft ? <Badge tone="warn">Não salvo</Badge> : null}
        {/* Nome fixo + aria-pressed: o estado vem do "pressionado", não de um rótulo que muda. */}
        <Button
          size="sm"
          variant="ghost"
          icon={seg.locked ? Lock : LockOpen}
          aria-pressed={seg.locked}
          aria-label={`Travar o trecho ${index}`}
          title={lockTitle}
          busy={lockBusy}
          disabled={Boolean(lockBlocked) || (!seg.locked && hasAnyDraft)}
          onClick={onLock}
          className={`ml-auto ${seg.locked ? 'text-fg' : ''}`}
        >
          Travar
        </Button>
      </div>
      {conflict ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="shrink-0 text-amber-200" />
          <span className="min-w-0 flex-1">Este trecho mudou no servidor enquanto você editava. Sua versão foi mantida.</span>
          <Button size="sm" variant="ghost" onClick={onUseServer}>
            Usar a do servidor
          </Button>
          <Button size="sm" variant="ghost" onClick={onKeepMine}>
            Manter a minha
          </Button>
        </div>
      ) : null}
      <Textarea
        aria-label={`Texto do trecho ${index} (${langLabel(lang)})`}
        className="field-sizing-content min-h-16 w-full"
        rows={Math.max(2, Math.ceil(value.length / 95))}
        value={value}
        disabled={seg.locked}
        readOnly={readOnly}
        placeholder={primary ? 'Escreva o texto deste trecho' : 'Sem tradução ainda'}
        onChange={(e) => onDraft(e.target.value)}
      />
    </Card>
  );
};

/* ------------------------------------------------------------------ */
/* Rascunhos de trechos que não existem mais                           */
/* ------------------------------------------------------------------ */

const OrphanDrafts: React.FC<{ projectId: string; entries: [string, Draft][]; multiLang: boolean }> = ({ projectId, entries, multiLang }) => {
  const confirm = useConfirm();
  const [copied, setCopied] = useState(false);
  const text = entries.map(([, d]) => d.value.trim()).join('\n\n');
  const copy = () => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };
  const discard = async () => {
    const ok = await confirm({
      title: entries.length === 1 ? 'Descartar este texto?' : `Descartar estes ${entries.length} textos?`,
      description: 'O texto que você digitou nesses trechos some. Não dá para desfazer.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (ok)
      scriptDrafts.update(projectId, (current) => {
        const next = { ...current };
        for (const [key] of entries) delete next[key];
        return next;
      });
  };
  return (
    <div role="status" className="flex flex-col gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm">
      <div className="flex items-start gap-2">
        <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-fg">
            {entries.length === 1 ? '1 trecho editado não existe mais nesta versão' : `${entries.length} trechos editados não existem mais nesta versão`}
          </div>
          <p className="text-[13px] text-amber-100">
            O roteiro mudou (versão restaurada, roteiro novo ou edição do agente). O que você digitou continua guardado: copie o que quiser aproveitar e descarte.
          </p>
        </div>
      </div>
      <ul className="flex flex-col gap-1.5">
        {entries.map(([key, d]) => (
          <li key={key} className="rounded-md border border-line bg-canvas/60 px-2.5 py-1.5 text-[13px] text-muted">
            {multiLang ? <span className="mr-1 text-subtle">{langLabel(parseScriptDraftKey(key).lang)}:</span> : null}
            <span className="line-clamp-3 whitespace-pre-wrap break-words">{d.value}</span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" icon={copied ? Check : Copy} onClick={copy}>
          {copied ? 'Copiado' : 'Copiar texto'}
        </Button>
        <Button size="sm" variant="ghost" icon={Trash2} onClick={() => void discard()}>
          Descartar
        </Button>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Aba                                                                 */
/* ------------------------------------------------------------------ */

type JobOrigin = 'empty' | 'strip' | 'more';

export const ScriptTab: React.FC<{ detail: ProjectDetail; busy: boolean } & TabNavigation> = ({ detail, busy: pageBusy }) => {
  // A checagem de fatos não regrava o documento (pode levar até 30 min com busca web): não trava o roteiro.
  const busy = pageBusy && detail.jobs.some((j) => isActive(j) && j.kind !== 'factcheck');
  const { doc, style } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const startJob = useStartJob(doc.id);
  const starting = useJobStarting(doc.id);
  const approving = useApproving(doc.id, 'script');
  const [origin, setOrigin] = useState<JobOrigin | null>(null);
  const [langState, setLang] = useState(doc.primary_language);
  const lang = doc.languages.includes(langState) ? langState : doc.primary_language;
  // Mesma consulta do painel de checagem: os selos dos trechos mostram as afirmações ainda abertas.
  const factOpen = useFactCheck(doc.id, lang, detail.project.current_version).data?.summary.open_by_segment ?? {};
  const drafts = useKeyedStore(scriptDrafts, doc.id);
  const promptBucket = useKeyedStore(promptDrafts, doc.id);
  const promptCount = Object.keys(promptBucket).length;
  // Rascunhos de trechos que sumiram ficam guardados (nunca são descartados em silêncio) e aparecem num aviso próprio.
  const orphanEntries = Object.entries(drafts).filter(([key]) => isOrphanScriptDraft(doc, key));
  const liveDrafts: Record<string, Draft> = Object.fromEntries(Object.entries(drafts).filter(([key]) => !isOrphanScriptDraft(doc, key)));
  const draftKeys = Object.keys(liveDrafts);
  const changed = draftKeys.length > 0;
  const docBusy = docJobRunning(detail);

  const startKind = (kind: string, from: JobOrigin, payload?: Record<string, unknown>) => {
    if (starting) return;
    setOrigin(from);
    startJob.mutate({ kind, payload });
  };
  const jobBusy = (kind: string, from: JobOrigin) => startJob.isPending && origin === from && startJob.variables?.kind === kind;
  const jobError = (from: JobOrigin) => (origin === from ? startJob.error : null);

  const setDraft = (seg: Segment, value: string) => {
    const key = scriptDraftKey(lang, seg.id);
    const server = seg.text[lang] ?? '';
    scriptDrafts.set(doc.id, key, value === server ? null : { value, base: drafts[key]?.base ?? server });
  };

  const save = useMutation({
    mutationFn: (snapshot: Record<string, Draft>) => {
      const next = structuredClone(doc);
      for (const [key, d] of Object.entries(snapshot)) {
        const { lang: l, segmentId } = parseScriptDraftKey(key);
        const seg = next.segments.find((s) => s.id === segmentId);
        if (seg) seg.text[l] = d.value;
      }
      return api.put(`/api/projects/${doc.id}/doc`, { doc: next, message: 'Roteiro editado à mão' });
    },
    onSuccess: async (_r, snapshot) => {
      // Espera o documento novo: tirar o rascunho antes faria a caixa mostrar o texto antigo por um instante.
      await client.invalidateQueries({ queryKey: ['project', doc.id] });
      // Só sai o que foi salvo (se a pessoa continuou digitando, o texto novo fica como rascunho).
      scriptDrafts.update(doc.id, (current) => {
        const next = { ...current };
        for (const [key, d] of Object.entries(snapshot)) if (next[key]?.value === d.value) delete next[key];
        return next;
      });
    },
  });
  const approve = useMutation({
    mutationKey: approveKey(doc.id, 'script'),
    mutationFn: () => api.post(`/api/projects/${doc.id}/approve`, { stage: 'script', continue_pipeline: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });
  const lock = useMutation({
    mutationFn: ({ id, locked }: { id: string; locked: boolean }) =>
      api.post(`/api/projects/${doc.id}/ops`, { ops: [{ op: 'set_lock', ids: [id], locked }], message: locked ? 'Trecho travado' : 'Trecho destravado' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });

  const saveAndApprove = async () => {
    if (approving) return;
    try {
      await save.mutateAsync(liveDrafts);
      approve.mutate();
    } catch {
      /* o erro do save aparece no ErrorBox */
    }
  };

  const discardAll = async () => {
    const ok = await confirm({
      title: `Descartar ${draftKeys.length === 1 ? 'a alteração' : `as ${draftKeys.length} alterações`}?`,
      description: 'O texto que você digitou e não salvou volta para o que está salvo. Não dá para desfazer.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });
    if (ok) {
      save.reset();
      scriptDrafts.update(doc.id, (current) => Object.fromEntries(Object.entries(current).filter(([key]) => !(key in liveDrafts))));
    }
  };

  const askUseServer = async (key: string) => {
    const ok = await confirm({
      title: 'Usar o texto do servidor?',
      description: 'O texto que você digitou neste trecho some e volta o que está salvo. Não dá para desfazer.',
      confirmLabel: 'Usar o do servidor',
      tone: 'danger',
    });
    if (ok) scriptDrafts.set(doc.id, key, null);
  };

  const promptNote = promptCount
    ? ` ${promptCount === 1 ? 'O prompt de cena não salvo se perde' : `Os ${promptCount} prompts de cena não salvos se perdem`}.`
    : '';

  const regenerate = async () => {
    const ok = await confirm({
      title: 'Gerar outro roteiro?',
      description: (
        <>
          Gera um roteiro novo do zero: substitui <strong className="text-fg">todos</strong> os trechos (inclusive os travados), apaga as cenas e tira a
          aprovação. O título e as fontes também são trocados pelos do novo roteiro.
          {changed
            ? ` Suas ${draftKeys.length === 1 ? 'alteração não salva fica guardada' : `${draftKeys.length} alterações não salvas ficam guardadas`} para você copiar ou descartar.`
            : ''}
          {promptNote} Dá para voltar em Versões. Usa o Claude.
        </>
      ),
      confirmLabel: 'Gerar outro roteiro',
      tone: 'danger',
    });
    // Os rascunhos não saem aqui: se o job falhar, o texto continua; se der certo, eles viram "trechos que não existem mais".
    if (ok) startKind('script', 'more');
  };

  const gateScript = detail.project.settings.gates?.script ?? true;
  const redoStoryboard = async () => {
    if (doc.scenes.length) {
      const ok = await confirm({
        title: 'Refazer as cenas?',
        description: `Cria as cenas de novo a partir do roteiro: substitui as cenas atuais e os ajustes feitos nelas.${promptNote} Dá para voltar em Versões. Usa o Claude.`,
        confirmLabel: 'Refazer cenas',
      });
      if (!ok) return;
    } else if (!doc.script_approved && gateScript) {
      const ok = await confirm({
        title: 'Criar as cenas sem aprovar o roteiro?',
        description: 'O roteiro ainda não foi aprovado. Criar as cenas agora pula a sua revisão. Usa o Claude.',
        confirmLabel: 'Criar as cenas',
      });
      if (!ok) return;
    }
    startKind('storyboard', 'more');
  };

  const translatedCount = (l: string) => doc.segments.filter((s) => (s.text[l] ?? '').trim()).length;
  const otherLangs = doc.languages.filter((l) => l !== doc.primary_language);

  const translate = async (langs: string[], from: JobOrigin) => {
    const withText = langs.filter((l) => translatedCount(l) > 0);
    if (withText.length) {
      const names = withText.map((l) => langLabel(l)).join(', ');
      const ok = await confirm({
        title: 'Refazer a tradução?',
        description: `Traduz de novo todos os trechos em ${names}: substitui as traduções atuais, inclusive as que você corrigiu e as dos trechos travados. Dá para voltar em Versões. Usa o Claude.`,
        confirmLabel: 'Traduzir',
        tone: 'danger',
      });
      if (!ok) return;
    }
    startKind('translate', from, { langs });
  };

  const activeJobs = detail.jobs.filter(isActive);
  const writing = activeJobs.find((j) => j.kind === 'script' || (j.kind === 'autopilot' && !doc.segments.length));
  const translating = activeJobs.find((j) => j.kind === 'translate');
  const jobsBlocked = busy || starting;

  /* ---------- Sem roteiro ---------- */
  if (!doc.segments.length) {
    if (writing) {
      return (
        <div className="grid gap-4 @5xl:grid-cols-[minmax(0,1fr)_300px]">
          <Card className="flex flex-col gap-4" role="status">
            <div className="flex items-start gap-3">
              <Spinner className="mt-1 text-accent" />
              <div className="min-w-0">
                <h2 className="font-display text-[15px] font-semibold text-fg">O Claude está escrevendo o roteiro…</h2>
                <p className="text-[13px] text-muted">{writing.message || 'Pesquisando o tema e montando os trechos.'}</p>
                <p className="mt-1 text-[13px] text-subtle">Leva de 1 a 2 minutos. Você pode sair desta página: o trabalho continua.</p>
              </div>
            </div>
            <div className="flex flex-col gap-3">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16 rounded-xl" />
              ))}
            </div>
          </Card>
          <BriefCard detail={detail} />
        </div>
      );
    }
    return (
      <div className="grid gap-4 @5xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-3">
          {orphanEntries.length ? <OrphanDrafts projectId={doc.id} entries={orphanEntries} multiLang={doc.languages.length > 1} /> : null}
          <Empty
            icon={FileText}
            title="Ainda não há roteiro"
            action={
              <div className="flex flex-col items-center gap-1.5">
                <Button
                  variant="primary"
                  icon={Wand2}
                  busy={jobBusy('script', 'empty')}
                  disabled={jobsBlocked && !jobBusy('script', 'empty')}
                  title={busy ? BUSY_REASON : undefined}
                  onClick={() => startKind('script', 'empty')}
                >
                  Gerar roteiro
                </Button>
                <span className="text-[13px] text-subtle">Usa o Claude.</span>
              </div>
            }
          >
            O Claude escreve o roteiro a partir do tema e das orientações. Confira os dois ao lado antes de gerar.
            <ErrorBox error={jobError('empty')} className="mt-3 text-left" />
          </Empty>
        </div>
        <BriefCard detail={detail} defaultOpen />
      </div>
    );
  }

  /* ---------- Com roteiro ---------- */
  const textOf = (seg: Segment, l: string) => drafts[scriptDraftKey(l, seg.id)]?.value ?? seg.text[l] ?? '';
  const texts = doc.segments.map((s) => textOf(s, lang));
  const seconds = estimateSeconds(style, lang, texts);
  const words = texts.reduce((n, t) => n + t.split(/\s+/).filter(Boolean).length, 0);
  const durationCheck = checkDuration(style, seconds);
  const target = style?.format?.duration?.target_s ?? null;
  const hookMax = style?.script?.hook_max_s ?? null;
  // Duração real (narração gerada), para não confundir com a estimada pelo texto.
  const builtFinal = detail.outputs.final?.langs?.[lang];
  const built = builtFinal ?? detail.outputs.preview?.langs?.[lang];
  const builtIsFinal = Boolean(builtFinal?.duration);

  const missing = lang !== doc.primary_language ? doc.segments.length - translatedCount(lang) : 0;
  // Narração traduzida, mas cartões e sobreposições de cenas criadas depois ainda só no idioma principal.
  const screenMissing = lang !== doc.primary_language && !missing ? screenTextsMissing(doc, lang) : 0;
  // Termos do glossário do canal que a tradução deste idioma não respeitou (o servidor confere a cada abertura).
  const glossaryWarnings = (detail.glossary_warnings ?? []).filter((w) => w.lang === lang);
  const conflicts = Object.entries(liveDrafts).filter(([key, d]) => {
    const { lang: l, segmentId } = parseScriptDraftKey(key);
    const seg = doc.segments.find((s) => s.id === segmentId);
    return seg && (seg.text[l] ?? '') !== d.base;
  }).length;
  const otherLangDrafts = draftKeys.filter((k) => parseScriptDraftKey(k).lang !== lang).length;
  const toFinal = detail.project.settings.gates?.preview === false;
  // Com a parada das cenas, aprovar o roteiro só cria as cenas e espera a conferência (sem cota de voz e de imagens).
  // Sem cenas, as que o piloto criar chegam sem aprovação (um "Gerar outro roteiro" apaga as cenas aprovadas).
  const stopAtScenes = gatesOf(detail).storyboard && (!doc.scenes.length || !doc.storyboard_approved);
  const afterApprove = stopAtScenes
    ? 'cria as cenas e para para você conferir antes de gerar as imagens'
    : toFinal
      ? 'cria as cenas, narra, monta a prévia e renderiza o vídeo'
      : 'cria as cenas, narra e monta a prévia';
  const approveCost = stopAtScenes ? 'Usa o Claude.' : 'Usa o Claude e a cota de voz e de imagens.';
  const saveBlocked = writing ? WRITING_BLOCKED : docBusy ? SAVE_BLOCKED : null;
  const lockBlocked = !style ? STYLE_MISSING_REASON : docBusy ? DOC_BUSY_REASON : lock.isPending ? 'Aguarde: salvando a trava de outro trecho.' : null;

  let approval: React.ReactNode;
  if (doc.script_approved) {
    approval = (
      <div className="flex flex-col gap-2">
        <Badge tone="good" icon={CircleCheck} className="w-fit">
          Roteiro aprovado
        </Badge>
        {/* O próximo passo é da faixa no topo da página: aqui só o efeito de mexer no texto. */}
        {doc.scenes.length ? <p className="text-[13px] text-muted">Se mudar o texto, gere a prévia de novo para ouvir.</p> : null}
      </div>
    );
  } else if (changed) {
    approval = (
      <div className="flex flex-col gap-1.5">
        <Button
          icon={Check}
          busy={save.isPending || approving}
          disabled={busy || Boolean(saveBlocked)}
          title={busy ? BUSY_REASON : undefined}
          onClick={() => void saveAndApprove()}
        >
          Salvar e aprovar
        </Button>
        <p className="text-[13px] leading-snug text-subtle">
          Salve as alterações para aprovar. Ao aprovar, o app {afterApprove}. {approveCost}
        </p>
      </div>
    );
  } else {
    approval = (
      <div className="flex flex-col gap-1.5">
        <Button
          variant="primary"
          icon={Check}
          busy={approving}
          disabled={busy}
          title={busy ? BUSY_REASON : undefined}
          onClick={() => {
            if (!approving) approve.mutate();
          }}
        >
          {stopAtScenes ? 'Aprovar e criar as cenas' : toFinal ? 'Aprovar e gerar o vídeo' : 'Aprovar e gerar a prévia'}
        </Button>
        <p className="text-[13px] leading-snug text-subtle">
          {stopAtScenes
            ? 'Cria as cenas e para para você conferir antes de gerar as imagens.'
            : toFinal
              ? 'Cria as cenas, narra, monta a prévia e renderiza o vídeo final.'
              : 'Cria as cenas, narra e monta a prévia.'}{' '}
          {approveCost}
        </p>
      </div>
    );
  }

  return (
    <div className="grid items-start gap-4 @5xl:grid-cols-[minmax(0,1fr)_300px]">
      <div className="flex min-w-0 flex-col gap-3">
        {writing ? (
          <div role="status" className="flex items-center gap-2 rounded-lg border border-sky-400/35 bg-sky-400/10 px-3 py-2 text-sm text-sky-100">
            <Spinner className="text-sky-300" />
            Gerando um roteiro novo. Quando terminar, ele substitui este; até lá o texto fica só para leitura.
          </div>
        ) : null}
        {orphanEntries.length ? <OrphanDrafts projectId={doc.id} entries={orphanEntries} multiLang={doc.languages.length > 1} /> : null}
        {conflicts > 0 ? (
          <div role="status" className="flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-sm text-amber-100">
            <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
            O roteiro mudou no servidor; {conflicts === 1 ? 'sua alteração foi mantida' : `suas ${conflicts} alterações foram mantidas`}. Confira os trechos marcados.
          </div>
        ) : null}
        {missing > 0 ? (
          <div className="flex flex-col gap-2 rounded-lg border border-line-strong bg-card px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Languages size={16} aria-hidden="true" className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 text-muted">
                {translating ? 'Traduzindo…' : `Faltam ${missing} ${missing === 1 ? 'tradução' : 'traduções'} em ${langLabel(lang)}.`}
              </span>
              {!translating ? (
                <>
                  <span className="text-[13px] text-subtle">Usa o Claude.</span>
                  <Button
                    size="sm"
                    icon={Languages}
                    busy={jobBusy('translate', 'strip')}
                    disabled={jobsBlocked && !jobBusy('translate', 'strip')}
                    title={busy ? BUSY_REASON : undefined}
                    onClick={() => void translate([lang], 'strip')}
                  >
                    Traduzir agora
                  </Button>
                </>
              ) : null}
            </div>
            <ErrorBox error={jobError('strip')} title="Não foi possível traduzir" />
          </div>
        ) : screenMissing > 0 ? (
          <div className="flex flex-col gap-2 rounded-lg border border-line-strong bg-card px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Languages size={16} aria-hidden="true" className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 text-muted">
                {translating
                  ? 'Traduzindo…'
                  : `Os textos de tela de ${screenMissing === 1 ? '1 cena' : `${screenMissing} cenas`} ainda não estão em ${langLabel(lang)}: sem traduzir, o vídeo nesse idioma mostra esses textos em ${langLabel(doc.primary_language)}. A narração não muda.`}
              </span>
              {!translating ? (
                <>
                  <span className="text-[13px] text-subtle">Usa o Claude.</span>
                  <Button
                    size="sm"
                    icon={Languages}
                    busy={jobBusy('translate', 'strip')}
                    disabled={jobsBlocked && !jobBusy('translate', 'strip')}
                    title={busy ? BUSY_REASON : undefined}
                    onClick={() => startKind('translate', 'strip', { langs: [lang], screen_only: true })}
                  >
                    Traduzir os textos de tela
                  </Button>
                </>
              ) : null}
            </div>
            <ErrorBox error={jobError('strip')} title="Não foi possível traduzir" />
          </div>
        ) : null}
        {glossaryWarnings.length ? (
          <div role="status" className="flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-sm text-amber-100">
            <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
            <div className="flex min-w-0 flex-col gap-1">
              <span>
                A tradução para {langLabel(lang)} não seguiu o glossário do canal em {glossaryWarnings.length === 1 ? '1 ponto' : `${glossaryWarnings.length} pontos`}. Corrija o
                texto aqui (o aviso some ao salvar) ou ajuste o glossário em Canais.
              </span>
              <ul className="flex flex-col gap-0.5 text-[13px] text-amber-100/90">
                {glossaryWarnings.map((w, i) => (
                  <li key={i}>
                    {w.kind === 'segment'
                      ? `Trecho ${doc.segments.findIndex((s) => s.id === w.id) + 1}`
                      : `Texto de tela da cena ${doc.scenes.findIndex((s) => s.id === w.id) + 1}`}
                    : “{w.term}” devia aparecer como “{w.expected}”.
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {doc.segments.map((seg, index) => {
          const key = scriptDraftKey(lang, seg.id);
          const draft = drafts[key];
          const hasAnyDraft = doc.languages.some((l) => drafts[scriptDraftKey(l, seg.id)]);
          return (
            <SegmentCard
              key={seg.id}
              seg={seg}
              index={index + 1}
              lang={lang}
              primary={lang === doc.primary_language}
              draft={draft}
              hasAnyDraft={hasAnyDraft}
              seconds={estimateSeconds(style, lang, [texts[index]])}
              hookMax={hookMax}
              readOnly={Boolean(writing)}
              onDraft={(value) => setDraft(seg, value)}
              onUseServer={() => void askUseServer(key)}
              onKeepMine={() => draft && scriptDrafts.set(doc.id, key, { value: draft.value, base: seg.text[lang] ?? '' })}
              onLock={() => lock.mutate({ id: seg.id, locked: !seg.locked })}
              lockBusy={lock.isPending && lock.variables?.id === seg.id}
              lockBlocked={lock.isPending && lock.variables?.id === seg.id ? null : lockBlocked}
              factOpen={factOpen[seg.id] ?? 0}
            />
          );
        })}
        <ErrorBox error={lock.error} title="Não foi possível travar o trecho" />

        {changed ? (
          <div className="sticky bottom-0 z-10 -mx-1 flex flex-wrap items-center gap-2 rounded-xl border border-line-strong bg-raised/95 px-3 py-2.5 shadow-2xl shadow-black/60 backdrop-blur-md">
            <span className="min-w-0 flex-1 text-sm text-fg" role="status">
              {draftKeys.length === 1 ? '1 alteração não salva' : `${draftKeys.length} alterações não salvas`}
              {otherLangDrafts ? <span className="text-muted"> ({otherLangDrafts} em outro idioma)</span> : null}
            </span>
            <Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => void discardAll()}>
              Descartar
            </Button>
            <Button size="sm" variant="primary" icon={Save} busy={save.isPending} disabled={Boolean(saveBlocked)} onClick={() => save.mutate(liveDrafts)}>
              Salvar alterações
            </Button>
            {saveBlocked ? <p className="basis-full text-[13px] text-amber-200">{saveBlocked}</p> : null}
            <ErrorBox error={save.error} title="Não foi possível salvar" className="basis-full" />
          </div>
        ) : null}
      </div>

      <div className="flex flex-col gap-4">
        <Section title="Revisão" icon={FileText}>
          {doc.languages.length > 1 ? (
            <Field label="Idioma exibido">
              <Select value={lang} onChange={(e) => setLang(e.target.value)}>
                {doc.languages.map((l) => (
                  <option key={l} value={l}>
                    {langLabel(l)}
                    {l === doc.primary_language ? ' (principal)' : ` · ${translatedCount(l)}/${doc.segments.length} traduzidos`}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="text-muted">Duração estimada</span>
              <span className="tabular-nums text-fg">
                ~{formatDuration(seconds)}
                {target ? <span className="text-muted"> · meta {formatDuration(target)}</span> : null}
              </span>
            </div>
            {durationCheck ? (
              <>
                <ProgressBar value={Math.min(1, durationCheck.ratio)} tone={durationCheck.tone} label="Duração estimada em relação à meta do estilo" />
                <p className={`text-[13px] ${durationCheck.tone === 'bad' ? 'text-red-300' : durationCheck.tone === 'accent' ? 'text-amber-200' : 'text-subtle'}`}>
                  {durationCheck.message}
                </p>
              </>
            ) : null}
            <p className="text-[13px] tabular-nums text-subtle">{words} palavras</p>
            {built?.duration ? (
              <div className="flex items-baseline justify-between gap-2 border-t border-line pt-2 text-sm">
                <span className="text-muted">{builtIsFinal ? 'Duração do vídeo gerado' : 'Duração da prévia gerada'}</span>
                <span className="tabular-nums text-fg">{formatDuration(built.duration)}</span>
              </div>
            ) : null}
          </div>
          <div className="border-t border-line pt-3">{approval}</div>
          <ErrorBox error={approve.error} title="Não foi possível aprovar" />
        </Section>

        <BriefCard detail={detail} />

        <Section title="Outras ações do roteiro">
          <div className="-mx-1 flex flex-col items-stretch gap-1">
            <Button
              variant="ghost"
              icon={Wand2}
              busy={jobBusy('script', 'more')}
              disabled={jobsBlocked && !jobBusy('script', 'more')}
              title={busy ? BUSY_REASON : undefined}
              onClick={() => void regenerate()}
              className="justify-start"
            >
              Gerar outro roteiro
            </Button>
            {otherLangs.length ? (
              <Button
                variant="ghost"
                icon={Languages}
                busy={jobBusy('translate', 'more')}
                disabled={jobsBlocked && !jobBusy('translate', 'more')}
                title={busy ? BUSY_REASON : undefined}
                onClick={() => void translate(otherLangs, 'more')}
                className="justify-start"
              >
                Traduzir para os outros idiomas
              </Button>
            ) : null}
            <Button
              variant="ghost"
              icon={LayoutGrid}
              busy={jobBusy('storyboard', 'more')}
              disabled={jobsBlocked && !jobBusy('storyboard', 'more')}
              title={busy ? BUSY_REASON : undefined}
              onClick={() => void redoStoryboard()}
              className="justify-start"
            >
              {doc.scenes.length ? 'Refazer as cenas' : !doc.script_approved && gateScript ? 'Criar as cenas sem aprovar' : 'Criar as cenas'}
            </Button>
          </div>
          <p className="text-[13px] text-subtle">{busy ? BUSY_REASON : 'Estas ações usam o Claude.'}</p>
          <ErrorBox error={jobError('more')} title="Não foi possível iniciar" />
        </Section>

        <FactCheckPanel detail={detail} lang={lang} busy={busy} />

        <LessonsUsedPanel detail={detail} />

        {doc.sources.length ? (
          <Section title="Fontes">
            <ul className="flex flex-col gap-2 text-[13px]">
              {doc.sources.map((s, i) => (
                <li key={`${s.title}-${i}`}>
                  {s.url ? (
                    <a className="inline-flex items-start gap-1 text-sky-300 hover:underline" href={s.url} target="_blank" rel="noreferrer">
                      {s.title}
                      <ExternalLink size={12} aria-hidden="true" className="mt-1 shrink-0" />
                      <span className="sr-only">(abre em nova aba)</span>
                    </a>
                  ) : (
                    <span className="text-fg">{s.title}</span>
                  )}
                  {s.note ? <span className="text-subtle"> · {s.note}</span> : null}
                </li>
              ))}
            </ul>
          </Section>
        ) : null}
      </div>
    </div>
  );
};
