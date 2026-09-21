"""The vintage table, the normalizer and the source registry.

The TLC has changed the trip record schema eleven times. The mapping is written
out rather than inferred, which means it is a table of facts that can be wrong,
and a table of facts that nothing checks is a table of facts that drifts. These
tests check the three properties the rest of the ingest assumes: the ranges
partition the history, a file of any vintage lands on the same canonical row,
and a column a vintage does not have becomes a typed NULL rather than a zero.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pytest

from cityflow_core.config import CityflowConfig, SourceSpec
from cityflow_core.paths import Paths
from cityflow_ingest.normalize import (
    CANONICAL_COLUMNS,
    normalize_select,
    schema_difference,
    trip_id_offset,
)
from cityflow_ingest.quarantine import (
    BUSINESS_KEY,
    DUPLICATE_RULE,
    all_rule_names,
    duplicate_flag_sql,
    quarantine_case,
    rules_for,
)
from cityflow_ingest.registry import SourceRegistry, SourceUnavailableError
from cityflow_ingest.synthetic import (
    expected_row_count,
    load_zones,
    sanity_check_profiles,
    total_target,
)
from cityflow_ingest.vintage import (
    HVFHS_OPERATORS,
    PAYMENT_TYPES,
    TIP_OBSERVABLE_PAYMENT_TYPES,
    VINTAGES,
    UnknownVintageError,
    expected_columns,
    resolve_vintage,
)

REPO = Path(__file__).resolve().parent.parent
SERVICES = ("yellow", "green", "fhvhv")

# July 2016 is the first month the files carry zone ids instead of raw
# coordinates, and is therefore the first month this project can read.
FIRST_ZONE_MONTH = dt.date(2016, 7, 1)


@pytest.fixture
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    handle = duckdb.connect()
    yield handle
    handle.close()


def months_between(first: dt.date, last: dt.date) -> list[dt.date]:
    out: list[dt.date] = []
    cursor = first
    while cursor <= last:
        out.append(cursor)
        cursor = dt.date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    return out


# ---------------------------------------------------------------------------
# The vintage table
# ---------------------------------------------------------------------------


def test_no_two_vintages_of_one_service_overlap() -> None:
    """The ranges partition the history, which is what makes returning the
    first match in resolve_vintage a fact rather than an arbitrary choice."""
    for service in SERVICES:
        ranges = sorted(
            ((v.first, v.last, v.name) for v in VINTAGES if v.service == service),
            key=lambda item: item[0],
        )
        assert ranges, service
        for (_, first_last, first_name), (second_first, _, second_name) in pairwise(ranges):
            assert first_last is not None, f"{first_name} is open ended but is not the last"
            assert first_last < second_first, f"{first_name} overlaps {second_name}"
        assert ranges[-1][1] is None, f"{service} has no current vintage"


def test_every_month_since_zone_ids_resolves_to_exactly_one_vintage() -> None:
    today = dt.date.today().replace(day=1)
    for service in SERVICES:
        began = min(v.first for v in VINTAGES if v.service == service)
        start = max(began, FIRST_ZONE_MONTH)
        for month in months_between(start, today):
            covering = [v for v in VINTAGES if v.service == service and v.covers(month)]
            assert len(covering) == 1, f"{service} {month:%Y-%m} matched {covering}"
            assert resolve_vintage(service, month) is covering[0]


def test_the_day_of_the_month_does_not_change_the_vintage() -> None:
    """A period is a month. Handing resolve_vintage the 28th must not fall off
    the end of a range that closes on the first."""
    assert resolve_vintage("yellow", dt.date(2021, 12, 28)).name == "yellow_v3_congestion"
    assert resolve_vintage("yellow", dt.date(2022, 1, 31)).name == "yellow_v4_airport_fee"


def test_a_month_before_the_service_existed_is_refused() -> None:
    with pytest.raises(UnknownVintageError, match="No vintage covers fhvhv"):
        resolve_vintage("fhvhv", dt.date(2018, 1, 1))
    with pytest.raises(UnknownVintageError, match="earliest published"):
        resolve_vintage("yellow", dt.date(2008, 12, 1))
    with pytest.raises(UnknownVintageError, match="2013-08"):
        resolve_vintage("green", dt.date(2013, 7, 1))


def test_the_coordinate_era_is_flagged_and_nothing_else_is() -> None:
    coordinate = [v.name for v in VINTAGES if v.is_coordinate_era]
    assert coordinate == ["yellow_v1_coordinates", "green_v1_coordinates"]
    for vintage in VINTAGES:
        assert vintage.is_coordinate_era == (vintage.pu_zone_column is None)


def test_expected_columns_names_a_surcharge_only_where_it_exists() -> None:
    """The surcharge columns arrive part way through the history. Expecting one
    in a month that predates it is how a schema check produces a false alarm;
    not expecting one in a month that has it is how a column goes unread."""
    for vintage in VINTAGES:
        columns = expected_columns(vintage)
        assert ("congestion_surcharge" in columns) == vintage.has_congestion_surcharge
        assert ("airport_fee" in columns) == vintage.has_airport_fee
        assert ("cbd_congestion_fee" in columns) == vintage.has_cbd_congestion_fee
        assert ("passenger_count" in columns) == vintage.has_passenger_count
        assert ("RatecodeID" in columns) == vintage.has_ratecode
        assert ("payment_type" in columns) == vintage.has_payment_type
        assert ("total_amount" in columns) == vintage.has_total_amount
        assert ("bcf" in columns) == vintage.has_black_car_fund
        assert ("sales_tax" in columns) == vintage.has_sales_tax
        # The zone columns come as a pair or not at all.
        assert ("PULocationID" in columns) == ("DOLocationID" in columns)
        assert columns.isdisjoint(vintage.ignored)


def test_the_surcharge_history_is_the_published_one() -> None:
    """Spot checks against the dates the columns actually appear in the files,
    so a typo in a range is caught by something other than a reviewer."""
    assert not resolve_vintage("yellow", dt.date(2019, 1, 1)).has_congestion_surcharge
    assert resolve_vintage("yellow", dt.date(2019, 2, 1)).has_congestion_surcharge
    assert not resolve_vintage("yellow", dt.date(2021, 12, 1)).has_airport_fee
    assert resolve_vintage("yellow", dt.date(2022, 1, 1)).has_airport_fee
    assert not resolve_vintage("green", dt.date(2024, 12, 1)).has_cbd_congestion_fee
    assert resolve_vintage("green", dt.date(2025, 1, 1)).has_cbd_congestion_fee


def test_no_for_hire_vintage_claims_a_total_the_files_do_not_publish() -> None:
    """This caught a real defect, which is why it checks every vintage.

    fhvhv_v3_cbd omitted has_total_amount=False, has_black_car_fund=True and
    has_sales_tax=True, all three of which its two predecessors set. The
    published for hire files carry no total, so any build window reaching 2025
    aborted with "missing columns ... total_amount" and the total expression
    would have read a column that does not exist. A test that checked only the
    vintage in the current build window would not have found it.
    """
    for vintage in VINTAGES:
        if vintage.service != "fhvhv":
            continue
        assert not vintage.has_total_amount, vintage.name
        assert vintage.has_black_car_fund, vintage.name
        assert vintage.has_sales_tax, vintage.name


def test_the_payment_lookup_decides_what_an_observable_tip_is() -> None:
    assert PAYMENT_TYPES[2] == "cash"
    assert "cash" not in TIP_OBSERVABLE_PAYMENT_TYPES
    assert set(TIP_OBSERVABLE_PAYMENT_TYPES) == {"card", "app"}
    assert set(HVFHS_OPERATORS) == {"HV0002", "HV0003", "HV0004", "HV0005"}


# ---------------------------------------------------------------------------
# The normalizer
# ---------------------------------------------------------------------------


def test_a_coordinate_era_file_is_refused_with_the_reason() -> None:
    """Joining raw coordinates to a taxi zone needs a spatial join against the
    shapefile. That is a different project, and half supporting it is worse
    than refusing it."""
    vintage = resolve_vintage("yellow", dt.date(2016, 6, 1))
    with pytest.raises(ValueError, match="spatial join") as caught:
        normalize_select(
            SourceSpec(service="yellow", year=2016, month=6), vintage, "nowhere.parquet"
        )
    assert "July 2016 onwards" in str(caught.value)


def test_trip_id_ranges_are_disjoint_across_a_decade() -> None:
    """A month keeps its ids across a rebuild, and two months cannot collide,
    which is what lets months be rebuilt in any order and in parallel."""
    offsets: dict[int, str] = {}
    for service in SERVICES:
        for month in months_between(dt.date(2016, 7, 1), dt.date(2026, 12, 1)):
            spec = SourceSpec(service=service, year=month.year, month=month.month)
            offset = trip_id_offset(spec)
            assert offset not in offsets, f"{spec} collides with {offsets.get(offset)}"
            offsets[offset] = str(spec)

    ordered = sorted(offsets)
    gaps = [second - first for first, second in pairwise(ordered)]
    # A billion rows of headroom per month, against real months of tens of
    # millions, so the ranges cannot meet.
    assert min(gaps) >= 1_000_000_000


def _write_row(connection: duckdb.DuckDBPyConnection, path: Path, columns: dict[str, str]) -> None:
    select = ", ".join(f"{value} as {name}" for name, value in columns.items())
    connection.execute(f"copy (select {select}) to '{path}' (format parquet)")


def _normalized(
    connection: duckdb.DuckDBPyConnection, spec: SourceSpec, path: Path
) -> dict[str, Any]:
    vintage = resolve_vintage(spec.service, spec.period)
    sql = normalize_select(spec, vintage, path, trip_id_offset=trip_id_offset(spec))
    cursor = connection.execute(sql)
    assert cursor.description is not None
    names = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    assert row is not None
    return dict(zip(names, row, strict=True))


def _typeof(
    connection: duckdb.DuckDBPyConnection, spec: SourceSpec, path: Path, column: str
) -> str:
    vintage = resolve_vintage(spec.service, spec.period)
    sql = normalize_select(spec, vintage, path)
    row = connection.execute(f"select typeof({column}) from ({sql})").fetchone()
    assert row is not None
    return str(row[0])


GREEN_2018_ROW: dict[str, str] = {
    "VendorID": "2",
    "lpep_pickup_datetime": "timestamp '2018-03-04 10:00:00'",
    "lpep_dropoff_datetime": "timestamp '2018-03-04 10:20:00'",
    "passenger_count": "3",
    "trip_distance": "4.5",
    "RatecodeID": "1",
    "store_and_fwd_flag": "'N'",
    "PULocationID": "74",
    "DOLocationID": "75",
    "payment_type": "2",
    "fare_amount": "18.0",
    "extra": "0.5",
    "mta_tax": "0.5",
    "tip_amount": "0.0",
    "tolls_amount": "0.0",
    "ehail_fee": "cast(null as double)",
    "improvement_surcharge": "0.3",
    "total_amount": "19.3",
    "trip_type": "1",
}

FHVHV_2019_ROW: dict[str, str] = {
    "hvfhs_license_num": "'HV0003'",
    "dispatching_base_num": "'B03404'",
    "pickup_datetime": "timestamp '2019-06-02 08:00:00'",
    "dropoff_datetime": "timestamp '2019-06-02 08:30:00'",
    "PULocationID": "132",
    "DOLocationID": "161",
    "trip_miles": "12.0",
    "trip_time": "1800",
    "base_passenger_fare": "40.0",
    "tolls": "6.0",
    "bcf": "1.2",
    "sales_tax": "3.55",
    "congestion_surcharge": "2.75",
    "tips": "5.0",
    "driver_pay": "28.8",
}


def test_a_column_the_vintage_lacks_becomes_a_typed_null_not_a_zero(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Writing zero would say the surcharge was collected and came to nothing.
    NULL says it was not collected, which is what happened."""
    path = tmp_path / "green_2018_03.parquet"
    _write_row(connection, path, GREEN_2018_ROW)
    spec = SourceSpec(service="green", year=2018, month=3)

    row = _normalized(connection, spec, path)
    assert row["congestion_surcharge"] is None
    assert row["airport_fee"] is None
    assert row["cbd_congestion_fee"] is None
    # Typed, so the column still has a type in the staging table and a sum over
    # it is a sum of doubles rather than of an untyped NULL.
    assert _typeof(connection, spec, path, "congestion_surcharge") == "DOUBLE"
    assert _typeof(connection, spec, path, "cbd_congestion_fee") == "DOUBLE"


def test_a_cash_row_carries_its_payment_channel_into_the_warehouse(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "green_2018_03.parquet"
    _write_row(connection, path, GREEN_2018_ROW)
    row = _normalized(connection, SourceSpec(service="green", year=2018, month=3), path)

    assert row["payment_type"] == "cash"
    assert row["tip_is_observable"] is False
    assert row["operator"] == "Curb"
    assert row["vintage"] == "green_v2_zones"
    assert row["source_period"] == dt.date(2018, 3, 1)
    assert row["trip_id"] == trip_id_offset(SourceSpec(service="green", year=2018, month=3)) + 1
    assert row["total_amount"] == pytest.approx(19.3)
    assert set(row) == set(CANONICAL_COLUMNS)


def test_a_for_hire_row_is_settled_in_the_app_and_its_total_is_reconstructed(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The for hire files publish the parts and no total, so the total is summed
    from them. Stated here because it is not the same construction as the
    yellow total and nobody should compare the two without knowing."""
    path = tmp_path / "fhvhv_2019_06.parquet"
    _write_row(connection, path, FHVHV_2019_ROW)
    row = _normalized(connection, SourceSpec(service="fhvhv", year=2019, month=6), path)

    assert row["payment_type"] == "app"
    assert row["tip_is_observable"] is True
    assert row["operator"] == "Uber"
    assert row["passenger_count"] is None
    assert row["ratecode_id"] is None
    assert row["total_amount"] == pytest.approx(40.0 + 6.0 + 5.0 + 1.2 + 3.55 + 2.75)
    assert row["airport_fee"] is None


def test_an_unrecognised_operator_code_is_labelled_rather_than_dropped(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "fhvhv_2019_06.parquet"
    _write_row(connection, path, {**FHVHV_2019_ROW, "hvfhs_license_num": "'HV9999'"})
    row = _normalized(connection, SourceSpec(service="fhvhv", year=2019, month=6), path)
    assert row["operator"] == "Unknown operator"

    green = tmp_path / "green_2018_03.parquet"
    _write_row(connection, green, {**GREEN_2018_ROW, "VendorID": "99"})
    row = _normalized(connection, SourceSpec(service="green", year=2018, month=3), green)
    assert row["operator"] == "Unknown vendor"


def test_an_unpublished_payment_code_is_unknown_and_unobservable(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "green_2018_03.parquet"
    _write_row(connection, path, {**GREEN_2018_ROW, "payment_type": "42"})
    row = _normalized(connection, SourceSpec(service="green", year=2018, month=3), path)
    assert row["payment_type"] == "unknown"
    assert row["tip_is_observable"] is False


def test_a_missing_column_and_an_unexpected_one_are_different_failures() -> None:
    """Missing means the vintage table is wrong about this month and the build
    must stop. Unexpected means the TLC added something, which is information."""
    missing, unexpected = schema_difference(
        actual={"a", "b", "store_and_fwd_flag", "new_tlc_column"},
        expected={"a", "b", "c"},
        ignored={"store_and_fwd_flag"},
    )
    assert missing == {"c"}
    assert unexpected == {"new_tlc_column"}


# ---------------------------------------------------------------------------
# The quarantine rules
# ---------------------------------------------------------------------------


def _config(**overrides: Any) -> CityflowConfig:
    return CityflowConfig.model_validate(
        {
            "backend": "synthetic",
            "start": "2024-01-01",
            "end": "2024-01-01",
            "detail_month": "2024-01-01",
            **overrides,
        }
    )


def test_every_rule_name_is_unique_and_the_duplicate_rule_comes_last() -> None:
    spec = SourceSpec(service="yellow", year=2024, month=1)
    names = all_rule_names(_config(), spec)
    assert len(names) == len(set(names))
    assert names[-1] == DUPLICATE_RULE.name


def test_a_rule_prints_the_threshold_the_build_actually_used() -> None:
    """The document reads this object, so a threshold cannot be stated in one
    place and applied in another."""
    spec = SourceSpec(service="yellow", year=2024, month=1)
    rules = {rule.name: rule for rule in rules_for(_config(), spec)}
    assert "80 mph" in rules["implausible_speed"].reason
    assert "80.0" in rules["implausible_speed"].predicate

    loosened = _config(quarantine={"max_implied_speed_mph": 120.0})
    relaxed = {rule.name: rule for rule in rules_for(loosened, spec)}
    assert "120 mph" in relaxed["implausible_speed"].reason
    assert "120.0" in relaxed["implausible_speed"].predicate


def test_the_period_window_stretches_over_the_end_of_december() -> None:
    """A trip starting on the last night of a month is recorded in the next
    file. The tolerance absorbs it; the 2001 and 2098 timestamps it does not."""
    december = SourceSpec(service="yellow", year=2024, month=12)
    rules = {rule.name: rule for rule in rules_for(_config(), december)}
    predicate = rules["timestamp_out_of_period"].predicate
    assert "2024-11-29" in predicate
    assert "2025-01-03" in predicate


def test_the_rule_case_evaluates_in_order_and_falls_through_to_null(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    spec = SourceSpec(service="yellow", year=2024, month=1)
    case = quarantine_case(rules_for(_config(), spec))
    row = connection.execute(
        f"""
        select {case} from (
            select
                timestamp '2024-01-05 10:00:00' as pickup_ts,
                timestamp '2024-01-05 09:00:00' as dropoff_ts,
                cast(null as double) as trip_distance,
                cast(-1.0 as double) as fare_amount,
                cast(2 as smallint) as passenger_count,
                cast(0.0 as double) as tip_amount,
                cast(0.0 as double) as tolls_amount,
                cast(null as double) as congestion_surcharge,
                cast(null as double) as airport_fee
        )
        """
    ).fetchone()
    assert row is not None
    # Three rules match this row. The earliest one wins, which is what makes
    # the per rule counts a partition rather than an overlapping tally.
    assert row[0] == "dropoff_before_pickup"


def test_the_duplicate_key_is_the_business_key_and_not_our_own_id() -> None:
    sql = duplicate_flag_sql()
    assert "trip_id" not in sql.split("order by")[0]
    assert "order by trip_id" in sql
    for column in BUSINESS_KEY:
        assert column in sql
    assert "vintage" not in BUSINESS_KEY


# ---------------------------------------------------------------------------
# The source registry
# ---------------------------------------------------------------------------


def _registry(connection: duckdb.DuckDBPyConnection, root: Path, backend: str) -> SourceRegistry:
    return SourceRegistry(_config(backend=backend), connection, paths=Paths(root=root))


def test_the_registry_refuses_a_coordinate_era_month(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    registry = _registry(connection, tmp_path, "synthetic")
    with pytest.raises(SourceUnavailableError, match="July 2016 onwards"):
        registry.resolve(SourceSpec(service="yellow", year=2015, month=5))


def test_the_generator_needs_the_zone_reference_and_says_so(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    registry = _registry(connection, tmp_path, "synthetic")
    with pytest.raises(SourceUnavailableError, match="cityflow zones"):
        _ = registry.zones


def test_the_tlc_backend_says_what_it_needs_when_curl_is_absent(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    registry = _registry(connection, tmp_path, "tlc")
    with pytest.raises(SourceUnavailableError, match="curl is not on PATH") as caught:
        registry.resolve(SourceSpec(service="yellow", year=2024, month=1))
    assert "backend: synthetic" in str(caught.value)


def test_a_failed_download_keeps_the_reason_and_leaves_no_partial_file(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted transfer must not leave a truncated parquet that reads as
    a valid file with fewer rows."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/curl")

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        Path(command[command.index("--output") + 1]).write_bytes(b"half a file")
        return subprocess.CompletedProcess(command, 22, "", "curl: (22) 403 Forbidden")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = _registry(connection, tmp_path, "tlc")
    with pytest.raises(SourceUnavailableError, match="403 Forbidden") as caught:
        registry.resolve(SourceSpec(service="yellow", year=2024, month=1))
    assert "egress policy" in str(caught.value)
    assert list((tmp_path / "raw").glob("*")) == []


def test_a_downloaded_file_is_described_and_schema_checked(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = SourceSpec(service="fhvhv", year=2019, month=6)
    staged = tmp_path / "staged.parquet"
    _write_row(connection, staged, FHVHV_2019_ROW)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/curl")

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert spec.url in command
        Path(command[command.index("--output") + 1]).write_bytes(staged.read_bytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = _registry(connection, tmp_path, "tlc")
    resolved = registry.resolve(spec)

    assert resolved.backend == "tlc"
    assert resolved.row_count == 1
    assert resolved.bytes_on_disk > 0
    assert resolved.path.name == spec.filename
    assert not resolved.path.with_suffix(".parquet.partial").exists()

    missing, unexpected = registry.check_schema(resolved)
    assert missing == set()
    assert unexpected == set()

    # A second resolve finds the file already on disk and does not fetch again.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("fetched twice"))
    assert registry.resolve(spec).row_count == 1


def test_the_schema_check_separates_a_missing_column_from_a_new_one(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = SourceSpec(service="fhvhv", year=2019, month=6)
    row = {k: v for k, v in FHVHV_2019_ROW.items() if k != "tolls"}
    row["some_new_tlc_column"] = "1"
    target = tmp_path / "raw" / spec.filename
    target.parent.mkdir(parents=True)
    _write_row(connection, target, row)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/curl")

    registry = _registry(connection, tmp_path, "tlc")
    missing, unexpected = registry.check_schema(registry.resolve(spec))
    assert missing == {"tolls"}
    assert unexpected == {"some_new_tlc_column"}


# ---------------------------------------------------------------------------
# The generator's own arithmetic
# ---------------------------------------------------------------------------


def test_the_expected_row_count_includes_the_duplicates_it_plants() -> None:
    config = _config()
    spec = SourceSpec(service="yellow", year=2024, month=1)
    base = int(config.synthetic.trips_per_month["yellow"] * 0.1)
    assert expected_row_count(config, spec, 0.1) > base
    assert total_target(config, 0.1) == sum(
        expected_row_count(config, source, 0.1) for source in config.sources()
    )


def test_the_profile_tables_are_the_length_the_generator_assumes() -> None:
    """Cheap, and it catches the class of bug where an hour is dropped from a
    24 element tuple and every later hour silently shifts by one."""
    sanity_check_profiles()


def test_the_zone_reference_reads_back_with_the_unknown_codes_flagged() -> None:
    zones = load_zones(REPO / "data" / "reference" / "dim_zone.csv")
    assert len(zones) > 250
    unknown = {z.zone_id for z in zones if z.is_unknown}
    assert unknown == {264, 265}
    assert {z.zone_id for z in zones if z.is_airport} == {1, 132, 138}
    assert len({z.zone_id for z in zones}) == len(zones)
