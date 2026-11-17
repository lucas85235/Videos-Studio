/* Aba Sugestões: resumo, temas, lacunas e as ideias da síntese, por data (agenda) ou por nota (ranking). */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, CalendarDays, ExternalLink, Lightbulb, ListOrdered, Save, ShieldAlert, Wrench } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { IDEA_STATUS_LABELS } from '../../apiBusiness';
import {
  RESEARCH_EVIDENCE_LABELS,
  RESEARCH_RISK_LABELS,
  RESEARCH_WHEN_LABELS,
  researchApi,
  type ResearchIdea,
  type ResearchRun,
} from '../../apiResearch';
import { Badge, type BadgeTone, Button, buttonClass, Card, Empty, SegmentedControl, useToast } from '../../ui';
import { decimal } from '../radar/format';
import { Thumb } from '../radar/shared';
import { CheckBadge, countFormats, dayText, type FormatFilter, FormatFilterControl, formatLabel, GapsBox, scoreTone, youtubeIdOf } from './shared';

const RISK_TONE: Record<string, BadgeTone> = { baixo: 'good', medio: 'warn', alto: 'bad' };

type View = 'agenda' | 'ranking';

const whenText = (i: ResearchIdea) => {
  const w = i.when;
  const range = w.until ? `${dayText(w.date)} a ${dayText(w.until)}` : dayText(w.date);
  return `${range} · ${RESEARCH_WHEN_LABELS[w.type] ?? w.type}`;
};

/** Link para Ideias já no canal e na situação da ideia (o filtro padrão "Para decidir" esconderia uma rejeitada). */
const ideasLink = (channelId: string, status: string | null | undefined) =>
  `/ideias?canal=${encodeURIComponent(channelId)}&situacao=${!status ? 'todas' : status === 'nova' ? 'abertas' : status}`;

/** Salvar no banco de ideias ou abrir a ideia já salva. */
const IdeaAction: React.FC<{ idea: ResearchIdea; run: ResearchRun }> = ({ idea, run }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const chk = idea.check;
  const save = useMutation({
    mutationFn: () => researchApi.saveIdeas(run.id, [idea.rank]),
    onSuccess: (out) => {
      qc.setQueryData(['research', 'run', run.id], out.run);
      qc.invalidateQueries({ queryKey: ['ideas'] });
      qc.invalidateQueries({ queryKey: ['research', 'state', run.channel_id] });
      const skipped = out.skipped[0];
      toast(
        out.created.length
          ? { tone: 'good', title: 'Ideia salva', description: idea.title, action: { label: 'Ver em Ideias', to: ideasLink(run.channel_id, 'nova') } }
          : { tone: 'info', title: 'A ideia não foi salva', description: skipped?.motivo ?? idea.title, action: { label: 'Ver em Ideias', to: ideasLink(run.channel_id, null) } },
      );
    },
    onError: (e) => toast({ tone: 'bad', title: 'Não deu para salvar a ideia', description: e instanceof Error ? e.message : String(e) }),
  });
  if (chk?.idea_id && (chk.saved || chk.status === 'ja_no_banco')) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {chk.idea_status ? <Badge tone="accent">Em Ideias: {IDEA_STATUS_LABELS[chk.idea_status] ?? chk.idea_status}</Badge> : null}
        <Link to={ideasLink(run.channel_id, chk.idea_status)} className={buttonClass({ size: 'sm', variant: 'ghost' })}>
          Ver em Ideias
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
    );
  }
  return (
    <Button size="sm" variant={chk?.status === 'repetida' ? 'ghost' : 'secondary'} icon={Save} busy={save.isPending} onClick={() => save.mutate()}>
      {chk?.status === 'repetida' ? 'Salvar mesmo assim' : 'Salvar ideia'}
    </Button>
  );
};

/** Cartão de uma ideia da síntese. */
export const ResearchIdeaCard: React.FC<{ idea: ResearchIdea; run: ResearchRun; showDate?: boolean }> = ({ idea, run, showDate = true }) => (
  <Card className="flex flex-col gap-3 p-4" aria-label={`Ideia ${idea.rank}: ${idea.title}`}>
    <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
      <span className="mt-0.5 font-display text-sm font-semibold tabular-nums text-muted">#{idea.rank}</span>
      <h3 className="min-w-[12rem] flex-1 font-display text-[15px] font-semibold leading-6 text-fg [overflow-wrap:anywhere]">{idea.title}</h3>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={scoreTone(idea.score)} title="Chance relativa dentro do canal (0 a 10)">
          Nota {decimal(idea.score)}
        </Badge>
        <Badge>{formatLabel(idea.format)}</Badge>
        <CheckBadge check={idea.check} />
        {idea.no_proof ? (
          <Badge tone="bad" title="Nenhuma evidência sobrou depois da conferência: perdeu 2 pontos">
            Sem prova
          </Badge>
        ) : null}
      </div>
    </div>
    {showDate ? (
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-fg">
        <CalendarDays size={14} aria-hidden="true" className="text-muted" />
        <span className="font-medium">{whenText(idea)}</span>
        {idea.when.note ? <span className="text-muted">— {idea.when.note}</span> : null}
      </div>
    ) : idea.when.note ? (
      <p className="text-[13px] text-muted">
        {RESEARCH_WHEN_LABELS[idea.when.type]}: {idea.when.note}
      </p>
    ) : null}
    {idea.angle ? (
      <p className="text-sm text-fg">
        <span className="text-muted">Ângulo: </span>
        {idea.angle}
      </p>
    ) : null}
    {idea.why ? (
      <p className="text-sm text-fg">
        <span className="text-muted">Por que: </span>
        {idea.why}
      </p>
    ) : null}
    {idea.evidence.length ? (
      <div className="flex flex-col gap-1.5">
        <div className="text-xs font-medium uppercase tracking-wide text-muted">Evidências</div>
        <ul className="flex flex-col gap-2">
          {idea.evidence.map((ev) => {
            const vid = youtubeIdOf(ev.url);
            return (
              <li key={`${ev.ref}-${ev.url}`} className="flex items-start gap-2.5">
                {vid ? <Thumb id={vid} alt="" className="w-24 shrink-0" /> : null}
                <div className="min-w-0 flex-1 text-[13px]">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={ev.kind === 'web' ? 'info' : 'neutral'}>
                      {RESEARCH_EVIDENCE_LABELS[ev.kind] ?? ev.kind}
                      {ev.ref ? ` ${ev.ref}` : ''}
                    </Badge>
                    {ev.url ? (
                      <a href={ev.url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 text-accent hover:underline">
                        <span className="truncate">{ev.title || ev.url}</span>
                        <ExternalLink size={12} aria-hidden="true" className="shrink-0" />
                      </a>
                    ) : (
                      <span className="text-fg">{ev.title}</span>
                    )}
                  </div>
                  {ev.note ? <p className="mt-0.5 text-muted">{ev.note}</p> : null}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    ) : null}
    {idea.needs.length ? (
      <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
        <Wrench size={14} aria-hidden="true" className="text-muted" />
        <span className="text-muted">Precisa:</span>
        {idea.needs.map((n) => (
          <Badge key={n}>{n}</Badge>
        ))}
      </div>
    ) : null}
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-[13px]">
        <ShieldAlert size={14} aria-hidden="true" className="text-muted" />
        <Badge tone={RISK_TONE[idea.risk.level] ?? 'neutral'}>{RESEARCH_RISK_LABELS[idea.risk.level] ?? idea.risk.level}</Badge>
        {idea.risk.note ? <span className="text-muted">{idea.risk.note}</span> : null}
      </div>
      <IdeaAction idea={idea} run={run} />
    </div>
  </Card>
);

const EMPTY_TITLE: Record<string, string> = {
  parcial: 'Rodada parcial: sem ideias',
  cancelada: 'Rodada cancelada',
  falhou: 'A rodada falhou',
};
const EMPTY_TEXT: Record<string, string> = {
  ok: 'A síntese rodou, mas nenhuma ideia sobrou (veja as descartadas e as lacunas acima). Os sinais estão nas outras abas.',
  parcial: 'A síntese não rodou (sem a busca na web, etapa pulada ou o limite do plano Claude). Os sinais estão nas outras abas; veja as lacunas acima.',
  cancelada: 'Você cancelou antes da síntese. Os sinais coletados estão nas outras abas.',
  falhou: 'Um erro parou a rodada depois da coleta (veja no Histórico). Os sinais coletados estão nas outras abas.',
};

const monthText = (iso: string) => {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
};

export const SuggestionsTab: React.FC<{ run: ResearchRun }> = ({ run }) => {
  const [view, setView] = useState<View>('agenda');
  const [fmt, setFmt] = useState<FormatFilter>('all');
  const synth = run.synthesis;
  const ideas = useMemo(() => synth.ideas ?? [], [synth.ideas]);
  const byRank = useMemo(() => new Map(ideas.map((i) => [i.rank, i])), [ideas]);
  const counts = countFormats(ideas);
  const keep = (i: { format: string }) => fmt === 'all' || i.format === fmt;

  // Agenda: a ordem do backend (por data; a repetida fica de fora), agrupada por mês e com o dia em destaque.
  const agenda = useMemo(() => {
    const ordered = synth.agenda?.length
      ? synth.agenda.map((a) => byRank.get(a.rank)).filter((i): i is ResearchIdea => !!i)
      : ideas.filter((i) => i.check?.status !== 'repetida' || i.check.saved).sort((a, b) => a.when.date.localeCompare(b.when.date));
    const groups: { month: string; items: ResearchIdea[] }[] = [];
    for (const i of ordered) {
      if (fmt !== 'all' && i.format !== fmt) continue;
      const m = monthText(i.when.date);
      const g = groups[groups.length - 1];
      if (g && g.month === m) g.items.push(i);
      else groups.push({ month: m, items: [i] });
    }
    return groups;
  }, [synth.agenda, ideas, byRank, fmt]);
  const ranking = ideas.filter(keep);
  const repeated = ideas.filter((i) => i.check?.status === 'repetida' && !i.check.saved && keep(i));

  return (
    <div className="flex flex-col gap-4">
      <GapsBox gaps={run.lacunas} />
      {synth.resumo ? (
        <Card className="flex flex-col gap-2 p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted">Resumo</div>
          <p className="whitespace-pre-line text-sm text-fg">{synth.resumo}</p>
          {synth.temas?.length ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {synth.temas.map((t) => (
                <Badge key={t} tone="accent">
                  {t}
                </Badge>
              ))}
            </div>
          ) : null}
        </Card>
      ) : null}
      {!ideas.length ? (
        <Empty icon={Lightbulb} title={EMPTY_TITLE[run.status] ?? 'Nenhuma ideia nesta rodada'}>
          {EMPTY_TEXT[run.status] ?? EMPTY_TEXT.ok}
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <SegmentedControl
              label="Ordem"
              size="sm"
              value={view}
              onChange={setView}
              options={[
                { id: 'agenda', label: 'Agenda (por data)', icon: CalendarDays },
                { id: 'ranking', label: 'Ranking (por nota)', icon: ListOrdered },
              ]}
            />
            {counts.long && counts.short ? <FormatFilterControl value={fmt} onChange={setFmt} counts={counts} /> : null}
            <span className="text-xs text-muted">
              {run.n_ideas} {run.n_ideas === 1 ? 'ideia' : 'ideias'}, {run.n_saved} {run.n_saved === 1 ? 'salva' : 'salvas'} em Ideias
            </span>
          </div>
          {view === 'agenda' ? (
            <div className="flex flex-col gap-5">
              {agenda.map((g) => (
                <section key={g.month} aria-label={g.month} className="flex flex-col gap-3">
                  <h3 className="font-display text-sm font-semibold capitalize text-muted">{g.month}</h3>
                  <ol className="flex flex-col gap-3">
                    {g.items.map((i) => (
                      <li key={i.rank} className="grid gap-3 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
                        <div className="text-sm sm:pt-4">
                          <div className="font-semibold text-fg">{dayText(i.when.date)}</div>
                          {i.when.until ? <div className="text-xs text-muted">até {dayText(i.when.until)}</div> : null}
                          <div className="text-xs text-muted">{RESEARCH_WHEN_LABELS[i.when.type]}</div>
                        </div>
                        <ResearchIdeaCard idea={i} run={run} showDate={false} />
                      </li>
                    ))}
                  </ol>
                </section>
              ))}
              {repeated.length ? (
                <section aria-label="Repetidas" className="flex flex-col gap-3">
                  <h3 className="font-display text-sm font-semibold text-muted">Fora da agenda: repetem um vídeo já feito</h3>
                  {repeated.map((i) => (
                    <ResearchIdeaCard key={i.rank} idea={i} run={run} />
                  ))}
                </section>
              ) : null}
            </div>
          ) : (
            <ol className="flex flex-col gap-3">
              {ranking.map((i) => (
                <li key={i.rank}>
                  <ResearchIdeaCard idea={i} run={run} />
                </li>
              ))}
            </ol>
          )}
        </>
      )}
      {synth.discarded?.length ? (
        <Card className="flex flex-col gap-2 p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted">Descartadas pelo Vulto</div>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-[13px] text-muted">
            {synth.discarded.map((d) => (
              <li key={d.title}>
                <span className="text-fg">{d.title}</span>: {d.motivo}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      {synth.web_sources?.length ? (
        <Card className="flex flex-col gap-2 p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted">Fontes da web</div>
          <ul className="flex flex-col gap-1 text-[13px]">
            {synth.web_sources.map((w) => (
              <li key={w.url} className="flex flex-wrap items-center gap-2">
                <a href={w.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                  {w.title || w.url}
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
                {w.date ? <span className="text-muted">{w.date}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
};
