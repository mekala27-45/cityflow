'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { serviceColor, SERVICE_LABEL, SERVICE_ORDER } from '@/lib/palette';
import { servicePredicate, type Filters } from '@/lib/sql';
import { boxFromHistogram, type BoxSummary } from '@/lib/stats';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  service: string;
  minute_bucket: number;
  trips: number;
}

interface Box extends BoxSummary {
  service: string;
  row: number;
}

const HALF = 0.3;
const NOTCH_HALF = 0.12;

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  return `with agg as (
  select service, minute_bucket, trips
  from 'agg_duration_dist.parquet'
  where ${servicePredicate(filters)}
)
select service, minute_bucket::int as minute_bucket, ${catalog.projection('trips')}
from agg
group by 1, 2
order by 1, 2`;
}

export function DurationBoxes() {
  const { catalog, filters, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'duration histogram by service');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const boxes = useMemo<Box[]>(() => {
    const byService = new Map<string, { value: number; count: number }[]>();
    for (const row of rows) {
      const service = String(row.service);
      const list = byService.get(service) ?? [];
      list.push({ value: Number(row.minute_bucket), count: Number(row.trips) });
      byService.set(service, list);
    }
    const ordered: string[] = SERVICE_ORDER.filter((s) => byService.has(s));
    const built: Box[] = [];
    for (const service of ordered) {
      const summary = boxFromHistogram(byService.get(service) ?? []);
      if (summary) built.push({ ...summary, service, row: built.length });
    }
    return built;
  }, [rows]);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 560;
      return {
        width,
        height: Math.max(170, boxes.length * (compact ? 62 : 78) + 56),
        marginLeft: compact ? 88 : 140,
        marginRight: 18,
        marginTop: 16,
        marginBottom: 38,
        style: { background: 'transparent' },
        x: { label: 'Trip duration, minutes', grid: true, domain: [0, 61], tickFormat: (v: number) => (v === 60 ? '60+' : String(v)) },
        y: {
          // Reversed so row zero sits at the top and the rows read in the same
          // fixed service order the legend does.
          domain: [boxes.length - 0.4, -0.6],
          ticks: boxes.map((b) => b.row),
          tickFormat: (v: number) => SERVICE_LABEL[boxes[v]?.service ?? ''] ?? '',
          label: null,
        },
        marks: [
          // Whiskers first, so the box sits on top of them, with caps at each end
          // so the reach of the distribution is visible against the grid.
          Plot.ruleY(boxes, {
            y: 'row',
            x1: 'min',
            x2: 'max',
            stroke: (d: Box) => serviceColor(theme, d.service),
            strokeOpacity: 0.5,
            strokeWidth: 2,
          }),
          Plot.ruleX(boxes, {
            x: 'min',
            y1: (d: Box) => d.row - 0.12,
            y2: (d: Box) => d.row + 0.12,
            stroke: (d: Box) => serviceColor(theme, d.service),
            strokeOpacity: 0.7,
            strokeWidth: 2,
          }),
          Plot.ruleX(boxes, {
            x: 'max',
            y1: (d: Box) => d.row - 0.12,
            y2: (d: Box) => d.row + 0.12,
            stroke: (d: Box) => serviceColor(theme, d.service),
            strokeOpacity: 0.7,
            strokeWidth: 2,
          }),
          // The box is drawn as three rectangles rather than one: the two arms at
          // full height and the waist between the notch bounds at a third of it.
          // That waist is the median interval, and two boxes whose waists do not
          // overlap have medians that differ.
          ...boxes.flatMap((box) => {
            const fill = serviceColor(theme, box.service);
            return [
              Plot.rect([box], {
                x1: 'q1',
                x2: 'notchLower',
                y1: () => box.row - HALF,
                y2: () => box.row + HALF,
                fill,
                fillOpacity: 0.55,
                stroke: fill,
                strokeWidth: 1.5,
                rx: 2,
              }),
              Plot.rect([box], {
                x1: 'notchUpper',
                x2: 'q3',
                y1: () => box.row - HALF,
                y2: () => box.row + HALF,
                fill,
                fillOpacity: 0.55,
                stroke: fill,
                strokeWidth: 1.5,
                rx: 2,
              }),
              Plot.rect([box], {
                x1: 'notchLower',
                x2: 'notchUpper',
                y1: () => box.row - NOTCH_HALF,
                y2: () => box.row + NOTCH_HALF,
                fill,
                fillOpacity: 0.85,
                stroke: fill,
                strokeWidth: 1.5,
                rx: 2,
              }),
              Plot.rect([box], {
                x1: 'q1',
                x2: 'q3',
                y1: () => box.row - HALF,
                y2: () => box.row + HALF,
                fill: 'transparent',
                title: (d: Box) =>
                  [
                    SERVICE_LABEL[d.service] ?? d.service,
                    `Median ${d.median} minutes`,
                    `Quartiles ${d.q1} to ${d.q3} minutes`,
                    `Median interval ${d.notchLower.toFixed(2)} to ${d.notchUpper.toFixed(2)}`,
                    `${d.n.toLocaleString('en-US')} trips`,
                  ].join('\n'),
                tip: true,
              }),
            ];
          }),
          Plot.ruleX(boxes, {
            x: 'median',
            y1: (d: Box) => d.row - HALF,
            y2: (d: Box) => d.row + HALF,
            stroke: 'var(--surface)',
            strokeWidth: 2,
          }),
        ],
      };
    },
    [boxes, theme],
  );

  return (
    <ChartCard
      testId="chart-duration-boxes"
      title="Does a trip take longer on one service than another?"
      subtitle="Quartiles and the median interval for trip duration, one box per service."
      legend={
        <Legend
          items={boxes.map((box) => ({
            label: SERVICE_LABEL[box.service] ?? box.service,
            color: serviceColor(theme, box.service),
          }))}
        />
      }
      loading={query.loading}
      stale={query.loading && boxes.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={200} ariaLabel="Notched box plots of trip duration by service" />}
      table={
        <DataTable
          rows={boxes}
          columns={[
            { key: 'service', label: 'Service', render: (b) => SERVICE_LABEL[b.service] ?? b.service },
            { key: 'q1', label: 'Lower quartile', align: 'right', render: (b) => `${b.q1} min` },
            { key: 'median', label: 'Median', align: 'right', render: (b) => `${b.median} min` },
            { key: 'q3', label: 'Upper quartile', align: 'right', render: (b) => `${b.q3} min` },
            { key: 'notchLower', label: 'Median interval lower', align: 'right', render: (b) => b.notchLower.toFixed(2) },
            { key: 'notchUpper', label: 'Median interval upper', align: 'right', render: (b) => b.notchUpper.toFixed(2) },
            { key: 'n', label: 'Trips', align: 'right', render: (b) => b.n.toLocaleString('en-US') },
          ]}
          caption="Quantiles read off the one minute duration histogram."
        />
      }
      footnote={
        <>
          The quantiles are read off agg_duration_dist, which is a histogram of one minute buckets, so
          every quartile and every median on this chart is accurate to a minute and no finer: a median
          printed as twelve means the twelfth minute bucket is where the running count crosses half, not
          that the median is 12.0 minutes. The waist is the usual 1.58 times the interquartile range over
          the square root of the count, the approximation that makes two boxes comparable at a glance:
          where two waists do not overlap, the medians differ. The upper whisker sits at the 60+ bucket
          wherever any trips fall in it, because that bucket is the top of the scale rather than a value.
        </>
      }
    />
  );
}
