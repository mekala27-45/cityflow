-- A trip that ends before it starts. The ingest quarantines these, so a row here
-- means either the quarantine rule stopped running or the fact is reading
-- timestamps in the wrong order, and every duration and speed built on them is
-- suspect until that is answered.

select
    trip_id,
    service,
    pickup_ts,
    dropoff_ts,
    duration_s
from {{ ref('fct_trip') }}
where duration_s < 0
