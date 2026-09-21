"""Interval estimates for the zone level measures on the dashboard.

Two kinds of measure appear on this dashboard and they need different
intervals.

A proportion is a count over a count: the share of trips in a zone that were
tipped, the share that started at an airport, the share paid by card. The
denominator counts trips and the numerator counts the trips that did the thing.
Those get wilson_interval. The Wilson score interval inverts the score test
instead of the Wald test, so it stays inside [0, 1], it is defined when the
count is 0 or equal to the denominator, and its coverage near the boundary is
close to nominal where the normal approximation collapses to a point. Zones on
this map carry a few dozen trips between 3am and 5am, so that is not an
academic concern.

A mean is not a proportion even when it is printed with a percent sign. Mean
tip share (the average over trips of tip divided by fare) and mean trip
duration are means of a continuous quantity: their sampling distribution is
governed by the spread of the per trip values, not by n and p. Those get
mean_ci, a Student t interval.

Choosing the wrong one is the usual mistake here, because "tip rate" names both
the share of trips that tipped (a proportion, Wilson) and the mean of tip over
fare (a mean, t interval), and the two numbers are not the same quantity.
Whoever adds a metric picks the function from how the metric is defined in
metrics/metrics.yml, not from how the number is formatted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import stats

__all__ = ["Interval", "mean_ci", "wilson_interval", "wilson_interval_array"]

# Successes and trials arrive as counts, which are integers in the warehouse and
# floats once they have been through a pandas groupby with a nullable column.
CountArray = npt.NDArray[np.integer] | npt.NDArray[np.floating]


@dataclass(frozen=True, slots=True)
class Interval:
    """A point estimate with its confidence bounds and the method that made it.

    The three values are None together, and only when there was nothing to
    estimate from: a zone with no trips in the selected hour. Returning 0.0
    there would be read as a measured zero by every consumer downstream, and it
    would plot as a zero bar next to zones that were actually measured.
    """

    point: float | None
    lower: float | None
    upper: float | None
    method: str
    n: int


def _z_critical(confidence: float) -> float:
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must lie in (0, 1), got {confidence}")
    return float(stats.norm.ppf(0.5 + confidence / 2.0))


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> Interval:
    """Wilson score interval for a binomial proportion.

    Args:
        successes: number of trips that did the thing.
        trials: number of trips observed.
        confidence: two sided coverage, 0.95 by default.

    Returns:
        An Interval whose point estimate is the raw proportion. The point is
        deliberately not the Wilson centre: the centre is shrunk towards 0.5 and
        reporting it as "the" tip rate would disagree with the number the same
        query returns without an interval.
    """
    if trials < 0:
        raise ValueError(f"trials must be non negative, got {trials}")
    if successes < 0 or successes > trials:
        raise ValueError(f"successes must lie in [0, {trials}], got {successes}")
    if trials == 0:
        return Interval(None, None, None, "wilson", 0)

    z = _z_critical(confidence)
    z_squared = z * z
    proportion = successes / trials
    denominator = 1.0 + z_squared / trials
    centre = (proportion + z_squared / (2.0 * trials)) / denominator
    spread = (z / denominator) * float(
        np.sqrt(proportion * (1.0 - proportion) / trials + z_squared / (4.0 * trials * trials))
    )
    # Wilson cannot leave [0, 1] algebraically. The clamp is against floating
    # point drift at the boundary, where centre and spread are nearly equal.
    return Interval(
        point=proportion,
        lower=max(0.0, centre - spread),
        upper=min(1.0, centre + spread),
        method="wilson",
        n=trials,
    )


def wilson_interval_array(
    successes: CountArray,
    trials: CountArray,
    confidence: float = 0.95,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Wilson intervals for many groups at once.

    The map redraws intervals for all 260 zones on every filter change, so the
    scalar function in a comprehension is the wrong shape of code here.

    Zones with no trips come back as NaN in all three arrays rather than as a
    zero, for the reason given on Interval.

    Returns:
        (point, lower, upper), each the shape of the broadcast inputs.
    """
    success_counts = np.asarray(successes, dtype=np.float64)
    trial_counts = np.asarray(trials, dtype=np.float64)
    success_counts, trial_counts = np.broadcast_arrays(success_counts, trial_counts)

    if np.any(trial_counts < 0):
        raise ValueError("trials must be non negative")
    observed = trial_counts > 0
    if np.any(success_counts[observed] < 0) or np.any(
        success_counts[observed] > trial_counts[observed]
    ):
        raise ValueError("successes must lie in [0, trials] elementwise")

    z = _z_critical(confidence)
    z_squared = z * z
    # Empty groups are divided by one and masked out afterwards, which keeps
    # numpy from raising a divide warning that pytest is configured to treat as
    # an error.
    safe_trials = np.where(observed, trial_counts, 1.0)
    proportion = success_counts / safe_trials

    denominator = 1.0 + z_squared / safe_trials
    centre = (proportion + z_squared / (2.0 * safe_trials)) / denominator
    spread = (z / denominator) * np.sqrt(
        proportion * (1.0 - proportion) / safe_trials
        + z_squared / (4.0 * safe_trials * safe_trials)
    )

    nan = np.float64(np.nan)
    point = np.where(observed, proportion, nan)
    lower = np.where(observed, np.maximum(0.0, centre - spread), nan)
    upper = np.where(observed, np.minimum(1.0, centre + spread), nan)
    return (
        point.astype(np.float64, copy=False),
        lower.astype(np.float64, copy=False),
        upper.astype(np.float64, copy=False),
    )


def mean_ci(values: npt.ArrayLike, confidence: float = 0.95) -> Interval:
    """Student t interval for the mean of a continuous measure.

    NaNs are dropped rather than propagated: a trip with a null fare cannot
    contribute to a mean tip share and should not wipe out the zone.

    A single observation yields a point estimate with no bounds. The alternative
    is an infinite interval, which is correct and useless.
    """
    sample = np.asarray(values, dtype=np.float64).ravel()
    sample = sample[np.isfinite(sample)]
    count = int(sample.size)
    if count == 0:
        return Interval(None, None, None, "student-t", 0)

    mean = float(sample.mean())
    if count == 1:
        return Interval(mean, None, None, "student-t", 1)

    standard_error = float(sample.std(ddof=1) / np.sqrt(count))
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must lie in (0, 1), got {confidence}")
    t_critical = float(stats.t.ppf(0.5 + confidence / 2.0, df=count - 1))
    spread = t_critical * standard_error
    return Interval(
        point=mean,
        lower=mean - spread,
        upper=mean + spread,
        method="student-t",
        n=count,
    )
