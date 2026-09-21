"""Interval estimates, multiple testing correction, decomposition and changepoints.

Every number the dashboard publishes with a claim attached comes through here,
so that the claim and the uncertainty are computed in one place and tested
against published worked examples rather than against last week's output.
"""

from cityflow_stats.fdr import (
    FDRResult,
    PairwiseTests,
    benjamini_hochberg,
    pairwise_proportion_tests,
)
from cityflow_stats.pelt import L2Cost, bic_penalty, estimate_sigma, pelt
from cityflow_stats.stl import Decomposition, decompose
from cityflow_stats.wilson import Interval, mean_ci, wilson_interval, wilson_interval_array

__all__ = [
    "Decomposition",
    "FDRResult",
    "Interval",
    "L2Cost",
    "PairwiseTests",
    "benjamini_hochberg",
    "bic_penalty",
    "decompose",
    "estimate_sigma",
    "mean_ci",
    "pairwise_proportion_tests",
    "pelt",
    "wilson_interval",
    "wilson_interval_array",
]
