'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { monthLabel, percent } from '@/lib/format';
import { sequentialRamp, SERVICE_LABEL, SERVICE_ORDER } from '@/lib/palette';
import { monthWindow, servicePredicate, type Filters } from '@/lib/sql';

interface Row {
  service: string;
  period: string;
  column_name: string;
  rows: number;
  null_rows: number;
  null_share: number;
}

function buildSql(filters: Filters): string {
  return `select service, source_period::varchar as period, column_name,
       rows, null_rows, null_share
from 'mart_null_rates.parquet'
where ${monthWindow(filters, 'source_period')}
  and ${servicePredicate(filters)}
order by service, column_name, source_period`;
}

export function NullRates() {
  const { filters, engineReady, theme } = useApp();

  const sql = useMemo(() => (engineReady ? buildSql(filters) : null), [filters, engineReady]);
  const query = useQuery<Row>(sql, 'null rates by column');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const cells = useMemo(
    () =>
      rows.map((r) => ({
        service: String(r.service),
        serviceLabel: SERVICE_LABEL[String(r.service)] ?? String(r.service),
        period: r.period.slice(0, 7),
        column_name: String(r.column_name),
        null_share: Number(r.null_share),
        rows: Number(r.rows),
        null_rows: Number(r.null_rows),
      })),
    [rows],
  );

  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const periods = useMemo(() => [...new Set(cells.map((c) => c.period))].sort(), [cells]);
  const labelled = useMemo(() => {
    const januaries = periods.filter((p) => p.endsWith('-01'));
    const first = periods[0];
    // The first period earns a label when it is not already a January and is far
    // enough from the next one to print without colliding.
    if (first && !first.endsWith('-01') && (januaries.length === 0 || januaries[0] !== periods[1])) {
      return [first, ...januaries];
    }
    return januaries.length > 0 ? januaries : periods;
  }, [periods]);
  const columns = useMemo(() => [...new Set(cells.map((c) => c.column_name))].sort(), [cells]);
  const services = useMemo(
    () => SERVICE_ORDER.filter((s) => cells.some((c) => c.service === s)).map((s) => SERVICE_LABEL[s] ?? s),
    [cells],
  );

  // A column that is null for a whole month and then drops is an introduction,
  // not a defect. Counting them gives the copy something concrete to point at.
  const introductions = useMemo(() => {
    const out: { service: string; column_name: string; period: string }[] = [];
    for (const service of new Set(cells.map((c) => c.service))) {
      for (const column of columns) {
        const series = cells
          .filter((c) => c.service === service && c.column_name === column)
          .sort((a, b) => a.period.localeCompare(b.period));
        for (let i = 1; i < series.length; i += 1) {
          if (series[i - 1]!.null_share === 1 && series[i]!.null_share < 1) {
            out.push({ service, column_name: column, period: series[i]!.period });
          }
        }
      }
    }
    return out;
  }, [cells, columns]);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 680;
      const rowHeight = compact ? 16 : 20;
      return {
        width,
        height: services.length * (columns.length * rowHeight + 34) + 60,
        marginLeft: compact ? 108 : 150,
        marginRight: compact ? 10 : 90,
        marginTop: 30,
        marginBottom: 46,
        style: { background: 'transparent' },
        x: {
          domain: periods,
          label: null,
          // Forty two rotated month labels overlap into a solid block. Only
          // January of each year is labelled, plus the first period so the axis
          // says where the history starts; every other month keeps its tick.
          ticks: labelled,
          tickFormat: (p: string) => (p.endsWith('-01') ? p.slice(0, 4) : monthLabel(`${p}-01`)),
          axis: 'top' as const,
        },
        y: { domain: columns, label: null, tickSize: 0 },
        fy: { domain: services, label: null },
        color: { type: 'linear' as const, range: ramp, domain: [0, 1], label: 'Null share' },
        marks: [
          Plot.cell(cells, {
            x: 'period',
            y: 'column_name',
            fy: 'serviceLabel',
            fill: 'null_share',
            inset: 1,
            rx: 2,
            title: (d: { serviceLabel: string; column_name: string; period: string; null_share: number; null_rows: number; rows: number }) =>
              [
                `${d.serviceLabel}, ${d.column_name}`,
                monthLabel(`${d.period}-01`),
                `${percent(d.null_share, 2)} null`,
                `${d.null_rows.toLocaleString('en-US')} of ${d.rows.toLocaleString('en-US')} rows`,
              ].join('\n'),
            tip: true,
          }),
        ],
      };
    },
    [cells, periods, labelled, columns, services, ramp],
  );

  return (
    <ChartCard
      testId="chart-null-rates"
      title="Which columns were missing, and when?"
      subtitle="Null share per watched column and source period, one panel per service."
      legend={<RampLegend colors={ramp} low="0 percent null" high="100 percent null" caption="Null share" />}
      loading={query.loading}
      stale={query.loading && cells.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={300} ariaLabel="Heatmap of null share by column and source period, faceted by service" />}
      table={
        <DataTable
          rows={cells}
          columns={[
            { key: 'serviceLabel', label: 'Service' },
            { key: 'period', label: 'Source period', render: (c) => monthLabel(`${c.period}-01`) },
            { key: 'column_name', label: 'Column' },
            { key: 'null_share', label: 'Null share', align: 'right', render: (c) => percent(c.null_share, 3) },
            { key: 'null_rows', label: 'Null rows', align: 'right', render: (c) => c.null_rows.toLocaleString('en-US') },
            { key: 'rows', label: 'Rows', align: 'right', render: (c) => c.rows.toLocaleString('en-US') },
          ]}
          caption="Null share per column, service and source period."
          pageSize={20}
        />
      }
      footnote={
        <>
          <strong style={{ color: 'var(--text)' }}>
            A null share of exactly 1.0 followed by a drop is a column being introduced, not a data quality
            problem.
          </strong>{' '}
          congestion_surcharge appears in 2019, airport_fee in 2022, cbd_congestion_fee in 2025, and the
          high volume for hire vehicle schema has no passenger_count or ratecode_id at all, which is why
          those two rows are solid for that service.{' '}
          {introductions.length > 0
            ? `${introductions.length} such transitions fall inside this window: ${introductions
                .slice(0, 4)
                .map((i) => `${i.column_name} for ${SERVICE_LABEL[i.service] ?? i.service} at ${monthLabel(`${i.period}-01`)}`)
                .join('; ')}.`
            : 'No such transition falls inside this window.'}{' '}
          What should raise an alarm is a share that moves in the middle of a service&apos;s history,
          because that is a source that changed shape without announcing it.
        </>
      }
    />
  );
}
