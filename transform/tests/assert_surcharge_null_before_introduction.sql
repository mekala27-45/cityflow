-- Two surcharges arrived part way through the published history: the congestion
-- surcharge in the February 2019 files, the airport fee in the January 2022
-- files. Before those months the column does not exist, and a trip from that era
-- must carry null rather than zero. Zero would mean the charge was levied and
-- came to nothing, which would drag every historical average down.
--
-- The test passes with no rows when the build window starts after both dates,
-- which is the normal case today: the window is 2024. That is a vacuous pass,
-- not a broken test. It becomes load bearing the moment anyone backfills far
-- enough to reach either boundary, which is exactly when a coalesce to zero
-- somewhere upstream would otherwise go unnoticed.

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
    or (source_period < date '2022-01-01' and airport_fee is not null)
