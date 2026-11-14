import React from 'react';
import { PIPELINE_STEPS } from './meta';

/** Retângulo na proporção do vídeo (9:16 em pé, 16:9 deitado), para ver o formato num relance. */
export const FormatGlyph: React.FC<{ aspect: string; className?: string }> = ({ aspect, className = '' }) => {
  const [w, h] = aspect.split(':').map(Number);
  const ratio = w > 0 && h > 0 ? w / h : 1;
  // Cabe numa caixa de 16 px: o lado maior tem 16 px.
  const width = ratio >= 1 ? 16 : Math.max(6, Math.round(16 * ratio));
  const height = ratio >= 1 ? Math.max(6, Math.round(16 / ratio)) : 16;
  return (
    <span aria-hidden="true" className={`inline-flex h-4 w-4 shrink-0 items-center justify-center ${className}`}>
      <span className="block rounded-[3px] border-[1.5px] border-current" style={{ width, height }} />
    </span>
  );
};

/**
 * Trilha compacta das 5 etapas (Roteiro → Publicação), com as mesmas cores do Stepper da página do
 * projeto: concluída em verde, atual em âmbar, com falha em vermelho.
 */
export const StepTrack: React.FC<{ step: number; /** Índice da etapa em que a última tarefa falhou. */ failed?: number | null; className?: string }> = ({
  step,
  failed = null,
  className = '',
}) => {
  const shown = failed ?? step;
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div aria-hidden="true" className="flex flex-1 gap-1">
        {PIPELINE_STEPS.map((name, i) => (
          <span
            key={name}
            className={`h-1 flex-1 rounded-full ${
              i === failed ? 'bg-red-400' : i < step ? 'bg-emerald-400/60' : i === step ? 'bg-accent' : 'bg-line-strong'
            }`}
          />
        ))}
      </div>
      <span className={`shrink-0 text-xs tabular-nums ${failed != null ? 'text-red-300' : 'text-subtle'}`}>
        Etapa {shown + 1} de {PIPELINE_STEPS.length}: {PIPELINE_STEPS[shown]}
      </span>
    </div>
  );
};

/** Número da seção do formulário, num círculo discreto. */
export const StepNumber: React.FC<{ n: number }> = ({ n }) => (
  <span
    aria-hidden="true"
    className="mr-2 inline-flex h-5 w-5 -translate-y-px items-center justify-center rounded-full border border-line-strong align-middle text-xs font-semibold tabular-nums text-muted"
  >
    {n}
  </span>
);
