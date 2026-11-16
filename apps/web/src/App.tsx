import { ArrowLeft, Menu, SearchX } from 'lucide-react';
import React, { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import { JOB_LABELS } from './api';
import { VultoLogo } from './brand';
import { useCurrentPageTitle } from './documentTitle';
import { ConnectionBanner } from './shell/ConnectionBanner';
import { isActiveJob, isBoolean, readLocal, useJobs, writeLocal } from './shell/data';
import { Sidebar } from './shell/Sidebar';
import { Empty, ErrorBox, IconButton, Loading, buttonClass } from './ui';
import { useBackendEvents } from './useEvents';

// Cada página vira um pedaço separado do bundle (o Player do Remotion só carrega na prévia).
const ProjectsPage = lazy(() => import('./pages/Projects').then((m) => ({ default: m.ProjectsPage })));
const NewProjectPage = lazy(() => import('./pages/NewProject').then((m) => ({ default: m.NewProjectPage })));
const ProjectPage = lazy(() => import('./pages/ProjectPage').then((m) => ({ default: m.ProjectPage })));
const StylesPage = lazy(() => import('./pages/Styles').then((m) => ({ default: m.StylesPage })));
const StyleEditorPage = lazy(() => import('./pages/StyleEditor').then((m) => ({ default: m.StyleEditorPage })));
const ChannelsPage = lazy(() => import('./pages/Channels').then((m) => ({ default: m.ChannelsPage })));
const SettingsPage = lazy(() => import('./pages/Settings').then((m) => ({ default: m.SettingsPage })));
const AgendaPage = lazy(() => import('./pages/Agenda').then((m) => ({ default: m.AgendaPage })));
// Gestão de canal (ADR 0002)
const TodayPage = lazy(() => import('./pages/Today').then((m) => ({ default: m.TodayPage })));
const IdeasPage = lazy(() => import('./pages/Ideas').then((m) => ({ default: m.IdeasPage })));
const AnalyticsPage = lazy(() => import('./pages/Analytics').then((m) => ({ default: m.AnalyticsPage })));
const CommentsPage = lazy(() => import('./pages/Comments').then((m) => ({ default: m.CommentsPage })));
const MoneyPage = lazy(() => import('./pages/Money').then((m) => ({ default: m.MoneyPage })));
// Qualidade e pesquisa (ADR 0003)
const RadarPage = lazy(() => import('./pages/Radar').then((m) => ({ default: m.RadarPage })));
const ReferencesPage = lazy(() => import('./pages/References').then((m) => ({ default: m.ReferencesPage })));

const COLLAPSED_KEY = 'vulto.barraRecolhida';

const PageLoading: React.FC = () => <Loading label="Carregando página…" className="px-4 py-6 sm:px-6 lg:px-8" />;

const NotFound: React.FC = () => (
  <div className="mx-auto max-w-[960px] px-4 py-10 sm:px-6 lg:px-8">
    <Empty
      icon={SearchX}
      title="Página não encontrada"
      action={
        <Link to="/" className={buttonClass({ variant: 'primary' })}>
          <ArrowLeft size={16} aria-hidden="true" />
          Voltar aos projetos
        </Link>
      }
    >
      O endereço não existe ou o item foi apagado.
    </Empty>
  </div>
);

/** Mostra um erro (ex.: falha ao baixar uma página) sem derrubar a casca. Volta ao normal ao trocar de rota. */
class RouteErrorBoundary extends React.Component<{ resetKey: string; children: React.ReactNode }, { error: unknown; key: string }> {
  state = { error: null as unknown, key: this.props.resetKey };

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  static getDerivedStateFromProps(props: { resetKey: string }, state: { error: unknown; key: string }) {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-[960px] px-4 py-6 sm:px-6 lg:px-8">
          <ErrorBox error={this.state.error} title="Não foi possível abrir esta página" onRetry={() => window.location.reload()} />
        </div>
      );
    }
    return this.props.children;
  }
}

/** Um erro na barra lateral não derruba o conteúdo: a barra some e o resto continua. */
class SidebarBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Título da aba: progresso do job em andamento, senão o título da página. */
const useDocumentTitle = () => {
  const jobs = useJobs();
  const pageTitle = useCurrentPageTitle();
  const running = (jobs.data ?? []).filter(isActiveJob).sort((a, b) => (a.status === b.status ? 0 : a.status === 'running' ? -1 : 1))[0];
  const title = running
    ? `(${Math.round(running.progress * 100)}%) ${JOB_LABELS[running.kind] ?? 'Tarefa'} · Vulto`
    : pageTitle
      ? `${pageTitle} · Vulto`
      : 'Vulto';
  useEffect(() => {
    document.title = title;
  }, [title]);
};

/** Folha que assina os jobs: os tiques de progresso re-renderizam só ela, não o App e a página aberta. */
const DocumentTitle: React.FC = () => {
  useDocumentTitle();
  return null;
};

export const App: React.FC = () => {
  useBackendEvents();
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  const [collapsed, setCollapsed] = useState<boolean>(() =>
    // Abaixo de 1280 px a barra começa recolhida (64 px): a 1024 px a barra aberta tomaria 25% da largura.
    readLocal<boolean>(COLLAPSED_KEY, typeof window !== 'undefined' && window.innerWidth < 1280, isBoolean),
  );
  const toggleCollapsed = () =>
    setCollapsed((c) => {
      writeLocal(COLLAPSED_KEY, !c);
      return !c;
    });

  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    window.setTimeout(() => menuButton.current?.focus(), 0);
  }, []);

  // Rolagem volta ao topo ao trocar de rota (não ao trocar só a query string). Comparar com o caminho anterior
  // é idempotente no StrictMode, e o layout effect rola antes de pintar a nova rota.
  const prevPath = useRef(pathname);
  useLayoutEffect(() => {
    if (prevPath.current === pathname) return;
    prevPath.current = pathname;
    mainRef.current?.scrollTo({ top: 0 });
    // Depois de navegar pela barra lateral, o teclado passa a rolar o conteúdo.
    const active = document.activeElement;
    if (!active || active === document.body || active.closest('aside, [data-drawer]')) mainRef.current?.focus({ preventScroll: true });
    setDrawerOpen(false);
  }, [pathname]);

  // Link da gaveta: fecha e, se a rota não mudou (link da página atual), o foco não fica perdido no <body>.
  const onDrawerNavigate = useCallback(() => {
    setDrawerOpen(false);
    window.setTimeout(() => {
      if (!document.activeElement || document.activeElement === document.body) mainRef.current?.focus({ preventScroll: true });
    }, 0);
  }, []);

  // Gaveta: Esc fecha e o foco começa dentro dela.
  useEffect(() => {
    if (!drawerOpen) return;
    drawerRef.current?.querySelector<HTMLElement>('a, button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeDrawer();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen, closeDrawer]);

  return (
    <div className="flex h-full overflow-hidden bg-canvas text-fg">
      <DocumentTitle />
      <a
        href="#conteudo"
        inert={drawerOpen || undefined}
        className="fixed left-3 top-3 z-[90] -translate-y-24 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-on-accent transition-transform focus:translate-y-0"
      >
        Pular para o conteúdo
      </a>

      <aside className={`hidden shrink-0 transition-[width] duration-200 md:block ${collapsed ? 'w-16' : 'w-64'}`} aria-label="Barra lateral">
        <SidebarBoundary>
          <Sidebar collapsed={collapsed} mode="desktop" onToggleCollapsed={toggleCollapsed} />
        </SidebarBoundary>
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Menu" data-drawer="">
          <div className="absolute inset-0 animate-fade-in bg-black/60" onClick={closeDrawer} aria-hidden="true" />
          <div ref={drawerRef} id="menu-gaveta" className="absolute inset-y-0 left-0 w-72 max-w-[85vw] shadow-2xl shadow-black/60">
            <SidebarBoundary>
              <Sidebar collapsed={false} mode="drawer" onClose={closeDrawer} onNavigate={onDrawerNavigate} />
            </SidebarBoundary>
          </div>
        </div>
      ) : null}

      {/* Com a gaveta aberta, o resto fica inerte: o Tab não navega atrás do fundo escuro. */}
      <div className="flex min-w-0 flex-1 flex-col" inert={drawerOpen || undefined}>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-panel px-3 md:hidden">
          <IconButton
            ref={menuButton}
            label="Abrir menu"
            icon={Menu}
            aria-expanded={drawerOpen}
            aria-controls="menu-gaveta"
            onClick={() => setDrawerOpen(true)}
          />
          <Link to="/" className="flex items-center gap-2 rounded-lg p-1">
            <VultoLogo collapsed />
            <span aria-hidden="true" className="font-display text-[15px] font-extrabold tracking-[0.14em] text-fg">
              VULTO
            </span>
          </Link>
        </header>
        <ConnectionBanner />
        <main id="conteudo" ref={mainRef} tabIndex={-1} className="@container min-w-0 flex-1 scroll-pt-32 overflow-y-auto focus:outline-none">
          <RouteErrorBoundary resetKey={pathname}>
            <Suspense fallback={<PageLoading />}>
              <Routes>
                <Route path="/" element={<ProjectsPage />} />
                <Route path="/novo" element={<NewProjectPage />} />
                <Route path="/projetos/:projectId" element={<ProjectPage />} />
                <Route path="/estilos" element={<StylesPage />} />
                <Route path="/estilos/:styleId" element={<StyleEditorPage />} />
                <Route path="/agenda" element={<AgendaPage />} />
                <Route path="/hoje" element={<TodayPage />} />
                <Route path="/ideias" element={<IdeasPage />} />
                <Route path="/desempenho" element={<AnalyticsPage />} />
                <Route path="/comentarios" element={<CommentsPage />} />
                <Route path="/dinheiro" element={<MoneyPage />} />
                <Route path="/radar" element={<RadarPage />} />
                <Route path="/referencias" element={<ReferencesPage />} />
                <Route path="/canais" element={<ChannelsPage />} />
                <Route path="/configuracoes" element={<SettingsPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
  );
};
