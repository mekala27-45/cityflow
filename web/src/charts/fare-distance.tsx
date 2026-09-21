'use client';

import * as Plot from '@observablehq/plot';
import { useCallback, useMemo, useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { PlotFigure } from '@/components/plot-figure';
import { useQuery } from '@/hooks/use-query';
import { compactCount } from '@/lib/format';
import { sequentialRamp, SERVICE_LABEL, SERVICE_ORDER, SERVICE_SHORT } from '@/lib/palette';
import type { Filters } from '@/lib/sql';
import { weightedPolyFit } from '@/lib/stats';

interface Row {
  hex_q: number;
  hex_r: number;
  trips: number;
  mean_distance_mi: number;
  mean_fare: number;
  flat_rate_trips: number;
}

// The 2024 JFK to Manhattan flat fare. It is a published tariff, not a number
// derived from this data, which is why it is a constant and not a query.
const JFK_FLAT_FARE = 70;

/**
 * One service at a time. Pooling the bins across services would mean averaging
 * each cell's mean distance and mean fare back together, and that is the metric
 * layer's arithmetic, not the page's: mean_distance_mi and mean_fare are catalog
 * metrics with their own definitions over the component sums, which this mart
 * does not carry. Reading one service's rows verbatim avoids the question.
 */
function buildSql(service: string): string {
  return `select hex_q::int as hex_q, hex_r::int as hex_r,
       trips, flat_rate_trips, mean_distance_mi, mean_fare
from 'agg_fare_distance.parquet'
where service = '${service.replace(/'/g, "''")}'
  and trips > 0
order by trips desc`;
}

/** Yellow first when it is selected: it is the only service with a flat rate code. */
function preferredService(filters: Filters): string {
  if (filters.services.includes('yellow')) return 'yellow';
  return SERVICE_ORDER.find((s) => filters.services.includes(s)) ?? 'yellow';
}

export function FareDistance() {
  const { filters, engineReady, theme } = useApp();
  const [chosen, setChosen] = useState<string | null>(null);
  const service = chosen && filters.services.includes(chosen) ? chosen : preferredService(filters);

  const sql = useMemo(() => (engineReady ? buildSql(service) : null), [service, engineReady]);
  const query = useQuery<Row>(sql, 'Fare against distance hexbin');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const cells = useMemo(
    () =>
      rows
        .map((r) => ({
          x: Number(r.mean_distance_mi),
          y: Number(r.mean_fare),
          trips: Number(r.trips),
          flat: Number(r.flat_rate_trips),
        }))
        .filter((c) => Number.isFinite(c.x) && Number.isFinite(c.y)),
    [rows],
  );

  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const maxTrips = cells.reduce((acc, c) => Math.max(acc, c.trips), 0);
  const maxDistance = cells.reduce((acc, c) => Math.max(acc, c.x), 0);
  const xMax = Math.min(30, maxDistance * 1.02) || 1;

  // A cubic through the cells, weighted by the trips in each, drawn only over the
  // range where there is enough volume to mean anything.
  const fit = useMemo(() => weightedPolyFit(cells.map((c) => ({ x: c.x, y: c.y, w: c.trips }))), [cells]);
  const fitPoints = useMemo(() => {
    if (!fit) return [];
    const heavy = [...cells].sort((a, b) => b.trips - a.trips).slice(0, Math.ceil(cells.length * 0.9));
    const upper = heavy.reduce((acc, c) => Math.max(acc, c.x), 0);
    const points: { x: number; y: number }[] = [];
    for (let i = 0; i <= 80; i += 1) {
      const x = (upper * i) / 80;
      points.push({ x, y: fit(x) });
    }
    return points.filter((p) => Number.isFinite(p.y) && p.y >= 0);
  }, [fit, cells]);

  const flatRateTrips = cells.reduce((acc, c) => acc + c.flat, 0);
  const flatNearBand = cells
    .filter((c) => Math.abs(c.y - JFK_FLAT_FARE) <= 1.5)
    .reduce((acc, c) => acc + c.flat, 0);

  const spec = useCallback(
    (width: number) => {
      const compact = width < 620;
      return {
        width,
        height: compact ? 320 : 400,
        marginLeft: 54,
        marginRight: 16,
        marginTop: 18,
        marginBottom: 40,
        style: { background: 'transparent' },
        x: { label: 'Mean trip distance in the bin, miles', grid: true, domain: [0, xMax] },
        y: { label: 'Mean fare in the bin, dollars', grid: true, domain: [0, 150] },
        color: { type: 'log' as const, range: ramp, domain: [1, Math.max(2, maxTrips)] },
        marks: [
          // The flat fare band, behind the data, so it reads as a rule the fares
          // obey rather than something drawn on top of them.
          Plot.rect([{ x1: 0, x2: xMax, y1: JFK_FLAT_FARE - 1, y2: JFK_FLAT_FARE + 1 }], {
            x1: 'x1',
            x2: 'x2',
            y1: 'y1',
            y2: 'y2',
            fill: 'var(--text-faint)',
            fillOpacity: 0.28,
          }),
          Plot.text([{ x: 1, y: JFK_FLAT_FARE + 4 }], {
            x: 'x',
            y: 'y',
            text: () => `JFK flat fare, $${JFK_FLAT_FARE}`,
            textAnchor: 'start' as const,
            fontSize: 10,
            fill: 'var(--text-muted)',
          }),
          Plot.dot(cells, {
            x: 'x',
            y: 'y',
            r: compact ? 3.4 : 4.4,
            symbol: 'hexagon' as const,
            fill: 'trips',
            stroke: 'var(--surface)',
            strokeWidth: 0.5,
            title: (d: { x: number; y: number; trips: number; flat: number }) =>
              [
                `${d.x.toFixed(2)} miles, $${d.y.toFixed(2)}`,
                `${d.trips.toLocaleString('en-US')} trips`,
                d.flat > 0 ? `${d.flat.toLocaleString('en-US')} on a flat rate` : 'no flat rate trips',
              ].join('\n'),
            tip: true,
          }),
          fitPoints.length > 0
            ? Plot.line(fitPoints, { x: 'x', y: 'y', stroke: 'var(--text)', strokeWidth: 2, strokeOpacity: 0.7 })
            : null,
        ].filter(Boolean) as Plot.Markish[],
      };
    },
    [cells, fitPoints, maxTrips, xMax, ramp],
  );

  return (
    <ChartCard
      testId="chart-fare-distance"
      title="What does distance buy, and where does the meter stop mattering?"
      subtitle={`${SERVICE_LABEL[service] ?? service}: hex bins of mean fare against mean distance, shaded by the trips inside the bin, with a volume weighted cubic through them.`}
      legend={<RampLegend colors={ramp} low="1 trip" high={compactCount(maxTrips)} caption="Trips in the bin, log scaled" />}
      controls={
        <div className="flex overflow-hidden rounded border text-xs" style={{ borderColor: 'var(--border)' }} role="group" aria-label="Service">
          {SERVICE_ORDER.filter((s) => filters.services.includes(s)).map((option) => (
            <button
              key={option}
              type="button"
              data-testid={`fare-service-${option}`}
              aria-pressed={option === service}
              onClick={() => setChosen(option)}
              className="px-2 py-1"
              style={{
                background: option === service ? 'var(--panel-raised)' : 'transparent',
                color: option === service ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              {SERVICE_SHORT[option] ?? option}
            </button>
          ))}
        </div>
      }
      loading={query.loading}
      stale={query.loading && cells.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={<PlotFigure spec={spec} height={340} ariaLabel="Hexbin of mean fare against mean distance with a fitted curve" />}
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'mean_distance_mi', label: 'Mean distance, miles', align: 'right', render: (r) => Number(r.mean_distance_mi).toFixed(2) },
            { key: 'mean_fare', label: 'Mean fare', align: 'right', render: (r) => `$${Number(r.mean_fare).toFixed(2)}` },
            { key: 'trips', label: 'Trips', align: 'right', render: (r) => Number(r.trips).toLocaleString('en-US') },
            { key: 'flat_rate_trips', label: 'Flat rate trips', align: 'right', render: (r) => Number(r.flat_rate_trips).toLocaleString('en-US') },
          ]}
          caption="One row per hex bin in the fare against distance plane."
          pageSize={20}
        />
      }
      footnote={
        <>
          The horizontal band at ${JFK_FLAT_FARE} is the JFK flat fare, and it is where the curve stops
          being a curve: {flatNearBand.toLocaleString('en-US')} of the{' '}
          {flatRateTrips.toLocaleString('en-US')} flat rate trips in this window sit inside one dollar of
          it, at every distance from eight miles to twenty. Newark, rate code three, does not draw a band,
          because it is not a flat fare: it is the meter plus a fixed surcharge, so its trips scatter along
          the same curve as everything else and only the intercept moves. The cubic is weighted by the
          trips in each bin and drawn over the distance range that holds ninety percent of the bins;
          beyond that the bins hold single trips and a fit through them would be a drawing, not a model.
          agg_fare_distance is keyed by service, so the date and day type filters do not reach this chart,
          and one service is drawn at a time: the mart holds a mean per cell, not the component sums, so
          pooling services here would mean averaging averages.
        </>
      }
    />
  );
}
