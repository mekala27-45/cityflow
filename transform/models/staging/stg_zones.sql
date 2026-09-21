-- The seed is one row per shapefile polygon, not one row per zone. Zone 56
-- (Corona) has two parts and zone 103 (Governors Island, Ellis Island and
-- Liberty Island) has three, so the file holds 265 rows for 262 zones. Left as
-- it is, the zone join in the fact fans out and invents trips, which is how a
-- trip count quietly grows by half a percent.
--
-- Collapsed to one row per zone here, because the grain belongs to the
-- dimension and every consumer would otherwise have to rediscover this.
--
-- Area is additive across parts. A centroid is not: the mean of the three island
-- centroids in zone 103 lands in the harbour, inside no zone at all. The largest
-- part's centroid is used instead, which is on land and is where the volume is.

with parts as (

    select * from {{ ref('zone_lookup') }}

),

measured as (

    select
        zone_id,
        sum(area_sq_mi) as area_sq_mi
    from parts
    group by zone_id

),

ranked as (

    select
        *,
        row_number() over (
            partition by zone_id order by area_sq_mi desc, centroid_lon asc
        ) as part_rank
    from parts

)

select
    cast(ranked.zone_id as smallint) as zone_id,
    ranked.zone,
    ranked.borough,
    ranked.service_zone,
    ranked.centroid_lon,
    ranked.centroid_lat,
    measured.area_sq_mi,
    cast(ranked.is_unknown as boolean) as is_unknown,
    cast(ranked.is_airport as boolean) as is_airport
from ranked
inner join measured on ranked.zone_id = measured.zone_id
where ranked.part_rank = 1
