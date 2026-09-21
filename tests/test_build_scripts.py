"""The two build scripts that produce artifacts rather than verdicts.

They are not gates, so they get thinner tests than the gates do. What is worth
asserting is the thing each one exists to guarantee: the Tableau extracts carry
components and never a rate, and the animation is assembled with one palette
and inside its size budget.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import build_demo_gif
import build_tableau_extracts
import build_zone_fixture
import duckdb
import pytest
from PIL import Image

from cityflow_core import Paths

PATHS = Paths.resolve()
SHIPPED = PATHS.shipped

needs_shipped = pytest.mark.skipif(
    not (SHIPPED / "agg_zone_hour.parquet").is_file(),
    reason="No shipped layer; run 'make publish' first.",
)

# A column whose name reads like a rate has no business in an extract. A
# published rate cannot be re-aggregated, and Tableau is where somebody will
# drag Borough onto the view and get a wrong number silently.
RATE_SHAPED = ("_rate", "_share", "_pct", "percent", "avg_", "mean_", "_per_")


@needs_shipped
def test_the_extracts_build_and_carry_only_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Written to tmp_path, never to the repository's own tableau directory. A
    # test that rewrites a tracked file leaves the working tree dirty after a
    # clean run, and the next person to see that spends ten minutes finding out
    # it was the test suite.
    monkeypatch.setattr(
        build_tableau_extracts.Paths,
        "resolve",
        classmethod(lambda cls: Paths(root=PATHS.root)),
    )
    assert build_tableau_extracts.main(["--out", str(tmp_path)]) == 0

    for name in build_tableau_extracts.EXTRACTS:
        target = tmp_path / name
        assert target.is_file(), name
        with target.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        assert header, name
        offenders = [
            column
            for column in header
            if any(fragment in column.lower() for fragment in RATE_SHAPED)
        ]
        assert not offenders, (
            f"{name} publishes {offenders}, which read as rates. A rate cannot "
            "be re-aggregated, so an extract carries the numerator and the "
            "denominator and the workbook divides them."
        )


@needs_shipped
def test_the_extract_totals_match_the_shipped_layer() -> None:
    """An extract that does not reconcile is a second source of truth."""
    connection = duckdb.connect()
    extract = connection.execute(
        f"select sum(trips) from read_csv('{PATHS.root / 'tableau' / 'zone_month.csv'}')"
    ).fetchone()
    shipped = connection.execute(
        f"select sum(trips) from read_parquet('{SHIPPED / 'agg_zone_hour.parquet'}')"
    ).fetchone()
    assert extract is not None and shipped is not None
    assert int(extract[0]) == int(shipped[0])


def test_the_extract_builder_refuses_a_tree_with_no_shipped_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        build_tableau_extracts.Paths,
        "resolve",
        classmethod(lambda cls: Paths(root=tmp_path)),
    )
    assert build_tableau_extracts.main([]) == 2


def _frames(directory: Path, count: int, size: tuple[int, int]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        image = Image.new("RGB", size, (11, 15, 20))
        for x in range(0, size[0], 4):
            for y in range(0, size[1], 4):
                shade = (x + y + index * 7) % 255
                image.putpixel((x, y), (8, shade, 178))
        image.save(directory / f"frame-{index:04d}.png")


def test_the_animation_is_assembled_and_loops(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    _frames(frames, 10, (320, 180))
    target = tmp_path / "demo.gif"
    assert build_demo_gif.main([str(frames), str(target), "--duration", "120"]) == 0
    with Image.open(target) as animation:
        assert animation.n_frames == 10
        assert animation.info["loop"] == 0
        assert animation.info["duration"] == 120
    assert target.stat().st_size <= build_demo_gif.MAX_BYTES


def test_frames_of_different_shapes_are_not_stretched(tmp_path: Path) -> None:
    """Two charts, two aspect ratios, and a heatmap cell that must stay square."""
    frames = tmp_path / "frames"
    _frames(frames, 4, (320, 180))
    _frames(frames / "wide", 3, (200, 200))
    for index, path in enumerate(sorted((frames / "wide").glob("frame-*.png"))):
        path.rename(frames / f"frame-{index + 10:04d}.png")
    target = tmp_path / "demo.gif"
    assert build_demo_gif.main([str(frames), str(target)]) == 0
    with Image.open(target) as animation:
        assert animation.size == (320, 200)
        assert animation.n_frames == 7


def test_an_empty_frame_directory_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "frames"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="No frames"):
        build_demo_gif.main([str(empty), str(tmp_path / "demo.gif")])


# ---------------------------------------------------------------------------
# The zone fixture
# ---------------------------------------------------------------------------

REFERENCE = PATHS.root / "data" / "reference" / "dim_zone.csv"


def _reference_shape() -> tuple[int, int, int]:
    """Polygon records, zone ids with geometry, and zones in several pieces."""
    with REFERENCE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    mappable = [row for row in rows if row["has_geometry"] == "1"]
    return (
        sum(int(row["part_count"]) for row in mappable),
        len(mappable),
        sum(1 for row in mappable if int(row["part_count"]) > 1),
    )


def test_the_fixture_reproduces_the_shape_of_the_real_shapefile(tmp_path: Path) -> None:
    """The fixture is worth having only if it carries the same defect.

    A stand in with one record per zone would let every geometry test pass
    while the dissolve did nothing, which is the failure the dissolve exists
    to prevent. The counts come out of the committed reference rather than
    being written down here, so the two cannot drift apart.
    """
    records, zone_ids, multi_part = _reference_shape()
    stats = build_zone_fixture.build(REFERENCE, tmp_path / "taxi_zones")

    assert stats["polygon_records"] == records
    assert stats["zone_ids"] == zone_ids
    assert stats["multi_part_zones"] == multi_part
    assert stats["polygon_records"] > stats["zone_ids"], (
        "The fixture holds one record per zone, so nothing in it fans out and "
        "every dissolve test built on it is vacuous."
    )
    for suffix in (".shp", ".shx", ".dbf", ".prj"):
        assert (tmp_path / "taxi_zones").with_suffix(suffix).is_file(), suffix


def test_the_fixture_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """A seeded generator that is not reproducible is a generator of noise."""
    first = tmp_path / "first" / "taxi_zones"
    second = tmp_path / "second" / "taxi_zones"
    build_zone_fixture.build(REFERENCE, first)
    build_zone_fixture.build(REFERENCE, second)
    for suffix in (".shp", ".shx", ".dbf"):
        assert first.with_suffix(suffix).read_bytes() == second.with_suffix(suffix).read_bytes(), (
            suffix
        )


def test_the_fixture_refuses_a_missing_reference(tmp_path: Path) -> None:
    assert (
        build_zone_fixture.main(
            [str(tmp_path / "taxi_zones"), "--reference", str(tmp_path / "absent.csv")]
        )
        == 2
    )


def test_the_fixture_builds_from_the_command_line(tmp_path: Path) -> None:
    assert (
        build_zone_fixture.main([str(tmp_path / "taxi_zones"), "--reference", str(REFERENCE)]) == 0
    )
    assert (tmp_path / "taxi_zones.shp").is_file()


def test_the_scripts_are_runnable_as_programs() -> None:
    """The __main__ guards, which nothing else reaches."""
    import subprocess

    for script in ("build_demo_gif.py", "build_tableau_extracts.py", "build_zone_fixture.py"):
        result = subprocess.run(
            [sys.executable, str(PATHS.root / "scripts" / script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, script
        assert "usage" in result.stdout.lower()


@needs_shipped
def test_the_extracts_are_byte_identical_across_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild that changes nothing must produce no diff.

    It did not, twice over, and both causes are the kind that hide. A parallel
    sum of doubles is not associative, so the last digit moved between runs;
    and five zone ids share a name with another zone, so ordering by name left
    those rows in an order the engine was free to change. Neither changed a
    number anybody would read, and both produced a two hundred line diff on a
    rebuild, which is how a generated file stops being trusted.
    """
    monkeypatch.setattr(
        build_tableau_extracts.Paths,
        "resolve",
        classmethod(lambda cls: Paths(root=PATHS.root)),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert build_tableau_extracts.main(["--out", str(first)]) == 0
    assert build_tableau_extracts.main(["--out", str(second)]) == 0

    for name in build_tableau_extracts.EXTRACTS:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
