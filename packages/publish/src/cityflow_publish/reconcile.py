"""Run every metric two ways and fail when they disagree.

The warehouse expression reads `fct_trip`, the grain. The browser expression
reads the shipped aggregates, which is what the page actually runs. Both come
from the same definition, and neither is derived from the other, so agreeing is
evidence rather than tautology.

This is the check that makes a metric layer load bearing. A definitions file
without it is a document about the code. With it, shipping an aggregate that
quietly drops a filter, or a metric whose expression forgets its own
population, breaks the build.

Three grains are checked, not one: the whole window, by service, and by month.
A metric can agree in total and disagree everywhere underneath, which is the
usual shape of a grain bug, and checking only the total is how that ships.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import duckdb

from cityflow_core.paths import Paths
from cityflow_publish.metric_layer import Metric, MetricLayer

# Relative tolerance. These are sums of the same doubles in a different order,
# so the difference is floating point associativity and nothing else. Anything
# larger than this is a real disagreement.
RELATIVE_TOLERANCE = 1e-9
ABSOLUTE_TOLERANCE = 1e-6

# The aggregate the browser expressions are written against.
BROWSER_SOURCE = "agg_zone_hour.parquet"

GRAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("overall", ()),
    ("by service", ("service",)),
    ("by month", ("month",)),
    ("by service and month", ("service", "month")),
)


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One cell where the two paths differ."""

    metric: str
    grain: str
    key: str
    warehouse: float | None
    browser: float | None

    @property
    def difference(self) -> float:
        if self.warehouse is None or self.browser is None:
            return math.inf
        return abs(self.warehouse - self.browser)

    def __str__(self) -> str:
        return (
            f"{self.metric} [{self.grain}] {self.key}: "
            f"warehouse {self.warehouse!r} vs browser {self.browser!r}"
        )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    checks: int
    cells: int
    disagreements: tuple[Disagreement, ...]

    @property
    def ok(self) -> bool:
        return not self.disagreements


def _close(left: float | None, right: float | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if math.isnan(left) and math.isnan(right):
        return True
    return math.isclose(left, right, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


def _warehouse_dimensions(dimensions: tuple[str, ...]) -> tuple[str, ...]:
    """The fact table calls the month column something else.

    Written out rather than hidden in an alias, because a silent rename is how
    a reconcile check ends up comparing two different groupings and passing.
    """
    return tuple(
        "date_trunc('month', pickup_date)::date as month" if d == "month" else d for d in dimensions
    )


def reconcile_metric(
    connection: duckdb.DuckDBPyConnection,
    metric: Metric,
    paths: Paths,
) -> list[Disagreement]:
    """Compare one metric across every grain."""
    aggregate = paths.shipped / BROWSER_SOURCE
    out: list[Disagreement] = []

    for label, dimensions in GRAINS:
        group = ", ".join(str(i + 1) for i in range(len(dimensions)))

        warehouse_select = ", ".join(
            [*_warehouse_dimensions(dimensions), f"{metric.warehouse} as value"]
        )
        warehouse_sql = f"select {warehouse_select} from fct_trip" + (
            f" group by {group}" if dimensions else ""
        )
        browser_select = ", ".join([*dimensions, f"{metric.browser} as value"])
        browser_sql = f"select {browser_select} from read_parquet('{aggregate}')" + (
            f" group by {group}" if dimensions else ""
        )

        if dimensions:
            joined = connection.execute(
                f"""
                with w as ({warehouse_sql}), b as ({browser_sql})
                select {", ".join(f"coalesce(w.{d}, b.{d}) as {d}" for d in dimensions)},
                       w.value as wv, b.value as bv
                from w full outer join b
                  on {" and ".join(f"w.{d} is not distinct from b.{d}" for d in dimensions)}
                """
            ).fetchall()
            for row in joined:
                key_values = row[: len(dimensions)]
                warehouse_value, browser_value = row[-2], row[-1]
                if not _close(warehouse_value, browser_value):
                    out.append(
                        Disagreement(
                            metric=metric.name,
                            grain=label,
                            key=", ".join(
                                f"{k}={v}" for k, v in zip(dimensions, key_values, strict=True)
                            ),
                            warehouse=warehouse_value,
                            browser=browser_value,
                        )
                    )

        else:
            warehouse_value = _single(connection, warehouse_sql)
            browser_value = _single(connection, browser_sql)
            if not _close(warehouse_value, browser_value):
                out.append(
                    Disagreement(
                        metric=metric.name,
                        grain=label,
                        key="all",
                        warehouse=warehouse_value,
                        browser=browser_value,
                    )
                )
    return out


def reconcile_all(connection: duckdb.DuckDBPyConnection, paths: Paths) -> ReconcileReport:
    layer = MetricLayer.load(paths.metrics_file)
    aggregate = paths.shipped / BROWSER_SOURCE
    if not aggregate.is_file():
        raise FileNotFoundError(
            f"{aggregate} is missing. Run 'cityflow publish' before reconciling: "
            "there is nothing to compare the warehouse against."
        )

    disagreements: list[Disagreement] = []
    cells = 0
    for metric in layer.metrics:
        found = reconcile_metric(connection, metric, paths)
        disagreements.extend(found)
        cells += 1
    return ReconcileReport(
        checks=len(layer.metrics) * len(GRAINS),
        cells=cells,
        disagreements=tuple(disagreements),
    )


def _single(connection: duckdb.DuckDBPyConnection, sql: str) -> float | None:
    row = connection.execute(sql).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])
