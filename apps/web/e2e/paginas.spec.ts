import { criarProjetoPelaApi, expect, test, tituloDaPagina } from './apoio';

// Páginas principais: cada uma abre com o título certo, sem erro na tela nem no console.
const PAGINAS = [
  { caminho: '/', titulo: 'Projetos' },
  { caminho: '/novo', titulo: 'Novo projeto' },
  { caminho: '/estilos', titulo: 'Estilos' },
  { caminho: '/agenda', titulo: 'Agenda' },
  { caminho: '/canais', titulo: 'Canais' },
  { caminho: '/configuracoes', titulo: 'Configurações' },
];

for (const { caminho, titulo } of PAGINAS) {
  test(`a página ${titulo} abre sem erro`, async ({ page }) => {
    await page.goto(caminho);
    await expect(tituloDaPagina(page)).toHaveText(titulo);
    await expect(page).toHaveTitle(new RegExp(`${titulo} · Vulto`));
    await expect(page.getByText(/Não foi possível/)).toHaveCount(0);
  });
}

test('a barra lateral navega entre as páginas', async ({ page }) => {
  await page.goto('/');
  await expect(tituloDaPagina(page)).toHaveText('Projetos');
  const barra = page.getByRole('complementary', { name: 'Barra lateral' });
  for (const [nome, caminho, titulo] of [
    ['Estilos', '/estilos', 'Estilos'],
    ['Agenda', '/agenda', 'Agenda'],
    ['Canais', '/canais', 'Canais'],
    ['Configurações', '/configuracoes', 'Configurações'],
    ['Projetos', '/', 'Projetos'],
  ] as const) {
    await barra.getByRole('link', { name: nome, exact: true }).click();
    await expect(page).toHaveURL(caminho);
    await expect(tituloDaPagina(page)).toHaveText(titulo);
  }
});

test('endereço inexistente mostra a página de não encontrado', async ({ page }) => {
  await page.goto('/endereco-que-nao-existe');
  await expect(page.getByText('Página não encontrada')).toBeVisible();
  await page.getByRole('link', { name: 'Voltar aos projetos' }).click();
  await expect(tituloDaPagina(page)).toHaveText('Projetos');
});

test('o editor de estilo abre a partir da lista de estilos', async ({ page }) => {
  await page.goto('/estilos');
  const primeiro = page.locator('main a[href^="/estilos/"]').first();
  await expect(primeiro).toBeVisible();
  const destino = await primeiro.getAttribute('href');
  await primeiro.click();
  await expect(page).toHaveURL(destino ?? /\/estilos\//);
  await expect(tituloDaPagina(page)).not.toBeEmpty();
  await expect(page.getByText(/Não foi possível/)).toHaveCount(0);
});

test('um projeto criado aparece na lista e abre na aba Roteiro', async ({ page }) => {
  const tema = 'A cidade que mudou de lugar inteira';
  const id = await criarProjetoPelaApi(page, tema);
  await page.goto('/');
  const cartao = page.locator(`main a[href="/projetos/${id}"]`).first();
  await expect(cartao).toBeVisible();
  await cartao.click();
  await expect(page).toHaveURL(new RegExp(`/projetos/${id}`));
  await expect(tituloDaPagina(page)).toContainText(tema);
  await expect(page.getByRole('tab', { name: /Roteiro/ })).toHaveAttribute('aria-selected', 'true');
});

test.describe('no celular', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('o menu abre em gaveta e navega', async ({ page }) => {
    await page.goto('/');
    await expect(tituloDaPagina(page)).toHaveText('Projetos');
    await page.getByRole('button', { name: 'Abrir menu' }).click();
    const gaveta = page.getByRole('dialog', { name: 'Menu' });
    await expect(gaveta).toBeVisible();
    await gaveta.getByRole('link', { name: 'Estilos', exact: true }).click();
    await expect(gaveta).toBeHidden();
    await expect(tituloDaPagina(page)).toHaveText('Estilos');
  });
});
