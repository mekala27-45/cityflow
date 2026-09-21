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

export const DEFAULT_FILTERS: Filters = {
  from: '2024-01-01',
  to: '2024-12-31',
  services: [...ALL_SERVICES],
  dayTypes: [...ALL_DAY_TYPES],
  boroughs: [],
};

/** The TLC's own unknown location codes. Real volume, no geometry, never mapped. */
export const UNKNOWN_ZONE_IDS = [264, 265] as const;

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
 * agg_zone_hour without the zone join, for the hour of day profile. The join
 * costs a scan of pu_zone_id across every row and buys nothing when the caller
 * only needs the shape of a day: any chart using this ignores the borough
 * filter, and says so.
 */
export function hourProfileAgg(filters: Filters): string {
  return `agg as (
    select hour, day_type, trips
    from 'agg_zone_hour.parquet'
    where ${monthWindow(filters, 'month')}
      and ${servicePredicate(filters)}
      and ${dayTypePredicate(filters)}
  )`;
}

/** Month window predicate for relations that carry a month column and no zone. */
export function monthWindow(filters: Filters, column = 'month'): string {
  return `${column} between date_trunc('month', ${dateLiteral(filters.from)}) and date_trunc('month', ${dateLiteral(filters.to)})`;
}

export function dateWindow(filters: Filters, column = 'date_day'): string {
  return `${column} between ${dateLiteral(filters.from)} and ${dateLiteral(filters.to)}`;
}

export function filtersKey(filters: Filters): string {
  return [
    filters.from,
    filters.to,
    [...filters.services].sort().join('+'),
    [...filters.dayTypes].sort().join('+'),
    [...filters.boroughs].sort().join('+'),
  ].join('|');
}

/** Days in the window, used to size the comparison period. */
export function windowDays(filters: Filters): number {
  const from = Date.parse(`${filters.from}T00:00:00Z`);
  const to = Date.parse(`${filters.to}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return 0;
  return Math.max(1, Math.round((to - from) / 86_400_000) + 1);
}
