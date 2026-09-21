"""Configuration, path resolution and the DuckDB session factory.

Three small modules that everything else stands on. The configuration refuses
unknown keys because a typo that silently takes a default is the failure mode
it exists to prevent; the paths resolve from one anchor because hardcoded
relative paths are why a build works from the repository root and nowhere else;
and the session factory is the one place a connection is opened, so a query
that works in a test works in the pipeline.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from cityflow_core.config import (
    TLC_URL_TEMPLATE,
    CityflowConfig,
    QuarantineThresholds,
    SourceSpec,
    load_config,
)
from cityflow_core.duck import connect, session
from cityflow_core.paths import MARKER, WORKSPACE_KEY, Paths, repo_root

REPO = Path(__file__).resolve().parent.parent

BASE = {"start": "2024-01-01", "end": "2024-01-01", "detail_month": "2024-01-01"}


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("CITYFLOW_ROOT", str(tmp_path))
    monkeypatch.delenv("CITYFLOW_BACKEND", raising=False)
    repo_root.cache_clear()
    yield tmp_path
    repo_root.cache_clear()


# ---------------------------------------------------------------------------
# The build window
# ---------------------------------------------------------------------------


def test_the_window_runs_forwards() -> None:
    with pytest.raises(ValidationError, match="end must not be before start"):
        CityflowConfig.model_validate({**BASE, "start": "2024-06-01", "end": "2024-01-01"})


def test_the_detail_month_has_to_be_inside_the_window() -> None:
    """The detail file is the one month published at trip grain. A detail month
    outside the window is a file the browser can reach and the warehouse cannot
    explain."""
    with pytest.raises(ValidationError, match="detail_month must fall inside"):
        CityflowConfig.model_validate(
            {"start": "2024-01-01", "end": "2024-03-01", "detail_month": "2024-05-01"}
        )


def test_the_detail_month_has_to_be_a_month() -> None:
    with pytest.raises(ValidationError, match="first of a month"):
        CityflowConfig.model_validate(
            {"start": "2024-01-01", "end": "2024-03-01", "detail_month": "2024-02-15"}
        )


def test_the_months_roll_over_a_year_boundary() -> None:
    config = CityflowConfig.model_validate(
        {"start": "2024-11-01", "end": "2025-02-01", "detail_month": "2024-12-01"}
    )
    assert config.months() == [
        dt.date(2024, 11, 1),
        dt.date(2024, 12, 1),
        dt.date(2025, 1, 1),
        dt.date(2025, 2, 1),
    ]


def test_a_one_month_window_is_inclusive_at_both_ends() -> None:
    config = CityflowConfig.model_validate(BASE)
    assert config.months() == [dt.date(2024, 1, 1)]


def test_the_sources_are_the_full_cross_in_a_stable_order() -> None:
    config = CityflowConfig.model_validate(
        {"start": "2024-11-01", "end": "2024-12-01", "detail_month": "2024-11-01"}
    )
    sources = config.sources()
    assert len(sources) == 2 * len(config.services)
    assert [str(s) for s in sources[:3]] == [
        "yellow:2024-11",
        "green:2024-11",
        "fhvhv:2024-11",
    ]
    assert config.sources() == sources


# ---------------------------------------------------------------------------
# One source file
# ---------------------------------------------------------------------------


def test_a_source_spec_knows_where_its_file_lives() -> None:
    spec = SourceSpec(service="yellow", year=2024, month=3)
    assert spec.period == dt.date(2024, 3, 1)
    assert spec.period_key == "2024-03"
    assert spec.filename == "yellow_tripdata_2024-03.parquet"
    assert spec.url == TLC_URL_TEMPLATE.format(service="yellow", year=2024, month=3)
    assert spec.url.endswith("yellow_tripdata_2024-03.parquet")
    assert str(spec) == "yellow:2024-03"


def test_a_month_outside_the_calendar_is_refused() -> None:
    with pytest.raises(ValidationError):
        SourceSpec(service="yellow", year=2024, month=13)
    with pytest.raises(ValidationError):
        SourceSpec(service="yellow", year=1999, month=1)


# ---------------------------------------------------------------------------
# Strictness
# ---------------------------------------------------------------------------


def test_a_typo_in_the_config_is_refused_rather_than_defaulted() -> None:
    """The failure this exists to prevent: a misspelled key that silently takes
    the default and a build that quietly does something else."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CityflowConfig.model_validate({**BASE, "duckdb_thread": 4})


def test_the_config_is_frozen() -> None:
    config = CityflowConfig.model_validate(BASE)
    with pytest.raises(ValidationError):
        config.backend = "synthetic"  # type: ignore[misc]


def test_a_threshold_that_makes_no_sense_is_refused() -> None:
    with pytest.raises(ValidationError):
        QuarantineThresholds(max_trip_hours=0.0)
    with pytest.raises(ValidationError):
        QuarantineThresholds(period_tolerance_days=-1)
    assert QuarantineThresholds().max_implied_speed_mph == 80.0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_an_absent_config_file_gives_the_documented_defaults(scratch_root: Path) -> None:
    config = load_config()
    assert config.backend == "tlc"
    assert config.services == ("yellow", "green", "fhvhv")
    assert config.shipped_file_budget_mb == 95.0


def test_a_config_file_is_read_from_the_path_it_is_given(tmp_path: Path) -> None:
    target = tmp_path / "custom.yml"
    target.write_text(
        "backend: synthetic\nstart: 2023-01-01\nend: 2023-02-01\n"
        "detail_month: 2023-01-01\nduckdb_threads: 7\n",
        encoding="utf-8",
    )
    config = load_config(target)
    assert config.backend == "synthetic"
    assert config.duckdb_threads == 7
    assert config.months() == [dt.date(2023, 1, 1), dt.date(2023, 2, 1)]


def test_the_backend_override_is_the_only_one(
    scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same committed configuration builds either warehouse without an
    edit. Everything else is a property of the build and belongs in version
    control where a reviewer can see it."""
    (scratch_root / "config").mkdir()
    (scratch_root / "config" / "cityflow.yml").write_text(
        "backend: synthetic\nduckdb_threads: 2\n", encoding="utf-8"
    )
    assert load_config().backend == "synthetic"

    monkeypatch.setenv("CITYFLOW_BACKEND", "tlc")
    overridden = load_config()
    assert overridden.backend == "tlc"
    assert overridden.duckdb_threads == 2


def test_an_unknown_backend_in_the_environment_is_refused(
    scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CITYFLOW_BACKEND", "postgres")
    with pytest.raises(ValueError, match="expected 'tlc' or 'synthetic'"):
        load_config()


def test_an_empty_config_file_is_not_a_parse_error(scratch_root: Path) -> None:
    (scratch_root / "config").mkdir()
    (scratch_root / "config" / "cityflow.yml").write_text("# nothing yet\n", encoding="utf-8")
    assert load_config().backend == "tlc"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_the_root_is_overridden_by_the_environment(scratch_root: Path) -> None:
    assert repo_root() == scratch_root.resolve()
    assert Paths.resolve().root == scratch_root.resolve()


def test_every_path_hangs_off_the_one_anchor(scratch_root: Path) -> None:
    paths = Paths(root=scratch_root)
    assert paths.reference == scratch_root / "data" / "reference"
    assert paths.raw == scratch_root / "raw"
    assert paths.warehouse_dir == scratch_root / "warehouse"
    assert paths.warehouse == scratch_root / "warehouse" / "cityflow.duckdb"
    assert paths.transform == scratch_root / "transform"
    assert paths.metrics_file == scratch_root / "metrics" / "metrics.yml"
    assert paths.shipped == scratch_root / "web" / "public" / "data"
    assert paths.manifest == paths.shipped / "manifest.json"
    assert paths.docs == scratch_root / "docs"


def test_ensure_creates_what_the_pipeline_writes_into(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)
    paths.ensure()
    for directory in (paths.raw, paths.warehouse_dir, paths.shipped, paths.reference):
        assert directory.is_dir()
    paths.ensure()  # idempotent, because a resumed build calls it again


def test_the_workspace_root_is_found_without_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The marker distinguishes the workspace root from the four member
    packages, each of which also has a pyproject."""
    monkeypatch.delenv("CITYFLOW_ROOT", raising=False)
    repo_root.cache_clear()
    try:
        found = repo_root()
        assert found == REPO
        assert WORKSPACE_KEY in (found / MARKER).read_text(encoding="utf-8")
    finally:
        repo_root.cache_clear()


def test_a_tree_with_no_marker_says_what_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITYFLOW_ROOT", raising=False)
    monkeypatch.setattr("cityflow_core.paths.MARKER", "no_such_marker.toml")
    repo_root.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Set CITYFLOW_ROOT"):
            repo_root()
    finally:
        repo_root.cache_clear()


# ---------------------------------------------------------------------------
# The session factory
# ---------------------------------------------------------------------------


def test_an_in_memory_connection_takes_the_configured_settings(scratch_root: Path) -> None:
    config = CityflowConfig.model_validate({**BASE, "duckdb_threads": 3})
    connection = connect(config)
    try:
        threads = connection.execute("select current_setting('threads')").fetchone()
        assert threads is not None and int(threads[0]) == 3
        limit = connection.execute("select current_setting('memory_limit')").fetchone()
        assert limit is not None
    finally:
        connection.close()


def test_a_file_connection_creates_the_directory_it_needs(scratch_root: Path) -> None:
    config = CityflowConfig.model_validate(BASE)
    target = scratch_root / "deep" / "nested" / "cityflow.duckdb"
    with session(config, target) as connection:
        connection.execute("create table t as select 1 as n")
    assert target.is_file()
    assert (scratch_root / "warehouse" / "tmp").is_dir()


def test_a_read_only_session_cannot_write(scratch_root: Path) -> None:
    """The benchmark reads a warehouse a dbt run may still be writing, so it
    opens without taking the write lock."""
    config = CityflowConfig.model_validate(BASE)
    target = scratch_root / "warehouse" / "cityflow.duckdb"
    with session(config, target) as connection:
        connection.execute("create table t as select 1 as n")

    with session(config, target, read_only=True) as connection:
        rows = connection.execute("select count(*) from t").fetchone()
        assert rows is not None and rows[0] == 1
        with pytest.raises(duckdb.Error):
            connection.execute("create table u as select 2 as n")


def test_a_session_closes_even_when_the_body_raises(scratch_root: Path) -> None:
    config = CityflowConfig.model_validate(BASE)
    escaped: duckdb.DuckDBPyConnection | None = None
    with pytest.raises(ZeroDivisionError), session(config) as connection:
        escaped = connection
        _ = 1 / 0
    assert escaped is not None
    with pytest.raises(duckdb.Error):
        escaped.execute("select 1")
