"""Zone geometry, and the component list the aggregates are built from.

The official TLC shapefile is not redistributable and is not committed, so the
geometry tests here used to skip on any machine without it, which meant all of
them skipped in continuous integration and the geometry module ran at 41
percent coverage: the lowest in the repository, and the one module where a
silent fan out had already shipped once.

They now run against a fixture built by `scripts/build_zone_fixture.py` from
the committed zone reference, carrying the same 263 polygon records over the
same 260 zone ids and the same defects, and additionally against the real file
whenever it is present. A fixture that reproduces the defect is worth more than
a skip that reports green.
"""

from __future__ import annotations

import json
from pathlib import Path

import build_zone_fixture
import pytest

from cityflow_publish.aggregates import (
    COMPONENT_SQL,
    _hexbin_sql,
    _next_month,
    component_select,
)
from cityflow_publish.geometry import (
    AIRPORT_ZONE_IDS,
    COORDINATE_PRECISION,
    GEOMETRYLESS_ZONE_IDS,
    UNKNOWN_ZONE_IDS,
    Zone,
    _service_zone,
    prepare_zones,
)

REPO = Path(__file__).resolve().parent.parent
SHAPEFILE_STEM = REPO / "raw" / "zones" / "taxi_zones"


@pytest.fixture(scope="module")
def fixture_stem(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A shapefile carrying the real file's structure and none of its shapes.

    Built from the committed zone reference, so it holds the same 263 polygon
    records over the same 260 zone ids, the same two zones in several pieces,
    and the same five ids with no polygon.
    """
    stem = tmp_path_factory.mktemp("zone-fixture") / "taxi_zones"
    build_zone_fixture.build(REPO / "data" / "reference" / "dim_zone.csv", stem)
    return stem


@pytest.fixture(scope="module", params=["fixture", "real"])
def shapefile_stem(request: pytest.FixtureRequest, fixture_stem: Path) -> Path:
    """Every geometry test below runs twice, once against each source.

    The real shapefile is not redistributable, so on a runner only the fixture
    exists and the real pass skips. That is the whole point: before this, every
    test in this section skipped on a runner and the geometry module ran at 41
    percent coverage, in the one module where a silent fan out had already
    shipped once. A fixture that reproduces the defect is worth more than a
    skip that reports green.
    """
    if request.param == "real":
        if not SHAPEFILE_STEM.with_suffix(".shp").is_file():
            pytest.skip(f"{SHAPEFILE_STEM}.shp is not present; see docs/runbook.md.")
        return SHAPEFILE_STEM
    return fixture_stem


@pytest.fixture(scope="module")
def prepared(
    shapefile_stem: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[list[Zone], dict[str, float]]:
    out = tmp_path_factory.mktemp("zones") / "zones.geojson"
    return prepare_zones(shapefile_stem, out)


@pytest.fixture(scope="module")
def geojson_path(shapefile_stem: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("geojson") / "zones.geojson"
    prepare_zones(shapefile_stem, out)
    return out


# ---------------------------------------------------------------------------
# Dissolving, and the zones with no shape
# ---------------------------------------------------------------------------


def test_multi_part_zones_are_dissolved_to_one_row(
    prepared: tuple[list[Zone], dict[str, float]],
) -> None:
    """The shapefile holds more polygon records than zones. Left alone the
    duplicates fan out any join on zone id and inflate every count downstream,
    and the failure is silent."""
    zones, stats = prepared
    ids = [zone.zone_id for zone in zones]
    assert len(ids) == len(set(ids)), "a zone id was emitted twice"

    assert stats["polygon_records_read"] > stats["zones_with_geometry"]
    dissolved = [zone for zone in zones if zone.part_count > 1]
    assert dissolved, "no zone was dissolved, so the dissolve is untested"
    assert len(dissolved) == stats["zones_dissolved_from_multiple_parts"]
    assert sum(zone.part_count for zone in zones) == stats["polygon_records_read"]


def test_the_geometryless_zones_are_emitted_rather_than_lost(
    prepared: tuple[list[Zone], dict[str, float]],
) -> None:
    """Real places a trip record can name that have no polygon. Counting them
    as unknown would overstate the unresolved share."""
    zones, stats = prepared
    by_id = {zone.zone_id: zone for zone in zones}
    for zone_id, (name, borough) in GEOMETRYLESS_ZONE_IDS.items():
        zone = by_id[zone_id]
        assert zone.has_geometry is False
        assert zone.is_unknown is False
        assert zone.part_count == 0
        assert zone.centroid_lon is None and zone.centroid_lat is None
        assert zone.area_sq_mi is None
        assert zone.zone == name
        assert zone.borough == borough
    assert stats["zones_without_geometry"] == len(GEOMETRYLESS_ZONE_IDS)


def test_the_unknown_codes_are_kept_and_flagged(
    prepared: tuple[list[Zone], dict[str, float]],
) -> None:
    """Zones 264 and 265 carry real trip volume and join to nothing. An inner
    join drops them without saying so, which is the single most common way a
    NYC taxi analysis goes quietly wrong."""
    zones, stats = prepared
    by_id = {zone.zone_id: zone for zone in zones}
    for zone_id in UNKNOWN_ZONE_IDS:
        zone = by_id[zone_id]
        assert zone.is_unknown is True
        assert zone.has_geometry is False
        assert zone.service_zone == "N/A"
        assert zone.borough == "Unknown"
    assert by_id[264].zone == "Unknown"
    assert by_id[265].zone == "Outside of NYC"
    assert stats["zones_total"] == len(zones)
    assert stats["zones_total"] == (
        stats["zones_with_geometry"] + stats["zones_without_geometry"] + len(UNKNOWN_ZONE_IDS)
    )


def test_the_airport_zones_are_flagged_from_the_id_list(
    prepared: tuple[list[Zone], dict[str, float]],
) -> None:
    zones, _ = prepared
    flagged = {zone.zone_id for zone in zones if zone.is_airport}
    assert flagged == set(AIRPORT_ZONE_IDS)


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------


def test_simplification_reduces_vertices(
    prepared: tuple[list[Zone], dict[str, float]],
) -> None:
    _, stats = prepared
    assert stats["kept_vertices"] < stats["raw_vertices"]
    assert stats["vertex_reduction_pct"] > 50.0
    assert stats["simplify_tolerance_feet"] == 150.0
    assert stats["geojson_bytes"] > 0


def test_a_coarser_tolerance_keeps_fewer_vertices(
    prepared: tuple[list[Zone], dict[str, float]], shapefile_stem: Path, tmp_path: Path
) -> None:
    """The tolerance is a distance in feet and it has to mean something. If it
    did not, the published number would be decoration."""
    _, default = prepared
    zones, coarse = prepare_zones(shapefile_stem, tmp_path / "coarse.geojson", tolerance_feet=600.0)
    assert coarse["raw_vertices"] == default["raw_vertices"]
    assert coarse["kept_vertices"] < default["kept_vertices"]
    assert coarse["geojson_bytes"] < default["geojson_bytes"]
    # Coarsening must not lose a zone.
    assert len(zones) == default["zones_total"]


def test_the_geojson_carries_one_feature_per_mappable_zone(geojson_path: Path) -> None:
    collection = json.loads(geojson_path.read_text(encoding="utf-8"))
    assert collection["type"] == "FeatureCollection"
    assert collection["crs"]["properties"]["name"].endswith("CRS84")

    features = collection["features"]
    assert len({f["id"] for f in features}) == len(features)
    for zone_id in (*UNKNOWN_ZONE_IDS, *GEOMETRYLESS_ZONE_IDS):
        assert zone_id not in {f["id"] for f in features}

    sample = features[0]
    assert set(sample["properties"]) == {"zone_id", "zone", "borough", "service_zone"}
    longitude, latitude = _first_coordinate(sample["geometry"]["coordinates"])
    # Reprojected out of EPSG:2263 feet into WGS84 degrees.
    assert -75.0 < longitude < -73.0
    assert 40.0 < latitude < 41.5


def test_the_emitted_coordinates_are_rounded_to_the_published_precision(
    geojson_path: Path,
) -> None:
    """Six places is about eleven centimetres at this latitude, well below the
    simplification tolerance, so the rounding throws away nothing the
    simplification has not already thrown away.

    This caught a real defect. The rounding helper walked lists and tuples but
    not dictionaries, and shapely hands it a dictionary, so it returned its
    input untouched and the precision was never applied. Nothing failed: the
    geometry was correct, the file was simply a third larger than it should
    have been, 314KB against 203KB. Every other test here asks whether the
    geometry is right. This one asks whether the step that shrinks it ran.
    """
    collection = json.loads(geojson_path.read_text(encoding="utf-8"))
    for feature in collection["features"]:
        longitude, latitude = _first_coordinate(feature["geometry"]["coordinates"])
        assert round(longitude, COORDINATE_PRECISION) == longitude
        assert round(latitude, COORDINATE_PRECISION) == latitude


def _first_coordinate(coordinates: object) -> tuple[float, float]:
    cursor = coordinates
    while isinstance(cursor, list) and not isinstance(cursor[0], int | float):
        cursor = cursor[0]
    assert isinstance(cursor, list)
    return float(cursor[0]), float(cursor[1])


def test_the_service_zone_is_derived_from_two_facts_in_the_shapefile() -> None:
    """The TLC publishes a lookup carrying this column. It is derived here
    instead, and docs/data.md says so rather than presenting it as sourced."""
    assert _service_zone(132, "Queens") == "Airports"
    assert _service_zone(1, "EWR") == "EWR"
    assert _service_zone(161, "Manhattan") == "Yellow Zone"
    assert _service_zone(7, "Queens") == "Boro Zone"
    assert _service_zone(264, "Unknown") == "N/A"


# ---------------------------------------------------------------------------
# The component list
# ---------------------------------------------------------------------------


def test_an_unknown_component_is_refused_rather_than_selected_as_null() -> None:
    """A metric whose component nobody publishes returns nulls in the interface
    if this does not raise, and nulls in an interface look like zero."""
    with pytest.raises(KeyError, match="invented_component") as caught:
        component_select(["trips", "invented_component"])
    assert "COMPONENT_SQL" in str(caught.value)


def test_the_component_select_names_every_column_it_emits() -> None:
    rendered = component_select(["trips", "tip_obs_tip_sum"])
    assert "count(*) as trips" in rendered
    assert "sum(tip_amount) filter (where tip_is_observable) as tip_obs_tip_sum" in rendered
    assert rendered.count(" as ") == 2


def test_no_component_is_a_rate() -> None:
    """The aggregates publish numerators and denominators. A published rate
    cannot be re-aggregated: averaging two zones' tip rates is wrong for both
    together, and somebody always does it."""
    for name, sql in COMPONENT_SQL.items():
        assert "/" not in sql, f"{name} is a rate, not an additive component"
        assert sql.startswith(("count(", "sum("))


def test_the_hexbin_lattice_is_scaled_on_each_axis() -> None:
    """Miles and dollars are not comparable, so a geometrically regular hexagon
    in data space would be a very tall one on screen."""
    q, r = _hexbin_sql("trip_distance", "fare_amount", 0.35, 1.75)
    assert "trip_distance / 0.35" in q
    assert "fare_amount / 1.75" in r
    assert "trip_distance / 0.35" in r


def test_the_next_month_rolls_over_the_year() -> None:
    import datetime as dt

    assert _next_month(dt.date(2024, 11, 1)) == dt.date(2024, 12, 1)
    assert _next_month(dt.date(2024, 12, 1)) == dt.date(2025, 1, 1)
