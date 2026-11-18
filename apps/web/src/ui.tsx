import {
  Check,
  ChevronRight,
  CircleCheck,
  CircleX,
  Copy,
  Info,
  RotateCw,
  TriangleAlert,
  X,
  type LucideIcon,
} from 'lucide-react';
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link } from 'react-router-dom';
import { ApiError, NETWORK_ERROR_MESSAGE } from './api';
import { usePageTitle } from './documentTitle';

/* ------------------------------------------------------------------ */
/* Botões                                                              */
/* ------------------------------------------------------------------ */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle';
export type ButtonSize = 'sm' | 'md';

const BUTTON_BASE =
  'inline-flex select-none items-center justify-center rounded-lg font-medium leading-tight transition-colors duration-150 disabled:cursor-not-allowed aria-busy:cursor-progress aria-busy:opacity-70';

// A opacidade de desabilitado fica em cada variante: o primário desabilitado vira neutro (sem âmbar a 50%),
// para não parecer a ação âmbar da tela enquanto ainda não está liberado.
const BUTTON_VARIANTS: Record<Exclude<ButtonVariant, 'subtle'>, string> = {
  primary:
    'bg-accent font-semibold text-on-accent hover:bg-accent-strong disabled:bg-raised disabled:text-subtle disabled:ring-1 disabled:ring-inset disabled:ring-line-strong disabled:hover:bg-raised',
  secondary:
    'border border-line-strong bg-raised text-fg hover:border-[#4a4a56] hover:bg-[#24242c] disabled:opacity-50 disabled:hover:border-line-strong disabled:hover:bg-raised',
  ghost: 'text-muted hover:bg-raised hover:text-fg disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-muted',
  danger:
    'border border-red-400/40 bg-red-400/10 text-red-300 hover:border-red-400/60 hover:bg-red-400/20 hover:text-red-200 disabled:opacity-50 disabled:hover:bg-red-400/10',
};

// Em tela de toque (ponteiro grosso), os alvos crescem para 36-40 px.
const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'min-h-7 gap-1.5 px-2.5 py-1 text-xs pointer-coarse:min-h-9',
  md: 'min-h-8 gap-2 px-3 py-1.5 text-sm pointer-coarse:min-h-10',
};

const ICON_BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 w-7 pointer-coarse:h-9 pointer-coarse:w-9',
  md: 'h-8 w-8 pointer-coarse:h-10 pointer-coarse:w-10',
};

const resolveVariant = (variant: ButtonVariant = 'secondary') => (variant === 'subtle' ? 'secondary' : variant);

/** Classes do Button, para usar em <Link>/<a> com cara de botão. */
export const buttonClass = (opts: { variant?: ButtonVariant; size?: ButtonSize } = {}) =>
  `${BUTTON_BASE} ${BUTTON_VARIANTS[resolveVariant(opts.variant)]} ${BUTTON_SIZES[opts.size ?? 'md']}`;

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  ref?: React.Ref<HTMLButtonElement>;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  busy?: boolean;
};

export const Button: React.FC<ButtonProps> = ({
  variant = 'secondary',
  size = 'md',
  icon: Icon,
  busy,
  className = '',
  children,
  disabled,
  type = 'button',
  onClick,
  ...rest
}) => (
  // Ocupado não vira `disabled`: o botão continua focável (o foco do teclado não cai no <body>), mas ignora cliques.
  <button
    {...rest}
    type={type}
    className={`${buttonClass({ variant, size })} ${className}`}
    disabled={disabled}
    aria-disabled={busy && !disabled ? true : rest['aria-disabled']}
    aria-busy={busy || undefined}
    onClick={busy ? (e) => e.preventDefault() : onClick}
  >
    {busy ? <Spinner /> : Icon ? <Icon size={size === 'sm' ? 14 : 16} strokeWidth={2} aria-hidden="true" className="shrink-0" /> : null}
    {children}
  </button>
);

type IconButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  ref?: React.Ref<HTMLButtonElement>;
  label: string;
  icon: LucideIcon;
  size?: ButtonSize;
  variant?: ButtonVariant;
  busy?: boolean;
};

/** Botão só com ícone: o rótulo vira aria-label e dica (title). */
export const IconButton: React.FC<IconButtonProps> = ({
  label,
  icon: Icon,
  size = 'md',
  variant = 'ghost',
  busy,
  className = '',
  disabled,
  type = 'button',
  onClick,
  ...rest
}) => (
  <button
    {...rest}
    type={type}
    aria-label={label}
    title={label}
    className={`${BUTTON_BASE} ${BUTTON_VARIANTS[resolveVariant(variant)]} ${ICON_BUTTON_SIZES[size]} shrink-0 p-0 ${className}`}
    disabled={disabled}
    aria-disabled={busy && !disabled ? true : rest['aria-disabled']}
    aria-busy={busy || undefined}
    onClick={busy ? (e) => e.preventDefault() : onClick}
  >
    {busy ? <Spinner /> : <Icon size={size === 'sm' ? 14 : 16} strokeWidth={2} aria-hidden="true" />}
  </button>
);

export const Spinner: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span
    aria-hidden="true"
    className={`inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
  />
);

/* ------------------------------------------------------------------ */
/* Superfícies e estrutura de página                                   */
/* ------------------------------------------------------------------ */

type CardProps = React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean };

export const Card: React.FC<CardProps> = ({ className = '', interactive, ...rest }) => (
  <div
    data-card=""
    className={`rounded-xl border border-line bg-card p-4 ${
      interactive ? 'transition-colors duration-150 hover:border-line-strong hover:bg-raised' : ''
    } ${className}`}
    {...rest}
  />
);

export const Section: React.FC<{
  title: React.ReactNode;
  /** Editor do título (renomear em linha): aparece no lugar do título, fora do `<h2>`, que segue com o título atual
   * (só para leitor de tela) e continua dando nome à seção. */
  titleEditor?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: LucideIcon;
  children?: React.ReactNode;
  className?: string;
}> = ({ title, titleEditor, description, actions, icon: Icon, children, className = '' }) => {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={`rounded-xl border border-line bg-card ${className}`}>
      {/* As ações ficam sempre no topo à direita, qualquer que seja o tamanho do texto; só empilham num cabeçalho estreito. */}
      <header className="@container border-b border-line px-4 py-3">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 @max-sm:grid-cols-1">
          <div className="flex min-w-0 items-start gap-2.5">
            {Icon ? <Icon size={16} strokeWidth={2} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" /> : null}
            <div className="min-w-0">
              <h2
                id={headingId}
                className={titleEditor ? 'sr-only' : 'font-display text-[15px] font-semibold leading-6 text-fg [overflow-wrap:anywhere]'}
              >
                {title}
              </h2>
              {titleEditor ? <div className="min-w-0">{titleEditor}</div> : null}
              {description ? <div className="mt-0.5 text-[13px] leading-snug text-muted">{description}</div> : null}
            </div>
          </div>
          {actions ? <div className="flex flex-wrap items-center justify-end gap-2 @max-sm:justify-start">{actions}</div> : null}
        </div>
      </header>
      <div className="flex flex-col gap-3 p-4">{children}</div>
    </section>
  );
};

const PAGE_WIDTHS = {
  narrow: 'max-w-[960px]',
  default: 'max-w-[1200px]',
  wide: 'max-w-[1440px]',
  full: 'max-w-none',
} as const;

/** Contêiner de página: largura máxima e respiro padronizados. */
export const Page: React.FC<{ children: React.ReactNode; width?: keyof typeof PAGE_WIDTHS; className?: string }> = ({
  children,
  width = 'default',
  className = '',
}) => <div className={`mx-auto w-full ${PAGE_WIDTHS[width]} px-4 pb-10 pt-6 sm:px-6 lg:px-8 ${className}`}>{children}</div>;

export type Crumb = { label: string; to?: string };

export const PageHeader: React.FC<{
  title: React.ReactNode;
  description?: React.ReactNode;
  breadcrumb?: Crumb[];
  actions?: React.ReactNode;
  meta?: React.ReactNode;
  sticky?: boolean;
}> = ({ title, description, breadcrumb, actions, meta, sticky }) => {
  usePageTitle(typeof title === 'string' ? title : null);
  return (
    <header
      className={`mb-6 flex flex-col gap-2 ${
        sticky
          ? // -mt-6 pt-6 anula o respiro do <Page>: o título fica na mesma altura e tamanho das páginas sem cabeçalho fixo.
            'sticky top-0 z-20 -mx-4 -mt-6 border-b border-line bg-canvas/90 px-4 pb-3 pt-6 backdrop-blur-md sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8'
          : ''
      }`}
    >
      {breadcrumb?.length ? (
        <nav aria-label="Trilha de navegação">
          <ol className="flex flex-wrap items-center gap-1 text-[13px] text-subtle">
            {breadcrumb.map((crumb, i) => (
              <li key={`${crumb.label}-${i}`} className="flex items-center gap-1">
                {i > 0 ? <ChevronRight size={14} aria-hidden="true" className="text-zinc-600" /> : null}
                {crumb.to ? (
                  <Link to={crumb.to} className="rounded-sm hover:text-fg">
                    {crumb.label}
                  </Link>
                ) : (
                  <span aria-current={i === breadcrumb.length - 1 ? 'page' : undefined} className="text-muted">
                    {crumb.label}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-2xl font-bold tracking-tight text-fg [overflow-wrap:anywhere]">{title}</h1>
          {description ? <div className="mt-1 max-w-prose text-sm text-muted">{description}</div> : null}
          {meta ? <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-subtle">{meta}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
};

/* ------------------------------------------------------------------ */
/* Selos                                                               */
/* ------------------------------------------------------------------ */

export type BadgeTone = 'neutral' | 'good' | 'warn' | 'bad' | 'info' | 'accent';

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: 'border-line-strong bg-raised text-muted',
  good: 'border-emerald-400/35 bg-emerald-400/10 text-emerald-300',
  warn: 'border-amber-300/35 bg-amber-300/10 text-amber-200',
  bad: 'border-red-400/35 bg-red-400/10 text-red-300',
  info: 'border-sky-400/35 bg-sky-400/10 text-sky-300',
  accent: 'border-accent/40 bg-accent/15 text-accent',
};

export const Badge: React.FC<{ tone?: BadgeTone; icon?: LucideIcon; children: React.ReactNode; className?: string; title?: string }> = ({
  tone = 'neutral',
  icon: Icon,
  children,
  className = '',
  title,
}) => (
  <span
    title={title}
    className={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border [overflow-wrap:anywhere] px-1.5 py-0.5 text-xs font-medium leading-4 ${BADGE_TONES[tone]} ${className}`}
  >
    {Icon ? <Icon size={12} strokeWidth={2.25} aria-hidden="true" className="shrink-0" /> : null}
    {children}
  </span>
);

/* ------------------------------------------------------------------ */
/* Formulários                                                         */
/* ------------------------------------------------------------------ */

const inputBase =
  'rounded-lg border border-line-input bg-canvas px-2.5 py-1.5 text-sm text-fg placeholder:text-subtle transition-colors duration-150 hover:border-[#7a7a86] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:cursor-not-allowed disabled:border-line disabled:bg-card disabled:text-subtle disabled:opacity-75 disabled:hover:border-line aria-[invalid=true]:border-red-400/70 aria-[invalid=true]:focus:ring-red-400/25';

type Invalid = { invalid?: boolean };

const invalidAttr = (invalid: boolean | undefined, aria: React.AriaAttributes['aria-invalid']) =>
  invalid ? true : aria && aria !== 'false' ? aria : undefined;

// Atributos calculados vêm DEPOIS do spread, para que um 'aria-invalid' indefinido (ex.: vindo do Field) não apague `invalid`.
export const Input: React.FC<React.InputHTMLAttributes<HTMLInputElement> & Invalid> = ({ className = '', invalid, 'aria-invalid': ariaInvalid, ...rest }) => (
  <input {...rest} aria-invalid={invalidAttr(invalid, ariaInvalid)} className={`${inputBase} ${className}`} />
);

export const Textarea: React.FC<React.TextareaHTMLAttributes<HTMLTextAreaElement> & Invalid> = ({
  className = '',
  invalid,
  'aria-invalid': ariaInvalid,
  ...rest
}) => <textarea {...rest} aria-invalid={invalidAttr(invalid, ariaInvalid)} className={`${inputBase} leading-relaxed ${className}`} />;

export const Select: React.FC<React.SelectHTMLAttributes<HTMLSelectElement> & Invalid> = ({
  className = '',
  children,
  invalid,
  'aria-invalid': ariaInvalid,
  ...rest
}) => (
  <select {...rest} aria-invalid={invalidAttr(invalid, ariaInvalid)} className={`${inputBase} pr-7 ${className}`}>
    {children}
  </select>
);

/** Elementos que podem receber <label htmlFor>. Outros filhos (grupos de botões, listas) recebem só um texto de rótulo. */
const LABELABLE = new Set<unknown>(['input', 'select', 'textarea', Input, Textarea, Select]);

const FIELD_LABEL = 'text-sm font-medium text-fg';
const FIELD_HINT = 'text-[13px] leading-snug text-subtle';
const FIELD_ERROR = 'flex items-start gap-1.5 text-[13px] leading-snug text-red-300';

const FieldError: React.FC<{ id: string; children: React.ReactNode }> = ({ id, children }) => (
  <p id={id} className={FIELD_ERROR}>
    <CircleX size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
    <span>{children}</span>
  </p>
);

type FieldChildProps = { id?: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean | 'true' | 'false' };

/**
 * Campo com rótulo. Para input/select/textarea, o rótulo é um <label htmlFor> ligado ao controle (id via useId).
 * Para qualquer outro filho, o rótulo é só texto e o filho fica num grupo rotulado (nunca dentro de um <label>).
 */
export const Field: React.FC<{
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  children: React.ReactElement;
  className?: string;
}> = ({ label, hint, error, children, className = '' }) => {
  const baseId = useId();
  const hintId = `${baseId}-dica`;
  const errorId = `${baseId}-erro`;
  const labelId = `${baseId}-rotulo`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined;

  if (React.isValidElement<FieldChildProps>(children) && LABELABLE.has(children.type)) {
    const controlId = children.props.id ?? `${baseId}-campo`;
    const extra: FieldChildProps = {
      id: controlId,
      'aria-describedby': [children.props['aria-describedby'], describedBy].filter(Boolean).join(' ') || undefined,
    };
    // Só define a chave com erro: um valor indefinido apagaria o `invalid` do filho.
    if (error) extra['aria-invalid'] = true;
    const control = React.cloneElement(children, extra);
    return (
      <div className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
        <label htmlFor={controlId} id={labelId} data-field-label="" className={FIELD_LABEL}>
          {label}
        </label>
        {control}
        {hint ? (
          <p id={hintId} className={FIELD_HINT}>
            {hint}
          </p>
        ) : null}
        {error ? <FieldError id={errorId}>{error}</FieldError> : null}
      </div>
    );
  }

  return (
    <div role="group" aria-labelledby={labelId} aria-describedby={describedBy} className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
      <div id={labelId} data-field-label="" className={FIELD_LABEL}>
        {label}
      </div>
      {children}
      {hint ? (
        <p id={hintId} className={FIELD_HINT}>
          {hint}
        </p>
      ) : null}
      {error ? <FieldError id={errorId}>{error}</FieldError> : null}
    </div>
  );
};

/** Grupo de controles (chips, caixas, botões) com <fieldset>/<legend>, no mesmo visual do Field. */
export const FieldGroup: React.FC<{ legend: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode; className?: string }> = ({
  legend,
  hint,
  children,
  className = '',
}) => {
  const hintId = useId();
  return (
    <fieldset aria-describedby={hint ? hintId : undefined} className={`m-0 min-w-0 border-0 p-0 ${className}`}>
      <legend data-field-label="" className={`mb-1.5 p-0 ${FIELD_LABEL}`}>
        {legend}
      </legend>
      <div className="flex min-w-0 flex-col gap-1.5">
        {children}
        {hint ? (
          <p id={hintId} className={FIELD_HINT}>
            {hint}
          </p>
        ) : null}
      </div>
    </fieldset>
  );
};

export const Checkbox: React.FC<{
  label: React.ReactNode;
  description?: React.ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}> = ({ label, description, checked, onChange, disabled, className = '' }) => {
  const descId = useId();
  return (
    <label className={`flex items-start gap-2.5 text-sm ${disabled ? 'cursor-not-allowed opacity-60' : ''} ${className}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-describedby={description ? descId : undefined}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 rounded border-line-strong disabled:cursor-not-allowed"
      />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span data-field-label="" className="text-fg">
          {label}
        </span>
        {description ? (
          <span id={descId} className={FIELD_HINT}>
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
};

/* ------------------------------------------------------------------ */
/* Controle segmentado (filtros e opções exclusivas)                   */
/* ------------------------------------------------------------------ */

/**
 * Classe de uma opção do controle segmentado. A selecionada fica neutra (fundo elevado e contorno), não âmbar:
 * o âmbar fica para a ação principal da tela e para o foco do teclado.
 */
export const segmentClass = (on: boolean, size: ButtonSize = 'md') =>
  `inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-offset-0 ${
    size === 'sm' ? 'min-h-6 px-2 py-0.5 text-xs pointer-coarse:min-h-8' : 'min-h-7 px-2.5 py-1 text-[13px] pointer-coarse:min-h-9'
  } ${on ? 'bg-raised text-fg shadow-sm shadow-black/30 ring-1 ring-inset ring-line-strong' : 'text-muted enabled:hover:text-fg'}`;

/** Trilho do controle segmentado (para quem monta os botões à mão com `segmentClass`). */
export const SEGMENTED_TRACK = 'flex w-fit max-w-full flex-wrap gap-0.5 rounded-lg border border-line bg-canvas p-0.5';

export type SegmentOption<T extends string> = {
  id: T;
  label: React.ReactNode;
  icon?: LucideIcon;
  /** Contagem ao lado do rótulo (ex.: quantos itens o filtro mostra). */
  count?: React.ReactNode;
  disabled?: boolean;
  title?: string;
};

/** Grupo de botões exclusivos (aria-pressed): filtros de lista e escolhas curtas. */
export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  size = 'md',
  disabled,
  className = '',
}: {
  /** Nome do grupo para o leitor de tela (dentro de um FieldGroup a legenda já nomeia; pode omitir). */
  label?: string;
  options: SegmentOption<T>[];
  value: T;
  onChange: (id: T) => void;
  size?: ButtonSize;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={`${SEGMENTED_TRACK} ${className}`}>
      {options.map((o) => {
        const on = o.id === value;
        const Icon = o.icon;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            title={o.title}
            disabled={disabled || o.disabled}
            onClick={() => onChange(o.id)}
            className={segmentClass(on, size)}
          >
            {Icon ? <Icon size={14} strokeWidth={2} aria-hidden="true" className="shrink-0" /> : null}
            {o.label}
            {o.count != null ? <span className={`tabular-nums ${on ? 'text-muted' : 'text-subtle'}`}>{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Abas                                                                */
/* ------------------------------------------------------------------ */

export type TabItem = { id: string; label: React.ReactNode; icon?: LucideIcon; badge?: React.ReactNode; disabled?: boolean };

const tabDomId = (prefix: string, id: string) => `${prefix}-aba-${id}`;
const panelDomId = (prefix: string, id: string) => `${prefix}-painel-${id}`;

/**
 * Props para o painel da aba ativa (role=tabpanel ligado à aba). Use o mesmo `idPrefix` passado às Tabs;
 * com mais de uma Tabs na mesma página, cada uma precisa do seu `idPrefix` (o padrão 'tabs' repetiria ids).
 */
export const tabPanelProps = (id: string, idPrefix = 'tabs') => ({
  role: 'tabpanel' as const,
  id: panelDomId(idPrefix, id),
  'aria-labelledby': tabDomId(idPrefix, id),
  tabIndex: 0,
});

export const Tabs: React.FC<{
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  ariaLabel?: string;
  idPrefix?: string;
  className?: string;
}> = ({ tabs, active, onChange, ariaLabel, idPrefix = 'tabs', className = '' }) => {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = tabs.filter((t) => !t.disabled);
  // Se `active` não for uma aba habilitada (ex.: ?aba= inválido), a primeira habilitada recebe o foco do Tab.
  const focusId = enabled.some((t) => t.id === active) ? active : enabled[0]?.id;
  // aria-controls só quando o painel existe de fato (a página usou tabPanelProps).
  const [hasPanel, setHasPanel] = useState(false);
  useLayoutEffect(() => {
    const exists = Boolean(document.getElementById(panelDomId(idPrefix, active)));
    setHasPanel((prev) => (prev === exists ? prev : exists));
  });

  // Lista que rola na horizontal (celular): a aba ativa fica inteira à vista e as bordas com mais abas esmaecem.
  const listRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState<{ start: boolean; end: boolean }>({ start: false, end: false });
  const updateEdges = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const start = el.scrollLeft > 1;
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
  }, []);
  useLayoutEffect(() => {
    const list = listRef.current;
    const tab = refs.current[active];
    if (list && tab && list.scrollWidth > list.clientWidth) {
      // scrollLeft (e não scrollIntoView): só a lista rola, a página fica onde está. A folga deixa a aba fora do esmaecimento.
      const pad = 32;
      const l = list.getBoundingClientRect();
      const t = tab.getBoundingClientRect();
      if (t.left < l.left + pad) list.scrollLeft -= l.left + pad - t.left;
      else if (t.right > l.right - pad) list.scrollLeft += t.right - (l.right - pad);
    }
    updateEdges();
  }, [active, updateEdges]);
  useEffect(() => {
    const el = listRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(updateEdges);
    ro.observe(el);
    return () => ro.disconnect();
  }, [updateEdges]);
  const fade = 28;
  const mask =
    edges.start && edges.end
      ? `linear-gradient(to right, transparent, #000 ${fade}px, #000 calc(100% - ${fade}px), transparent)`
      : edges.end
        ? `linear-gradient(to right, #000 calc(100% - ${fade}px), transparent)`
        : edges.start
          ? `linear-gradient(to right, transparent, #000 ${fade}px)`
          : undefined;

  const focusTab = (id: string) => {
    onChange(id);
    refs.current[id]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent, id: string) => {
    const index = enabled.findIndex((t) => t.id === id);
    if (index < 0 || !enabled.length) return;
    let next: TabItem | undefined;
    if (event.key === 'ArrowRight') next = enabled[(index + 1) % enabled.length];
    else if (event.key === 'ArrowLeft') next = enabled[(index - 1 + enabled.length) % enabled.length];
    else if (event.key === 'Home') next = enabled[0];
    else if (event.key === 'End') next = enabled[enabled.length - 1];
    if (next) {
      event.preventDefault();
      focusTab(next.id);
    }
  };

  return (
    // A linha de base é uma sombra interna (não uma borda): nada passa da caixa, então não surge rolagem vertical.
    <div
      ref={listRef}
      role="tablist"
      aria-label={ariaLabel}
      onScroll={updateEdges}
      style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
      className={`flex gap-1 overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_var(--color-line)] ${className}`}
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[tab.id] = el;
            }}
            type="button"
            role="tab"
            id={tabDomId(idPrefix, tab.id)}
            aria-selected={selected}
            aria-controls={selected && hasPanel ? panelDomId(idPrefix, tab.id) : undefined}
            tabIndex={tab.id === focusId ? 0 : -1}
            disabled={tab.disabled}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKeyDown(e, tab.id)}
            className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-t-md px-3 py-2.5 text-sm font-medium transition-[color,box-shadow] focus-visible:outline-offset-[-2px] disabled:cursor-not-allowed disabled:opacity-50 ${
              selected
                ? 'text-fg shadow-[inset_0_-2px_0_var(--color-accent)]'
                : 'text-muted hover:text-fg hover:shadow-[inset_0_-2px_0_var(--color-line-strong)]'
            }`}
          >
            {Icon ? <Icon size={16} strokeWidth={2} aria-hidden="true" className={selected ? 'text-accent' : ''} /> : null}
            {tab.label}
            {tab.badge != null ? tab.badge : null}
          </button>
        );
      })}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Estados: erro, vazio, carregando, progresso                         */
/* ------------------------------------------------------------------ */

const NETWORK_FAILURE = /^(failed to fetch|networkerror when attempting to fetch resource|load failed)/i;

/** Mensagem legível (em português) para qualquer erro. */
export const errorMessage = (error: unknown): string => {
  if (!error) return '';
  if (error instanceof ApiError) return error.message || `Erro ${error.status}`;
  if (error instanceof Error) {
    // Só as mensagens de rede dos navegadores (Chrome, Firefox, Safari); outros TypeError são bugs e aparecem como são.
    if (NETWORK_FAILURE.test(error.message)) return NETWORK_ERROR_MESSAGE;
    return error.message;
  }
  return String(error);
};

export const ErrorBox: React.FC<{ error: unknown; title?: string; onRetry?: () => void; onClose?: () => void; className?: string }> = ({
  error,
  title,
  onRetry,
  onClose,
  className = '',
}) => {
  const [copied, setCopied] = useState(false);
  if (!error) return null;
  const message = errorMessage(error).trim();
  const lines = message.split('\n');
  const summary = lines[0];
  const rest = lines.slice(1).join('\n').trim();
  const long = rest.length > 280 || lines.length > 5;
  const copy = () => {
    navigator.clipboard?.writeText(message).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };
  return (
    <div role="alert" className={`flex gap-3 rounded-lg border border-red-400/35 bg-red-400/10 p-3 text-sm ${className}`}>
      <CircleX size={18} strokeWidth={2} aria-hidden="true" className="mt-0.5 shrink-0 text-red-300" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="font-medium text-fg">{title ?? 'Algo deu errado'}</div>
        <div className="whitespace-pre-wrap break-words text-red-200">{summary}</div>
        {rest && !long ? <div className="whitespace-pre-wrap break-words text-[13px] text-red-200/90">{rest}</div> : null}
        {rest && long ? (
          <details className="mt-1 text-[13px] text-muted">
            <summary className="w-fit rounded-sm hover:text-fg">Detalhes técnicos</summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-2 font-mono text-xs text-muted">
              {rest}
            </pre>
            <Button size="sm" variant="ghost" icon={copied ? Check : Copy} onClick={copy} className="mt-1">
              {copied ? 'Copiado' : 'Copiar detalhes'}
            </Button>
          </details>
        ) : null}
        {onRetry ? (
          <div className="mt-1">
            <Button size="sm" icon={RotateCw} onClick={onRetry}>
              Tentar de novo
            </Button>
          </div>
        ) : null}
      </div>
      {onClose ? <IconButton label="Fechar aviso de erro" icon={X} size="sm" onClick={onClose} className="-mr-1 -mt-1" /> : null}
    </div>
  );
};

export const Empty: React.FC<{ icon?: LucideIcon; title?: React.ReactNode; children?: React.ReactNode; action?: React.ReactNode; className?: string }> = ({
  icon: Icon,
  title,
  children,
  action,
  className = '',
}) => (
  <div className={`flex flex-col items-center rounded-xl border border-dashed border-line-strong px-6 py-10 text-center ${className}`}>
    {Icon ? (
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-raised text-muted">
        <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
      </div>
    ) : null}
    {title ? <div className="font-display text-[15px] font-semibold text-fg">{title}</div> : null}
    {children ? <div className={`max-w-md text-sm text-muted ${title ? 'mt-1' : ''}`}>{children}</div> : null}
    {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
  </div>
);

export const Loading: React.FC<{ label?: string; className?: string }> = ({ label = 'Carregando…', className = '' }) => (
  <div role="status" aria-live="polite" className={`flex items-center gap-2 text-sm text-muted ${className}`}>
    <Spinner />
    <span>{label}</span>
  </div>
);

export const Skeleton: React.FC<{ className?: string }> = ({ className = '' }) => (
  <div aria-hidden="true" className={`animate-pulse rounded-lg bg-raised ${className}`} />
);

const PROGRESS_TONES = { accent: 'bg-accent', good: 'bg-emerald-400', warn: 'bg-amber-400', bad: 'bg-red-400' } as const;

export const ProgressBar: React.FC<{
  value: number;
  indeterminate?: boolean;
  tone?: keyof typeof PROGRESS_TONES;
  label?: string;
  /** Texto lido pelo leitor de tela (ex.: "Na fila"). Padrão: a porcentagem, ou "Em andamento" no indeterminado. */
  valueText?: string;
  className?: string;
}> = ({ value, indeterminate, tone = 'accent', label, valueText, className = '' }) => {
  const pct = Math.round(Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label ?? 'Progresso'}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : pct}
      aria-valuetext={valueText ?? (indeterminate ? 'Em andamento' : `${pct}%`)}
      className={`relative h-1.5 w-full overflow-hidden rounded-full bg-line ${className}`}
    >
      {indeterminate ? (
        <div
          className={`absolute inset-y-0 left-0 w-2/5 animate-indeterminate rounded-full ${PROGRESS_TONES[tone]} opacity-80 motion-reduce:w-full motion-reduce:animate-none motion-reduce:opacity-40`}
        />
      ) : (
        <div className={`h-full rounded-full transition-[width] duration-300 ${PROGRESS_TONES[tone]}`} style={{ width: `${pct}%` }} />
      )}
    </div>
  );
};

export type StepState = 'done' | 'current' | 'todo' | 'error';

/**
 * Trilha horizontal compacta de etapas. Num contêiner estreito (abaixo de 32rem), só a etapa atual e a com erro
 * mostram o rótulo; as outras ficam só com o círculo (rótulo no leitor de tela e na dica). Se ainda assim não couber,
 * a trilha rola sozinha até a etapa atual.
 */
export const Stepper: React.FC<{
  steps: { id: string; label: string; state: StepState }[];
  onSelect?: (id: string) => void;
  className?: string;
}> = ({ steps, onSelect, className = '' }) => {
  const listRef = useRef<HTMLOListElement>(null);
  const currentId = steps.find((s) => s.state === 'current')?.id;
  useLayoutEffect(() => {
    const list = listRef.current;
    const item = list?.querySelector<HTMLElement>('[aria-current="step"]');
    if (!list || !item || list.scrollWidth <= list.clientWidth) return;
    // scrollLeft (e não scrollIntoView, que rolaria a página): só a trilha se move.
    const l = list.getBoundingClientRect();
    const t = item.getBoundingClientRect();
    if (t.right > l.right) list.scrollLeft += t.right - l.right;
    else if (t.left < l.left) list.scrollLeft -= l.left - t.left;
  }, [currentId, steps.length]);
  return (
    // w-full: com container-type a largura não vem do conteúdo; sem largura explícita, num pai flex ela vira 0.
    <div className={`@container w-full min-w-0 ${className}`}>
      <ol ref={listRef} className="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Etapas">
        {steps.map((step, i) => {
          const dot = {
            done: 'border-emerald-400/50 bg-emerald-400/15 text-emerald-300',
            current: 'border-accent bg-accent text-on-accent',
            todo: 'border-line-strong bg-transparent text-subtle',
            error: 'border-red-400/60 bg-red-400/15 text-red-300',
          }[step.state];
          const text = step.state === 'current' ? 'text-fg font-medium' : step.state === 'error' ? 'text-red-300' : step.state === 'done' ? 'text-muted' : 'text-subtle';
          const stateLabel = { done: 'concluída', current: 'etapa atual', todo: 'a fazer', error: 'com erro' }[step.state];
          const alwaysLabeled = step.state === 'current' || step.state === 'error';
          const content = (
            <>
              <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums ${dot}`} aria-hidden="true">
                {step.state === 'done' ? <Check size={12} strokeWidth={3} /> : step.state === 'error' ? <X size={12} strokeWidth={3} /> : i + 1}
              </span>
              <span className={`whitespace-nowrap text-[13px] ${text} ${alwaysLabeled ? '' : 'sr-only @lg:not-sr-only'}`}>{step.label}</span>
              <span className="sr-only">({stateLabel})</span>
            </>
          );
          const tip = alwaysLabeled ? undefined : `${step.label} (${stateLabel})`;
          return (
            <li key={step.id} className="flex shrink-0 items-center gap-1" aria-current={step.state === 'current' ? 'step' : undefined}>
              {i > 0 ? (
                <span
                  aria-hidden="true"
                  className={`h-px w-3 @lg:w-6 ${step.state === 'todo' || step.state === 'current' ? 'bg-line-strong' : 'bg-emerald-400/50'}`}
                />
              ) : null}
              {onSelect ? (
                <button
                  type="button"
                  title={tip}
                  onClick={() => onSelect(step.id)}
                  className="flex items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-raised focus-visible:outline-offset-[-2px] pointer-coarse:py-2"
                >
                  {content}
                </button>
              ) : (
                <span title={tip} className="flex items-center gap-1.5 px-1.5 py-1">
                  {content}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Avisos (toasts)                                                     */
/* ------------------------------------------------------------------ */

export type ToastTone = 'good' | 'bad' | 'info' | 'warn';
export type ToastOptions = {
  tone?: ToastTone;
  title: string;
  description?: string;
  action?: { label: string; to?: string; onClick?: () => void };
  durationMs?: number;
};
type ToastEntry = ToastOptions & { id: number };

const ToastContext = createContext<{ toast: (opts: ToastOptions) => void } | null>(null);

const TOAST_ICONS: Record<ToastTone, { icon: LucideIcon; className: string }> = {
  good: { icon: CircleCheck, className: 'text-emerald-300' },
  bad: { icon: CircleX, className: 'text-red-300' },
  info: { icon: Info, className: 'text-sky-300' },
  warn: { icon: TriangleAlert, className: 'text-amber-200' },
};

const ToastItem: React.FC<{ entry: ToastEntry; onClose: (id: number) => void }> = ({ entry, onClose }) => {
  const tone = entry.tone ?? 'info';
  const duration = entry.durationMs ?? (tone === 'bad' ? Infinity : 6000);
  const timer = useRef<number | undefined>(undefined);
  const start = useCallback(() => {
    if (!Number.isFinite(duration)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onClose(entry.id), duration);
  }, [duration, entry.id, onClose]);
  const stop = () => window.clearTimeout(timer.current);
  useEffect(() => {
    start();
    return () => window.clearTimeout(timer.current);
  }, [start]);
  const { icon: Icon, className } = TOAST_ICONS[tone];
  const action = entry.action;
  const runAction = () => {
    action?.onClick?.();
    onClose(entry.id);
  };
  return (
    <div
      onMouseEnter={stop}
      onMouseLeave={start}
      onFocus={stop}
      onBlur={start}
      className={`pointer-events-auto flex w-full animate-toast-in gap-3 rounded-xl border bg-raised p-3 shadow-2xl shadow-black/60 ${
        tone === 'bad' ? 'border-red-400/40' : 'border-line-strong'
      }`}
    >
      <Icon size={18} strokeWidth={2} aria-hidden="true" className={`mt-0.5 shrink-0 ${className}`} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="text-sm font-semibold text-fg">{entry.title}</div>
        {entry.description ? <div className="line-clamp-3 break-words text-[13px] text-muted">{entry.description}</div> : null}
        {action ? (
          <div className="mt-1.5">
            {action.to ? (
              <Link to={action.to} onClick={runAction} className={buttonClass({ size: 'sm' })}>
                {action.label}
              </Link>
            ) : (
              <Button size="sm" onClick={runAction}>
                {action.label}
              </Button>
            )}
          </div>
        ) : null}
      </div>
      <IconButton label={`Fechar aviso: ${entry.title}`} icon={X} size="sm" onClick={() => onClose(entry.id)} className="-mr-1 -mt-1" />
    </div>
  );
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ToastEntry[]>([]);
  const nextId = useRef(1);
  const close = useCallback((id: number) => setItems((list) => list.filter((t) => t.id !== id)), []);
  const toast = useCallback((opts: ToastOptions) => {
    const id = nextId.current++;
    setItems((list) => {
      const next = [...list, { ...opts, id }];
      // Acima de 4, sai primeiro o aviso mais antigo que não é erro: erros ficam até o usuário fechar.
      while (next.length > 4) {
        const i = next.findIndex((t) => (t.tone ?? 'info') !== 'bad');
        next.splice(i >= 0 ? i : 0, 1);
      }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Duas regiões vivas fixas (montadas desde o início): erros como alerta, o resto como aviso polido. */}
      <div
        aria-label="Avisos"
        role="region"
        className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2"
      >
        <div role="alert" className="flex flex-col gap-2 empty:hidden">
          {items
            .filter((entry) => entry.tone === 'bad')
            .map((entry) => (
              <ToastItem key={entry.id} entry={entry} onClose={close} />
            ))}
        </div>
        <div aria-live="polite" className="flex flex-col gap-2 empty:hidden">
          {items
            .filter((entry) => entry.tone !== 'bad')
            .map((entry) => (
              <ToastItem key={entry.id} entry={entry} onClose={close} />
            ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
};

const noopToast = { toast: () => undefined };

export const useToast = () => useContext(ToastContext) ?? noopToast;

/* ------------------------------------------------------------------ */
/* Diálogo de confirmação                                              */
/* ------------------------------------------------------------------ */

export type ConfirmOptions = {
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'danger' | 'primary';
};

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(null);

const ConfirmDialog: React.FC<{ opts: ConfirmOptions; onResult: (ok: boolean) => void }> = ({ opts, onResult }) => {
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  // <dialog> nativo em modo modal: o resto da página fica inerte, o Tab não sai e o Esc funciona onde estiver o foco.
  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    // Sem close() no cleanup: ao sair do DOM o diálogo deixa a camada do topo sem disparar `close`
    // (no StrictMode, um close() aqui cancelaria o diálogo recém-aberto).
    if (!el.open) el.showModal();
    cancelRef.current?.focus();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={opts.description ? descId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        onResult(false);
      }}
      onClose={() => onResult(false)}
      onMouseDown={(e) => {
        // Clique no fundo escurecido (fora do painel) cancela.
        if (e.target !== e.currentTarget) return;
        const r = e.currentTarget.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) onResult(false);
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-md animate-fade-in rounded-xl border border-line-strong bg-card p-5 text-fg shadow-2xl shadow-black/60 backdrop:bg-black/65 backdrop:backdrop-blur-sm"
    >
      <div className="flex items-start gap-3">
        {opts.tone === 'danger' ? (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-400/10 text-red-300">
            <TriangleAlert size={18} aria-hidden="true" />
          </div>
        ) : null}
        <div className="min-w-0">
          <h2 id={titleId} className="font-display text-lg font-semibold text-fg">
            {opts.title}
          </h2>
          {opts.description ? (
            <div id={descId} className="mt-1.5 text-sm text-muted">
              {opts.description}
            </div>
          ) : null}
        </div>
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button ref={cancelRef} type="button" className={buttonClass({ variant: 'secondary' })} onClick={() => onResult(false)}>
          {opts.cancelLabel ?? 'Cancelar'}
        </button>
        <Button variant={opts.tone === 'danger' ? 'danger' : 'primary'} onClick={() => onResult(true)}>
          {opts.confirmLabel ?? 'Confirmar'}
        </Button>
      </div>
    </dialog>
  );
};

export const ConfirmProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<{ opts: ConfirmOptions; resolve: (ok: boolean) => void; restore: HTMLElement | null } | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        stateRef.current?.resolve(false);
        const restore = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setState({ opts, resolve, restore });
      }),
    [],
  );

  const finish = (ok: boolean) => {
    const current = stateRef.current;
    if (!current) return;
    current.resolve(ok);
    setState(null);
    window.setTimeout(() => current.restore?.focus(), 0);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state ? <ConfirmDialog opts={state.opts} onResult={finish} /> : null}
    </ConfirmContext.Provider>
  );
};

const fallbackConfirm = (opts: ConfirmOptions) => Promise.resolve(window.confirm(opts.title));

export const useConfirm = () => useContext(ConfirmContext) ?? fallbackConfirm;

/* ------------------------------------------------------------------ */
/* Reprodução e formatação                                             */
/* ------------------------------------------------------------------ */

export const PLAYBACK_RATES = [1, 1.25, 1.5, 1.75, 2];

/** Velocidade de reprodução (o navegador mantém o tom da voz). */
export const SpeedButtons: React.FC<{ value: number; onChange: (rate: number) => void; className?: string }> = ({ value, onChange, className = '' }) => (
  <div className={`${SEGMENTED_TRACK} items-center ${className}`} role="group" aria-label="Velocidade de reprodução">
    {PLAYBACK_RATES.map((rate) => (
      <button key={rate} type="button" aria-pressed={rate === value} onClick={() => onChange(rate)} className={`${segmentClass(rate === value, 'sm')} tabular-nums`}>
        {String(rate).replace('.', ',')}x
      </button>
    ))}
  </div>
);

export const formatBytes = (n: number) => {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2).replace('.', ',')} GB`;
};

export const formatSeconds = (s: number) => {
  const total = Math.max(0, Math.round(s));
  const m = Math.floor(total / 60);
  const r = total % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
};

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** Tempo relativo em português: "agora", "há 5 min", "há 2 h", "ontem", "há 3 dias", depois data curta. */
export const formatRelative = (iso: string): string => {
  const date = new Date(iso);
  const t = date.getTime();
  if (Number.isNaN(t)) return '';
  const now = new Date();
  const diff = now.getTime() - t;
  const abs = Math.abs(diff);
  const min = Math.round(abs / 60_000);
  const hours = Math.floor(abs / 3_600_000);
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (abs < 45_000) return 'agora';
  if (diff > 0) {
    if (min < 60) return `há ${min} min`;
    if (days === 0) return `há ${Math.max(1, hours)} h`;
    if (days === 1) return 'ontem';
    if (days < 7) return `há ${days} dias`;
  } else {
    if (min < 60) return `em ${min} min`;
    if (days === 0) return `em ${Math.max(1, hours)} h`;
    if (days === -1) return 'amanhã';
    if (days > -7) return `em ${-days} dias`;
  }
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: sameYear ? undefined : 'numeric' }).replace('.', '');
};

/** Valor em dólar no formato brasileiro: "US$ 0,00" (centavos de centavo quando o valor é muito pequeno). */
export const formatMoney = (usd: number): string => {
  const value = Number.isFinite(usd) ? usd : 0;
  const tiny = value !== 0 && Math.abs(value) < 0.01;
  const digits = tiny ? 4 : 2;
  const [int, frac] = Math.abs(value).toFixed(digits).split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${value < 0 ? '-' : ''}US$ ${grouped},${frac}`;
};
