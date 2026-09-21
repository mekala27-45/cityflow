'use client';

import {
  Popup,
  type ExpressionSpecification,
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapGeoJSONFeature,
} from 'maplibre-gl';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { MapCanvas } from '@/components/map-canvas';
import { useQuery } from '@/hooks/use-query';
import { useZoneGeometry } from '@/hooks/use-zone-geometry';
import { compactCount } from '@/lib/format';
import { sequentialRamp } from '@/lib/palette';
import { boroughPredicate, monthWindow, servicePredicate, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface FlowRow {
  pu_zone_id: number;
  do_zone_id: number;
  pu_zone: string;
  do_zone: string;
  pu_borough: string;
  do_borough: string;
  pu_lon: number;
  pu_lat: number;
  do_lon: number;
  do_lat: number;
  trips: number;
}

const FLOW_COUNTS = [50, 150, 400] as const;

function buildSql(catalog: MetricCatalog, filters: Filters, limit: number): string {
  // agg_od_flow carries no day type, so that filter cannot reach this chart.
  // Unknown endpoints are already absent from the mart, which is why the totals
  // here sit below the citywide totals on the other panels.
  return `with agg as (
  select f.trips,
         f.pu_zone_id, f.do_zone_id,
         pz.zone as pu_zone, dz.zone as do_zone,
         pz.borough as pu_borough, dz.borough as do_borough,
         pz.centroid_lon as pu_lon, pz.centroid_lat as pu_lat,
         dz.centroid_lon as do_lon, dz.centroid_lat as do_lat
  from 'agg_od_flow.parquet' f
  join 'dim_zone.parquet' pz on pz.zone_id = f.pu_zone_id
  join 'dim_zone.parquet' dz on dz.zone_id = f.do_zone_id
  where ${monthWindow(filters, 'f.month')}
    and ${servicePredicate(filters, 'f.service')}
    and not pz.is_unknown and not dz.is_unknown
    and pz.has_geometry and dz.has_geometry
    and ${boroughPredicate(filters, 'pz.borough')}
)
select pu_zone_id::int as pu_zone_id, do_zone_id::int as do_zone_id,
       any_value(pu_zone) as pu_zone, any_value(do_zone) as do_zone,
       any_value(pu_borough) as pu_borough, any_value(do_borough) as do_borough,
       any_value(pu_lon) as pu_lon, any_value(pu_lat) as pu_lat,
       any_value(do_lon) as do_lon, any_value(do_lat) as do_lat,
       ${catalog.projection('trips')}
from agg
group by 1, 2
order by trips desc
limit ${limit}`;
}

/**
 * A quadratic bezier offset perpendicular to the chord. Straight lines between
 * centroids overlap into a hairball; a consistent bow lets a reader separate the
 * two directions of the same pair, which for commuting flows is the interesting
 * part.
 */
function arc(from: [number, number], to: [number, number], bow = 0.14): [number, number][] {
  const [x1, y1] = from;
  const [x2, y2] = to;
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const cx = mx - dy * bow;
  const cy = my + dx * bow;
  const points: [number, number][] = [];
  const steps = 24;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const u = 1 - t;
    points.push([u * u * x1 + 2 * u * t * cx + t * t * x2, u * u * y1 + 2 * u * t * cy + t * t * y2]);
  }
  return points;
}

type FlowCollection = GeoJSON.FeatureCollection<GeoJSON.LineString, { label: string; trips: number; weight: number }>;

export function OdFlows() {
  const { catalog, filters, engineReady, theme } = useApp();
  const geo = useZoneGeometry();
  const [limit, setLimit] = useState<number>(150);
  const mapRef = useRef<MapLibreMap | null>(null);
  const popupRef = useRef<Popup | null>(null);

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters, limit) : null),
    [catalog, filters, engineReady, limit],
  );
  const query = useQuery<FlowRow>(sql, `top ${limit} origin destination flows`);
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const max = rows.reduce((acc, r) => Math.max(acc, Number(r.trips)), 0);

  const collection = useMemo<FlowCollection>(() => {
    return {
      type: 'FeatureCollection',
      features: rows
        .filter((r) => Number.isFinite(Number(r.pu_lon)) && Number.isFinite(Number(r.do_lon)))
        .map((r) => {
          const trips = Number(r.trips);
          return {
            type: 'Feature' as const,
            geometry: {
              type: 'LineString' as const,
              coordinates:
                Number(r.pu_zone_id) === Number(r.do_zone_id)
                  ? // A trip that starts and ends in one zone has no chord, so it
                    // gets a small closed loop rather than a zero length line.
                    arc([Number(r.pu_lon) - 0.005, Number(r.pu_lat)], [Number(r.pu_lon) + 0.005, Number(r.pu_lat)], 1.1)
                  : arc([Number(r.pu_lon), Number(r.pu_lat)], [Number(r.do_lon), Number(r.do_lat)]),
            },
            properties: {
              label: `${r.pu_zone} to ${r.do_zone}`,
              trips,
              weight: max > 0 ? trips / max : 0,
            },
          };
        }),
    };
  }, [rows, max]);

  const paint = useCallback(
    (map: MapLibreMap) => {
      // Arcs with nothing under them are a tangle of lines in the dark. The zone
      // outlines are the base: recessive, one flat tone, no data encoded in them,
      // so they orient the reader without competing with the flows.
      if (geo && !map.getSource('zones-base')) {
        // MapLibre validates paint values, so the CSS tokens are resolved before
        // the layer is declared rather than patched in afterwards: a var() string
        // is rejected and the layer is never created at all.
        const tokens = getComputedStyle(document.documentElement);
        const land = tokens.getPropertyValue('--panel-raised').trim() || '#161D26';
        const gap = tokens.getPropertyValue('--surface').trim() || '#0B0F14';
        // Geometry can arrive after the first result set, so the base is inserted
        // beneath the flow layer explicitly rather than appended, which would put
        // the land on top of the arcs.
        const below = map.getLayer('flow-lines') ? 'flow-lines' : undefined;
        map.addSource('zones-base', { type: 'geojson', data: geo });
        map.addLayer(
          {
            id: 'zones-base-fill',
            type: 'fill',
            source: 'zones-base',
            paint: { 'fill-color': land, 'fill-opacity': 1 },
          },
          below,
        );
        map.addLayer(
          {
            id: 'zones-base-line',
            type: 'line',
            source: 'zones-base',
            paint: { 'line-color': gap, 'line-width': 0.6, 'line-opacity': 0.8 },
          },
          below,
        );
      }

      const source = map.getSource('flows');
      if (source && 'setData' in source) {
        (source as GeoJSONSource).setData(collection);
        return;
      }
      map.addSource('flows', { type: 'geojson', data: collection });
      map.addLayer({
        id: 'flow-lines',
        type: 'line',
        source: 'flows',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-width': ['interpolate', ['linear'], ['get', 'weight'], 0, 0.6, 0.25, 1.6, 1, 5],
          'line-color': [
            'interpolate',
            ['linear'],
            ['get', 'weight'],
            ...ramp.flatMap((color, step) => [step / (ramp.length - 1), color]),
          ] as ExpressionSpecification,
          'line-opacity': 0.75,
        },
      });

      map.on('mousemove', 'flow-lines', (event) => {
        const feature = event.features?.[0] as MapGeoJSONFeature | undefined;
        if (!feature) return;
        map.getCanvas().style.cursor = 'pointer';
        const props = feature.properties as { label: string; trips: number };
        const popup =
          popupRef.current ?? (popupRef.current = new Popup({ closeButton: false, closeOnClick: false, offset: 6 }));
        popup
          .setLngLat(event.lngLat)
          .setHTML(`<strong>${props.label}</strong><br>${Number(props.trips).toLocaleString('en-US')} trips`)
          .addTo(map);
      });
      map.on('mouseleave', 'flow-lines', () => {
        map.getCanvas().style.cursor = '';
        popupRef.current?.remove();
      });
    },
    [collection, geo, ramp],
  );

  const onReady = useCallback(
    (map: MapLibreMap) => {
      mapRef.current = map;
      paint(map);
    },
    [paint],
  );

  useEffect(() => {
    const map = mapRef.current;
    if (map && map.isStyleLoaded()) paint(map);
  }, [paint]);

  const drawnTrips = rows.reduce((acc, r) => acc + Number(r.trips), 0);

  return (
    <ChartCard
      testId="chart-od-flows"
      title="Which pairs of zones move the most people?"
      subtitle={`The ${limit} heaviest zone pairs, drawn as arcs between zone centroids and weighted by volume.`}
      legend={<RampLegend colors={ramp} low="fewer trips" high={compactCount(max)} caption="Trips on the pair" />}
      controls={
        <div className="flex overflow-hidden rounded border text-xs" style={{ borderColor: 'var(--border)' }} role="group" aria-label="Flows drawn">
          {FLOW_COUNTS.map((count) => (
            <button
              key={count}
              type="button"
              data-testid={`od-limit-${count}`}
              aria-pressed={limit === count}
              onClick={() => setLimit(count)}
              className="px-2 py-1"
              style={{
                background: limit === count ? 'var(--panel-raised)' : 'transparent',
                color: limit === count ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              {count}
            </button>
          ))}
        </div>
      }
      loading={query.loading}
      stale={query.loading && rows.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={
        <MapCanvas
          testId="map-flows"
          height={420}
          ariaLabel="Arcs between zone centroids weighted by trips"
          onReady={onReady}
          overlay={
            <div
              className="pointer-events-none absolute bottom-2 left-2 max-w-[19rem] rounded border px-2 py-1.5 text-[11px] leading-snug"
              style={{ background: 'var(--panel)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
            >
              The arcs carry {compactCount(drawnTrips)} trips between them. An arc bows to the left of its own
              direction, so a pair that runs both ways draws two curves rather than one line.
            </div>
          }
        />
      }
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'pu_zone', label: 'From' },
            { key: 'pu_borough', label: 'From borough' },
            { key: 'do_zone', label: 'To' },
            { key: 'do_borough', label: 'To borough' },
            { key: 'trips', label: 'Trips', align: 'right', render: (r) => Number(r.trips).toLocaleString('en-US') },
          ]}
          caption={`The ${limit} heaviest zone pairs in the window.`}
        />
      }
      footnote={
        <>
          A centroid is not where a trip started, it is the middle of the zone, so an arc is a claim about
          which pair of zones moved people, not about a route. agg_od_flow holds no day type, so that filter
          does not reach this chart; the date, service and borough filters do, with borough applied to the
          origin. Trips whose origin or destination was never recorded are absent from this mart entirely,
          which is why its totals sit below the citywide totals in panel one.
        </>
      }
    />
  );
}
