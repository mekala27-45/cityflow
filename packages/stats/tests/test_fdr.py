"""Benjamini-Hochberg checked against the 1995 paper and against R's p.adjust.

The p value vector below is the one from Benjamini and Hochberg (1995), taken
from Needleman's multiple endpoint study, which is the worked example the
procedure is usually taught from: at q = 0.05 it rejects exactly four of the
fifteen hypotheses where Bonferroni rejects three.
"""

from __future__ import annotations

import numpy as np
import pytest
from cityflow_stats import benjamini_hochberg, pairwise_proportion_tests
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportions_ztest

BH_1995_P_VALUES = np.fromstring(
    "0.0001 0.0004 0.0019 0.0095 0.0201 0.0278 0.0298 0.0344 "
    "0.0459 0.3240 0.4262 0.5719 0.6528 0.7590 1.0000",
    sep=" ",
)

# R: p.adjust(BH_1995_P_VALUES, method = "BH")
R_ADJUSTED = np.fromstring(
    "0.0015000 0.0030000 0.0095000 0.0356250 0.0603000 0.0638571 0.0638571 0.0645000 "
    "0.0765000 0.4860000 0.5811818 0.7148750 0.7532308 0.8132143 1.0000000",
    sep=" ",
)


def test_bh_1995_rejects_exactly_four() -> None:
    result = benjamini_hochberg(BH_1995_P_VALUES, q=0.05)
    assert result.n_comparisons == 15
    assert result.n_rejected == 4
    assert list(np.flatnonzero(result.rejected)) == [0, 1, 2, 3]
    assert result.critical_p == pytest.approx(0.0095, abs=1e-12)
    assert result.q == pytest.approx(0.05, abs=1e-12)


def test_adjusted_values_match_r_p_adjust() -> None:
    result = benjamini_hochberg(BH_1995_P_VALUES, q=0.05)
    assert result.adjusted == pytest.approx(R_ADJUSTED, abs=1e-7)


def test_adjusted_values_match_statsmodels() -> None:
    """A second independent implementation, in case the R figures were transcribed wrong."""
    reference = multipletests(BH_1995_P_VALUES, alpha=0.05, method="fdr_bh")
    reference_rejected = np.asarray(reference[0], dtype=np.bool_)
    reference_adjusted = np.asarray(reference[1], dtype=np.float64)

    result = benjamini_hochberg(BH_1995_P_VALUES, q=0.05)
    assert result.rejected.tolist() == reference_rejected.tolist()
    assert result.adjusted == pytest.approx(reference_adjusted, abs=1e-12)


def test_monotone_enforcement_pulls_one_value_below_its_own_ratio() -> None:
    """The sixth p value, 0.0278, adjusts to 0.06386 and not to its own 15/6 ratio.

    Without the cumulative minimum it would adjust to 0.0695, above the
    seventh, and the table would sort into an order a reader cannot make sense
    of.
    """
    result = benjamini_hochberg(BH_1995_P_VALUES, q=0.05)
    assert result.adjusted[5] == pytest.approx(0.0638571, abs=1e-7)
    assert result.adjusted[5] < 0.0278 * 15.0 / 6.0


def test_adjusted_values_are_monotone_in_the_raw_p_value() -> None:
    rng = np.random.default_rng(20240917)
    raw = rng.uniform(0.0, 1.0, size=500)
    result = benjamini_hochberg(raw, q=0.10)
    ordered = result.adjusted[np.argsort(raw, kind="stable")]
    assert np.all(np.diff(ordered) >= -1e-15)
    assert np.all(ordered <= 1.0)


def test_nothing_is_rejected_when_every_p_value_is_near_one() -> None:
    p_values = np.linspace(0.9, 1.0, 15)
    result = benjamini_hochberg(p_values, q=0.05)
    assert result.n_rejected == 0
    assert not result.rejected.any()
    assert result.critical_p is None


def test_bh_rejects_at_least_as_many_as_bonferroni() -> None:
    q = 0.05
    result = benjamini_hochberg(BH_1995_P_VALUES, q=q)
    bonferroni_threshold = q / BH_1995_P_VALUES.size
    survives_bonferroni = bonferroni_threshold >= BH_1995_P_VALUES
    assert int(survives_bonferroni.sum()) == 3
    assert result.n_rejected >= int(survives_bonferroni.sum())
    assert np.all(result.rejected[survives_bonferroni])


def test_empty_and_all_nan_inputs_do_not_raise() -> None:
    empty = benjamini_hochberg(np.array([], dtype=np.float64))
    assert empty.n_comparisons == 0
    assert empty.n_rejected == 0
    assert empty.critical_p is None
    assert empty.adjusted.shape == (0,)

    missing = benjamini_hochberg(np.full(5, np.nan))
    assert missing.n_comparisons == 0
    assert missing.n_rejected == 0
    assert np.isnan(missing.adjusted).all()
    assert not missing.rejected.any()


def test_untested_pairs_are_excluded_from_the_comparison_count() -> None:
    """A NaN is a test that did not happen, so it must not inflate the denominator."""
    with_gaps = np.concatenate([BH_1995_P_VALUES, np.full(85, np.nan)])
    result = benjamini_hochberg(with_gaps, q=0.05)
    assert result.n_comparisons == 15
    assert result.n_rejected == 4


def test_rejects_an_impossible_q_or_p_value() -> None:
    with pytest.raises(ValueError, match="q must"):
        benjamini_hochberg(BH_1995_P_VALUES, q=0.0)
    with pytest.raises(ValueError, match="p values"):
        benjamini_hochberg(np.array([0.5, 1.5]))


def test_pairwise_z_test_matches_the_worked_example() -> None:
    """15 of 50 against 25 of 50: z = -2.0412, two sided p = 0.0412."""
    tests = pairwise_proportion_tests(np.array([15, 25]), np.array([50, 50]))
    assert tests.p_values.shape == (1,)
    assert float(tests.p_values[0]) == pytest.approx(0.0412, abs=5e-5)

    reference = proportions_ztest(np.array([15, 25]), np.array([50, 50]), alternative="two-sided")
    assert float(tests.p_values[0]) == pytest.approx(float(reference[1]), abs=1e-12)


def test_pairwise_covers_every_pair_in_upper_triangle_order() -> None:
    successes = np.array([1, 2, 3, 4])
    trials = np.array([10, 10, 10, 10])
    tests = pairwise_proportion_tests(successes, trials)
    assert tests.n_groups == 4
    assert tests.n_comparisons == 6
    assert tests.pairs.tolist() == [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]]


def test_pairwise_over_the_full_zone_set_is_the_number_the_interface_prints() -> None:
    """264 zones is 34,716 pairs, and the panel says so."""
    rng = np.random.default_rng(11)
    trials = rng.integers(200, 5000, size=264)
    successes = rng.binomial(trials, 0.3)
    tests = pairwise_proportion_tests(successes, trials)
    assert tests.n_comparisons == 264 * 263 // 2 == 34716
    assert tests.p_values.shape == (34716,)
    assert np.all(np.isfinite(tests.p_values))


def test_thin_groups_are_not_tested() -> None:
    tests = pairwise_proportion_tests(np.array([1, 30, 40]), np.array([2, 100, 100]), min_trials=25)
    assert tests.n_comparisons == 1
    assert np.isnan(tests.p_values[0])
    assert np.isnan(tests.p_values[1])
    assert np.isfinite(tests.p_values[2])


def test_two_all_zero_groups_give_no_evidence_of_a_difference() -> None:
    tests = pairwise_proportion_tests(np.array([0, 0]), np.array([40, 90]))
    assert float(tests.p_values[0]) == pytest.approx(1.0, abs=1e-12)
