-- The zone join, on its own, must not change the row count.
--
-- This test exists because it already happened once. The zone reference was
-- published one row per shapefile polygon, so the two zones with multi part
-- geometry appeared two and three times, and joining the fact to it invented
-- 7,036 trips, half a percent, spread across every month. Nothing errored.
-- Every total was simply slightly too high, and only a row count comparison
-- would have said so.
--
-- The cause is fixed in the ingest, which now dissolves polygons by zone id.
-- The test stays because the next person to touch that step needs it: a
-- dimension that gains a duplicate key is the quietest bug in a warehouse.
--
-- The join is rebuilt here rather than read off fct_trip, so a failure points
-- at the zone dimension and not at the incremental predicate. The fact count is
-- checked in the same pass so the two cannot drift apart.

with staged as (

    select count(*) as trips from {{ ref('stg_trips') }}

),

rejoined as (

    select count(*) as trips
    from {{ ref('stg_trips') }} as trips
    left join {{ ref('dim_zone') }} as pickup_zone on trips.pu_zone_id = pickup_zone.zone_id
    left join {{ ref('dim_zone') }} as dropoff_zone on trips.do_zone_id = dropoff_zone.zone_id

),

built as (

    select count(*) as trips from {{ ref('fct_trip') }}

)

select
    staged.trips as staged_trips,
    rejoined.trips as joined_trips,
    built.trips as fact_trips
from staged
cross join rejoined
cross join built
where staged.trips != rejoined.trips or staged.trips != built.trips
