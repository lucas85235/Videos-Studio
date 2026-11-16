import { existsSync } from 'node:fs';
import { defineConfig, devices } from '@playwright/test';
import { E2E_PORT } from './e2e/ambiente.mjs';

/**
 * Navegador dos testes de interface. Não baixamos navegador: usamos o Edge ou o Chrome já instalados (canal
 * "msedge"/"chrome" do Playwright). VS_E2E_CHANNEL força um canal; "chromium" usa o Chromium do Playwright
 * (só se você mesmo tiver rodado "npx playwright install chromium").
 */
const detectChannel = (): string => {
  if (process.env.VS_E2E_CHANNEL) return process.env.VS_E2E_CHANNEL;
  const localAppData = process.env.LOCALAPPDATA ?? '';
  const candidates: Record<string, string[]> = {
    win32: [
      'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
      'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
      'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
      // Instalação "só para este usuário", sem precisar de administrador.
      ...(localAppData ? [`${localAppData}\\Google\\Chrome\\Application\\chrome.exe`] : []),
    ],
    linux: ['/opt/google/chrome/chrome', '/opt/microsoft/msedge/msedge'],
    darwin: ['/Applications/Google Chrome.app', '/Applications/Microsoft Edge.app'],
  };
  const found = (candidates[process.platform] ?? []).find((p) => existsSync(p));
  if (!found) {
    throw new Error(
      'Não achei o Edge nem o Chrome instalados para os testes de interface. Defina VS_E2E_CHANNEL=msedge ou ' +
        'VS_E2E_CHANNEL=chrome se um deles estiver em outro lugar, ou instale o Chromium do Playwright ' +
        '("npx playwright install chromium") e use VS_E2E_CHANNEL=chromium.',
    );
  }
  return /edge/i.test(found) ? 'msedge' : 'chrome';
};

const baseURL = `http://127.0.0.1:${E2E_PORT}`;

export default defineConfig({
  testDir: './e2e',
  // Um backend só, com um banco só: os testes rodam em sequência para um não ver os projetos do outro pela metade.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  globalTimeout: 20 * 60_000,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL,
    channel: detectChannel(),
    locale: 'pt-BR',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    // Backend offline com pasta de dados temporária (veja e2e/servidor.mjs); ele mesmo serve a interface compilada.
    command: 'node e2e/servidor.mjs',
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
