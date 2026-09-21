-- Zone resolution lives here rather than in fct_trip because it carries a policy
-- decision, and a policy decision that is spread across two joins in a fact
-- table is a decision nobody will find again.
--
-- The policy: a zone id that does not resolve in the dimension is treated as
-- unknown, exactly like the TLC's own 264 and 265. The alternative, a null
-- flag, would make has_known_geography null, and a null there reads as "no" in
-- some tools and as "yes" in others.
--
-- Ephemeral, so this is inlined into fct_trip and the trip rows are read once.

with trips as (

    select * from {{ ref('stg_trips') }}

),

zones as (

    select
        zone_id,
        is_unknown,
        is_airport
    from {{ ref('dim_zone') }}

)

select
    trips.*,
    coalesce(pickup_zone.is_unknown, true) as pu_is_unknown,
    coalesce(dropoff_zone.is_unknown, true) as do_is_unknown,
    -- The airport flag is taken from the dimension rather than from a literal
    -- list, so zones 1 (Newark), 132 (JFK) and 138 (LaGuardia) are named in one
    -- place. A trip that touches an airport at either end counts.
    coalesce(pickup_zone.is_airport, false)
    or coalesce(dropoff_zone.is_airport, false) as is_airport_trip
from trips
left join zones as pickup_zone on trips.pu_zone_id = pickup_zone.zone_id
left join zones as dropoff_zone on trips.do_zone_id = dropoff_zone.zone_id
