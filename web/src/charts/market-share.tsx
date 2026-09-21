'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { monthLabel, percent } from '@/lib/format';
import { serviceColor, SERVICE_LABEL, SERVICE_ORDER } from '@/lib/palette';
import { dailyAgg, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  month: string;
  service: string;
  trips: number;
}

interface SharePoint {
  month: Date;
  service: string;
  trips: number;
  share: number;
}

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  return `with ${dailyAgg(filters)}
select date_trunc('month', date_day)::varchar as month, service, ${catalog.projection('trips')}
from agg
group by 1, 2
order by 1, 2`;
}

export function MarketShare() {
  const { catalog, filters, engineReady, theme } = useApp();

  // Identical SQL text to the indexed chart, so the second of the two is served
  // from the query cache and costs nothing.
  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'monthly trips by service');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const points = useMemo<SharePoint[]>(() => {
    const totals = new Map<string, number>();
    for (const row of rows) totals.set(row.month, (totals.get(row.month) ?? 0) + Number(row.trips));
    return rows.map((row) => ({
      month: new Date(`${row.month.slice(0, 10)}T00:00:00Z`),
      service: String(row.service),
      trips: Number(row.trips),
      share: (totals.get(row.month) ?? 0) > 0 ? Number(row.trips) / (totals.get(row.month) ?? 1) : 0,
    }));
  }, [rows]);

  const services = useMemo(
    () => SERVICE_ORDER.filter((s) => points.some((p) => p.service === s)),
    [points],
  );

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: compact ? 240 : 300,
        marginLeft: 48,
        marginRight: 16,
        marginTop: 18,
        marginBottom: 34,
        style: { background: 'transparent' },
        x: { type: 'utc' as const, label: null },
        y: { label: 'Share of trips', grid: true, domain: [0, 1], tickFormat: (v: number) => `${Math.round(v * 100)}%` },
        marks: [
          Plot.areaY(points, {
            x: 'month',
            y: 'share',
            z: 'service',
            // The stack order is the fixed entity order, so a month where one
            // service overtakes another does not reshuffle the bands.
            order: services as unknown as string[],
            fill: (d: SharePoint) => serviceColor(theme, d.service),
            // A two pixel stroke in the surface colour is the gap: adjacent
            // segments stay distinct without a border that reads as a mark.
            stroke: 'var(--surface)',
            strokeWidth: 2,
            curve: 'monotone-x' as const,
          }),
          Plot.ruleY([0], { stroke: 'var(--axis)' }),
          Plot.crosshairX(points, { x: 'month', y: 'share', color: 'var(--text-faint)' }),
          Plot.tip(
            points,
            Plot.pointerX({
              x: 'month',
              y: 'share',
              z: 'service',
              title: (d: SharePoint) =>
                [
                  `${SERVICE_LABEL[d.service] ?? d.service}, ${monthLabel(d.month.toISOString())}`,
                  `${percent(d.share, 1)} of trips`,
                  `${d.trips.toLocaleString('en-US')} trips`,
                ].join('\n'),
            }),
          ),
        ],
      };
    },
    [points, services, theme],
  );

  return (
    <ChartCard
      testId="chart-market-share"
      title="How is the month split between the three services?"
      subtitle="Share of all trips in the month, stacked to one hundred percent."
      legend={
        <Legend
          items={services.map((service) => ({
            label: SERVICE_LABEL[service] ?? service,
            color: serviceColor(theme, service),
          }))}
        />
      }
      loading={query.loading}
      stale={query.loading && points.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={260} ariaLabel="Stacked area of service share of trips by month" />}
      table={
        <DataTable
          rows={points}
          columns={[
            { key: 'month', label: 'Month', render: (p) => monthLabel(p.month.toISOString()) },
            { key: 'service', label: 'Service', render: (p) => SERVICE_LABEL[p.service] ?? p.service },
            { key: 'trips', label: 'Trips', align: 'right', render: (p) => p.trips.toLocaleString('en-US') },
            { key: 'share', label: 'Share', align: 'right', render: (p) => percent(p.share, 2) },
          ]}
          caption="Monthly trips and each service's share of the month."
          pageSize={18}
        />
      }
      footnote={
        <>
          A share is only readable against a denominator, and the denominator here is the services the
          filter keeps: dropping one from the filter row does not shrink the stack, it redistributes it.
          The band at the bottom is the only one whose height can be read against the axis directly; the
          two above it are read as thicknesses, which is what a stacked area is for and the reason the
          indexed chart above exists alongside it.
        </>
      }
    />
  );
}
