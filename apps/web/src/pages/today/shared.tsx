/* Peças comuns da página Hoje. */
import { ArrowRight } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';

export const TODAY_KEY = ['today'] as const;

/** Duração curta em português: "45 s", "3 min 20 s", "1 h 05 min" (mesma regra do servidor). */
export const dur = (seconds: number): string => {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return r ? `${m} min ${r} s` : `${m} min`;
  }
  const h = Math.floor(s / 3600);
  return `${h} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
};

export const pct = (share: number) => `${Math.round(share * 100)}%`;

export const comma = (n: number) => String(n).replace('.', ',');

/** Data e hora local curtas ("15/11, 08:02"). */
export const shortWhen = (iso: string | null | undefined) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}, ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
};

/** Link para onde fazer: caminho do app vira <Link>; endereço externo abre em outra aba. */
export const WhereLink: React.FC<{ where: string; label?: string }> = ({ where, label = 'Abrir' }) => {
  if (!where) return null;
  const cls = 'inline-flex shrink-0 items-center gap-1 text-[13px] font-medium text-accent hover:underline';
  if (/^https?:\/\//.test(where)) {
    return (
      <a href={where} target="_blank" rel="noreferrer" className={cls}>
        {label}
        <ArrowRight size={14} aria-hidden="true" />
      </a>
    );
  }
  return (
    <Link to={where.startsWith('/') ? where : `/${where}`} className={cls}>
      {label}
      <ArrowRight size={14} aria-hidden="true" />
    </Link>
  );
};
