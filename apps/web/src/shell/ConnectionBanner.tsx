import { WifiOff } from 'lucide-react';
import React from 'react';
import { Spinner } from '../ui';
import { useBackendConnection } from '../useEvents';

/** Faixa no topo da área principal enquanto o servidor local estiver fora do ar. */
export const ConnectionBanner: React.FC = () => {
  const { status } = useBackendConnection();
  if (status !== 'offline') return null;
  return (
    <div role="alert" className="flex shrink-0 items-start gap-3 border-b border-red-400/40 bg-red-400/10 px-4 py-2.5 text-sm text-red-100 sm:px-6 lg:px-8">
      <WifiOff size={18} strokeWidth={2} aria-hidden="true" className="mt-0.5 shrink-0 text-red-300" />
      <p className="min-w-0 flex-1">
        Sem conexão com o servidor local (127.0.0.1:8765). Se você fechou a janela do terminal, abra{' '}
        <code className="rounded bg-black/30 px-1 py-0.5 font-mono text-[13px] text-fg">scripts\iniciar.cmd</code> de novo. Tentando reconectar…
      </p>
      <Spinner className="mt-1 text-red-300" />
    </div>
  );
};
