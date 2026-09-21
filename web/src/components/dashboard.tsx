'use client';

import { AppProvider, useApp } from './app-context';
import { DebugOverlay } from './debug-overlay';
import { FilterBar } from './filter-bar';
import { NavRail } from './nav-rail';
import { ProvenanceBanner, ProvenancePill } from './provenance';
import { PanelPulse } from '@/panels/panel-pulse';
import { PanelGeography } from '@/panels/panel-geography';
import { PanelBehavior } from '@/panels/panel-behavior';
import { PanelMix } from '@/panels/panel-mix';
import { PanelTrust } from '@/panels/panel-trust';
import { PanelLineage } from '@/panels/panel-lineage';

function ThemeToggle() {
  const { theme, setTheme } = useApp();
  return (
    <button
      type="button"
      data-testid="theme-toggle"
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      className="shrink-0 rounded-full border px-2.5 py-1 text-[11px]"
      style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      aria-label={`Switch to the ${theme === 'dark' ? 'light' : 'dark'} theme`}
    >
      {theme === 'dark' ? 'Light' : 'Dark'}
    </button>
  );
}

function Header() {
  return (
    <header className="border-b" style={{ borderColor: 'var(--border)' }}>
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="min-w-0">
          <h1 className="text-base font-semibold tracking-tight" style={{ color: 'var(--text)' }}>
            cityflow
          </h1>
          <p className="text-xs" style={{ color: 'var(--text-faint)' }}>
            NYC trip records, queried in the browser
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <ProvenancePill />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function Shell() {
  return (
    <>
      <Header />
      <FilterBar />
      <main className="mx-auto max-w-[1600px] px-4 py-5">
        <div className="mb-5">
          <ProvenanceBanner />
        </div>
        <div className="grid gap-6 lg:grid-cols-[168px_minmax(0,1fr)]">
          <NavRail />
          <div className="flex min-w-0 flex-col gap-10">
            <PanelPulse />
            <PanelGeography />
            <PanelBehavior />
            <PanelMix />
            <PanelTrust />
            <PanelLineage />
          </div>
        </div>
        <footer className="mt-12 border-t pt-4 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--text-faint)' }}>
          Aggregates are read straight from parquet over HTTP range requests by DuckDB compiled to
          WebAssembly. Nothing is fetched from a CDN: the engine, its worker and every data file are served
          from this origin. Open the benchmark panel at the bottom right to see what each query actually
          pulled.
        </footer>
      </main>
      <DebugOverlay />
    </>
  );
}

export function Dashboard() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
