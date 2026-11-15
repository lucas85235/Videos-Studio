import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Ban, Check, Clapperboard, ExternalLink, Trash2, X } from 'lucide-react';
import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { StyleItem } from '../../api';
import { businessApi, IDEA_STATUS_LABELS, type Idea, type IdeaStatus } from '../../apiBusiness';
import { Badge, type BadgeTone, Button, buttonClass, Card, ErrorBox, Field, IconButton, Input, Select, useConfirm, useToast } from '../../ui';

const STATUS_TONE: Record<IdeaStatus, BadgeTone> = {
  nova: 'info',
  aprovada: 'good',
  rejeitada: 'bad',
  em_producao: 'accent',
  feita: 'good',
  descartada: 'neutral',
};

const scoreTone = (score: number | null): BadgeTone => (score == null ? 'neutral' : score >= 8 ? 'good' : score >= 7.5 ? 'warn' : 'neutral');
const formatScore = (score: number | null) => (score == null ? 'sem nota' : score.toLocaleString('pt-BR', { maximumFractionDigits: 1 }));

type Pending = { status: IdeaStatus; label: string } | null;

/** Uma ideia: nota, texto, decisão (com motivo) e "Virar projeto". */
export const IdeaCard: React.FC<{ idea: Idea; channelName: string | null; styles: StyleItem[]; defaultStyle: string }> = ({
  idea,
  channelName,
  styles,
  defaultStyle,
}) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [pending, setPending] = useState<Pending>(null);
  const [reason, setReason] = useState('');
  const [converting, setConverting] = useState(false);
  // Vazio = padrão: o backend escolhe o estilo do canal (ou um do formato da ideia). Assim o card não fixa um estilo
  // antes de os canais carregarem.
  const [styleId, setStyleId] = useState('');
  const autoStyle = pickStyle(styles, defaultStyle, idea.format);
  const refresh = () => qc.invalidateQueries({ queryKey: ['ideas'] });

  const decide = useMutation({
    mutationFn: (p: { status: IdeaStatus; reason?: string }) => businessApi.decideIdeas({ ids: [idea.id], status: p.status, reason: p.reason || null }),
    onSuccess: (_, p) => {
      refresh();
      setPending(null);
      setReason('');
      toast({ tone: 'good', title: `Ideia: ${IDEA_STATUS_LABELS[p.status].toLowerCase()}` });
    },
  });
  const toProject = useMutation({
    mutationFn: () => businessApi.ideaToProject(idea.id, { style_id: styleId || null }),
    onSuccess: (out) => {
      refresh();
      qc.invalidateQueries({ queryKey: ['projects'] });
      toast({ tone: 'good', title: 'Projeto criado', description: out.project.title });
      navigate(`/projetos/${out.project.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: () => businessApi.deleteIdea(idea.id),
    onSuccess: () => {
      refresh();
      toast({ tone: 'good', title: 'Ideia apagada' });
    },
  });

  const canDecide = idea.status !== 'em_producao' && idea.status !== 'feita';
  const askDelete = async () => {
    if (await confirm({ title: 'Apagar esta ideia?', description: idea.title, confirmLabel: 'Apagar', tone: 'danger' })) remove.mutate();
  };

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start gap-3">
        <Badge tone={scoreTone(idea.score)} className="tabular-nums" title="Nota da ideia (0 a 10)">
          {formatScore(idea.score)}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-fg">{idea.title}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-subtle">
            <Badge tone={STATUS_TONE[idea.status]}>{IDEA_STATUS_LABELS[idea.status]}</Badge>
            {channelName ? <span>{channelName}</span> : <span>Sem canal</span>}
            {idea.format ? <span>· {idea.format === 'short' ? 'Short' : 'Vídeo longo'}</span> : null}
            {idea.origin && idea.origin !== 'manual' ? <span>· {idea.origin}</span> : null}
          </div>
        </div>
        <IconButton label="Apagar ideia" icon={Trash2} size="sm" onClick={askDelete} busy={remove.isPending} />
      </div>
      {idea.angle || idea.evidence || idea.search || idea.reason ? (
        <dl className="grid gap-1.5 text-sm">
          {idea.angle ? <Detail label="Ângulo">{idea.angle}</Detail> : null}
          {idea.evidence ? <Detail label="Evidência">{idea.evidence}</Detail> : null}
          {idea.search ? <Detail label="Busca">{idea.search}</Detail> : null}
          {idea.reason ? <Detail label="Motivo">{idea.reason}</Detail> : null}
        </dl>
      ) : null}

      {pending ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            decide.mutate({ status: pending.status, reason: reason.trim() });
          }}
        >
          <Field label={`Motivo (${pending.label.toLowerCase()})`} className="min-w-[200px] flex-1">
            <Input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Opcional" />
          </Field>
          <Button type="submit" variant="primary" size="sm" busy={decide.isPending}>
            {pending.label}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPending(null)}>
            Cancelar
          </Button>
        </form>
      ) : converting ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            toProject.mutate();
          }}
        >
          <Field label="Estilo do projeto" className="min-w-[220px] flex-1">
            <Select value={styleId} onChange={(e) => setStyleId(e.target.value)}>
              <option value="">Padrão{autoStyle ? `: ${autoStyle.style.name}` : ''}</option>
              {styles.map((s) => (
                <option key={s.style.id} value={s.style.id}>
                  {s.style.name} ({s.style.format.type === 'short' ? 'Short' : 'longo'})
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="primary" size="sm" icon={Clapperboard} busy={toProject.isPending}>
            Criar projeto
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConverting(false)}>
            Cancelar
          </Button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          {idea.project_id ? (
            <Link to={`/projetos/${idea.project_id}`} className={buttonClass({ size: 'sm' })}>
              <ExternalLink size={14} aria-hidden="true" />
              Abrir projeto
            </Link>
          ) : (
            <Button size="sm" variant="primary" icon={Clapperboard} onClick={() => setConverting(true)}>
              Virar projeto
            </Button>
          )}
          {canDecide && idea.status !== 'aprovada' ? (
            <Button size="sm" icon={Check} onClick={() => decide.mutate({ status: 'aprovada' })} busy={decide.isPending}>
              Aprovar
            </Button>
          ) : null}
          {canDecide && idea.status !== 'rejeitada' ? (
            <Button size="sm" variant="ghost" icon={X} onClick={() => setPending({ status: 'rejeitada', label: 'Rejeitar' })}>
              Rejeitar
            </Button>
          ) : null}
          {canDecide && idea.status !== 'descartada' ? (
            <Button size="sm" variant="ghost" icon={Ban} onClick={() => setPending({ status: 'descartada', label: 'Descartar' })}>
              Descartar
            </Button>
          ) : null}
          {canDecide && idea.status !== 'nova' ? (
            <Button size="sm" variant="ghost" onClick={() => decide.mutate({ status: 'nova' })}>
              Voltar para nova
            </Button>
          ) : null}
        </div>
      )}
      <ErrorBox error={decide.error || toProject.error || remove.error} />
    </Card>
  );
};

/** O mesmo critério do backend: o estilo padrão do canal se o formato da ideia bate; senão o primeiro do formato. */
function pickStyle(styles: StyleItem[], channelStyle: string, format: Idea['format']): StyleItem | undefined {
  const preferred = styles.find((s) => s.style.id === channelStyle);
  if (!format || preferred?.style.format.type === format) return preferred;
  return styles.find((s) => s.style.format.type === format) ?? preferred;
}

const Detail: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid gap-x-3 sm:grid-cols-[90px_minmax(0,1fr)]">
    <dt className="text-[13px] text-subtle">{label}</dt>
    <dd className="m-0 whitespace-pre-wrap break-words text-muted">{children}</dd>
  </div>
);
