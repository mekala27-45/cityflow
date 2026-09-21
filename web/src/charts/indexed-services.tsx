'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { monthLabel } from '@/lib/format';
import { serviceColor, SERVICE_LABEL, SERVICE_ORDER } from '@/lib/palette';
import { dailyAgg, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  month: string;
  service: string;
  trips: number;
}

interface IndexedPoint {
  month: Date;
  service: string;
  trips: number;
  index: number;
}

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  return `with ${dailyAgg(filters)}
select date_trunc('month', date_day)::varchar as month, service, ${catalog.projection('trips')}
from agg
group by 1, 2
order by 1, 2`;
}

/**
 * Three services whose volumes differ by two orders of magnitude. Putting them
 * on one linear axis buries two of them; putting them on two axes invites the
 * reader to see a crossing that is an artefact of where the axes were pinned.
 * Indexing to a common base answers the question actually being asked, which is
 * about relative movement, and it needs exactly one scale.
 */
export function IndexedServices() {
  const { catalog, filters, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'monthly trips by service');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const points = useMemo<IndexedPoint[]>(() => {
    const base = new Map<string, number>();
    for (const row of rows) {
      const service = String(row.service);
      if (!base.has(service)) base.set(service, Number(row.trips));
    }
    return rows.map((row) => {
      const service = String(row.service);
      const start = base.get(service) ?? 0;
      return {
        month: new Date(`${row.month.slice(0, 10)}T00:00:00Z`),
        service,
        trips: Number(row.trips),
        index: start > 0 ? (Number(row.trips) / start) * 100 : Number.NaN,
      };
    });
  }, [rows]);

  const services = useMemo(
    () => SERVICE_ORDER.filter((s) => points.some((p) => p.service === s)),
    [points],
  );
  const baseMonth = rows[0]?.month ?? null;

  const spec = useCallback(
    (width: number) => {
      // The threshold is lower than elsewhere because this chart gives up its
      // direct labels when it crosses it, and losing them costs more than a
      // slightly tighter plot area does.
      const compact = width < 440;
      const lastByService = services
        .map((service) => {
          const inService = points.filter((p) => p.service === service);
          return inService.reduce<IndexedPoint | null>(
            (acc, p) => (!acc || p.month > acc.month ? p : acc),
            null,
          );
        })
        .filter((p): p is IndexedPoint => p !== null);

      return {
        width,
        height: compact ? 260 : 330,
        marginLeft: 48,
        marginRight: compact ? 16 : 122,
        marginTop: 18,
        marginBottom: 34,
        style: { background: 'transparent' },
        x: { type: 'utc' as const, label: null, grid: false },
        y: { label: 'Trips, indexed to the first month in the window as 100', grid: true, nice: true },
        marks: [
          Plot.ruleY([100], { stroke: 'var(--axis)', strokeDasharray: '4,4' }),
          Plot.lineY(points, {
            x: 'month',
            y: 'index',
            stroke: (d: IndexedPoint) => serviceColor(theme, d.service),
            strokeWidth: 2,
            strokeLinecap: 'round' as const,
            curve: 'monotone-x' as const,
          }),
          Plot.dot(points, {
            x: 'month',
            y: 'index',
            fill: (d: IndexedPoint) => serviceColor(theme, d.service),
            r: 4.5,
          }),
          // Direct labels as well as the legend: with three series the reader
          // should never have to carry a colour across the chart.
          compact
            ? null
            : Plot.text(lastByService, {
                x: 'month',
                y: 'index',
                text: (d: IndexedPoint) => SERVICE_LABEL[d.service] ?? d.service,
                dx: 10,
                textAnchor: 'start' as const,
                fontSize: 11,
                fill: 'var(--text-muted)',
              }),
          Plot.crosshairX(points, { x: 'month', y: 'index', color: 'var(--text-faint)' }),
          Plot.tip(
            points,
            Plot.pointer({
              x: 'month',
              y: 'index',
              title: (d: IndexedPoint) =>
                [
                  `${SERVICE_LABEL[d.service] ?? d.service}, ${monthLabel(d.month.toISOString())}`,
                  `Index ${d.index.toFixed(1)}`,
                  `${d.trips.toLocaleString('en-US')} trips`,
                ].join('\n'),
            }),
          ),
        ].filter(Boolean) as Plot.Markish[],
      };
    },
    [points, services, theme],
  );

  return (
    <ChartCard
      testId="chart-indexed-services"
      title="Which service grew, relative to where it started?"
      subtitle={
        baseMonth
          ? `Monthly trips for each service, indexed so that ${monthLabel(baseMonth)} is 100 for all three.`
          : 'Monthly trips for each service, indexed to a common base.'
      }
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
      chart={<PlotFigure spec={spec} height={300} ariaLabel="Monthly trips by service, indexed to a common base" />}
      table={
        <DataTable
          rows={points}
          columns={[
            { key: 'month', label: 'Month', render: (p) => monthLabel(p.month.toISOString()) },
            { key: 'service', label: 'Service', render: (p) => SERVICE_LABEL[p.service] ?? p.service },
            { key: 'trips', label: 'Trips', align: 'right', render: (p) => p.trips.toLocaleString('en-US') },
            { key: 'index', label: 'Index', align: 'right', render: (p) => p.index.toFixed(1) },
          ]}
          caption="Monthly trips and the index against each service's first month in the window."
          pageSize={18}
        />
      }
      footnote={
        <>
          Each line is that service against itself, not against the others: an index of 120 means twenty
          percent more trips than the service ran in the base month, and says nothing about whether it
          carries more people than another service. Changing the date range moves the base, so the lines
          rebase and the shape changes; that is the index working, not a bug. Absolute volumes are in the
          table, and the stacked area below shows the levels as shares.
        </>
      }
    />
  );
}
