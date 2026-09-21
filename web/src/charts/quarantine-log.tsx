'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { monthLabel, percent } from '@/lib/format';
import { seriesColor, SERVICE_LABEL } from '@/lib/palette';
import { monthWindow, servicePredicate, type Filters } from '@/lib/sql';
import { wilson } from '@/lib/stats';

interface Row {
  rule: string;
  service: string;
  period: string;
  rows: number;
  source_rows: number;
  share_of_source: number;
}

interface RuleSummary {
  rule: string;
  rows: number;
  source_rows: number;
  share: number;
  lower: number;
  upper: number;
}

function buildSql(filters: Filters): string {
  return `select rule, service, source_period::varchar as period,
       rows, source_rows, share_of_source
from 'mart_quarantine.parquet'
where ${monthWindow(filters, 'source_period')}
  and ${servicePredicate(filters)}
order by source_period, service, rule`;
}

export function QuarantineLog() {
  const { filters, engineReady, theme } = useApp();

  const sql = useMemo(() => (engineReady ? buildSql(filters) : null), [filters, engineReady]);
  const query = useQuery<Row>(sql, 'quarantine rules');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const summary = useMemo<RuleSummary[]>(() => {
    const byRule = new Map<string, { rows: number; source: number }>();
    for (const row of rows) {
      const current = byRule.get(row.rule) ?? { rows: 0, source: 0 };
      current.rows += Number(row.rows);
      current.source += Number(row.source_rows);
      byRule.set(row.rule, current);
    }
    return [...byRule.entries()]
      .map(([rule, totals]) => {
        // A share of rows caught out of rows read is a proportion of counts, so
        // it gets an interval rather than a bare percentage, even though the
        // metric layer does not own this one.
        const interval = wilson(totals.rows, totals.source);
        return {
          rule,
          rows: totals.rows,
          source_rows: totals.source,
          share: interval?.point ?? 0,
          lower: interval?.lower ?? 0,
          upper: interval?.upper ?? 0,
        };
      })
      .sort((a, b) => b.share - a.share);
  }, [rows]);

  const totalCaught = summary.reduce((acc, r) => acc + r.rows, 0);
  const sourceRows = rows.length > 0 ? Math.max(...summary.map((r) => r.source_rows)) : 0;
  const color = seriesColor(theme, 1);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: Math.max(180, summary.length * (compact ? 24 : 30) + 54),
        marginLeft: compact ? 140 : 190,
        marginRight: 20,
        marginTop: 14,
        marginBottom: 38,
        style: { background: 'transparent' },
        x: {
          label: 'Share of source rows caught by the rule',
          grid: true,
          tickFormat: (v: number) => `${(v * 100).toFixed(2)}%`,
        },
        y: { domain: summary.map((r) => r.rule), label: null, tickSize: 0 },
        marks: [
          Plot.barX(summary, {
            y: 'rule',
            x: 'share',
            fill: color,
            // Rounded ends on the free side, anchored square to the baseline.
            rx: 2,
            insetTop: 2,
            insetBottom: 2,
            title: (d: RuleSummary) =>
              [
                d.rule,
                `${percent(d.share, 3)} of source rows`,
                `95 percent interval ${percent(d.lower, 3)} to ${percent(d.upper, 3)}`,
                `${d.rows.toLocaleString('en-US')} rows of ${d.source_rows.toLocaleString('en-US')}`,
              ].join('\n'),
            tip: true,
          }),
          Plot.ruleY(summary, { y: 'rule', x1: 'lower', x2: 'upper', stroke: 'var(--text-faint)', strokeWidth: 1.5 }),
          Plot.ruleX([0], { stroke: 'var(--axis)' }),
        ],
      };
    },
    [summary, color],
  );

  return (
    <ChartCard
      testId="chart-quarantine"
      title="What did the ingest throw away, and why?"
      subtitle={
        sourceRows > 0
          ? `${totalCaught.toLocaleString('en-US')} rows caught across ${summary.length} rules, out of ${sourceRows.toLocaleString('en-US')} read.`
          : 'Rows removed by each quarantine rule, as a share of the rows the source held.'
      }
      loading={query.loading}
      stale={query.loading && summary.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={220} ariaLabel="Share of source rows caught by each quarantine rule" />}
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'period', label: 'Source period', render: (r) => monthLabel(r.period) },
            { key: 'service', label: 'Service', render: (r) => SERVICE_LABEL[r.service] ?? r.service },
            { key: 'rule', label: 'Rule' },
            { key: 'rows', label: 'Rows caught', align: 'right', render: (r) => Number(r.rows).toLocaleString('en-US') },
            { key: 'source_rows', label: 'Source rows', align: 'right', render: (r) => Number(r.source_rows).toLocaleString('en-US') },
            { key: 'share_of_source', label: 'Share', align: 'right', render: (r) => percent(Number(r.share_of_source), 4) },
          ]}
          caption="Every rule, service and source period in the window."
          pageSize={20}
        />
      }
      footnote={
        <>
          The share is the comparable number: a rule that catches two thousand rows in a busy month and
          two hundred in a quiet one has not changed behaviour. The interval is a Wilson score on rows
          caught out of rows read, which is what makes a rule with a handful of catches distinguishable
          from one with none. Quarantined rows are not in any figure on the other panels: they were
          removed before the fact table, which is why the totals here and the totals there differ by
          exactly this much.
        </>
      }
    />
  );
}
