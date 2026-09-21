'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { hourLabel } from '@/lib/format';
import { sequentialAt, sequentialRamp } from '@/lib/palette';
import { servicePredicate, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  hour: number;
  minute_bucket: number;
  trips: number;
}

interface RidgePoint {
  hour: number;
  minute: number;
  baseline: number;
  y: number;
  share: number;
  trips: number;
}

// How far the tallest ridge climbs into the rows above it. Two rows is enough
// overlap to read as a ridgeline and little enough that a row underneath is not
// buried by the one in front of it.
const OVERLAP = 2.0;

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  // agg_duration_dist is keyed by service and hour only: it carries no date and
  // no day type, so those two filters cannot reach this chart.
  return `with agg as (
  select hour, minute_bucket, trips
  from 'agg_duration_dist.parquet'
  where ${servicePredicate(filters)}
)
select hour::int as hour, minute_bucket::int as minute_bucket, ${catalog.projection('trips')}
from agg
group by 1, 2
order by 1, 2`;
}

export function DurationRidgeline() {
  const { catalog, filters, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'duration distribution by hour');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const points = useMemo<RidgePoint[]>(() => {
    const byHour = new Map<number, Row[]>();
    for (const row of rows) {
      const hour = Number(row.hour);
      const list = byHour.get(hour) ?? [];
      list.push(row);
      byHour.set(hour, list);
    }
    // Height is the share of an hour's trips in a one minute bucket, so every
    // ridge is a distribution over the same denominator. The scale factor is
    // global rather than per ridge: normalising each row to its own peak would
    // make all twenty four exactly the same height and throw away the fact that
    // some hours concentrate on a few minutes and others spread.
    const shares: RidgePoint[] = [];
    let peak = 0;
    for (const [hour, list] of byHour) {
      const total = list.reduce((acc, r) => acc + Number(r.trips), 0);
      const baseline = 23 - hour;
      for (const row of list) {
        const share = Number(row.trips) / (total || 1);
        if (share > peak) peak = share;
        shares.push({ hour, minute: Number(row.minute_bucket), baseline, y: baseline, share, trips: Number(row.trips) });
      }
    }
    const scale = peak > 0 ? OVERLAP / peak : 0;
    const out = shares.map((p) => ({ ...p, y: p.baseline + p.share * scale }));
    return out.sort((a, b) => a.hour - b.hour || a.minute - b.minute);
  }, [rows]);

  const hours = useMemo(() => [...new Set(points.map((p) => p.hour))].sort((a, b) => a - b), [points]);
  const ramp = useMemo(() => sequentialRamp(theme), [theme]);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      const rowHeight = compact ? 13 : 17;
      return {
        width,
        height: 24 * rowHeight + 70,
        marginLeft: compact ? 40 : 54,
        marginRight: 16,
        marginTop: 16,
        marginBottom: 40,
        style: { background: 'transparent' },
        x: {
          label: 'Trip duration, minutes',
          domain: [0, 61],
          grid: true,
          ticks: compact ? [0, 15, 30, 45, 60] : [0, 5, 10, 15, 20, 25, 30, 40, 50, 60],
          tickFormat: (v: number) => (v === 60 ? '60+' : String(v)),
        },
        y: {
          label: null,
          domain: [-0.6, 23 + OVERLAP + 0.4],
          ticks: hours.map((h) => 23 - h),
          tickFormat: (v: number) => hourLabel(23 - v),
        },
        marks: [
          ...hours.map((hour) =>
            Plot.areaY(
              points.filter((p) => p.hour === hour),
              {
                x: 'minute',
                y1: 'baseline',
                y2: 'y',
                // One hue, lighter as the day gets later, so the ramp carries the
                // hour and nothing has to be looked up in a key.
                fill: sequentialAt(theme, hour / 23),
                fillOpacity: 0.92,
                curve: 'basis' as const,
              },
            ),
          ),
          ...hours.map((hour) =>
            Plot.line(
              points.filter((p) => p.hour === hour),
              {
                x: 'minute',
                y: 'y',
                stroke: 'var(--surface)',
                strokeWidth: 1.25,
                curve: 'basis' as const,
              },
            ),
          ),
          Plot.tip(
            points,
            Plot.pointer({
              x: 'minute',
              y: 'y',
              title: (d: RidgePoint) =>
                [
                  `${hourLabel(d.hour)}, ${d.minute === 60 ? '60+' : d.minute} minutes`,
                  `${(d.share * 100).toFixed(1)} percent of trips in that hour`,
                  `${d.trips.toLocaleString('en-US')} trips`,
                ].join('\n'),
            }),
          ),
        ],
      };
    },
    [points, hours, theme],
  );

  return (
    <ChartCard
      testId="chart-duration-ridgeline"
      title="How long does a trip take, hour by hour?"
      subtitle="One ridge per hour of the day. Every ridge is a distribution over the same denominator and they share one height scale, so a narrower ridge means a tighter spread of trip times."
      legend={<RampLegend colors={ramp} low="00:00" high="23:00" caption="Hour of day" />}
      loading={query.loading}
      stale={query.loading && points.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={380} ariaLabel="Ridgeline of trip duration distributions by hour of day" />}
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'hour', label: 'Hour', render: (r) => hourLabel(Number(r.hour)) },
            { key: 'minute_bucket', label: 'Duration, minutes', align: 'right', render: (r) => (Number(r.minute_bucket) === 60 ? '60+' : String(r.minute_bucket)) },
            { key: 'trips', label: 'Trips', align: 'right', render: (r) => Number(r.trips).toLocaleString('en-US') },
          ]}
          caption="One row per hour and one minute duration bucket."
          pageSize={30}
        />
      }
      footnote={
        <>
          Height is the share of that hour&apos;s trips falling in a one minute bucket, not volume, so the
          ridges compare shape rather than size. The last bucket is everything at sixty minutes or longer
          and is labelled 60+, which is why it stands up: it is a tail folded into one column, not a spike
          at exactly an hour. agg_duration_dist is keyed by service and hour only, so the date and day type
          filters do not reach this chart; the service filter does.
        </>
      }
    />
  );
}
