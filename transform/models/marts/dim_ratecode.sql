-- Member 0 is ours, not the TLC's. For hire vehicle trips record no rate code at
-- all, and a null foreign key is a row that drops out of every rate code
-- breakdown without anyone being told. Mapping those trips to an explicit "not
-- applicable" member keeps them in the total and turns the missing rate code
-- into something a reader can see.
--
-- Member 99 is the TLC's own unknown code. fct_trip also folds any code outside
-- 1 to 6 into it, so the fact can never point at a member that is not here.

with members as (

    select
        t.ratecode_id,
        t.ratecode_label,
        t.is_flat_rate
    from (
        values
        (0, 'Not applicable', false),
        (1, 'Standard rate', false),
        (2, 'JFK flat rate', true),
        (3, 'Newark', true),
        (4, 'Nassau or Westchester', false),
        (5, 'Negotiated fare', false),
        (6, 'Group ride', false),
        (99, 'Unknown', false)
    ) as t (ratecode_id, ratecode_label, is_flat_rate)

)

select
    cast(ratecode_id as smallint) as ratecode_id,
    ratecode_label,
    is_flat_rate
from members
