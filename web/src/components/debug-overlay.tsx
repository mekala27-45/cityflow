'use client';

import { useSyncExternalStore } from 'react';
import { useApp } from './app-context';
import { bytes as formatBytes, ms as formatMs } from '@/lib/format';
import { cacheSize } from '@/lib/duckdb-client';
import { queryLog, type QueryRecord } from '@/lib/query-log';

function subscribe(fn: () => void): () => void {
  return queryLog.subscribe(fn);
}

function snapshot(): readonly QueryRecord[] {
  return queryLog.getQueries();
}

/**
 * The numbers behind the architecture claim. A reader who does not believe that
 * the page pulls column chunks rather than whole parquet files can open this and
 * watch the range request count and the byte total move per query.
 */
export function DebugOverlay() {
  const { debugOpen, setDebugOpen, engineReady, engineError } = useApp();
  const queries = useSyncExternalStore(subscribe, snapshot, snapshot);
  const network = queryLog.getNetwork();

  const totalBytes = network.reduce((acc, n) => acc + n.bytes, 0);
  // A HEAD is how DuckDB learns a file's size; it moves no body, so it is not a
  // fetch and does not belong in the ratio the architecture claim rests on.
  const bodies = network.filter((n) => n.method !== 'HEAD');
  const ranged = bodies.filter((n) => n.ranged).length;
  const live = queries.filter((q) => !q.cached && !q.error);
  const slowest = live.reduce<QueryRecord | null>((acc, q) => (!acc || q.durationMs > acc.durationMs ? q : acc), null);
  const median = (() => {
    if (live.length === 0) return null;
    const sorted = [...live].map((q) => q.durationMs).sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 1 ? sorted[middle]! : ((sorted[middle - 1]! + sorted[middle]!) / 2);
  })();

  if (!debugOpen) {
    return (
      <button
        type="button"
        data-testid="debug-toggle"
        onClick={() => setDebugOpen(true)}
        className="fixed bottom-3 right-3 z-40 rounded-full border px-3 py-1.5 text-[11px] tabular-nums shadow-sm"
        style={{ background: 'var(--panel)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        {queries.length} queries, {formatBytes(totalBytes)}
      </button>
    );
  }

  return (
    <aside
      data-testid="debug-panel"
      className="fixed bottom-3 right-3 z-40 w-[min(420px,calc(100vw-1.5rem))] rounded-lg border shadow-lg"
      style={{ background: 'var(--panel)', borderColor: 'var(--border)' }}
      aria-label="Query benchmark"
    >
      <header className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-xs font-semibold" style={{ color: 'var(--text)' }}>
          Engine and query benchmark
        </h2>
        <button
          type="button"
          onClick={() => setDebugOpen(false)}
          className="rounded px-1.5 text-xs"
          style={{ color: 'var(--text-muted)' }}
          aria-label="Close benchmark panel"
        >
          Close
        </button>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-3 py-2.5 text-[11px] tabular-nums">
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Engine</dt>
          <dd style={{ color: engineError ? 'var(--status-bad)' : 'var(--text)' }}>
            {engineError ? 'failed' : engineReady ? formatMs(queryLog.engineReadyMs ?? 0) : 'booting'}
          </dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>First paint</dt>
          <dd style={{ color: 'var(--text)' }}>{formatMs(queryLog.firstPaintMs ?? Number.NaN)}</dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Queries</dt>
          <dd style={{ color: 'var(--text)' }}>
            {live.length} live, {queries.length - live.length} cached
          </dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Cache</dt>
          <dd style={{ color: 'var(--text)' }}>{cacheSize()} entries</dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Median query</dt>
          <dd style={{ color: 'var(--text)' }}>{median === null ? 'no data' : formatMs(median)}</dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Slowest</dt>
          <dd style={{ color: 'var(--text)' }}>{slowest ? formatMs(slowest.durationMs) : 'no data'}</dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Bytes pulled</dt>
          <dd style={{ color: 'var(--text)' }}>{formatBytes(totalBytes)}</dd>
        </div>
        <div className="flex justify-between">
          <dt style={{ color: 'var(--text-faint)' }}>Range requests</dt>
          <dd style={{ color: 'var(--text)' }}>
            {ranged} of {bodies.length}
          </dd>
        </div>
      </dl>

      <div className="cf-scroll max-h-52 overflow-y-auto border-t px-3 py-2" style={{ borderColor: 'var(--border)' }}>
        <table className="w-full text-[10px] tabular-nums">
          <thead>
            <tr style={{ color: 'var(--text-faint)' }}>
              <th scope="col" className="pb-1 text-left font-normal">
                Query
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Rows
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Bytes
              </th>
              <th scope="col" className="pb-1 text-right font-normal">
                Time
              </th>
            </tr>
          </thead>
          <tbody>
            {[...queries]
              .reverse()
              .slice(0, 40)
              .map((q) => (
                <tr key={q.id}>
                  <td className="max-w-[180px] truncate pr-2" style={{ color: q.error ? 'var(--status-bad)' : 'var(--text-muted)' }}>
                    {q.label}
                    {q.cached ? ' (cache)' : ''}
                  </td>
                  <td className="text-right" style={{ color: 'var(--text-faint)' }}>
                    {q.rows}
                  </td>
                  <td className="text-right" style={{ color: 'var(--text-faint)' }}>
                    {q.bytes ? formatBytes(q.bytes) : '0 B'}
                  </td>
                  <td className="text-right" style={{ color: 'var(--text-faint)' }}>
                    {q.cached ? '0 ms' : formatMs(q.durationMs)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <p className="border-t px-3 py-2 text-[10px] leading-relaxed" style={{ borderColor: 'var(--border)', color: 'var(--text-faint)' }}>
        Bytes are counted in the worker by wrapping XMLHttpRequest, which is where DuckDB issues its
        Range requests. Only response bodies count: the HEAD each file opens with moves headers and
        nothing else. The same records are on window.__cityflowQueryLog.
      </p>
    </aside>
  );
}
