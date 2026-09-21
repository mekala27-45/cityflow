"""Map a file of any vintage onto one canonical trip row.

The rule that shapes this module: a column a vintage does not have becomes
NULL, never zero. Congestion surcharge did not exist before February 2019.
Writing zero there would say "we charged nothing", the series would show a flat
line running into a step, and somebody would eventually explain that step in a
slide. NULL says "not collected", which is what happened.

The second rule: the payment channel travels with the row. Whether a recorded
tip of zero is a zero or a missing value depends on it, and deciding that at
query time, in six different places, is how six different tip rates get
published.
"""

from __future__ import annotations

from pathlib import Path

from cityflow_core.config import SourceSpec
from cityflow_ingest.vintage import HVFHS_OPERATORS, PAYMENT_TYPES, Vintage

# The canonical staging schema, in order. Everything downstream reads this and
# nothing downstream reads a vintage column name.
CANONICAL_COLUMNS: tuple[str, ...] = (
    "trip_id",
    "service",
    "vintage",
    "source_period",
    "operator",
    "pickup_ts",
    "dropoff_ts",
    "pu_zone_id",
    "do_zone_id",
    "passenger_count",
    "trip_distance",
    "ratecode_id",
    "payment_type",
    "tip_is_observable",
    "fare_amount",
    "extra",
    "mta_tax",
    "improvement_surcharge",
    "congestion_surcharge",
    "airport_fee",
    "cbd_congestion_fee",
    "tip_amount",
    "tolls_amount",
    "total_amount",
)

# Yellow and green record the technology provider, not the fleet. Two codes
# cover almost the whole history, two more appear from 2024.
VENDORS: dict[int, str] = {
    1: "Creative Mobile",
    2: "Curb",
    6: "Myle",
    7: "Helix",
}


def _nullable(column: str | None, cast: str) -> str:
    """A column expression, or a typed NULL when the vintage lacks the column."""
    if column is None:
        return f"cast(null as {cast})"
    return f"cast({column} as {cast})"


def _operator_expression(vintage: Vintage) -> str:
    if vintage.service == "fhvhv":
        cases = "\n            ".join(
            f"when '{code}' then '{name}'" for code, name in sorted(HVFHS_OPERATORS.items())
        )
        return f"""case trim(cast({vintage.operator_column} as varchar))
            {cases}
            else 'Unknown operator'
        end"""
    cases = "\n            ".join(
        f"when {code} then '{name}'" for code, name in sorted(VENDORS.items())
    )
    return f"""case cast({vintage.operator_column} as integer)
            {cases}
            else 'Unknown vendor'
        end"""


def _payment_expression(vintage: Vintage) -> str:
    """Payment channel as a label, with the fhvhv case stated rather than guessed.

    For hire vehicle trips carry no payment type column because every one of
    them is settled in the app. That is not a missing value, it is a constant,
    and it is the reason a for hire tip of zero is a real zero while a yellow
    cash tip of zero is not.
    """
    if not vintage.has_payment_type:
        return "'app'"
    cases = "\n            ".join(
        f"when {code} then '{label}'" for code, label in sorted(PAYMENT_TYPES.items())
    )
    return f"""case cast(payment_type as integer)
            {cases}
            else 'unknown'
        end"""


def _total_expression(vintage: Vintage) -> str:
    """The rider's total.

    Yellow and green publish it. The for hire files publish only the parts, so
    the total is summed from them: base fare, tolls, black car fund, sales tax,
    congestion surcharge, airport fee and tips. Missing parts are treated as
    zero *inside this sum only*, because a component that a vintage does not
    collect genuinely contributed nothing to what the rider paid, which is not
    the same claim as saying the surcharge itself was zero.
    """
    if vintage.has_total_amount:
        return "cast(total_amount as double)"
    parts = [
        f"cast({vintage.fare_column} as double)",
        f"coalesce(cast({vintage.tolls_column} as double), 0)",
        f"coalesce(cast({vintage.tip_column} as double), 0)",
    ]
    if vintage.has_black_car_fund:
        parts.append("coalesce(cast(bcf as double), 0)")
    if vintage.has_sales_tax:
        parts.append("coalesce(cast(sales_tax as double), 0)")
    if vintage.has_congestion_surcharge:
        parts.append("coalesce(cast(congestion_surcharge as double), 0)")
    if vintage.has_airport_fee:
        parts.append("coalesce(cast(airport_fee as double), 0)")
    if vintage.has_cbd_congestion_fee:
        parts.append("coalesce(cast(cbd_congestion_fee as double), 0)")
    return " + ".join(parts)


def normalize_select(
    source: SourceSpec,
    vintage: Vintage,
    parquet_path: Path | str,
    *,
    trip_id_offset: int = 0,
) -> str:
    """The SELECT that turns one vintage file into canonical rows.

    trip_id is assigned here rather than by the warehouse so a row keeps its
    identity across a rebuild of a single month. The offset is derived from the
    service and period, not from a global counter, so months can be rebuilt in
    any order and in parallel without colliding.
    """
    if vintage.is_coordinate_era:
        raise ValueError(
            f"{vintage.name} predates zone ids and carries raw coordinates. "
            "Joining it to a taxi zone needs a spatial join against the "
            "shapefile, which this project does not do. Restrict the build "
            "window to July 2016 onwards."
        )

    payment = _payment_expression(vintage)
    observable_labels = "', '".join(sorted({"card", "app"}))

    return f"""
select
    {trip_id_offset} + cast(row_number() over () as bigint)  as trip_id,
    '{source.service}'                                        as service,
    '{vintage.name}'                                          as vintage,
    date '{source.period:%Y-%m-%d}'                           as source_period,
    {_operator_expression(vintage)}                           as operator,
    cast({vintage.pickup_column} as timestamp)                as pickup_ts,
    cast({vintage.dropoff_column} as timestamp)               as dropoff_ts,
    cast({vintage.pu_zone_column} as smallint)                as pu_zone_id,
    cast({vintage.do_zone_column} as smallint)                as do_zone_id,
    {_nullable("passenger_count" if vintage.has_passenger_count else None, "smallint")}
                                                              as passenger_count,
    cast({vintage.distance_column} as double)                 as trip_distance,
    {_nullable("RatecodeID" if vintage.has_ratecode else None, "smallint")}
                                                              as ratecode_id,
    {payment}                                                 as payment_type,
    ({payment}) in ('{observable_labels}')                     as tip_is_observable,
    cast({vintage.fare_column} as double)                     as fare_amount,
    {_nullable("extra" if vintage.has_extra else None, "double")}
                                                              as extra,
    {_nullable("mta_tax" if vintage.has_mta_tax else None, "double")}
                                                              as mta_tax,
    {
        _nullable("improvement_surcharge" if vintage.has_improvement_surcharge else None, "double")
    }                                                        as improvement_surcharge,
    {
        _nullable("congestion_surcharge" if vintage.has_congestion_surcharge else None, "double")
    }                                                        as congestion_surcharge,
    {_nullable("airport_fee" if vintage.has_airport_fee else None, "double")}
                                                              as airport_fee,
    {
        _nullable("cbd_congestion_fee" if vintage.has_cbd_congestion_fee else None, "double")
    }                                                        as cbd_congestion_fee,
    cast({vintage.tip_column} as double)                      as tip_amount,
    cast({vintage.tolls_column} as double)                    as tolls_amount,
    {_total_expression(vintage)}                              as total_amount
from read_parquet('{parquet_path}')
"""


def trip_id_offset(source: SourceSpec) -> int:
    """A disjoint id range per service and month.

    Service index times 10^14, plus year and month times 10^9. Every real month
    is well under a billion rows, so the ranges cannot meet. This is worth the
    arithmetic because the alternative, a sequence, makes two months of the
    same build non reproducible and makes a rebuild of one month renumber every
    row after it.
    """
    service_index = {"yellow": 1, "green": 2, "fhvhv": 3}[source.service]
    return service_index * 100_000_000_000_000 + (source.year * 100 + source.month) * 1_000_000_000


def schema_difference(
    actual: set[str], expected: set[str], ignored: set[str]
) -> tuple[set[str], set[str]]:
    """What the file is missing, and what it has that we did not plan for.

    Returned separately because they are different failures. A missing column
    means the vintage table is wrong about this month. An unexpected column
    means the TLC added something, which is information, not an error.
    """
    missing = expected - actual
    unexpected = actual - expected - ignored
    return missing, unexpected
