import { CalendarClock, Clapperboard, PanelLeftClose, PanelLeftOpen, Palette, Plus, Settings, Tv, X, type LucideIcon } from 'lucide-react';
import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { VultoLogo } from '../brand';
import { IconButton, buttonClass } from '../ui';
import { isActiveJob, useAgenda, useJobs } from './data';
import { JobsDock } from './JobsDock';
import { StudioStatus } from './StudioStatus';

const DISCLAIMER = 'Uso pessoal. Revise cada vídeo antes de publicar: o YouTube desmonetiza conteúdo repetitivo.';

type NavDef = { to: string; label: string; icon: LucideIcon; match: (path: string) => boolean };

const PRODUCTION: NavDef[] = [
  { to: '/', label: 'Projetos', icon: Clapperboard, match: (p) => p === '/' || p.startsWith('/projetos') },
  { to: '/agenda', label: 'Agenda', icon: CalendarClock, match: (p) => p.startsWith('/agenda') },
];
const LIBRARY: NavDef[] = [
  { to: '/estilos', label: 'Estilos', icon: Palette, match: (p) => p.startsWith('/estilos') },
  { to: '/canais', label: 'Canais', icon: Tv, match: (p) => p.startsWith('/canais') },
];
const SETTINGS: NavDef = { to: '/configuracoes', label: 'Configurações', icon: Settings, match: (p) => p.startsWith('/configuracoes') };

/** Dica visível à direita na barra recolhida: aparece no hover e no foco do teclado (o `title` não aparece no foco). */
const CollapsedTip: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span
    aria-hidden="true"
    className="pointer-events-none absolute left-full top-1/2 z-30 ml-2 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-line-strong bg-raised px-2 py-1 text-xs font-medium text-fg shadow-lg shadow-black/40 group-hover:block group-focus-visible:block"
  >
    {children}
  </span>
);

const NavItem: React.FC<{ def: NavDef; active: boolean; collapsed: boolean; extra?: React.ReactNode; extraCollapsed?: React.ReactNode; srExtra?: string; onNavigate?: () => void }> = ({
  def,
  active,
  collapsed,
  extra,
  extraCollapsed,
  srExtra,
  onNavigate,
}) => {
  const Icon = def.icon;
  return (
    <li>
      <Link
        to={def.to}
        onClick={onNavigate}
        aria-current={active ? 'page' : undefined}
        className={`group relative flex h-9 items-center rounded-lg text-sm font-medium transition-colors ${
          collapsed ? 'mx-auto w-10 justify-center' : 'gap-3 px-2.5'
        } ${
          active
            ? 'bg-raised text-fg before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-full before:bg-accent'
            : 'text-muted hover:bg-raised/60 hover:text-fg'
        }`}
      >
        <Icon size={18} strokeWidth={1.75} aria-hidden="true" className={`shrink-0 ${active ? 'text-accent' : ''}`} />
        {collapsed ? <span className="sr-only">{def.label}</span> : <span className="min-w-0 flex-1 truncate">{def.label}</span>}
        {srExtra ? <span className="sr-only">, {srExtra}</span> : null}
        {collapsed ? extraCollapsed : extra}
        {collapsed ? <CollapsedTip>{def.label + (srExtra ? ` (${srExtra})` : '')}</CollapsedTip> : null}
      </Link>
    </li>
  );
};

const GroupTitle: React.FC<{ children: React.ReactNode; collapsed: boolean; id: string }> = ({ children, collapsed, id }) =>
  collapsed ? (
    <h2 id={id} className="sr-only">
      {children}
    </h2>
  ) : (
    <h2 id={id} className="mb-1 px-2.5 font-sans text-xs font-semibold uppercase tracking-[0.08em] text-subtle">
      {children}
    </h2>
  );

export type SidebarMode = 'desktop' | 'drawer';

export const Sidebar: React.FC<{
  collapsed: boolean;
  mode: SidebarMode;
  onToggleCollapsed?: () => void;
  onClose?: () => void;
  /** Chamado ao seguir um link (a gaveta fecha). */
  onNavigate?: () => void;
}> = ({ collapsed, mode, onToggleCollapsed, onClose, onNavigate }) => {
  const { pathname } = useLocation();
  const jobs = useJobs();
  const agenda = useAgenda();
  const running = (jobs.data ?? []).filter(isActiveJob).length;
  const manual = agenda.data?.manual.length ?? 0;

  const jobsExtra = running ? (
    <span className="flex items-center gap-1.5 text-xs font-semibold text-accent tabular-nums" aria-hidden="true">
      <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" />
      {running}
    </span>
  ) : null;
  const jobsExtraCollapsed = running ? (
    <span aria-hidden="true" className="absolute right-1 top-1 h-2 w-2 animate-pulse-dot rounded-full bg-accent ring-2 ring-panel" />
  ) : null;
  const agendaExtra = manual ? (
    <span aria-hidden="true" className="rounded-md border border-line-strong bg-canvas px-1.5 text-xs font-semibold leading-5 text-fg tabular-nums">
      {manual}
    </span>
  ) : null;
  const agendaExtraCollapsed = manual ? (
    <span aria-hidden="true" className="absolute -right-0.5 top-0 flex h-4 min-w-4 items-center justify-center rounded-full bg-raised px-1 text-xs font-semibold leading-none text-fg tabular-nums ring-1 ring-line-strong">
      {manual}
    </span>
  ) : null;

  const extras: Record<string, { extra: React.ReactNode; extraCollapsed: React.ReactNode; sr?: string }> = {
    '/': {
      extra: jobsExtra,
      extraCollapsed: jobsExtraCollapsed,
      sr: running ? `${running} ${running === 1 ? 'tarefa em andamento' : 'tarefas em andamento'}` : undefined,
    },
    '/agenda': {
      extra: agendaExtra,
      extraCollapsed: agendaExtraCollapsed,
      sr: manual ? `${manual} ${manual === 1 ? 'pacote para postar' : 'pacotes para postar'}` : undefined,
    },
  };

  const renderItem = (def: NavDef) => (
    <NavItem
      key={def.to}
      def={def}
      active={def.match(pathname)}
      collapsed={collapsed}
      extra={extras[def.to]?.extra}
      extraCollapsed={extras[def.to]?.extraCollapsed}
      srExtra={extras[def.to]?.sr}
      onNavigate={onNavigate}
    />
  );

  const creating = pathname === '/novo';

  return (
    <div className="flex h-full flex-col border-r border-line bg-panel">
      <div className={`flex shrink-0 pb-3 pt-4 ${collapsed ? 'flex-col items-center gap-2 px-2' : 'items-center justify-between gap-2 px-3'}`}>
        <Link
          to="/"
          onClick={onNavigate}
          title={`Vulto · ${DISCLAIMER}`}
          aria-describedby={`vulto-aviso-${mode}`}
          className={`min-w-0 rounded-lg ${collapsed ? 'p-1' : 'py-1 pl-0.5 pr-1'}`}
        >
          <VultoLogo collapsed={collapsed} />
          <span className="sr-only">: início</span>
        </Link>
        <span id={`vulto-aviso-${mode}`} className="sr-only">
          {DISCLAIMER}
        </span>
        {mode === 'drawer' ? (
          <IconButton label="Fechar menu" icon={X} onClick={onClose} />
        ) : onToggleCollapsed ? (
          <IconButton
            label={collapsed ? 'Expandir barra lateral' : 'Recolher barra lateral'}
            icon={collapsed ? PanelLeftOpen : PanelLeftClose}
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            size={collapsed ? 'md' : 'sm'}
          />
        ) : null}
      </div>

      <div className={collapsed ? 'flex justify-center px-2' : 'px-3'}>
        <Link
          to="/novo"
          onClick={onNavigate}
          aria-current={creating ? 'page' : undefined}
          aria-label={collapsed ? 'Novo projeto' : undefined}
          className={`${buttonClass({ variant: 'primary' })} ${collapsed ? 'group relative h-10 w-10 p-0' : 'w-full'}`}
        >
          <Plus size={16} strokeWidth={2.25} aria-hidden="true" />
          {collapsed ? <CollapsedTip>Novo projeto</CollapsedTip> : 'Novo projeto'}
        </Link>
      </div>

            {/* py-1: o contorno de foco (2 px + 2 px de afastamento) não é cortado no topo/fundo. Recolhida, sem rolagem: as dicas saem à direita. */}
      <nav aria-label="Principal" className={`mt-4 flex min-h-0 flex-1 flex-col py-1 ${collapsed ? 'px-2' : 'overflow-y-auto px-3'}`}>
        <section aria-labelledby={`grupo-producao-${mode}`} className="mb-4">
          <GroupTitle id={`grupo-producao-${mode}`} collapsed={collapsed}>
            Produção
          </GroupTitle>
          <ul className="flex flex-col gap-0.5">{PRODUCTION.map(renderItem)}</ul>
        </section>
        {collapsed ? <hr className="mx-3 mb-4 border-line" /> : null}
        <section aria-labelledby={`grupo-biblioteca-${mode}`}>
          <GroupTitle id={`grupo-biblioteca-${mode}`} collapsed={collapsed}>
            Biblioteca
          </GroupTitle>
          <ul className="flex flex-col gap-0.5">{LIBRARY.map(renderItem)}</ul>
        </section>
        <ul className="mt-auto flex flex-col gap-0.5 pt-4">{renderItem(SETTINGS)}</ul>
      </nav>

      <div className={`flex shrink-0 flex-col gap-2 border-t border-line pb-3 pt-3 ${collapsed ? 'px-2' : 'px-3'}`}>
        <JobsDock collapsed={collapsed} onNavigate={onNavigate} />
        <StudioStatus collapsed={collapsed} onNavigate={onNavigate} />
      </div>
    </div>
  );
};
