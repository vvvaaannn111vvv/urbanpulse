"""Feature builder: grid regularity, lag alignment, calendar flags, no leakage."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from services.common.features import (
    BUCKETS_PER_WEEK,
    FEATURE_COLUMNS,
    HORIZON_BUCKETS,
    build_features,
    easter_sunday,
    regular_grid,
    slovenian_holidays,
    training_rows,
)

START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def frame(n_buckets: int, stations: int = 2) -> pd.DataFrame:
    rows = []
    for s in range(stations):
        for i in range(n_buckets):
            rows.append(
                {
                    "bucket": START + timedelta(minutes=15 * i),
                    "station_id": f"s{s}",
                    "avg_bikes": float(i % 17),
                    "avg_docks": float(20 - i % 17),
                    "capacity": 20,
                    "temp_c": 18.0,
                    "precip_mm": 0.0,
                    "wind_kmh": 5.0,
                }
            )
    return pd.DataFrame(rows)


def test_easter_matches_known_dates():
    # Cross-checked against published ecclesiastical calendars.
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_slovenian_holidays_include_fixed_and_movable():
    hols = slovenian_holidays(2026)
    assert date(2026, 2, 8) in hols  # Prešeren Day
    assert date(2026, 6, 25) in hols  # Statehood Day
    assert date(2026, 4, 6) in hols  # Easter Monday
    assert date(2026, 5, 24) in hols  # Pentecost Sunday
    assert date(2026, 3, 15) not in hols


def test_regular_grid_fills_short_gaps_and_leaves_long_ones():
    df = frame(20, stations=1)
    df = df.drop(index=[5, 12, 13, 14, 15, 16])  # a 1-bucket gap and a 5-bucket gap
    filled = regular_grid(df, ffill_limit=4)
    assert len(filled) == 20, "grid is restored to one row per bucket"
    assert filled.loc[5, "avg_bikes"] == filled.loc[4, "avg_bikes"], "short gap forward-filled"
    assert pd.isna(filled.loc[16, "avg_bikes"]), "gap beyond the limit stays missing"


def test_lags_align_with_the_source_series():
    feat = build_features(frame(BUCKETS_PER_WEEK + 10), with_targets=True)
    one = feat[feat["station_id"] == "s0"].reset_index(drop=True)
    i = BUCKETS_PER_WEEK + 5
    assert one.loc[i, "lag_1"] == one.loc[i - 1, "bikes"]
    assert one.loc[i, "lag_4"] == one.loc[i - 4, "bikes"]
    assert one.loc[i, "lag_672"] == one.loc[i - BUCKETS_PER_WEEK, "bikes"]
    assert one.loc[i, "diff_1"] == one.loc[i, "bikes"] - one.loc[i - 1, "bikes"]


def test_targets_and_seasonal_naive_point_at_the_same_instant():
    feat = build_features(frame(BUCKETS_PER_WEEK + 20), with_targets=True)
    one = feat[feat["station_id"] == "s0"].reset_index(drop=True)
    i = BUCKETS_PER_WEEK + 5
    for horizon, steps in HORIZON_BUCKETS.items():
        assert one.loc[i, f"y_{horizon}"] == one.loc[i + steps, "bikes"]
        # naive = the value at the target time, exactly one week earlier
        assert one.loc[i, f"naive_{horizon}"] == one.loc[i + steps - BUCKETS_PER_WEEK, "bikes"]


def test_no_feature_column_uses_future_information():
    """Perturbing only future buckets must leave every feature row untouched."""
    base = frame(BUCKETS_PER_WEEK + 40, stations=1)
    cut = BUCKETS_PER_WEEK + 20
    tampered = base.copy()
    tampered.loc[cut:, "avg_bikes"] = 999.0

    a = build_features(base, with_targets=False).iloc[:cut]
    b = build_features(tampered, with_targets=False).iloc[:cut]
    for col in FEATURE_COLUMNS:
        np.testing.assert_allclose(
            a[col].astype(float).fillna(-1), b[col].astype(float).fillna(-1), err_msg=col
        )


def test_calendar_and_weather_features_present():
    feat = build_features(frame(200), with_targets=False)
    assert set(FEATURE_COLUMNS).issubset(feat.columns)
    assert feat["hour"].between(0, 23).all()
    assert feat["dow"].between(0, 6).all()
    assert feat["is_weekend"].isin([0, 1]).all()
    assert feat["is_wet"].isin([0, 1]).all()
    assert feat["occupancy"].between(0.0, 1.0).all()
    assert pytest.approx(feat["tod_sin"] ** 2 + feat["tod_cos"] ** 2, abs=1e-9) == 1.0


def test_training_rows_drop_incomplete_history():
    feat = build_features(frame(BUCKETS_PER_WEEK + 30), with_targets=True)
    rows = training_rows(feat, 15)
    assert not rows.empty
    assert rows[[*FEATURE_COLUMNS, "y_15", "naive_15"]].notna().all().all()
    assert len(rows) < len(feat), "the weekly-lag warm-up must be excluded"


def test_empty_frame_is_handled():
    empty = pd.DataFrame(
        columns=[
            "bucket",
            "station_id",
            "avg_bikes",
            "avg_docks",
            "capacity",
            "temp_c",
            "precip_mm",
            "wind_kmh",
        ]
    )
    out = build_features(empty, with_targets=True)
    assert out.empty
