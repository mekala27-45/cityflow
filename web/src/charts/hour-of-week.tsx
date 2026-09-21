'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { compactCount, hourLabel } from '@/lib/format';
import { sequentialRamp } from '@/lib/palette';
import { hourOfWeekAgg, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Cell {
  dow: number;
  day_name: string;
  hour: number;
  trips: number;
}

const DAY_ORDER = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/**
 * One row per day of week and hour, read straight out of agg_hour_of_week. Every
 * cell is a count of trips that actually started in that hour of that day.
 */
function buildSql(catalog: MetricCatalog, filters: Filters): string {
  return `with ${hourOfWeekAgg(filters)}
select day_of_week::int as dow,
       any_value(day_name) as day_name,
       hour::int as hour,
       ${catalog.projection('trips')}
from agg
group by 1, 3
order by 1, 3`;
}

export function HourOfWeek() {
  const { catalog, filters, bootstrap, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Cell>(sql, 'Hour of week grid');

  const fallback = useMemo<Cell[]>(
    () =>
      bootstrap
        ? bootstrap.hour_of_week.map((c) => ({ dow: c.dow, day_name: c.day, hour: c.hour, trips: c.trips }))
        : [],
    [bootstrap],
  );

  const cells = query.rows ?? fallback;
  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const max = useMemo(() => cells.reduce((acc, c) => Math.max(acc, Number(c.trips)), 0), [cells]);
  // The colour domain starts at the quietest cell rather than at zero. Nothing in
  // this grid is near zero, so anchoring the ramp there spends half of it on
  // values that do not occur and flattens the pattern the chart exists to show.
  // Both ends are printed in the legend so the floor is never mistaken for zero.
  const min = useMemo(
    () =>
      cells.length > 0
        ? cells.reduce((acc, c) => Math.min(acc, Number(c.trips)), Number.POSITIVE_INFINITY)
        : 0,
    [cells],
  );

  const days = useMemo(() => DAY_ORDER.filter((day) => cells.some((c) => c.day_name === day)), [cells]);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 680;
      const cellHeight = compact ? 22 : 30;
      return {
        width,
        height: Math.max(3, days.length) * cellHeight + 62,
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
          domain: days,
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
    [cells, days, min, max, ramp],
  );

  const peak = cells.reduce<Cell | null>((acc, c) => (!acc || Number(c.trips) > Number(acc.trips) ? c : acc), null);
  const trough = cells.reduce<Cell | null>((acc, c) => (!acc || Number(c.trips) < Number(acc.trips) ? c : acc), null);
  const total = cells.reduce((acc, c) => acc + Number(c.trips), 0);

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
      loading={query.loading}
      stale={query.loading && cells.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={
        <PlotFigure
          spec={spec}
          height={260}
          ariaLabel="Trips by hour of the week, day on the vertical axis and hour on the horizontal"
        />
      }
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
        <>
          Every cell is a count, measured at the grain agg_hour_of_week publishes: month, service,
          borough, day of week and hour. All four filters reach this chart, and the{' '}
          {cells.length.toLocaleString('en-US')} cells drawn here carry{' '}
          {compactCount(total)} trips, the same total the tiles above report for the same window. Trips
          whose pickup zone was never recorded are counted here, because this is a question about time
          rather than about place, and dropping them would put a different denominator under this chart
          than under the rest of the panel.
        </>
      }
    />
  );
}
