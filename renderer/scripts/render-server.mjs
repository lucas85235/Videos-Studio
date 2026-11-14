// Servidor de render persistente do Videos Studio.
//
// Protocolo: uma mensagem JSON por linha no stdin; respostas JSON por linha no stdout.
//   -> {"id": "...", "type": "render", "props": {...}, "output": "C:/.../chunk.mp4", "concurrency": 4}
//   <- {"id": "...", "type": "progress", "progress": 0.42}
//   <- {"id": "...", "type": "done", "output": "..."}  |  {"id": "...", "type": "error", "message": "..."}
//   -> {"type": "shutdown"}
//
// Mantém um único bundle e um navegador aberto, e serve os arquivos do diretório de dados por HTTP local
// (assetBaseUrl) para as composições. Uso: node render-server.mjs --data-dir <dir> --bundle-dir <dir>

import { bundle } from '@remotion/bundler';
import { openBrowser, renderMedia, selectComposition } from '@remotion/renderer';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const rendererRoot = path.resolve(here, '..');
const repoRoot = path.resolve(rendererRoot, '..');

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, all) => {
    if (cur.startsWith('--')) acc.push([cur.slice(2), all[i + 1]]);
    return acc;
  }, []),
);
const dataDir = path.resolve(args['data-dir'] ?? path.join(repoRoot, 'data'));
const bundleDir = path.resolve(args['bundle-dir'] ?? path.join(dataDir, 'renderer-bundle'));
const gl = args.gl ?? 'angle';

const send = (msg) => process.stdout.write(`${JSON.stringify(msg)}\n`);
const log = (...items) => process.stderr.write(`[render-server] ${items.join(' ')}\n`);

const MIME = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.ttf': 'font/ttf',
  '.json': 'application/json',
};

// Servidor estático (somente leitura) para o diretório de dados.
const startStaticServer = () =>
  new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url ?? '/', 'http://localhost');
        if (!url.pathname.startsWith('/files/')) {
          res.writeHead(404).end();
          return;
        }
        const rel = decodeURIComponent(url.pathname.slice('/files/'.length));
        const full = path.resolve(dataDir, rel);
        if (!full.startsWith(dataDir) || !fs.existsSync(full) || !fs.statSync(full).isFile()) {
          res.writeHead(404).end();
          return;
        }
        res.writeHead(200, {
          'Content-Type': MIME[path.extname(full).toLowerCase()] ?? 'application/octet-stream',
          'Access-Control-Allow-Origin': '*',
          'Cache-Control': 'no-cache',
        });
        fs.createReadStream(full).pipe(res);
      } catch (err) {
        res.writeHead(500).end(String(err));
      }
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });

const main = async () => {
  const server = await startStaticServer();
  const port = server.address().port;
  const assetBaseUrl = `http://127.0.0.1:${port}/files/`;
  const fontBaseUrl = null;

  let serveUrl = path.join(bundleDir, 'index.html');
  if (!fs.existsSync(serveUrl)) {
    log('gerando bundle do renderer em', bundleDir);
    const tmp = await bundle({
      entryPoint: path.join(rendererRoot, 'src', 'index.ts'),
      publicDir: path.join(repoRoot, 'assets'),
      outDir: bundleDir,
      onProgress: (p) => send({ type: 'bundle', progress: p / 100 }),
    });
    serveUrl = tmp;
  } else {
    serveUrl = bundleDir;
  }
  log('bundle pronto:', serveUrl);

  const browser = await openBrowser('chrome', { chromiumOptions: { gl }, logLevel: 'error' });
  send({ type: 'ready', port, assetBaseUrl });

  let queue = Promise.resolve();
  const rl = readline.createInterface({ input: process.stdin });
  rl.on('line', (line) => {
    const text = line.trim();
    if (!text) return;
    let msg;
    try {
      msg = JSON.parse(text);
    } catch {
      send({ type: 'error', message: 'JSON inválido' });
      return;
    }
    if (msg.type === 'shutdown') {
      queue.finally(async () => {
        await browser.close({ silent: true }).catch(() => {});
        server.close();
        process.exit(0);
      });
      return;
    }
    if (msg.type !== 'render') return;
    queue = queue.then(async () => {
      const started = Date.now();
      const inputProps = { ...msg.props, assetBaseUrl, fontBaseUrl };
      try {
        const composition = await selectComposition({
          serveUrl,
          id: msg.composition ?? 'Timeline',
          inputProps,
          puppeteerInstance: browser,
          chromiumOptions: { gl },
          logLevel: 'error',
        });
        fs.mkdirSync(path.dirname(msg.output), { recursive: true });
        let lastSent = -1;
        await renderMedia({
          composition,
          serveUrl,
          codec: 'h264',
          outputLocation: msg.output,
          inputProps,
          puppeteerInstance: browser,
          chromiumOptions: { gl },
          muted: true,
          concurrency: msg.concurrency ?? 4,
          hardwareAcceleration: msg.hardwareAcceleration ?? 'if-possible',
          crf: msg.hardwareAcceleration === 'disable' ? 18 : undefined,
          imageFormat: 'jpeg',
          jpegQuality: 92,
          overwrite: true,
          logLevel: 'error',
          onProgress: ({ progress }) => {
            const rounded = Math.round(progress * 100);
            if (rounded !== lastSent) {
              lastSent = rounded;
              send({ id: msg.id, type: 'progress', progress });
            }
          },
        });
        send({ id: msg.id, type: 'done', output: msg.output, seconds: (Date.now() - started) / 1000 });
      } catch (err) {
        send({ id: msg.id, type: 'error', message: String(err?.stack ?? err) });
      }
    });
  });
  rl.on('close', () => {
    queue.finally(async () => {
      await browser.close({ silent: true }).catch(() => {});
      server.close();
      process.exit(0);
    });
  });
};

main().catch((err) => {
  send({ type: 'fatal', message: String(err?.stack ?? err) });
  process.exit(1);
});
