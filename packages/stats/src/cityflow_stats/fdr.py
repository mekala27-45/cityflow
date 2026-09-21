"""False discovery rate control for the "which zones actually differ" panel.

Comparing every pair of 264 zones is 34,716 tests. At an uncorrected 5 percent
level roughly 1,700 of them come back significant on pure noise, which is more
than the panel would show on a real effect. Bonferroni fixes that and costs
almost all of the power, because the pairs are far from independent and the
family is enormous.

Benjamini-Hochberg controls the expected share of false positives among the
pairs the panel marks, which is the quantity a reader of that panel actually
cares about: "of the differences shown here, about 5 percent are noise". It is
valid under independence and under positive regression dependence, which is the
right assumption for pairwise comparisons drawn from a common pool of zones.

NaN p values are not treated as p values of 1. A pair where one zone had no
trips in the selected window is a test that was never run, and counting it
inflates the denominator, which makes every other pair harder to detect. Those
entries come back NaN and not rejected, and they are left out of
n_comparisons, so the number the interface prints is the number of tests that
happened.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import stats

__all__ = ["FDRResult", "PairwiseTests", "benjamini_hochberg", "pairwise_proportion_tests"]

CountArray = npt.NDArray[np.integer] | npt.NDArray[np.floating]


@dataclass(frozen=True, slots=True)
class FDRResult:
    """The outcome of one Benjamini-Hochberg step-up run."""

    rejected: npt.NDArray[np.bool_]
    adjusted: npt.NDArray[np.float64]
    n_comparisons: int
    n_rejected: int
    q: float
    critical_p: float | None


@dataclass(frozen=True, slots=True)
class PairwiseTests:
    """Every pairwise two proportion z test over a set of groups.

    pairs[k] holds the two positions in the input arrays that p_values[k]
    compares, so the caller can map back to zone ids without recomputing the
    upper triangle.
    """

    p_values: npt.NDArray[np.float64]
    pairs: npt.NDArray[np.int64]
    n_groups: int
    n_comparisons: int


def benjamini_hochberg(p_values: npt.ArrayLike, q: float = 0.05) -> FDRResult:
    """Benjamini-Hochberg step-up procedure at false discovery rate q.

    Args:
        p_values: raw p values, NaN where a test could not be run.
        q: the false discovery rate to control, 0.05 by default.

    Returns:
        An FDRResult whose adjusted values are the standard BH adjusted p
        values: the running minimum of m/i times p(i) taken from the largest p
        downwards, clipped at 1. The running minimum is what makes them
        monotone in the raw p value, which matters because a non monotone
        adjusted column lets a reader sort the table and see a smaller p value
        marked non significant next to a larger one that is marked.
    """
    if not 0.0 < q <= 1.0:
        raise ValueError(f"q must lie in (0, 1], got {q}")

    raw = np.asarray(p_values, dtype=np.float64).ravel()
    adjusted = np.full(raw.shape, np.nan, dtype=np.float64)
    rejected = np.zeros(raw.shape, dtype=np.bool_)

    tested = np.isfinite(raw)
    count = int(tested.sum())
    if count == 0:
        return FDRResult(rejected, adjusted, 0, 0, q, None)
    if np.any(raw[tested] < 0.0) or np.any(raw[tested] > 1.0):
        raise ValueError("p values must lie in [0, 1]")

    positions = np.flatnonzero(tested)
    order = np.argsort(raw[positions], kind="stable")
    ascending = raw[positions][order]
    ranks = np.arange(1, count + 1, dtype=np.float64)

    inflated = ascending * count / ranks
    step_up = np.minimum.accumulate(inflated[::-1])[::-1]
    np.clip(step_up, 0.0, 1.0, out=step_up)
    adjusted[positions[order]] = step_up

    below = ascending <= ranks * q / count
    largest = int(np.flatnonzero(below).max()) + 1 if below.any() else 0
    if largest:
        rejected[positions[order[:largest]]] = True
    critical_p = float(ascending[largest - 1]) if largest else None

    return FDRResult(
        rejected=rejected,
        adjusted=adjusted,
        n_comparisons=count,
        n_rejected=largest,
        q=q,
        critical_p=critical_p,
    )


def pairwise_proportion_tests(
    successes: CountArray,
    trials: CountArray,
    min_trials: int = 1,
) -> PairwiseTests:
    """All pairwise two proportion z tests over a set of groups.

    The z test is the pooled variance form, which is the one that matches the
    null being tested (the two groups share a proportion). The unpooled form
    belongs with a confidence interval on the difference, not with a test of
    equality.

    Args:
        successes: per group counts of the event.
        trials: per group denominators.
        min_trials: groups below this denominator are not tested against
            anything. The normal approximation behind the z test is poor on a
            handful of trips, and a zone with four trips marked as
            significantly different from midtown is a bug report waiting to
            happen. The pair still appears in pairs, with a NaN p value.

    Returns:
        PairwiseTests, with the pairs in upper triangle order: (0, 1), (0, 2),
        and so on.
    """
    success_counts = np.asarray(successes, dtype=np.float64).ravel()
    trial_counts = np.asarray(trials, dtype=np.float64).ravel()
    if success_counts.shape != trial_counts.shape:
        raise ValueError("successes and trials must have the same shape")
    if np.any(trial_counts < 0):
        raise ValueError("trials must be non negative")
    if min_trials < 1:
        raise ValueError(f"min_trials must be at least 1, got {min_trials}")

    n_groups = int(success_counts.size)
    left, right = np.triu_indices(n_groups, k=1)
    pairs = np.column_stack((left, right)).astype(np.int64, copy=False)
    if left.size == 0:
        return PairwiseTests(np.empty(0, dtype=np.float64), pairs, n_groups, 0)

    trials_left = trial_counts[left]
    trials_right = trial_counts[right]
    testable = (trials_left >= min_trials) & (trials_right >= min_trials)

    # Untestable pairs are divided by one and masked out below. Letting them
    # divide by zero would raise, because warnings are errors in this suite.
    safe_left = np.where(testable, trials_left, 1.0)
    safe_right = np.where(testable, trials_right, 1.0)
    successes_left = np.where(testable, success_counts[left], 0.0)
    successes_right = np.where(testable, success_counts[right], 0.0)

    pooled = (successes_left + successes_right) / (safe_left + safe_right)
    variance = pooled * (1.0 - pooled) * (1.0 / safe_left + 1.0 / safe_right)
    standard_error = np.sqrt(variance)
    difference = successes_left / safe_left - successes_right / safe_right

    # A zero pooled variance means both groups were all success or all failure
    # at any denominator. There is no evidence of a difference there, so the
    # test returns 1 rather than a NaN that would look like a missing group.
    degenerate = standard_error <= 0.0
    z_scores = difference / np.where(degenerate, 1.0, standard_error)
    p_values = 2.0 * np.asarray(stats.norm.sf(np.abs(z_scores)), dtype=np.float64)
    p_values = np.where(degenerate, 1.0, p_values)
    p_values = np.where(testable, p_values, np.nan)

    return PairwiseTests(
        p_values=p_values.astype(np.float64, copy=False),
        pairs=pairs,
        n_groups=n_groups,
        n_comparisons=int(testable.sum()),
    )
