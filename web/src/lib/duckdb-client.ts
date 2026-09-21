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

// The first four bytes of every parquet file, and of the last four. A reader
// that does not find them is not reading a parquet file.
const PARQUET_MAGIC = [0x50, 0x41, 0x52, 0x31]; // "PAR1"

export type DeliveryMode = 'range' | 'buffer';

export interface DeliveryRecord {
  file: string;
  mode: DeliveryMode;
  /** Set only when the file had to be fetched whole. */
  bytes?: number;
  /** Why the range path was rejected, for the data health panel. */
  reason?: string;
}

let delivery: DeliveryRecord[] = [];

/** What each parquet file ended up being read through, and why. */
export function deliveryReport(): DeliveryRecord[] {
  return delivery.slice();
}

/**
 * Decide how to read one file, by asking for its first four bytes.
 *
 * Registering a URL is the point of this architecture: DuckDB then reads
 * footers and the column chunks a query needs, rather than the whole file. That
 * only works if the host serves byte ranges of the bytes it claims to be
 * serving, and GitHub Pages, on which this is deployed, does not always do so.
 *
 * It gzip compresses parquet responses, and for some objects its edge answers a
 * range request out of the compressed representation while labelling the
 * response with the identity length. The first few kilobytes come back as
 * zeroes, the tail comes back correct, and the total size is right, so nothing
 * upstream notices. DuckDB reads the footer successfully, seeks to a column
 * chunk near the start of the file, gets zeroes, and fails inside the Thrift
 * parser with "Invalid data", which names neither the file nor the cause.
 *
 * Measured on the deployed site: a whole file GET returned the correct bytes
 * and the correct SHA-256 while a 'bytes=0-3' GET against the same URL in the
 * same second returned four zero bytes. Two of eighteen files were affected,
 * and a redeploy did not clear it.
 *
 * So the range path is used where it works and verified before it is trusted.
 * Four bytes per file, in parallel, is a cheap price for the difference between
 * a working page and an error nobody can act on. A file that fails the check is
 * fetched whole, which is correct on this host, and the fallback is reported
 * rather than hidden: a reader is entitled to know the page went around a
 * delivery fault instead of quietly downloading more than it said it would.
 */
async function register(db: duckdb.AsyncDuckDB, file: string): Promise<DeliveryRecord> {
  const url = absoluteDataUrl(file);
  let reason: string | undefined;

  try {
    const probe = await fetch(url, { headers: { Range: 'bytes=0-3' } });
    if (probe.status !== 206) {
      reason = `range request answered ${probe.status}, not 206`;
    } else {
      const head = new Uint8Array(await probe.arrayBuffer());
      if (head.length !== PARQUET_MAGIC.length || !PARQUET_MAGIC.every((b, i) => head[i] === b)) {
        reason = `range request returned ${hex(head)} where the parquet magic 50 41 52 31 should be`;
      }
    }
  } catch (err) {
    reason = `range request failed: ${String(err)}`;
  }

  if (!reason) {
    // false is "do not fetch now". DuckDB opens the file lazily on first
    // reference and then reads only the ranges a query needs.
    await db.registerFileURL(file, url, duckdb.DuckDBDataProtocol.HTTP, false);
    return { file, mode: 'range' };
  }

  const whole = await fetch(url);
  const bytes = new Uint8Array(await whole.arrayBuffer());
  const tail = bytes.subarray(bytes.length - PARQUET_MAGIC.length);
  if (!PARQUET_MAGIC.every((b, i) => bytes[i] === b) || !PARQUET_MAGIC.every((b, i) => tail[i] === b)) {
    // The fallback is for a host that mis-serves ranges. A whole file that is
    // also not a parquet is a different problem and must not be registered as
    // though it were one, because the error it produces later names a query
    // rather than a download.
    throw new Error(
      `${file} is not a parquet file as delivered: it starts ${hex(bytes.subarray(0, 4))} ` +
        `and ends ${hex(tail)}, and both should be 50 41 52 31. The copy in the ` +
        `repository is intact, so this is a delivery fault rather than a data one.`,
    );
  }
  await db.registerFileBuffer(file, bytes);
  return { file, mode: 'buffer', bytes: bytes.byteLength, reason };
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join(' ');
}

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

  delivery = await Promise.all(PARQUET_FILES.map((file) => register(db, file)));

  connection = await db.connect();

  // A deliberate escape hatch for the benchmark script and for anyone who wants
  // to check a number on the page against the parquet directly, from a console.
  window.__cityflowQuery = (sql: string) => runQuery(sql, { label: 'console' }).then((r) => r.rows);
  window.__cityflowDelivery = deliveryReport;

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
