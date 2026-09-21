'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { hourLabel, percent } from '@/lib/format';
import { intervalNote } from '@/lib/metrics';
import { seriesColor } from '@/lib/palette';
import { hourOfWeekAgg, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  borough: string;
  hour: number;
  tip_rate: number;
  observable_trips: number;
}

/**
 * Read from agg_hour_of_week rather than agg_zone_hour. Both carry borough, hour
 * and the tip components, but the hour of week aggregate is 4 MB against 25 MB
 * because it is grouped to borough instead of to zone, and this chart never asks
 * a question below borough. The cheaper file is not a shortcut here, it is the
 * right grain.
 */
function buildSql(catalog: MetricCatalog, filters: Filters): string {
  const tipRate = catalog.get('tip_rate');
  return `with ${hourOfWeekAgg(filters, { columns: ['hour', 'borough', 'tip_obs_tip_sum', 'tip_obs_fare_sum', 'tip_obs_trips'] })}
select borough, hour::int as hour,
       ${catalog.projection('tip_rate')},
       sum(${tipRate.components[0]}) as observable_trips
from agg
group by 1, 2
having sum(${tipRate.components[0]}) > 0
order by 1, 2`;
}

/**
 * One small multiple per borough, one series in each. Faceting is the answer to
 * six categories: six lines on one frame is a plate of spaghetti, and colouring
 * them would burn six of the eight series hues on a distinction the facet
 * already makes.
 */
export function TipRateMultiples() {
  const { catalog, filters, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'tip rate by hour and borough');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const points = useMemo(
    () =>
      rows.map((r) => ({
        borough: String(r.borough),
        hour: Number(r.hour),
        tip_rate: Number(r.tip_rate),
        observable_trips: Number(r.observable_trips),
      })),
    [rows],
  );

  const boroughs = useMemo(() => {
    const totals = new Map<string, number>();
    for (const p of points) totals.set(p.borough, (totals.get(p.borough) ?? 0) + p.observable_trips);
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  }, [points]);

  const color = seriesColor(theme, 1);
  const metric = catalog?.get('tip_rate');

  const spec = useCallback(
    (width: number) => {
      const compact = width < 680;
      return {
        width,
        height: Math.max(220, boroughs.length * (compact ? 78 : 96) + 50),
        marginLeft: compact ? 44 : 52,
        // Room for the facet axis on the right, which is where each panel's
        // borough name sits. Too little and the label is clipped to a stub.
        marginRight: compact ? 12 : 104,
        marginTop: 14,
        marginBottom: 34,
        style: { background: 'transparent' },
        x: {
          label: 'Hour of day',
          domain: [0, 23],
          ticks: compact ? [0, 6, 12, 18] : [0, 3, 6, 9, 12, 15, 18, 21],
          tickFormat: (h: number) => hourLabel(h).slice(0, 2),
          grid: true,
        },
        y: { label: 'Tip rate', grid: true, tickFormat: (v: number) => `${(v * 100).toFixed(0)}%`, zero: true },
        // Plot puts the facet axis on the right, which is the direct label: each
        // panel carries its borough's name beside it rather than in a key.
        fy: { domain: boroughs, label: null },
        marks: [
          Plot.areaY(points, { x: 'hour', y: 'tip_rate', fy: 'borough', fill: color, fillOpacity: 0.12, curve: 'monotone-x' as const }),
          Plot.lineY(points, {
            x: 'hour',
            y: 'tip_rate',
            fy: 'borough',
            stroke: color,
            strokeWidth: 2,
            curve: 'monotone-x' as const,
          }),
          Plot.dot(points, { x: 'hour', y: 'tip_rate', fy: 'borough', fill: color, r: 4 }),
          Plot.crosshairX(points, { x: 'hour', y: 'tip_rate', fy: 'borough', color: 'var(--text-faint)' }),
          Plot.tip(
            points,
            Plot.pointerX({
              x: 'hour',
              y: 'tip_rate',
              fy: 'borough',
              title: (d: { borough: string; hour: number; tip_rate: number; observable_trips: number }) =>
                [
                  `${d.borough}, ${hourLabel(d.hour)}`,
                  `Tip rate ${percent(d.tip_rate, 2)}`,
                  `${d.observable_trips.toLocaleString('en-US')} trips where a tip is observable`,
                ].join('\n'),
            }),
          ),
        ].filter(Boolean) as Plot.Markish[],
      };
    },
    [points, boroughs, color],
  );

  return (
    <ChartCard
      testId="chart-tip-rate"
      title="Does the tip rate move with the hour, and does it differ by borough?"
      subtitle="Tip as a share of fare, over the hours of the day, one panel per borough, ordered by volume."
      loading={query.loading}
      stale={query.loading && points.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={380} ariaLabel="Small multiples of tip rate by hour, one panel per borough" />}
      table={
        <DataTable
          rows={points}
          columns={[
            { key: 'borough', label: 'Borough' },
            { key: 'hour', label: 'Hour', render: (r) => hourLabel(r.hour) },
            { key: 'tip_rate', label: 'Tip rate', align: 'right', render: (r) => percent(r.tip_rate, 2) },
            { key: 'observable_trips', label: 'Observable trips', align: 'right', render: (r) => r.observable_trips.toLocaleString('en-US') },
          ]}
          caption="Tip rate by borough and hour, with the number of trips it was measured on."
          pageSize={24}
        />
      }
      footnote={
        <>
          The rate is tip over fare across the trips where a tip can be seen at all, which is card
          payments: a cash fare records a tip of zero whether or not one was handed over, and folding those
          in would turn a payment mix difference into a generosity difference.{' '}
          <strong style={{ color: 'var(--text)' }}>
            No interval is shown, and the catalog says why: {metric ? intervalNote(metric) : 'the catalog could not be read.'}
          </strong>{' '}
          The observable trip count is in the tooltip and the table so a point built on a few hundred trips
          is not read as firmly as one built on a hundred thousand. All four filters reach this chart;
          borough is applied to the pickup zone.
        </>
      }
    />
  );
}
