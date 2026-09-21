"""PELT checked against planted changepoints and against unpruned optimal partitioning.

The second of those is the test that carries the weight. Recovering planted
shifts shows the detector works; agreeing exactly with a brute force search
over every candidate start shows that the pruning rule, which is the only
subtle part of PELT, throws away nothing that could have won.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from cityflow_stats import L2Cost, bic_penalty, estimate_sigma, pelt

PLANTED = (100, 250, 400)
LEVELS = (10.0, 14.0, 10.0, 17.0)
NOISE = 1.0


def piecewise_signal(seed: int = 3, noise: float = NOISE) -> npt.NDArray[np.float64]:
    rng = np.random.default_rng(seed)
    boundaries = (0, *PLANTED, 500)
    pieces = [
        np.full(boundaries[i + 1] - boundaries[i], LEVELS[i], dtype=np.float64)
        for i in range(len(LEVELS))
    ]
    return np.concatenate(pieces) + rng.normal(0.0, noise, 500)


def optimal_partition(signal: npt.NDArray[np.float64], penalty: float, min_size: int) -> list[int]:
    """Optimal partitioning with no pruning at all: every start, every end, O(n squared).

    This is Jackson et al. (2005), the algorithm PELT accelerates. It is the
    reference answer by construction, since it evaluates the candidates PELT
    discards.
    """
    cost = L2Cost(signal)
    n = int(signal.size)
    best = np.full(n + 1, np.inf, dtype=np.float64)
    best[0] = -penalty
    last_change = np.zeros(n + 1, dtype=np.int64)
    for end in range(min_size, n + 1):
        for start in range(0, end - min_size + 1):
            if not np.isfinite(best[start]):
                continue
            value = best[start] + cost(start, end) + penalty
            if value < best[end]:
                best[end] = value
                last_change[end] = start
    changepoints: list[int] = []
    cursor = n
    while cursor > 0:
        start = int(last_change[cursor])
        if start > 0:
            changepoints.append(start)
        cursor = start
    return sorted(changepoints)


def test_l2_cost_is_the_sum_of_squared_deviations() -> None:
    """[1, 2, 3, 4, 5] has mean 3 and squared deviations summing to 10."""
    cost = L2Cost(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert cost(0, 5) == pytest.approx(10.0, abs=1e-12)
    assert cost(1, 4) == pytest.approx(2.0, abs=1e-12)
    assert cost(2, 3) == pytest.approx(0.0, abs=1e-12)
    assert cost(3, 3) == pytest.approx(0.0, abs=1e-12)


def test_l2_cost_of_a_constant_segment_is_zero_not_negative() -> None:
    """Prefix sums far from zero lose their low bits, so this has to be clamped."""
    cost = L2Cost(np.full(200, 1.0e6, dtype=np.float64))
    assert cost(0, 200) == pytest.approx(0.0, abs=1e-12)
    assert cost(0, 200) >= 0.0


def test_finds_the_planted_changepoints() -> None:
    signal = piecewise_signal()
    penalty = bic_penalty(signal.size, estimate_sigma(signal), n_params=2)
    found = pelt(signal, penalty, min_size=10)
    assert len(found) == len(PLANTED)
    for detected, planted in zip(found, PLANTED, strict=True):
        assert abs(detected - planted) <= 2


def test_a_constant_signal_has_no_changepoints() -> None:
    assert pelt(np.full(300, 4.2, dtype=np.float64), penalty=1.0, min_size=7) == []


def test_pure_noise_with_a_bic_penalty_is_not_carved_up() -> None:
    rng = np.random.default_rng(99)
    signal = rng.normal(50.0, 2.0, 400)
    penalty = bic_penalty(signal.size, estimate_sigma(signal), n_params=2)
    assert pelt(signal, penalty, min_size=10) == []


def test_a_single_large_shift_is_found_with_a_bic_penalty() -> None:
    """A 12 unit shift in unit noise, against the 2 log n penalty Killick et al use."""
    rng = np.random.default_rng(17)
    signal = np.concatenate([rng.normal(20.0, 1.0, 150), rng.normal(32.0, 1.0, 150)])
    penalty = bic_penalty(signal.size, estimate_sigma(signal), n_params=2)
    found = pelt(signal, penalty, min_size=10)
    assert len(found) == 1
    assert abs(found[0] - 150) <= 2


def test_a_signal_shorter_than_two_segments_has_no_changepoints() -> None:
    rng = np.random.default_rng(5)
    assert pelt(rng.normal(0.0, 1.0, 13), penalty=1.0, min_size=7) == []


def test_rejects_nan_and_nonsense_arguments() -> None:
    signal = piecewise_signal()
    with_nan = signal.copy()
    with_nan[42] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        pelt(with_nan, penalty=10.0)
    with pytest.raises(ValueError, match="min_size"):
        pelt(signal, penalty=10.0, min_size=0)
    with pytest.raises(ValueError, match="jump"):
        pelt(signal, penalty=10.0, jump=0)
    with pytest.raises(ValueError, match="penalty"):
        pelt(signal, penalty=-1.0)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("penalty", [2.0, 8.0, 40.0])
def test_pruning_does_not_change_the_segmentation(seed: int, penalty: float) -> None:
    """The test that actually proves the implementation, over a range of penalties.

    A low penalty leaves a large candidate set, a high one prunes almost
    everything. Both have to agree with the unpruned search.
    """
    rng = np.random.default_rng(seed)
    signal = np.concatenate(
        [
            rng.normal(0.0, 1.0, 20),
            rng.normal(3.0, 1.0, 25),
            rng.normal(-1.0, 1.0, 15),
        ]
    )
    assert pelt(signal, penalty, min_size=5) == optimal_partition(signal, penalty, min_size=5)


def test_pruning_agrees_on_the_long_planted_signal_too() -> None:
    signal = piecewise_signal()
    penalty = bic_penalty(signal.size, estimate_sigma(signal), n_params=2)
    assert pelt(signal, penalty, min_size=10) == optimal_partition(signal, penalty, min_size=10)


def test_jump_restricts_candidates_without_losing_the_shifts() -> None:
    signal = piecewise_signal()
    penalty = bic_penalty(signal.size, estimate_sigma(signal), n_params=2)
    found = pelt(signal, penalty, min_size=10, jump=5)
    assert all(point % 5 == 0 for point in found)
    assert len(found) == len(PLANTED)
    for detected, planted in zip(found, PLANTED, strict=True):
        assert abs(detected - planted) <= 5


def test_estimate_sigma_recovers_the_noise_scale_despite_the_shifts() -> None:
    """The claim the docstring makes: the plain standard deviation is useless here."""
    rng = np.random.default_rng(23)
    signal = np.concatenate([rng.normal(0.0, 1.0, 1000), rng.normal(20.0, 1.0, 1000)])
    assert estimate_sigma(signal) == pytest.approx(1.0, rel=0.10)
    assert float(signal.std(ddof=1)) > 9.0


def test_estimate_sigma_of_a_straight_line_is_zero() -> None:
    """Every first difference is identical, so the median absolute deviation is zero."""
    assert estimate_sigma(np.arange(100, dtype=np.float64) * 3.0) == pytest.approx(0.0, abs=1e-12)


def test_bic_penalty_is_the_schwarz_formula() -> None:
    assert bic_penalty(100, 2.0) == pytest.approx(4.0 * np.log(100.0), abs=1e-12)
    assert bic_penalty(100, 2.0, n_params=2) == pytest.approx(8.0 * np.log(100.0), abs=1e-12)
    with pytest.raises(ValueError, match="sigma"):
        bic_penalty(100, -1.0)
    with pytest.raises(ValueError, match="n must"):
        bic_penalty(1, 1.0)
