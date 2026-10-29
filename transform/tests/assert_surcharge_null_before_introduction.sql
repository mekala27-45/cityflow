-- Two surcharges arrived part way through the published history: the congestion
-- surcharge in the February 2019 files, the airport fee in the April 2021 files.
-- Before those months the charge was not collected, and a trip from that era
-- must carry null rather than zero. Zero would mean the charge was levied and
-- came to nothing, which would drag every historical average down.
--
-- The airport fee date is the vintage table's, read off a census of the column
-- in every published file; it was January 2022 here until that census, and
-- test_ingest_mapping checks the two dates below still agree with the table.
--
-- The test passes with no rows when the build window starts after both dates.
-- That is a vacuous pass, not a broken test, and the committed window starts in
-- January 2021 so that it is not one for the airport fee.

select
    trip_id,
    service,
    source_period,
    congestion_surcharge,
    airport_fee
from {{ ref('fct_trip') }}
where
    (
        service in ('yellow', 'green')
        and source_period < date '2019-02-01'
        and congestion_surcharge is not null
    )
    or (source_period < date '2021-04-01' and airport_fee is not null)
