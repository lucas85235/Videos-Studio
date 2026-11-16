import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, FileText, GitBranch, History, LayoutGrid, MonitorPlay, Sparkles, Undo2, X, type LucideIcon } from 'lucide-react';
import React from 'react';
import { api, type ProjectDetail, type Version } from '../../api';
import { Badge, Button, ErrorBox, IconButton, Section, useConfirm } from '../../ui';
import { useJobStarting, useStartJob } from '../ProjectPage';
import { AbBranches } from './AbBranches';
import { isOrphanScriptDraft, restoreNotices, scriptDrafts, useKeyedStore, useKeyedValue, useUnsavedCount } from './core/drafts';
import { freshness, humanizeVersionMessage, versionMilestone, type MilestoneKind } from './core/model';
import type { TabNavigation } from './types';

const AUTHOR: Record<string, string> = { user: 'Você', ai: 'IA', 'ai-edit': 'IA' };

/** Marco da versão: só ícone e cor (o texto da versão já diz o que foi); o rótulo fica no title e no leitor de tela. */
const MILESTONE_ICON: Record<MilestoneKind, LucideIcon> = {
  approval: CircleCheck,
  script: FileText,
  scenes: LayoutGrid,
  restore: Undo2,
  edit: Sparkles,
};
const MILESTONE_COLOR = { good: 'text-emerald-300', info: 'text-sky-300' } as const;

const dayKey = (iso: string | null) => {
  if (!iso) return 'sem-data';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 'sem-data' : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

const dayLabel = (iso: string | null) => {
  if (!iso) return 'Sem data';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Sem data';
  const today = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86_400_000);
  if (diff === 0) return 'Hoje';
  if (diff === 1) return 'Ontem';
  return d.toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
};

const timeOf = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '');

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many.replace('{n}', String(n)));

export const VersionsTab: React.FC<{ detail: ProjectDetail; busy?: boolean } & TabNavigation> = ({ detail, busy = false, goTo }) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const projectId = detail.doc.id;
  const startJob = useStartJob(projectId);
  const starting = useJobStarting(projectId);
  const unsaved = useUnsavedCount(projectId);
  const scriptBucket = useKeyedStore(scriptDrafts, projectId);
  const notice = useKeyedValue(restoreNotices, projectId, 'last');
  // O aviso some sozinho quando uma prévia nova termina depois da restauração.
  const restored = notice && notice.at > freshness(detail).lastPreviewAt ? notice : null;
  const checkout = useMutation({
    mutationFn: (number: number) => api.post<{ version: number }>(`/api/projects/${projectId}/checkout`, { number }),
    onSuccess: async (result, number) => {
      await client.invalidateQueries({ queryKey: ['project', projectId] });
      restoreNotices.set(projectId, 'last', { from: number, to: typeof result?.version === 'number' ? result.version : null, at: Date.now() });
    },
  });
  const current = detail.project.current_version;
  // Versão de onde nasce o próximo ramo A/B (null: o formulário de criar ramo está fechado).
  const [branchFrom, setBranchFrom] = React.useState<number | null>(null);
  const closeNotice = () => restoreNotices.set(projectId, 'last', null);

  const restore = async (v: Version) => {
    const next = Math.max(current, ...detail.versions.map((x) => x.number)) + 1;
    const liveScript = Object.keys(scriptBucket).filter((key) => !isOrphanScriptDraft(detail.doc, key)).length;
    const pending = liveScript + unsaved.prompts;
    const draftsNote = pending
      ? ` Você tem ${plural(pending, '1 alteração não salva', '{n} alterações não salvas')} no roteiro ou nas cenas: as dos trechos e cenas que continuarem na v${v.number} ficam marcadas para você revisar; trechos que não existem na v${v.number} ficam guardados na aba Roteiro para copiar; prompts de cenas que não existem nela se perdem.`
      : '';
    const ok = await confirm({
      title: `Voltar para a versão ${v.number}?`,
      description: (
        <>
          Cria a v{next} com o conteúdo da v{v.number} (“{humanizeVersionMessage(v.message)}”). A versão atual continua no histórico.{draftsNote} Depois, gere a
          prévia de novo para ver o resultado.
        </>
      ),
      confirmLabel: `Voltar para a v${v.number}`,
    });
    if (ok) checkout.mutate(v.number);
  };

  const groups: { key: string; label: string; items: Version[] }[] = [];
  for (const v of detail.versions) {
    const key = dayKey(v.created_at);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(v);
    else groups.push({ key, label: dayLabel(v.created_at), items: [v] });
  }

  return (
    <div className="flex flex-col gap-4">
      {restored ? (
        <div role="status" className="flex flex-wrap items-start gap-3 rounded-xl border border-emerald-400/35 bg-emerald-400/10 p-3">
          <CircleCheck size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-emerald-300" />
          <div className="min-w-0 flex-1 basis-64 text-sm">
            <div className="font-medium text-fg">
              Versão {restored.from} restaurada{restored.to ? ` como v${restored.to}` : ''}.
            </div>
            <p className="text-[13px] text-muted">
              A prévia e o vídeo ainda mostram o resultado anterior. Gere a prévia de novo para ver esta versão (usa a cota de voz e de imagens só no que mudou).
            </p>
          </div>
          <div className="flex items-center gap-2">
            {detail.doc.scenes.length ? (
              <Button
                size="sm"
                variant="primary"
                icon={MonitorPlay}
                busy={startJob.isPending}
                disabled={(busy || starting) && !startJob.isPending}
                title={busy ? 'Aguarde o trabalho em andamento terminar.' : undefined}
                onClick={() => {
                  if (starting) return;
                  startJob.mutate(
                    { kind: 'preview' },
                    {
                      onSuccess: () => {
                        closeNotice();
                        goTo?.('preview');
                      },
                    },
                  );
                }}
              >
                Gerar prévia
              </Button>
            ) : null}
            <IconButton size="sm" label="Fechar aviso" icon={X} onClick={closeNotice} />
          </div>
          <ErrorBox error={startJob.error} title="Não foi possível gerar a prévia" className="basis-full" />
        </div>
      ) : null}

      <AbBranches detail={detail} busy={busy} createFrom={branchFrom} onCreated={() => setBranchFrom(null)} />

      <Section
        title="Histórico de versões"
        icon={History}
        description="Cada mudança vira uma versão. Voltar cria uma versão nova com o conteúdo antigo: nada se perde e não gasta cota de novo."
      >
        <ErrorBox error={checkout.error} title="Não foi possível voltar para a versão" onClose={() => checkout.reset()} />
        {busy ? <p className="text-[13px] text-subtle">Há um trabalho em andamento: espere terminar para voltar a uma versão.</p> : null}
        <div className="flex flex-col gap-4">
          {groups.map((group) => (
            <div key={group.key}>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-subtle first-letter:uppercase">{group.label}</h3>
              <ol className="flex flex-col divide-y divide-line">
                {group.items.map((v) => {
                  const milestone = versionMilestone(v.message, v.author);
                  const MilestoneIcon = milestone ? MILESTONE_ICON[milestone.kind] : null;
                  const isCurrent = v.number === current;
                  const rowBusy = checkout.isPending && checkout.variables === v.number;
                  const text = humanizeVersionMessage(v.message);
                  const onBranch = v.branch && v.branch !== 'main' ? v.branch.toUpperCase() : null;
                  return (
                    <li
                      key={v.number}
                      // No celular: número | mensagem (até 2 linhas) | ação, e autor/hora numa segunda linha.
                      className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 py-2 text-sm sm:flex sm:flex-nowrap ${
                        isCurrent ? '-mx-2 rounded-lg bg-raised/60 px-2' : ''
                      }`}
                    >
                      <span className="w-10 shrink-0 text-[13px] tabular-nums text-subtle">v{v.number}</span>
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        {/* Coluna fixa (mesmo com linha sem marco): os títulos começam todos no mesmo x. */}
                        <span className="flex w-4 shrink-0 justify-center" title={milestone?.label}>
                          {milestone && MilestoneIcon ? (
                            <>
                              <MilestoneIcon size={16} aria-hidden="true" className={MILESTONE_COLOR[milestone.tone]} />
                              <span className="sr-only">{milestone.label}: </span>
                            </>
                          ) : null}
                        </span>
                        {onBranch ? (
                          <Badge tone="info" icon={GitBranch} title={`Versão do ramo ${onBranch} (não mexe na principal)`}>
                            {onBranch}
                          </Badge>
                        ) : null}
                        <span className={`line-clamp-2 sm:line-clamp-1 ${milestone ? 'font-medium text-fg' : 'text-fg'}`} title={text}>
                          {text}
                        </span>
                      </span>
                      <span className="col-start-2 row-start-2 flex items-center gap-3 text-[13px] text-subtle sm:contents">
                        <span className="shrink-0">{AUTHOR[v.author] ?? 'Outro'}</span>
                        <time
                          className="shrink-0 tabular-nums sm:w-12 sm:text-right"
                          dateTime={v.created_at ?? undefined}
                          title={v.created_at ? new Date(v.created_at).toLocaleString('pt-BR') : undefined}
                        >
                          {timeOf(v.created_at)}
                        </time>
                      </span>
                      <span className="col-start-3 row-span-2 row-start-1 flex shrink-0 items-center justify-end gap-1 sm:w-32">
                        <IconButton
                          size="sm"
                          icon={GitBranch}
                          label={`Criar uma versão A/B a partir da v${v.number}`}
                          onClick={() => {
                            setBranchFrom(v.number);
                            window.scrollTo({ top: 0, behavior: 'smooth' });
                          }}
                        />
                        {isCurrent ? (
                          <Badge tone="good" icon={CircleCheck}>
                            Atual
                          </Badge>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={Undo2}
                            busy={rowBusy}
                            disabled={busy || (checkout.isPending && !rowBusy)}
                            title={busy ? 'Aguarde o trabalho em andamento terminar' : `Voltar para a v${v.number}`}
                            aria-label={`Voltar para a versão ${v.number}: ${text}`}
                            onClick={() => void restore(v)}
                          >
                            Voltar
                          </Button>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ol>
            </div>
          ))}
        </div>
        {detail.versions.length >= 30 ? <p className="text-[13px] text-subtle">Mostrando as 30 versões mais recentes.</p> : null}
      </Section>
    </div>
  );
};
