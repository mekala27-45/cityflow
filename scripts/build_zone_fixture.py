"""Write a stand in for the TLC taxi zone shapefile, from the committed reference.

The official shapefile is not redistributable and is not committed, so every
test that touches `prepare_zones` was guarded by a skip and the geometry module
ran at 41 percent coverage in continuous integration: the lowest in the
repository, and the module where a silent fan out had already shipped once.

This builds a shapefile carrying the same structure and the same defects as the
real one, the same way the trip generator reproduces the published parquet's
schema and defects. It is not a copy of the geometry and it is not a map of New
York. Every polygon here is a circle.

What it reproduces, read out of `data/reference/dim_zone.csv` rather than
restated here so the two cannot drift:

  * one record per polygon, not one per zone, so the file holds 263 records
    over 260 zone ids and any per record loop fans out;
  * the two zones that appear more than once, Corona and the harbour islands,
    with their parts separated so the dissolve has real work to do;
  * the five zone ids with no polygon at all, by leaving them out;
  * rings dense enough that simplification has something to remove, which is
    what makes the tolerance a measurable quantity rather than a decoration.

What it does not reproduce is the shape of any zone, the borders between them,
or anything else a reader could mistake for cartography.

    python scripts/build_zone_fixture.py /tmp/e2e/raw/zones/taxi_zones
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from pathlib import Path

import shapefile
from pyproj import Transformer

# The projection the TLC publishes in: New York Long Island, in US survey feet.
# Distances here are feet, which is what makes an area in square miles and a
# simplification tolerance in feet both mean something.
SOURCE_CRS = "EPSG:2263"
WGS84 = "EPSG:4326"

SQUARE_FEET_PER_SQUARE_MILE = 27_878_400.0

# Vertices per ring. A circle does not simplify away to nothing, so this is the
# number that decides how much the 150 foot tolerance can remove. At this count
# a typical zone loses well over ninety percent of its vertices, which is the
# order of the reduction on the real file.
RING_VERTICES = 240

# Radial noise, as a fraction of the radius, so the ring is not a perfect
# circle that a simplifier could reduce analytically. Kept well under the
# simplification tolerance at every realistic radius.
RING_NOISE = 0.02

# How far apart the parts of a multi part zone are placed, in radii. Far enough
# that the union is genuinely several polygons rather than one merged blob, so
# the dissolve is doing the thing the test says it is doing.
PART_SPACING = 3.0

SEED = 20240812


def _ring(x: float, y: float, radius: float, rng: random.Random) -> list[list[float]]:
    """A closed clockwise ring of RING_VERTICES points around a centre.

    Clockwise because the shapefile specification says an outer ring is
    clockwise, and pyshp decides what is a hole by orientation. A counter
    clockwise ring here would be read back as a hole in a polygon with no
    outer ring, which shapely then reports as empty.
    """
    points: list[list[float]] = []
    for index in range(RING_VERTICES):
        angle = -2.0 * math.pi * index / RING_VERTICES
        jitter = 1.0 + rng.uniform(-RING_NOISE, RING_NOISE)
        points.append(
            [
                x + radius * jitter * math.cos(angle),
                y + radius * jitter * math.sin(angle),
            ]
        )
    points.append(list(points[0]))
    return points


def build(reference: Path, stem: Path) -> dict[str, int]:
    with reference.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    mappable = [row for row in rows if row["has_geometry"] == "1"]
    if not mappable:
        raise ValueError(f"{reference} carries no zone with geometry.")

    to_state_plane = Transformer.from_crs(WGS84, SOURCE_CRS, always_xy=True)
    rng = random.Random(SEED)
    stem.parent.mkdir(parents=True, exist_ok=True)

    records = 0
    with shapefile.Writer(str(stem), shapeType=shapefile.POLYGON) as writer:
        # The three fields prepare_zones reads, under the names the real file
        # uses. LocationID is the one that repeats.
        writer.field("LocationID", "N", size=6, decimal=0)
        writer.field("zone", "C", size=64)
        writer.field("borough", "C", size=32)

        for row in sorted(mappable, key=lambda r: int(r["zone_id"])):
            zone_id = int(row["zone_id"])
            parts = max(1, int(row["part_count"]))
            x, y = to_state_plane.transform(float(row["centroid_lon"]), float(row["centroid_lat"]))

            # Each part carries an equal share of the area, so the area
            # weighted centroid of a symmetric row of parts lands back on the
            # centroid the reference records.
            area_per_part = (float(row["area_sq_mi"]) * SQUARE_FEET_PER_SQUARE_MILE) / parts
            radius = math.sqrt(area_per_part / math.pi)
            offset = PART_SPACING * radius

            for part in range(parts):
                shift = (part - (parts - 1) / 2.0) * offset
                writer.poly([_ring(x + shift, y, radius, rng)])
                writer.record(zone_id, row["zone"], row["borough"])
                records += 1

    # A .prj is not read by prepare_zones, which is told the source projection
    # by configuration, but a shapefile without one is a trap for anything else
    # that opens it.
    stem.with_suffix(".prj").write_text(_esri_wkt(), encoding="utf-8")

    return {
        "polygon_records": records,
        "zone_ids": len({int(row["zone_id"]) for row in mappable}),
        "multi_part_zones": sum(1 for row in mappable if int(row["part_count"]) > 1),
        "omitted_without_geometry": len(rows) - len(mappable),
    }


def _esri_wkt() -> str:
    """The EPSG:2263 definition, in the flavour ESRI tools expect in a .prj."""
    from pyproj import CRS

    return str(CRS.from_epsg(2263).to_wkt(version="WKT1_ESRI"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stem", help="Output path without an extension.")
    parser.add_argument(
        "--reference",
        default="data/reference/dim_zone.csv",
        help="The committed zone reference the fixture is built from.",
    )
    args = parser.parse_args(argv)

    reference = Path(args.reference)
    if not reference.is_file():
        print(
            f"Missing {reference}. The fixture is built from the committed zone "
            "reference so the two cannot drift; run 'make zones' against the real "
            "shapefile to produce it.",
            file=sys.stderr,
        )
        return 2

    stats = build(reference, Path(args.stem))
    print(
        f"{args.stem}.shp: {stats['polygon_records']} polygon records over "
        f"{stats['zone_ids']} zone ids, {stats['multi_part_zones']} of them in "
        f"more than one piece, {stats['omitted_without_geometry']} zone ids left "
        "out because the real file has no polygon for them either."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
