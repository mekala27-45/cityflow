'use client';

import {
  Popup,
  type ExpressionSpecification,
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapGeoJSONFeature,
} from 'maplibre-gl';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { RampLegend } from '@/components/legend';
import { MapCanvas } from '@/components/map-canvas';
import { useQuery } from '@/hooks/use-query';
import { useZoneGeometry, type ZoneCollection } from '@/hooks/use-zone-geometry';
import { compactCount, percent } from '@/lib/format';
import { sequentialRamp } from '@/lib/palette';
import { zoneHourAgg, type Filters } from '@/lib/sql';
import { buildZoneRankSql, type ZoneRankRow } from './zone-queries';
import type { MetricCatalog } from '@/lib/metrics';

interface ExclusionRow {
  unknown_zone_share: number;
  unknown_trips: number;
  all_trips: number;
  placeable_zones: number;
  zones_without_geometry: number;
}

/**
 * The excluded share is read through the catalog's own unknown_zone_share
 * definition, not counted again here, so the figure on the map is the same one
 * the metric layer would publish.
 */
function buildExclusionSql(catalog: MetricCatalog, filters: Filters): string {
  const metric = catalog.get('unknown_zone_share');
  return `with ${zoneHourAgg(filters, { excludeUnknown: false, columns: ['month', 'service', 'pu_zone_id', 'day_type', 'trips', 'unknown_zone_trips'] })}
select ${catalog.projection('unknown_zone_share')},
       sum(${metric.numerator}) as unknown_trips,
       sum(${metric.denominator}) as all_trips,
       (select count(*) from 'dim_zone.parquet' where not is_unknown) as placeable_zones,
       (select count(*) from 'dim_zone.parquet' where not is_unknown and not has_geometry) as zones_without_geometry
from agg`;
}

export function ZoneChoropleth() {
  const { catalog, filters, bootstrap, engineReady, theme } = useApp();
  const geo = useZoneGeometry();
  const mapRef = useRef<MapLibreMap | null>(null);
  const popupRef = useRef<Popup | null>(null);

  const zoneSql = useMemo(
    () => (catalog && engineReady ? buildZoneRankSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const exclusionSql = useMemo(
    () => (catalog && engineReady ? buildExclusionSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );

  const zones = useQuery<ZoneRankRow>(zoneSql, 'Choropleth, trips by zone');
  const exclusion = useQuery<ExclusionRow>(exclusionSql, 'unknown zone share');

  const fallback = useMemo<ZoneRankRow[]>(
    () => bootstrap?.top_zones.map((z) => ({ zone_id: z.zone_id, zone: z.zone, borough: z.borough, trips: z.trips })) ?? [],
    [bootstrap],
  );
  const rows = zones.rows ?? fallback;

  const ramp = useMemo(() => sequentialRamp(theme), [theme]);
  const max = rows.reduce((acc, r) => Math.max(acc, Number(r.trips)), 0);

  // Five interior edges cut the zones into six bins of roughly equal count.
  const breaks = useMemo(() => {
    const values = rows.map((r) => Number(r.trips)).filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
    if (values.length < ramp.length) return [];
    const edges: number[] = [];
    for (let i = 1; i < ramp.length; i += 1) {
      const at = Math.floor((values.length * i) / ramp.length);
      const value = values[Math.min(values.length - 1, at)] ?? 0;
      if (edges.length === 0 || value > edges[edges.length - 1]!) edges.push(value);
    }
    return edges;
  }, [rows, ramp.length]);

  const paint = useCallback(
    (map: MapLibreMap) => {
      if (!geo) return;
      const byZone = new Map(rows.map((r) => [Number(r.zone_id), Number(r.trips)]));
      const merged: ZoneCollection = {
        type: 'FeatureCollection',
        features: geo.features.map((feature) => ({
          ...feature,
          properties: { ...feature.properties, trips: byZone.get(Number(feature.properties.zone_id)) ?? 0 },
        })),
      };

      // Six steps of one hue, cut at sextiles rather than at equal intervals. Trip
      // volume by zone is a power law: East Williamsburg runs forty times the
      // median zone, so an equal interval ramp puts every zone but a handful in
      // the darkest bin and the map says nothing. The bin edges are printed in the
      // legend, so the reader is never left guessing what a shade is worth.
      const fillColor: ExpressionSpecification = [
        'step',
        ['get', 'trips'],
        ramp[0],
        ...breaks.flatMap((edge, index) => [edge, ramp[index + 1] ?? ramp[ramp.length - 1]]),
      ] as ExpressionSpecification;

      const source = map.getSource('zones');
      if (source && 'setData' in source) {
        (source as GeoJSONSource).setData(merged);
        // The expression has to be reapplied, not just the data: the edges move
        // with the filter, and a ramp frozen at layer creation is a ramp built
        // from whatever the first, often empty, result set implied.
        if (breaks.length > 0) map.setPaintProperty('zone-fill', 'fill-color', fillColor);
        return;
      }

      map.addSource('zones', { type: 'geojson', data: merged });
      map.addLayer({
        id: 'zone-fill',
        type: 'fill',
        source: 'zones',
        paint: {
          'fill-color': breaks.length > 0 ? fillColor : ramp[0]!,
          'fill-opacity': 0.92,
        },
      });
      // A hairline in the surface colour is the two pixel gap rule applied to a
      // map: adjacent zones read as separate shapes rather than one blob. The
      // value is read from the token so it follows the theme.
      const gap = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim() || '#0B0F14';
      map.addLayer({
        id: 'zone-outline',
        type: 'line',
        source: 'zones',
        paint: { 'line-color': gap, 'line-width': 0.7, 'line-opacity': 0.6 },
      });

      map.on('mousemove', 'zone-fill', (event) => {
        const feature = event.features?.[0] as MapGeoJSONFeature | undefined;
        if (!feature) return;
        map.getCanvas().style.cursor = 'pointer';
        const props = feature.properties as { zone: string; borough: string; trips: number };
        const html = `<strong>${props.zone}</strong><br>${props.borough}<br>${Number(props.trips).toLocaleString('en-US')} trips`;
        const popup =
          popupRef.current ?? (popupRef.current = new Popup({ closeButton: false, closeOnClick: false, offset: 8 }));
        popup.setLngLat(event.lngLat).setHTML(html).addTo(map);
      });
      map.on('mouseleave', 'zone-fill', () => {
        map.getCanvas().style.cursor = '';
        popupRef.current?.remove();
      });
    },
    [geo, rows, breaks, ramp],
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

  const excluded = exclusion.rows?.[0];
  const excludedShare = excluded ? Number(excluded.unknown_zone_share) : (bootstrap?.unknown_zone_share ?? null);
  const excludedTrips = excluded ? Number(excluded.unknown_trips) : null;

  return (
    <ChartCard
      testId="chart-zone-choropleth"
      title="Pickups by zone"
      subtitle="Every taxi zone the source can place, shaded by the trips that started there."
      legend={
        <RampLegend
          colors={ramp}
          low={breaks.length > 0 ? `under ${compactCount(breaks[0] ?? 0)}` : '0'}
          high={`up to ${compactCount(max)}`}
          caption="Trips starting in the zone, cut at sextiles"
        />
      }
      loading={zones.loading}
      stale={zones.loading && rows.length > 0}
      error={zones.error}
      durationMs={zones.durationMs}
      chart={
        <MapCanvas
          testId="map-choropleth"
          height={420}
          ariaLabel="Choropleth of trips by pickup zone"
          onReady={onReady}
          overlay={
            <div
              data-testid="choropleth-exclusion"
              className="pointer-events-none absolute inset-x-2 bottom-2 rounded border px-2 py-1.5 text-[11px] leading-snug"
              style={{ background: 'var(--panel)', borderColor: 'var(--status-warn)', color: 'var(--text-muted)' }}
            >
              <strong style={{ color: 'var(--text)' }}>Unknown locations are not on this map.</strong> Zone
              ids 264 and 265 are the TLC&apos;s own codes for a trip whose location was never recorded. They
              have no geometry, so they cannot be drawn, but they carry real volume:{' '}
              {excludedShare === null ? 'share loading' : percent(excludedShare, 2)} of trips in this window
              {excludedTrips === null ? '' : `, ${compactCount(excludedTrips)} trips`}. They stay in every
              total on this page and are excluded only from geography.
            </div>
          }
        />
      }
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'zone', label: 'Zone' },
            { key: 'borough', label: 'Borough' },
            { key: 'trips', label: 'Trips', align: 'right', render: (r) => Number(r.trips).toLocaleString('en-US') },
          ]}
          caption="Pickup zones with at least one trip in the window, unknown locations excluded."
        />
      }
      footnote={
        <>
          The style has no external sources: there is no tile server, no basemap and no font fetch, so the
          page works from a static host with no network beyond its own origin. Geometry comes from
          zones.geojson, which holds {geo?.features.length ?? 260} shapes
          {excluded
            ? ` for the ${Number(excluded.placeable_zones)} zones that have a borough, leaving ${Number(excluded.zones_without_geometry)} with no shape to draw`
            : ''}
          . Those zones keep their trips in the table view and in every total.
        </>
      }
    />
  );
}
