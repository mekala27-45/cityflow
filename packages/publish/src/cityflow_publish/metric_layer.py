"""The metric layer: one definition per published number, and a way to check it.

Every analytics organisation eventually spends a quarter arguing about why two
dashboards disagree about revenue. The cause is almost never a bug. It is that
the number was written twice, by two people, at two grains, and both are
defensible in isolation.

The defence here has three parts.

1. Each metric is defined once, in `metrics/metrics.yml`, with a description
   that says what it means and what it excludes.

2. Each metric carries **two** expressions: one over `fct_trip`, the grain,
   and one over the additive columns of the shipped aggregate layer that the
   browser actually queries. `reconcile` evaluates both and fails when they
   disagree. A metric layer that cannot prove the shipped copy agrees with the
   warehouse is a document about the code, not a constraint on it.

3. A test refuses to let the dashboard compute a metric the layer defines. See
   `tests/test_metric_layer_enforcement.py`, which includes a deliberate
   violation, because a gate whose only test asserts the current tree passes is
   not a gate.

The `components` list is the mechanism behind part two. It names the additive
columns the browser expression needs, the aggregate builders read that list,
and publishing an aggregate that is missing a component a metric needs fails at
build time rather than returning nulls in the interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

MetricKind = Literal["sum", "mean", "ratio", "proportion"]
IntervalMethod = Literal["wilson", "student-t"]

VALID_KINDS: frozenset[str] = frozenset({"sum", "mean", "ratio", "proportion"})
VALID_INTERVALS: frozenset[str] = frozenset({"wilson", "student-t"})


class MetricLayerError(ValueError):
    """The definition file is wrong. Raised at load, never at query time."""


@dataclass(frozen=True, slots=True)
class Metric:
    """One published number."""

    name: str
    kind: MetricKind
    description: str
    grain: tuple[str, ...]
    unit: str
    format: str
    components: tuple[str, ...]
    warehouse: str
    browser: str
    owner: str
    models: tuple[str, ...]
    filters: tuple[str, ...] = ()
    interval: IntervalMethod | None = None
    numerator: str | None = None
    denominator: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise MetricLayerError(
                f"{self.name}: kind {self.kind!r} is not one of {sorted(VALID_KINDS)}"
            )
        if self.interval is not None and self.interval not in VALID_INTERVALS:
            raise MetricLayerError(
                f"{self.name}: interval {self.interval!r} is not one of {sorted(VALID_INTERVALS)}"
            )
        if self.kind == "proportion":
            if self.interval != "wilson":
                raise MetricLayerError(
                    f"{self.name} is a proportion and must carry a Wilson "
                    "interval. The normal approximation is wrong at the counts "
                    "a zone and hour filter produces, which is exactly when "
                    "somebody reads it."
                )
            if not (self.numerator and self.denominator):
                raise MetricLayerError(
                    f"{self.name} is a proportion and must name its numerator "
                    "and denominator columns, or the interval cannot be built "
                    "in the browser."
                )
        if self.kind == "sum" and self.interval is not None:
            raise MetricLayerError(
                f"{self.name}: a total is not an estimate and does not take an interval."
            )
        if not self.components:
            raise MetricLayerError(f"{self.name}: no components named")

    def browser_sql(self, dimensions: tuple[str, ...] = (), source: str = "agg") -> str:
        """The query the page runs for this metric at the given grain."""
        select = [*dimensions, f"{self.browser} as {self.name}"]
        group = f"\ngroup by {', '.join(dimensions)}" if dimensions else ""
        return f"select {', '.join(select)}\nfrom {source}{group}"

    def warehouse_sql(self, dimensions: tuple[str, ...] = (), source: str = "fct_trip") -> str:
        """The same metric, computed from the grain, for the reconcile check.

        `filters` describes the population the metric is defined over and is
        rendered as a comment, not as a WHERE clause. The expression has to
        encode its own population, because the shipped components already do
        and the two have to be comparable. An expression that forgets its
        filter therefore disagrees with the browser expression, and reconcile
        catches it. That is the test: the population is not restated in two
        places where it could drift, it is stated once and checked.
        """
        note = f"-- population: {' and '.join(self.filters)}\n" if self.filters else ""
        select = [*dimensions, f"{self.warehouse} as {self.name}"]
        group = f"\ngroup by {', '.join(dimensions)}" if dimensions else ""
        return f"{note}select {', '.join(select)}\nfrom {source}{group}"


@dataclass(frozen=True, slots=True)
class MetricLayer:
    """Every metric, loaded and validated."""

    metrics: tuple[Metric, ...]
    path: Path

    @classmethod
    def load(cls, path: Path) -> MetricLayer:
        if not path.is_file():
            raise MetricLayerError(f"No metric definitions at {path}")
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults: dict[str, Any] = raw.get("defaults", {})
        entries: list[dict[str, Any]] = raw.get("metrics", [])
        if not entries:
            raise MetricLayerError(f"{path} defines no metrics")

        metrics: list[Metric] = []
        seen: set[str] = set()
        for entry in entries:
            name = entry.get("name")
            if not name:
                raise MetricLayerError(f"{path}: a metric has no name")
            if name in seen:
                raise MetricLayerError(f"{path}: {name} is defined twice")
            seen.add(name)
            metrics.append(
                Metric(
                    name=name,
                    kind=entry["kind"],
                    description=str(entry["description"]).strip(),
                    grain=tuple(entry.get("grain", ())),
                    unit=str(entry.get("unit", "")),
                    format=str(entry.get("format", "{}")),
                    components=tuple(entry["components"]),
                    warehouse=_collapse(entry["warehouse"]),
                    browser=_collapse(entry["browser"]),
                    owner=str(entry.get("owner", defaults.get("owner", "analytics"))),
                    models=tuple(entry.get("models", defaults.get("models", ()))),
                    filters=tuple(entry.get("filters", ())),
                    interval=entry.get("interval"),
                    numerator=entry.get("numerator"),
                    denominator=entry.get("denominator"),
                )
            )
        return cls(metrics=tuple(metrics), path=path)

    def __getitem__(self, name: str) -> Metric:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(
            f"{name!r} is not a defined metric. Defined: {', '.join(m.name for m in self.metrics)}"
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(metric.name for metric in self.metrics)

    def required_components(self) -> tuple[str, ...]:
        """Every additive column the shipped aggregates must carry.

        The aggregate builders read this, so a metric that needs a component
        nobody publishes fails at build time rather than in the interface.
        """
        out: set[str] = set()
        for metric in self.metrics:
            out.update(metric.components)
        return tuple(sorted(out))

    def compile(self, name: str, dimensions: tuple[str, ...] = ()) -> str:
        """The compiled SQL for one metric, as the catalog panel displays it."""
        metric = self[name]
        return (
            f"-- {metric.name}: {metric.description}\n"
            f"-- grain: {', '.join(metric.grain)}\n"
            f"-- owner: {metric.owner}\n"
            f"-- from the warehouse, at trip grain:\n"
            f"{metric.warehouse_sql(dimensions)}\n\n"
            f"-- from the shipped aggregates, which is what the page runs:\n"
            f"{metric.browser_sql(dimensions)}"
        )

    def catalog(self) -> list[dict[str, Any]]:
        """The catalog the metric lineage panel renders, as plain data."""
        return [
            {
                "name": metric.name,
                "kind": metric.kind,
                "description": metric.description,
                "grain": list(metric.grain),
                "unit": metric.unit,
                "format": metric.format,
                "interval": metric.interval,
                "filters": list(metric.filters),
                "owner": metric.owner,
                "models": list(metric.models),
                "components": list(metric.components),
                "warehouse_sql": metric.warehouse_sql(),
                "browser_sql": metric.browser_sql(),
                "numerator": metric.numerator,
                "denominator": metric.denominator,
            }
            for metric in self.metrics
        ]


def _collapse(text: str) -> str:
    """Fold a YAML folded scalar back into one line of SQL."""
    return re.sub(r"\s+", " ", str(text)).strip()
