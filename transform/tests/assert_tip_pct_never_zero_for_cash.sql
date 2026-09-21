-- The same rule from the other side, aimed at the specific mistake: coalescing
-- an unobservable tip to zero instead of leaving it null.
--
-- A cash meter records zero because no tip passed through it, not because the
-- passenger left nothing. A zero here is a missing value wearing a number, and
-- it is indistinguishable from a real zero once it is in an average.

select
    trip_id,
    payment_type,
    tip_amount,
    tip_pct
from {{ ref('fct_trip') }}
where payment_type = 'cash' and tip_pct = 0
