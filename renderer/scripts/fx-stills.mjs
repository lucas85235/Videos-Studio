// Quadros fixos da composição "Edicao" (sample.ts): confere que planos, câmera e eventos renderizam sem erro e grava
// PNGs para o teste medir (punch no quadro N maior que 1,1 etc.). Roda à mão ou pelo
// `backend/tests/test_renderer_fx.py` com VS_RENDER_STILLS=1 (precisa do navegador do Remotion e do ffmpeg).
//
// Uso: node scripts/fx-stills.mjs --out <pasta> [--frames 0,30,...] [--browser <chrome-headless-shell.exe>]
// Saída (stdout, uma linha JSON): {"stills": {"<quadro>": "<png>"}, "clip": true|false}
//
// Tudo fica na pasta de saída e numa pasta temporária: o clipe de exemplo é gerado aqui (ffmpeg testsrc, conteúdo
// próprio) e servido por HTTP local, como o render-server faz com a pasta de dados.

import { bundle } from '@remotion/bundler';
import { openBrowser, renderStill, selectComposition } from '@remotion/renderer';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const rendererRoot = path.resolve(here, '..');
const repoRoot = path.resolve(rendererRoot, '..');

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, all) => {
    if (cur.startsWith('--')) acc.push([cur.slice(2), all[i + 1]]);
    return acc;
  }, []),
);
const outDir = path.resolve(args.out ?? path.join(os.tmpdir(), 'vulto-fx-stills'));
const log = (...items) => process.stderr.write(`[fx-stills] ${items.join(' ')}\n`);

const { sampleFxProps, SAMPLE_CLIP, SAMPLE_PUNCH } = await import(pathToFileURL(path.join(rendererRoot, 'src', 'sample.ts')).href);

const target = sampleFxProps.scenes.find((s) => s.id === SAMPLE_PUNCH.scene);
const punchAt = target.from + SAMPLE_PUNCH.at;
const defaults = [
  punchAt - 1, // antes do punch (escala 1)
  punchAt + 3, // punch inteiro
  10, // captura: marca-texto varrendo
  30, // círculo e seta
  46, // câmera no retângulo do marca-texto
  62, // B-roll com enquadramento e punch
  74, // tremor
  89, // whip saindo
  92, // whip entrando no clipe
  104, // câmera lenta + meme de canto
  140, // congelado + texto que pula
  210, // meme "congela + zoom"
];
const frames = (args.frames ? args.frames.split(',').map(Number) : defaults).filter((f) => Number.isFinite(f));

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vulto-fx-data-'));
const clipPath = path.join(dataDir, ...SAMPLE_CLIP.split('/'));
fs.mkdirSync(path.dirname(clipPath), { recursive: true });
const ff = spawnSync(
  'ffmpeg',
  ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=3', '-pix_fmt', 'yuv420p', '-y', clipPath],
  { encoding: 'utf-8' },
);
const clip = ff.status === 0 && fs.existsSync(clipPath);
if (!clip) log('ffmpeg indisponível: o plano de clipe fica sem vídeo');

const props = clip
  ? sampleFxProps
  : {
      ...sampleFxProps,
      scenes: sampleFxProps.scenes.map((s) => ({ ...s, shots: s.shots?.map((sh) => ({ ...sh, video: undefined })) })),
    };

const server = await new Promise((resolve) => {
  const srv = http.createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    const rel = decodeURIComponent(url.pathname.replace(/^\/files\//, ''));
    const full = path.resolve(dataDir, rel);
    if (!url.pathname.startsWith('/files/') || !full.startsWith(dataDir) || !fs.existsSync(full)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': 'video/mp4', 'Access-Control-Allow-Origin': '*' });
    fs.createReadStream(full).pipe(res);
  });
  srv.listen(0, '127.0.0.1', () => resolve(srv));
});
const assetBaseUrl = `http://127.0.0.1:${server.address().port}/files/`;

const browserExecutable = args.browser ?? process.env.VS_BROWSER ?? null;
let browser;
try {
  log('gerando bundle');
  const serveUrl = await bundle({
    entryPoint: path.join(rendererRoot, 'src', 'index.ts'),
    publicDir: path.join(repoRoot, 'assets'),
    outDir: path.join(dataDir, 'bundle'),
  });
  browser = await openBrowser('chrome', { browserExecutable, logLevel: 'error' });
  const inputProps = { ...props, assetBaseUrl, fontBaseUrl: null };
  const composition = await selectComposition({ serveUrl, id: 'Edicao', inputProps, puppeteerInstance: browser, logLevel: 'error' });
  fs.mkdirSync(outDir, { recursive: true });
  const stills = {};
  for (const frame of frames) {
    const output = path.join(outDir, `fx-${String(frame).padStart(4, '0')}.png`);
    await renderStill({ composition, serveUrl, frame, output, inputProps, puppeteerInstance: browser, imageFormat: 'png', overwrite: true, logLevel: 'error' });
    stills[frame] = output;
    log('quadro', frame, 'ok');
  }
  process.stdout.write(`${JSON.stringify({ stills, clip, punch: { at: punchAt, scale: SAMPLE_PUNCH.scale } })}\n`);
} finally {
  await browser?.close({ silent: true }).catch(() => {});
  server.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
}
