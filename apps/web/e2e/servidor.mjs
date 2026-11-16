// Sobe o backend do Vulto para os testes de interface: modo offline (provedores simulados, nada vai para a
// internet), pasta de dados temporária e porta própria. A interface servida é a compilada em apps/web/dist.
// Uso: node e2e/servidor.mjs (o Playwright chama sozinho pelo webServer do playwright.config.ts).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { E2E_DATA_DIR, E2E_PORT } from './ambiente.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const dist = join(root, 'apps', 'web', 'dist', 'index.html');

if (!existsSync(dist)) {
  console.error('A interface ainda não foi compilada. Rode "npm run web:build" (ou "npm run test:ui", que compila antes).');
  process.exit(1);
}

// Começa sempre do zero: banco e cache de uma rodada anterior não podem mudar o resultado.
rmSync(E2E_DATA_DIR, { recursive: true, force: true });
mkdirSync(E2E_DATA_DIR, { recursive: true });

const env = {
  ...process.env,
  VS_OFFLINE: '1',
  VS_DATA_DIR: E2E_DATA_DIR,
  VS_PORT: String(E2E_PORT),
  VS_HOST: '127.0.0.1',
  PYTHONUTF8: '1',
};
// O .venv do backend é o padrão do uv; um UV_PROJECT_ENVIRONMENT herdado (o start.ps1 aponta para a pasta de
// dados real) faria o teste usar outro ambiente.
delete env.UV_PROJECT_ENVIRONMENT;

const child = spawn('uv', ['run', '--no-sync', '--project', 'backend', 'python', '-m', 'studio', 'serve'], {
  cwd: root,
  env,
  stdio: 'inherit',
});
child.on('error', (err) => {
  console.error(`Não foi possível iniciar o backend com o uv (${err.message}). Instale o uv e rode "uv sync --project backend".`);
  process.exit(1);
});

const stop = () => {
  if (!child.killed) child.kill();
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
child.on('exit', (code) => process.exit(code ?? 0));
