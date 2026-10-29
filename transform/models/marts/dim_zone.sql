-- has_geometry is the column the map reads, and it now comes from the ingest
-- rather than being inferred here from a null centroid. Two different things
-- make a zone unmappable and they must not be conflated:
--
--   is_unknown: zones 264 and 265, the TLC's own codes for a location that was
--   never resolved. They carry real trips and real money. They are kept,
--   flagged, and excluded from anything drawn on a map, never from a total.
--
--   has_geometry false with is_unknown false: a real place a trip record can
--   name that the shapefile has no polygon for. The TLC's current file has a
--   polygon for every id from 1 to 263, so on it there are none; an older copy
--   had none for 57, 104 and 105. The trip's location is known, it simply
--   cannot be drawn. Counting these as unknown would overstate the unresolved
--   share.

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
    has_geometry,
    part_count,
    centroid_lon,
    centroid_lat,
    area_sq_mi
from zones
