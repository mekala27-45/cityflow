'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { PlotFigure } from '@/components/plot-figure';
import { bytes as formatBytes, ms as formatMs } from '@/lib/format';
import { seriesColor } from '@/lib/palette';
import type { BenchRow } from '@/lib/types';

interface Bar extends BenchRow {
  prunedPct: number;
  keptPct: number;
}

/**
 * The benchmark the build publishes, as evidence rather than as a claim. Latency
 * is measured locally against the same parquet the browser reads, which is the
 * lower bound: no network and no WebAssembly. What makes it fast is in the
 * pruning column, and that is a property of how the files were sorted and
 * written, not of the query engine.
 */
export function BenchTable() {
  const { manifest, theme } = useApp();
  const rows = useMemo<BenchRow[]>(() => manifest?.bench ?? [], [manifest]);

  const bars = useMemo<Bar[]>(
    () =>
      rows.map((row) => {
        const groups = row.row_groups || 1;
        const prunedPct = row.row_groups_skipped / groups;
        return { ...row, prunedPct, keptPct: 1 - prunedPct };
      }),
    [rows],
  );

  const pruning = useMemo(() => bars.filter((b) => b.row_groups_skipped > 0), [bars]);
  const best = pruning.reduce<Bar | null>((acc, b) => (!acc || b.prunedPct > acc.prunedPct ? b : acc), null);
  const slowest = bars.reduce<BenchRow | null>((acc, b) => (!acc || b.p95_ms > acc.p95_ms ? b : acc), null);
  const medianP95 = useMemo(() => {
    if (bars.length === 0) return null;
    const sorted = bars.map((b) => b.p95_ms).sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 1 ? sorted[middle]! : (sorted[middle - 1]! + sorted[middle]!) / 2;
  }, [bars]);

  const keptColor = seriesColor(theme, 1);
  const prunedColor = seriesColor(theme, 8);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 680;
      const ordered = [...bars].sort((a, b) => b.prunedPct - a.prunedPct);
      // Two segments of one bar, both shares of the same row group count, so one
      // axis is the whole story and a second would be inventing a second unit.
      const stacked = ordered.flatMap((row) => [
        { query: row.query, part: 'Row groups read', share: row.keptPct, row },
        { query: row.query, part: 'Row groups skipped', share: row.prunedPct, row },
      ]);
      return {
        width,
        height: Math.max(200, ordered.length * (compact ? 22 : 26) + 56),
        marginLeft: compact ? 140 : 250,
        marginRight: 18,
        marginTop: 14,
        marginBottom: 38,
        style: { background: 'transparent' },
        x: {
          label: 'Share of the file’s row groups',
          domain: [0, 1],
          grid: true,
          tickFormat: (v: number) => `${Math.round(v * 100)}%`,
        },
        y: { domain: ordered.map((row) => row.query), label: null, tickSize: 0 },
        color: {
          domain: ['Row groups read', 'Row groups skipped'],
          range: [keptColor, prunedColor],
        },
        marks: [
          Plot.barX(stacked, {
            y: 'query',
            x: 'share',
            fill: 'part',
            order: ['Row groups read', 'Row groups skipped'],
            // A two pixel surface gap between the segments, and between bars.
            stroke: 'var(--surface)',
            strokeWidth: 2,
            rx: 2,
            insetTop: 1,
            insetBottom: 1,
            title: (d: { row: Bar; part: string }) =>
              [
                d.row.query,
                `${d.row.file}, ${(d.row.file_bytes / (1024 * 1024)).toFixed(1)} MB`,
                `${d.row.row_groups_skipped} of ${d.row.row_groups} row groups skipped`,
                `p50 ${d.row.p50_ms} ms, p95 ${d.row.p95_ms} ms, p99 ${d.row.p99_ms} ms`,
              ].join('\n'),
            tip: true,
          }),
          Plot.ruleX([0], { stroke: 'var(--axis)' }),
        ],
      };
    },
    [bars, keptColor, prunedColor],
  );

  if (rows.length === 0) {
    return (
      <ChartCard
        testId="chart-bench"
        title="What does a panel query cost?"
        subtitle="Published with the build, in manifest.json."
        chart={
          <p className="py-6 text-center text-xs" style={{ color: 'var(--text-faint)' }}>
            No benchmark was published with this build.
          </p>
        }
      />
    );
  }

  return (
    <ChartCard
      testId="chart-bench"
      title="What does a panel query cost, and how much of the file does it skip?"
      subtitle={
        best
          ? `${pruning.length} of ${bars.length} queries prune row groups; the best skips ${Math.round(
              best.prunedPct * 100,
            )} percent of ${best.file}.`
          : 'Latency and row group pruning for every panel query.'
      }
      legend={
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--text-muted)' }}>
          <span className="flex items-center gap-1.5">
            <span aria-hidden style={{ width: 14, height: 8, borderRadius: 2, background: keptColor }} />
            Row groups read
          </span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden style={{ width: 14, height: 8, borderRadius: 2, background: prunedColor }} />
            Row groups skipped by the filter
          </span>
        </div>
      }
      chart={<PlotFigure spec={spec} height={240} ariaLabel="Row groups read against row groups skipped, per panel query" />}
      table={
        <DataTable
          rows={bars}
          columns={[
            { key: 'panel', label: 'Panel' },
            { key: 'query', label: 'Query' },
            { key: 'file', label: 'File' },
            { key: 'rows', label: 'Rows out', align: 'right', render: (r) => r.rows.toLocaleString('en-US') },
            { key: 'p50_ms', label: 'p50', align: 'right', render: (r) => formatMs(r.p50_ms) },
            { key: 'p95_ms', label: 'p95', align: 'right', render: (r) => formatMs(r.p95_ms) },
            { key: 'p99_ms', label: 'p99', align: 'right', render: (r) => formatMs(r.p99_ms) },
            {
              key: 'row_groups_skipped',
              label: 'Row groups skipped',
              align: 'right',
              render: (r) => `${r.row_groups_skipped} of ${r.row_groups}`,
            },
            { key: 'file_bytes', label: 'File', align: 'right', render: (r) => formatBytes(r.file_bytes) },
            {
              key: 'browser_bytes',
              label: 'Bytes in the browser',
              align: 'right',
              render: (r) => (r.browser_bytes === null ? 'not captured' : formatBytes(r.browser_bytes)),
            },
          ]}
          caption="One row per benchmarked panel query."
          pageSize={12}
        />
      }
      footnote={
        <>
          Latency is measured locally against the same parquet the browser reads, so it is a lower bound
          with no network and no WebAssembly in it: a query slow here is slow everywhere. Median p95 is{' '}
          {medianP95 === null ? 'not available' : formatMs(medianP95)} and the slowest is{' '}
          {slowest ? `${formatMs(slowest.p95_ms)} on ${slowest.query.toLowerCase()}` : 'not available'}.
          The skipped column is the one that matters: a row group whose column statistics cannot satisfy
          the filter is never read, so a query over a 31 MB file can touch a fraction of it. Queries that
          skip nothing are the ones that genuinely scan the window, which is what a whole window total is.
          The bytes column is filled in by the smoke test, which counts the range requests the engine
          actually issued; it is blank in a build where that log was not collected.
        </>
      }
    />
  );
}
