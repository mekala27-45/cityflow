-- The tip rule, stated as a test. tip_pct exists only where the payment channel
-- records a tip, which is card and in app payment.
--
-- If this fails, a cash, disputed or no charge trip has acquired a tip
-- percentage. Every one of them would be zero, every average tip rate on the
-- dashboard would drop by roughly a third, and the number would look plausible
-- while being wrong. That is the failure mode this project exists to avoid.

select
    trip_id,
    payment_type,
    tip_amount,
    tip_pct
from {{ ref('fct_trip') }}
where tip_is_observable = false and tip_pct is not null
