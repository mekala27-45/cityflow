// Builds public/bootstrap.json: the numbers the first view needs before the WASM
// engine exists. Without it the page would show empty panels for the second or
// two DuckDB takes to instantiate and pull its first range requests, which is
// exactly the moment a reader decides whether the thing works.
//
// Every aggregate here is spliced out of metric_catalog.json rather than written
// again, for the same reason the browser does it: two copies of a definition is
// one copy too many, and the catalog is the one the warehouse also compiles.

import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DuckDBInstance } from '@duckdb/node-api';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');
const dataDir = join(webRoot, 'public', 'data');

// browser_sql is always "select <expression> as <name>\nfrom agg". Pulling the
// projection out lets several metrics share one scan instead of one query each.
function projection(metric) {
  const match = /^\s*select\s+([\s\S]+?)\s+from\s+agg\s*$/i.exec(metric.browser_sql);
  if (!match || !match[1]) {
    throw new Error(`metric ${metric.name} has a browser_sql shape this splicer does not handle`);
  }
  return match[1];
}

const KPI_METRICS = ['trips', 'revenue', 'mean_duration_min', 'tip_rate', 'airport_share'];

function round(value, places) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const factor = 10 ** places;
  return Math.round(n * factor) / factor;
}

export async function buildBootstrap() {
  const catalog = JSON.parse(await readFile(join(dataDir, 'metric_catalog.json'), 'utf8'));
  const byName = new Map(catalog.map((m) => [m.name, m]));

  const instance = await DuckDBInstance.create(':memory:');
  const conn = await instance.connect();
  // getRowObjectsJson hands back plain objects with wide numeric types already
  // narrowed to strings, which is what we want: no BigInt leaks into JSON.stringify.
  const rows = async (sql) => {
    const reader = await conn.run(sql);
    return await reader.getRowObjectsJson();
  };
  const p = (name) => `'${join(dataDir, name)}'`;

  const kpiProjection = KPI_METRICS.map((name) => {
    const metric = byName.get(name);
    if (!metric) throw new Error(`metric catalog is missing ${name}`);
    return projection(metric);
  }).join(',\n       ');

  // Daily series across all services, one row per calendar day. The KPI tiles,
  // their sparklines and the period over period intervals all read this.
  const daily = await rows(`
    with agg as (select * from ${p('agg_daily.parquet')})
    select date_day::varchar as d,
           any_value(day_type) as day_type,
           bool_or(is_holiday) as holiday,
           ${kpiProjection}
    from agg
    group by date_day
    order by date_day
  `);

  const trend = await rows(`
    select date_day::varchar as d, sum(trend) as trend, sum(observed) as observed
    from ${p('agg_daily_decomposition.parquet')}
    group by date_day
    order by date_day
  `);

  // The aggregate carries hour by day type, not hour by day of week, so the full
  // year grid is a product of a measured daily level and a measured hour profile.
  // The estimate is labelled as one in the panel; see HourOfWeek.tsx.
  const hourProfile = await rows(`
    select day_type, hour::int as hour, sum(trips) as trips
    from ${p('agg_zone_hour.parquet')}
    group by 1, 2
    order by 1, 2
  `);
  const dailyByDow = await rows(`
    select d.day_of_week::int as dow, any_value(d.day_name) as day_name,
           any_value(d.day_type) as day_type, avg(t.trips) as mean_trips
    from (select date_day, sum(trips) as trips from ${p('agg_daily.parquet')} group by 1) t
    join ${p('dim_date.parquet')} d using (date_day)
    group by 1
    order by 1
  `);

  const profileTotals = new Map();
  for (const row of hourProfile) {
    profileTotals.set(row.day_type, (profileTotals.get(row.day_type) ?? 0) + Number(row.trips));
  }
  const hourOfWeek = [];
  for (const day of dailyByDow) {
    for (const row of hourProfile) {
      if (row.day_type !== day.day_type) continue;
      const total = profileTotals.get(row.day_type) ?? 1;
      hourOfWeek.push({
        dow: day.dow,
        day: day.day_name,
        hour: row.hour,
        trips: round((Number(row.trips) / total) * Number(day.mean_trips), 1),
      });
    }
  }

  const topZones = await rows(`
    select z.zone_id::int as zone_id, any_value(z.zone) as zone, any_value(z.borough) as borough,
           sum(a.trips) as trips
    from ${p('agg_zone_hour.parquet')} a
    join ${p('dim_zone.parquet')} z on z.zone_id = a.pu_zone_id
    where not z.is_unknown
    group by 1
    order by trips desc
    limit 15
  `);

  const unknown = await rows(`
    with agg as (select * from ${p('agg_zone_hour.parquet')})
    select ${projection(byName.get('unknown_zone_share'))} from agg
  `);

  const provenance = await rows(`
    select any_value(backend) as backend,
           min(source_period)::varchar as min_period,
           max(source_period)::varchar as max_period,
           count(*) as periods,
           sum(case when has_gap then 1 else 0 end) as gaps,
           sum(source_rows) as source_rows,
           sum(clean_rows) as clean_rows,
           sum(quarantined_rows) as quarantined_rows
    from ${p('mart_source_freshness.parquet')}
  `);

  const services = await rows(`select service, service_label, service_description from ${p('dim_service.parquet')} order by service`);

  const payload = {
    generated_at: new Date().toISOString(),
    metrics: KPI_METRICS,
    daily: daily.map((r) => ({
      d: r.d,
      day_type: r.day_type,
      holiday: Boolean(r.holiday),
      trips: Number(r.trips),
      revenue: round(r.revenue, 2),
      mean_duration_min: round(r.mean_duration_min, 3),
      tip_rate: round(r.tip_rate, 5),
      airport_share: round(r.airport_share, 5),
    })),
    trend: trend.map((r) => ({ d: r.d, trend: round(r.trend, 1), observed: round(r.observed, 1) })),
    hour_of_week: hourOfWeek,
    top_zones: topZones.map((r, i) => ({ ...r, trips: Number(r.trips), rank: i + 1 })),
    unknown_zone_share: round(unknown[0]?.unknown_zone_share, 5),
    provenance: {
      backend: provenance[0]?.backend ?? 'unknown',
      min_period: provenance[0]?.min_period ?? null,
      max_period: provenance[0]?.max_period ?? null,
      periods: Number(provenance[0]?.periods ?? 0),
      gaps: Number(provenance[0]?.gaps ?? 0),
      source_rows: Number(provenance[0]?.source_rows ?? 0),
      clean_rows: Number(provenance[0]?.clean_rows ?? 0),
      quarantined_rows: Number(provenance[0]?.quarantined_rows ?? 0),
    },
    services,
  };

  const target = join(webRoot, 'public', 'bootstrap.json');
  await writeFile(target, JSON.stringify(payload), 'utf8');
  const kb = Math.round(JSON.stringify(payload).length / 1024);
  process.stdout.write(`precompute: wrote public/bootstrap.json (${kb} kB, backend=${payload.provenance.backend})\n`);
  return payload;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  buildBootstrap().catch((err) => {
    process.stderr.write(`precompute failed: ${err.stack || err}\n`);
    process.exit(1);
  });
}
