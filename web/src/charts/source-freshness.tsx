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
import { monthWindow, servicePredicate, type Filters } from '@/lib/sql';

interface Row {
  service: string;
  period: string;
  vintage: string;
  backend: string;
  source_rows: number;
  clean_rows: number;
  quarantined_rows: number;
  ingested_at: string;
  has_gap: boolean;
}

interface Point {
  period: Date;
  service: string;
  clean_rows: number;
  has_gap: boolean;
  /** Segment id: a new one starts wherever a month follows a gap. */
  segment: string;
}

function buildSql(filters: Filters): string {
  return `select service, source_period::varchar as period, vintage, backend,
       source_rows, clean_rows, quarantined_rows,
       ingested_at::varchar as ingested_at, has_gap
from 'mart_source_freshness.parquet'
where ${monthWindow(filters, 'source_period')}
  and ${servicePredicate(filters)}
order by service, source_period`;
}

export function SourceFreshness() {
  const { filters, engineReady, theme } = useApp();

  const sql = useMemo(() => (engineReady ? buildSql(filters) : null), [filters, engineReady]);
  const query = useQuery<Row>(sql, 'source freshness');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  // A gap is drawn as a gap. Breaking the series into segments at every month
  // flagged has_gap is the only honest way to show it: a line drawn straight
  // across a missing month asserts a value that was never read.
  const points = useMemo<Point[]>(() => {
    const out: Point[] = [];
    const counters = new Map<string, number>();
    for (const row of rows) {
      const service = String(row.service);
      if (!counters.has(service)) counters.set(service, 0);
      if (row.has_gap) counters.set(service, (counters.get(service) ?? 0) + 1);
      out.push({
        period: new Date(`${row.period.slice(0, 10)}T00:00:00Z`),
        service,
        clean_rows: Number(row.clean_rows),
        has_gap: Boolean(row.has_gap),
        segment: `${service}#${counters.get(service)}`,
      });
    }
    return out;
  }, [rows]);

  const services = useMemo(
    () => SERVICE_ORDER.filter((s) => points.some((p) => p.service === s)),
    [points],
  );
  const gaps = points.filter((p) => p.has_gap);
  const backends = [...new Set(rows.map((r) => String(r.backend)))];

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: compact ? 240 : 300,
        marginLeft: 62,
        marginRight: compact ? 16 : 130,
        marginTop: 16,
        marginBottom: 34,
        style: { background: 'transparent' },
        x: { type: 'utc' as const, label: null },
        y: { label: 'Rows kept after quarantine', grid: true, nice: true, zero: true },
        marks: [
          Plot.lineY(points, {
            x: 'period',
            y: 'clean_rows',
            // z is the segment, not the service, so the line stops at a gap.
            z: 'segment',
            stroke: (d: Point) => serviceColor(theme, d.service),
            strokeWidth: 2,
            strokeLinecap: 'round' as const,
          }),
          Plot.dot(points, {
            x: 'period',
            y: 'clean_rows',
            fill: (d: Point) => serviceColor(theme, d.service),
            r: 4.5,
            title: (d: Point) =>
              `${SERVICE_LABEL[d.service] ?? d.service}, ${monthLabel(d.period.toISOString())}\n${d.clean_rows.toLocaleString('en-US')} rows kept`,
            tip: true,
          }),
          gaps.length > 0
            ? Plot.ruleX(gaps, { x: 'period', stroke: 'var(--status-warn)', strokeDasharray: '3,3' })
            : null,
          compact
            ? null
            : Plot.text(
                services
                  .map((service) => {
                    const inService = points.filter((p) => p.service === service);
                    return inService.reduce<Point | null>((acc, p) => (!acc || p.period > acc.period ? p : acc), null);
                  })
                  .filter((p): p is Point => p !== null),
                {
                  x: 'period',
                  y: 'clean_rows',
                  text: (d: Point) => SERVICE_LABEL[d.service] ?? d.service,
                  dx: 10,
                  textAnchor: 'start' as const,
                  fontSize: 11,
                  fill: 'var(--text-muted)',
                },
              ),
          Plot.ruleY([0], { stroke: 'var(--axis)' }),
          Plot.crosshairX(points, { x: 'period', y: 'clean_rows', color: 'var(--text-faint)' }),
        ].filter(Boolean) as Plot.Markish[],
      };
    },
    [points, gaps, services, theme],
  );

  return (
    <ChartCard
      testId="chart-freshness"
      title="Did every month arrive, and how much of it survived?"
      subtitle={`Rows kept per source period, by service. Backend reported as ${backends.join(', ') || 'unknown'}.`}
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
      chart={<PlotFigure spec={spec} height={260} ariaLabel="Rows kept per source period by service, with gaps drawn as gaps" />}
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'period', label: 'Source period', render: (r) => monthLabel(r.period) },
            { key: 'service', label: 'Service', render: (r) => SERVICE_LABEL[r.service] ?? r.service },
            { key: 'vintage', label: 'Schema vintage' },
            { key: 'backend', label: 'Backend' },
            { key: 'source_rows', label: 'Source rows', align: 'right', render: (r) => Number(r.source_rows).toLocaleString('en-US') },
            { key: 'clean_rows', label: 'Rows kept', align: 'right', render: (r) => Number(r.clean_rows).toLocaleString('en-US') },
            { key: 'quarantined_rows', label: 'Quarantined', align: 'right', render: (r) => Number(r.quarantined_rows).toLocaleString('en-US') },
            { key: 'ingested_at', label: 'Ingested at', render: (r) => String(r.ingested_at).slice(0, 19).replace('T', ' ') },
            { key: 'has_gap', label: 'Follows a gap', render: (r) => (r.has_gap ? 'yes' : 'no') },
          ]}
          caption="One row per service and source period."
          pageSize={18}
        />
      }
      footnote={
        <>
          {gaps.length === 0 ? (
            <>
              No month in this window follows a gap, so every line is continuous. If one did, the line
              would stop and restart rather than slope across the hole: a segment drawn over a missing
              month asserts a number nobody read.
            </>
          ) : (
            <>
              {gaps.length} {gaps.length === 1 ? 'month follows' : 'months follow'} a gap, marked with an
              amber rule, and the line breaks there rather than sloping across the hole. A segment drawn
              over a missing month asserts a number nobody read.
            </>
          )}{' '}
          The schema vintage column in the table is what the loader matched the file against, which is how
          a column that appears partway through the history is handled without a null rate alarm.
        </>
      }
    />
  );
}
