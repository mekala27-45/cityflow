"""Turn the official TLC taxi zone shapefile into something a browser can hold.

Three things happen here and each of them is a decision worth stating.

1. The shapefile is in NAD83 / New York Long Island (EPSG:2263), in US survey
   feet. MapLibre wants WGS84 degrees. Reprojecting after simplification would
   make the tolerance meaningless, so the simplification runs in feet, where a
   tolerance is a distance a person can reason about, and the reprojection
   happens afterwards.

2. The raw polygons are about 3.9MB of GeoJSON. At the zoom a choropleth of 260
   zones is actually viewed, coastline detail below roughly 150 feet is invisible
   and costs a third of the payload. The tolerance is published, not hidden.

3. Zones 264 and 265 have no geometry at all. They are the TLC's codes for
   "unknown" and they are the single most common way a NYC taxi analysis goes
   quietly wrong: they carry real trip volume, they join to nothing, and an
   inner join drops them without saying so. They are emitted here as rows with a
   null geometry and an explicit is_unknown flag, so every downstream consumer
   has to decide what to do with them rather than never seeing them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapefile
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

# The projection the TLC publishes in, and the one the web expects.
SOURCE_CRS = "EPSG:2263"
TARGET_CRS = "EPSG:4326"

# Feet. See the module docstring for why this number and not a smaller one.
SIMPLIFY_TOLERANCE_FEET = 150.0

# Coordinate decimal places kept in the emitted GeoJSON. Six places is about
# 11cm at this latitude, well below the simplification tolerance, so rounding
# here throws away nothing the simplification has not already thrown away, and
# it removes about a fifth of the bytes.
COORDINATE_PRECISION = 6

# The TLC's codes for a trip whose zone could not be determined.
UNKNOWN_ZONE_IDS = (264, 265)

# The three airport zones, which the official lookup separates out because they
# carry flat fares and because airport share is a metric people ask for.
AIRPORT_ZONE_IDS = {1: "EWR", 132: "Airports", 138: "Airports"}


@dataclass(frozen=True, slots=True)
class Zone:
    """One row of dim_zone, before it reaches the warehouse."""

    zone_id: int
    zone: str
    borough: str
    service_zone: str
    is_unknown: bool
    is_airport: bool
    centroid_lon: float | None
    centroid_lat: float | None
    area_sq_mi: float | None


def _service_zone(zone_id: int, borough: str) -> str:
    """Derive the service zone from the borough and the airport zone ids.

    The TLC publishes an authoritative lookup file carrying this column. This
    repository derives it instead, from the two facts that are actually in the
    shapefile, and says so in docs/data.md rather than presenting a derived
    column as a sourced one. The derivation is: the three airport zones by id,
    Manhattan is the yellow zone, every other borough is a boro zone, and the
    unknown codes get N/A.
    """
    if zone_id in UNKNOWN_ZONE_IDS:
        return "N/A"
    if zone_id in AIRPORT_ZONE_IDS:
        return AIRPORT_ZONE_IDS[zone_id]
    if borough == "Manhattan":
        return "Yellow Zone"
    if borough == "EWR":
        return "EWR"
    return "Boro Zone"


def _round_coordinates(obj: Any, places: int = COORDINATE_PRECISION) -> Any:
    """Recursively round every float in a GeoJSON coordinate structure."""
    if isinstance(obj, float):
        return round(obj, places)
    if isinstance(obj, int):
        return obj
    if isinstance(obj, list | tuple):
        return [_round_coordinates(item, places) for item in obj]
    return obj


def _to_wgs84(geometry: BaseGeometry) -> BaseGeometry:
    transformer = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)

    def project(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        # shapely passes a third ordinate for 3D geometries. The taxi zones are
        # flat, so it is accepted and dropped rather than refused.
        del z
        return transformer.transform(x, y)

    return shapely_transform(project, geometry)


def prepare_zones(
    shapefile_stem: Path,
    geojson_out: Path,
    *,
    tolerance_feet: float = SIMPLIFY_TOLERANCE_FEET,
) -> tuple[list[Zone], dict[str, float]]:
    """Read the shapefile, simplify, reproject, write GeoJSON, return dim_zone.

    Args:
        shapefile_stem: path without the extension, the way pyshp wants it.
        geojson_out: where to write the simplified WGS84 FeatureCollection.
        tolerance_feet: simplification tolerance, applied in the source
            projection where a distance in feet means something.

    Returns:
        The dim_zone rows, and a small dict of measured statistics for the
        build manifest.
    """
    reader = shapefile.Reader(str(shapefile_stem), encoding="latin-1")
    field_names = [f[0] for f in reader.fields[1:]]

    features: list[dict[str, Any]] = []
    zones: list[Zone] = []
    raw_vertices = 0
    kept_vertices = 0

    for shape_record in reader.iterShapeRecords():
        record = dict(zip(field_names, list(shape_record.record), strict=True))
        zone_id = int(record["LocationID"])
        borough = str(record["borough"]).strip()
        name = str(record["zone"]).strip()

        projected = shape(shape_record.shape.__geo_interface__)
        raw_vertices += _count_vertices(projected)

        simplified = projected.simplify(tolerance_feet, preserve_topology=True)
        if simplified.is_empty:
            # Simplification can empty a sliver. Keep the original rather than
            # losing the zone, and let the size budget complain if it matters.
            simplified = projected
        kept_vertices += _count_vertices(simplified)

        # Area in the source projection is square feet, which is exact here.
        # 27,878,400 square feet to the square mile.
        area_sq_mi = projected.area / 27_878_400.0

        wgs84 = _to_wgs84(simplified)
        # The centroid is taken on the full polygon, not the simplified one, so
        # the flow map arcs land where the zone actually is rather than where
        # the 150 foot tolerance left it.
        centroid = _to_wgs84(projected.centroid)
        if not isinstance(centroid, Point):  # pragma: no cover
            raise TypeError(f"Zone {zone_id} centroid is not a point")

        zones.append(
            Zone(
                zone_id=zone_id,
                zone=name,
                borough=borough,
                service_zone=_service_zone(zone_id, borough),
                is_unknown=False,
                is_airport=zone_id in AIRPORT_ZONE_IDS,
                centroid_lon=round(centroid.x, COORDINATE_PRECISION),
                centroid_lat=round(centroid.y, COORDINATE_PRECISION),
                area_sq_mi=round(area_sq_mi, 4),
            )
        )

        geometry = _round_coordinates(mapping(wgs84))
        features.append(
            {
                "type": "Feature",
                "id": zone_id,
                "properties": {
                    "zone_id": zone_id,
                    "zone": name,
                    "borough": borough,
                    "service_zone": _service_zone(zone_id, borough),
                },
                "geometry": geometry,
            }
        )

    # The two unknown codes, which have no polygon and must never be silently
    # dropped by a join.
    for zone_id in UNKNOWN_ZONE_IDS:
        zones.append(
            Zone(
                zone_id=zone_id,
                zone="Unknown" if zone_id == 264 else "Outside of NYC",
                borough="Unknown",
                service_zone="N/A",
                is_unknown=True,
                is_airport=False,
                centroid_lon=None,
                centroid_lat=None,
                area_sq_mi=None,
            )
        )

    collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    geojson_out.parent.mkdir(parents=True, exist_ok=True)
    geojson_out.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")

    stats = {
        "zones_with_geometry": float(len(features)),
        "zones_total": float(len(zones)),
        "raw_vertices": float(raw_vertices),
        "kept_vertices": float(kept_vertices),
        "vertex_reduction_pct": round(100.0 * (1.0 - kept_vertices / raw_vertices), 2),
        "geojson_bytes": float(geojson_out.stat().st_size),
        "simplify_tolerance_feet": tolerance_feet,
    }
    return zones, stats


def _count_vertices(geometry: BaseGeometry) -> int:
    """Total coordinate count, across every ring of every part."""
    geo = mapping(geometry)
    return _count_in_coordinates(geo.get("coordinates", []))


def _count_in_coordinates(coordinates: Any) -> int:
    if not isinstance(coordinates, list | tuple):
        return 0
    if coordinates and isinstance(coordinates[0], int | float):
        return 1
    return sum(_count_in_coordinates(item) for item in coordinates)
