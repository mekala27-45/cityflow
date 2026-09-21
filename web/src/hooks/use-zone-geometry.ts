'use client';

import { useEffect, useState } from 'react';
import { dataUrl } from '@/lib/base-path';

export interface ZoneProperties {
  zone_id: number;
  zone: string;
  borough: string;
  trips?: number;
}

export type ZoneCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, ZoneProperties>;

let cached: ZoneCollection | null = null;
let inFlight: Promise<ZoneCollection | null> | null = null;

/**
 * zones.geojson is 300 kB and two maps want it. Caching the parsed collection in
 * a module means the second map costs nothing, and a filter change costs nothing
 * either, because geometry does not depend on the filters.
 */
export function useZoneGeometry(): ZoneCollection | null {
  const [geo, setGeo] = useState<ZoneCollection | null>(cached);

  useEffect(() => {
    if (cached) return;
    let alive = true;
    inFlight ??= fetch(dataUrl('zones.geojson'))
      .then((response) => (response.ok ? (response.json() as Promise<ZoneCollection>) : null))
      .then((data) => {
        cached = data;
        return data;
      })
      .catch(() => null);
    void inFlight.then((data) => {
      if (alive) setGeo(data);
    });
    return () => {
      alive = false;
    };
  }, []);

  return geo;
}
