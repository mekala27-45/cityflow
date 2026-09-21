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
  const formatted = applyFormat(current, metric.format);
  return (
    // Stacked from the top rather than justified apart: the interval notes differ
    // in length by four lines, and justify-between would slide the five headline
    // numbers to five different heights across the row.
    <div
      data-testid={`kpi-${name}`}
      className="flex flex-col rounded-lg border p-3"
      style={{ background: 'var(--panel)', borderColor: 'var(--border)' }}
    >
      {/* Fixed height, because two of the five labels wrap to a second line and
          the others do not, which would otherwise step the headline numbers down
          a line in those two tiles. */}
      <div className="flex min-h-[30px] items-start justify-between gap-2">
        <span className="text-[11px] uppercase leading-tight tracking-wide" style={{ color: 'var(--text-faint)' }}>
          {TILE_LABEL[name] ?? name}
        </span>
        <Sparkline values={spark} color={color} label={`${TILE_LABEL[name] ?? name}, last ${spark.length} days`} />
      </div>

      {/* Revenue over a three year window is fourteen characters wide. The size
          steps down rather than the number being clipped or abbreviated: a total
          the reader might quote belongs on the tile in full. */}
      <p
        className={`mt-2 mb-1 font-semibold tabular-nums ${
          formatted.length > 13 ? 'text-lg' : formatted.length > 10 ? 'text-xl' : 'text-2xl'
        }`}
        style={{ color: 'var(--text)' }}
        data-testid={`kpi-${name}-value`}
      >
        {formatted}
      </p>

      {level ? (
        <p className="pb-2 text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
          {applyFormat(level.lower, metric.format)} to {applyFormat(level.upper, metric.format)}{' '}
          <span style={{ color: 'var(--text-faint)' }}>({level.method})</span>
        </p>
      ) : (
        // No interval, and the catalog says why. The sentence is the metric
        // layer's, not this component's: whether a ratio of two totals can carry
        // an interval is a property of the metric, not of the tile.
        <p className="pb-2 text-[11px] leading-snug" style={{ color: 'var(--text-faint)' }}>
          {intervalNote(metric)}
        </p>
      )}

      <div className="mt-auto border-t pt-2" style={{ borderColor: 'var(--border)' }}>
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

  const windowQuery = useQuery<WindowRow>(windowSql, 'KPI tiles, window totals');
  const dailyQuery = useQuery<DailyRow>(dailySql, 'KPI tiles, daily series');

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

  // Three comparisons, in order of how well they read, and the tile says which
  // one it made. A window with a full equal length calendar before it inside the
  // source gets that. A window of two years or more has no calendar before it
  // but contains its own: the last 365 days against the 365 before them, which
  // is the year over year comparison and holds the season constant. Anything
  // shorter falls back to halves, which does not hold the season constant and is
  // labelled so nobody reads it as though it did.
  const YEAR = 365;
  const { later, earlier, comparison } = useMemo(() => {
    if (priorDaily.length >= 2) {
      return {
        later: currentDaily,
        earlier: priorDaily,
        comparison: `against the prior ${priorDays} days`,
      };
    }
    if (currentDaily.length >= 2 * YEAR) {
      return {
        later: currentDaily.slice(-YEAR),
        earlier: currentDaily.slice(-2 * YEAR, -YEAR),
        comparison: 'year over year, last 365 days against the 365 before',
      };
    }
    const half = Math.floor(currentDaily.length / 2);
    return {
      later: currentDaily.slice(half),
      earlier: currentDaily.slice(0, half),
      comparison: `later ${currentDaily.length - half} days against the first ${half}, seasons not matched`,
    };
  }, [currentDaily, priorDaily, priorDays]);

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
