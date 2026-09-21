'use client';

import { useApp } from './app-context';
import { serviceColor, SERVICE_LABEL } from '@/lib/palette';
import { ALL_BOROUGHS, ALL_DAY_TYPES, ALL_SERVICES, windowFromManifest, type Filters } from '@/lib/sql';

function toggle(list: string[], value: string, atLeastOne: boolean): string[] {
  const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  if (atLeastOne && next.length === 0) return list;
  return next;
}

interface ChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
  swatch?: string;
  testId?: string;
}

function Chip({ label, active, onClick, swatch, testId }: ChipProps) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-pressed={active}
      onClick={onClick}
      className="flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs transition-colors"
      style={{
        borderColor: active ? 'var(--focus)' : 'var(--border)',
        background: active ? 'var(--panel-raised)' : 'transparent',
        color: active ? 'var(--text)' : 'var(--text-faint)',
      }}
    >
      {swatch ? (
        <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: swatch, opacity: active ? 1 : 0.35 }} />
      ) : null}
      {label}
    </button>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <span className="shrink-0 text-[11px] uppercase tracking-wide" style={{ color: 'var(--text-faint)' }}>
        {label}
      </span>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

/**
 * One row, always at the top, never repeated inside a panel. Every panel reads
 * this state; where a filter cannot bite at a panel's grain the panel says so
 * rather than the control quietly doing nothing.
 */
export function FilterBar() {
  const { filters, setFilters, resetFilters, theme, manifest } = useApp();

  // The pickers are bounded by the window the build published, so a date outside
  // the source cannot be entered at all rather than quietly returning nothing.
  const bounds = manifest?.window ? windowFromManifest(manifest.window) : null;
  const patch = (next: Partial<Filters>) => setFilters((prev) => ({ ...prev, ...next }));
  const isDefault =
    (!bounds || (filters.from === bounds.from && filters.to === bounds.to)) &&
    filters.services.length === ALL_SERVICES.length &&
    filters.dayTypes.length === ALL_DAY_TYPES.length &&
    filters.boroughs.length === 0;

  return (
    <div
      className="sticky top-0 z-30 border-b backdrop-blur"
      style={{ background: 'color-mix(in srgb, var(--surface) 92%, transparent)', borderColor: 'var(--border)' }}
      data-testid="filter-bar"
    >
      <div className="cf-scroll mx-auto flex max-w-[1600px] items-center gap-5 overflow-x-auto px-4 py-2.5">
        <Group label="Dates">
          <input
            type="date"
            aria-label="Start date"
            data-testid="filter-from"
            value={filters.from}
            min={bounds?.from}
            max={filters.to}
            onChange={(e) => e.target.value && patch({ from: e.target.value })}
            className="rounded border px-2 py-1 text-xs"
            style={{ borderColor: 'var(--border)', background: 'var(--panel)', color: 'var(--text)' }}
          />
          <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
            to
          </span>
          <input
            type="date"
            aria-label="End date"
            data-testid="filter-to"
            value={filters.to}
            min={filters.from}
            max={bounds?.to}
            onChange={(e) => e.target.value && patch({ to: e.target.value })}
            className="rounded border px-2 py-1 text-xs"
            style={{ borderColor: 'var(--border)', background: 'var(--panel)', color: 'var(--text)' }}
          />
        </Group>

        <Group label="Service">
          {ALL_SERVICES.map((service) => (
            <Chip
              key={service}
              testId={`filter-service-${service}`}
              label={SERVICE_LABEL[service] ?? service}
              swatch={serviceColor(theme, service)}
              active={filters.services.includes(service)}
              onClick={() => patch({ services: toggle(filters.services, service, true) })}
            />
          ))}
        </Group>

        <Group label="Day">
          {ALL_DAY_TYPES.map((dayType) => (
            <Chip
              key={dayType}
              testId={`filter-daytype-${dayType}`}
              label={dayType === 'weekday' ? 'Weekday' : 'Weekend'}
              active={filters.dayTypes.includes(dayType)}
              onClick={() => patch({ dayTypes: toggle(filters.dayTypes, dayType, true) })}
            />
          ))}
        </Group>

        <Group label="Borough">
          <Chip
            label="All"
            active={filters.boroughs.length === 0}
            onClick={() => patch({ boroughs: [] })}
          />
          {ALL_BOROUGHS.map((borough) => (
            <Chip
              key={borough}
              testId={`filter-borough-${borough.replace(/\s+/g, '-')}`}
              label={borough}
              active={filters.boroughs.includes(borough)}
              onClick={() => patch({ boroughs: toggle(filters.boroughs, borough, false) })}
            />
          ))}
        </Group>

        <button
          type="button"
          onClick={resetFilters}
          disabled={isDefault}
          className="ml-auto shrink-0 rounded border px-2.5 py-1 text-xs disabled:opacity-40"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          Reset
        </button>
      </div>
    </div>
  );
}
