"""Wilson and t intervals checked against published values, not against ourselves.

The reference numbers here come from two places: the worked example every
textbook treatment of the score interval uses (10 successes in 100 trials at 95
percent) and statsmodels' independent implementation of the same interval. A
test that compares the function to itself would pass on any sign error that is
made consistently.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from statsmodels.stats.proportion import proportion_confint

from cityflow_stats import Interval, mean_ci, wilson_interval, wilson_interval_array


def test_textbook_ten_of_one_hundred() -> None:
    """The standard worked example: 10 of 100 at 95 percent is (0.0552, 0.1744)."""
    interval = wilson_interval(10, 100, confidence=0.95)
    assert interval.point == pytest.approx(0.10, abs=1e-12)
    assert interval.lower == pytest.approx(0.0552, abs=5e-5)
    assert interval.upper == pytest.approx(0.1744, abs=5e-5)
    assert interval.method == "wilson"
    assert interval.n == 100


def test_matches_statsmodels_across_a_grid() -> None:
    """An independent implementation of the same interval, over counts that matter here."""
    for trials in (5, 10, 37, 100, 2500):
        for successes in (0, 1, trials // 3, trials - 1, trials):
            reference_lower, reference_upper = proportion_confint(
                successes, trials, alpha=0.05, method="wilson"
            )
            interval = wilson_interval(successes, trials)
            assert interval.lower == pytest.approx(float(reference_lower), abs=1e-12)
            assert interval.upper == pytest.approx(float(reference_upper), abs=1e-12)


def test_zero_successes_keeps_the_lower_bound_at_zero() -> None:
    """0 of 10 has an upper bound of 0.2775, where the Wald interval has none at all."""
    interval = wilson_interval(0, 10)
    assert interval.point == pytest.approx(0.0, abs=1e-12)
    assert interval.lower == pytest.approx(0.0, abs=1e-12)
    assert interval.upper == pytest.approx(0.2775, abs=5e-5)


def test_extreme_counts_stay_inside_the_unit_interval() -> None:
    for successes, trials in ((0, 1), (1, 1), (0, 3), (3, 3)):
        interval = wilson_interval(successes, trials)
        assert interval.lower is not None
        assert interval.upper is not None
        assert 0.0 <= interval.lower <= interval.upper <= 1.0


def test_interval_is_not_symmetric_about_the_point_estimate() -> None:
    """The asymmetry is the whole point of the method, so assert it, do not assume it.

    The two halves differ by exactly twice the shrinkage of the Wilson centre
    towards one half, which at 10 of 100 is 2 * (0.114797 - 0.1) = 0.029595.
    """
    interval = wilson_interval(10, 100)
    assert interval.point is not None
    assert interval.lower is not None
    assert interval.upper is not None
    below = interval.point - interval.lower
    above = interval.upper - interval.point
    assert above - below == pytest.approx(0.029595, abs=5e-6)


@pytest.mark.parametrize("successes", [0, 1, 2])
def test_differs_from_the_normal_approximation_at_small_n(successes: int) -> None:
    """At n = 10 the Wald interval is wrong by far more than a rounding difference."""
    trials = 10
    proportion = successes / trials
    z = 1.959963985
    wald_halfwidth = z * math.sqrt(proportion * (1.0 - proportion) / trials)
    interval = wilson_interval(successes, trials)
    assert interval.lower is not None
    assert interval.upper is not None
    gap = max(
        abs(interval.lower - (proportion - wald_halfwidth)),
        abs(interval.upper - (proportion + wald_halfwidth)),
    )
    assert gap > 0.01


def test_no_trials_gives_a_null_interval() -> None:
    interval = wilson_interval(0, 0)
    assert interval == Interval(None, None, None, "wilson", 0)


def test_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError, match="successes"):
        wilson_interval(11, 10)
    with pytest.raises(ValueError, match="trials"):
        wilson_interval(0, -1)
    with pytest.raises(ValueError, match="confidence"):
        wilson_interval(1, 10, confidence=1.0)


def test_array_form_agrees_with_the_scalar_form() -> None:
    successes = np.array([0, 1, 10, 37, 2500], dtype=np.int64)
    trials = np.array([10, 10, 100, 37, 2500], dtype=np.int64)
    point, lower, upper = wilson_interval_array(successes, trials)
    for position in range(successes.size):
        scalar = wilson_interval(int(successes[position]), int(trials[position]))
        assert scalar.point is not None
        assert scalar.lower is not None
        assert scalar.upper is not None
        assert float(point[position]) == pytest.approx(scalar.point, abs=1e-12)
        assert float(lower[position]) == pytest.approx(scalar.lower, abs=1e-12)
        assert float(upper[position]) == pytest.approx(scalar.upper, abs=1e-12)


def test_array_form_returns_nan_for_empty_groups() -> None:
    point, lower, upper = wilson_interval_array(
        np.array([0, 5]),
        np.array([0, 10]),
    )
    assert np.isnan(point[0]) and np.isnan(lower[0]) and np.isnan(upper[0])
    assert float(point[1]) == pytest.approx(0.5, abs=1e-12)


def test_mean_ci_matches_the_textbook_t_interval() -> None:
    """Mean 5.0, s = 2.138, n = 8, t(0.975, 7) = 2.365 gives (3.2125, 6.7875)."""
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    interval = mean_ci(values, confidence=0.95)
    assert interval.point == pytest.approx(5.0, abs=1e-12)
    assert interval.lower == pytest.approx(3.2125, abs=5e-4)
    assert interval.upper == pytest.approx(6.7875, abs=5e-4)
    assert interval.method == "student-t"
    assert interval.n == 8


def test_mean_ci_is_wider_than_the_normal_interval_it_replaces() -> None:
    """The t correction is worth about 30 percent of the halfwidth at n = 8."""
    values = np.array([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    interval = mean_ci(values)
    assert interval.lower is not None and interval.upper is not None
    normal_halfwidth = 1.959963985 * float(values.std(ddof=1)) / math.sqrt(values.size)
    assert (interval.upper - interval.lower) / 2.0 > normal_halfwidth


def test_mean_ci_degenerate_samples() -> None:
    assert mean_ci([]) == Interval(None, None, None, "student-t", 0)
    assert mean_ci([3.5]) == Interval(3.5, None, None, "student-t", 1)
    assert mean_ci([1.0, np.nan, 3.0]).n == 2
