import { type Page, expect, test as base } from '@playwright/test';

/** Chamadas à API em andamento por página (o /api/events, que fica aberto o tempo todo, não conta). */
const atividadeDaApi = new WeakMap<Page, { abertas: number; ultimaMudanca: number }>();

const vigiarApi = (page: Page) => {
  const estado = { abertas: 0, ultimaMudanca: Date.now() };
  atividadeDaApi.set(page, estado);
  const ehApi = (url: string) => {
    const caminho = new URL(url).pathname;
    return caminho.startsWith('/api/') && caminho !== '/api/events';
  };
  page.on('request', (req) => {
    if (!ehApi(req.url())) return;
    estado.abertas += 1;
    estado.ultimaMudanca = Date.now();
  });
  const fechou = (req: { url(): string }) => {
    if (!ehApi(req.url())) return;
    estado.abertas = Math.max(0, estado.abertas - 1);
    estado.ultimaMudanca = Date.now();
  };
  page.on('requestfinished', fechou);
  page.on('requestfailed', fechou);
};

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
      vigiarApi(page);
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


/**
 * Espera a API ficar quieta: nenhuma chamada a /api/ em andamento por `quietoMs` seguidos. O padrão de 1,5 s passa
 * da espera de 1 s antes da nova tentativa do react-query (retry: 1 em main.tsx), então uma consulta que falha já
 * virou caixa de erro na tela quando a função volta.
 */
export const esperarApiQuieta = async (page: Page, quietoMs = 1_500, limiteMs = 20_000) => {
  const estado = atividadeDaApi.get(page);
  if (!estado) throw new Error('use o "test" de e2e/apoio.ts, que vigia a API');
  const inicio = Date.now();
  while (estado.abertas > 0 || Date.now() - estado.ultimaMudanca < quietoMs) {
    if (Date.now() - inicio > limiteMs) throw new Error(`a API não ficou quieta em ${limiteMs / 1000} s`);
    await page.waitForTimeout(100);
  }
};
