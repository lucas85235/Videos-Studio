import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Check, Plus, Undo2, X } from 'lucide-react';
import React, { useState } from 'react';
import { analyticsApi, type Lesson } from '../../apiAnalytics';
import { Badge, Button, Empty, ErrorBox, Field, Input, Select, Skeleton, Textarea, useToast } from '../../ui';

const ORIGIN_LABELS: Record<string, string> = { manual: 'à mão', experimento: 'experimento', metricas: 'métricas', retro: 'retro' };

const scopeLabel = (scope: string, channels: Record<string, string>) => {
  if (scope === 'global') return 'Todos os canais';
  if (scope === 'formato:short') return 'Shorts';
  if (scope === 'formato:long') return 'Vídeos longos';
  if (scope.startsWith('canal:')) return `Canal ${channels[scope.slice(6)] ?? scope.slice(6)}`;
  if (scope.startsWith('estilo:')) return `Estilo ${scope.slice(7)}`;
  return scope;
};

// Aplicar não é só registrar: o texto da lição passa a entrar sozinho nos pedidos ao LLM.
const APPLIED_HINT = 'Lição aplicada entra sozinha no pedido do roteiro e dos metadados dos próximos vídeos deste escopo.';

const LessonItem: React.FC<{ lesson: Lesson; channels: Record<string, string>; onChanged: () => void }> = ({ lesson, channels, onChanged }) => {
  const { toast } = useToast();
  const applied = lesson.status === 'aplicada';
  const [mode, setMode] = useState<'aplicada' | 'descartada' | null>(null);
  const [note, setNote] = useState('');
  const decide = useMutation({
    mutationFn: (status: 'aplicada' | 'descartada') =>
      analyticsApi.decideLesson(lesson.id, status === 'aplicada' ? { status, applied_where: note } : { status, reason: note }),
    onSuccess: (_, status) => {
      toast(
        status === 'aplicada'
          ? { tone: 'good', title: 'Lição aplicada', description: APPLIED_HINT }
          : { tone: 'good', title: applied ? 'Lição retirada' : 'Lição descartada', description: applied ? 'Ela não entra mais nos próximos roteiros e metadados.' : undefined },
      );
      setMode(null);
      onChanged();
    },
  });
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm text-fg">{lesson.text}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[13px] text-subtle">
            <Badge>{scopeLabel(lesson.scope, channels)}</Badge>
            <span>origem: {ORIGIN_LABELS[lesson.origin] ?? lesson.origin}</span>
          </div>
          {lesson.evidence ? <div className="mt-1 text-[13px] text-muted">{lesson.evidence}</div> : null}
          {applied && lesson.applied_where ? <div className="mt-1 text-[13px] text-muted">Onde: {lesson.applied_where}</div> : null}
        </div>
        {!mode && applied ? (
          <Button size="sm" variant="ghost" icon={Undo2} onClick={() => setMode('descartada')}>
            Retirar
          </Button>
        ) : null}
        {!mode && !applied ? (
          <div className="flex gap-2">
            <Button size="sm" icon={Check} onClick={() => setMode('aplicada')}>
              Aplicar
            </Button>
            <Button size="sm" variant="ghost" icon={X} onClick={() => setMode('descartada')}>
              Descartar
            </Button>
          </div>
        ) : null}
      </div>
      {mode ? (
        <form
          className="flex flex-col gap-2 border-t border-line pt-2"
          onSubmit={(e) => {
            e.preventDefault();
            decide.mutate(mode);
          }}
        >
          <Field
            label={mode === 'aplicada' ? 'Observação (opcional)' : applied ? 'Por que retirar' : 'Por que descartar'}
            hint={mode === 'aplicada' ? `${APPLIED_HINT} Vale para: ${scopeLabel(lesson.scope, channels)}.` : applied ? 'Ela deixa de entrar nos próximos roteiros e metadados.' : undefined}
          >
            <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
          </Field>
          <ErrorBox error={decide.error} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setMode(null)}>
              Cancelar
            </Button>
            <Button type="submit" variant="primary" busy={decide.isPending}>
              {mode === 'aplicada' ? 'Aplicar nos próximos vídeos' : applied ? 'Retirar lição' : 'Descartar lição'}
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
};

const NewLesson: React.FC<{ channelId: string | null; onDone: () => void; onCancel: () => void }> = ({ channelId, onDone, onCancel }) => {
  const [scope, setScope] = useState(channelId ? `canal:${channelId}` : 'global');
  const [text, setText] = useState('');
  const [evidence, setEvidence] = useState('');
  const create = useMutation({ mutationFn: () => analyticsApi.createLesson({ scope, text: text.trim(), evidence: evidence.trim() }), onSuccess: onDone });
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim().length >= 3) create.mutate();
      }}
    >
      <Field label="Lição">
        <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Vale para">
          <Select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="global">Todos os canais</option>
            {channelId ? <option value={`canal:${channelId}`}>Este canal</option> : null}
            <option value="formato:short">Shorts</option>
            <option value="formato:long">Vídeos longos</option>
          </Select>
        </Field>
        <Field label="Evidência (opcional)">
          <Input value={evidence} onChange={(e) => setEvidence(e.target.value)} maxLength={4000} />
        </Field>
      </div>
      <ErrorBox error={create.error} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" disabled={text.trim().length < 3} busy={create.isPending}>
          Registrar lição
        </Button>
      </div>
    </form>
  );
};

/** Lições propostas, esperando aplicação ou descarte. */
export const ProposedLessons: React.FC<{ channelId: string | null; channels: Record<string, string> }> = ({ channelId, channels }) => {
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const lessons = useQuery({ queryKey: ['analytics-lessons', 'proposta'], queryFn: () => analyticsApi.lessons({ status: 'proposta' }) });
  const refresh = () => client.invalidateQueries({ queryKey: ['analytics-lessons'] });
  // Com um canal escolhido: as lições dele e as gerais (global, formato, estilo).
  const items = (lessons.data ?? []).filter((l) => !channelId || !l.scope.startsWith('canal:') || l.scope === `canal:${channelId}`);
  return (
    <div className="flex flex-col gap-3">
      {adding ? (
        <NewLesson
          channelId={channelId}
          onCancel={() => setAdding(false)}
          onDone={() => {
            setAdding(false);
            refresh();
          }}
        />
      ) : (
        <div>
          <Button size="sm" icon={Plus} onClick={() => setAdding(true)}>
            Nova lição
          </Button>
        </div>
      )}
      {lessons.isLoading ? <Skeleton className="h-16" /> : null}
      <ErrorBox error={lessons.error} onRetry={() => lessons.refetch()} />
      {lessons.data && !items.length ? (
        <Empty icon={BookOpen} title="Nenhuma lição esperando">
          Ao fechar um experimento, registre o que aprendeu: a lição aparece aqui para você aplicar ou descartar.
        </Empty>
      ) : null}
      {items.length ? (
        <ul className="flex flex-col gap-2">
          {items.map((l) => (
            <LessonItem key={l.id} lesson={l} channels={channels} onChanged={refresh} />
          ))}
        </ul>
      ) : null}
    </div>
  );
};

/** Lições aplicadas: entram sozinhas no pedido do roteiro e dos metadados. Daqui dá para retirar uma. */
export const AppliedLessons: React.FC<{ channelId: string | null; channels: Record<string, string> }> = ({ channelId, channels }) => {
  const client = useQueryClient();
  const lessons = useQuery({ queryKey: ['analytics-lessons', 'aplicada'], queryFn: () => analyticsApi.lessons({ status: 'aplicada' }) });
  const refresh = () => client.invalidateQueries({ queryKey: ['analytics-lessons'] });
  const items = (lessons.data ?? []).filter((l) => !channelId || !l.scope.startsWith('canal:') || l.scope === `canal:${channelId}`);
  return (
    <div className="flex flex-col gap-3">
      {lessons.isLoading ? <Skeleton className="h-16" /> : null}
      <ErrorBox error={lessons.error} onRetry={() => lessons.refetch()} />
      {lessons.data && !items.length ? (
        <Empty icon={BookOpen} title="Nenhuma lição aplicada">
          Ao aplicar uma lição proposta, ela aparece aqui e passa a entrar nos próximos roteiros e metadados.
        </Empty>
      ) : null}
      {items.length ? (
        <ul className="flex flex-col gap-2">
          {items.map((l) => (
            <LessonItem key={l.id} lesson={l} channels={channels} onChanged={refresh} />
          ))}
        </ul>
      ) : null}
    </div>
  );
};
