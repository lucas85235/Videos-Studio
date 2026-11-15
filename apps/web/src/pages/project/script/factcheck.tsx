/* Checagem de fatos (ADR 0003, bloco F): consulta compartilhada pelo painel e pelos selos dos trechos, e rótulos. */
import { useQuery } from '@tanstack/react-query';
import type React from 'react';
import { factcheckApi, type FactVerdict } from '../../../apiFactcheck';

export const factcheckKey = (projectId: string, lang?: string) => (lang ? ['factcheck', projectId, lang] : ['factcheck', projectId]);

/** Afirmações e resumo da checagem no idioma. A versão do roteiro entra na chave: editar um trecho refaz o "desatualizada". */
export const useFactCheck = (projectId: string, lang: string, version: number) =>
  useQuery({
    queryKey: [...factcheckKey(projectId, lang), version],
    queryFn: () => factcheckApi.get(projectId, lang),
    placeholderData: (previous) => previous,
  });

export const VERDICT_INFO: Record<FactVerdict, { label: string; className: string; hint: string }> = {
  ok: {
    label: 'ok',
    className: 'border-emerald-400/35 bg-emerald-400/10 text-emerald-300',
    hint: 'Confere com a fonte.',
  },
  corrigir: {
    label: 'corrigir',
    className: 'border-amber-300/35 bg-amber-300/10 text-amber-200',
    hint: 'A fonte diz outra coisa: aplique a correção ou edite o trecho.',
  },
  sem_fonte: {
    label: 'sem fonte',
    className: 'border-red-400/35 bg-red-400/10 text-red-300',
    hint: 'Sem fonte primária: o que não tem fonte sai do roteiro.',
  },
  boato: {
    label: 'boato',
    className: 'border-violet-400/35 bg-violet-400/10 text-violet-300',
    hint: 'Boato: diga quem disse e quando (boato desmentido entra como desmentido).',
  },
};

export const VerdictBadge: React.FC<{ verdict: FactVerdict }> = ({ verdict }) => {
  const info = VERDICT_INFO[verdict] ?? VERDICT_INFO.sem_fonte;
  return (
    <span
      title={info.hint}
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-semibold uppercase leading-4 tracking-wide ${info.className}`}
    >
      {info.label}
    </span>
  );
};

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
