import React from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { ChannelsPage } from './pages/Channels';
import { NewProjectPage } from './pages/NewProject';
import { ProjectPage } from './pages/ProjectPage';
import { ProjectsPage } from './pages/Projects';
import { SettingsPage } from './pages/Settings';
import { StyleEditorPage } from './pages/StyleEditor';
import { StylesPage } from './pages/Styles';
import { useBackendEvents } from './useEvents';

const NavItem: React.FC<{ to: string; label: string; icon: string }> = ({ to, label, icon }) => (
  <NavLink
    to={to}
    end={to === '/'}
    className={({ isActive }) =>
      `flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${isActive ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`
    }
  >
    <span className="w-4 text-center">{icon}</span>
    {label}
  </NavLink>
);

export const App: React.FC = () => {
  useBackendEvents();
  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col gap-1 border-r border-zinc-900 bg-zinc-950 p-3">
        <div className="mb-4 flex items-center gap-2 px-2 pt-1">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-900 text-amber-400">▶</div>
          <div>
            <div className="text-sm font-bold text-zinc-100">Videos Studio</div>
            <div className="text-[11px] text-zinc-500">estúdio de vídeos dark</div>
          </div>
        </div>
        <NavItem to="/" label="Projetos" icon="◧" />
        <NavItem to="/novo" label="Novo projeto" icon="+" />
        <NavItem to="/estilos" label="Estilos" icon="✦" />
        <NavItem to="/canais" label="Canais" icon="◉" />
        <NavItem to="/configuracoes" label="Configurações" icon="⚙" />
        <div className="mt-auto px-2 text-[11px] leading-relaxed text-zinc-600">
          Uso pessoal. Revise cada vídeo antes de publicar: o YouTube desmonetiza conteúdo repetitivo.
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/novo" element={<NewProjectPage />} />
          <Route path="/projetos/:projectId" element={<ProjectPage />} />
          <Route path="/estilos" element={<StylesPage />} />
          <Route path="/estilos/:styleId" element={<StyleEditorPage />} />
          <Route path="/canais" element={<ChannelsPage />} />
          <Route path="/configuracoes" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
};
