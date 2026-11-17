import { esperarApiQuieta, expect, test, tituloDaPagina } from './apoio';

// Pesquisa de vídeos do canal: a tela abre pelo canal da URL, mostra o cartão de execução e a configuração,
// sem disparar nenhuma rodada (nada de cota do YouTube nem do plano Claude).
test('a pesquisa de vídeos abre pelo canal e mostra a configuração', async ({ page }) => {
  const res = await page.request.post('/api/channels', { data: { name: 'Canal da pesquisa e2e', niche: 'curiosidades' } });
  expect(res.ok(), await res.text()).toBeTruthy();
  const { id } = (await res.json()) as { id: string };

  await page.goto(`/pesquisa?canal=${id}`);
  await expect(tituloDaPagina(page)).toHaveText('Pesquisa de vídeos');
  await expect(page).toHaveTitle(/Pesquisa de vídeos · Vulto/);
  await expect(page.getByRole('combobox', { name: 'Canal' })).toHaveValue(id);
  await expect(page.getByRole('heading', { name: 'Rodar pesquisa' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simular' })).toBeVisible();
  await expect(page.getByText(/Nenhuma pesquisa do Canal da pesquisa e2e ainda/)).toBeVisible();

  await page.getByRole('tab', { name: /Configuração/ }).click();
  await expect(page).toHaveURL(/aba=config/);
  await expect(page.getByRole('checkbox', { name: /Brasil/ })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: /EUA/ })).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'Salvar configuração' })).toBeDisabled();

  await esperarApiQuieta(page);
  await expect(page.getByText(/Não foi possível/)).toHaveCount(0);
});

// No modo offline dos testes a síntese é o LLM simulado e não há chave da YouTube Data API: a rodada não gasta cota.
test('uma rodada offline mostra as sugestões com agenda, ranking e lacunas', async ({ page }) => {
  const res = await page.request.post('/api/channels', { data: { name: 'Canal da rodada e2e', niche: 'história' } });
  expect(res.ok(), await res.text()).toBeTruthy();
  const { id } = (await res.json()) as { id: string };

  await page.goto(`/pesquisa?canal=${id}`);
  await page.getByRole('button', { name: 'Pesquisar agora' }).click();
  const primeira = page.locator('[aria-label^="Ideia 1:"]');
  await expect(primeira).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('note', { name: 'Lacunas desta rodada' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Agenda \(por data\)/ })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: /Ranking \(por nota\)/ }).click();
  await expect(page.locator('[aria-label^="Ideia 1:"]')).toBeVisible();

  await page.getByRole('tab', { name: /Histórico/ }).click();
  await expect(page.getByRole('link', { name: 'Baixar relatório' })).toHaveCount(1);
  await esperarApiQuieta(page);
  await expect(page.getByText(/Não foi possível/)).toHaveCount(0);
});

test('a barra lateral leva à pesquisa de vídeos', async ({ page }) => {
  await page.goto('/');
  const barra = page.getByRole('complementary', { name: 'Barra lateral' });
  await barra.getByRole('link', { name: 'Pesquisa de vídeos', exact: true }).click();
  await expect(page).toHaveURL(/\/pesquisa/);
  await expect(tituloDaPagina(page)).toHaveText('Pesquisa de vídeos');
});
