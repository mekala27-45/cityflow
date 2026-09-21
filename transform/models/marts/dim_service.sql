-- Three rows, written out rather than selected distinct from the fact. A
-- dimension built from whatever happens to be in the data cannot show that a
-- service has stopped appearing, which is the one thing a service list is for.

with members as (

    select
        t.service,
        t.service_label,
        t.service_description
    from (
        values
        ('yellow', 'Yellow Medallion Taxi', 'Street hail medallion cab, licensed citywide.'),
        ('green', 'Green Boro Taxi', 'Street hail licensed outside the Manhattan core.'),
        ('fhvhv', 'High Volume For Hire Vehicle', 'Uber, Lyft and Via. Prearranged only.')
    ) as t (service, service_label, service_description)

)

select
    service,
    service_label,
    service_description
from members
