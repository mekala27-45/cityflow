-- The fact is an append of whole months, so it has to hold exactly the trips
-- staging holds. Nothing here aggregates and nothing filters.
--
-- This is the test that catches an incremental predicate losing a month. The
-- symptom of that failure is not an error: it is a chart that is simply lower
-- than it should be for one month, which nobody notices until someone asks why
-- March was quiet. It also catches the opposite, a predicate that reloads a
-- month it already holds and doubles it.

with staged as (

    select count(*) as trips from {{ ref('stg_trips') }}

),

built as (

    select count(*) as trips from {{ ref('fct_trip') }}

)

select
    staged.trips as staged_trips,
    built.trips as fact_trips
from staged
cross join built
where staged.trips != built.trips
