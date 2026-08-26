"""Seasonal-naive baseline and the error metrics used to compare against it.

The baseline predicts the value observed at the same time of day one week
earlier — the standard reference for strongly weekly-seasonal urban demand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike

from services.common.features import BUCKETS_PER_WEEK, HORIZON_BUCKETS


def seasonal_naive(series: pd.Series, horizon_min: int) -> pd.Series:
    """Value at (t + horizon - 1 week), aligned to predict t + horizon."""
    steps = HORIZON_BUCKETS[horizon_min]
    return series.shift(BUCKETS_PER_WEEK - steps)


def mae(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def rmse(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    diff = np.asarray(y_true) - np.asarray(y_pred)
    return float(np.sqrt(np.mean(diff**2)))


def skill(model_error: float, baseline_error: float) -> float:
    """Fractional error reduction versus the baseline; >0 means the model wins."""
    if baseline_error == 0:
        return 0.0
    return float(1.0 - model_error / baseline_error)
