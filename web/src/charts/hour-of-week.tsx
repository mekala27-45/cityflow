'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo, useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { compactCount, hourLabel } from '@/lib/format';
import { sequentialRamp } from '@/lib/palette';
import { dailyAgg, dateWindow, dayTypePredicate, hourProfileAgg, servicePredicate, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Cell {
  dow: number;
  day_name: string;
  hour: number;
  trips: number;
}

const DAY_ORDER = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

type Source = 'estimated' | 'measured';

/**
 * The aggregate carries hour by day type, not hour by day of week. The full year
 * grid is therefore a product of two measured things: the mean daily volume for
 * each day of week, and the hour profile of that day's day type. Row totals are
 * measured; the shape within a row is the day type profile, shared by the five
 * weekdays and by the two weekend days. The alternative would be to show only
 * June, which is what the measured mode does.
 */
function buildEstimatedSql(catalog: MetricCatalog, filters: Filters): string {
  const trips = catalog.projection('trips');
  return `with profile as (
  with ${hourProfileAgg(filters)}
  select day_type, hour, ${trips} from agg group by 1, 2
),
-- Re-summing the catalog's own output to get the denominator for the
-- allocation below. This is not a second definition of trips: profile.trips is
-- already whatever the catalog compiled.
profile_total as (select day_type, sum(trips) as total from profile group by 1),
daily as (
  with ${dailyAgg(filters)}
  select date_day, ${trips} from agg group by 1
),
dow as (
  select d.day_of_week::int as dow,
         any_value(d.day_name) as day_name,
         any_value(d.day_type) as day_type,
         avg(daily.trips) as mean_trips
  from daily
  join 'dim_date.parquet' d on d.date_day = daily.date_day
  group by 1
)
select dow.dow, dow.day_name, profile.hour::int as hour,
       -- allocation, not a metric: a measured daily level split by a measured
       -- hour profile for the same day type.
       profile.trips / profile_total.total * dow.mean_trips as trips
from dow
join profile on profile.day_type = dow.day_type
join profile_total on profile_total.day_type = profile.day_type
order by 1, 3`;
}

/** June 2024 at trip level. Yellow and green only: the detail extract holds no
 *  for hire vehicle rows, which the footnote states. */
function buildMeasuredSql(catalog: MetricCatalog, filters: Filters): string {
  const trips = catalog.rowLevelProjection('trips') ?? 'count(*) as trips';
  return `select d.day_of_week::int as dow,
       any_value(d.day_name) as day_name,
       hour(t.pickup_ts)::int as hour,
       ${trips}
from 'detail_2024_06.parquet' t
join 'dim_date.parquet' d on d.date_day = t.pickup_ts::date
where ${servicePredicate(filters, 't.service')}
  and ${dayTypePredicate(filters, 'd.day_type')}
  and ${dateWindow(filters, 'd.date_day')}
group by 1, 3
order by 1, 3`;
}

export function HourOfWeek() {
  const { catalog, filters, bootstrap, engineReady, theme } = useApp();
  const [source, setSource] = useState<Source>('estimated');

  const sql = useMemo(() => {
    if (!catalog || !engineReady) return null;
    return source === 'estimated' ? buildEstimatedSql(catalog, filters) : buildMeasuredSql(catalog, filters);
  }, [catalog, filters, engineReady, source]);

  const query = useQuery<Cell>(sql, `hour of week, ${source}`);

  const fallback = useMemo<Cell[]>(
    () => (bootstrap ? bootstrap.hour_of_week.map((c) => ({ dow: c.dow, day_name: c.day, hour: c.hour, trips: c.trips })) : []),
    [bootstrap],
  );

  // The bootstrap grid stands in for the full year mode only; the measured mode
  // has nothing to fall back to and shows an empty grid until the engine answers.
  const cells = useMemo(
    () => (source === 'estimated' ? (query.rows ?? fallback) : (query.rows ?? [])),
    [source, query.rows, fallback],
  );
  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const max = useMemo(() => cells.reduce((acc, c) => Math.max(acc, Number(c.trips)), 0), [cells]);
  // The colour domain starts at the quietest cell rather than at zero. Nothing in
  // this grid is near zero, so anchoring the ramp there spends half of it on
  // values that do not occur and flattens the pattern the chart exists to show.
  // Both ends are printed in the legend so the floor is never mistaken for zero.
  const min = useMemo(
    () => (cells.length > 0 ? cells.reduce((acc, c) => Math.min(acc, Number(c.trips)), Number.POSITIVE_INFINITY) : 0),
    [cells],
  );

  const spec = useCallback(
    (width: number) => {
      const compact = width < 680;
      const cellHeight = compact ? 22 : 30;
      return {
        width,
        height: 7 * cellHeight + 62,
        marginLeft: compact ? 40 : 84,
        marginRight: 10,
        marginTop: 26,
        marginBottom: 34,
        padding: 0,
        style: { background: 'transparent' },
        x: {
          domain: Array.from({ length: 24 }, (_, i) => i),
          label: null,
          tickFormat: (h: number) => (compact ? (h % 6 === 0 ? String(h) : '') : hourLabel(h).slice(0, 2)),
          axis: 'top' as const,
        },
        y: {
          domain: DAY_ORDER,
          label: null,
          tickFormat: (d: string) => (compact ? d.slice(0, 3) : d),
        },
        color: {
          type: 'linear' as const,
          range: ramp,
          domain: [min, max || 1],
          label: 'Trips',
        },
        marks: [
          Plot.cell(cells, {
            x: 'hour',
            y: 'day_name',
            fill: 'trips',
            // A two pixel surface gap keeps adjacent cells from fusing into a band.
            inset: 1,
            rx: 2,
            title: (d: Cell) =>
              `${d.day_name} ${hourLabel(d.hour)}\n${Math.round(Number(d.trips)).toLocaleString('en-US')} trips`,
            tip: true,
          }),
        ],
      };
    },
    [cells, min, max, ramp],
  );

  const peak = cells.reduce<Cell | null>((acc, c) => (!acc || Number(c.trips) > Number(acc.trips) ? c : acc), null);
  const trough = cells.reduce<Cell | null>((acc, c) => (!acc || Number(c.trips) < Number(acc.trips) ? c : acc), null);

  return (
    <ChartCard
      testId="chart-hour-of-week"
      title="The week, hour by hour"
      subtitle={
        peak && trough
          ? `Busiest cell is ${peak.day_name} at ${hourLabel(peak.hour)}, quietest is ${trough.day_name} at ${hourLabel(trough.hour)}.`
          : 'One hundred and sixty eight cells, one per hour of the week.'
      }
      legend={
        <RampLegend
          colors={ramp}
          low={compactCount(min)}
          high={compactCount(max)}
          caption="Trips in the cell, quietest to busiest"
        />
      }
      controls={
        <div className="flex overflow-hidden rounded border text-xs" style={{ borderColor: 'var(--border)' }} role="group" aria-label="Grid source">
          {(['estimated', 'measured'] as const).map((option) => (
            <button
              key={option}
              type="button"
              data-testid={`how-source-${option}`}
              aria-pressed={source === option}
              onClick={() => setSource(option)}
              className="px-2 py-1"
              style={{
                background: source === option ? 'var(--panel-raised)' : 'transparent',
                color: source === option ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              {option === 'estimated' ? 'Full year' : 'June, measured'}
            </button>
          ))}
        </div>
      }
      loading={query.loading}
      stale={query.loading && cells.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={260} ariaLabel="Trips by hour of the week, day on the vertical axis and hour on the horizontal" />}
      table={
        <DataTable
          rows={cells}
          columns={[
            { key: 'day_name', label: 'Day' },
            { key: 'hour', label: 'Hour', align: 'right', render: (c) => hourLabel(c.hour) },
            { key: 'trips', label: 'Trips', align: 'right', render: (c) => Math.round(Number(c.trips)).toLocaleString('en-US') },
          ]}
          caption="One row per hour of the week."
          pageSize={24}
        />
      }
      footnote={
        source === 'estimated' ? (
          <>
            Full year mode is a construction, not a direct measurement. agg_zone_hour carries hour by day
            type and agg_daily carries volume by date, and nothing in the warehouse carries hour by day of
            week. Each cell is the mean daily volume for that day of week multiplied by the share of trips
            its day type puts in that hour. Row totals are measured; the shape inside a row is the day type
            profile, so the five weekdays share one shape and the two weekend days share another. Switch to
            June, measured for the trip level grid, which is real but covers one month and, because the
            detail extract holds no for hire vehicle rows, only yellow and green. The borough filter does
            not reach this chart: agg_daily carries no geography, so a borough shaped hour profile against
            a citywide daily level would be two different populations in one cell.
          </>
        ) : (
          <>
            June 2024 at trip level from detail_2024_06, which holds yellow and green only: the for hire
            vehicle rows are not in the extract, so selecting that service alone empties this grid. Every
            cell here is a count of trips, not an allocation.
          </>
        )
      }
    />
  );
}
