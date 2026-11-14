import { ChevronDown, type LucideIcon } from 'lucide-react';
import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Button, IconButton, type ButtonVariant } from '../../../ui';

/**
 * Painel que abre sob um botão (padrão de "disclosure"): fecha com Esc, clique fora ou ao escolher uma ação.
 * `children` recebe `close` para fechar depois de uma ação.
 */
export const Popover: React.FC<{
  label: string;
  icon: LucideIcon;
  /** Só ícone (o rótulo vira aria-label e dica). */
  iconOnly?: boolean;
  variant?: ButtonVariant;
  align?: 'left' | 'right';
  panelClassName?: string;
  children: (close: () => void) => React.ReactNode;
}> = ({ label, icon, iconOnly, variant = 'secondary', align = 'right', panelClassName = 'w-80', children }) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  // Deslocamento horizontal do painel (px, relativo ao contêiner): mantém o painel inteiro dentro da janela,
  // mesmo quando o botão fica perto da borda esquerda (cabeçalho quebrado, celular).
  const [left, setLeft] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      setLeft(null);
      return;
    }
    const place = () => {
      const root = rootRef.current;
      const panel = panelRef.current;
      if (!root || !panel) return;
      const r = root.getBoundingClientRect();
      const w = panel.offsetWidth;
      const margin = 16;
      const vw = document.documentElement.clientWidth;
      const wanted = align === 'right' ? r.right - w : r.left;
      const clamped = Math.max(margin, Math.min(wanted, vw - margin - w));
      setLeft(Math.round(clamped - r.left));
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  const common = {
    ref: buttonRef,
    'aria-expanded': open,
    'aria-controls': open ? panelId : undefined,
    onClick: () => setOpen((v) => !v),
  };

  return (
    <div ref={rootRef} className="relative">
      {iconOnly ? (
        <IconButton {...common} label={label} icon={icon} variant={variant} />
      ) : (
        <Button {...common} icon={icon} variant={variant}>
          {label}
          <ChevronDown size={14} aria-hidden="true" className={`-mr-0.5 transition-transform ${open ? 'rotate-180' : ''}`} />
        </Button>
      )}
      {open ? (
        <div
          ref={panelRef}
          id={panelId}
          style={left === null ? { visibility: 'hidden', left: 0 } : { left }}
          className={`absolute top-full z-30 mt-1.5 max-h-[calc(100dvh-6rem)] max-w-[calc(100vw-2rem)] animate-fade-in overflow-y-auto rounded-xl border border-line-strong bg-raised p-3 shadow-2xl shadow-black/60 ${panelClassName}`}
        >
          {children(close)}
        </div>
      ) : null}
    </div>
  );
};

/** Item de ação dentro do Popover (linha clicável com ícone, título e dica). */
export const PopoverItem: React.FC<{
  icon: LucideIcon;
  label: string;
  hint?: string;
  danger?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  onClick: () => void;
}> = ({ icon: Icon, label, hint, danger, disabled, disabledReason, onClick }) => (
  <button
    type="button"
    disabled={disabled}
    title={disabled ? disabledReason : undefined}
    onClick={onClick}
    className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
      danger ? 'text-red-300 hover:bg-red-400/10 disabled:hover:bg-transparent' : 'text-fg hover:bg-card disabled:hover:bg-transparent'
    }`}
  >
    <Icon size={16} strokeWidth={2} aria-hidden="true" className={`mt-0.5 shrink-0 ${danger ? '' : 'text-muted'}`} />
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="font-medium">{label}</span>
      {hint || (disabled && disabledReason) ? (
        <span className="text-[13px] leading-snug text-subtle">{disabled && disabledReason ? disabledReason : hint}</span>
      ) : null}
    </span>
  </button>
);
