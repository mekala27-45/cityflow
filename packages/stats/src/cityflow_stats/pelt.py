"""Exact changepoint detection by PELT (Killick, Fearnhead and Eckley, 2012).

The dashboard needs to say when a zone's level moved and stayed moved: the
congestion surcharge landing, a bridge closing, a neighbourhood's nightlife
coming back. That is a change in mean, not a trend and not an outlier, and it
is found by partitioning the series so that the total within segment cost plus
a penalty per segment is minimised.

Optimal partitioning solves that exactly in O(n squared). PELT adds one
observation: if the best cost up to t, plus the cost of the segment from t to
s, is already worse than the best cost up to s, then t can never be the last
changepoint before any point beyond s, so it can be dropped from the candidate
set forever. With the L2 cost the constant K in that rule is zero, because
splitting a segment never increases its cost. The pruned candidate set stays
bounded under mild conditions, which is what turns the quadratic into something
close to linear, and it changes nothing about the answer: the segmentation is
still the exact minimiser, which test_pelt checks against a brute force
optimal partitioning.

ruptures is not a dependency here on purpose. The algorithm is forty lines, the
package pulls in a version pin that fights with the rest of this repository,
and a changepoint that appears on a public dashboard should come from code that
can be read in one sitting.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

__all__ = ["L2Cost", "bic_penalty", "estimate_sigma", "pelt"]


class L2Cost:
    """Within segment sum of squared deviations from the segment mean.

    Held behind a call interface so a different cost (Poisson for counts,
    normal with changing variance, a kernel cost) can be dropped in without
    touching the search. Segments are half open, [start, end), matching numpy
    slicing.

    Every evaluation is O(1) from prefix sums. Without that, PELT's pruning
    saves nothing: the cost evaluation would carry the quadratic term the
    pruning just removed.
    """

    __slots__ = ("_size", "_sum", "_sum_of_squares")

    def __init__(self, signal: npt.NDArray[np.float64]) -> None:
        values = np.asarray(signal, dtype=np.float64).ravel()
        self._size = int(values.size)
        self._sum: npt.NDArray[np.float64] = np.concatenate(
            ([0.0], np.cumsum(values, dtype=np.float64))
        )
        self._sum_of_squares: npt.NDArray[np.float64] = np.concatenate(
            ([0.0], np.cumsum(values * values, dtype=np.float64))
        )

    def __len__(self) -> int:
        return self._size

    def __call__(self, start: int, end: int) -> float:
        length = end - start
        if length <= 0:
            return 0.0
        total = float(self._sum[end] - self._sum[start])
        total_of_squares = float(self._sum_of_squares[end] - self._sum_of_squares[start])
        # Prefix sums of a series far from zero lose the low bits, so a
        # constant segment can come out at minus 1e-9. Clamping keeps the
        # pruning comparisons from being decided by that noise.
        return max(total_of_squares - total * total / length, 0.0)


def estimate_sigma(signal: npt.NDArray[np.float64]) -> float:
    """Robust noise scale from the median absolute deviation of first differences.

    A plain standard deviation of the signal is the wrong scale here: it is
    inflated by the very level shifts being searched for, so the BIC penalty
    built from it comes out too large and the shifts go undetected. Differencing
    removes the level, and the median absolute deviation ignores the handful of
    differences that contain a shift. The 1.4826 makes the MAD consistent for
    the standard deviation of a normal sample, and the square root of two undoes
    the variance doubling that differencing introduces.
    """
    values = np.asarray(signal, dtype=np.float64).ravel()
    if values.size < 2:
        raise ValueError("need at least two observations to estimate a noise scale")
    if not np.all(np.isfinite(values)):
        raise ValueError("signal contains NaN or infinite values")
    differences = np.diff(values)
    deviation = float(np.median(np.abs(differences - np.median(differences))))
    return 1.4826 * deviation / math.sqrt(2.0)


def bic_penalty(n: int, sigma: float, n_params: int = 1) -> float:
    """The BIC (Schwarz) penalty per changepoint: n_params * sigma squared * log(n).

    n_params is the number of free parameters a changepoint introduces. One
    counts only the new level, and on real data that under penalises: each
    changepoint also carries its own location, so the usual choice for a change
    in mean is two, which is the 2 log n penalty in Killick et al. One is left
    as the default because it is the bare Schwarz form, and because anything
    else would hide the choice inside this function instead of putting it at
    the call site where a reader of the dashboard code can see it. Pass three
    if the variance is allowed to move as well.
    """
    if n < 2:
        raise ValueError(f"n must be at least 2, got {n}")
    if sigma < 0.0:
        raise ValueError(f"sigma must be non negative, got {sigma}")
    if n_params < 1:
        raise ValueError(f"n_params must be at least 1, got {n_params}")
    return n_params * sigma * sigma * math.log(n)


def pelt(
    signal: npt.NDArray[np.float64],
    penalty: float,
    min_size: int = 7,
    jump: int = 1,
) -> list[int]:
    """Find the changepoints that minimise segment cost plus penalty per segment.

    Args:
        signal: one dimensional series, no NaNs.
        penalty: cost charged per segment. See bic_penalty.
        min_size: shortest admissible segment. Seven on daily data, so a single
            odd week cannot be split into its own regime.
        jump: only consider changepoints at multiples of this. One is exact;
            larger values trade optimality for speed on long series.

    Returns:
        Interior changepoint indices in ascending order. Index i means the new
        segment starts at signal[i], so the segments are signal[:c0],
        signal[c0:c1] and so on. The endpoints 0 and len(signal) are not
        included.
    """
    values = np.asarray(signal, dtype=np.float64).ravel()
    if not np.all(np.isfinite(values)):
        raise ValueError("signal contains NaN or infinite values")
    if min_size < 1:
        raise ValueError(f"min_size must be at least 1, got {min_size}")
    if jump < 1:
        raise ValueError(f"jump must be at least 1, got {jump}")
    if penalty < 0.0:
        raise ValueError(f"penalty must be non negative, got {penalty}")

    n = int(values.size)
    if n < 2 * min_size:
        return []
    # A flat signal has zero cost under every partition, so the penalty decides
    # and the answer is no changepoints. Saying so here keeps that answer from
    # depending on how ties fall out of the float comparisons below.
    if float(np.ptp(values)) == 0.0:
        return []

    cost = L2Cost(values)
    best_cost: npt.NDArray[np.float64] = np.full(n + 1, np.inf, dtype=np.float64)
    best_cost[0] = -penalty
    last_change: npt.NDArray[np.int64] = np.zeros(n + 1, dtype=np.int64)

    ends = [end for end in range(min_size, n + 1) if end % jump == 0]
    if not ends or ends[-1] != n:
        ends.append(n)

    candidates: list[int] = [0]
    for end in ends:
        surviving: list[int] = []
        scored: list[tuple[int, float]] = []
        for start in candidates:
            if end - start < min_size:
                # Not admissible yet, but it will be once end has moved on.
                surviving.append(start)
                continue
            scored.append((start, best_cost[start] + cost(start, end)))
        if not scored:
            candidates = surviving
            continue

        # min keeps the first of a tie and scored is in ascending start order,
        # so a tie resolves to the earliest start, which is the segmentation
        # with the fewer changepoints.
        chosen, lowest = min(scored, key=lambda item: item[1])
        best_cost[end] = lowest + penalty
        last_change[end] = chosen

        # The PELT rule with K = 0: a start whose own optimum plus the cost of
        # reaching end already exceeds the optimum at end can never win later.
        surviving.extend(start for start, value in scored if value <= best_cost[end])
        surviving.append(end)
        candidates = sorted(surviving)

    changepoints: list[int] = []
    cursor = n
    while cursor > 0:
        start = int(last_change[cursor])
        if start > 0:
            changepoints.append(start)
        cursor = start
    return sorted(changepoints)
