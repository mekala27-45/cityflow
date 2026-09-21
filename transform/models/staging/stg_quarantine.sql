-- A pass through view on purpose. The value of the quarantine log is that it is
-- exactly what the ingest wrote, so reshaping it here would put a second version
-- of the removal counts into circulation.

with source as (

    select * from {{ source('warehouse', 'quarantine_log') }}

)

select
    service,
    source_period,
    vintage,
    rule,
    rows,
    source_rows
from source
