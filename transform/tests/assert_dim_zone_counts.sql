-- The shape of the zone dimension.
--
-- 265 zones, once each, because the TLC publishes LocationID 1 to 265 and a
-- trip record can name any of them. The shapefile the TLC publishes today has
-- a polygon for every id from 1 to 263; an older copy, the one the first
-- builds read, had none for 57, 104 and 105. So the only zones allowed to have
-- no polygon are those three and the unknown codes 264 and 265, and the
-- unknown codes must never have one.
--
-- The ids are written out rather than derived, which is the point. The
-- geometry step upstream simplifies, reprojects and dissolves; a change there
-- that drops a zone or splits one would otherwise show up as a map with a hole
-- in it that nobody can date. Changing the list here should be a deliberate
-- edit with a reason in the commit message.

with zones as (

    select
        zone_id,
        has_geometry
    from {{ ref('dim_zone') }}

),

shape as (

    select
        count(*) as zones,
        count(distinct zone_id) as distinct_ids,
        min(zone_id) as first_id,
        max(zone_id) as last_id
    from zones

)

select
    'shape' as failure,
    zones as zone_id
from shape
where
    zones != 265
    or distinct_ids != 265
    or first_id != 1
    or last_id != 265

union all

select
    'no polygon' as failure,
    zone_id
from zones
where not has_geometry and zone_id not in (57, 104, 105, 264, 265)

union all

select
    'unknown code drawn' as failure,
    zone_id
from zones
where has_geometry and zone_id in (264, 265)
