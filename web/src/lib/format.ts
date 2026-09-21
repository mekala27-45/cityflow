// The metric catalog ships a Python format spec per metric. Rather than keep a
// second table of JavaScript formatters that can drift from it, the spec is read
// and translated. Only the handful of shapes the catalog actually uses are
// supported, and an unknown shape falls through to a plain number rather than
// silently rendering something that looks precise and is not.

export interface ParsedFormat {
  prefix: string;
  suffix: string;
  percent: boolean;
  digits: number;
  grouped: boolean;
}

const SPEC = /^(?<prefix>[^{]*)\{:(?<grouping>,?)(?:\.(?<digits>\d+))?(?<kind>[fF%%])?\}(?<suffix>.*)$/;

export function parseFormat(spec: string | null | undefined): ParsedFormat {
  const fallback: ParsedFormat = { prefix: '', suffix: '', percent: false, digits: 0, grouped: true };
  if (!spec) return fallback;
  const m = SPEC.exec(spec);
  if (!m || !m.groups) return fallback;
  const g = m.groups;
  return {
    prefix: g.prefix ?? '',
    suffix: g.suffix ?? '',
    percent: g.kind === '%',
    digits: g.digits === undefined ? 0 : Number(g.digits),
    grouped: (g.grouping ?? '') === ',',
  };
}

export function applyFormat(value: number | null | undefined, spec: string | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'no data';
  const f = parseFormat(spec);
  const scaled = f.percent ? value * 100 : value;
  const body = scaled.toLocaleString('en-US', {
    minimumFractionDigits: f.digits,
    maximumFractionDigits: f.digits,
    useGrouping: f.grouped,
  });
  return `${f.prefix}${body}${f.percent ? '%' : ''}${f.suffix}`;
}

export function compactCount(value: number): string {
  if (!Number.isFinite(value)) return 'no data';
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(abs >= 1e10 ? 0 : 1)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(abs >= 1e4 ? 0 : 1)}k`;
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'no data';
  return `${(value * 100).toFixed(digits)}%`;
}

/** Signed percent, for deltas. The sign is a hyphen, never a Unicode minus. */
export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'no data';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(digits)}%`;
}

export function shortDate(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

export function longDate(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
}

export function monthLabel(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', timeZone: 'UTC' });
}

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

export function bytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${n} B`;
}

export function ms(n: number): string {
  if (!Number.isFinite(n)) return 'no data';
  if (n >= 1000) return `${(n / 1000).toFixed(2)} s`;
  return `${Math.round(n)} ms`;
}
