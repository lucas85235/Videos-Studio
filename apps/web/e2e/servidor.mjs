// Sobe o backend do Vulto para os testes de interface: modo offline (provedores simulados, nada vai para a
// internet), pasta de dados temporária e porta própria. A interface servida é a compilada em apps/web/dist.
// Uso: node e2e/servidor.mjs (o Playwright chama sozinho pelo webServer do playwright.config.ts).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { E2E_DATA_DIR, E2E_PORT } from './ambiente.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const backend = join(root, 'backend');
const dist = join(root, 'apps', 'web', 'dist');

const sair = (mensagem) => {
  console.error(mensagem);
  process.exit(1);
};

if (!existsSync(join(dist, 'index.html'))) {
  sair('A interface ainda não foi compilada. Rode "npm run web:build" (ou "npm run web:test:ui", que compila antes).');
}

// ---------- Pasta de dados: só apaga o que é claramente dos testes ----------

const MARCADOR = '.vulto-e2e';
const pastaReal = resolve(process.env.VS_DATA_DIR || join(process.env.USERPROFILE || homedir(), 'VideosStudioData'));
const dentroDe = (pai, filho) => {
  const r = relative(pai, filho);
  return r !== '' && !r.startsWith('..') && !isAbsolute(r);
};
// relative() ignora maiúsculas no Windows: "c:\users\x" e "C:\Users\X" contam como a mesma pasta.
const mesmaOuAcima = (a, b) => relative(a, b) === '' || dentroDe(a, b);

const pastaDados = resolve(E2E_DATA_DIR);
const proibidas = [pastaReal, root, homedir(), tmpdir()];
if (proibidas.some((p) => mesmaOuAcima(pastaDados, p))) {
  sair(
    `Recusei usar "${pastaDados}" como pasta dos testes de interface: ela é (ou contém) a pasta de dados real, ` +
      'o repositório, a sua pasta pessoal ou a pasta temporária, e ela é apagada a cada rodada. ' +
      'Aponte VS_E2E_DATA_DIR para uma pasta só dos testes (ou não defina, para usar %TEMP%\\vulto-e2e-<porta>).',
  );
}
const ehDeTeste =
  dentroDe(tmpdir(), pastaDados) || basename(pastaDados).startsWith('vulto-e2e') || existsSync(join(pastaDados, MARCADOR));
if (existsSync(pastaDados) && !ehDeTeste) {
  sair(
    `Recusei apagar "${pastaDados}": ela não está na pasta temporária, o nome não começa com "vulto-e2e" e ela não ` +
      `tem o arquivo ${MARCADOR} dos testes. Use uma pasta nova (ou vazia com esse nome) em VS_E2E_DATA_DIR.`,
  );
}

// Começa sempre do zero: banco e cache de uma rodada anterior não podem mudar o resultado.
rmSync(pastaDados, { recursive: true, force: true });
mkdirSync(pastaDados, { recursive: true });
writeFileSync(join(pastaDados, MARCADOR), 'Pasta dos testes de interface do Vulto: apagada a cada rodada.\n');

// ---------- Ambiente Python do backend (venv do uv) ----------

/** O venv existe de verdade (o uv grava pyvenv.cfg ao criar). */
const venvExiste = (pasta) => existsSync(join(pasta, 'pyvenv.cfg'));

// O venv é só o ambiente Python (os dados vêm de VS_DATA_DIR, trocado logo abaixo). Ordem: o herdado (o start.ps1
// aponta para a pasta de dados), o backend/.venv (padrão do uv, usado no CI) e o do setup.ps1, fora do OneDrive.
const candidatosVenv = [
  process.env.UV_PROJECT_ENVIRONMENT && resolve(backend, process.env.UV_PROJECT_ENVIRONMENT),
  join(backend, '.venv'),
  join(pastaReal, '.venv-backend'),
].filter(Boolean);
const venv = candidatosVenv.find(venvExiste);
if (!venv) {
  sair(
    'Não achei o ambiente Python do backend. Procurei em:\n' +
      candidatosVenv.map((p) => `  - ${p}`).join('\n') +
      '\nRode "scripts\\setup.ps1" (cria o venv em %USERPROFILE%\\VideosStudioData\\.venv-backend) ' +
      'ou aponte UV_PROJECT_ENVIRONMENT para um venv que já exista.',
  );
}

// ---------- Variáveis do backend ----------

// Nenhum VS_* herdado entra, a não ser os caminhos de programas (FFmpeg, Node), que só dizem onde eles estão.
// Os forçados aqui também ganham do .env da raiz (variável de ambiente tem prioridade sobre o arquivo).
const HERDAVEIS = new Set(['VS_FFMPEG', 'VS_FFPROBE', 'VS_NODE']);
const env = Object.fromEntries(
  Object.entries(process.env).filter(([k]) => !k.toUpperCase().startsWith('VS_') || HERDAVEIS.has(k.toUpperCase())),
);
Object.assign(env, {
  VS_OFFLINE: '1',
  VS_DATA_DIR: pastaDados,
  // Vazio = sem banco externo: o SQLite fica dentro da pasta temporária, nunca no banco do VS_DATABASE_URL do .env.
  VS_DATABASE_URL: '',
  VS_PORT: String(E2E_PORT),
  VS_HOST: '127.0.0.1',
  VS_WEB_DIST_DIR: dist,
  VS_WORKERS_ENABLED: '1',
  UV_PROJECT_ENVIRONMENT: venv,
  PYTHONUTF8: '1',
});

const child = spawn('uv', ['run', '--no-sync', '--project', 'backend', 'python', '-m', 'studio', 'serve'], {
  cwd: root,
  env,
  stdio: 'inherit',
});
child.on('error', (err) => {
  sair(`Não foi possível iniciar o backend com o uv (${err.message}). Instale o uv e rode "scripts\\setup.ps1".`);
});

const stop = () => {
  if (!child.killed) child.kill();
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
child.on('exit', (code) => process.exit(code ?? 0));
