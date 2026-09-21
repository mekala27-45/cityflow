'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { longDate, percent, signedPercent } from '@/lib/format';
import { seriesColor } from '@/lib/palette';
import { dailyAgg, dateWindow, servicePredicate, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';
import type { Changepoint } from '@/lib/types';

interface Row {
  d: string;
  trips: number;
  trend: number | null;
}

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  // The decomposition mart carries no day type, so it is joined on date and
  // service only. The panel says as much rather than pretending the day type
  // filter reaches the trend line.
  return `with ${dailyAgg(filters)},
obs as (
  select date_day, ${catalog.projection('trips')}
  from agg
  group by date_day
),
stl as (
  select date_day, sum(trend) as trend
  from 'agg_daily_decomposition.parquet'
  where ${dateWindow(filters)} and ${servicePredicate(filters)}
  group by date_day
)
select obs.date_day::varchar as d, obs.trips, stl.trend
from obs
left join stl on stl.date_day = obs.date_day
order by 1`;
}

export function DailyVolume() {
  const { catalog, filters, bootstrap, engineReady, theme, changepoints } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'Daily volume with trend');

  const fallback = useMemo<Row[]>(() => {
    if (!bootstrap) return [];
    const trendByDate = new Map(bootstrap.trend.map((t) => [t.d, t.trend]));
    return bootstrap.daily.map((row) => ({ d: row.d, trips: row.trips, trend: trendByDate.get(row.d) ?? null }));
  }, [bootstrap]);

  const rows = query.rows ?? fallback;

  const points = useMemo(
    () =>
      rows.map((r) => ({
        date: new Date(`${r.d}T00:00:00Z`),
        trips: Number(r.trips),
        trend: r.trend === null ? null : Number(r.trend),
      })),
    [rows],
  );

  // Changepoints are per service and the panel shows the services the filter
  // keeps. A shift detected on for hire vehicles is not a shift on the total, so
  // the rule is annotated with the service it belongs to.
  const marks = useMemo(() => {
    if (!changepoints) return [];
    const out: (Changepoint & { service: string })[] = [];
    for (const [service, list] of Object.entries(changepoints.detected)) {
      if (!filters.services.includes(service)) continue;
      for (const point of list) {
        if (point.date < filters.from || point.date > filters.to) continue;
        out.push({ ...point, service });
      }
    }
    return out.sort((a, b) => a.date.localeCompare(b.date));
  }, [changepoints, filters]);

  const observedColor = seriesColor(theme, 1);
  const trendColor = seriesColor(theme, 2);
  const ruleColor = seriesColor(theme, 3);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 560;
      // Every changepoint gets a rule and a marker. Only the ones far enough
      // apart to read get a text label: over three and a half years there are
      // clusters days wide, and printing all ten produces a smear rather than an
      // annotation. The footnote lists every one of them, and the markers carry
      // them individually on hover.
      const spanMs = points.length > 1 ? points[points.length - 1]!.date.getTime() - points[0]!.date.getTime() : 0;
      const minGapMs = spanMs / 14;
      let lastLabelled = Number.NEGATIVE_INFINITY;
      const annotated = marks.map((m, index) => {
        const date = new Date(`${m.date}T00:00:00Z`);
        const labelled = date.getTime() - lastLabelled >= minGapMs;
        if (labelled) lastLabelled = date.getTime();
        return { ...m, iso: m.date, date, tier: index % 2, labelled };
      });
      const labelled = annotated.filter((d) => d.labelled);
      return {
        width,
        height: compact ? 260 : 340,
        marginLeft: 58,
        marginRight: 16,
        marginTop: 28,
        marginBottom: 32,
        style: { background: 'transparent', color: 'var(--text-muted)' },
        x: { type: 'utc' as const, label: null, grid: false },
        y: { label: 'Trips per day', grid: true, nice: true, zero: true },
        marks: [
          Plot.areaY(points, { x: 'date', y: 'trips', fill: observedColor, fillOpacity: 0.1, curve: 'step' as const }),
          Plot.lineY(points, {
            x: 'date',
            y: 'trips',
            stroke: observedColor,
            // Twelve hundred days of a weekly cycle is a dense line. It is kept
            // thin and half transparent so the trend layer reads through it
            // rather than fighting it.
            strokeWidth: compact ? 0.7 : 0.9,
            strokeOpacity: 0.55,
          }),
          Plot.lineY(
            points.filter((p) => p.trend !== null),
            { x: 'date', y: 'trend', stroke: trendColor, strokeWidth: 2, strokeLinecap: 'round' as const },
          ),
          Plot.ruleX(annotated, { x: 'date', stroke: ruleColor, strokeWidth: 1.5, strokeDasharray: '3,3' }),
          Plot.dot(annotated, {
            x: 'date',
            y: 0,
            fill: ruleColor,
            r: 4,
            symbol: 'triangle' as const,
            title: (d: { iso: string; service: string; relative_change: number; before_mean: number; after_mean: number }) =>
              [
                `${d.service} on ${d.iso}`,
                `Level ${signedPercent(d.relative_change)}`,
                `${Math.round(d.before_mean).toLocaleString('en-US')} to ${Math.round(d.after_mean).toLocaleString('en-US')} trips per day`,
              ].join('\n'),
            tip: true,
          }),
          // dy is a constant in Plot, not a channel, so the two heights are two
          // marks over two halves of the same list.
          ...[0, 1].map((tier) =>
            Plot.text(
              labelled.filter((d) => d.tier === tier),
              {
                x: 'date',
                y: 0,
                text: (d: { iso: string; relative_change: number }) =>
                  `${d.iso} ${signedPercent(d.relative_change)}`,
                dy: tier === 0 ? -10 : -24,
                dx: 5,
                textAnchor: 'start' as const,
                fontSize: 9,
                fill: 'var(--text-faint)',
              },
            ),
          ),
          Plot.ruleY([0], { stroke: 'var(--axis)' }),
          Plot.crosshairX(points, { x: 'date', y: 'trips', color: 'var(--text-faint)' }),
          Plot.tip(
            points,
            Plot.pointerX({
              x: 'date',
              y: 'trips',
              title: (d: { date: Date; trips: number; trend: number | null }) =>
                [
                  longDate(d.date.toISOString()),
                  `Observed ${d.trips.toLocaleString('en-US')} trips`,
                  d.trend === null ? 'Trend not available' : `STL trend ${Math.round(d.trend).toLocaleString('en-US')}`,
                ].join('\n'),
            }),
          ),
        ],
      };
    },
    [points, marks, observedColor, trendColor, ruleColor],
  );

  return (
    <ChartCard
      testId="chart-daily-volume"
      title="Daily trip volume against its trend"
      subtitle="Observed trips per day with the STL trend component, and every level shift the changepoint search kept."
      legend={
        <Legend
          items={[
            { label: 'Observed trips', color: observedColor },
            { label: 'STL trend', color: trendColor },
            { label: 'Detected changepoint', color: ruleColor, dashed: true },
          ]}
        />
      }
      loading={query.loading}
      stale={query.loading && rows.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={300} ariaLabel="Daily trip volume with the STL trend and detected changepoints" />}
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'd', label: 'Date' },
            { key: 'trips', label: 'Observed trips', align: 'right' },
            { key: 'trend', label: 'STL trend', align: 'right', render: (r) => (r.trend === null ? '' : Math.round(Number(r.trend)).toLocaleString('en-US')) },
          ]}
          caption="Observed daily volume and the trend component of the STL decomposition."
        />
      }
      footnote={
        <>
          {marks.length === 0
            ? 'No changepoint falls inside this window for the selected services. '
            : `${marks.length} annotated changepoints fall inside this window: ${marks
                .map((m) => `${m.service} on ${m.date}, ${signedPercent(m.relative_change)}`)
                .join('; ')}. `}
          The search runs PELT on {changepoints?.searched_on ?? 'the STL trend component'}, period{' '}
          {changepoints?.stl_period ?? 7}, minimum segment {changepoints?.min_size_days ?? 14} days, q{' '}
          {changepoints?.q ?? 0.05}. Running it on the observed series returned eighty five candidates,
          because the weekly cycle is larger than most level shifts and PELT was finding the cycle.{' '}
          {changepoints
            ? `Across the whole window it found ${changepoints.found} candidates and dropped ${
                changepoints.below_effect_floor
              } for moving the level by less than ${percent(changepoints.min_effect, 0)}, leaving ${
                changepoints.found - changepoints.below_effect_floor
              } annotated.`
            : ''}{' '}
          <strong style={{ color: 'var(--text)' }}>
            A detected shift is a change in level, not a cause:
          </strong>{' '}
          the search knows nothing about fare policy, weather or a source that changed shape. The trend
          layer is joined on date and service only, because agg_daily_decomposition carries no day type,
          so the day type filter moves the observed line and not the trend.
        </>
      }
    />
  );
}
