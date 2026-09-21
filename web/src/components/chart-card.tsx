'use client';

import { useState, type ReactNode } from 'react';
import { ms } from '@/lib/format';

interface ChartCardProps {
  title: string;
  /** One line under the title saying what the chart shows, in sentence case. */
  subtitle?: string;
  /** Method, caveat or exclusion, rendered under the chart in small type. */
  footnote?: ReactNode;
  chart: ReactNode;
  table?: ReactNode;
  /** Legend or direct label key, rendered above the chart. */
  legend?: ReactNode;
  controls?: ReactNode;
  loading?: boolean;
  error?: string | null;
  durationMs?: number | null;
  stale?: boolean;
  testId?: string;
}

export function ChartCard({
  title,
  subtitle,
  footnote,
  chart,
  table,
  legend,
  controls,
  loading,
  error,
  durationMs,
  stale,
  testId,
}: ChartCardProps) {
  const [mode, setMode] = useState<'chart' | 'table'>('chart');

  return (
    <section
      data-testid={testId}
      data-view={mode}
      className="rounded-lg border p-4"
      style={{ background: 'var(--panel)', borderColor: 'var(--border)' }}
    >
      <header className="mb-3 flex flex-wrap items-start justify-between gap-3">
        {/* basis-full at phone width pushes the controls onto their own line;
            shrink-0 on them means they would otherwise force the card wider than
            the screen. */}
        <div className="min-w-0 flex-1 basis-full sm:basis-0">
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            {title}
          </h3>
          {subtitle ? (
            <p className="mt-0.5 text-xs" style={{ color: 'var(--text-muted)' }}>
              {subtitle}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {controls}
          {table ? (
            <div
              className="flex overflow-hidden rounded border text-xs"
              style={{ borderColor: 'var(--border)' }}
              role="group"
              aria-label={`View for ${title}`}
            >
              <button
                type="button"
                data-testid={testId ? `${testId}-view-chart` : undefined}
                className="px-2 py-1"
                style={{
                  background: mode === 'chart' ? 'var(--panel-raised)' : 'transparent',
                  color: mode === 'chart' ? 'var(--text)' : 'var(--text-muted)',
                }}
                aria-pressed={mode === 'chart'}
                onClick={() => setMode('chart')}
              >
                Chart
              </button>
              <button
                type="button"
                data-testid={testId ? `${testId}-view-table` : undefined}
                className="px-2 py-1"
                style={{
                  background: mode === 'table' ? 'var(--panel-raised)' : 'transparent',
                  color: mode === 'table' ? 'var(--text)' : 'var(--text-muted)',
                }}
                aria-pressed={mode === 'table'}
                onClick={() => setMode('table')}
              >
                Table
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {legend && mode === 'chart' ? <div className="mb-2">{legend}</div> : null}

      {error ? (
        <div
          className="rounded border px-3 py-3 text-xs"
          role="alert"
          data-testid={testId ? `${testId}-error` : undefined}
          style={{ borderColor: 'var(--status-bad)', color: 'var(--status-bad)' }}
        >
          <strong className="font-semibold">Query failed.</strong> {error}
        </div>
      ) : (
        <div
          className="relative"
          style={{ opacity: stale ? 0.55 : 1, transition: 'opacity 120ms linear' }}
          data-testid={testId ? `${testId}-body` : undefined}
        >
          {mode === 'chart' ? chart : table}
          {loading ? (
            <div
              className="pointer-events-none absolute right-0 top-0 rounded px-2 py-0.5 text-[10px]"
              style={{ background: 'var(--panel-raised)', color: 'var(--text-faint)' }}
            >
              querying
            </div>
          ) : null}
        </div>
      )}

      {(footnote || durationMs !== undefined) && !error ? (
        <footer className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
          <p className="max-w-3xl text-[11px] leading-relaxed" style={{ color: 'var(--text-faint)' }}>
            {footnote}
          </p>
          {durationMs !== null && durationMs !== undefined ? (
            <span className="shrink-0 text-[10px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
              {ms(durationMs)}
            </span>
          ) : null}
        </footer>
      ) : null}
    </section>
  );
}
