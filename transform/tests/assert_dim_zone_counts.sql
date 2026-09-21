-- The shape of the zone dimension, asserted as two numbers.
--
-- 265 zones, because the TLC publishes LocationID 1 to 265 and a trip record
-- can name any of them. 260 of those have a polygon: the five without are 57,
-- 104 and 105, which are real places missing from the shapefile, and 264 and
-- 265, the unknown codes.
--
-- Both numbers are written out rather than derived, which is the point. The
-- geometry step upstream simplifies, reprojects and dissolves; a change there
-- that drops a zone or splits one would otherwise show up as a map with a hole
-- in it that nobody can date. Changing either number here should be a
-- deliberate edit with a reason in the commit message.

with counted as (

    select
        count(*) as zones,
        count(*) filter (where has_geometry) as mappable
    from {{ ref('dim_zone') }}

)

select
    zones,
    mappable
from counted
where zones != 265 or mappable != 260
