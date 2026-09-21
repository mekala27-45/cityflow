-- has_geometry is the column the map reads. Zones 264 and 265 are the TLC's own
-- codes for a location the driver or the app never resolved. They are not empty
-- rows to be cleaned away: they carry real trips and real money, and dropping
-- them silently would move every borough share. They are kept, flagged, and
-- excluded from anything that needs a point on a map by has_geometry.

with zones as (

    select * from {{ ref('stg_zones') }}

)

select
    zone_id,
    zone,
    borough,
    service_zone,
    is_unknown,
    is_airport,
    centroid_lon,
    centroid_lat,
    area_sq_mi,
    centroid_lon is not null and centroid_lat is not null as has_geometry
from zones
