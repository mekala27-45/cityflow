"""The metric layer, and the checks that make it load bearing.

The layer's value is not that metrics are written down. It is that a metric
cannot be written anywhere else, and that the copy the browser queries has been
proved to agree with the warehouse. Both of those are claims about behaviour
under failure, so both get a test that induces the failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cityflow_core import Paths
from cityflow_publish.aggregates import COMPONENT_SQL, verify_components
from cityflow_publish.metric_layer import MetricLayer, MetricLayerError

PATHS = Paths.resolve()


@pytest.fixture(scope="module")
def layer() -> MetricLayer:
    return MetricLayer.load(PATHS.metrics_file)


def write_layer(tmp_path: Path, metrics: list[dict[str, object]]) -> Path:
    target = tmp_path / "metrics.yml"
    target.write_text(yaml.safe_dump({"version": 1, "metrics": metrics}), encoding="utf-8")
    return target


VALID_PROPORTION: dict[str, object] = {
    "name": "tipped_share",
    "kind": "proportion",
    "description": "Share of observable trips that tipped.",
    "interval": "wilson",
    "components": ["tipped_trips", "tip_obs_trips"],
    "warehouse": "count(*) filter (where tip_amount > 0)::double / count(*)",
    "browser": "sum(tipped_trips)::double / nullif(sum(tip_obs_trips), 0)",
    "numerator": "tipped_trips",
    "denominator": "tip_obs_trips",
}


# ---------------------------------------------------------------------------
# The committed layer
# ---------------------------------------------------------------------------


def test_the_committed_layer_loads(layer: MetricLayer) -> None:
    assert len(layer.metrics) >= 10
    assert len(set(layer.names)) == len(layer.names)


def test_every_component_can_actually_be_published(layer: MetricLayer) -> None:
    """A metric needing a column no builder emits returns nulls in the page."""
    assert verify_components(layer)


def test_no_component_is_published_that_nothing_uses(layer: MetricLayer) -> None:
    """The other direction: a component nobody reads is dead weight in every file."""
    required = set(layer.required_components())
    unused = set(COMPONENT_SQL) - required
    assert not unused, (
        f"These components are published but no metric uses them: {sorted(unused)}. "
        "Every one of them costs bytes in agg_zone_hour and agg_hour_of_week."
    )


def test_every_proportion_names_its_numerator_and_denominator(
    layer: MetricLayer,
) -> None:
    for metric in layer.metrics:
        if metric.kind == "proportion":
            assert metric.numerator in COMPONENT_SQL
            assert metric.denominator in COMPONENT_SQL


def test_every_metric_either_has_an_interval_or_says_why(layer: MetricLayer) -> None:
    for metric in layer.metrics:
        assert metric.interval or metric.interval_note, metric.name


def test_compiled_sql_mentions_both_engines(layer: MetricLayer) -> None:
    compiled = layer.compile("tip_rate")
    assert "fct_trip" in compiled
    assert "agg" in compiled


# ---------------------------------------------------------------------------
# Deliberate violations
# ---------------------------------------------------------------------------


def test_a_duplicate_metric_name_is_refused(tmp_path: Path) -> None:
    path = write_layer(tmp_path, [dict(VALID_PROPORTION), dict(VALID_PROPORTION)])
    with pytest.raises(MetricLayerError, match="defined twice"):
        MetricLayer.load(path)


def test_a_proportion_without_a_wilson_interval_is_refused(tmp_path: Path) -> None:
    """The normal approximation is wrong exactly where a filter takes you."""
    broken = dict(VALID_PROPORTION)
    broken["interval"] = "student-t"
    path = write_layer(tmp_path, [broken])
    with pytest.raises(MetricLayerError, match="Wilson"):
        MetricLayer.load(path)


def test_a_proportion_without_numerator_columns_is_refused(tmp_path: Path) -> None:
    broken = dict(VALID_PROPORTION)
    del broken["numerator"]
    path = write_layer(tmp_path, [broken])
    with pytest.raises(MetricLayerError, match="numerator"):
        MetricLayer.load(path)


def test_a_sum_with_an_interval_is_refused(tmp_path: Path) -> None:
    """A total is not an estimate."""
    broken: dict[str, object] = {
        "name": "revenue",
        "kind": "sum",
        "description": "Total revenue.",
        "interval": "wilson",
        "components": ["total_amount_sum"],
        "warehouse": "sum(total_amount)",
        "browser": "sum(total_amount_sum)",
    }
    path = write_layer(tmp_path, [broken])
    with pytest.raises(MetricLayerError, match="not an estimate"):
        MetricLayer.load(path)


def test_a_metric_with_no_interval_and_no_reason_is_refused(tmp_path: Path) -> None:
    broken: dict[str, object] = {
        "name": "revenue",
        "kind": "sum",
        "description": "Total revenue.",
        "components": ["total_amount_sum"],
        "warehouse": "sum(total_amount)",
        "browser": "sum(total_amount_sum)",
    }
    path = write_layer(tmp_path, [broken])
    with pytest.raises(MetricLayerError, match="interval_note"):
        MetricLayer.load(path)


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    broken = dict(VALID_PROPORTION)
    broken["kind"] = "percentage"
    path = write_layer(tmp_path, [broken])
    with pytest.raises(MetricLayerError, match="kind"):
        MetricLayer.load(path)


def test_a_metric_needing_an_unpublishable_component_is_refused(
    tmp_path: Path,
) -> None:
    """The deliberate violation for verify_components."""
    broken = dict(VALID_PROPORTION)
    broken["components"] = ["tipped_trips", "a_column_nobody_publishes"]
    path = write_layer(tmp_path, [broken])
    with pytest.raises(KeyError, match="a_column_nobody_publishes"):
        verify_components(MetricLayer.load(path))


def test_an_empty_definition_file_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "metrics.yml"
    empty.write_text("version: 1\nmetrics: []\n", encoding="utf-8")
    with pytest.raises(MetricLayerError, match="no metrics"):
        MetricLayer.load(empty)
