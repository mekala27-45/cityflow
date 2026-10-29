"""The probe: a real build's schema and size, read from footers before any download.

What is pinned here is the comparison and the reporting, against parquet files
written locally with the exact column names of each vintage. The one path not
exercised is the remote read itself, which needs the TLC host; the probe
workflow runs that path on a runner, and everything it relies on underneath
(the comparison, the footer reads, the grouping) is the code tested here.
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
from cityflow_ingest import probe as probe_module
from cityflow_ingest.probe import (
    head_path,
    head_url,
    probe_source,
    probe_window,
    schema_groups,
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
    assert ("lpep_pickup_datetime", "TIMESTAMP") in first.columns
    assert not second.published and not second.readable
    assert second.error == "no such file"


def test_a_file_that_is_not_parquet_is_reported_not_raised(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="yellow", year=2024, month=1)
    (tmp_path / spec.filename).write_bytes(b"not a parquet file")

    result = probe_source(connection, spec, str(tmp_path / spec.filename), head_path)

    assert result.published and not result.readable
    assert result.error is not None


def test_a_month_before_the_service_existed_is_reported_not_raised(
    connection: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    spec = SourceSpec(service="green", year=2013, month=1)

    result = probe_source(connection, spec, str(tmp_path / spec.filename), head_path)

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


def _completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["curl"], code, stdout, "")


def test_head_reads_the_final_response_after_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = (
        "HTTP/1.1 301 Moved Permanently\r\nContent-Length: 0\r\nLocation: elsewhere\r\n\r\n"
        "HTTP/2 200\r\ncontent-length: 48123456\r\ncontent-type: binary/octet-stream\r\n\r\n"
    )
    monkeypatch.setattr(probe_module.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(probe_module.subprocess, "run", lambda *a, **k: _completed(headers))

    answer = head_url("https://example.invalid/yellow_tripdata_2024-01.parquet")

    assert answer.published and answer.bytes == 48_123_456


def test_head_treats_a_refusal_as_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_module.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(
        probe_module.subprocess, "run", lambda *a, **k: _completed("HTTP/1.1 403 Forbidden\r\n\r\n")
    )

    answer = head_url("https://example.invalid/fhvhv_tripdata_2031-01.parquet")

    assert not answer.published and answer.detail == "HTTP 403"


def test_head_reports_a_transfer_failure_and_a_missing_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe_module.shutil, "which", lambda name: None)
    assert head_url("https://example.invalid/x.parquet").detail == "curl is not on PATH"

    monkeypatch.setattr(probe_module.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(probe_module.subprocess, "run", lambda *a, **k: _completed("", 6))
    assert head_url("https://example.invalid/x.parquet").detail.startswith("curl exit 6")


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
    """The 2021 yellow files already carry an airport_fee column, a year before
    the vintage table dates the fee. Whether that column holds anything decides
    whether the dating is right, and the footer statistics answer it."""
    months = [SourceSpec(service="yellow", year=2021, month=m) for m in (11, 12)]
    for spec in months:
        columns = vintage_columns(spec) | {"airport_fee"}
        empty = {"airport_fee", "congestion_surcharge"} if spec.month == 11 else {"airport_fee"}
        write_file(connection, tmp_path / spec.filename, columns, rows=4, empty=empty)

    probes = probe_window(connection, months, local_dir=tmp_path)
    groups = schema_groups(probes)

    assert probes[0].empty == ("airport_fee", "congestion_surcharge")
    assert probes[0].unexpected == ("airport_fee",)
    assert len(groups) == 1 and groups[0]["empty"] == ["airport_fee"]
