-- Every pickup date has to exist in the date dimension. A missing day is not a
-- visible error: an inner join to dim_date simply returns fewer trips, and the
-- day disappears from the daily series as though no one travelled.
--
-- dim_date is generated from the fact, so this can only fail if the two were
-- built from different states of the warehouse, which is the case after an
-- incremental run that added a month the dimension has not been rebuilt for.

select
    fact.pickup_date,
    count(*) as trips
from {{ ref('fct_trip') }} as fact
left join {{ ref('dim_date') }} as dates on fact.pickup_date = dates.date_day
where dates.date_day is null
group by fact.pickup_date
