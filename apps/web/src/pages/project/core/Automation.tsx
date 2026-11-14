import { ArrowRight, Pause, SlidersHorizontal, Wand2 } from 'lucide-react';
import React from 'react';
import type { ProjectDetail } from '../../../api';
import { Button, Checkbox, ErrorBox, Spinner } from '../../../ui';
import { nextStep, type StepId } from './model';
import { Popover } from './Popover';

type Gates = { script: boolean; preview: boolean };

/** Motivo para o piloto automático não poder rodar agora (ou null). Segue o próximo passo do projeto. */
export const autopilotBlock = (detail: ProjectDetail, gates: Gates, busy: boolean): string | null => {
  if (busy) return 'Aguarde o trabalho em andamento terminar.';
  const { action } = nextStep(detail);
  if (action.kind === 'publish' || action.kind === 'done') return 'O vídeo já está pronto. Para refazer, use o menu Mais ações (…), ao lado.';
  const { doc } = detail;
  const scriptPending = doc.segments.length > 0 && !doc.script_approved && !doc.scenes.length && gates.script;
  if (scriptPending) return 'Aprove o roteiro primeiro: o piloto automático não pula a sua revisão.';
  if (action.kind === 'approve_preview' && gates.preview)
    return 'A prévia está pronta: aprove a prévia primeiro (aba Prévia). O piloto automático não pula a sua revisão.';
  return null;
};

/** O que o botão do piloto faz agora, a partir do próximo passo do projeto. */
const autopilotLabel = (detail: ProjectDetail, gates: Gates) => {
  const { action } = nextStep(detail);
  switch (action.kind) {
    case 'generate_script':
      return gates.script ? 'Gerar o roteiro e parar para revisão' : gates.preview ? 'Continuar até a prévia' : 'Continuar até o vídeo final';
    case 'approve_preview':
    case 'render':
    case 'publish':
    case 'done':
      return 'Continuar até o vídeo final';
    default:
      return gates.preview ? 'Continuar até a prévia' : 'Continuar até o vídeo final';
  }
};

const PathChip: React.FC<{ children: React.ReactNode; pause?: boolean; current?: boolean }> = ({ children, pause, current }) => (
  <span
    aria-current={current ? 'step' : undefined}
    className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs ${
      pause ? 'border-line-strong text-fg' : current ? 'border-accent/60 bg-card font-medium text-fg' : 'border-line-strong bg-card text-muted'
    }`}
  >
    {pause ? <Pause size={12} aria-hidden="true" className="text-muted" /> : null}
    {children}
    {current ? <span className="sr-only"> (etapa atual)</span> : null}
  </span>
);

/** Seta + o chip seguinte, juntos: a quebra de linha nunca deixa uma seta sozinha no fim da linha. */
const Next: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="inline-flex items-center gap-1 whitespace-nowrap">
    <ArrowRight size={12} aria-hidden="true" className="shrink-0 text-subtle" />
    {children}
  </span>
);

/** Botão "Automação": piloto automático e portões de aprovação, com o caminho que o piloto vai seguir. */
export const AutomationMenu: React.FC<{
  detail: ProjectDetail;
  gates: Gates;
  busy: boolean;
  onGate: (gate: 'script' | 'preview', value: boolean) => void;
  gatePending: boolean;
  gateError: unknown;
  onAutopilot: () => void;
  autopilotBusy: boolean;
}> = ({ detail, gates, busy, onGate, gatePending, gateError, onAutopilot, autopilotBusy }) => {
  const block = autopilotBlock(detail, gates, busy);
  const current: StepId = nextStep(detail).current;
  const isCurrent = (...ids: StepId[]) => ids.includes(current);
  return (
    <Popover label="Automação" icon={SlidersHorizontal} panelClassName="w-[24rem]">
      {(close) => (
        <div className="flex flex-col gap-3">
          <div>
            <div className="font-display text-[15px] font-semibold text-fg">Piloto automático</div>
            <p className="mt-0.5 text-[13px] leading-snug text-muted">
              Faz as etapas em sequência e pausa nas revisões marcadas abaixo. Usa o Claude, a cota de imagens e a da voz do vídeo final.
            </p>
          </div>
          <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
            <legend className="mb-2 flex items-center gap-2 p-0 text-sm font-medium text-fg">
              Paradas para revisão
              {gatePending ? (
                <span className="inline-flex items-center gap-1 text-[13px] font-normal text-subtle" role="status">
                  <Spinner /> Salvando…
                </span>
              ) : null}
            </legend>
            <Checkbox
              label="Parar para eu revisar o roteiro"
              description="Antes de criar cenas, narrar e gerar imagens."
              checked={gates.script}
              disabled={gatePending}
              onChange={(v) => onGate('script', v)}
            />
            <Checkbox
              label="Parar para eu revisar a prévia"
              description="Antes de renderizar o vídeo final."
              checked={gates.preview}
              disabled={gatePending}
              onChange={(v) => onGate('preview', v)}
            />
            {busy ? (
              <p className="text-[13px] leading-snug text-amber-200">Mudanças nas paradas valem para a próxima execução do piloto, não para a que está rodando.</p>
            ) : null}
          </fieldset>
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5" role="group" aria-label="Caminho do piloto automático">
            <PathChip current={isCurrent('script')}>Roteiro</PathChip>
            {gates.script ? (
              <Next>
                <PathChip pause>revisão</PathChip>
              </Next>
            ) : null}
            <Next>
              <PathChip current={isCurrent('scenes')}>Cenas</PathChip>
            </Next>
            <Next>
              <PathChip current={isCurrent('preview')}>Prévia</PathChip>
            </Next>
            {gates.preview ? (
              <Next>
                <PathChip pause>revisão</PathChip>
              </Next>
            ) : null}
            <Next>
              <PathChip current={isCurrent('video', 'publish')}>Vídeo</PathChip>
            </Next>
          </div>
          <ErrorBox error={gateError} title="Não foi possível salvar as paradas" />
          <div className="flex flex-col gap-1.5 border-t border-line pt-3">
            <Button
              icon={Wand2}
              busy={autopilotBusy}
              disabled={Boolean(block)}
              onClick={() => {
                onAutopilot();
                close();
              }}
            >
              {autopilotLabel(detail, gates)}
            </Button>
            {block ? <p className="text-[13px] leading-snug text-subtle">{block}</p> : null}
          </div>
        </div>
      )}
    </Popover>
  );
};
