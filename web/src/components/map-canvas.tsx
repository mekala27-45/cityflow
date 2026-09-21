'use client';

import { Map as MapLibreMap, NavigationControl, setWorkerUrl, type StyleSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useApp } from './app-context';
import { assetUrl } from '@/lib/base-path';
import { SURFACE } from '@/lib/palette';

// MapLibre derives its worker URL from import.meta.url, which after bundling
// resolves inside a Next chunk directory that holds no worker file. The worker
// is then never reachable: no error is raised, the GeoJSON source simply never
// reports itself loaded and the map paints its background and nothing else.
// scripts/prepare-assets.mjs copies the worker next to the site so this can
// point at a URL that exists under the base path.
let workerConfigured = false;
function configureWorker(): void {
  if (workerConfigured) return;
  workerConfigured = true;
  setWorkerUrl(assetUrl('/maplibre/maplibre-gl-worker.mjs'));
}

// No tile server is reachable from the deploy target and none would be wanted
// here anyway: a basemap under a choropleth of administrative zones adds roads
// nobody is reading. The style declares no sources at all, so MapLibre renders
// the background and whatever GeoJSON the caller adds, and nothing leaves the
// origin. No glyphs URL is set either, which means no symbol layer may carry
// text; labels are HTML on top of the canvas instead.
function plainStyle(background: string): StyleSpecification {
  return {
    version: 8,
    name: 'cityflow-plain',
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': background } }],
  };
}

// The five boroughs, with a little air around them.
export const NYC_BOUNDS: [[number, number], [number, number]] = [
  [-74.29, 40.47],
  [-73.67, 40.93],
];

interface MapCanvasProps {
  /** Called once the style has loaded; add sources and layers here. */
  onReady: (map: MapLibreMap) => void;
  /** Called when the theme flips, after the style has been rebuilt. */
  height: number;
  ariaLabel: string;
  overlay?: ReactNode;
  testId?: string;
}

export function MapCanvas({ onReady, height, ariaLabel, overlay, testId }: MapCanvasProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(onReady);
  const { theme } = useApp();
  const [failed, setFailed] = useState<string | null>(null);

  // The callback is kept in a ref so a new closure from the parent does not tear
  // the map down and build it again; only a theme change should do that.
  useEffect(() => {
    ready.current = onReady;
  }, [onReady]);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    configureWorker();
    let instance: MapLibreMap;
    try {
      instance = new MapLibreMap({
        container: node,
        style: plainStyle(SURFACE[theme]),
        bounds: NYC_BOUNDS,
        fitBoundsOptions: { padding: 12 },
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        // A raster free style has nothing to prefetch, so the only cost here is
        // the GeoJSON the caller adds.
        maxZoom: 15,
        minZoom: 8,
      });
    } catch (err) {
      // Reported on the next microtask rather than inside the effect body, so a
      // constructor failure does not cascade a render inside the same commit.
      const message = err instanceof Error ? err.message : String(err);
      queueMicrotask(() => setFailed(message));
      return;
    }
    map.current = instance;
    instance.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    instance.on('load', () => {
      if (!map.current) return;
      // The container is measured at construction and can grow once the card has
      // laid out, and a resize keeps the centre and zoom rather than the bounds,
      // so the city drifts off the frame. Refitting on load and on every resize
      // keeps the five boroughs in view at any card width.
      map.current.fitBounds(NYC_BOUNDS, { padding: 12, animate: false });
      ready.current(map.current);
    });
    instance.on('resize', () => {
      map.current?.fitBounds(NYC_BOUNDS, { padding: 12, animate: false });
    });
    instance.on('error', (event) => {
      const message = event.error?.message;
      if (message) setFailed(message);
    });
    return () => {
      instance.remove();
      map.current = null;
    };
    // The style is rebuilt on a theme change, which is why theme is a dependency.
  }, [theme]);

  return (
    <div className="relative" style={{ height }} data-testid={testId}>
      {/* The position and inset are inline rather than utility classes on purpose:
          maplibre-gl.css sets .maplibregl-map { position: relative } at the same
          specificity as Tailwind's .absolute and later in the bundle, so the
          class loses, inset stops applying and the container collapses to zero
          height. The map then renders correctly into a canvas nobody can see. */}
      <div
        ref={host}
        className="overflow-hidden rounded"
        style={{ position: 'absolute', inset: 0 }}
        role="img"
        aria-label={ariaLabel}
      />
      {overlay}
      {failed ? (
        <div
          role="alert"
          className="absolute inset-x-2 bottom-2 rounded border px-2 py-1 text-[11px]"
          style={{ borderColor: 'var(--status-bad)', color: 'var(--status-bad)', background: 'var(--panel)' }}
        >
          Map failed: {failed}
        </div>
      ) : null}
    </div>
  );
}
