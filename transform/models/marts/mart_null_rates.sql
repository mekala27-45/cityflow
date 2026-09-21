-- Five columns are watched because all five arrive part way through the history:
-- congestion_surcharge in 2019-02, airport_fee in 2022-01, cbd_congestion_fee in
-- 2025-01, and passenger_count and ratecode_id are simply absent from the high
-- volume for hire vehicle schema. Each one is counted per service and per source
-- period so the month a column appears is visible as a step, not averaged away.

with trips as (

    select * from {{ ref('stg_trips') }}

),

counted as (

    select
        service,
        source_period,
        count(*) as rows,
        count(*) filter (where congestion_surcharge is null) as congestion_surcharge,
        count(*) filter (where airport_fee is null) as airport_fee,
        count(*) filter (where cbd_congestion_fee is null) as cbd_congestion_fee,
        count(*) filter (where passenger_count is null) as passenger_count,
        count(*) filter (where ratecode_id is null) as ratecode_id
    from trips
    group by service, source_period

),

-- One row per watched column. A union is used rather than an unpivot because it
-- keeps the column name a written literal: adding a column to the watch list is
-- then a visible edit rather than a silent change in what the panel covers.
stacked as (

    select
        service,
        source_period,
        rows,
        'congestion_surcharge' as column_name,
        congestion_surcharge as null_rows
    from counted

    union all

    select
        service,
        source_period,
        rows,
        'airport_fee' as column_name,
        airport_fee as null_rows
    from counted

    union all

    select
        service,
        source_period,
        rows,
        'cbd_congestion_fee' as column_name,
        cbd_congestion_fee as null_rows
    from counted

    union all

    select
        service,
        source_period,
        rows,
        'passenger_count' as column_name,
        passenger_count as null_rows
    from counted

    union all

    select
        service,
        source_period,
        rows,
        'ratecode_id' as column_name,
        ratecode_id as null_rows
    from counted

)

select
    service,
    source_period,
    column_name,
    rows,
    null_rows,
    cast(null_rows as double) / nullif(rows, 0) as null_share
from stacked
