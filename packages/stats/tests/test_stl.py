"""STL checked by planting a trend and a season and asking for them back.

There is no published worked example for STL the way there is for Wilson or for
Benjamini-Hochberg, so the reference here is construction: a series built from
a known slope and a known weekly amplitude, where the right answer is the one
that was planted. The tolerances (5 percent on the slope, 10 percent on the
amplitude) are loose enough for loess end effects and tight enough that a phase
error or a period mix up fails.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cityflow_stats import decompose

PLANTED_SLOPE = 0.5
PLANTED_AMPLITUDE = 20.0
PERIOD = 7


def planted_series(n_days: int = 364, noise: float = 1.0, seed: int = 4) -> pd.Series:
    """A daily series with a known linear trend and a known weekly season."""
    rng = np.random.default_rng(seed)
    position = np.arange(n_days, dtype=np.float64)
    trend = 100.0 + PLANTED_SLOPE * position
    seasonal = PLANTED_AMPLITUDE * np.sin(2.0 * np.pi * position / PERIOD)
    index = pd.date_range("2023-01-02", periods=n_days, freq="D")
    return pd.Series(trend + seasonal + rng.normal(0.0, noise, n_days), index=index)


def discrete_amplitude(n_days: int = 364) -> float:
    """Half the peak to trough range of the planted season as it is actually sampled.

    A sine of period 7 sampled at whole days never lands on its own peak, so the
    sampled amplitude is 0.9749 of the nominal one. Comparing the recovered
    component to the nominal 20 would be comparing it to a value that is not in
    the data.
    """
    position = np.arange(n_days, dtype=np.float64)
    sampled = PLANTED_AMPLITUDE * np.sin(2.0 * np.pi * position / PERIOD)
    return float(sampled.max() - sampled.min()) / 2.0


def test_recovers_the_planted_trend_slope() -> None:
    result = decompose(planted_series(), period=PERIOD)
    position = np.arange(result.trend.size, dtype=np.float64)
    slope = float(np.polyfit(position, np.asarray(result.trend, dtype=np.float64), 1)[0])
    assert slope == pytest.approx(PLANTED_SLOPE, rel=0.05)


def test_recovers_the_planted_seasonal_amplitude() -> None:
    result = decompose(planted_series(), period=PERIOD)
    seasonal = np.asarray(result.seasonal, dtype=np.float64)
    recovered = float(seasonal.max() - seasonal.min()) / 2.0
    assert recovered == pytest.approx(discrete_amplitude(), rel=0.10)


def test_seasonal_phase_survives_the_decomposition() -> None:
    """The recovered season has to line up with the planted one, day for day."""
    series = planted_series()
    position = np.arange(series.size, dtype=np.float64)
    planted = PLANTED_AMPLITUDE * np.sin(2.0 * np.pi * position / PERIOD)
    recovered = np.asarray(decompose(series, period=PERIOD).seasonal, dtype=np.float64)
    correlation = float(np.corrcoef(planted, recovered)[0, 1])
    assert correlation > 0.99


def test_components_add_back_to_the_observed_series() -> None:
    result = decompose(planted_series(), period=PERIOD)
    reconstructed = result.trend + result.seasonal + result.resid
    assert np.asarray(reconstructed, dtype=np.float64) == pytest.approx(
        np.asarray(result.observed, dtype=np.float64), abs=1e-8
    )


def test_strength_of_seasonality_is_high_for_a_seasonal_series() -> None:
    result = decompose(planted_series(), period=PERIOD)
    assert result.strength_of_seasonality > 0.9
    assert result.strength_of_trend > 0.9
    assert 0.0 <= result.strength_of_seasonality <= 1.0


def test_strength_of_seasonality_is_low_for_white_noise() -> None:
    rng = np.random.default_rng(7)
    index = pd.date_range("2023-01-02", periods=364, freq="D")
    noise = pd.Series(rng.normal(0.0, 1.0, index.size), index=index)
    result = decompose(noise, period=PERIOD)
    assert result.strength_of_seasonality < 0.3
    assert result.strength_of_trend < 0.3


def test_gaps_are_filled_and_counted() -> None:
    series = planted_series()
    dropped = series.index[[10, 11, 200]]
    punctured = series.drop(index=dropped)
    result = decompose(punctured, period=PERIOD)
    assert result.gaps_filled == 3
    assert result.observed.size == series.size
    assert result.observed.index.equals(series.index)


def test_a_series_with_no_gaps_reports_none() -> None:
    result = decompose(planted_series(), period=PERIOD)
    assert result.gaps_filled == 0


def test_gap_filling_keeps_the_seasonal_phase() -> None:
    """The reason the gap check exists: without it the season slides by three days."""
    series = planted_series()
    punctured = series.drop(index=series.index[[10, 11, 200]])
    result = decompose(punctured, period=PERIOD)
    position = np.arange(result.observed.size, dtype=np.float64)
    planted = PLANTED_AMPLITUDE * np.sin(2.0 * np.pi * position / PERIOD)
    recovered = np.asarray(result.seasonal, dtype=np.float64)
    assert float(np.corrcoef(planted, recovered)[0, 1]) > 0.99


def test_null_values_count_as_gaps() -> None:
    series = planted_series()
    series.iloc[[3, 77]] = np.nan
    result = decompose(series, period=PERIOD)
    assert result.gaps_filled == 2
    assert not bool(result.observed.isna().any())


def test_rejects_a_non_datetime_index() -> None:
    series = pd.Series(np.arange(30, dtype=np.float64))
    with pytest.raises(ValueError, match="DatetimeIndex"):
        decompose(series, period=PERIOD)


def test_rejects_fewer_than_two_periods() -> None:
    index = pd.date_range("2023-01-02", periods=13, freq="D")
    series = pd.Series(np.arange(13, dtype=np.float64), index=index)
    with pytest.raises(ValueError, match="two full periods"):
        decompose(series, period=PERIOD)


def test_rejects_an_unsorted_or_duplicated_index() -> None:
    series = planted_series(n_days=30)
    with pytest.raises(ValueError, match="sorted"):
        decompose(series.iloc[::-1], period=PERIOD)
    repeated = pd.Series(
        np.concatenate([series.to_numpy(), series.to_numpy()[:1]]),
        index=series.index.append(series.index[:1]),
    )
    with pytest.raises(ValueError, match="duplicate"):
        decompose(repeated, period=PERIOD)
