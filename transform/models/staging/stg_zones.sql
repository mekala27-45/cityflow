-- The seed arrives with the flags as 0 and 1 because CSV has no boolean. They
-- are cast once, here, so that no downstream model has to remember which
-- integer means unknown.

with source as (

    select * from {{ ref('zone_lookup') }}

)

select
    cast(zone_id as smallint) as zone_id,
    zone,
    borough,
    service_zone,
    centroid_lon,
    centroid_lat,
    area_sq_mi,
    cast(part_count as smallint) as part_count,
    cast(is_unknown as boolean) as is_unknown,
    cast(is_airport as boolean) as is_airport,
    cast(has_geometry as boolean) as has_geometry
from source
