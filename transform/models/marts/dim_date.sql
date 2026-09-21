-- The spine is generated from the fact, not from a hardcoded range, so a build
-- window that grows cannot leave the date dimension short and quietly drop the
-- new months out of every date joined report.
--
-- The holidays come from the holiday seed rather than a case expression in this
-- file. A holiday list is data: it changes every year, Juneteenth was added in
-- 2021, and the observed date of a fixed holiday moves with the weekday. Buried
-- in a case expression none of that is reviewable, and nobody finds it when the
-- next year has to be added.

with bounds as (

    select
        min(pickup_date) as first_day,
        max(pickup_date) as last_day
    from {{ ref('stg_trips') }}

),

series as (

    select unnest(generate_series(first_day, last_day, interval 1 day)) as day_ts
    from bounds

),

spine as (

    select cast(day_ts as date) as date_day
    from series

),

holidays as (

    select
        holiday_date,
        holiday_name
    from {{ ref('holiday') }}

)

select
    spine.date_day,
    holidays.holiday_name,
    cast(year(spine.date_day) as smallint) as year,
    cast(quarter(spine.date_day) as smallint) as quarter,
    cast(month(spine.date_day) as smallint) as month,
    monthname(spine.date_day) as month_name,
    cast(week(spine.date_day) as smallint) as week_of_year,
    cast(day(spine.date_day) as smallint) as day_of_month,
    cast(dayofweek(spine.date_day) as smallint) as day_of_week,
    dayname(spine.date_day) as day_name,
    dayofweek(spine.date_day) in (0, 6) as is_weekend,
    holidays.holiday_date is not null as is_holiday,
    case
        when dayofweek(spine.date_day) in (0, 6) then 'weekend'
        else 'weekday'
    end as day_type
from spine
left join holidays on spine.date_day = holidays.holiday_date
