-- One row per service, source period and ingest run. Kept whole, including the
-- byte count and the elapsed seconds, because the freshness panel has to be able
-- to say whether a month is small because the source was small or because the
-- read was cut short.

with source as (

    select * from {{ source('warehouse', 'source_audit') }}

)

select
    service,
    source_period,
    vintage,
    backend,
    source_rows,
    clean_rows,
    quarantined_rows,
    source_bytes,
    seconds,
    ingested_at
from source
