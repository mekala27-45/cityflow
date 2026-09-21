"""The build manifest: every published figure, with the query that produced it.

Day 1's lesson, written into the architecture rather than the process.
Re-verifying the published figures against the run records caught two wrong
numbers that were already committed, and writing the gate that prevents it
caught a third. So on this build the registry comes first and the documents are
written against it.

The manifest is a flat dictionary of claim id to value, built by running the
queries below against the warehouse and the shipped layer. `README.md` and
`RESULTS.md` reference claims by id, and `scripts/check_published_numbers.py`
re-derives every one of them and fails the build when the rendered text and the
manifest disagree.

The important property: **nothing here is typed by hand.** A figure that cannot
be produced by a query does not go in a document.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

import duckdb

from cityflow_core.config import CityflowConfig
from cityflow_core.paths import Paths
from cityflow_ingest.synthetic import PLANTED_CHANGEPOINT, PLANTED_SHIFT

# Claims are grouped only for readability in the JSON. The gate reads the flat
# id space, so a claim can move between groups without breaking a document.
CLAIM_QUERIES: dict[str, str] = {
    # Scale -------------------------------------------------------------------
    "source_rows": "select sum(source_rows) from source_audit",
    "clean_rows": "select sum(clean_rows) from source_audit",
    "quarantined_rows": "select sum(quarantined_rows) from source_audit",
    "quarantine_share_pct": (
        "select 100.0 * sum(quarantined_rows) / nullif(sum(source_rows), 0) from source_audit"
    ),
    "months": "select count(distinct source_period) from source_audit",
    "services": "select count(distinct service) from source_audit",
    "source_files": "select count(*) from source_audit",
    "vintages": "select count(distinct vintage) from source_audit",
    "ingest_seconds": "select sum(seconds) from source_audit",
    "first_period": "select min(source_period) from source_audit",
    "last_period": "select max(source_period) from source_audit",
    # The fact table ----------------------------------------------------------
    "fct_trip_rows": "select count(*) from fct_trip",
    "distinct_zones": "select count(*) from dim_zone",
    "zones_with_geometry": "select count(*) from dim_zone where has_geometry",
    "zones_dissolved": "select count(*) from dim_zone where part_count > 1",
    "zones_without_geometry": (
        "select count(*) from dim_zone where not has_geometry and not is_unknown"
    ),
    "unknown_zone_trips": ("select count(*) from fct_trip where not has_known_geography"),
    "unknown_zone_share_pct": (
        "select 100.0 * count(*) filter (where not has_known_geography) "
        "/ nullif(count(*), 0) from fct_trip"
    ),
    # The tip finding ---------------------------------------------------------
    "tip_rate_naive_pct": (
        "select 100.0 * avg(tip_amount / nullif(fare_amount, 0)) "
        "from fct_trip where service in ('yellow', 'green')"
    ),
    "tip_rate_correct_pct": (
        "select 100.0 * avg(tip_pct) from fct_trip where service in ('yellow', 'green')"
    ),
    "tip_rate_understatement_pct": (
        "select 100.0 * (avg(tip_pct) - avg(tip_amount / nullif(fare_amount, 0))) "
        "/ nullif(avg(tip_amount / nullif(fare_amount, 0)), 0) "
        "from fct_trip where service in ('yellow', 'green')"
    ),
    "cash_trips": "select count(*) from fct_trip where payment_type = 'cash'",
    "cash_share_of_metered_pct": (
        "select 100.0 * count(*) filter (where payment_type = 'cash') "
        "/ nullif(count(*) filter (where service in ('yellow', 'green')), 0) "
        "from fct_trip"
    ),
    "tip_pct_null_rows": "select count(*) from fct_trip where tip_pct is null",
    # Schema change -----------------------------------------------------------
    "airport_fee_null_months": (
        "select count(*) from mart_null_rates "
        "where column_name = 'airport_fee' and null_share = 1.0"
    ),
    "airport_fee_first_populated": (
        "select min(source_period) from mart_null_rates "
        "where column_name = 'airport_fee' and null_share < 1.0"
    ),
    "airport_fee_last_all_null": (
        "select max(source_period) from mart_null_rates "
        "where column_name = 'airport_fee' and null_share = 1.0"
    ),
    "congestion_surcharge_null_months": (
        "select count(*) from mart_null_rates "
        "where column_name = 'congestion_surcharge' and null_share = 1.0"
    ),
    "cbd_fee_null_months": (
        "select count(*) from mart_null_rates "
        "where column_name = 'cbd_congestion_fee' and null_share = 1.0"
    ),
    "shapefile_polygon_records": "select sum(part_count) from dim_zone",
    "zones_multi_part_polygons": ("select sum(part_count) from dim_zone where part_count > 1"),
    # Statistics --------------------------------------------------------------
    "zone_comparisons": "select count(*) from zone_comparison_view",
    "zone_comparisons_naive_significant": (
        "select count(*) from zone_comparison_view where p_value < 0.05"
    ),
    "zone_comparisons_after_bh": ("select count(*) from zone_comparison_view where is_significant"),
    "zone_comparisons_expected_by_chance": ("select 0.05 * count(*) from zone_comparison_view"),
    # Concentration -----------------------------------------------------------
    "zones_for_half_of_trips": """
        select min(rank_in_city) from (
            select
                row_number() over (order by trips desc) as rank_in_city,
                sum(trips) over (order by trips desc
                    rows between unbounded preceding and current row) as running,
                sum(trips) over () as total
            from (
                select pu_zone_id, count(*) as trips
                from fct_trip where has_known_geography group by 1
            )
        ) where running >= total / 2.0
    """,
    "busiest_zone": """
        select z.zone
        from fct_trip f join dim_zone z on z.zone_id = f.pu_zone_id
        where f.has_known_geography
        group by z.zone order by count(*) desc limit 1
    """,
}


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = connection.execute(sql).fetchone()
    if row is None:
        return None
    value = row[0]
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _git(args: list[str], root: Path) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def build_manifest(
    connection: duckdb.DuckDBPyConnection, config: CityflowConfig, paths: Paths
) -> dict[str, Any]:
    """Every published figure, measured now."""
    comparisons = paths.shipped / "zone_comparisons.parquet"
    if comparisons.is_file():
        connection.execute(
            f"create or replace temporary view zone_comparison_view as "
            f"select * from read_parquet('{comparisons}')"
        )
    else:
        connection.execute(
            "create or replace temporary view zone_comparison_view as "
            "select null::varchar as service, null::double as p_value, "
            "false as is_significant where false"
        )

    claims: dict[str, Any] = {}
    for claim_id, sql in CLAIM_QUERIES.items():
        claims[claim_id] = _scalar(connection, sql)

    shipped: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(paths.shipped.glob("*.parquet")):
        size = path.stat().st_size
        total_bytes += size
        rows = _scalar(connection, f"select count(*) from read_parquet('{path}')")
        shipped.append({"file": path.name, "rows": rows, "bytes": size})
    for path in sorted(paths.shipped.glob("*.json")):
        total_bytes += path.stat().st_size
        shipped.append({"file": path.name, "rows": None, "bytes": path.stat().st_size})
    geojson = paths.shipped / "zones.geojson"
    if geojson.is_file() and not any(s["file"] == geojson.name for s in shipped):
        total_bytes += geojson.stat().st_size
        shipped.append({"file": geojson.name, "rows": None, "bytes": geojson.stat().st_size})

    # Flatten the per file and per rule detail into the claim namespace. The
    # gate's lookup walks dictionaries, and a document that wants to state one
    # rule's count should not have to reach into a list by index, which would
    # break the moment a rule is added.
    for entry in shipped:
        stem = str(entry["file"]).replace(".", "_")
        if entry["rows"] is not None:
            claims[f"rows.{stem}"] = entry["rows"]
        claims[f"mb.{stem}"] = round(int(entry["bytes"]) / 1_000_000, 2)

    claims["shipped_files"] = len(shipped)
    claims["shipped_bytes"] = total_bytes
    claims["shipped_mb"] = round(total_bytes / 1_000_000, 2)
    claims["largest_shipped_mb"] = round(
        max((s["bytes"] for s in shipped), default=0) / 1_000_000, 2
    )
    claims["summarization_ratio"] = (
        round(float(claims["fct_trip_rows"]) / max(total_bytes, 1) * 1_000_000, 1)
        if claims.get("fct_trip_rows")
        else 0.0
    )

    # Configuration that documents quote. Read from the config object the
    # pipeline actually used, so docs/data.md cannot state a threshold the
    # build did not apply.
    thresholds = config.quarantine
    for field_name in type(thresholds).model_fields:
        claims[f"threshold.{field_name}"] = getattr(thresholds, field_name)
    for name, rate in config.synthetic.defect_rates.items():
        claims[f"defect_rate.{name}"] = rate
        claims[f"defect_rate_pct.{name}"] = round(rate * 100, 4)
    for service, shift in PLANTED_SHIFT.items():
        claims[f"planted_shift_pct.{service}"] = round((shift - 1.0) * 100, 2)
    claims["planted_changepoint"] = PLANTED_CHANGEPOINT.isoformat()
    claims["synthetic_seed"] = config.synthetic.seed
    claims["file_budget_mb"] = config.shipped_file_budget_mb
    claims["total_budget_mb"] = config.shipped_total_budget_mb
    claims["memory_limit"] = config.duckdb_memory_limit

    # The denominator is every source row in the window, not the rows of the
    # service months in which this rule happened to fire. Those differ: the
    # passenger count rule cannot fire on for hire files, which do not record
    # it, so a per rule denominator would report its share of a different
    # population than the row above it and the column would not add up to the
    # total. Shares here are comparable to each other and they sum.
    quarantine = connection.execute(
        """
        select
            rule,
            sum(rows) as rows,
            100.0 * sum(rows) / nullif(
                (select sum(source_rows) from source_audit), 0
            ) as share_pct
        from mart_quarantine
        group by rule
        order by rows desc
        """
    ).fetchall()

    for row in quarantine:
        rule = str(row[0])
        claims[f"quarantine_rows.{rule}"] = int(row[1])
        claims[f"quarantine_share_pct.{rule}"] = round(float(row[2]), 6)
    claims["quarantine_rules_fired"] = len(quarantine)

    # Counted rather than asserted: a document that says "150 tests" while the
    # project has 141 is exactly the kind of figure this gate exists to catch.
    manifest_path = paths.transform / "target" / "manifest.json"
    if manifest_path.is_file():
        dbt_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        nodes = dbt_manifest.get("nodes", {})
        claims["dbt_tests"] = sum(1 for n in nodes.values() if n.get("resource_type") == "test")
        claims["dbt_models"] = sum(1 for n in nodes.values() if n.get("resource_type") == "model")
        claims["dbt_exposures"] = len(dbt_manifest.get("exposures", {}))

    catalog_path = paths.shipped / "metric_catalog.json"
    if catalog_path.is_file():
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        claims["metric_count"] = len(catalog)
        claims["metrics_with_interval"] = sum(1 for m in catalog if m.get("interval"))

    changepoint_path = paths.shipped / "changepoints.json"
    if changepoint_path.is_file():
        detected = json.loads(changepoint_path.read_text(encoding="utf-8"))
        claims["changepoints_found"] = detected.get("found")
        claims["changepoints_below_effect_floor"] = detected.get("below_effect_floor")
        claims["changepoints_annotated"] = sum(
            len(v) for v in detected.get("detected", {}).values()
        )
        claims["changepoint_min_effect_pct"] = round(float(detected.get("min_effect", 0)) * 100, 2)
        # The generator plants one level shift. Whether the detector finds it,
        # and how far off it lands, is the only check on this pipeline that has
        # a known right answer, so it is published rather than assumed.
        planted = dt.date.fromisoformat(PLANTED_CHANGEPOINT.isoformat())
        nearest: int | None = None
        for entry in detected.get("detected", {}).get("fhvhv", []):
            distance = abs((dt.date.fromisoformat(entry["date"]) - planted).days)
            if nearest is None or distance < nearest:
                nearest = distance
                claims["planted_changepoint_detected_change_pct"] = round(
                    float(entry["relative_change"]) * 100, 2
                )
        claims["planted_changepoint_miss_days"] = nearest

    geojson_path = paths.shipped / "zones.geojson"
    if geojson_path.is_file():
        features = json.loads(geojson_path.read_text(encoding="utf-8"))["features"]
        claims["geojson_features"] = len(features)

    backends = connection.execute("select distinct backend from source_audit order by 1").fetchall()

    bench_path = paths.shipped / "bench.json"
    bench: list[dict[str, Any]] = []
    if bench_path.is_file():
        for entry in json.loads(bench_path.read_text(encoding="utf-8")):
            bench.append(
                {
                    **entry,
                    "file_mb": round(int(entry["file_bytes"]) / 1_000_000, 2),
                    "pruned_pct": round(
                        100.0 * int(entry["row_groups_skipped"]) / max(int(entry["row_groups"]), 1),
                        0,
                    ),
                }
            )
        claims["bench_queries"] = len(bench)
        claims["bench_p95_max_ms"] = round(max(b["p95_ms"] for b in bench), 2)
        claims["bench_p95_median_ms"] = round(
            sorted(b["p95_ms"] for b in bench)[len(bench) // 2], 2
        )
        pruning = [b for b in bench if b["pruned_pct"] > 0]
        claims["bench_queries_pruned"] = len(pruning)
        claims["bench_best_pruning_pct"] = (
            round(max(b["pruned_pct"] for b in pruning), 0) if pruning else 0
        )
        measured = [b for b in bench if b.get("browser_bytes")]
        if measured:
            claims["bench_browser_queries"] = len(measured)
            claims["bench_browser_bytes"] = sum(int(b["browser_bytes"]) for b in measured)
            claims["bench_browser_requests"] = sum(
                int(b["browser_requests"] or 0) for b in measured
            )
            claims["bench_browser_file_bytes"] = sum(int(b["file_bytes"]) for b in measured)
            claims["bench_browser_fetched_pct"] = round(
                100.0 * claims["bench_browser_bytes"] / max(claims["bench_browser_file_bytes"], 1),
                1,
            )
            tightest = min(measured, key=lambda b: int(b["browser_bytes"]) / int(b["file_bytes"]))
            claims["bench_tightest_query"] = tightest["query"]
            claims["bench_tightest_kb"] = round(int(tightest["browser_bytes"]) / 1024, 1)
            claims["bench_tightest_file_mb"] = round(int(tightest["file_bytes"]) / 1_000_000, 2)

    root = paths.root
    claims["backend"] = ", ".join(sorted({str(b[0]) for b in backends}))

    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "commit": _git(["rev-parse", "--short", "HEAD"], root),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], root),
        "backend": [b[0] for b in backends],
        "window": {
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "detail_month": config.detail_month.isoformat(),
        },
        "claims": claims,
        "quarantine_by_rule": [
            {"rule": r[0], "rows": int(r[1]), "share_pct": round(float(r[2]), 6)}
            for r in quarantine
        ],
        "shipped": shipped,
        "bench": bench,
    }


def write_manifest(
    connection: duckdb.DuckDBPyConnection, config: CityflowConfig, paths: Paths
) -> dict[str, Any]:
    data = build_manifest(connection, config, paths)
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
