-- Removal counts as a share of what the source actually held. The absolute count
-- is close to useless on its own: a rule that removes 40,000 rows is alarming
-- until you know the month held 20 million. The share is what a reader can
-- compare between months and between services.

with removed as (

    select * from {{ ref('stg_quarantine') }}

)

select
    rule,
    service,
    source_period,
    rows,
    source_rows,
    -- source_rows is written by the same ingest run, so a zero here would mean
    -- a month that was read and found empty. Dividing by it would turn that
    -- into an error instead of a visible zero row month.
    cast(rows as double) / nullif(source_rows, 0) as share_of_source
from removed
