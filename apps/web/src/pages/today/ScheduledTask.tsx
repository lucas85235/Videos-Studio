import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, RefreshCw } from 'lucide-react';
import React from 'react';
import { opsApi, type RoutinesState } from '../../apiOps';
import { Badge, Button, formatRelative } from '../../ui';
import { CopyButton } from '../project/delivery/shared';

const OFFLINE_LABEL: Record<'ok' | 'parcial' | 'travada' | 'falhou', string> = {
  ok: 'rodou',
  parcial: 'rodou, mas parte da manhã falhou',
  travada: 'não rodou',
  falhou: 'falhou',
};

/** Tarefa agendada do Windows: rotinas com o Vulto fechado (existe? como instalar? última execução). */
export const ScheduledTaskBox: React.FC<{ state: RoutinesState }> = ({ state }) => {
  const client = useQueryClient();
  // o horário da manhã entra na chave: mudou, o comando de instalar e o aviso de horário diferente vêm de novo
  const key = ['ops', 'scheduled-task', state.morning_time];
  const task = useQuery({ queryKey: key, queryFn: () => opsApi.scheduledTask() });
  // "Conferir de novo" pula o cache de 1 min do backend (acabou de instalar a tarefa, por exemplo).
  const refresh = useMutation({
    mutationFn: () => opsApi.scheduledTask(true),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const info = task.data;
  const mismatch = Boolean(info?.supported && info.installed && info.time_mismatch);
  const skippedLater =
    state.last_offline_skipped &&
    (!state.last_offline_run || state.last_offline_skipped > state.last_offline_run);

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <CalendarClock className="h-4 w-4 text-muted" aria-hidden />
        <span className="font-medium text-fg">Com o Vulto fechado</span>
        {info ? (
          !info.supported ? (
            <Badge>Só no Windows</Badge>
          ) : mismatch ? (
            <Badge tone="warn">Tarefa em outro horário</Badge>
          ) : info.installed ? (
            <Badge tone="good">Tarefa agendada</Badge>
          ) : (
            <Badge tone="warn">Sem tarefa agendada</Badge>
          )
        ) : null}
        {info?.supported ? (
          <Button size="sm" variant="ghost" icon={RefreshCw} busy={refresh.isPending} onClick={() => refresh.mutate()}>
            Conferir de novo
          </Button>
        ) : null}
      </div>
      <p className="text-[13px] text-muted">
        {!info || !info.supported
          ? 'A tarefa agendada é do Windows. Em outro sistema, agende o comando "python -m studio rotinas" no agendador dele (cron, por exemplo).'
          : mismatch
            ? `A tarefa "${info.name}" roda todo dia às ${info.installed_time}, mas a manhã está marcada para ${info.morning_time}. Antes desse horário só a cópia do banco roda: com o app fechado o dia todo, a manhã fica sem rodar. Para acertar, cole este comando no PowerShell (ele troca a tarefa):`
            : info.installed
              ? `A tarefa "${info.name}" do Agendador de Tarefas roda as rotinas ao entrar no Windows e todo dia às ${info.installed_time ?? info.morning_time}, mesmo com o app fechado. Com o app aberto, quem roda é o app.`
              : `Sem a tarefa agendada, as rotinas só rodam com o app aberto. Para rodarem com ele fechado (ao entrar no Windows e todo dia às ${info.morning_time}, o horário da manhã), cole este comando no PowerShell (uma vez só):`}
      </p>
      {info?.error ? <p className="text-[13px] text-amber-200">{info.error}</p> : null}
      {info?.supported ? (
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded bg-raised px-2 py-1 font-mono text-[12px] text-fg">
            {info.installed && !mismatch ? info.remove_command : info.install_command}
          </code>
          <CopyButton
            text={info.installed && !mismatch ? info.remove_command : info.install_command}
            ariaLabel={
              info.installed && !mismatch ? 'Copiar o comando que tira a tarefa' : 'Copiar o comando que agenda a tarefa'
            }
          />
        </div>
      ) : null}
      {info?.supported && info.installed && !mismatch ? (
        <p className="text-[12px] text-subtle">
          O comando acima tira a tarefa. Se mudar o horário da manhã, aqui aparece o comando para agendar de novo.
        </p>
      ) : null}
      {state.last_offline_run ? (
        <p className="text-[13px] text-fg">
          Última execução com o app fechado: {formatRelative(state.last_offline_run)}
          {state.last_offline_status ? `, ${OFFLINE_LABEL[state.last_offline_status]}` : ''}
          {state.last_offline_message ? <span className="text-subtle">{` · ${state.last_offline_message}`}</span> : null}
        </p>
      ) : null}
      {skippedLater && state.last_offline_skipped ? (
        <p className="text-[12px] text-subtle">
          A tarefa foi chamada {state.last_offline_run ? 'de novo ' : ''}{formatRelative(state.last_offline_skipped)} e não rodou
          {state.last_offline_skipped_message ? ` · ${state.last_offline_skipped_message}` : ''}
        </p>
      ) : null}
    </div>
  );
};
