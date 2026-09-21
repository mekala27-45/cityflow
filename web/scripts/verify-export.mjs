// Runs after next build. Two jobs.
//
// First, it removes the MapLibre development bundles from the export. Turbopack
// emits both the production and the development variants because MapLibre picks
// between them at runtime from its own module URL, and both ends of that choice
// look like static asset references to a bundler. The development branch is
// never taken here (src/components/map-canvas.tsx sets the worker URL before any
// map is constructed), so what actually ships is 2.5 MB of unreachable
// development code carrying its own doc comments, one of which names a CDN.
//
// Second, and more importantly, it fails the build if anything in the export
// points at a third party origin. The page claims to be self contained; a claim
// that nobody checks is a claim that quietly stops being true.

import { readdir, readFile, rm, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');
const outDir = join(webRoot, 'out');

const DEV_BUNDLE = /-dev(\.[A-Za-z0-9_-]+)?\.mjs$/;
const SCANNED = new Set(['.js', '.mjs', '.html', '.css', '.json', '.txt']);

// Origins that would make the page depend on something other than its own host.
// The repository link in the provenance banner is deliberate and is allowed.
const FORBIDDEN = [
  'cdn.jsdelivr.net',
  'jsdelivr',
  'unpkg',
  'fonts.googleapis',
  'fonts.gstatic',
  'cdnjs.cloudflare',
  'extensions.duckdb.org',
  'tiles.mapbox.com',
  'api.maptiler.com',
];

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}

async function main() {
  if (!existsSync(outDir)) throw new Error(`no export at ${outDir}`);

  let pruned = 0;
  let prunedBytes = 0;
  const mediaDir = join(outDir, '_next', 'static', 'media');
  if (existsSync(mediaDir)) {
    for (const entry of await readdir(mediaDir)) {
      if (!DEV_BUNDLE.test(entry)) continue;
      const path = join(mediaDir, entry);
      prunedBytes += (await stat(path)).size;
      await rm(path);
      pruned += 1;
    }
  }

  const offenders = [];
  for await (const path of walk(outDir)) {
    if (!SCANNED.has(extname(path))) continue;
    const text = await readFile(path, 'utf8');
    for (const needle of FORBIDDEN) {
      if (text.includes(needle)) offenders.push(`${relative(outDir, path)}: ${needle}`);
    }
  }

  process.stdout.write(
    `verify-export: pruned ${pruned} development bundles (${Math.round(prunedBytes / 1024)} kB)\n`,
  );

  if (offenders.length > 0) {
    process.stderr.write(`verify-export: the export references third party origins:\n`);
    for (const offender of offenders.slice(0, 20)) process.stderr.write(`  ${offender}\n`);
    process.exit(1);
  }
  process.stdout.write('verify-export: no third party origin in the export\n');
}

main().catch((err) => {
  process.stderr.write(`verify-export failed: ${err.stack || err}\n`);
  process.exit(1);
});
