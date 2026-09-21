"""Seasonal-trend decomposition by loess, wrapped so it cannot be misused.

Daily taxi volume is dominated by a weekly cycle. Anything the dashboard says
about a trend, a level shift or an anomaly has to be said about the series with
that cycle taken out, or it is a statement about what day of the week it is.

Two things about STL are worth enforcing rather than documenting and hoping.

The first is the index. STL works on position, not on time. Hand it a daily
series with three days missing and it will happily decompose the 362 rows it
was given, sliding the seasonal phase by three days at every gap and producing
a clean looking seasonal component that is wrong. Nothing in the output says
so. decompose therefore reindexes onto a complete range and reports how many
points it had to fill, so the caller can print that or refuse to plot it.

The second is robustness. A single holiday, a blizzard or a data outage is
enough to bend the loess trend around it for weeks. robust=True downweights
those points instead, which is why it is the default here and not in
statsmodels.

The two strength measures are from Wang, Smith and Hyndman (2006): the share of
variance the seasonal and trend components take out of what is left after the
other one is removed. They are the standard way to answer "is this series
actually seasonal", which is the question that decides whether a
seasonally adjusted number should be shown at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

__all__ = ["Decomposition", "decompose"]


@dataclass(frozen=True, slots=True)
class Decomposition:
    """An STL fit, with the components kept on the same index as the input."""

    observed: pd.Series
    trend: pd.Series
    seasonal: pd.Series
    resid: pd.Series
    period: int
    robust: bool
    strength_of_seasonality: float
    strength_of_trend: float
    gaps_filled: int


def _variance(values: pd.Series) -> float:
    sample = np.asarray(values, dtype=np.float64)
    if sample.size < 2:
        return 0.0
    return float(np.var(sample, ddof=1))


def _strength(component: pd.Series, resid: pd.Series) -> float:
    """One minus the share of the combined variance that is left in the residual.

    Clipped to [0, 1] because the ratio can exceed one when a component
    explains nothing, and a negative strength is not a quantity anyone has a
    use for.
    """
    residual_variance = _variance(resid)
    combined_variance = _variance(component + resid)
    if combined_variance <= 0.0:
        return 0.0
    return float(np.clip(1.0 - residual_variance / combined_variance, 0.0, 1.0))


def _regular_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The complete range the observed timestamps were drawn from.

    The step is the most common spacing rather than the smallest, so one short
    interval in a year of daily data does not turn the whole series into a
    minute by minute grid of almost entirely missing values.
    """
    spacings, counts = np.unique(np.diff(index.to_numpy()), return_counts=True)
    step = pd.Timedelta(spacings[int(np.argmax(counts))])
    if step <= pd.Timedelta(0):
        raise ValueError("the series index must be strictly increasing")

    regular = pd.date_range(start=index[0], end=index[-1], freq=step)
    off_grid = index.difference(regular)
    if len(off_grid) > 0:
        raise ValueError(
            f"the series index is irregular: {len(off_grid)} timestamps do not sit on "
            f"the inferred spacing of {step}. Resample before decomposing."
        )
    return regular


def decompose(series: pd.Series, period: int = 7, robust: bool = True) -> Decomposition:
    """Decompose a regularly spaced time series into trend, season and residual.

    Args:
        series: values indexed by a sorted DatetimeIndex. Gaps are filled by
            interpolation and counted, see the module docstring.
        period: observations per cycle, 7 for daily data with a weekly season.
        robust: downweight outlying points when fitting, on by default.

    Raises:
        ValueError: if the index is not a DatetimeIndex, is not sorted, carries
            duplicates, is irregular in a way interpolation cannot fix, or if
            the series is shorter than two full periods.
    """
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError(f"series must have a DatetimeIndex, got {type(series.index).__name__}")
    if period < 2:
        raise ValueError(f"period must be at least 2, got {period}")

    index: pd.DatetimeIndex = series.index
    if len(index) < 2:
        raise ValueError("series must have at least two observations")
    if index.has_duplicates:
        raise ValueError("the series index has duplicate timestamps")
    if not index.is_monotonic_increasing:
        raise ValueError("the series index must be sorted ascending")

    values = series.astype(np.float64)
    regular = _regular_index(index)
    # Points that STL would otherwise see as a phase shift: timestamps absent
    # from the input, plus timestamps present but null.
    gaps_filled = int(len(regular) - len(index) + int(values.isna().sum()))
    if gaps_filled:
        values = values.reindex(regular).interpolate(method="time", limit_direction="both")
        if bool(values.isna().any()):
            raise ValueError("the series is empty or entirely null after interpolation")

    if len(values) < 2 * period:
        raise ValueError(
            f"STL needs two full periods, got {len(values)} observations for period {period}"
        )

    fit: Any = STL(values, period=period, robust=robust).fit()
    trend = pd.Series(np.asarray(fit.trend, dtype=np.float64), index=values.index, name="trend")
    seasonal = pd.Series(
        np.asarray(fit.seasonal, dtype=np.float64), index=values.index, name="seasonal"
    )
    resid = pd.Series(np.asarray(fit.resid, dtype=np.float64), index=values.index, name="resid")

    return Decomposition(
        observed=values,
        trend=trend,
        seasonal=seasonal,
        resid=resid,
        period=period,
        robust=robust,
        strength_of_seasonality=_strength(seasonal, resid),
        strength_of_trend=_strength(trend, resid),
        gaps_filled=gaps_filled,
    )
