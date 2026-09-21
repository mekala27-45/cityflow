-- The daypart boundaries are the ones the TLC and the MTA use for surcharge and
-- service planning, so a chart cut this way lines up with the published rules
-- rather than with a round number someone liked. is_peak is derived from the
-- daypart rather than restated, so the two can never disagree.

with hours as (

    select unnest(generate_series(0, 23)) as hour_of_day

),

labelled as (

    select
        cast(hour_of_day as smallint) as hour,
        lpad(cast(hour_of_day as varchar), 2, '0') || ':00' as hour_label,
        case
            when hour_of_day between 0 and 5 then 'overnight'
            when hour_of_day between 6 and 9 then 'morning peak'
            when hour_of_day between 10 and 15 then 'midday'
            when hour_of_day between 16 and 19 then 'evening peak'
            else 'evening'
        end as daypart
    from hours

)

select
    hour,
    hour_label,
    daypart,
    daypart in ('morning peak', 'evening peak') as is_peak
from labelled
