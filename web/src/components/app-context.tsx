'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { assetUrl, dataUrl } from '@/lib/base-path';
import { getEngine } from '@/lib/duckdb-client';
import { MetricCatalog } from '@/lib/metrics';
import { queryLog } from '@/lib/query-log';
import { DEFAULT_FILTERS, type Filters } from '@/lib/sql';
import { themeStore } from '@/lib/theme-store';
import type { ThemeName } from '@/lib/palette';
import type { Bootstrap, Changepoints, Lineage, Metric } from '@/lib/types';

interface AppState {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
  filters: Filters;
  setFilters: (next: Filters | ((prev: Filters) => Filters)) => void;
  resetFilters: () => void;
  bootstrap: Bootstrap | null;
  catalog: MetricCatalog | null;
  lineage: Lineage | null;
  changepoints: Changepoints | null;
  engineReady: boolean;
  engineError: string | null;
  debugOpen: boolean;
  setDebugOpen: (open: boolean) => void;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const theme = useSyncExternalStore(themeStore.subscribe, themeStore.getSnapshot, themeStore.getServerSnapshot);
  const [filters, setFiltersState] = useState<Filters>(DEFAULT_FILTERS);
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [catalog, setCatalog] = useState<MetricCatalog | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [changepoints, setChangepoints] = useState<Changepoints | null>(null);
  const [engineReady, setEngineReady] = useState(false);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);

  // The attribute is the one place the theme leaves React: the CSS tokens key off
  // it, and writing it is a side effect on an external system, not state.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((next: ThemeName) => themeStore.set(next), []);

  // Small JSON first. These three files carry the first view and panel six, and
  // none of them wait on WASM.
  useEffect(() => {
    let cancelled = false;
    const load = async <T,>(url: string): Promise<T | null> => {
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`${response.status} for ${url}`);
        return (await response.json()) as T;
      } catch {
        return null;
      }
    };
    void (async () => {
      const [boot, metrics, graph, cps] = await Promise.all([
        load<Bootstrap>(assetUrl('/bootstrap.json')),
        load<Metric[]>(dataUrl('metric_catalog.json')),
        load<Lineage>(dataUrl('lineage.json')),
        load<Changepoints>(dataUrl('changepoints.json')),
      ]);
      if (cancelled) return;
      setBootstrap(boot);
      if (metrics) setCatalog(new MetricCatalog(metrics));
      setLineage(graph);
      setChangepoints(cps);
      queryLog.setFirstPaint(performance.now());
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The engine boots after the first paint has had a chance to land. Blocking on
  // a 36 MB wasm module before showing anything is the failure mode this whole
  // bootstrap arrangement exists to avoid.
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getEngine()
        .then(() => {
          if (!cancelled) setEngineReady(true);
        })
        .catch((err: unknown) => {
          if (!cancelled) setEngineError(err instanceof Error ? err.message : String(err));
        });
    }, 60);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const resetFilters = useCallback(() => setFiltersState(DEFAULT_FILTERS), []);

  const value = useMemo<AppState>(
    () => ({
      theme,
      setTheme,
      filters,
      setFilters: setFiltersState,
      resetFilters,
      bootstrap,
      catalog,
      lineage,
      changepoints,
      engineReady,
      engineError,
      debugOpen,
      setDebugOpen,
    }),
    [theme, setTheme, filters, resetFilters, bootstrap, catalog, lineage, changepoints, engineReady, engineError, debugOpen],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}
