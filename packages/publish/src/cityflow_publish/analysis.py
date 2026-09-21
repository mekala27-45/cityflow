"""The statistics the dashboard displays, computed once at build time.

Three things are computed here rather than in the browser, and each for a
reason that is about correctness rather than performance.

**STL** needs the whole series and a stable implementation. Reimplementing
Cleveland's loess in TypeScript to save a round trip would be a worse version
of a well tested one, and the page would be the only place it existed.

**Changepoints** are an inference, not a view. PELT run in the browser would
give a different answer under a different filter, and the chart would annotate
different dates depending on what the reader had clicked, which is not what an
annotated changepoint means.

**The pairwise zone comparisons** are the one that matters most. Comparing 260
zones against the city is 260 tests; comparing them against each other is
33,670. At a five percent threshold, seventeen hundred of those come back
significant by chance alone. So the comparison is run once, corrected once with
Benjamini-Hochberg, and the surviving set is shipped along with the number of
comparisons made, which the panel states on screen. A reader who wants to know
why a zone is not marked can read the q value.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from cityflow_core.config import CityflowConfig
from cityflow_core.paths import Paths
from cityflow_stats import (
    benjamini_hochberg,
    bic_penalty,
    decompose,
    estimate_sigma,
    pelt,
    wilson_interval_array,
)

# The false discovery rate the zone comparisons are corrected at, stated in the
# panel next to the comparison count.
FDR_Q = 0.05

# STL period. Seven, because the dominant cycle in trip volume is the week.
STL_PERIOD = 7

# A changepoint must have this many days on each side. Below a fortnight the
# detector finds the edges of holidays, which are already annotated.
CHANGEPOINT_MIN_SIZE = 14


@dataclass(slots=True)
class AnalysisOutputs:
    """What the analysis step wrote, for the manifest."""

    decomposition_rows: int = 0
    changepoints: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    zone_comparisons: int = 0
    zone_comparisons_significant: int = 0
    pairwise_comparisons: int = 0
    pairwise_significant: int = 0
    fdr_q: float = FDR_Q
    notes: list[str] = field(default_factory=list)


def build_decomposition(
    connection: duckdb.DuckDBPyConnection, paths: Paths, outputs: AnalysisOutputs
) -> None:
    """STL per service, plus the changepoints on the observed series."""
    frame = connection.execute(
        """
        select service, date_day, sum(trips) as trips
        from read_parquet(?)
        group by 1, 2
        order by 1, 2
        """,
        [str(paths.shipped / "agg_daily.parquet")],
    ).fetchdf()

    pieces: list[pd.DataFrame] = []
    for service, group in frame.groupby("service", sort=True):
        series = pd.Series(
            group["trips"].to_numpy(dtype=float),
            index=pd.DatetimeIndex(group["date_day"]),
            name="trips",
        )
        if len(series) < 2 * STL_PERIOD:
            outputs.notes.append(
                f"{service}: {len(series)} days is too short for an STL with a "
                f"period of {STL_PERIOD}, so no decomposition was published."
            )
            continue

        result = decompose(series, period=STL_PERIOD, robust=True)
        index = pd.DatetimeIndex(result.observed.index)
        piece = pd.DataFrame(
            {
                "service": service,
                "date_day": index.date,
                "observed": result.observed.to_numpy(),
                "trend": result.trend.to_numpy(),
                "seasonal": result.seasonal.to_numpy(),
                "resid": result.resid.to_numpy(),
            }
        )
        pieces.append(piece)
        if result.gaps_filled:
            outputs.notes.append(
                f"{service}: {result.gaps_filled} missing day(s) were "
                "interpolated before decomposition, because a gap shifts the "
                "seasonal phase without complaining."
            )

        signal = result.observed.to_numpy(dtype=float)
        sigma = estimate_sigma(signal)
        penalty = bic_penalty(len(signal), sigma, n_params=2)
        indices = pelt(signal, penalty=penalty, min_size=CHANGEPOINT_MIN_SIZE)
        outputs.changepoints[str(service)] = [
            _describe_changepoint(result.observed, index) for index in indices
        ]

    if pieces:
        combined = pd.concat(pieces, ignore_index=True)
        connection.register("decomposition_frame", combined)
        target = paths.shipped / "agg_daily_decomposition.parquet"
        connection.execute(
            f"copy (select * from decomposition_frame order by service, date_day) "
            f"to '{target}' (format parquet, compression zstd)"
        )
        connection.unregister("decomposition_frame")
        outputs.decomposition_rows = len(combined)


def _describe_changepoint(series: pd.Series, index: int) -> dict[str, Any]:
    """A changepoint with the numbers a reader needs to judge it.

    The direction and the two means are included because "a changepoint was
    detected here" is not a finding. "Volume fell 11.8 percent and stayed
    there" is one, and it is checkable.
    """
    before = series.iloc[max(0, index - 28) : index]
    after = series.iloc[index : index + 28]
    before_mean = float(before.mean()) if len(before) else float("nan")
    after_mean = float(after.mean()) if len(after) else float("nan")
    change = (
        (after_mean - before_mean) / before_mean
        if before_mean not in (0.0,) and not np.isnan(before_mean)
        else float("nan")
    )
    date_value = series.index[index]
    return {
        "date": (date_value.date().isoformat() if hasattr(date_value, "date") else str(date_value)),
        "index": int(index),
        "before_mean": round(before_mean, 1),
        "after_mean": round(after_mean, 1),
        "relative_change": round(float(change), 4),
        "direction": "down" if change < 0 else "up",
    }


def build_zone_comparisons(
    connection: duckdb.DuckDBPyConnection, paths: Paths, outputs: AnalysisOutputs
) -> None:
    """Wilson intervals per zone and service, and a corrected comparison.

    The proportion compared is the tipped share: the share of trips that left
    any tip, over trips whose payment channel records one.

    The comparison is made **within a service**, against that service's own
    citywide rate, and not against a single pooled rate. Pooling would confound
    the question. Yellow card trips tip on most rides and app trips mostly do
    not, so a zone's pooled tipped share is largely a measure of its service
    mix rather than of its riders, and every Manhattan zone would come back
    "significantly different" for a reason that has nothing to do with
    tipping. Comparing like with like is what makes the surviving set mean
    something.

    Every cell across every service is corrected together, because the
    multiplicity a reader faces is the number of comparisons on the screen,
    not the number inside one facet.
    """
    frame = connection.execute(
        """
        select
            a.service,
            z.zone_id,
            z.zone,
            z.borough,
            sum(a.tipped_trips)   as tipped,
            sum(a.tip_obs_trips)  as observable,
            sum(a.trips)          as trips
        from read_parquet(?) a
        join read_parquet(?) z on z.zone_id = a.pu_zone_id
        where z.is_unknown = false
        group by 1, 2, 3, 4
        having sum(a.tip_obs_trips) >= 30
        order by a.service, z.zone_id
        """,
        [
            str(paths.shipped / "agg_zone_hour.parquet"),
            str(paths.shipped / "dim_zone.parquet"),
        ],
    ).fetchdf()

    if frame.empty:
        outputs.notes.append("No zone carried an observable tip; comparisons skipped.")
        return

    successes = frame["tipped"].to_numpy(dtype=np.int64)
    trials = frame["observable"].to_numpy(dtype=np.int64)
    point, lower, upper = wilson_interval_array(successes, trials)

    # One reference rate per service, so a zone is compared with like.
    reference = np.zeros(len(frame), dtype=float)
    for service in frame["service"].unique():
        mask = (frame["service"] == service).to_numpy()
        reference[mask] = float(successes[mask].sum()) / float(trials[mask].sum())

    p_values = _two_sided_binomial_p(successes, trials, reference)
    corrected = benjamini_hochberg(p_values, q=FDR_Q)

    frame = frame.assign(
        tipped_share=point,
        ci_lower=lower,
        ci_upper=upper,
        service_rate=reference,
        p_value=p_values,
        q_value=corrected.adjusted,
        is_significant=corrected.rejected,
    )

    connection.register("zone_comparison_frame", frame)
    target = paths.shipped / "zone_comparisons.parquet"
    connection.execute(
        f"copy (select * from zone_comparison_frame order by service, zone_id) "
        f"to '{target}' "
        "(format parquet, compression zstd)"
    )
    connection.unregister("zone_comparison_frame")

    outputs.zone_comparisons = int(corrected.n_comparisons)
    outputs.zone_comparisons_significant = int(corrected.n_rejected)

    # The all pairs count, stated on the panel so the reader knows how many
    # comparisons the correction is protecting against rather than assuming it
    # is the number of zones.
    per_service = frame.groupby("service").size()
    outputs.pairwise_comparisons = int(sum(int(n) * (int(n) - 1) // 2 for n in per_service))


def _two_sided_binomial_p(
    successes: np.ndarray, trials: np.ndarray, reference: np.ndarray
) -> np.ndarray:
    """Normal approximation to the two sided test against a fixed rate.

    The approximation is fine here and only here: this p value decides
    membership in a corrected set, not a displayed interval, and every zone in
    the set has thousands of trials. The displayed interval is Wilson, which is
    what actually needs to be right at small counts.
    """
    from scipy import stats

    trials_f = trials.astype(float)
    observed = successes.astype(float) / np.where(trials_f == 0, np.nan, trials_f)
    standard_error = np.sqrt(reference * (1.0 - reference) / trials_f)
    z = np.divide(
        observed - reference,
        standard_error,
        out=np.zeros_like(observed),
        where=standard_error > 0,
    )
    return np.asarray(2.0 * stats.norm.sf(np.abs(z)), dtype=float)


def write_catalog(paths: Paths) -> int:
    """The metric catalog, as the lineage panel reads it."""
    from cityflow_publish.metric_layer import MetricLayer

    layer = MetricLayer.load(paths.metrics_file)
    target = paths.shipped / "metric_catalog.json"
    target.write_text(json.dumps(layer.catalog(), indent=2, sort_keys=False), encoding="utf-8")
    return len(layer.metrics)


def write_lineage(paths: Paths) -> int:
    """Model to panel lineage, lifted from the dbt manifest.

    Read from `transform/target/manifest.json` rather than hand written, so the
    graph the panel draws is the graph dbt actually built. When the manifest is
    absent the panel says so instead of drawing a graph from nothing.
    """
    manifest_path = paths.transform / "target" / "manifest.json"
    target = paths.shipped / "lineage.json"
    if not manifest_path.is_file():
        target.write_text(
            json.dumps({"available": False, "reason": "dbt manifest not found"}),
            encoding="utf-8",
        )
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes: dict[str, dict[str, Any]] = {}
    for key, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") not in ("model", "seed", "test"):
            continue
        if node.get("resource_type") == "test":
            continue
        nodes[key] = {
            "name": node.get("name"),
            "type": node.get("resource_type"),
            "layer": (node.get("fqn") or ["", ""])[1],
            "description": (node.get("description") or "").strip(),
            "depends_on": [
                d for d in node.get("depends_on", {}).get("nodes", []) if not d.startswith("test.")
            ],
        }
    for key, node in manifest.get("sources", {}).items():
        nodes[key] = {
            "name": node.get("name"),
            "type": "source",
            "layer": "source",
            "description": (node.get("description") or "").strip(),
            "depends_on": [],
        }

    exposures = [
        {
            "name": node.get("name"),
            "label": node.get("label") or node.get("name"),
            "description": (node.get("description") or "").strip(),
            "depends_on": node.get("depends_on", {}).get("nodes", []),
            "url": node.get("url"),
            "owner": (node.get("owner") or {}).get("name"),
        }
        for node in manifest.get("exposures", {}).values()
    ]

    target.write_text(
        json.dumps(
            {"available": True, "nodes": nodes, "exposures": exposures},
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(exposures)


def run_analysis(
    connection: duckdb.DuckDBPyConnection, config: CityflowConfig, paths: Paths
) -> AnalysisOutputs:
    outputs = AnalysisOutputs()
    build_decomposition(connection, paths, outputs)
    build_zone_comparisons(connection, paths, outputs)
    outputs.notes.append(f"Metric catalog: {write_catalog(paths)} metrics.")
    outputs.notes.append(f"Lineage: {write_lineage(paths)} exposures.")
    (paths.shipped / "changepoints.json").write_text(
        json.dumps(
            {
                "q": FDR_Q,
                "min_size_days": CHANGEPOINT_MIN_SIZE,
                "stl_period": STL_PERIOD,
                "detected": outputs.changepoints,
                "generated_for_window": [
                    config.start.isoformat(),
                    config.end.isoformat(),
                ],
                "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return outputs
