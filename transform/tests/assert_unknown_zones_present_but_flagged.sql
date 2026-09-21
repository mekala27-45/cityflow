-- Two assertions in one, because they fail in opposite directions.
--
-- Zones 264 and 265 are the TLC's codes for a trip whose location was never
-- resolved. They carry real volume, roughly one trip in sixty. If they have all
-- disappeared, something upstream has started dropping rows rather than
-- labelling them, and every borough share on the map has silently moved.
--
-- If they are present but not flagged, they will be drawn on a map at a
-- centroid they do not have, or counted into a borough they do not belong to.

with unknown_zone_trips as (

    select
        count(*) as trips,
        count(*) filter (where not pu_is_unknown and not do_is_unknown) as unflagged
    from {{ ref('fct_trip') }}
    where pu_zone_id in (264, 265) or do_zone_id in (264, 265)

)

select
    trips,
    unflagged
from unknown_zone_trips
where trips = 0 or unflagged > 0
