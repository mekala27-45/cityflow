'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo, useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { percent } from '@/lib/format';
import { serviceColor, SERVICE_LABEL, SERVICE_SHORT, STATUS } from '@/lib/palette';
import { boroughPredicate, servicePredicate, type Filters } from '@/lib/sql';

interface ComparisonRow {
  service: string;
  zone_id: number;
  zone: string;
  borough: string;
  tipped: number;
  observable: number;
  tipped_share: number;
  ci_lower: number;
  ci_upper: number;
  service_rate: number;
  p_value: number;
  q_value: number;
  is_significant: boolean;
}

const Q_THRESHOLD = 0.05;

function buildSql(filters: Filters): string {
  // zone_comparisons is a mart, already one row per zone and service with the
  // interval and the corrected p value computed in the warehouse. Nothing is
  // recomputed here; the date and day type filters do not reach it, which the
  // footnote says.
  return `select service, zone_id::int as zone_id, zone, borough,
       tipped, observable, tipped_share, ci_lower, ci_upper,
       service_rate, p_value, q_value, is_significant
from 'zone_comparisons.parquet'
where ${servicePredicate(filters)}
  and ${boroughPredicate(filters)}
order by tipped_share desc`;
}

export function ZoneComparisons() {
  const { filters, engineReady, theme } = useApp();
  const [service, setService] = useState<string>('fhvhv');

  const sql = useMemo(() => (engineReady ? buildSql(filters) : null), [filters, engineReady]);
  const query = useQuery<ComparisonRow>(sql, 'zone tipping comparisons');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const services = useMemo(() => [...new Set(rows.map((r) => r.service))].sort(), [rows]);
  const active = services.includes(service) ? service : (services[0] ?? service);

  const forService = useMemo(() => rows.filter((r) => r.service === active), [rows, active]);
  const significant = forService.filter((r) => r.is_significant).length;
  const rawBelow = forService.filter((r) => Number(r.p_value) <= Q_THRESHOLD).length;
  const serviceRate = forService[0] ? Number(forService[0].service_rate) : null;

  // Twelve either side: a ranking chart that draws 260 rows is a texture, not a
  // ranking. The table view carries every row.
  const shown = useMemo(() => {
    const sorted = [...forService].sort((a, b) => Number(b.tipped_share) - Number(a.tipped_share));
    if (sorted.length <= 24) return sorted;
    return [...sorted.slice(0, 12), ...sorted.slice(-12)];
  }, [forService]);

  const color = serviceColor(theme, active);
  const survivorColor = STATUS[theme].ok;

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: Math.max(220, shown.length * (compact ? 15 : 18) + 56),
        marginLeft: compact ? 118 : 178,
        marginRight: 18,
        marginTop: 18,
        marginBottom: 34,
        style: { background: 'transparent' },
        x: {
          label: 'Share of observable trips that were tipped',
          grid: true,
          tickFormat: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
        y: {
          domain: shown.map((r) => r.zone),
          label: null,
          tickSize: 0,
          // Several zone names run past thirty characters and would be cut off by
          // the margin; the table view has them in full.
          tickFormat: (zone: string) => (zone.length > 26 ? `${zone.slice(0, 25)}...` : zone),
        },
        marks: [
          serviceRate === null
            ? null
            : Plot.ruleX([serviceRate], { stroke: 'var(--text-faint)', strokeDasharray: '4,4' }),
          Plot.ruleY(shown, {
            y: 'zone',
            x1: 'ci_lower',
            x2: 'ci_upper',
            stroke: color,
            strokeOpacity: 0.45,
            strokeWidth: 2,
          }),
          Plot.dot(shown, {
            y: 'zone',
            x: 'tipped_share',
            r: 4.5,
            fill: color,
            // Significance is a state, so it wears a status colour, never a series one.
            stroke: (d: ComparisonRow) => (d.is_significant ? survivorColor : 'transparent'),
            strokeWidth: 2,
            title: (d: ComparisonRow) =>
              [
                `${d.zone}, ${d.borough}`,
                `${percent(Number(d.tipped_share), 1)} tipped`,
                `95 percent Wilson interval ${percent(Number(d.ci_lower), 1)} to ${percent(Number(d.ci_upper), 1)}`,
                `${Number(d.tipped).toLocaleString('en-US')} of ${Number(d.observable).toLocaleString('en-US')} observable trips`,
                `p ${Number(d.p_value).toFixed(4)}, q ${Number(d.q_value).toFixed(3)}`,
              ].join('\n'),
            tip: true,
          }),
        ].filter(Boolean) as Plot.Markish[],
      };
    },
    [shown, color, survivorColor, serviceRate],
  );

  return (
    <ChartCard
      testId="chart-zone-comparisons"
      title="Does any zone tip differently from its service?"
      subtitle={
        serviceRate === null
          ? 'Zone tipping rates against the service rate, with Wilson intervals.'
          : `${SERVICE_LABEL[active] ?? active} tips on ${percent(serviceRate, 1)} of observable trips. The dashed rule is that rate.`
      }
      legend={
        <Legend
          items={[
            { label: `${SERVICE_LABEL[active] ?? active} zone rate with 95 percent Wilson interval`, color },
            { label: 'Survives the correction', color: survivorColor },
          ]}
        />
      }
      controls={
        <div className="flex overflow-hidden rounded border text-xs" style={{ borderColor: 'var(--border)' }} role="group" aria-label="Service">
          {services.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={option === active}
              onClick={() => setService(option)}
              className="px-2 py-1"
              style={{
                background: option === active ? 'var(--panel-raised)' : 'transparent',
                color: option === active ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              {SERVICE_SHORT[option] ?? option}
            </button>
          ))}
        </div>
      }
      loading={query.loading}
      stale={query.loading && shown.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={320} ariaLabel="Zone tipping rates with Wilson intervals against the service rate" />}
      table={
        <DataTable
          rows={forService}
          columns={[
            { key: 'zone', label: 'Zone' },
            { key: 'borough', label: 'Borough' },
            { key: 'tipped_share', label: 'Tipped share', align: 'right', render: (r) => percent(Number(r.tipped_share), 2) },
            { key: 'ci_lower', label: 'Lower', align: 'right', render: (r) => percent(Number(r.ci_lower), 2) },
            { key: 'ci_upper', label: 'Upper', align: 'right', render: (r) => percent(Number(r.ci_upper), 2) },
            { key: 'observable', label: 'Observable trips', align: 'right', render: (r) => Number(r.observable).toLocaleString('en-US') },
            { key: 'p_value', label: 'p', align: 'right', render: (r) => Number(r.p_value).toFixed(4) },
            { key: 'q_value', label: 'q', align: 'right', render: (r) => Number(r.q_value).toFixed(3) },
            { key: 'is_significant', label: 'Survives', render: (r) => (r.is_significant ? 'yes' : 'no') },
          ]}
          caption="Every zone compared against its own service rate."
          pageSize={20}
        />
      }
      footnote={
        <>
          {forService.length} zones are compared against the {SERVICE_LABEL[active] ?? active} rate, and every
          one of those comparisons is a chance to find a difference that is not there. The q values are
          Benjamini Hochberg adjusted and the threshold is {Q_THRESHOLD}: without that correction, testing
          {' '}{forService.length} zones at five percent would be expected to flag about{' '}
          {Math.round(forService.length * Q_THRESHOLD)} zones by chance alone.{' '}
          {rawBelow > 0 && significant === 0 ? (
            <strong style={{ color: 'var(--text)' }}>
              {rawBelow} {rawBelow === 1 ? 'zone has' : 'zones have'} a raw p value at or below {Q_THRESHOLD} and
              none survive the correction, so nothing here is marked.
            </strong>
          ) : significant === 0 ? (
            <strong style={{ color: 'var(--text)' }}>No zone survives the correction, so nothing is marked.</strong>
          ) : (
            <strong style={{ color: 'var(--text)' }}>
              {significant} of {forService.length} survive and carry the outline.
            </strong>
          )}{' '}
          Tipping is measured only where a tip is observable, which is card payments: a cash trip records no
          tip, and counting those as zero tips would invent a difference between zones that differ in how
          people pay. The date and day type filters do not reach this chart, because zone_comparisons is
          computed once over the whole source window; the service and borough filters do.
        </>
      }
    />
  );
}
