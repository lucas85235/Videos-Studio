import { expect, test, tituloDaPagina } from './apoio';

// Fluxo principal de ponta a ponta, como o usuário faz: criar o projeto, revisar e aprovar o roteiro, conferir e
// aprovar as cenas e chegar à prévia. Offline, o roteiro e as cenas vêm do provedor simulado, a voz é um tom de
// teste e as imagens são procedurais, então nada sai do computador e nenhuma cota é gasta.
test('criar projeto → roteiro → cenas → prévia', async ({ page }) => {
  test.setTimeout(240_000);
  const tema = 'O farol que ficou aceso sem faroleiro';

  await page.goto('/novo');
  await expect(tituloDaPagina(page)).toHaveText('Novo projeto');
  // Sem tema, o botão fica bloqueado e diz por quê.
  await expect(page.getByRole('button', { name: 'Criar e gerar roteiro' })).toBeDisabled();
  await expect(page.getByText('Escreva o tema para continuar.')).toBeVisible();

  await page.getByPlaceholder('ex.: o farol que ficou aceso por 40 anos sem ninguém').fill(tema);
  await page.getByRole('button', { name: 'Criar e gerar roteiro' }).click();

  // Projeto criado: o roteiro é escrito sozinho e para esperando a revisão.
  await expect(page).toHaveURL(/\/projetos\/prj_/);
  await expect(tituloDaPagina(page)).toContainText(tema);
  await expect(page.getByText('Roteiro para revisar')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Trecho 1', { exact: true })).toBeVisible();
  await expect(page.getByText('Gancho', { exact: true }).first()).toBeVisible();

  // Aprovar o roteiro cria as cenas e para para conferir antes de gerar imagens.
  await page.getByRole('button', { name: 'Aprovar e criar as cenas' }).click();
  await expect(page.getByText('Roteiro aprovado', { exact: true })).toBeVisible({ timeout: 60_000 });
  const conferir = page.getByRole('button', { name: 'Conferir as cenas' });
  await expect(conferir).toBeVisible({ timeout: 60_000 });
  await conferir.click();
  await expect(page).toHaveURL(/aba=cenas/);
  await expect(page.getByRole('tab', { name: /Cenas/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Cena 1', { exact: true })).toBeVisible();

  // Aprovar as cenas gera as imagens e monta a prévia.
  await page.getByRole('button', { name: 'Aprovar cenas e gerar imagens' }).click();
  await expect(page.getByText('Prévia para aprovar')).toBeVisible({ timeout: 180_000 });

  await page.getByRole('tab', { name: /Prévia/ }).click();
  await expect(page).toHaveURL(/aba=previa/);
  await expect(page.getByText('Voz e imagens de teste')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Aprovar e renderizar' })).toBeVisible();
  await expect(page.getByText(/Cena 1\/\d+/)).toBeVisible();
  // O histórico registra cada etapa. As "Últimas versões" mostram 5: o pacote de título e thumb (gerado e travado
  // sozinho no Short, antes do roteiro) empurra o "Projeto criado" para o "Ver todas".
  await expect(page.getByText(/^Pacote aprovado: /).first()).toBeVisible();
  for (const etapa of ['Roteiro gerado', 'Roteiro aprovado', 'Cenas aprovadas']) {
    await expect(page.getByText(etapa, { exact: true }).first()).toBeVisible();
  }
});
