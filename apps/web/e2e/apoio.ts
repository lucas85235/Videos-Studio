import { type Page, expect, test as base } from '@playwright/test';

/**
 * Teste com vigia de erros: qualquer erro de JavaScript na página, erro no console ou resposta 5xx da API
 * reprova o teste no fim. Assim uma página que "abre" mas quebra por baixo não passa despercebida.
 */
export const test = base.extend<{ erros: string[] }>({
  erros: [
    async ({ page }, use) => {
      const erros: string[] = [];
      page.on('pageerror', (e) => erros.push(`erro na página: ${e.message}`));
      page.on('console', (msg) => {
        if (msg.type() === 'error') erros.push(`console: ${msg.text()}`);
      });
      page.on('response', (res) => {
        if (res.status() >= 500 && new URL(res.url()).pathname.startsWith('/api/')) {
          erros.push(`API ${res.status()}: ${res.request().method()} ${res.url()}`);
        }
      });
      await use(erros);
      expect(erros, 'erros durante o teste').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Título principal da página (o <h1> dentro do conteúdo). */
export const tituloDaPagina = (page: Page) => page.locator('main h1');

/** Cria um projeto pela API (mais rápido que pela tela) e devolve o id. */
export const criarProjetoPelaApi = async (page: Page, tema: string): Promise<string> => {
  const res = await page.request.post('/api/projects', { data: { title: tema, topic: tema, autopilot: false } });
  expect(res.ok(), await res.text()).toBeTruthy();
  const body = (await res.json()) as { project: { id: string } };
  return body.project.id;
};
