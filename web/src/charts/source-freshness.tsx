'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { PlotFigure } from '@/components/plot-figure';
import { compactCount, monthLabel } from '@/lib/format';
import { useQuery } from '@/hooks/use-query';
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

/** Fixed for every row so the three frames line up under one shared x axis. */
const MARGIN_LEFT = 58;
const MARGIN_RIGHT = 108;

interface ServiceRowProps {
  service: string;
  points: Point[];
  gaps: Point[];
  domain: [Date, Date];
  color: string;
  showAxis: boolean;
}

/**
 * One service, on its own y axis. Three services whose monthly row counts differ
 * by a factor of three hundred cannot share a linear axis: on one frame the two
 * smaller ones are flat lines on the baseline and the largest is pressed into
 * the top margin, which is how a legend entry ends up with no visible line.
 * Separate frames is not a second axis on one chart, it is three charts.
 */
function ServiceRow({ service, points, gaps, domain, color, showAxis }: ServiceRowProps) {
  const spec = useCallback(
    (width: number) => ({
      width,
      height: showAxis ? 116 : 92,
      marginLeft: MARGIN_LEFT,
      marginRight: MARGIN_RIGHT,
      marginTop: 12,
      marginBottom: showAxis ? 34 : 10,
      style: { background: 'transparent' },
      x: { type: 'utc' as const, label: null, domain, axis: showAxis ? ('bottom' as const) : null },
      y: {
        label: null,
        grid: true,
        nice: true,
        zero: true,
        ticks: 3,
        tickFormat: (v: number) => compactCount(v),
      },
      marks: [
        Plot.areaY(points, {
          x: 'period',
          y: 'clean_rows',
          z: 'segment',
          fill: color,
          fillOpacity: 0.12,
        }),
        Plot.lineY(points, {
          x: 'period',
          y: 'clean_rows',
          // z is the segment, not the service, so the line stops at a gap.
          z: 'segment',
          stroke: color,
          strokeWidth: 2,
          strokeLinecap: 'round' as const,
        }),
        Plot.dot(points, {
          x: 'period',
          y: 'clean_rows',
          fill: color,
          r: 3.2,
          title: (d: Point) =>
            `${SERVICE_LABEL[d.service] ?? d.service}, ${monthLabel(d.period.toISOString())}\n${d.clean_rows.toLocaleString('en-US')} rows kept`,
          tip: true,
        }),
        gaps.length > 0
          ? Plot.ruleX(gaps, { x: 'period', stroke: 'var(--status-warn)', strokeDasharray: '3,3' })
          : null,
        // The name sits in the frame's right margin, so identity never depends on
        // matching a colour to a key somewhere else on the page.
        Plot.text([points[points.length - 1]].filter(Boolean) as Point[], {
          x: 'period',
          y: 'clean_rows',
          text: () => SERVICE_LABEL[service] ?? service,
          dx: 10,
          textAnchor: 'start' as const,
          fontSize: 11,
          fill: 'var(--text-muted)',
        }),
        Plot.ruleY([0], { stroke: 'var(--axis)' }),
        Plot.crosshairX(points, { x: 'period', y: 'clean_rows', color: 'var(--text-faint)' }),
      ].filter(Boolean) as Plot.Markish[],
    }),
    [points, gaps, domain, color, service, showAxis],
  );

  return (
    <PlotFigure
      spec={spec}
      height={showAxis ? 116 : 92}
      ariaLabel={`Rows kept per source period for ${SERVICE_LABEL[service] ?? service}`}
    />
  );
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
  const gaps = useMemo(() => points.filter((p) => p.has_gap), [points]);

  // One x domain across the frames, so a month sits at the same place in all of
  // them and the rows can be read down as well as across.
  const domain = useMemo<[Date, Date] | null>(() => {
    if (points.length === 0) return null;
    const times = points.map((p) => p.period.getTime());
    return [new Date(Math.min(...times)), new Date(Math.max(...times))];
  }, [points]);

  const backends = [...new Set(rows.map((r) => String(r.backend)))];
  const vintages = [...new Set(rows.map((r) => String(r.vintage)))];

  return (
    <ChartCard
      testId="chart-freshness"
      title="Did every month arrive, and how much of it survived?"
      subtitle={`Rows kept per source period, one frame per service because their volumes differ by a factor of three hundred. Backend reported as ${backends.join(', ') || 'unknown'}.`}
      loading={query.loading}
      stale={query.loading && points.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={
        domain === null ? (
          <p className="py-6 text-center text-xs" style={{ color: 'var(--text-faint)' }}>
            {query.loading ? 'Reading source freshness.' : 'No source period matches the current filters.'}
          </p>
        ) : (
          <div className="flex flex-col">
            {services.map((service, index) => (
              <ServiceRow
                key={service}
                service={service}
                points={points.filter((p) => p.service === service)}
                gaps={gaps.filter((p) => p.service === service)}
                domain={domain}
                color={serviceColor(theme, service)}
                showAxis={index === services.length - 1}
              />
            ))}
          </div>
        )
      }
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
          Each frame has its own axis and none of them is a second axis on another: for hire vehicles run
          about {compactCount(1_892_000)} rows a month against{' '}
          {compactCount(298_000)} for yellow and {compactCount(5_460)} for green, and on one linear scale
          the smaller two are a flat line on the baseline. The frames share an x domain, so a month is in
          the same place in all three.{' '}
          {gaps.length === 0 ? (
            <>
              No month in this window follows a gap, so every line is continuous. If one did, the line
              would stop and restart rather than slope across the hole: a segment drawn over a missing
              month asserts a number nobody read.
            </>
          ) : (
            <>
              {gaps.length} {gaps.length === 1 ? 'month follows' : 'months follow'} a gap, marked with an
              amber rule, and the line breaks there rather than sloping across the hole.
            </>
          )}{' '}
          The {vintages.length} schema vintages in the table are what the loader matched each file
          against, which is how a column that appears partway through the history is handled without a
          null rate alarm.
        </>
      }
    />
  );
}
