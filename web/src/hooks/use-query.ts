'use client';

import { useEffect, useRef, useState } from 'react';
import { runQuery } from '@/lib/duckdb-client';

export interface QueryState<Row> {
  rows: Row[] | null;
  error: string | null;
  loading: boolean;
  durationMs: number | null;
  bytes: number | null;
  cached: boolean;
}

interface Settled<Row> {
  sql: string;
  rows: Row[] | null;
  error: string | null;
  durationMs: number | null;
  bytes: number | null;
  cached: boolean;
}

/**
 * Runs SQL against the WASM engine. The previous rows stay visible while the next
 * result is in flight, so a filter change dims a panel instead of emptying it.
 *
 * Loading is derived in render from whether the settled result belongs to the
 * current SQL, rather than pushed into state when the effect starts: that keeps
 * the effect to one job, starting and cancelling the request. A superseded
 * request is abandoned instead of being allowed to land out of order, and the
 * error is returned rather than logged, because a panel that fails silently is
 * worse than one that says why.
 *
 * sql is both the cache key and the dependency. Pass null to hold off entirely.
 */
export function useQuery<Row>(sql: string | null, label: string, enabled = true): QueryState<Row> {
  const [settled, setSettled] = useState<Settled<Row> | null>(null);
  const token = useRef(0);

  useEffect(() => {
    if (!sql || !enabled) return;
    const mine = token.current + 1;
    token.current = mine;
    const controller = new AbortController();

    runQuery<Row>(sql, { label, signal: controller.signal })
      .then((result) => {
        if (token.current !== mine) return;
        setSettled({
          sql,
          rows: result.rows,
          error: null,
          durationMs: result.durationMs,
          bytes: result.bytes,
          cached: result.cached,
        });
      })
      .catch((err: unknown) => {
        if (token.current !== mine) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setSettled({
          sql,
          rows: null,
          error: err instanceof Error ? err.message : String(err),
          durationMs: null,
          bytes: null,
          cached: false,
        });
      });

    return () => controller.abort();
  }, [sql, label, enabled]);

  const current = settled !== null && settled.sql === sql;
  return {
    rows: settled?.rows ?? null,
    error: current ? settled.error : null,
    loading: Boolean(sql) && enabled && !current,
    durationMs: current ? settled.durationMs : null,
    bytes: current ? settled.bytes : null,
    cached: current ? settled.cached : false,
  };
}
