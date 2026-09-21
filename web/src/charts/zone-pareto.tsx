'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { percent } from '@/lib/format';
import { seriesColor } from '@/lib/palette';
import { buildZoneRankSql, type ZoneRankRow } from './zone-queries';

interface ParetoPoint extends ZoneRankRow {
  rank: number;
  share: number;
  cumulative: number;
}

const SHOWN = 40;

/** Zone names run to forty characters; a rotated tick that long swallows the
 *  chart. The table view carries the full name. */
function shortZone(zone: string): string {
  return zone.length > 22 ? `${zone.slice(0, 21)}...` : zone;
}

export function ZonePareto() {
  const { catalog, filters, bootstrap, engineReady, theme } = useApp();

  const sql = useMemo(
    () => (catalog && engineReady ? buildZoneRankSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<ZoneRankRow>(sql, 'zones by trips');

  const fallback = useMemo<ZoneRankRow[]>(
    () => bootstrap?.top_zones.map((z) => ({ zone_id: z.zone_id, zone: z.zone, borough: z.borough, trips: z.trips })) ?? [],
    [bootstrap],
  );
  const rows = query.rows ?? fallback;

  // Share and cumulative share are compositions of the trips metric, not metrics
  // in their own right, so they are derived here from catalog computed totals.
  const points = useMemo<ParetoPoint[]>(() => {
    const total = rows.reduce((acc, r) => acc + Number(r.trips), 0);
    if (total <= 0) return [];
    let running = 0;
    return rows.map((row, index) => {
      const share = Number(row.trips) / total;
      running += share;
      return { ...row, rank: index + 1, share, cumulative: running };
    });
  }, [rows]);

  const half = points.find((p) => p.cumulative >= 0.5) ?? null;
  const eighty = points.find((p) => p.cumulative >= 0.8) ?? null;
  const shown = points.slice(0, SHOWN);

  const barColor = seriesColor(theme, 1);
  const lineColor = seriesColor(theme, 2);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: compact ? 280 : 360,
        marginLeft: 46,
        marginRight: 14,
        marginTop: 20,
        marginBottom: compact ? 120 : 150,
        style: { background: 'transparent' },
        x: {
          domain: shown.map((p) => p.zone),
          label: null,
          tickRotate: -55,
          tickSize: 0,
          tickFormat: shortZone,
        },
        // One scale. Both marks are a share of the same denominator, which is why
        // a Pareto does not need, and here does not get, a second axis.
        y: {
          label: 'Share of trips in the window',
          domain: [0, 1],
          grid: true,
          tickFormat: (v: number) => `${Math.round(v * 100)}%`,
        },
        marks: [
          Plot.ruleY([0.5], { stroke: 'var(--axis)', strokeDasharray: '4,4' }),
          Plot.ruleY([0.8], { stroke: 'var(--axis)', strokeDasharray: '4,4' }),
          Plot.barY(shown, {
            x: 'zone',
            y: 'share',
            fill: barColor,
            // Rounded ends anchored to the baseline, with a surface gap between bars.
            rx: 2,
            insetLeft: 1,
            insetRight: 1,
            title: (d: ParetoPoint) => `${d.zone}, ${d.borough}\n${percent(d.share, 2)} of trips\nrank ${d.rank}`,
            tip: true,
          }),
          Plot.lineY(shown, { x: 'zone', y: 'cumulative', stroke: lineColor, strokeWidth: 2, curve: 'monotone-x' as const }),
          Plot.text([{ zone: shown[Math.min(3, shown.length - 1)]?.zone ?? '', y: 0.53, label: 'half of all trips' }], {
            x: 'zone',
            y: 'y',
            text: 'label',
            fontSize: 10,
            fill: 'var(--text-faint)',
            textAnchor: 'start' as const,
          }),
          Plot.ruleY([0], { stroke: 'var(--axis)' }),
        ],
      };
    },
    [shown, barColor, lineColor],
  );

  return (
    <ChartCard
      testId="chart-zone-pareto"
      title="How concentrated are pickups?"
      subtitle={
        half && eighty
          ? `${half.rank} zones carry half the trips, ${eighty.rank} carry four fifths, out of ${points.length} with any volume.`
          : 'Zones ordered by volume, with the running total.'
      }
      legend={
        <Legend
          items={[
            { label: 'Share of trips in the zone', color: barColor },
            { label: 'Cumulative share', color: lineColor },
          ]}
        />
      }
      loading={query.loading}
      stale={query.loading && points.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={300} ariaLabel="Pareto chart of pickup zones by share of trips" />}
      table={
        <DataTable
          rows={points}
          columns={[
            { key: 'rank', label: 'Rank', align: 'right' },
            { key: 'zone', label: 'Zone' },
            { key: 'borough', label: 'Borough' },
            { key: 'trips', label: 'Trips', align: 'right', render: (p) => Number(p.trips).toLocaleString('en-US') },
            { key: 'share', label: 'Share', align: 'right', render: (p) => percent(p.share, 3) },
            { key: 'cumulative', label: 'Cumulative', align: 'right', render: (p) => percent(p.cumulative, 2) },
          ]}
          caption="Every zone with volume in the window, ordered by trips."
          pageSize={20}
        />
      }
      footnote={
        <>
          The first {SHOWN} zones are drawn; the table has all {points.length}. Both marks are shares of the
          same total, so they sit on one axis: a Pareto with a second axis is two charts pretending to be
          one, and the reader has no way to know which gridline belongs to which mark. Unknown locations are
          excluded here as well, for the same reason they are excluded from the map, so the denominator is
          trips with a known pickup zone.
        </>
      }
    />
  );
}
