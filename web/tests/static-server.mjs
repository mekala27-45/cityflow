// A static server for the exported site, used by the smoke test.
//
// It exists rather than a package because two things have to be true and no
// general purpose server guarantees both: the export must be served under the
// same /cityflow prefix GitHub Pages uses, or every asset URL in the bundle is
// wrong, and it must answer Range requests with 206, or DuckDB falls back to
// downloading whole parquet files and the architecture the page claims is not
// the architecture under test.

import { createReadStream, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', 'out');
const BASE = '/cityflow';
const PORT = Number(process.env.PORT ?? 4173);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json; charset=utf-8',
  '.parquet': 'application/octet-stream',
  '.wasm': 'application/wasm',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

function resolveTarget(urlPath) {
  const withoutBase = urlPath.startsWith(BASE) ? urlPath.slice(BASE.length) : null;
  if (withoutBase === null) return null;
  const clean = normalize(decodeURIComponent(withoutBase.split('?')[0] || '/'));
  if (clean.includes('..')) return null;
  let target = join(root, clean);
  try {
    if (statSync(target).isDirectory()) target = join(target, 'index.html');
  } catch {
    if (!extname(target)) target = `${target}.html`;
  }
  try {
    return statSync(target).isFile() ? target : null;
  } catch {
    return null;
  }
}

const server = createServer((request, response) => {
  const target = resolveTarget(request.url ?? '/');
  if (!target) {
    response.writeHead(404, { 'content-type': 'text/plain' });
    response.end('not found');
    return;
  }

  const size = statSync(target).size;
  const type = TYPES[extname(target)] ?? 'application/octet-stream';
  const headers = { 'content-type': type, 'accept-ranges': 'bytes', 'cache-control': 'no-cache' };

  if (request.method === 'HEAD') {
    // DuckDB opens a file with HEAD plus a Range header and expects a 206 with a
    // content length, which is how it learns the size without a body.
    const ranged = Boolean(request.headers.range);
    response.writeHead(ranged ? 206 : 200, {
      ...headers,
      'content-length': String(size),
      ...(ranged ? { 'content-range': `bytes 0-${size - 1}/${size}` } : {}),
    });
    response.end();
    return;
  }

  const range = request.headers.range;
  if (typeof range === 'string' && range.startsWith('bytes=')) {
    const [rawStart, rawEnd] = range.slice(6).split('-');
    const start = rawStart ? Number(rawStart) : 0;
    const end = rawEnd ? Math.min(Number(rawEnd), size - 1) : size - 1;
    if (!Number.isFinite(start) || start >= size || start > end) {
      response.writeHead(416, { ...headers, 'content-range': `bytes */${size}` });
      response.end();
      return;
    }
    response.writeHead(206, {
      ...headers,
      'content-range': `bytes ${start}-${end}/${size}`,
      'content-length': String(end - start + 1),
    });
    createReadStream(target, { start, end }).pipe(response);
    return;
  }

  response.writeHead(200, { ...headers, 'content-length': String(size) });
  createReadStream(target).pipe(response);
});

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`serving ${root} at http://127.0.0.1:${PORT}${BASE}/\n`);
});
