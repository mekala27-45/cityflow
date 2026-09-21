"""The shipped layer: what the browser is allowed to reach.

The detail lives in the warehouse. The browser queries conformed aggregates.
This is not a workaround for a static host, it is how BI works, and the reason
is worth stating plainly because it is the question this project gets asked.

A dashboard does not need two hundred million rows. It needs the additive
components of every published metric, at the finest grain any filter in the
interface can reach, and nothing else. Zone by hour by day type by month by
service is that grain here. It is four hundred thousand rows rather than two
hundred million, it answers every question the panels ask, and it answers them
in under a hundred milliseconds over HTTP range requests.

Two design rules shape every builder below.

**Publish components, not metrics.** No aggregate contains a rate. It contains
the numerator and the denominator, and the metric layer defines the division.
A published rate cannot be re-aggregated: averaging the tip rates of two zones
gives the wrong answer for both together, and somebody always does it. Sums can
be added. That is the whole reason this layer is built out of sums.

**Sort on what the filter uses.** A parquet row group is the unit of both
pruning and the HTTP range request. Sorting each file on the column it is most
filtered by is what turns "scan the file" into "fetch two row groups", and it
is why the bytes pulled column in the benchmark table is small enough to be
interesting.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from cityflow_core.config import CityflowConfig
from cityflow_core.paths import Paths
from cityflow_publish.metric_layer import MetricLayer

# Row group size. Smaller groups prune more finely and cost more metadata.
# 65,536 puts a zone and hour slice inside one or two groups at this grain,
# which is what makes a filtered query a two range fetch rather than a scan.
AGGREGATE_ROW_GROUP = 65_536
DETAIL_ROW_GROUP = 122_880

# The hexbin lattice for the fare against distance chart, in data units:
# miles on x, dollars on y. Chosen so the JFK flat fare lands inside one band
# of rows rather than straddling two, which is what makes the flat rate bands
# visible as bands.
HEX_SIZE_MILES = 0.35
HEX_SIZE_DOLLARS = 1.75
HEX_MAX_MILES = 30.0
HEX_MAX_DOLLARS = 140.0

# Duration histogram: one minute buckets to an hour, then a single overflow.
DURATION_BUCKET_SECONDS = 60
DURATION_MAX_MINUTES = 60


@dataclass(frozen=True, slots=True)
class AggregateResult:
    """One shipped file, measured rather than estimated."""

    name: str
    path: Path
    rows: int
    bytes: int
    source_rows: int
    sort_key: str
    note: str = ""

    @property
    def megabytes(self) -> float:
        return self.bytes / 1_000_000

    @property
    def compression_ratio(self) -> float:
        """Source rows summarized per byte shipped, as a plain ratio of rows."""
        return self.source_rows / self.rows if self.rows else 0.0


# The additive components, written once. Every aggregate that claims to support
# the full metric catalog emits exactly this list, and `verify_components`
# checks that claim against the metric layer rather than trusting it.
COMPONENT_SQL: dict[str, str] = {
    "trips": "count(*)",
    "total_amount_sum": "sum(total_amount)",
    "fare_amount_sum": "sum(fare_amount)",
    "trip_distance_sum": "sum(trip_distance)",
    "duration_s_sum": "sum(duration_s)",
    "congestion_surcharge_sum": "sum(congestion_surcharge)",
    "tip_obs_trips": "count(*) filter (where tip_is_observable)",
    "tip_obs_tip_sum": "sum(tip_amount) filter (where tip_is_observable)",
    "tip_obs_fare_sum": "sum(fare_amount) filter (where tip_is_observable)",
    "tipped_trips": "count(*) filter (where tip_is_observable and tip_amount > 0)",
    "airport_trips": "count(*) filter (where is_airport_trip)",
    "unknown_zone_trips": "count(*) filter (where not has_known_geography)",
    "card_trips": "count(*) filter (where payment_type = 'card')",
    "metered_trips": "count(*) filter (where service in ('yellow', 'green'))",
    "flat_rate_trips": "count(*) filter (where ratecode_id in (2, 3))",
    "passenger_count_sum": "sum(passenger_count)",
    "passenger_trips": "count(*) filter (where passenger_count is not null)",
}


def component_select(components: Sequence[str]) -> str:
    missing = [c for c in components if c not in COMPONENT_SQL]
    if missing:
        raise KeyError(
            f"No SQL for component(s) {missing}. Add them to COMPONENT_SQL, or "
            "the metric that needs them will return nulls in the interface."
        )
    return ",\n    ".join(f"{COMPONENT_SQL[c]} as {c}" for c in components)


def verify_components(layer: MetricLayer) -> tuple[str, ...]:
    """Every component the catalog needs, checked against what we can emit."""
    required = layer.required_components()
    missing = [c for c in required if c not in COMPONENT_SQL]
    if missing:
        raise KeyError(
            f"metrics.yml needs component(s) {missing} that the aggregate "
            "builders cannot produce. Either add the SQL or fix the metric."
        )
    return required


def _write(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    path: Path,
    *,
    row_group: int,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"copy ({sql}) to '{path}' (format parquet, compression zstd, row_group_size {row_group})"
    )
    row = connection.execute(f"select count(*) from read_parquet('{path}')").fetchone()
    return int(row[0]) if row else 0


def _fact_rows(connection: duckdb.DuckDBPyConnection) -> int:
    row = connection.execute("select count(*) from fct_trip").fetchone()
    return int(row[0]) if row else 0


def build_zone_hour(
    connection: duckdb.DuckDBPyConnection,
    paths: Paths,
    components: Sequence[str],
    fact_rows: int,
) -> AggregateResult:
    """Zone by hour by day type by month by service, carrying every component.

    This is the workhorse. Every KPI tile, the hour of week heatmap, the
    choropleth, the Pareto and the zone ranking all read it, which is the point:
    one conformed aggregate rather than five bespoke extracts that drift.
    """
    path = paths.shipped / "agg_zone_hour.parquet"
    sql = f"""
    select
        date_trunc('month', f.pickup_date)::date as month,
        f.service,
        f.pu_zone_id,
        f.pickup_hour as hour,
        d.day_type,
        {component_select(components)}
    from fct_trip f
    join dim_date d on d.date_day = f.pickup_date
    group by 1, 2, 3, 4, 5
    order by f.service, f.pu_zone_id, f.pickup_hour, month
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, pu_zone_id, hour, month",
        note="Carries every additive component in the metric catalog.",
    )


def build_hour_of_week(
    connection: duckdb.DuckDBPyConnection,
    paths: Paths,
    components: Sequence[str],
    fact_rows: int,
) -> AggregateResult:
    """Day of week by hour, by borough, month and service.

    This exists because the hour of week heatmap is a hundred and sixty eight
    measured cells or it is decoration. agg_zone_hour carries day type, which
    is two shapes for seven days, and reconstructing Tuesday from a weekday
    average and a daily total gives a grid where five rows are identical by
    construction. A reader cannot tell that from looking, which is exactly why
    it cannot ship.

    Borough rather than zone, because the heatmap's own filter is borough and
    the zone grain would multiply this file by forty for a chart that never
    shows a zone.
    """
    path = paths.shipped / "agg_hour_of_week.parquet"
    sql = f"""
    select
        date_trunc('month', f.pickup_date)::date as month,
        f.service,
        z.borough,
        d.day_of_week,
        d.day_name,
        f.pickup_hour as hour,
        {component_select(components)}
    from fct_trip f
    join dim_date d on d.date_day = f.pickup_date
    join dim_zone z on z.zone_id = f.pu_zone_id
    group by 1, 2, 3, 4, 5, 6
    order by f.service, z.borough, d.day_of_week, f.pickup_hour, month
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, borough, day_of_week, hour, month",
        note="The measured 168 cell grid. Borough grain, not zone.",
    )


def build_daily(
    connection: duckdb.DuckDBPyConnection,
    paths: Paths,
    components: Sequence[str],
    fact_rows: int,
) -> AggregateResult:
    """One row per day per service, for the trend, the STL layers and the KPIs."""
    path = paths.shipped / "agg_daily.parquet"
    sql = f"""
    select
        f.pickup_date as date_day,
        f.service,
        d.day_type,
        d.is_holiday,
        d.holiday_name,
        {component_select(components)}
    from fct_trip f
    join dim_date d on d.date_day = f.pickup_date
    group by 1, 2, 3, 4, 5
    order by f.service, f.pickup_date
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, date_day",
    )


def build_od_flow(
    connection: duckdb.DuckDBPyConnection,
    paths: Paths,
    fact_rows: int,
    *,
    min_trips: int = 1,
) -> AggregateResult:
    """Origin to destination, by month and service.

    Unknown zones are excluded here and only here, because this file is
    geographic by construction: a flow from "unknown" to "unknown" is not a
    flow. The share of trips that exclusion removes is published as its own
    metric so the omission is visible rather than silent.
    """
    path = paths.shipped / "agg_od_flow.parquet"
    sql = f"""
    select
        date_trunc('month', pickup_date)::date as month,
        service,
        pu_zone_id,
        do_zone_id,
        count(*) as trips,
        sum(total_amount) as total_amount_sum,
        sum(trip_distance) as trip_distance_sum,
        sum(duration_s) as duration_s_sum
    from fct_trip
    where has_known_geography
    group by 1, 2, 3, 4
    having count(*) >= {min_trips}
    order by service, pu_zone_id, do_zone_id, month
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, pu_zone_id, do_zone_id, month",
        note="Excludes unresolved zones; see the unknown_zone_share metric.",
    )


def build_duration_distribution(
    connection: duckdb.DuckDBPyConnection, paths: Paths, fact_rows: int
) -> AggregateResult:
    """Duration histogram by hour and service, for the ridgeline."""
    path = paths.shipped / "agg_duration_dist.parquet"
    sql = f"""
    select
        service,
        pickup_hour as hour,
        least(
            {DURATION_MAX_MINUTES},
            floor(duration_s / {DURATION_BUCKET_SECONDS})
        )::smallint as minute_bucket,
        count(*) as trips
    from fct_trip
    group by 1, 2, 3
    order by service, hour, minute_bucket
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, hour, minute_bucket",
        note=(
            f"One minute buckets to {DURATION_MAX_MINUTES} minutes, then one "
            "overflow bucket, which is labelled as such in the chart."
        ),
    )


def _hexbin_sql(x: str, y: str, size_x: float, size_y: float) -> tuple[str, str]:
    """Axial hex coordinates for a point, in the flat topped orientation.

    Written out rather than pulled from a library because it is twelve lines
    and because a chart whose binning nobody can read is a chart nobody can
    check. The lattice is scaled independently on each axis, since miles and
    dollars are not comparable and a geometrically regular hexagon in data
    space would be a very tall one on screen.
    """
    sqrt3 = math.sqrt(3.0)
    q = f"((2.0 / 3.0) * ({x} / {size_x}))"
    r = f"((-1.0 / 3.0) * ({x} / {size_x}) + ({sqrt3} / 3.0) * ({y} / {size_y}))"
    return q, r


def build_fare_distance(
    connection: duckdb.DuckDBPyConnection, paths: Paths, fact_rows: int
) -> AggregateResult:
    """Hexbinned fare against distance, with the flat rate bands intact."""
    path = paths.shipped / "agg_fare_distance.parquet"
    q, r = _hexbin_sql("trip_distance", "fare_amount", HEX_SIZE_MILES, HEX_SIZE_DOLLARS)
    sql = f"""
    with bounded as (
        select *
        from fct_trip
        where trip_distance <= {HEX_MAX_MILES}
          and fare_amount <= {HEX_MAX_DOLLARS}
    ),
    axial as (
        select
            service,
            ratecode_id,
            round({q})::smallint as hex_q,
            round({r})::smallint as hex_r,
            trip_distance,
            fare_amount
        from bounded
    )
    select
        service,
        hex_q,
        hex_r,
        count(*) as trips,
        avg(trip_distance) as mean_distance_mi,
        avg(fare_amount) as mean_fare,
        count(*) filter (where ratecode_id in (2, 3)) as flat_rate_trips
    from axial
    group by 1, 2, 3
    order by service, hex_q, hex_r
    """
    rows = _write(connection, sql, path, row_group=AGGREGATE_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=fact_rows,
        sort_key="service, hex_q, hex_r",
        note=(
            f"Clipped at {HEX_MAX_MILES:.0f} miles and "
            f"${HEX_MAX_DOLLARS:.0f}; the clipped share is published."
        ),
    )


def build_detail_month(
    connection: duckdb.DuckDBPyConnection,
    config: CityflowConfig,
    paths: Paths,
) -> AggregateResult:
    """One month at trip grain, so drill down reaches an actual row.

    Yellow and green only. A month of for hire records is nineteen million
    rows and does not fit inside the ninety five megabyte per file budget, and
    splitting it across eight files to get under a per file limit would be
    dodging the budget rather than meeting it. The interface says which
    services the detail covers rather than letting a reader assume.

    Dropoff timestamp, total amount and implied speed are not shipped: each is
    an exact function of columns that are, and at three million rows those
    three columns are seventeen megabytes of the budget. The page derives them
    for display. They are display fields, not metrics, so deriving them in the
    page does not cross the metric layer boundary, and the enforcement test is
    scoped accordingly.
    """
    month = config.detail_month
    path = paths.shipped / f"detail_{month:%Y_%m}.parquet"
    sql = f"""
    select
        trip_id,
        service,
        pickup_ts,
        duration_s,
        pu_zone_id,
        do_zone_id,
        trip_distance,
        fare_amount,
        tip_amount,
        payment_type,
        tip_is_observable,
        tip_pct,
        ratecode_id,
        is_airport_trip
    from fct_trip
    where service in ('yellow', 'green')
      and pickup_date >= date '{month:%Y-%m-%d}'
      and pickup_date < date '{_next_month(month):%Y-%m-%d}'
    order by pu_zone_id, pickup_ts
    """
    rows = _write(connection, sql, path, row_group=DETAIL_ROW_GROUP)
    return AggregateResult(
        name=path.name,
        path=path,
        rows=rows,
        bytes=path.stat().st_size,
        source_rows=rows,
        sort_key="pu_zone_id, pickup_ts",
        note="Yellow and green only. See the docstring for why.",
    )


def build_reference(
    connection: duckdb.DuckDBPyConnection, paths: Paths, fact_rows: int
) -> list[AggregateResult]:
    """The dimensions and the data health marts, shipped as they are."""
    out: list[AggregateResult] = []
    for table, sort in (
        ("dim_zone", "zone_id"),
        ("dim_date", "date_day"),
        ("dim_hour", "hour"),
        ("dim_service", "service"),
        ("dim_ratecode", "ratecode_id"),
        ("mart_quarantine", "service, source_period, rule"),
        ("mart_source_freshness", "service, source_period"),
        ("mart_null_rates", "service, source_period, column_name"),
    ):
        path = paths.shipped / f"{table}.parquet"
        rows = _write(
            connection,
            f"select * from {table} order by {sort}",
            path,
            row_group=AGGREGATE_ROW_GROUP,
        )
        out.append(
            AggregateResult(
                name=path.name,
                path=path,
                rows=rows,
                bytes=path.stat().st_size,
                source_rows=fact_rows if table.startswith("mart_") else rows,
                sort_key=sort,
            )
        )
    return out


def build_all(
    connection: duckdb.DuckDBPyConnection,
    config: CityflowConfig,
    paths: Paths,
) -> list[AggregateResult]:
    """Build every shipped file and check the size budget before returning."""
    layer = MetricLayer.load(paths.metrics_file)
    components = verify_components(layer)
    fact_rows = _fact_rows(connection)

    results = [
        build_zone_hour(connection, paths, components, fact_rows),
        build_hour_of_week(connection, paths, components, fact_rows),
        build_daily(connection, paths, components, fact_rows),
        build_od_flow(connection, paths, fact_rows),
        build_duration_distribution(connection, paths, fact_rows),
        build_fare_distance(connection, paths, fact_rows),
        build_detail_month(connection, config, paths),
        *build_reference(connection, paths, fact_rows),
    ]

    over = [r for r in results if r.megabytes > config.shipped_file_budget_mb]
    if over:
        names = ", ".join(f"{r.name} at {r.megabytes:.1f}MB" for r in over)
        raise RuntimeError(
            f"Shipped files over the {config.shipped_file_budget_mb:.0f}MB "
            f"per file budget: {names}. GitHub refuses a file over 100MB, so "
            "this is a hard limit, not a preference. Coarsen the grain or "
            "narrow the window."
        )
    total = sum(r.megabytes for r in results)
    if total > config.shipped_total_budget_mb:
        raise RuntimeError(
            f"Shipped layer is {total:.1f}MB against a "
            f"{config.shipped_total_budget_mb:.0f}MB budget."
        )
    return results


def _next_month(month: dt.date) -> dt.date:
    return dt.date(month.year + month.month // 12, month.month % 12 + 1, 1)
