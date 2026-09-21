"""The whole pipeline, run for real in a scratch tree.

This is the test the rest of the suite exists around. It generates two months
of source data through the seeded generator, ingests them through the real
registry, normalizer and quarantine rules, builds the marts, publishes the
shipped layer, reconciles it against the warehouse, and writes the manifest.
Nothing is stubbed except dbt, and that substitution is marked where it happens.

Four properties are asserted that nothing smaller can reach:

1. The balance identity. Source rows equal clean rows plus the per rule
   quarantine counts, exactly, for every month. If a row goes missing without a
   rule claiming it, the pipeline cannot say where its input went.
2. The rules partition the rejected set. First match wins, so the per rule
   counts sum to the quarantined total, and no row that a rule would catch is
   sitting in the clean table.
3. The tip semantics survive the whole trip from the generator to the fact
   table, which is the one finding this project is built to get right.
4. Reconcile is not vacuous. A metric is broken on purpose and reconcile has to
   name it. That last one is the most important assertion in this file: without
   it, "the shipped layer agrees with the warehouse" is a claim about a check
   that has never been observed to fail.

The window spans December 2021 and January 2022 deliberately, for the same
reason the committed config does: the airport fee column appears in the yellow
and for hire files that month and not before, so two of the three services
cross a vintage boundary inside a two month window and the per vintage mapping
is exercised rather than assumed. A window sitting inside one vintage proves
nothing about the vintage table.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bench_queries
import duckdb
import pytest
import yaml
from typer.testing import CliRunner

from cityflow.cli import app
from cityflow_core.config import CityflowConfig, load_config
from cityflow_core.duck import connect
from cityflow_core.paths import Paths, repo_root
from cityflow_ingest.pipeline import IngestReport, MonthReport, run_ingest
from cityflow_ingest.quarantine import quarantine_case, rules_for
from cityflow_ingest.synthetic import holidays
from cityflow_ingest.vintage import resolve_vintage
from cityflow_publish.aggregates import build_all
from cityflow_publish.analysis import run_analysis, write_lineage
from cityflow_publish.manifest import build_manifest
from cityflow_publish.reconcile import reconcile_all

REPO = Path(__file__).resolve().parent.parent

# Small enough to run in a unit test, large enough that a defect injected at a
# rate of one in ten thousand still lands often enough to be counted.
SCALE = 0.01

CONFIG_YAML = """
backend: synthetic
services: [yellow, green, fhvhv]
start: 2021-12-01
end: 2022-01-01
detail_month: 2021-12-01
duckdb_memory_limit: 1GB
duckdb_threads: 2
shipped_file_budget_mb: 95.0
shipped_total_budget_mb: 400.0
"""

# Which quarantine rule each configured injection rate is expected to land in.
# unknown_zone is deliberately absent: an unresolved zone is not a defect, the
# trip is real and it is kept, flagged, and excluded from geographic aggregates
# only. A rule by that name appearing here would be the bug.
DEFECT_TO_RULE: dict[str, str] = {
    "timestamp_out_of_period": "timestamp_out_of_period",
    "dropoff_before_pickup": "dropoff_before_pickup",
    "non_positive_distance": "non_positive_distance",
    "non_positive_fare": "non_positive_fare",
    "implausible_speed": "implausible_speed",
    "passenger_count_zero": "passenger_count_invalid",
    "exact_duplicate": "exact_duplicate",
}

# Rules that the generator cannot produce. A count here is a rule firing on
# something nobody injected, which is worth knowing about.
STRUCTURALLY_IMPOSSIBLE = ("null_timestamp", "negative_component")


@dataclass(slots=True)
class Built:
    """The scratch tree, after a full build."""

    root: Path
    paths: Paths
    config: CityflowConfig
    connection: duckdb.DuckDBPyConnection
    report: IngestReport
    progress: list[MonthReport]


# ---------------------------------------------------------------------------
# The stand in for dbt
# ---------------------------------------------------------------------------

# dbt is far too heavy for a unit test, so the handful of tables the publish
# step reads are built here directly from stg_trip, matching the column
# contract in transform/models/marts/ model for model. This is a STAND IN, not
# a second implementation: `dbt build` is covered by the CI pipeline job, and
# the dbt models remain the only thing that builds the committed warehouse.
# If these two ever disagree, the dbt models are right and this is wrong.

MARTS_SQL = """
create or replace table dim_zone as
select
    cast(zone_id as smallint)       as zone_id,
    zone,
    borough,
    service_zone,
    cast(is_unknown as boolean)     as is_unknown,
    cast(is_airport as boolean)     as is_airport,
    cast(has_geometry as boolean)   as has_geometry,
    cast(part_count as smallint)    as part_count,
    centroid_lon,
    centroid_lat,
    area_sq_mi
from read_csv_auto('{zone_csv}');

create or replace table fct_trip as
with trips as (
    select
        *,
        cast(pickup_ts as date)                              as pickup_date,
        hour(pickup_ts)                                      as pickup_hour,
        date_diff('second', pickup_ts, dropoff_ts)           as duration_s,
        trip_distance
            / nullif(date_diff('second', pickup_ts, dropoff_ts), 0) * 3600
                                                             as implied_speed_mph
    from stg_trip
),
located as (
    select
        trips.*,
        coalesce(pickup_zone.is_unknown, true)               as pu_is_unknown,
        coalesce(dropoff_zone.is_unknown, true)              as do_is_unknown,
        coalesce(pickup_zone.is_airport, false)
            or coalesce(dropoff_zone.is_airport, false)      as is_airport_trip
    from trips
    left join dim_zone as pickup_zone on trips.pu_zone_id = pickup_zone.zone_id
    left join dim_zone as dropoff_zone on trips.do_zone_id = dropoff_zone.zone_id
)
select
    trip_id, service, vintage, operator, source_period,
    pickup_ts, dropoff_ts, pickup_date, pickup_hour,
    pu_zone_id, do_zone_id, payment_type, tip_is_observable,
    passenger_count, trip_distance, duration_s, implied_speed_mph,
    fare_amount, tip_amount, tolls_amount, extra, mta_tax,
    improvement_surcharge, congestion_surcharge, airport_fee,
    cbd_congestion_fee, total_amount,
    pu_is_unknown, do_is_unknown, is_airport_trip,
    not pu_is_unknown and not do_is_unknown                  as has_known_geography,
    coalesce(extra, 0) + coalesce(mta_tax, 0)
        + coalesce(improvement_surcharge, 0)
        + coalesce(congestion_surcharge, 0)
        + coalesce(airport_fee, 0)
        + coalesce(cbd_congestion_fee, 0)                    as surcharge_total,
    case when tip_is_observable then tip_amount / nullif(fare_amount, 0) end as tip_pct,
    cast(
        case
            when ratecode_id is null then 0
            when ratecode_id between 1 and 6 then ratecode_id
            else 99
        end as smallint
    )                                                        as ratecode_id
from located;

create or replace table dim_date as
with bounds as (
    select min(pickup_date) as first_day, max(pickup_date) as last_day from fct_trip
),
spine as (
    select cast(unnest(generate_series(first_day, last_day, interval 1 day)) as date) as date_day
    from bounds
),
holiday as (
    select cast(holiday_date as date) as holiday_date, holiday_name
    from read_csv_auto('{holiday_csv}')
)
select
    spine.date_day,
    holiday.holiday_name,
    cast(year(spine.date_day) as smallint)         as year,
    cast(quarter(spine.date_day) as smallint)      as quarter,
    cast(month(spine.date_day) as smallint)        as month,
    monthname(spine.date_day)                      as month_name,
    cast(week(spine.date_day) as smallint)         as week_of_year,
    cast(day(spine.date_day) as smallint)          as day_of_month,
    cast(dayofweek(spine.date_day) as smallint)    as day_of_week,
    dayname(spine.date_day)                        as day_name,
    dayofweek(spine.date_day) in (0, 6)            as is_weekend,
    holiday.holiday_date is not null               as is_holiday,
    case when dayofweek(spine.date_day) in (0, 6) then 'weekend' else 'weekday' end as day_type
from spine
left join holiday on spine.date_day = holiday.holiday_date;

create or replace table dim_hour as
select
    cast(hour_of_day as smallint)                          as hour,
    lpad(cast(hour_of_day as varchar), 2, '0') || ':00'    as hour_label,
    case
        when hour_of_day between 0 and 5 then 'overnight'
        when hour_of_day between 6 and 9 then 'morning peak'
        when hour_of_day between 10 and 15 then 'midday'
        when hour_of_day between 16 and 19 then 'evening peak'
        else 'evening'
    end                                                    as daypart,
    case
        when hour_of_day between 6 and 9 then true
        when hour_of_day between 16 and 19 then true
        else false
    end                                                    as is_peak
from (select unnest(generate_series(0, 23)) as hour_of_day);

create or replace table dim_service as
select * from (values
    ('yellow', 'Yellow Medallion Taxi', 'Street hail medallion cab, licensed citywide.'),
    ('green', 'Green Boro Taxi', 'Street hail licensed outside the Manhattan core.'),
    ('fhvhv', 'High Volume For Hire Vehicle', 'Uber, Lyft and Via. Prearranged only.')
) as t (service, service_label, service_description);

create or replace table dim_ratecode as
select cast(ratecode_id as smallint) as ratecode_id, ratecode_label, is_flat_rate
from (values
    (0, 'Not applicable', false),
    (1, 'Standard rate', false),
    (2, 'JFK flat rate', true),
    (3, 'Newark', true),
    (4, 'Nassau or Westchester', false),
    (5, 'Negotiated fare', false),
    (6, 'Group ride', false),
    (99, 'Unknown', false)
) as t (ratecode_id, ratecode_label, is_flat_rate);

create or replace table mart_quarantine as
select
    rule, service, source_period, rows, source_rows,
    cast(rows as double) / nullif(source_rows, 0) as share_of_source
from quarantine_log;

create or replace table mart_source_freshness as
select
    service, source_period, vintage, backend, source_rows, clean_rows,
    quarantined_rows, ingested_at,
    case
        when previous_loaded_period is null then false
        else date_diff('month', previous_loaded_period, source_period) > 1
    end as has_gap
from (
    select
        *,
        lag(source_period) over (
            partition by service order by source_period
        ) as previous_loaded_period
    from source_audit
);

create or replace table mart_null_rates as
with counted as (
    select
        service,
        source_period,
        count(*) as rows,
        count(*) filter (where congestion_surcharge is null) as congestion_surcharge,
        count(*) filter (where airport_fee is null)          as airport_fee,
        count(*) filter (where cbd_congestion_fee is null)   as cbd_congestion_fee,
        count(*) filter (where passenger_count is null)      as passenger_count,
        count(*) filter (where ratecode_id is null)          as ratecode_id
    from stg_trip
    group by service, source_period
),
stacked as (
    select service, source_period, rows, 'congestion_surcharge' as column_name,
           congestion_surcharge as null_rows from counted
    union all
    select service, source_period, rows, 'airport_fee', airport_fee from counted
    union all
    select service, source_period, rows, 'cbd_congestion_fee', cbd_congestion_fee from counted
    union all
    select service, source_period, rows, 'passenger_count', passenger_count from counted
    union all
    select service, source_period, rows, 'ratecode_id', ratecode_id from counted
)
select
    service, source_period, column_name, rows, null_rows,
    cast(null_rows as double) / nullif(rows, 0) as null_share
from stacked;
"""


def _build_marts(connection: duckdb.DuckDBPyConnection, paths: Paths) -> None:
    sql = MARTS_SQL.format(
        zone_csv=paths.reference / "dim_zone.csv",
        holiday_csv=paths.transform / "seeds" / "holiday.csv",
    )
    for statement in [s for s in sql.split(";\n") if s.strip()]:
        connection.execute(statement)


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    """Generate, ingest, transform and publish two months into a scratch tree."""
    root = tmp_path_factory.mktemp("cityflow")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CITYFLOW_ROOT", str(root))
        patch.setenv("COLUMNS", "200")
        patch.delenv("CITYFLOW_BACKEND", raising=False)
        # repo_root and the holiday list are both cached, and a cache left over
        # from another module would silently point this build at the real tree.
        repo_root.cache_clear()
        holidays.cache_clear()

        (root / "config").mkdir()
        (root / "config" / "cityflow.yml").write_text(CONFIG_YAML, encoding="utf-8")
        (root / "metrics").mkdir()
        (root / "metrics" / "metrics.yml").write_text(
            (REPO / "metrics" / "metrics.yml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (root / "data" / "reference").mkdir(parents=True)
        (root / "data" / "reference" / "dim_zone.csv").write_text(
            (REPO / "data" / "reference" / "dim_zone.csv").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (root / "transform" / "seeds").mkdir(parents=True)
        (root / "transform" / "seeds" / "holiday.csv").write_text(
            (REPO / "transform" / "seeds" / "holiday.csv").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        config = load_config()
        paths = Paths.resolve()
        paths.ensure()
        assert paths.root == root

        connection = connect(config, paths.warehouse)
        progress: list[MonthReport] = []
        report = run_ingest(
            connection,
            config,
            paths=paths,
            scale=SCALE,
            on_progress=progress.append,
        )
        _build_marts(connection, paths)

        yield Built(
            root=root,
            paths=paths,
            config=config,
            connection=connection,
            report=report,
            progress=progress,
        )
        connection.close()

    repo_root.cache_clear()
    holidays.cache_clear()


@pytest.fixture(scope="module")
def published(built: Built) -> Built:
    """The shipped layer, built once from the warehouse above."""
    build_all(built.connection, built.config, built.paths)
    run_analysis(built.connection, built.config, built.paths)
    return built


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str, *parameters: Any) -> Any:
    row = connection.execute(sql, list(parameters)).fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# 1. The balance identity
# ---------------------------------------------------------------------------


def test_every_month_balances_exactly(built: Built) -> None:
    """Source rows equal clean rows plus the per rule counts, with no slack."""
    assert built.report.months, "the build window produced no months"
    for month in built.report.months:
        assert month.source_rows == month.clean_rows + sum(month.quarantined.values()), month.spec
    assert built.report.source_rows == built.report.clean_rows + built.report.quarantined_total


def test_the_warehouse_agrees_with_the_report(built: Built) -> None:
    """The audit table is the durable copy of the same arithmetic."""
    landed = _scalar(built.connection, "select count(*) from stg_trip")
    assert landed == built.report.clean_rows
    audited = _scalar(
        built.connection,
        "select sum(source_rows), sum(clean_rows), sum(quarantined_rows) from source_audit",
    )
    assert audited == built.report.source_rows
    logged = _scalar(built.connection, "select coalesce(sum(rows), 0) from quarantine_log")
    assert logged == built.report.quarantined_total


def test_every_month_was_reported_to_the_progress_callback(built: Built) -> None:
    assert [m.spec for m in built.progress] == [m.spec for m in built.report.months]
    assert {m.backend for m in built.report.months} == {"synthetic"}


def test_each_month_maps_to_the_vintage_its_period_names(built: Built) -> None:
    """The airport fee arrives in the yellow and for hire files in January 2022
    and not in the green ones, so exactly those two services cross a boundary
    inside this window. Asserting which ones is what makes this a test of the
    vintage table rather than of the fact that it has more than one row."""
    for month in built.report.months:
        assert month.vintage == resolve_vintage(month.spec.service, month.spec.period).name
    per_service: dict[str, set[str]] = {}
    for month in built.report.months:
        per_service.setdefault(month.spec.service, set()).add(month.vintage)
    crossed = {service for service, seen in per_service.items() if len(seen) > 1}
    assert crossed == {"yellow", "fhvhv"}, per_service


# ---------------------------------------------------------------------------
# 2. The rules partition the rejected set
# ---------------------------------------------------------------------------


def test_no_row_carries_two_rules(built: Built) -> None:
    """First match wins, so a rule name appears at most once per month and the
    per rule counts add up to the quarantined total without double counting."""
    for month in built.report.months:
        names = list(month.quarantined)
        assert len(names) == len(set(names))
        assert sum(month.quarantined.values()) == month.quarantined_total

    logged = built.connection.execute(
        "select service, source_period, rule, count(*) from quarantine_log group by 1, 2, 3"
    ).fetchall()
    assert all(row[3] == 1 for row in logged), "a rule was logged twice for one month"


def test_no_quarantinable_row_reached_the_clean_table(built: Built) -> None:
    """The other direction of the same claim. The rule set and the filter that
    splits the labelled month have to agree, or the balance identity holds
    while the warehouse quietly carries rows a rule says it caught."""
    for spec in built.config.sources():
        case = quarantine_case(rules_for(built.config, spec))
        leaked = _scalar(
            built.connection,
            f"select count(*) from stg_trip "
            f"where service = ? and source_period = ? and ({case}) is not null",
            spec.service,
            spec.period,
        )
        assert leaked == 0, f"{spec} landed {leaked} rows a rule would have caught"


def test_each_injected_defect_was_caught_at_roughly_its_injected_rate(built: Built) -> None:
    """Every rate in the config is a promise about how many rows a rule removes.

    The band is an order of magnitude on purpose. The point is not to pin a
    seeded count, which would break on a DuckDB upgrade; it is that a rule
    catching a thousandth of what was injected, or a hundred times it, is a
    rule pointed at the wrong column.
    """
    rates = built.config.synthetic.defect_rates
    expected: dict[str, float] = dict.fromkeys(DEFECT_TO_RULE.values(), 0.0)
    for spec in built.config.sources():
        generated = int(built.config.synthetic.trips_per_month[spec.service] * SCALE)
        vintage = resolve_vintage(spec.service, spec.period)
        for defect, rule in DEFECT_TO_RULE.items():
            if defect == "passenger_count_zero" and not vintage.has_passenger_count:
                # The for hire files carry no passenger count, so the defect
                # cannot be written into them and the rule cannot fire.
                continue
            expected[rule] += generated * rates[defect]

    caught = built.report.by_rule()
    for rule, target in expected.items():
        assert target > 0
        observed = caught.get(rule, 0)
        assert observed > 0, f"{rule} never fired, but {target:.0f} rows were injected for it"
        assert 0.2 <= observed / target <= 5.0, (
            f"{rule} caught {observed} rows against {target:.0f} injected"
        )

    for rule in STRUCTURALLY_IMPOSSIBLE:
        assert caught.get(rule, 0) == 0, f"{rule} fired on something nobody injected"


def test_an_unresolved_zone_is_not_a_quarantine_rule(built: Built) -> None:
    """Zones 264 and 265 carry real trips and real money. Throwing them away is
    the easy version of this and it biases every total downwards."""
    assert "unknown_zone" not in built.report.by_rule()
    kept = _scalar(
        built.connection,
        "select count(*) from stg_trip where pu_zone_id in (264, 265) or do_zone_id in (264, 265)",
    )
    assert kept > 0, "the generator injected unresolved zones and none of them landed"


def test_the_quarantine_share_is_reported_against_the_source(built: Built) -> None:
    assert 0.0 < built.report.quarantine_share < 0.05
    share = _scalar(
        built.connection,
        "select max(share_of_source) from mart_quarantine",
    )
    assert 0.0 < share < 1.0


# ---------------------------------------------------------------------------
# 3. Tip semantics, at the source
# ---------------------------------------------------------------------------


def test_a_cash_tip_is_never_observable(built: Built) -> None:
    """A cash trip records a tip of zero because no tip passed through the
    meter. That zero is a missing value, and it is the whole finding."""
    cash = _scalar(built.connection, "select count(*) from stg_trip where payment_type = 'cash'")
    assert cash > 0, "no cash trips were generated, so this asserts nothing"
    observable_cash = _scalar(
        built.connection,
        "select count(*) from stg_trip where payment_type = 'cash' and tip_is_observable",
    )
    assert observable_cash == 0


def test_every_for_hire_trip_is_settled_in_the_app(built: Built) -> None:
    """The for hire files carry no payment type column. That is a constant, not
    a missing value, and it is why a for hire tip of zero is a real zero."""
    fhvhv = _scalar(built.connection, "select count(*) from stg_trip where service = 'fhvhv'")
    assert fhvhv > 0
    wrong = _scalar(
        built.connection,
        "select count(*) from stg_trip where service = 'fhvhv' "
        "and (payment_type <> 'app' or not tip_is_observable)",
    )
    assert wrong == 0


def test_tip_pct_is_null_exactly_where_the_tip_is_unobservable(built: Built) -> None:
    leaked = _scalar(
        built.connection,
        "select count(*) from fct_trip where tip_is_observable = false and tip_pct is not null",
    )
    assert leaked == 0
    measured = _scalar(
        built.connection,
        "select count(*) from fct_trip where tip_is_observable and fare_amount > 0 "
        "and tip_pct is null",
    )
    assert measured == 0


def test_the_naive_tip_rate_understates_the_correct_one(built: Built) -> None:
    """The reason the distinction is drawn at all, measured on this build."""
    naive, correct = built.connection.execute(
        "select avg(tip_amount / nullif(fare_amount, 0)), avg(tip_pct) "
        "from fct_trip where service in ('yellow', 'green')"
    ).fetchone() or (None, None)
    assert naive is not None and correct is not None
    assert correct > naive


# ---------------------------------------------------------------------------
# 4. A column a vintage does not have is NULL, never zero
# ---------------------------------------------------------------------------


def test_a_column_that_did_not_exist_yet_is_null_and_not_zero(built: Built) -> None:
    """The airport fee arrives in January 2022. December has to be null, not
    zero: zero says the fee was charged and came to nothing, the series shows a
    flat line running into a step, and somebody eventually explains that step."""
    december = _scalar(
        built.connection,
        "select count(*) from stg_trip "
        "where source_period = date '2021-12-01' and airport_fee is not null",
    )
    assert december == 0
    january = _scalar(
        built.connection,
        "select count(*) from stg_trip "
        "where source_period = date '2022-01-01' and airport_fee is not null",
    )
    assert january > 0

    # Green never carried the column at all, in either month, which is a
    # different fact from the column arriving and is recorded separately.
    green = built.connection.execute(
        "select source_period, null_share from mart_null_rates "
        "where column_name = 'airport_fee' and service = 'green' order by 1"
    ).fetchall()
    assert [row[1] for row in green] == [1.0, 1.0]

    yellow = built.connection.execute(
        "select source_period, null_share from mart_null_rates "
        "where column_name = 'airport_fee' and service = 'yellow' order by 1"
    ).fetchall()
    assert yellow[0] == (dt.date(2021, 12, 1), 1.0)
    assert yellow[1][0] == dt.date(2022, 1, 1)
    assert yellow[1][1] < 1.0

    # The CBD fee is later than both months, so it is absent throughout.
    assert (
        _scalar(built.connection, "select count(*) from stg_trip where cbd_congestion_fee is null")
        == built.report.clean_rows
    )


# ---------------------------------------------------------------------------
# 5. The shipped layer
# ---------------------------------------------------------------------------


def test_the_shipped_layer_summarizes_rather_than_copies(published: Built) -> None:
    results = build_all(published.connection, published.config, published.paths)
    names = {r.name for r in results}
    assert "agg_zone_hour.parquet" in names
    assert "agg_hour_of_week.parquet" in names
    assert "detail_2021_12.parquet" in names
    for result in results:
        assert result.path.is_file()
        assert result.rows > 0, result.name

    zone_hour = next(r for r in results if r.name == "agg_zone_hour.parquet")
    fact_rows = _scalar(published.connection, "select count(*) from fct_trip")
    assert zone_hour.rows < fact_rows
    assert zone_hour.compression_ratio > 1.0
    assert zone_hour.megabytes == zone_hour.bytes / 1_000_000


def test_the_hour_of_week_grid_is_measured_and_not_reconstructed(published: Built) -> None:
    """Seven days by twenty four hours, each one counted. Rebuilding Tuesday
    from a weekday average gives five rows that are identical by construction
    and a reader cannot tell from looking."""
    cells = _scalar(
        published.connection,
        "select count(distinct (day_of_week, hour)) from read_parquet(?)",
        str(published.paths.shipped / "agg_hour_of_week.parquet"),
    )
    assert cells == 168


def test_the_od_flow_file_excludes_unresolved_zones_and_only_it_does(
    published: Built,
) -> None:
    unresolved = _scalar(
        published.connection,
        "select count(*) from read_parquet(?) where pu_zone_id in (264, 265) "
        "or do_zone_id in (264, 265)",
        str(published.paths.shipped / "agg_od_flow.parquet"),
    )
    assert unresolved == 0
    kept = _scalar(
        published.connection,
        "select sum(unknown_zone_trips) from read_parquet(?)",
        str(published.paths.shipped / "agg_zone_hour.parquet"),
    )
    assert kept > 0


def test_the_size_budget_is_a_hard_limit(published: Built) -> None:
    """The deliberate violation. GitHub refuses a file over 100MB, so a budget
    that is never observed to fire is a budget nobody can rely on."""
    squeezed = published.config.model_copy(update={"shipped_file_budget_mb": 0.000_001})
    with pytest.raises(RuntimeError, match="per file budget"):
        build_all(published.connection, squeezed, published.paths)

    total_only = published.config.model_copy(update={"shipped_total_budget_mb": 0.000_001})
    with pytest.raises(RuntimeError, match="Shipped layer is"):
        build_all(published.connection, total_only, published.paths)


# ---------------------------------------------------------------------------
# 6. Reconcile, and the proof that it is not vacuous
# ---------------------------------------------------------------------------


def test_the_shipped_layer_agrees_with_the_warehouse(published: Built) -> None:
    report = reconcile_all(published.connection, published.paths)
    assert report.cells > 0
    assert report.checks == report.cells * 4
    assert report.ok, "\n".join(str(d) for d in report.disagreements[:10])
    assert report.disagreements == ()


def test_reconcile_catches_a_browser_expression_that_drops_its_filter(
    published: Built,
) -> None:
    """The most important assertion in this file.

    card_share is the share of *metered* trips settled by card. Swapping its
    denominator for the trip count folds every for hire trip into the
    population, which is exactly the shape of mistake a shipped aggregate
    invites and exactly what reconcile exists to refuse. A reconcile check that
    has never been observed to fail is a document about the code.
    """
    metrics_file = published.paths.metrics_file
    original = metrics_file.read_text(encoding="utf-8")
    try:
        layer = yaml.safe_load(original)
        for metric in layer["metrics"]:
            if metric["name"] == "card_share":
                metric["browser"] = "sum(card_trips)::double / nullif(sum(trips), 0)"
                metric["components"] = ["card_trips", "trips"]
                break
        else:  # pragma: no cover
            pytest.fail("card_share is no longer defined; pick another metric to break")
        metrics_file.write_text(yaml.safe_dump(layer, sort_keys=False), encoding="utf-8")

        broken = reconcile_all(published.connection, published.paths)
        assert not broken.ok
        named = {d.metric for d in broken.disagreements}
        assert named == {"card_share"}, named
        worst = max(broken.disagreements, key=lambda d: d.difference)
        assert worst.difference > 0
        assert "card_share" in str(worst)
        assert {d.grain for d in broken.disagreements} >= {"overall"}
    finally:
        metrics_file.write_text(original, encoding="utf-8")

    assert reconcile_all(published.connection, published.paths).ok


def test_reconcile_refuses_to_run_without_a_shipped_aggregate(
    published: Built, tmp_path: Path
) -> None:
    empty = Paths(root=tmp_path)
    empty.ensure()
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "metrics.yml").write_text(
        published.paths.metrics_file.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError, match="cityflow publish"):
        reconcile_all(published.connection, empty)


# ---------------------------------------------------------------------------
# 7. The manifest
# ---------------------------------------------------------------------------

# Every figure a document is allowed to state has to be produced by a query.
# These are the ones README.md and RESULTS.md reference, so a null here is a
# document that cannot be rendered.
REQUIRED_CLAIMS = (
    "source_rows",
    "clean_rows",
    "quarantined_rows",
    "quarantine_share_pct",
    "months",
    "services",
    "source_files",
    "vintages",
    "first_period",
    "last_period",
    "fct_trip_rows",
    "distinct_zones",
    "zones_with_geometry",
    "zones_without_geometry",
    "unknown_zone_trips",
    "unknown_zone_share_pct",
    "tip_rate_naive_pct",
    "tip_rate_correct_pct",
    "tip_rate_understatement_pct",
    "cash_trips",
    "cash_share_of_metered_pct",
    "airport_fee_null_months",
    "cbd_fee_null_months",
    "airport_fee_first_populated",
    "airport_fee_last_all_null",
    "zones_for_half_of_trips",
    "busiest_zone",
    "shipped_files",
    "shipped_bytes",
    "shipped_mb",
    "largest_shipped_mb",
    "summarization_ratio",
    "quarantine_rules_fired",
    "backend",
    "metric_count",
    "threshold.max_implied_speed_mph",
    "defect_rate.unknown_zone",
    "planted_changepoint",
)


def test_the_manifest_carries_every_claim_a_document_needs(published: Built) -> None:
    data = build_manifest(published.connection, published.config, published.paths)
    claims = data["claims"]
    missing = [c for c in REQUIRED_CLAIMS if claims.get(c) is None]
    assert missing == [], f"claims missing or null: {missing}"

    assert data["window"]["start"] == "2021-12-01"
    assert data["backend"] == ["synthetic"]
    assert claims["source_rows"] == published.report.source_rows
    assert claims["clean_rows"] == published.report.clean_rows
    assert claims["quarantined_rows"] == published.report.quarantined_total
    assert claims["months"] == 2
    assert claims["services"] == 3
    assert claims["vintages"] == 5
    assert claims["backend"] == "synthetic"

    # The per file detail is flattened into the claim namespace, so a document
    # can state one file's size without reaching into a list by index.
    assert claims["rows.agg_zone_hour_parquet"] > 0
    assert claims["mb.agg_zone_hour_parquet"] > 0
    assert data["shipped"], "the manifest lists no shipped files"

    # Every rule that fired is named, and the shares are against one denominator.
    for entry in data["quarantine_by_rule"]:
        assert claims[f"quarantine_rows.{entry['rule']}"] == entry["rows"]
    assert claims["quarantine_rules_fired"] == len(data["quarantine_by_rule"])

    # It has to survive the round trip the CLI puts it through.
    assert json.loads(json.dumps(data, default=str))["claims"]["source_rows"] > 0


def test_the_analysis_publishes_what_the_panels_state(published: Built) -> None:
    changepoints = json.loads(
        (published.paths.shipped / "changepoints.json").read_text(encoding="utf-8")
    )
    assert changepoints["searched_on"].startswith("the STL trend")
    assert changepoints["found"] >= 0
    catalog = json.loads(
        (published.paths.shipped / "metric_catalog.json").read_text(encoding="utf-8")
    )
    assert {m["name"] for m in catalog} >= {"trips", "tip_rate", "card_share"}
    lineage = json.loads((published.paths.shipped / "lineage.json").read_text(encoding="utf-8"))
    # No dbt run in a unit test, so the panel has to say so rather than draw a
    # graph from nothing.
    assert lineage["available"] is False


# ---------------------------------------------------------------------------
# 8. The benchmark reads the layer the pipeline just built
# ---------------------------------------------------------------------------


def test_the_benchmark_measures_the_published_files(
    published: Built, capsys: pytest.CaptureFixture[str]
) -> None:
    target = published.root / "bench.json"
    assert bench_queries.main(["--repeats", "2", "--json", str(target)]) == 0
    output = capsys.readouterr().out
    assert "KPI tiles" in output
    assert "No browser query log found" in output

    measured = json.loads(target.read_text(encoding="utf-8"))
    assert len(measured) >= 11
    assert all(entry["row_groups"] >= 1 for entry in measured)
    assert any(entry["file"] == "agg_zone_hour.parquet" for entry in measured)


def test_the_benchmark_refuses_to_report_on_a_layer_that_is_not_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CITYFLOW_ROOT", str(tmp_path))
        repo_root.cache_clear()
        try:
            assert bench_queries.main([]) == 2
            assert "the shipped layer is not built" in capsys.readouterr().out
        finally:
            repo_root.cache_clear()


def test_the_benchmark_folds_in_a_browser_log_when_there_is_one(published: Built) -> None:
    """Bytes pulled cannot be measured by a local read, so the number comes
    from the browser smoke test and is folded in here when present."""
    log = published.root / bench_queries.BROWSER_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps([{"label": "KPI tiles, whole window", "bytes": 68_000, "requests": 3}]),
        encoding="utf-8",
    )
    try:
        captured = bench_queries.browser_log(published.paths)
        assert captured["KPI tiles, whole window"]["bytes"] == 68_000
        assert bench_queries.main(["--repeats", "1"]) == 0
    finally:
        log.unlink()


# ---------------------------------------------------------------------------
# 9. The command line, over the tree this module just built
# ---------------------------------------------------------------------------

# The Makefile is a thin wrapper over these commands, not a second
# implementation of them, so the commands are what a build actually runs.


@contextmanager
def released(built: Built) -> Iterator[None]:
    """Hand the warehouse over to the command line for the duration.

    Each command opens the file itself, and DuckDB refuses a second connection
    to the same database with a different configuration, so the write handle
    this module holds has to be released and taken back.
    """
    built.connection.close()
    try:
        yield
    finally:
        built.connection = connect(built.config, built.paths.warehouse)


def _break_metrics(path: Path, replacements: dict[str, str]) -> str:
    """Rewrite named browser expressions, returning the original text."""
    original = path.read_text(encoding="utf-8")
    layer = yaml.safe_load(original)
    for metric in layer["metrics"]:
        if metric["name"] in replacements:
            metric["browser"] = replacements[metric["name"]]
    path.write_text(yaml.safe_dump(layer, sort_keys=False), encoding="utf-8")
    return original


def test_the_publish_command_reports_what_it_shipped(published: Built) -> None:
    runner = CliRunner()
    with released(published):
        result = runner.invoke(app, ["publish"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Shipped layer" in result.output
    assert "agg_zone_hour.parquet" in result.output
    assert "decomposition" in result.output
    assert "changepoints" in result.output
    assert "Benjamini-Hochberg" in result.output


def test_the_reconcile_command_reports_agreement(published: Built) -> None:
    runner = CliRunner()
    with released(published):
        result = runner.invoke(app, ["reconcile"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "metrics reconcile" in result.output
    assert "grain checks" in result.output


def test_the_reconcile_command_exits_nonzero_when_the_layer_disagrees(
    published: Built,
) -> None:
    """The deliberate violation at the command line, where CI reads it. Five
    metrics lose the population their browser expression is defined over, which
    is more disagreements than the table prints, so the overflow line is
    covered too."""
    runner = CliRunner()
    original = _break_metrics(
        published.paths.metrics_file,
        {
            "card_share": "sum(card_trips)::double / nullif(sum(trips), 0)",
            "flat_rate_share": "sum(flat_rate_trips)::double / nullif(sum(trips), 0)",
            "airport_share": "sum(trips)::double / nullif(sum(trips), 0)",
            "unknown_zone_share": "sum(trips)::double / nullif(sum(trips), 0)",
            "tip_rate": "sum(tip_obs_tip_sum) / nullif(sum(fare_amount_sum), 0)",
        },
    )
    try:
        with released(published):
            result = runner.invoke(app, ["reconcile"], env={"COLUMNS": "200"})
        assert result.exit_code == 1, result.output
        assert "disagreements" in result.output
        assert "card_share" in result.output
        assert "more" in result.output
    finally:
        published.paths.metrics_file.write_text(original, encoding="utf-8")


def test_the_manifest_command_writes_where_the_gate_reads(published: Built) -> None:
    runner = CliRunner()
    with released(published):
        result = runner.invoke(app, ["manifest"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "sections" in result.output

    written = json.loads(published.paths.manifest.read_text(encoding="utf-8"))
    assert written["claims"]["source_rows"] == published.report.source_rows
    assert written["window"]["detail_month"] == "2021-12-01"


def test_the_doctor_command_sees_the_tree_this_module_built(published: Built) -> None:
    runner = CliRunner()
    with released(published):
        result = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
    # The shapefile is not in the scratch tree, so doctor reports a problem.
    assert result.exit_code == 1
    assert "backend: synthetic" in result.output
    assert result.output.count("yes") >= 2


# ---------------------------------------------------------------------------
# 10. The manifest sections that only exist once the rest of the build has run
# ---------------------------------------------------------------------------

# These run last, because they drop fixture files into the shipped directory
# and into transform/target that earlier assertions are written against the
# absence of. They are worth having: every one of these claims is quoted in
# RESULTS.md, and a claim nothing produces is a document that cannot render.

DBT_MANIFEST: dict[str, Any] = {
    "nodes": {
        "model.cityflow.fct_trip": {
            "resource_type": "model",
            "name": "fct_trip",
            "fqn": ["cityflow", "marts", "fct_trip"],
            "description": " One trip per row. ",
            "depends_on": {
                "nodes": ["model.cityflow.int_trip_geography", "test.cityflow.not_null"]
            },
        },
        "seed.cityflow.holiday": {
            "resource_type": "seed",
            "name": "holiday",
            "fqn": ["cityflow", "seeds", "holiday"],
            "depends_on": {"nodes": []},
        },
        "test.cityflow.not_null": {"resource_type": "test", "name": "not_null"},
        "analysis.cityflow.scratch": {"resource_type": "analysis", "name": "scratch"},
    },
    "sources": {
        "source.cityflow.warehouse.stg_trip": {
            "name": "stg_trip",
            "description": "The staging table the ingest owns.",
        }
    },
    "exposures": {
        "exposure.cityflow.dashboard": {
            "name": "dashboard",
            "label": "CityFlow",
            "description": "The published dashboard.",
            "depends_on": {"nodes": ["model.cityflow.fct_trip"]},
            "url": "https://example.invalid/cityflow",
            "owner": {"name": "analytics"},
        }
    },
}

GEOJSON_STUB = {
    "type": "FeatureCollection",
    "features": [{"type": "Feature", "id": 161, "properties": {}, "geometry": None}],
}


def test_the_manifest_counts_what_dbt_built_rather_than_asserting_it(
    published: Built,
) -> None:
    """A document that says 150 tests while the project has 141 is exactly the
    figure this whole mechanism exists to catch, so the counts are read out of
    the dbt manifest rather than typed."""
    target = published.paths.transform / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(DBT_MANIFEST), encoding="utf-8")
    (published.paths.shipped / "zones.geojson").write_text(
        json.dumps(GEOJSON_STUB), encoding="utf-8"
    )
    changepoints = published.paths.shipped / "changepoints.json"
    original = changepoints.read_text(encoding="utf-8")
    detected = json.loads(original)
    detected["detected"] = {
        "fhvhv": [
            {"date": "2024-08-15", "relative_change": -0.118, "direction": "down"},
            {"date": "2024-02-01", "relative_change": -0.070, "direction": "down"},
        ]
    }
    changepoints.write_text(json.dumps(detected), encoding="utf-8")

    bench = published.paths.shipped / "bench.json"
    assert bench_queries.main(["--repeats", "1", "--json", str(bench)]) == 0
    measured = json.loads(bench.read_text(encoding="utf-8"))
    measured[0]["browser_bytes"] = 68_000
    measured[0]["browser_requests"] = 3
    measured[1]["browser_bytes"] = 4_096
    measured[1]["browser_requests"] = 2
    bench.write_text(json.dumps(measured), encoding="utf-8")

    try:
        exposures = write_lineage(published.paths)
        assert exposures == 1
        lineage = json.loads((published.paths.shipped / "lineage.json").read_text(encoding="utf-8"))
        assert lineage["available"] is True
        # Tests and analyses are not lineage nodes, and a test edge is not a
        # dependency anyone wants drawn.
        assert set(lineage["nodes"]) == {
            "model.cityflow.fct_trip",
            "seed.cityflow.holiday",
            "source.cityflow.warehouse.stg_trip",
        }
        assert lineage["nodes"]["model.cityflow.fct_trip"]["depends_on"] == [
            "model.cityflow.int_trip_geography"
        ]
        assert lineage["nodes"]["model.cityflow.fct_trip"]["layer"] == "marts"
        assert lineage["exposures"][0]["owner"] == "analytics"

        claims = build_manifest(published.connection, published.config, published.paths)["claims"]
        assert claims["dbt_models"] == 1
        assert claims["dbt_tests"] == 1
        assert claims["dbt_exposures"] == 1
        assert claims["geojson_features"] == 1

        assert claims["bench_queries"] == len(measured)
        assert claims["bench_p95_max_ms"] >= claims["bench_p95_median_ms"]
        assert claims["bench_browser_queries"] == 2
        assert claims["bench_browser_bytes"] == 72_096
        assert claims["bench_browser_requests"] == 5
        # The number the whole shipped layer design is defended by: the query
        # fetched a fraction of the file rather than the file.
        assert 0 < claims["bench_browser_fetched_pct"] < 100
        assert claims["bench_tightest_kb"] > 0
        assert claims["bench_tightest_query"] in {entry["query"] for entry in measured}

        # The generator plants one level shift, so the detector has something
        # with a known right answer to be scored against.
        assert claims["planted_changepoint_miss_days"] == 3
        assert claims["planted_changepoint_detected_change_pct"] == -11.8
    finally:
        changepoints.write_text(original, encoding="utf-8")
