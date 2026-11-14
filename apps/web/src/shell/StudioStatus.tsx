import { Settings2, WifiOff } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import { Spinner } from '../ui';
import { useBackendConnection, type ConnectionStatus } from '../useEvents';
import { readinessOf, useProviders, type Readiness } from './data';

type Tone = 'good' | 'warn' | 'bad' | 'idle';

const DOT: Record<Tone, string> = {
  good: 'bg-emerald-400',
  warn: 'bg-amber-300',
  bad: 'bg-red-400',
  idle: 'bg-zinc-600',
};

type Line = { label: string; value: string; tone: Tone; title?: string };

const serverLine = (status: ConnectionStatus): Line =>
  status === 'online'
    ? { label: 'Servidor', value: 'Conectado', tone: 'good' }
    : status === 'connecting'
      ? { label: 'Servidor', value: 'Conectando…', tone: 'idle' }
      : { label: 'Servidor', value: 'Sem conexão', tone: 'bad' };

const providerLine = (label: string, r: Readiness, loading: boolean, failed: boolean): Line => {
  if (loading) return { label, value: 'Verificando…', tone: 'idle' };
  if (failed) return { label, value: 'Desconhecido', tone: 'idle' };
  if (!r.real) return { label, value: 'Modo prévia', tone: 'warn', title: 'Nenhum provedor real configurado: o vídeo sai só como prévia.' };
  if (r.quotaExhausted) return { label, value: 'Cota esgotada hoje', tone: 'warn', title: r.real.name };
  return { label, value: r.real.name, tone: 'good', title: `${r.real.name}: conteúdo real` };
};

/** Conexão com o servidor local e prontidão de voz e imagens (real × prévia). Leva às Configurações. */
export const StudioStatus: React.FC<{ collapsed?: boolean; onNavigate?: () => void }> = ({ collapsed, onNavigate }) => {
  const { status } = useBackendConnection();
  const providers = useProviders();
  const loading = providers.isLoading;
  const failed = !providers.data && Boolean(providers.error);
  const llm = readinessOf(providers.data, 'llm');
  const lines: Line[] = [
    serverLine(status),
    {
      label: 'Roteiro',
      ...(loading
        ? { value: 'Verificando…', tone: 'idle' as Tone }
        : failed
          ? { value: 'Desconhecido', tone: 'idle' as Tone }
          : (providers.data ?? []).some((p) => p.kind === 'llm' && p.available)
            ? { value: llm.real?.name ?? 'Disponível', tone: 'good' as Tone }
            : { value: 'Indisponível', tone: 'bad' as Tone }),
    },
    providerLine('Voz', readinessOf(providers.data, 'tts'), loading, failed),
    providerLine('Imagens', readinessOf(providers.data, 'image'), loading, failed),
  ];
  const summary = `Estado do estúdio: ${lines.map((l) => `${l.label.toLowerCase()} ${l.value.toLowerCase()}`).join('; ')}. Abrir configurações.`;
  const worst: Tone = lines.some((l) => l.tone === 'bad') ? 'bad' : lines.some((l) => l.tone === 'warn') ? 'warn' : lines.some((l) => l.tone === 'idle') ? 'idle' : 'good';

  if (collapsed) {
    return (
      <Link
        to="/configuracoes"
        onClick={onNavigate}
        title={lines.map((l) => `${l.label}: ${l.value}`).join('\n')}
        aria-label={summary}
        className="relative mx-auto flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-fg"
      >
        {status === 'offline' ? <WifiOff size={18} aria-hidden="true" /> : <Settings2 size={18} strokeWidth={1.75} aria-hidden="true" />}
        <span aria-hidden="true" className={`absolute right-1.5 top-1.5 h-2 w-2 rounded-full ring-2 ring-panel ${DOT[worst]}`} />
      </Link>
    );
  }

  return (
    <Link
      to="/configuracoes"
      onClick={onNavigate}
      aria-label={summary}
      className="block rounded-lg border border-line bg-card px-2.5 py-2 transition-colors hover:border-line-strong hover:bg-raised"
    >
      <span className="mb-1 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-subtle">Estúdio</span>
        {status === 'connecting' ? <Spinner className="h-3 w-3 text-subtle" /> : null}
      </span>
      <span className="flex flex-col gap-0.5">
        {lines.map((line) => (
          <span key={line.label} className="flex items-center gap-2 text-xs" title={line.title}>
            <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[line.tone]}`} />
            <span className="w-14 shrink-0 text-subtle">{line.label}</span>
            <span className={`min-w-0 truncate ${line.tone === 'warn' ? 'text-amber-200' : line.tone === 'bad' ? 'text-red-300' : 'text-muted'}`}>
              {line.value}
            </span>
          </span>
        ))}
      </span>
    </Link>
  );
};
