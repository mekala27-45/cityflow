// Filter state to SQL. Two base relations carry the component columns the metric
// layer sums: agg_daily (date grain, no geography) and agg_zone_hour (month grain
// with a pickup zone). Which one a panel reads decides which filters can bite,
// and the panels say so on screen rather than quietly ignoring a control.

export interface Filters {
  from: string;
  to: string;
  services: string[];
  dayTypes: string[];
  boroughs: string[];
}

export const ALL_SERVICES = ['fhvhv', 'yellow', 'green'] as const;
export const ALL_DAY_TYPES = ['weekday', 'weekend'] as const;
export const ALL_BOROUGHS = ['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island', 'EWR'] as const;

/**
 * The window the app opens on before manifest.json lands. It is a fallback, not
 * the truth: AppProvider replaces it with the published window as soon as the
 * manifest arrives, so a regenerated warehouse moves the dashboard without a
 * code change. These dates match the window shipped at the time of writing.
 */
export const DEFAULT_FILTERS: Filters = {
  from: '2021-07-01',
  to: '2024-12-31',
  services: [...ALL_SERVICES],
  dayTypes: [...ALL_DAY_TYPES],
  boroughs: [],
};

/**
 * The manifest publishes the first and last source period, which are months. The
 * daily aggregate runs to the end of the last of them, so the filter window has
 * to be widened to that month's end or the last month is silently clipped.
 */
export function windowFromManifest(window: { start: string; end: string }): { from: string; to: string } {
  const lastMonth = new Date(`${window.end.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(lastMonth.getTime())) return { from: DEFAULT_FILTERS.from, to: DEFAULT_FILTERS.to };
  const endOfMonth = new Date(Date.UTC(lastMonth.getUTCFullYear(), lastMonth.getUTCMonth() + 1, 0));
  return { from: window.start.slice(0, 10), to: endOfMonth.toISOString().slice(0, 10) };
}

function quoteList(values: readonly string[]): string {
  return values.map((v) => `'${v.replace(/'/g, "''")}'`).join(', ');
}

function dateLiteral(iso: string): string {
  // Filter dates come from a date input, so the shape is fixed, but a malformed
  // value would otherwise reach the engine as raw text.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) throw new Error(`bad date in filter: ${iso}`);
  return `DATE '${iso}'`;
}

export function servicePredicate(filters: Filters, column = 'service'): string {
  if (filters.services.length === 0 || filters.services.length === ALL_SERVICES.length) return 'true';
  return `${column} in (${quoteList(filters.services)})`;
}

export function dayTypePredicate(filters: Filters, column = 'day_type'): string {
  if (filters.dayTypes.length === 0 || filters.dayTypes.length === ALL_DAY_TYPES.length) return 'true';
  return `${column} in (${quoteList(filters.dayTypes)})`;
}

export function boroughPredicate(filters: Filters, column = 'borough'): string {
  if (filters.boroughs.length === 0) return 'true';
  return `${column} in (${quoteList(filters.boroughs)})`;
}

/** agg_daily restricted to the filter window. Borough cannot apply at this grain. */
export function dailyAgg(filters: Filters): string {
  return `agg as (
    select *
    from 'agg_daily.parquet'
    where date_day between ${dateLiteral(filters.from)} and ${dateLiteral(filters.to)}
      and ${servicePredicate(filters)}
      and ${dayTypePredicate(filters)}
  )`;
}

/**
 * agg_zone_hour joined to the zone dimension. The month column is a first of
 * month, so a date window is applied against the month it falls in rather than
 * silently dropping a partial month at either end.
 */
export function zoneHourAgg(
  filters: Filters,
  options: { excludeUnknown?: boolean; extra?: string; columns?: readonly string[] } = {},
): string {
  const excludeUnknown = options.excludeUnknown ?? true;
  const unknownClause = excludeUnknown ? 'and not z.is_unknown' : '';
  const extra = options.extra ? `and ${options.extra}` : '';
  // Naming the columns matters: parquet is columnar and DuckDB fetches only the
  // chunks a projection touches, so "a.*" turns a 40 kB read into a 3.5 MB one.
  const projected = options.columns ? options.columns.map((c) => `a.${c}`).join(', ') : 'a.*';
  return `agg as (
    select ${projected}, z.zone, z.borough, z.is_unknown, z.is_airport,
           z.centroid_lon, z.centroid_lat
    from 'agg_zone_hour.parquet' a
    join 'dim_zone.parquet' z on z.zone_id = a.pu_zone_id
    where a.month between date_trunc('month', ${dateLiteral(filters.from)})
                      and date_trunc('month', ${dateLiteral(filters.to)})
      and ${servicePredicate(filters, 'a.service')}
      and ${dayTypePredicate(filters, 'a.day_type')}
      and ${boroughPredicate(filters, 'z.borough')}
      ${unknownClause}
      ${extra}
  )`;
}

/**
 * day_type as a predicate on day_of_week, for relations that carry the day of
 * week and not the label. Verified against dim_date: day 0 is Sunday and day 6
 * is Saturday, day_type is a pure function of day_of_week there, and a holiday
 * keeps its weekday classification rather than being folded into the weekend.
 */
export function dayTypeFromDowPredicate(filters: Filters, column = 'day_of_week'): string {
  if (filters.dayTypes.length === 0 || filters.dayTypes.length === ALL_DAY_TYPES.length) return 'true';
  const weekend = `${column} in (0, 6)`;
  return filters.dayTypes.includes('weekend') ? weekend : `not (${weekend})`;
}

/**
 * agg_hour_of_week, the measured hour by day of week grid. Borough is a column
 * here rather than a join, so all four filters reach it directly.
 */
export function hourOfWeekAgg(
  filters: Filters,
  options: { columns?: readonly string[] } = {},
): string {
  // Parquet is columnar and DuckDB reads only the chunks a projection touches,
  // so naming the columns is the difference between a 60 kB read and a 4 MB one.
  const projected = (options.columns ?? ['day_of_week', 'day_name', 'hour', 'trips']).join(', ');
  return `agg as (
    select ${projected}
    from 'agg_hour_of_week.parquet'
    where ${monthWindow(filters, 'month')}
      and ${servicePredicate(filters)}
      and ${dayTypeFromDowPredicate(filters)}
      and ${boroughPredicate(filters)}
  )`;
}

/** Month window predicate for relations that carry a month column and no zone. */
export function monthWindow(filters: Filters, column = 'month'): string {
  return `${column} between date_trunc('month', ${dateLiteral(filters.from)}) and date_trunc('month', ${dateLiteral(filters.to)})`;
}

export function dateWindow(filters: Filters, column = 'date_day'): string {
  return `${column} between ${dateLiteral(filters.from)} and ${dateLiteral(filters.to)}`;
}

/** Days in the window, used to size the comparison period. */
export function windowDays(filters: Filters): number {
  const from = Date.parse(`${filters.from}T00:00:00Z`);
  const to = Date.parse(`${filters.to}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return 0;
  return Math.max(1, Math.round((to - from) / 86_400_000) + 1);
}
