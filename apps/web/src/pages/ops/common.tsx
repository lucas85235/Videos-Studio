// Peças locais das páginas de gestão (Canais, Agenda, Configurações).
import { X, type LucideIcon } from 'lucide-react';
import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { IconButton, useToast } from '../../ui';

/* ------------------------------------------------------------------ */
/* Âncoras                                                             */
/* ------------------------------------------------------------------ */

const prefersReducedMotion = () => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
};

/** Rola até a seção `id` (respeita o scroll-padding do <main>) e leva o foco para o título dela. */
export const jumpTo = (id: string) => {
  const el = document.getElementById(id);
  if (!el) return;
  const behavior: ScrollBehavior = prefersReducedMotion() ? 'auto' : 'smooth';
  // Rola só o contêiner de rolagem (o <main>), nunca a janela: scrollIntoView também deslocaria o documento.
  let scroller = el.parentElement;
  while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement;
  if (scroller) {
    // Desconta o cabeçalho fixo da página (PageHeader sticky), que pode ser mais alto que o scroll-padding.
    const sticky = scroller.querySelector<HTMLElement>('header.sticky');
    const padding = Math.max(parseFloat(getComputedStyle(scroller).scrollPaddingTop) || 0, sticky ? sticky.offsetHeight + 12 : 0);
    const top = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - padding;
    scroller.scrollTo({ top: Math.max(0, top), behavior });
  } else {
    el.scrollIntoView({ behavior, block: 'start' });
  }
  const target = el.querySelector<HTMLElement>('h2') ?? el;
  if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });
};

/** Link de âncora para uma seção da mesma página (sem trocar a rota). */
export const AnchorLink: React.FC<React.AnchorHTMLAttributes<HTMLAnchorElement> & { target: string }> = ({ target, onClick, children, ...rest }) => (
  <a
    {...rest}
    href={`#${target}`}
    onClick={(e) => {
      onClick?.(e);
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      jumpTo(target);
    }}
  >
    {children}
  </a>
);

/* ------------------------------------------------------------------ */
/* Copiar com retorno                                                  */
/* ------------------------------------------------------------------ */

/** Copia texto e confirma com um aviso; `copied` guarda a chave do que foi copiado por 2 s. */
export const useCopy = () => {
  const { toast } = useToast();
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = useCallback(
    async (key: string, text: string, title: string, description?: string) => {
      try {
        if (!navigator.clipboard) throw new Error('A área de transferência não está disponível nesta janela.');
        await navigator.clipboard.writeText(text);
        setCopied(key);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setCopied(null), 2000);
        toast({ tone: 'good', title, description, durationMs: 3000 });
      } catch {
        toast({ tone: 'bad', title: 'Não foi possível copiar', description: 'O navegador bloqueou a área de transferência. Selecione o texto e use Ctrl+C.' });
      }
    },
    [toast],
  );
  return { copied, copy };
};

/* ------------------------------------------------------------------ */
/* Contadores do topo                                                  */
/* ------------------------------------------------------------------ */

export type StatTone = 'neutral' | 'warn' | 'good';

const STAT_TONES: Record<StatTone, { box: string; value: string; icon: string }> = {
  neutral: { box: 'border-line bg-card hover:border-line-strong hover:bg-raised', value: 'text-fg', icon: 'text-muted' },
  warn: { box: 'border-amber-300/35 bg-amber-300/10 hover:border-amber-300/60', value: 'text-amber-200', icon: 'text-amber-200' },
  good: { box: 'border-emerald-400/30 bg-emerald-400/10 hover:border-emerald-400/50', value: 'text-emerald-300', icon: 'text-emerald-300' },
};

/** Contador clicável que leva a uma seção da página. */
export const StatTile: React.FC<{ target: string; icon: LucideIcon; label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: StatTone }> = ({
  target,
  icon: Icon,
  label,
  value,
  hint,
  tone = 'neutral',
}) => {
  const t = STAT_TONES[tone];
  return (
    <AnchorLink target={target} className={`flex min-w-0 flex-col gap-1 rounded-xl border px-3.5 py-3 transition-colors ${t.box}`}>
      <span className="flex items-center gap-1.5 text-[13px] text-muted">
        <Icon size={16} strokeWidth={2} aria-hidden="true" className={`shrink-0 ${t.icon}`} />
        {label}
      </span>
      <span className={`font-display text-2xl font-bold tabular-nums leading-tight ${t.value}`}>{value}</span>
      {hint ? <span className="text-[13px] leading-snug text-subtle">{hint}</span> : null}
    </AnchorLink>
  );
};

/* ------------------------------------------------------------------ */
/* Chips de filtro (grupo com aria-pressed)                            */
/* ------------------------------------------------------------------ */

export const chipClass = (on: boolean) =>
  `inline-flex min-h-7 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
    on ? 'border-accent/60 bg-accent/15 text-accent' : 'border-line-strong bg-raised text-muted hover:border-[#4a4a56] hover:text-fg'
  }`;

export const ChipGroup: React.FC<{
  label: string;
  options: { id: string; label: React.ReactNode; count?: number }[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}> = ({ label, options, value, onChange, className = '' }) => (
  <div role="group" aria-label={label} className={`flex flex-wrap items-center gap-1.5 ${className}`}>
    {options.map((o) => (
      <button key={o.id} type="button" aria-pressed={o.id === value} onClick={() => onChange(o.id)} className={chipClass(o.id === value)}>
        {o.label}
        {o.count != null ? <span className="tabular-nums opacity-80">{o.count}</span> : null}
      </button>
    ))}
  </div>
);

/* ------------------------------------------------------------------ */
/* Painel lateral (diálogo modal)                                      */
/* ------------------------------------------------------------------ */

/**
 * Painel à direita, em <dialog> modal (Tab não sai, Esc pede para fechar). `onRequestClose` decide se fecha
 * (ex.: confirmar descarte de alterações). O foco volta para quem abriu o painel quando ele sai do DOM.
 */
export const Sheet: React.FC<{
  title: React.ReactNode;
  description?: React.ReactNode;
  onRequestClose: () => void;
  onSubmit?: () => void;
  footer?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, description, onRequestClose, onSubmit, footer, children }) => {
  const titleId = useId();
  const descId = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const requestClose = useRef(onRequestClose);
  requestClose.current = onRequestClose;

  // Quem abriu o painel, lido no primeiro render (antes de o foco entrar no diálogo; no StrictMode o efeito roda duas vezes).
  const opener = useRef<HTMLElement | null>(typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const restore = opener.current;
    if (!el.open) el.showModal();
    el.querySelector<HTMLElement>('input, textarea, select')?.focus();
    return () => {
      // No StrictMode o efeito roda duas vezes com o diálogo ainda no DOM: só devolve o foco se ele saiu de fato.
      window.setTimeout(() => {
        if (!ref.current?.isConnected && restore?.isConnected) restore.focus();
      }, 0);
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        requestClose.current();
      }}
      onClose={() => {
        // No Chrome (CloseWatcher), um segundo Esc sem ativação do usuário fecha o <dialog> ignorando o preventDefault.
        // O React continua com o painel aberto: reabre e devolve a decisão ao pai (confirmar descarte ou fechar pelo estado).
        const el = ref.current;
        if (el?.isConnected && !el.open) {
          el.showModal();
          requestClose.current();
        }
      }}
      onMouseDown={(e) => {
        if (e.target !== e.currentTarget) return;
        const r = e.currentTarget.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) requestClose.current();
      }}
      className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-[min(520px,100vw)] animate-fade-in border-0 border-l border-line-strong bg-panel p-0 text-fg shadow-2xl shadow-black/60 backdrop:bg-black/50"
    >
      <form
        className="flex h-full flex-col"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit?.();
        }}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="font-display text-lg font-semibold text-fg">
              {title}
            </h2>
            {description ? (
              <p id={descId} className="mt-0.5 text-[13px] text-muted">
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="Fechar painel" icon={X} onClick={() => requestClose.current()} className="-mr-1.5 -mt-0.5" />
        </header>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-panel px-5 py-3">{footer}</footer> : null}
      </form>
    </dialog>
  );
};
