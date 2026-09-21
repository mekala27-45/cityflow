'use client';

import { useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { Sparkline } from '@/components/sparkline';
import { DataTable } from '@/components/data-table';
import { useQuery } from '@/hooks/use-query';
import { applyFormat, signedPercent } from '@/lib/format';
import { intervalNote } from '@/lib/metrics';
import { seriesColor } from '@/lib/palette';
import { dailyAgg, windowDays, type Filters } from '@/lib/sql';
import { welchDelta, wilson, tInterval, type Interval } from '@/lib/stats';
import type { MetricCatalog } from '@/lib/metrics';

const TILE_METRICS = ['trips', 'revenue', 'mean_duration_min', 'tip_rate', 'airport_share'] as const;

const TILE_LABEL: Record<string, string> = {
  trips: 'Total trips',
  revenue: 'Revenue',
  mean_duration_min: 'Mean duration',
  tip_rate: 'Tip rate',
  airport_share: 'Airport share',
};

// Hue by metric, fixed. Not by position in the row and not by rank, so adding a
// sixth tile later cannot repaint the first five.
const TILE_SLOT: Record<string, number> = {
  trips: 1,
  revenue: 6,
  mean_duration_min: 3,
  tip_rate: 7,
  airport_share: 8,
};

interface WindowRow {
  period: Period;
  trips: number;
  revenue: number;
  mean_duration_min: number;
  tip_rate: number;
  airport_share: number;
  airport_num: number;
  airport_den: number;
}

type Period = 'current' | 'prior';

interface DailyRow {
  d: string;
  period: Period;
  trips: number;
  revenue: number;
  mean_duration_min: number;
  tip_rate: number;
  airport_share: number;
}

function shiftDate(iso: string, days: number): string {
  const base = Date.parse(`${iso}T00:00:00Z`);
  return new Date(base + days * 86_400_000).toISOString().slice(0, 10);
}

function priorWindow(filters: Filters): { from: string; to: string } {
  const days = windowDays(filters);
  return { to: shiftDate(filters.from, -1), from: shiftDate(filters.from, -days) };
}

function buildWindowSql(catalog: MetricCatalog, filters: Filters): string {
  const airport = catalog.get('airport_share');
  const prior = priorWindow(filters);
  // One scan covers both periods. The prior window is the same length of calendar
  // immediately before the current one, so a 90 day view compares against 90 days.
  const widened: Filters = { ...filters, from: prior.from };
  return `with ${dailyAgg(widened)}
select case when date_day >= DATE '${filters.from}' then 'current' else 'prior' end as period,
       ${catalog.projections(TILE_METRICS)},
       sum(${airport.numerator}) as airport_num,
       sum(${airport.denominator}) as airport_den
from agg
group by 1`;
}

function buildDailySql(catalog: MetricCatalog, filters: Filters): string {
  const prior = priorWindow(filters);
  const widened: Filters = { ...filters, from: prior.from };
  return `with ${dailyAgg(widened)}
select date_day::varchar as d,
       case when date_day >= DATE '${filters.from}' then 'current' else 'prior' end as period,
       ${catalog.projections(TILE_METRICS)}
from agg
group by 1, 2
order by 1`;
}

interface TileProps {
  name: string;
  catalog: MetricCatalog;
  current: number | null;
  level: Interval | null;
  delta: ReturnType<typeof welchDelta>;
  spark: number[];
  color: string;
  comparison: string;
}

function Tile({ name, catalog, current, level, delta, spark, color, comparison }: TileProps) {
  const metric = catalog.get(name);
  return (
    <div
      data-testid={`kpi-${name}`}
      className="flex flex-col justify-between rounded-lg border p-3"
      style={{ background: 'var(--panel)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide" style={{ color: 'var(--text-faint)' }}>
          {TILE_LABEL[name] ?? name}
        </span>
        <Sparkline values={spark} color={color} label={`${TILE_LABEL[name] ?? name}, last ${spark.length} days`} />
      </div>

      <p
        className="mt-2 text-2xl font-semibold tabular-nums"
        style={{ color: 'var(--text)' }}
        data-testid={`kpi-${name}-value`}
      >
        {applyFormat(current, metric.format)}
      </p>

      {level ? (
        <p className="mt-0.5 text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {applyFormat(level.lower, metric.format)} to {applyFormat(level.upper, metric.format)}{' '}
          <span style={{ color: 'var(--text-faint)' }}>({level.method})</span>
        </p>
      ) : (
        <p className="mt-0.5 text-[11px]" style={{ color: 'var(--text-faint)' }}>
          Level interval: {intervalNote(metric)}.
        </p>
      )}

      <div className="mt-2 border-t pt-2" style={{ borderColor: 'var(--border)' }}>
        {delta ? (
          <>
            <p className="text-xs tabular-nums" style={{ color: 'var(--text)' }}>
              {signedPercent(delta.relative)} {comparison}
            </p>
            <p className="text-[11px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
              95 percent interval {signedPercent(delta.relativeLower)} to {signedPercent(delta.relativeUpper)},
              Welch t on daily means
            </p>
          </>
        ) : (
          <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
            Not enough days in the window to compare anything against anything.
          </p>
        )}
      </div>
    </div>
  );
}

export function KpiRow() {
  const { catalog, filters, bootstrap, engineReady, theme } = useApp();

  const windowSql = useMemo(
    () => (catalog && engineReady ? buildWindowSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const dailySql = useMemo(
    () => (catalog && engineReady ? buildDailySql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );

  const windowQuery = useQuery<WindowRow>(windowSql, 'kpi window totals');
  const dailyQuery = useQuery<DailyRow>(dailySql, 'kpi daily series');

  // Before the engine answers, the tiles run on the precomputed bootstrap, which
  // covers the default window only. The fallback is labelled in the panel intro.
  const fallbackDaily = useMemo<DailyRow[]>(() => {
    if (!bootstrap) return [];
    return bootstrap.daily.map((row) => ({ ...row, period: 'current' as const }));
  }, [bootstrap]);

  const daily = dailyQuery.rows ?? fallbackDaily;
  const windowRows = windowQuery.rows ?? [];
  const current = windowRows.find((r) => r.period === 'current') ?? null;
  const priorDays = windowDays(filters);

  const currentDaily = useMemo(() => daily.filter((r) => r.period === 'current'), [daily]);
  const priorDaily = useMemo(() => daily.filter((r) => r.period === 'prior'), [daily]);

  // The source starts in January 2024, so the default window (the whole year)
  // has no calendar before it to compare against. Rather than print an arrow
  // with nothing behind it, the comparison falls back to the second half of the
  // window against the first, which is a different question and is labelled as
  // one on every tile.
  const splitComparison = priorDaily.length < 2 && currentDaily.length >= 8;
  const half = Math.floor(currentDaily.length / 2);
  const later = splitComparison ? currentDaily.slice(half) : currentDaily;
  const earlier = splitComparison ? currentDaily.slice(0, half) : priorDaily;
  const comparison = splitComparison
    ? `later ${later.length} days against the first ${earlier.length}`
    : `against the prior ${priorDays} days`;

  const tiles = TILE_METRICS.map((name) => {
    const series = currentDaily.map((r) => Number(r[name]));
    const spark = series.slice(-90);

    let value: number | null = current ? Number(current[name]) : null;
    if (value === null && bootstrap) {
      // Bootstrap has no window aggregate for ratios, so the tile shows the
      // engine result or nothing rather than a number computed from daily means.
      value = name === 'trips' || name === 'revenue' ? series.reduce((a, b) => a + b, 0) : null;
    }

    let level: Interval | null = null;
    const metric = catalog?.get(name);
    if (metric && current) {
      if (metric.interval === 'wilson' && name === 'airport_share') {
        level = wilson(Number(current.airport_num), Number(current.airport_den));
      } else if (metric.interval === 'student-t') {
        const t = tInterval(series);
        if (t) level = { ...t, point: value ?? t.point, method: t.method };
      }
    }

    return {
      name,
      value,
      level,
      delta: welchDelta(
        later.map((r) => Number(r[name])),
        earlier.map((r) => Number(r[name])),
      ),
      spark,
      color: seriesColor(theme, TILE_SLOT[name] ?? 1),
    };
  });

  const tableRows = daily.filter((r) => r.period === 'current');

  return (
    <div className="flex flex-col gap-2">
      {windowQuery.error ? (
        <div
          role="alert"
          data-testid="kpi-error"
          className="rounded border px-3 py-2 text-xs"
          style={{ borderColor: 'var(--status-bad)', color: 'var(--status-bad)' }}
        >
          <strong>The tile query failed.</strong> {windowQuery.error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {catalog
          ? tiles.map((tile) => (
              <Tile
                key={tile.name}
                name={tile.name}
                catalog={catalog}
                current={tile.value}
                level={tile.level}
                delta={tile.delta}
                spark={tile.spark}
                color={tile.color}
                comparison={comparison}
              />
            ))
          : null}
      </div>

      <details className="rounded-lg border px-3 py-2" style={{ background: 'var(--panel)', borderColor: 'var(--border)' }}>
        <summary className="cursor-pointer text-xs" style={{ color: 'var(--text-muted)' }}>
          Table view: the daily values behind the tiles and their sparklines
        </summary>
        <div className="mt-2">
          <DataTable
            rows={tableRows}
            columns={[
              { key: 'd', label: 'Date' },
              ...TILE_METRICS.map((name) => ({
                key: name,
                label: TILE_LABEL[name] ?? name,
                align: 'right' as const,
                // Format spec comes from the catalog, so a precision change in dbt
                // reaches the table without anyone editing this file.
                render: (row: DailyRow) => applyFormat(Number(row[name]), catalog?.get(name).format),
              })),
            ]}
            caption="One row per day inside the selected window."
          />
        </div>
      </details>
    </div>
  );
}
