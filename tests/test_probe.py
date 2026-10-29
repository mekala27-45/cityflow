"""The probe: a real build's schema and size, read from footers before any download.

What is pinned here is the comparison and the reporting, against parquet files
written locally with the exact column names of each vintage, and the way a
remote refusal is read: a stand in connection raises the errors DuckDB raises
for a missing object and for the host's firewall. The remote read itself needs
the TLC host; the probe workflow runs it on a runner, and everything it relies
on underneath (the comparison, the footer reads, the grouping) is tested here.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import annotate_probe
import duckdb
import pytest
from typer.testing import CliRunner

from cityflow.cli import app
from cityflow_core.config import SourceSpec
from cityflow_core.paths import repo_root
from cityflow_ingest.probe import (
    FileProbe,
    census_lines,
    group_notes,
    probe_source,
    probe_window,
    read_footer,
    schema_groups,
    status_codes,
    write_report,
)
from cityflow_ingest.vintage import compare_columns, expected_columns, resolve_vintage

runner = CliRunner()
TEXT_COLUMNS = {
    "hvfhs_license_num",
    "dispatching_base_num",
    "originating_base_num",
    "store_and_fwd_flag",
}


@pytest.fixture
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    handle = duckdb.connect()
    yield handle
    handle.close()


def write_file(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    columns: Iterable[str],
    rows: int = 3,
    *,
    empty: Iterable[str] = (),
) -> None:
    """A tiny parquet file carrying exactly these column names, some all null."""
    nulls = set(empty)
    expressions = []
    for name in sorted(columns):
        lowered = name.lower()
        if name in nulls:
            expressions.append(f'cast(null as double) as "{name}"')
        elif lowered.endswith("datetime"):
            expressions.append(f"timestamp '2024-01-01 08:00:00' + to_minutes(i) as \"{name}\"")
        elif lowered in TEXT_COLUMNS or lowered.endswith("_flag"):
            expressions.append(f"'HV0003' as \"{name}\"")
        else:
            expressions.append(f'cast(i as double) as "{name}"')
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"copy (select {', '.join(expressions)} from range({rows}) t(i)) "
        f"to '{path}' (format parquet)"
    )


def vintage_columns(spec: SourceSpec) -> set[str]:
    vintage = resolve_vintage(spec.service, spec.period)
    return expected_columns(vintage) | set(vintage.ignored)


def test_columns_that_match_their_vintage_report_nothing() -> None:
    vintage = resolve_vintage("yellow", dt.date(2024, 3, 1))

    check = compare_columns(expected_columns(vintage) | set(vintage.ignored), vintage)

    assert (check.missing, check.unexpected, check.case_only) == ((), (), ())


def test_a_column_differing_only_in_case_is_not_called_missing() -> None:
    """DuckDB binds Airport_fee to airport_fee, so the normalizer reads it. The
    exact comparison would call it missing and stop the build."""
    vintage = resolve_vintage("yellow", dt.date(2024, 3, 1))
    columns = (expected_columns(vintage) - {"airport_fee"}) | {"Airport_fee"}

    check = compare_columns(columns, vintage)

    assert check.case_only == (("airport_fee", "Airport_fee"),)
    assert check.missing == ()
    assert check.unexpected == ()


def test_a_missing_column_and_an_unexpected_one_are_reported_apart() -> None:
    vintage = resolve_vintage("fhvhv", dt.date(2023, 5, 1))
    columns = (expected_columns(vintage) - {"bcf"}) | {"cbd_congestion_fee"}

    check = compare_columns(columns, vintage)

    assert check.missing == ("bcf",)
    assert check.unexpected == ("cbd_congestion_fee",)
    assert check.case_only == ()


def test_an_ignored_column_is_not_unexpected_whatever_its_case() -> None:
    vintage = resolve_vintage("green", dt.date(2022, 2, 1))
    columns = expected_columns(vintage) | {"Store_And_Fwd_Flag", "ehail_fee"}

    assert compare_columns(columns, vintage).unexpected == ()


def test_a_window_of_local_files_is_read_from_the_footer(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    present = SourceSpec(service="green", year=2024, month=1)
    absent = SourceSpec(service="green", year=2024, month=2)
    write_file(connection, tmp_path / present.filename, vintage_columns(present), rows=7)

    probes = probe_window(connection, [present, absent], local_dir=tmp_path)

    first, second = probes
    assert first.readable and first.rows == 7 and first.row_groups == 1
    assert first.bytes == (tmp_path / present.filename).stat().st_size
    assert first.vintage == "green_v3_congestion"
    # Physical types, from the footer: a timestamp is stored as INT64.
    assert ("lpep_pickup_datetime", "INT64") in first.columns
    assert ("fare_amount", "DOUBLE") in first.columns
    assert not second.published and not second.readable
    assert second.error == "no such file"


def test_a_file_that_is_not_parquet_is_reported_not_raised(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="yellow", year=2024, month=1)
    (tmp_path / spec.filename).write_bytes(b"not a parquet file")

    result = probe_source(connection, spec, str(tmp_path / spec.filename), local=True)

    assert result.published and not result.readable
    assert result.error is not None


def test_a_month_before_the_service_existed_is_reported_not_raised(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="green", year=2013, month=1)

    result = probe_source(connection, spec, str(tmp_path / spec.filename), local=True)

    assert result.vintage is None and not result.published
    assert result.error is not None and "No vintage covers green" in result.error


def test_groups_merge_consecutive_months_and_split_where_the_schema_drifts(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    months = [SourceSpec(service="yellow", year=2022, month=m) for m in (11, 12)]
    months += [SourceSpec(service="yellow", year=2023, month=m) for m in (1, 2)]
    for spec in months:
        columns = vintage_columns(spec)
        if spec.year == 2023:
            columns = (columns - {"airport_fee"}) | {"Airport_fee"}
        write_file(connection, tmp_path / spec.filename, columns, rows=2)

    groups = schema_groups(probe_window(connection, months, local_dir=tmp_path))

    assert [(g["first"], g["last"], g["months"], g["rows"]) for g in groups] == [
        ("2022-11", "2022-12", 2, 4),
        ("2023-01", "2023-02", 2, 4),
    ]
    assert groups[1]["case_only"] == [["airport_fee", "Airport_fee"]]


class _Refusing:
    """Stands in for a connection to the TLC host: raises the given errors in
    turn, then answers from a real connection once they run out."""

    def __init__(self, errors: list[str], real: duckdb.DuckDBPyConnection) -> None:
        self.errors = list(errors)
        self.real = real
        self.calls = 0

    def execute(self, sql: str) -> duckdb.DuckDBPyConnection:
        self.calls += 1
        if self.errors:
            raise duckdb.IOException(self.errors.pop(0))
        return self.real.execute(sql)


FIREWALL = (
    'HTTP Error: Full download failed to to URL "https://d37ci6vzurychx.cloudfront.net/'
    'trip-data/yellow_tripdata_2025-06.parquet": 202 (Accepted)'
)
GONE = "HTTP Error: HTTP GET error on 'https://example.invalid/x.parquet' (HTTP 403)"


def test_a_firewall_challenge_is_retried_after_a_pause_that_doubles(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="green", year=2024, month=1)
    path = tmp_path / spec.filename
    write_file(connection, path, vintage_columns(spec), rows=4)
    stand_in = _Refusing([FIREWALL, FIREWALL], connection)
    pauses: list[float] = []

    rows, attempts = read_footer(stand_in, str(path), pause=10.0, sleep=pauses.append)  # type: ignore[arg-type]

    assert attempts == 3 and pauses == [10.0, 20.0]
    assert rows, "the footer was read once the refusals ran out"


def test_a_firewall_that_never_relents_is_an_error_not_a_missing_month(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    spec = SourceSpec(service="yellow", year=2025, month=6)
    stand_in = _Refusing([FIREWALL] * 5, connection)

    result = probe_source(
        stand_in,  # type: ignore[arg-type]
        spec,
        spec.url,
        local=False,
        retries=2,
        sleep=lambda seconds: None,
    )

    assert result.published and not result.readable
    assert result.error is not None and "202 (Accepted)" in result.error
    assert stand_in.calls == 3


def test_a_missing_object_is_unpublished_at_once(connection: duckdb.DuckDBPyConnection) -> None:
    spec = SourceSpec(service="fhvhv", year=2031, month=1)
    stand_in = _Refusing([GONE], connection)

    result = probe_source(stand_in, spec, spec.url, local=False, sleep=pytest.fail)  # type: ignore[arg-type]

    assert not result.published
    assert result.error is not None and "403" in result.error
    assert stand_in.calls == 1


def test_a_census_reads_one_column_and_counts_what_it_holds(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The question a footer cannot answer: does a column the vintage table
    dates later already carry a fee, or only zeros?"""
    spec = SourceSpec(service="yellow", year=2021, month=2)
    path = tmp_path / spec.filename
    columns = sorted(vintage_columns(spec))
    select = ", ".join(f'cast(i as double) as "{name}"' for name in columns)
    connection.execute(
        f"copy (select {select}, case when i % 4 = 0 then 1.25 when i % 4 = 1 then 0.0 end "
        f"as Airport_fee from range(8) t(i)) to '{path}' (format parquet)"
    )

    result = probe_source(connection, spec, str(path), local=True, census="airport_fee")

    assert result.census is not None
    assert result.census.column == "Airport_fee"
    assert (result.census.non_null, result.census.non_zero) == (4, 2)
    assert (result.census.minimum, result.census.maximum) == (0.0, 1.25)
    assert census_lines([result]) == [
        "yellow:2021-02 Airport_fee: 4 of 8 rows carry a value, 2 non zero, 0 to 1.25"
    ]
    assert result.ranges == (("Airport_fee", 0.0, 1.25),)


def test_a_census_of_a_column_the_file_lacks_is_skipped(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="green", year=2024, month=1)
    write_file(connection, tmp_path / spec.filename, vintage_columns(spec))

    result = probe_source(
        connection, spec, str(tmp_path / spec.filename), local=True, census="airport_fee"
    )

    assert result.census is None
    assert census_lines([result]) == []


def test_remote_files_are_spaced_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three files, two pauses, and every one read from its URL."""
    seen: list[str] = []
    pauses: list[float] = []

    def fake_probe(connection: object, spec: SourceSpec, location: str, **kwargs: object) -> str:
        seen.append(location)
        return location

    import cityflow_ingest.probe as probe_module

    monkeypatch.setattr(probe_module, "probe_source", fake_probe)
    specs = [SourceSpec(service="green", year=2024, month=m) for m in (1, 2, 3)]

    class Quiet:
        def execute(self, sql: str) -> None:
            return None

    probe_window(Quiet(), specs, spacing=0.5, sleep=pauses.append)  # type: ignore[arg-type]

    assert seen == [spec.url for spec in specs]
    assert pauses == [0.5, 0.5]


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A repository root whose build window is two months of the three services."""
    monkeypatch.setenv("CITYFLOW_ROOT", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    repo_root.cache_clear()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "cityflow.yml").write_text(
        "backend: tlc\nservices: [yellow, green, fhvhv]\n"
        "start: 2024-01-01\nend: 2024-02-01\ndetail_month: 2024-01-01\n",
        encoding="utf-8",
    )
    yield tmp_path
    repo_root.cache_clear()


def _write_window(root: Path, *, drop: tuple[str, str] | None = None) -> Path:
    files = root / "files"
    with duckdb.connect() as connection:
        for month in (1, 2):
            for service in ("yellow", "green", "fhvhv"):
                spec = SourceSpec.model_validate({"service": service, "year": 2024, "month": month})
                columns = vintage_columns(spec)
                if drop is not None and str(spec) == drop[0]:
                    columns -= {drop[1]}
                write_file(connection, files / spec.filename, columns)
    return files


def test_the_probe_passes_when_every_build_month_is_readable(scratch_root: Path) -> None:
    files = _write_window(scratch_root)
    report = scratch_root / "probe.json"

    result = runner.invoke(
        app, ["probe", "--local-dir", str(files), "--end", "2024-03", "--json", str(report)]
    )

    assert result.exit_code == 0, result.output
    assert "6 published files, 18 rows" in result.output
    document = json.loads(report.read_text(encoding="utf-8"))
    assert len(document["files"]) == 9
    # March is outside the build window, so its absence is reported, not fatal.
    assert "not published" in result.output and "yellow:2024-03" in result.output


def test_the_probe_fails_when_a_build_month_is_missing_a_column(scratch_root: Path) -> None:
    files = _write_window(scratch_root, drop=("fhvhv:2024-02", "sales_tax"))

    result = runner.invoke(app, ["probe", "--local-dir", str(files)])

    assert result.exit_code == 1, result.output
    assert "would stop the build" in result.output
    assert "fhvhv:2024-02" in result.output and "sales_tax" in result.output


def test_the_probe_refuses_a_malformed_month(scratch_root: Path) -> None:
    assert runner.invoke(app, ["probe", "--start", "2024/01"]).exit_code == 2
    assert runner.invoke(app, ["probe", "--start", "2024-05", "--end", "2024-01"]).exit_code == 2


def test_annotations_are_one_per_kind_and_escape_their_newlines(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    good = SourceSpec(service="yellow", year=2024, month=1)
    broken = SourceSpec(service="yellow", year=2024, month=2)
    absent = SourceSpec(service="yellow", year=2024, month=3)
    write_file(connection, tmp_path / good.filename, vintage_columns(good))
    write_file(connection, tmp_path / broken.filename, vintage_columns(broken) - {"tip_amount"})
    report = tmp_path / "probe.json"
    write_report(probe_window(connection, [good, broken, absent], local_dir=tmp_path), report)

    lines = annotate_probe.render(json.loads(report.read_text(encoding="utf-8")))

    kinds = [line.split(" ", 1)[0] for line in lines]
    assert kinds == ["::notice", "::notice", "::notice", "::warning", "::error"]
    assert all("\n" not in line for line in lines)
    assert "%0A" in lines[0] and "2 of 3 files published" in lines[0]
    assert "yellow:2024-02: missing tip_amount" in lines[-1]
    assert annotate_probe.main([str(report)]) == 0
    assert annotate_probe.main([]) == 2


def test_the_annotation_script_runs_as_a_script(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """One subprocess run, so the __main__ guard keeps a test of its own."""
    spec = SourceSpec(service="green", year=2024, month=1)
    write_file(connection, tmp_path / spec.filename, vintage_columns(spec))
    report = tmp_path / "probe.json"
    write_report(probe_window(connection, [spec], local_dir=tmp_path), report)

    result = subprocess.run(
        [sys.executable, str(Path(annotate_probe.__file__)), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("::notice title=probe summary::1 of 1 files published")


def test_a_column_present_but_never_filled_is_reported_as_all_null(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """The 2020 yellow files already carry an airport_fee column, months before
    the vintage table dates the fee. Whether that column holds anything decides
    whether the dating is right, and the footer statistics answer it."""
    months = [SourceSpec(service="yellow", year=2020, month=m) for m in (11, 12)]
    for spec in months:
        columns = vintage_columns(spec) | {"airport_fee"}
        empty = {"airport_fee", "congestion_surcharge"} if spec.month == 11 else {"airport_fee"}
        write_file(connection, tmp_path / spec.filename, columns, rows=4, empty=empty)

    probes = probe_window(connection, months, local_dir=tmp_path)
    groups = schema_groups(probes)

    assert probes[0].empty == ("airport_fee", "congestion_surcharge")
    assert probes[0].unexpected == ("airport_fee",)
    assert len(groups) == 1 and groups[0]["empty"] == ["airport_fee"]
    assert group_notes(groups[0]) == ["unexpected airport_fee (all null)"]


def test_an_unexpected_column_that_carries_values_says_how_many_rows(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Present and filled is a different finding from present and empty: it
    means the vintage table dates the column later than the files do."""
    months = [SourceSpec(service="fhvhv", year=2020, month=m) for m in (11, 12)]
    for spec in months:
        empty = {"airport_fee"} if spec.month == 11 else set()
        write_file(
            connection,
            tmp_path / spec.filename,
            vintage_columns(spec) | {"airport_fee"},
            rows=5,
            empty=empty,
        )

    groups = schema_groups(probe_window(connection, months, local_dir=tmp_path))

    assert groups[0]["unexpected_values"] == {"airport_fee": 5}
    assert group_notes(groups[0]) == ["unexpected airport_fee (values in 5 rows, 0 to 4)"]


def test_a_month_without_null_counts_makes_the_total_unknown() -> None:
    probe = FileProbe(
        source="yellow:2021-02",
        service="yellow",
        period="2021-02",
        location="x",
        vintage="yellow_v3_congestion",
        published=True,
        rows=10,
        unexpected=("airport_fee",),
        values=(("airport_fee", None),),
    )
    group = schema_groups([probe])[0]

    assert group["unexpected_values"] == {"airport_fee": None}
    assert group_notes(group) == ["unexpected airport_fee (no null count in the footer)"]


def test_a_long_annotation_is_split_and_numbered_not_cut() -> None:
    lines = [f"fhvhv:2023-{m:02d} {'x' * 300}" for m in range(1, 13)] * 3

    commands = annotate_probe.annotations("notice", "sizes", lines)

    assert len(commands) > 1
    assert all(len(command) <= 4_096 + 40 for command in commands)
    assert commands[0].startswith(f"::notice title=sizes (1 of {len(commands)})::")
    carried = "%0A".join(command.split("::", 2)[2] for command in commands)
    assert carried.count("fhvhv:2023-") == len(lines)


def test_a_status_is_read_where_duckdb_writes_one_and_never_from_a_file_name() -> None:
    assert status_codes(FIREWALL) == {202}
    assert status_codes(GONE) == {403}
    dropped = (
        "HTTP Error: HTTP GET error on 'https://d37ci6vzurychx.cloudfront.net/trip-data/"
        "fhvhv_tripdata_2025-08.parquet' (HTTP 0 Internal Server Error)"
    )
    assert status_codes(dropped) == {0}
    local = "Invalid Input Error: File '/tmp/pytest-202/yellow_tripdata_2024-01.parquet' too small"
    assert status_codes(local) == set()
