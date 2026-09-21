'use client';

import * as duckdb from '@duckdb/duckdb-wasm';
import { absoluteDataUrl, assetUrl } from './base-path';
import { Lru } from './lru';
import { queryLog } from './query-log';

// The bundle map is written out by hand instead of using getJsDelivrBundles,
// because nothing on this network can reach a CDN and a silent fallback to one
// would turn into a page that works on the build machine and nowhere else.
// scripts/prepare-assets.mjs puts these four files in place; the cityflow-*
// workers wrap the DuckDB workers with XHR instrumentation.
//
// duckdb-wasm is pinned to 1.28.0 for the same reason. From 1.29.0 the parquet
// reader moved out of the core module into a loadable extension that the engine
// fetches from extensions.duckdb.org on first use. That request is unreachable
// here and would be an uncontrolled third party dependency on the deployed page
// even where it is reachable, so the last release with parquet statically linked
// is the one this project ships. Verified by querying duckdb_extensions() in the
// browser: on 1.29.0 and later parquet reports installed = false and the first
// scan traps.
function manualBundles(): duckdb.DuckDBBundles {
  return {
    mvp: {
      mainModule: assetUrl('/duckdb/duckdb-mvp.wasm'),
      mainWorker: assetUrl('/duckdb/cityflow-mvp.worker.js'),
    },
    eh: {
      mainModule: assetUrl('/duckdb/duckdb-eh.wasm'),
      mainWorker: assetUrl('/duckdb/cityflow-eh.worker.js'),
    },
  };
}

// Every parquet the dashboard can touch. Registering the URL rather than the
// bytes is the point of the architecture: DuckDB then reads footers and the
// column chunks a query actually needs over HTTP range requests, so opening the
// page does not cost the 7 MB the directory holds.
export const PARQUET_FILES = [
  'agg_zone_hour.parquet',
  'agg_hour_of_week.parquet',
  'agg_daily.parquet',
  'agg_daily_decomposition.parquet',
  'agg_od_flow.parquet',
  'agg_duration_dist.parquet',
  'agg_fare_distance.parquet',
  // No panel reads the trip level extract any more, now that the hour of week
  // grid is measured. It stays registered because registration is lazy and costs
  // nothing until a query names the file, and because it is what makes the detail
  // table reachable from window.__cityflowQuery for anyone checking an aggregate
  // against the rows underneath it.
  'detail_2024_06.parquet',
  'zone_comparisons.parquet',
  'dim_zone.parquet',
  'dim_date.parquet',
  'dim_hour.parquet',
  'dim_service.parquet',
  'dim_ratecode.parquet',
  'mart_quarantine.parquet',
  'mart_source_freshness.parquet',
  'mart_null_rates.parquet',
] as const;

export interface QueryResult<Row> {
  rows: Row[];
  durationMs: number;
  bytes: number;
  requests: number;
  cached: boolean;
}

const cache = new Lru<{ rows: unknown[]; durationMs: number; bytes: number; requests: number }>(64);

let dbPromise: Promise<duckdb.AsyncDuckDB> | null = null;
let connection: duckdb.AsyncDuckDBConnection | null = null;
let startedAt = 0;

// One connection executes one statement at a time regardless, so a queue costs
// nothing in throughput and buys exact instrumentation: the bytes a query pulled
// are the bytes the worker reported while that query, and only that query, held
// the lock. Without it three panels mounting together would report one query's
// traffic and three zeroes.
let lock: Promise<void> = Promise.resolve();

function acquire(): Promise<() => void> {
  let release!: () => void;
  const next = new Promise<void>((resolve) => {
    release = resolve;
  });
  const waitFor = lock;
  lock = lock.then(() => next);
  return waitFor.then(() => release);
}

async function boot(): Promise<duckdb.AsyncDuckDB> {
  queryLog.attach();
  startedAt = performance.now();
  const bundle = await duckdb.selectBundle(manualBundles());
  if (!bundle.mainWorker) throw new Error('no DuckDB worker bundle matched this browser');

  const worker = new Worker(bundle.mainWorker);
  const logger = new duckdb.ConsoleLogger(duckdb.LogLevel.ERROR);
  const db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker ?? undefined);

  // sum() over a BIGINT column returns HUGEINT, which Arrow hands back as a
  // Decimal128 that JavaScript cannot coerce to a number without help. Casting at
  // the engine boundary keeps that detail out of every call site, at the cost of
  // double precision above 2^53, which no count or currency total here reaches.
  await db.open({
    path: ':memory:',
    query: { castBigIntToDouble: true, castDecimalToDouble: true },
  });

  // false is "do not fetch now". DuckDB opens the file lazily on first reference
  // and then reads only the ranges a query needs.
  await Promise.all(
    PARQUET_FILES.map((file) =>
      db.registerFileURL(file, absoluteDataUrl(file), duckdb.DuckDBDataProtocol.HTTP, false),
    ),
  );

  connection = await db.connect();

  // A deliberate escape hatch for the benchmark script and for anyone who wants
  // to check a number on the page against the parquet directly, from a console.
  window.__cityflowQuery = (sql: string) => runQuery(sql, { label: 'console' }).then((r) => r.rows);

  queryLog.setEngineReady(performance.now() - startedAt);
  return db;
}

export function getEngine(): Promise<duckdb.AsyncDuckDB> {
  if (!dbPromise) {
    dbPromise = boot().catch((err) => {
      dbPromise = null;
      throw err;
    });
  }
  return dbPromise;
}

function toPlainRows<Row>(table: {
  toArray: () => unknown[];
}): Row[] {
  // Arrow rows are proxies over WASM memory. Copying to plain objects lets the
  // Arrow table be released and keeps React state free of engine lifetimes.
  return table.toArray().map((row) => {
    const source = row as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(source)) {
      const value = source[key];
      if (typeof value === 'bigint') {
        out[key] = Number(value);
      } else if (value instanceof Date) {
        out[key] = value.toISOString().slice(0, 10);
      } else {
        out[key] = value;
      }
    }
    return out as Row;
  });
}

export interface RunOptions {
  label?: string;
  signal?: AbortSignal;
}

export async function runQuery<Row>(sql: string, options: RunOptions = {}): Promise<QueryResult<Row>> {
  const label = options.label ?? 'query';

  const served = (hit: NonNullable<ReturnType<typeof cache.get>>): QueryResult<Row> => {
    queryLog.record({
      label,
      sql,
      startedAt: Date.now(),
      durationMs: 0,
      rows: hit.rows.length,
      bytes: 0,
      requests: 0,
      cached: true,
    });
    return { rows: hit.rows as Row[], durationMs: hit.durationMs, bytes: hit.bytes, requests: hit.requests, cached: true };
  };

  const early = cache.get(sql);
  if (early) return served(early);

  await getEngine();
  if (options.signal?.aborted) throw new DOMException('query cancelled', 'AbortError');
  if (!connection) throw new Error('DuckDB connection was not established');

  const release = await acquire();

  // Two panels that ask the same question mount together, so both miss the cache
  // before either has run. Looking again after the lock is what turns the second
  // one into a cache hit instead of a second full scan.
  const late = cache.get(sql);
  if (late) {
    release();
    return served(late);
  }

  const started = performance.now();
  queryLog.mark();
  try {
    const table = await connection.query(sql);
    const rows = toPlainRows<Row>(table as unknown as { toArray: () => unknown[] });
    const durationMs = performance.now() - started;
    // The worker reports over a channel, so give the last loadend a macrotask to
    // land before attributing bytes. Without it the final range request is missed.
    await new Promise((resolve) => setTimeout(resolve, 0));
    const claimed = queryLog.claim();
    queryLog.record({
      label,
      sql,
      startedAt: Date.now(),
      durationMs,
      rows: rows.length,
      bytes: claimed.bytes,
      requests: claimed.requests,
      cached: false,
    });
    cache.set(sql, { rows, durationMs, bytes: claimed.bytes, requests: claimed.requests });
    if (options.signal?.aborted) throw new DOMException('query cancelled', 'AbortError');
    return { rows, durationMs, bytes: claimed.bytes, requests: claimed.requests, cached: false };
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    const message = err instanceof Error ? err.message : String(err);
    queryLog.record({
      label,
      sql,
      startedAt: Date.now(),
      durationMs: performance.now() - started,
      rows: 0,
      bytes: 0,
      requests: 0,
      cached: false,
      error: message,
    });
    throw new Error(message);
  } finally {
    release();
  }
}

export function cacheSize(): number {
  return cache.size;
}
