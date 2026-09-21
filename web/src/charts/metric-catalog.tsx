'use client';

import { useMemo, useState } from 'react';
import { useApp } from '@/components/app-context';
import { intervalNote } from '@/lib/metrics';
import type { Metric } from '@/lib/types';

interface SqlBlockProps {
  label: string;
  sql: string;
  hint: string;
}

function SqlBlock({ label, sql, hint }: SqlBlockProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused; the text is selectable either way.
      setCopied(false);
    }
  };

  return (
    <div className="rounded border" style={{ borderColor: 'var(--border)' }}>
      <div
        className="flex items-center justify-between border-b px-2.5 py-1.5"
        style={{ borderColor: 'var(--border)' }}
      >
        <div className="min-w-0">
          <span className="text-[11px] font-medium" style={{ color: 'var(--text)' }}>
            {label}
          </span>
          <span className="ml-2 text-[10px]" style={{ color: 'var(--text-faint)' }}>
            {hint}
          </span>
        </div>
        <button
          type="button"
          onClick={copy}
          className="shrink-0 rounded border px-1.5 py-0.5 text-[10px]"
          style={{ borderColor: 'var(--border)', color: copied ? 'var(--status-ok)' : 'var(--text-muted)' }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre
        className="cf-scroll overflow-x-auto px-2.5 py-2 text-[11px] leading-relaxed"
        style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
      >
        {sql}
      </pre>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--text-faint)' }}>
        {label}
      </dt>
      <dd className="mt-0.5 text-xs" style={{ color: 'var(--text)' }}>
        {children}
      </dd>
    </div>
  );
}

export function MetricCatalogBrowser({
  selected,
  onSelect,
}: {
  selected: Metric | null;
  onSelect: (metric: Metric) => void;
}) {
  const { catalog } = useApp();
  const [search, setSearch] = useState('');

  const matches = useMemo(() => {
    if (!catalog) return [];
    const needle = search.trim().toLowerCase();
    if (!needle) return catalog.metrics;
    return catalog.metrics.filter((metric) =>
      [metric.name, metric.description, metric.unit, metric.kind, metric.owner, ...metric.components, ...metric.models]
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [catalog, search]);

  if (!catalog) {
    return (
      <p className="text-xs" style={{ color: 'var(--text-faint)' }}>
        Loading metric_catalog.json.
      </p>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-2">
        <label className="sr-only" htmlFor="metric-search">
          Search metrics
        </label>
        <input
          id="metric-search"
          data-testid="metric-search"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search name, definition, column"
          className="w-full rounded border px-2.5 py-1.5 text-xs"
          style={{ borderColor: 'var(--border)', background: 'var(--panel)', color: 'var(--text)' }}
        />
        <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
          {matches.length} of {catalog.metrics.length} metrics
        </p>
        <ul className="cf-scroll flex max-h-[420px] flex-col gap-1 overflow-y-auto pr-1">
          {matches.map((metric) => {
            const active = selected?.name === metric.name;
            return (
              <li key={metric.name}>
                <button
                  type="button"
                  data-testid={`metric-${metric.name}`}
                  onClick={() => onSelect(metric)}
                  aria-pressed={active}
                  className="w-full rounded border px-2.5 py-1.5 text-left"
                  style={{
                    borderColor: active ? 'var(--focus)' : 'var(--border)',
                    background: active ? 'var(--panel-raised)' : 'transparent',
                  }}
                >
                  <span className="block text-xs" style={{ color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>
                    {metric.name}
                  </span>
                  <span className="block text-[10px]" style={{ color: 'var(--text-faint)' }}>
                    {metric.kind}, {metric.unit}
                  </span>
                </button>
              </li>
            );
          })}
          {matches.length === 0 ? (
            <li className="px-1 py-2 text-[11px]" style={{ color: 'var(--text-faint)' }}>
              Nothing matches that.
            </li>
          ) : null}
        </ul>
      </div>

      {selected ? (
        <div className="flex min-w-0 flex-col gap-3" data-testid="metric-detail">
          <div>
            <h4 className="text-sm font-semibold" style={{ color: 'var(--text)', fontFamily: 'var(--font-mono)' }}>
              {selected.name}
            </h4>
            <p className="mt-1 text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              {selected.description}
            </p>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Field label="Kind">{selected.kind}</Field>
            <Field label="Unit">{selected.unit}</Field>
            <Field label="Owner">{selected.owner}</Field>
            <Field label="Grain">{selected.grain.join(', ')}</Field>
            <Field label="Interval method">{selected.interval ?? 'none'}</Field>
            <Field label="Display format">
              <code style={{ fontFamily: 'var(--font-mono)' }}>{selected.format}</code>
            </Field>
            <Field label="Models">{selected.models.join(', ')}</Field>
            <Field label="Components">{selected.components.join(', ')}</Field>
            <Field label="Filters">{selected.filters.length > 0 ? selected.filters.join('; ') : 'none'}</Field>
            {selected.numerator ? <Field label="Numerator">{selected.numerator}</Field> : null}
            {selected.denominator ? <Field label="Denominator">{selected.denominator}</Field> : null}
          </dl>

          <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-faint)' }}>
            Interval: {intervalNote(selected)}. Where this metric appears on a panel, that is the method
            behind the range printed next to it; where the entry says none, the page shows the level
            without one and says so rather than inventing a band.
          </p>

          <SqlBlock
            label="warehouse_sql"
            hint="what dbt compiles over the fact table"
            sql={selected.warehouse_sql}
          />
          <SqlBlock
            label="browser_sql"
            hint="what this page splices into every query, over a CTE named agg"
            sql={selected.browser_sql}
          />
        </div>
      ) : (
        <p className="text-xs" style={{ color: 'var(--text-faint)' }}>
          Pick a metric on the left.
        </p>
      )}
    </div>
  );
}
