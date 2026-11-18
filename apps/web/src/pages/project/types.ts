/** Abas da página do projeto (também usadas na URL: ?aba=roteiro, ?aba=cenas…). */
export type ProjectTab = 'package' | 'script' | 'scenes' | 'preview' | 'output' | 'versions';

export const PROJECT_TAB_SLUGS: Record<ProjectTab, string> = {
  package: 'titulo-thumb',
  script: 'roteiro',
  scenes: 'cenas',
  preview: 'previa',
  output: 'video',
  versions: 'versoes',
};

/** Props comuns das abas: `goTo` troca de aba (ex.: depois de aprovar a prévia, ir para Vídeo e publicação). */
export type TabNavigation = { goTo?: (tab: ProjectTab) => void };
