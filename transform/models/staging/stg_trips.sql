-- Nothing is renamed here. The ingest already made the naming decisions across
-- eleven published vintages, and a second vocabulary in the staging layer would
-- mean every conversation about a column starts by asking which name is meant.
--
-- Four derivations are added because every consumer needs them and none of them
-- can be computed correctly by accident: duration and speed both have a divide
-- by zero waiting in them, and the date and hour keys have to agree with the
-- date and hour dimensions on which timestamp defines a trip's slot (pickup).

with source as (

    select * from {{ source('warehouse', 'stg_trip') }}

)

select
    trip_id,
    service,
    vintage,
    source_period,
    operator,
    pickup_ts,
    dropoff_ts,
    pu_zone_id,
    do_zone_id,
    passenger_count,
    trip_distance,
    ratecode_id,
    payment_type,
    tip_is_observable,
    fare_amount,
    extra,
    mta_tax,
    improvement_surcharge,
    congestion_surcharge,
    airport_fee,
    cbd_congestion_fee,
    tip_amount,
    tolls_amount,
    total_amount,
    cast(pickup_ts as date) as pickup_date,
    hour(pickup_ts) as pickup_hour,
    date_diff('second', pickup_ts, dropoff_ts) as duration_s,
    -- A trip with no elapsed time has no speed, and a null is a far better
    -- answer than an infinity that survives into a mean and moves it.
    trip_distance / nullif(date_diff('second', pickup_ts, dropoff_ts), 0) * 3600
        as implied_speed_mph
from source
