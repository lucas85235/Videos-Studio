import React from 'react';

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' | 'subtle'; busy?: boolean };

export const Button: React.FC<ButtonProps> = ({ variant = 'subtle', busy, className = '', children, disabled, ...rest }) => {
  const styles = {
    primary: 'bg-amber-400 text-zinc-950 hover:bg-amber-300',
    subtle: 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700 border border-zinc-700',
    ghost: 'text-zinc-300 hover:bg-zinc-800',
    danger: 'bg-red-600/90 text-white hover:bg-red-500',
  }[variant];
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
      disabled={disabled || busy}
      {...rest}
    >
      {busy ? <Spinner /> : null}
      {children}
    </button>
  );
};

export const Spinner: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} />
);

export const Card: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className = '', ...rest }) => (
  <div className={`rounded-xl border border-zinc-800 bg-zinc-900/70 p-4 ${className}`} {...rest} />
);

export const Badge: React.FC<{ tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'info'; children: React.ReactNode; className?: string }> = ({
  tone = 'neutral',
  children,
  className = '',
}) => {
  const styles = {
    neutral: 'bg-zinc-800 text-zinc-300 border-zinc-700',
    good: 'bg-emerald-900/50 text-emerald-300 border-emerald-800',
    warn: 'bg-amber-900/40 text-amber-300 border-amber-800',
    bad: 'bg-red-900/40 text-red-300 border-red-800',
    info: 'bg-sky-900/40 text-sky-300 border-sky-800',
  }[tone];
  return <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs ${styles} ${className}`}>{children}</span>;
};

export const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode; className?: string }> = ({ label, hint, children, className = '' }) => (
  <label className={`flex flex-col gap-1 text-sm ${className}`}>
    <span className="font-medium text-zinc-300">{label}</span>
    {children}
    {hint ? <span className="text-xs text-zinc-500">{hint}</span> : null}
  </label>
);

const inputBase = 'rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-400';

export const Input: React.FC<React.InputHTMLAttributes<HTMLInputElement>> = ({ className = '', ...rest }) => (
  <input className={`${inputBase} ${className}`} {...rest} />
);

export const Textarea: React.FC<React.TextareaHTMLAttributes<HTMLTextAreaElement>> = ({ className = '', ...rest }) => (
  <textarea className={`${inputBase} ${className}`} {...rest} />
);

export const Select: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = ({ className = '', children, ...rest }) => (
  <select className={`${inputBase} ${className}`} {...rest}>
    {children}
  </select>
);

export const ProgressBar: React.FC<{ value: number; className?: string }> = ({ value, className = '' }) => (
  <div className={`h-1.5 w-full overflow-hidden rounded-full bg-zinc-800 ${className}`}>
    <div className="h-full rounded-full bg-amber-400 transition-all" style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
  </div>
);

export const ErrorBox: React.FC<{ error: unknown }> = ({ error }) =>
  error ? (
    <div className="whitespace-pre-wrap rounded-lg border border-red-800 bg-red-950/50 p-3 text-sm text-red-200">
      {error instanceof Error ? error.message : String(error)}
    </div>
  ) : null;

export const Empty: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="rounded-xl border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-500">{children}</div>
);

export const Tabs: React.FC<{ tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }> = ({ tabs, active, onChange }) => (
  <div className="flex gap-1 border-b border-zinc-800">
    {tabs.map((tab) => (
      <button
        key={tab.id}
        onClick={() => onChange(tab.id)}
        className={`-mb-px border-b-2 px-3 py-2 text-sm transition ${
          active === tab.id ? 'border-amber-400 text-zinc-50' : 'border-transparent text-zinc-400 hover:text-zinc-200'
        }`}
      >
        {tab.label}
      </button>
    ))}
  </div>
);

export const formatSeconds = (s: number) => {
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
};
