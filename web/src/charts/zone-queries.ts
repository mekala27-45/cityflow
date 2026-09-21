// The choropleth and the Pareto answer two questions from one result set, so
// they share the exact SQL text. That is not a micro optimisation: the LRU is
// keyed on the query string, so identical text means the second panel is served
// from cache and pulls no bytes at all.

import type { MetricCatalog } from '@/lib/metrics';
import { zoneHourAgg, type Filters } from '@/lib/sql';

export interface ZoneRankRow {
  zone_id: number;
  zone: string;
  borough: string;
  trips: number;
}

const ZONE_COLUMNS = ['month', 'service', 'pu_zone_id', 'day_type', 'trips'] as const;

export function buildZoneRankSql(catalog: MetricCatalog, filters: Filters): string {
  return `with ${zoneHourAgg(filters, { columns: ZONE_COLUMNS })}
select pu_zone_id::int as zone_id,
       any_value(zone) as zone,
       any_value(borough) as borough,
       ${catalog.projection('trips')}
from agg
group by 1
order by trips desc`;
}
