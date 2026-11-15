import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DatabaseBackup, Play, Timer } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { opsApi, type RoutinesPatch, type RoutinesState } from '../../apiOps';
import { isActiveJob, useJobs } from '../../shell/data';
import { Badge, Button, Checkbox, ErrorBox, Field, formatBytes, Input, Section, errorMessage, formatRelative, useToast } from '../../ui';
import { TODAY_KEY } from './shared';

/** Rotinas automáticas: horário da manhã, ligar/desligar, backup agora e últimas execuções. */
export const RoutinesSection: React.FC<{ state: RoutinesState }> = ({ state }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const jobs = useJobs();
  const [time, setTime] = useState(state.morning_time);
  const [watching, setWatching] = useState<string | null>(null);
  const backups = useQuery({ queryKey: ['ops', 'backups'], queryFn: opsApi.backups });

  useEffect(() => setTime(state.morning_time), [state.morning_time]);

  // Quando o job que mandamos rodar termina, atualiza o resumo (últimas execuções e cópias).
  const watched = watching ? jobs.data?.find((j) => j.id === watching) : undefined;
  useEffect(() => {
    if (watched && !isActiveJob(watched)) {
      setWatching(null);
      client.invalidateQueries({ queryKey: TODAY_KEY });
      client.invalidateQueries({ queryKey: ['ops', 'backups'] });
      toast(
        watched.status === 'done'
          ? { tone: 'good', title: watched.kind === 'backup' ? 'Cópia do banco feita' : 'Rotina da manhã concluída' }
          : { tone: 'bad', title: 'A rotina falhou', description: watched.error ?? undefined },
      );
    }
  }, [watched, client, toast]);

  const save = useMutation({
    mutationFn: (body: RoutinesPatch) => opsApi.updateRoutines(body),
    onSuccess: () => client.invalidateQueries({ queryKey: TODAY_KEY }),
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível salvar', description: errorMessage(err) }),
  });
  const backup = useMutation({
    mutationFn: opsApi.backupNow,
    onSuccess: (job) => {
      setWatching(job.id);
      toast({ tone: 'info', title: 'Cópia do banco na fila' });
    },
  });
  const morning = useMutation({
    mutationFn: opsApi.runMorning,
    onSuccess: (job) => {
      setWatching(job.id);
      toast({ tone: 'info', title: 'Rotina da manhã na fila' });
    },
  });
  const validTime = /^([01]\d|2[0-3]):[0-5]\d$/.test(time);
  const last = backups.data?.[0];

  return (
    <Section
      title="Rotinas"
      icon={Timer}
      description="Todo dia o app faz uma cópia do banco e, depois do horário da manhã, confere estoque, marcos dos vídeos e números das lojas."
      actions={state.enabled ? <Badge tone="good">Ligadas</Badge> : <Badge tone="warn">Paradas neste processo</Badge>}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-3">
          <Field label="Horário da manhã" hint="Hora deste computador. A rotina roda uma vez por dia, depois deste horário." error={validTime ? undefined : 'Use HH:MM, ex. 08:00'}>
            <Input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              onBlur={() => validTime && time !== state.morning_time && save.mutate({ morning_time: time })}
              className="w-32"
            />
          </Field>
          <Checkbox
            label="Cópia diária do banco"
            description="Guarda as 14 cópias mais novas na pasta backups."
            checked={state.backup}
            disabled={save.isPending}
            onChange={(v) => save.mutate({ backup: v })}
          />
          <Checkbox
            label="Atualizar as métricas de manhã"
            description="Pede a coleta de métricas dos canais ligados ao YouTube."
            checked={state.metrics}
            disabled={save.isPending}
            onChange={(v) => save.mutate({ metrics: v })}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" icon={DatabaseBackup} busy={backup.isPending || watched?.kind === 'backup'} onClick={() => backup.mutate()}>
              Fazer backup agora
            </Button>
            <Button size="sm" icon={Play} busy={morning.isPending || watched?.kind === 'routine_morning'} onClick={() => morning.mutate()}>
              Rodar a manhã agora
            </Button>
          </div>
          <ErrorBox error={backup.error ?? morning.error} title="Não foi possível iniciar" />
        </div>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] content-start gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted">Última manhã</dt>
          <dd className="text-fg">{state.last_morning ? formatRelative(state.last_morning) : 'ainda não rodou'}</dd>
          <dt className="text-muted">Última cópia</dt>
          <dd className="text-fg">
            {state.last_backup ? formatRelative(state.last_backup) : 'ainda não rodou'}
            {last ? <span className="text-subtle">{` · ${last.name}, ${formatBytes(last.size)}`}</span> : null}
          </dd>
          <dt className="text-muted">Cópias guardadas</dt>
          <dd className="text-fg tabular-nums">{backups.data ? backups.data.length : '…'}</dd>
          {state.last_morning_lines.length ? (
            <>
              <dt className="text-muted">A manhã fez</dt>
              <dd>
                <ul className="flex list-disc flex-col gap-0.5 pl-4 text-[13px] text-fg">
                  {state.last_morning_lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </dd>
            </>
          ) : null}
        </dl>
      </div>
    </Section>
  );
};
