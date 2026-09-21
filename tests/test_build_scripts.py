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
    monkeypatch.chdir(PATHS.root)
    monkeypatch.setattr(
        build_tableau_extracts.Paths,
        "resolve",
        classmethod(lambda cls: Paths(root=PATHS.root)),
    )
    assert build_tableau_extracts.main([]) == 0

    for name in build_tableau_extracts.EXTRACTS:
        target = PATHS.root / "tableau" / name
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


def test_the_scripts_are_runnable_as_programs() -> None:
    """The __main__ guards, which nothing else reaches."""
    import subprocess

    for script in ("build_demo_gif.py", "build_tableau_extracts.py"):
        result = subprocess.run(
            [sys.executable, str(PATHS.root / "scripts" / script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, script
        assert "usage" in result.stdout.lower()
