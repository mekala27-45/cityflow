{{ config(materialized='incremental') }}

-- Grain: one trip. No unique_key is configured because the ingest assigns
-- trip_id and never reissues one, so there is nothing to merge on; the model
-- appends whole source periods instead.
--
-- The incremental predicate is an anti join on source_period rather than
-- "greater than the newest loaded month". A month arrives whole, from one
-- published file, so a month is either fully loaded or absent. Comparing
-- against the maximum would silently refuse a backfill of an older month, which
-- is exactly the case where an incremental model quietly loses data.

with trips as (

    select * from {{ ref('int_trip_geography') }}
    {% if is_incremental() %}
        where source_period not in (select distinct source_period from {{ this }})
    {% endif %}

)

select
    trip_id,
    service,
    vintage,
    operator,
    source_period,
    pickup_ts,
    dropoff_ts,
    pickup_date,
    pickup_hour,
    pu_zone_id,
    do_zone_id,
    payment_type,
    tip_is_observable,
    passenger_count,
    trip_distance,
    duration_s,
    implied_speed_mph,
    fare_amount,
    tip_amount,
    tolls_amount,
    extra,
    mta_tax,
    improvement_surcharge,
    congestion_surcharge,
    airport_fee,
    cbd_congestion_fee,
    total_amount,
    pu_is_unknown,
    do_is_unknown,
    is_airport_trip,
    not pu_is_unknown and not do_is_unknown as has_known_geography,
    -- A surcharge column that did not exist yet contributes zero, not null. The
    -- money genuinely was not charged, and a null here would null the whole
    -- total and take the trip out of every fare composition chart. Whether the
    -- column existed is a separate question, answered by mart_null_rates.
    coalesce(extra, 0)
    + coalesce(mta_tax, 0)
    + coalesce(improvement_surcharge, 0)
    + coalesce(congestion_surcharge, 0)
    + coalesce(airport_fee, 0)
    + coalesce(cbd_congestion_fee, 0) as surcharge_total,
    -- Null means unobservable, and it has to stay null. A cash trip records a
    -- tip of zero because no tip passed through the meter, not because the
    -- passenger left nothing. Averaging that zero in is how published NYC tip
    -- rates end up roughly a third below the truth.
    case
        when tip_is_observable then tip_amount / nullif(fare_amount, 0)
    end as tip_pct,
    -- Any code outside the published 1 to 6 becomes the TLC's unknown member,
    -- and a missing code becomes the not applicable member, so the foreign key
    -- always resolves and no trip falls out of a rate code breakdown.
    cast(
        case
            when ratecode_id is null then 0
            when ratecode_id between 1 and 6 then ratecode_id
            else 99
        end as smallint
    ) as ratecode_id
from trips
