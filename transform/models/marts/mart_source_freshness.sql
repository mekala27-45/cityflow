-- has_gap is the reason this model exists. A missing month is invisible in a
-- line chart: the line simply joins the two months either side of the hole and
-- reads as a smooth decline. Flagging the gap lets the panel break the line
-- instead of drawing through it.

with audited as (

    select * from {{ ref('stg_source_audit') }}

),

sequenced as (

    select
        service,
        source_period,
        vintage,
        backend,
        source_rows,
        clean_rows,
        quarantined_rows,
        ingested_at,
        lag(source_period) over (
            partition by service order by source_period
        ) as previous_loaded_period
    from audited

)

select
    service,
    source_period,
    vintage,
    backend,
    source_rows,
    clean_rows,
    quarantined_rows,
    ingested_at,
    -- The first month a service appears has nothing before it, so it is not a
    -- gap. Everything after that must sit exactly one month behind its
    -- predecessor or there is a hole in the source.
    case
        when previous_loaded_period is null then false
        else date_diff('month', previous_loaded_period, source_period) > 1
    end as has_gap
from sequenced
