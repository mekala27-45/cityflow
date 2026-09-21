"""The command line, exercised in process against a scratch tree.

Every test here points `CITYFLOW_ROOT` at a `tmp_path`, so nothing reads the
committed warehouse or writes into `web/public/data`. `repo_root` is cached, so
the cache is cleared on the way in and on the way out; a stale cache would make
these tests pass while the next module quietly built against the wrong tree.

The assertions are on exit codes and on the substance of what is printed. They
deliberately do not assert on table borders or column widths: the exit code is
the contract a Makefile depends on, and the words are the contract a person
depends on.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import build_zone_fixture
import pytest
from typer.testing import CliRunner

from cityflow.cli import app
from cityflow_core import config as config_module
from cityflow_core import load_config, session
from cityflow_core.paths import repo_root
from cityflow_ingest.synthetic import holidays

REPO = Path(__file__).resolve().parent.parent

runner = CliRunner()


@pytest.fixture
def scratch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An empty repository root carrying only the metric catalog."""
    monkeypatch.setenv("CITYFLOW_ROOT", str(tmp_path))
    # Rich sizes its tables to the terminal. Eighty columns folds metric names
    # across lines and turns an assertion about content into an assertion about
    # wrapping, so the width is pinned.
    monkeypatch.setenv("COLUMNS", "200")
    repo_root.cache_clear()
    (tmp_path / "metrics").mkdir()
    shutil.copy(REPO / "metrics" / "metrics.yml", tmp_path / "metrics" / "metrics.yml")
    yield tmp_path
    repo_root.cache_clear()


def test_version_agrees_with_the_installed_package(scratch_root: Path) -> None:
    """Not a literal. A test that pins the version string is a third place the
    number lives, and it fails on every bump for no reason anybody learns
    from. What is worth asserting is that the command and the package agree.
    """
    from importlib.metadata import version as installed_version

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert installed_version("cityflow") in result.output


def test_doctor_fails_on_a_machine_that_cannot_build(scratch_root: Path) -> None:
    """An empty tree is missing almost everything, and doctor has to say so
    rather than let a build discover it halfway through."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, result.output
    assert "zone shapefile" in result.output
    assert "warehouse" in result.output
    # The remedy is printed next to the failure, not left to the reader.
    assert "cityflow ingest" in result.output


def test_doctor_passes_when_everything_it_checks_is_present(
    scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scratch_root / "data" / "reference").mkdir(parents=True)
    (scratch_root / "data" / "reference" / "dim_zone.csv").write_text("zone_id\n", encoding="utf-8")
    (scratch_root / "raw" / "zones").mkdir(parents=True)
    (scratch_root / "raw" / "zones" / "taxi_zones.shp").write_bytes(b"0")
    (scratch_root / "warehouse").mkdir()
    (scratch_root / "warehouse" / "cityflow.duckdb").write_bytes(b"0")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    # Seven checks, all of them reporting yes. Counting them is what makes this
    # a test of the check list rather than of the exit code alone.
    assert result.output.count("yes") == 7, result.output


def test_metrics_list_shows_every_defined_metric(scratch_root: Path) -> None:
    result = runner.invoke(app, ["metrics"])
    assert result.exit_code == 0, result.output
    for name in ("trips", "tip_rate", "tipped_share", "unknown_zone_share"):
        assert name in result.output
    # The owner column is read from the layer, not invented by the command.
    assert "analytics" in result.output


def test_metrics_list_is_the_default_action(scratch_root: Path) -> None:
    explicit = runner.invoke(app, ["metrics", "list"])
    assert explicit.exit_code == 0, explicit.output
    assert "tip_rate" in explicit.output


def test_metrics_compile_prints_both_expressions(scratch_root: Path) -> None:
    """The compiled form is the whole point of the layer: one metric, two
    engines, and a reader able to see that they are the same definition."""
    result = runner.invoke(app, ["metrics", "compile", "tip_rate"])
    assert result.exit_code == 0, result.output
    assert "fct_trip" in result.output
    assert "tip_obs_tip_sum" in result.output
    assert "population" in result.output


def test_metrics_compile_without_a_name_is_refused(scratch_root: Path) -> None:
    result = runner.invoke(app, ["metrics", "compile"])
    assert result.exit_code == 2, result.output
    assert "compile needs a metric name" in result.output


def test_metrics_compile_of_an_undefined_metric_names_what_is_defined(
    scratch_root: Path,
) -> None:
    result = runner.invoke(app, ["metrics", "compile", "revenue_per_passenger"])
    assert result.exit_code != 0
    assert isinstance(result.exception, KeyError)
    assert "is not a defined metric" in str(result.exception)


def test_metrics_rejects_an_unknown_action(scratch_root: Path) -> None:
    result = runner.invoke(app, ["metrics", "explain"])
    assert result.exit_code == 2, result.output
    assert "Unknown action" in result.output
    assert "list or compile" in result.output


def test_metrics_without_a_catalog_says_where_it_looked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CITYFLOW_ROOT", str(tmp_path))
    repo_root.cache_clear()
    try:
        result = runner.invoke(app, ["metrics"])
        assert result.exit_code != 0
        assert "No metric definitions at" in str(result.exception)
    finally:
        repo_root.cache_clear()


def test_zones_refuses_to_run_without_the_shapefile(scratch_root: Path) -> None:
    """The TLC shapefile is not redistributable, so its absence is the normal
    first run and has to point somewhere rather than raise."""
    result = runner.invoke(app, ["zones"])
    assert result.exit_code == 2, result.output
    assert "taxi_zones" in result.output
    assert "docs/runbook.md" in result.output
    assert not (scratch_root / "data" / "reference" / "dim_zone.csv").exists()


def test_reconcile_refuses_without_a_shipped_layer(scratch_root: Path) -> None:
    """Nothing to compare the warehouse against is not the same as agreement."""
    (scratch_root / "warehouse").mkdir()
    result = runner.invoke(app, ["reconcile"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# The commands that build something
# ---------------------------------------------------------------------------

SINGLE_SERVICE_CONFIG = """
backend: synthetic
services: [green]
start: 2024-06-01
end: 2024-06-01
detail_month: 2024-06-01
duckdb_memory_limit: 1GB
duckdb_threads: 2
"""


@pytest.fixture
def buildable_root(scratch_root: Path) -> Iterator[Path]:
    """A tree the ingest command can actually run in: one service, one month.

    Green is the small service, so a full month at a fiftieth of scale is about
    a thousand rows: small enough for a unit test, large enough that the
    injected defects land and the quarantine table has rows in it, and the
    command still takes the production path through the registry, the
    normalizer and every quarantine rule.
    """
    (scratch_root / "config").mkdir()
    (scratch_root / "config" / "cityflow.yml").write_text(SINGLE_SERVICE_CONFIG, encoding="utf-8")
    (scratch_root / "data" / "reference").mkdir(parents=True)
    (scratch_root / "data" / "reference" / "dim_zone.csv").write_text(
        (REPO / "data" / "reference" / "dim_zone.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (scratch_root / "transform" / "seeds").mkdir(parents=True)
    (scratch_root / "transform" / "seeds" / "holiday.csv").write_text(
        (REPO / "transform" / "seeds" / "holiday.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    holidays.cache_clear()
    yield scratch_root
    holidays.cache_clear()


def test_ingest_lands_rows_and_accounts_for_the_rest(buildable_root: Path) -> None:
    result = runner.invoke(app, ["ingest", "--scale", "0.02", "--keep-source"])
    assert result.exit_code == 0, result.output
    assert "backend synthetic" in result.output
    assert "green:2024-06" in result.output
    assert "Quarantine, by rule" in result.output
    assert "landed in" in result.output

    warehouse = buildable_root / "warehouse" / "cityflow.duckdb"
    assert warehouse.is_file()
    # --keep-source, so the generated parquet is still there to iterate on.
    assert list((buildable_root / "raw").glob("*.parquet"))

    with session(load_config(), warehouse, read_only=True) as connection:
        landed = connection.execute("select count(*) from stg_trip").fetchone()
        audited = connection.execute(
            "select sum(source_rows), sum(clean_rows), sum(quarantined_rows) from source_audit"
        ).fetchone()
    assert landed is not None and landed[0] > 0
    assert audited is not None
    assert audited[0] == audited[1] + audited[2]


def test_ingest_fresh_starts_the_warehouse_over(buildable_root: Path) -> None:
    """Resumable by default, restartable on request. Ingesting the same month
    twice without --fresh would double it, which is what --fresh is for."""
    assert runner.invoke(app, ["ingest", "--scale", "0.02"]).exit_code == 0
    config = load_config()
    warehouse = buildable_root / "warehouse" / "cityflow.duckdb"
    with session(config, warehouse, read_only=True) as connection:
        first = connection.execute("select count(*) from stg_trip").fetchone()

    result = runner.invoke(app, ["ingest", "--scale", "0.02", "--fresh"])
    assert result.exit_code == 0, result.output
    with session(config, warehouse, read_only=True) as connection:
        again = connection.execute("select count(*) from stg_trip").fetchone()
    assert first is not None and again is not None
    assert again[0] == first[0]


def test_ingest_takes_a_backend_override_on_the_command_line(
    buildable_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override exists so the same committed configuration can build either
    warehouse, and a tlc run that cannot reach the published files has to fail
    loudly rather than quietly falling back to the generator.

    The unreachable host is planted rather than assumed. The first version of
    this test asserted a non zero exit with no such patch, and passed, because
    the machine it was written on is denied egress to the TLC distribution host
    by policy. It failed the first time it ran on a runner with open egress,
    where the download succeeded and the exit code was zero. A test whose
    verdict depends on the network of the machine running it is a test of that
    machine. `.invalid` is reserved by RFC 2606 and resolves nowhere, on any
    runner, forever.
    """
    monkeypatch.setattr(
        config_module,
        "TLC_URL_TEMPLATE",
        "https://tlc.invalid/trip-data/{service}_tripdata_{year:04d}-{month:02d}.parquet",
    )
    result = runner.invoke(app, ["ingest", "--backend", "tlc", "--scale", "0.02"])

    assert result.exit_code != 0, result.output
    assert "backend tlc" in result.output
    # The override reached the registry: the failure names the host it could
    # not reach, rather than any generator path.
    assert "tlc.invalid" in result.output
    # And it did not fall back. A silent fallback would leave a generated
    # parquet behind and report success, which is the failure this guards.
    assert not list((buildable_root / "raw").glob("*.parquet")), (
        "A tlc run that cannot reach the source wrote a source file anyway, "
        "which means it fell back to the generator without saying so."
    )


def test_zones_writes_the_reference_and_the_geojson(scratch_root: Path) -> None:
    """Run against the fixture shapefile, so this covers the command on a
    runner rather than skipping there.

    The real file is not redistributable, and for a long time that meant this
    test, the one that exercises the command a person actually types, never ran
    anywhere except the machine it was written on.
    """
    build_zone_fixture.build(
        REPO / "data" / "reference" / "dim_zone.csv",
        scratch_root / "raw" / "zones" / "taxi_zones",
    )

    result = runner.invoke(app, ["zones", "--tolerance", "300"])
    assert result.exit_code == 0, result.output
    assert "Zone geometry" in result.output
    assert "zones dissolved from multiple parts" in result.output

    reference = scratch_root / "data" / "reference" / "dim_zone.csv"
    assert reference.is_file()
    with reference.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) > 250
    assert rows[0]["zone_id"] == "1"
    # The unknown codes are written out, not dropped, and are flagged as such.
    unknown = [row for row in rows if row["is_unknown"] == "1"]
    assert {row["zone_id"] for row in unknown} == {"264", "265"}
    assert all(row["centroid_lon"] == "" for row in unknown)

    geojson = json.loads(
        (scratch_root / "web" / "public" / "data" / "zones.geojson").read_text(encoding="utf-8")
    )
    assert len(geojson["features"]) == len(rows) - 5
