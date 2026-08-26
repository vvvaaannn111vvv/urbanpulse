"""Feature engineering shared by training (``services.forecast.train``) and
serving (``services.forecast.predict``).

The unit of work is the 15-minute continuous-aggregate bucket. One row per
(station, bucket); a row's features use only information available at that
bucket, so nothing leaks from the future.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from services.common.models import BUCKET_MIN, HORIZONS_MIN

BUCKETS_PER_HOUR = 60 // BUCKET_MIN  # 4
BUCKETS_PER_DAY = 24 * BUCKETS_PER_HOUR  # 96
BUCKETS_PER_WEEK = 7 * BUCKETS_PER_DAY  # 672

#: Lags in buckets: 15m, 30m, 1h, 2h, 3h, 1d, 1w.
LAG_BUCKETS: tuple[int, ...] = (1, 2, 4, 8, 12, BUCKETS_PER_DAY, BUCKETS_PER_WEEK)
#: Rolling windows in buckets: 1h and 4h.
ROLL_WINDOWS: tuple[int, ...] = (4, 16)

#: How many buckets ahead each forecast horizon is.
HORIZON_BUCKETS: dict[int, int] = {h: h // BUCKET_MIN for h in HORIZONS_MIN}

FEATURE_COLUMNS: list[str] = (
    ["bikes"]
    + [f"lag_{k}" for k in LAG_BUCKETS]
    + [f"diff_{k}" for k in (1, 2, 4)]
    + [f"roll_mean_{w}" for w in ROLL_WINDOWS]
    + [f"roll_std_{w}" for w in ROLL_WINDOWS]
    + [
        "capacity",
        "occupancy",
        "docks",
        "temp_c",
        "precip_mm",
        "wind_kmh",
        "is_wet",
        "hour",
        "dow",
        "is_weekend",
        "is_holiday",
        "tod_sin",
        "tod_cos",
        "dow_sin",
        "dow_cos",
    ]
)

CATEGORICAL_COLUMNS: list[str] = ["hour", "dow"]


# --------------------------------------------------------------------- holidays
def easter_sunday(year: int) -> date:
    """Anonymous Gregorian computus (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lmb = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lmb) // 451
    month, day = divmod(h + lmb - 7 * m + 114, 31)
    return date(year, month, day + 1)


def slovenian_holidays(year: int) -> set[date]:
    """Slovenian work-free days (Zakon o praznikih in dela prostih dnevih)."""
    easter = easter_sunday(year)
    fixed = [
        (1, 1),
        (1, 2),
        (2, 8),
        (4, 27),
        (5, 1),
        (5, 2),
        (6, 25),
        (8, 15),
        (10, 31),
        (11, 1),
        (12, 25),
        (12, 26),
    ]
    days = {date(year, m, d) for m, d in fixed}
    days.add(easter)
    days.add(easter + timedelta(days=1))  # Easter Monday
    days.add(easter + timedelta(days=49))  # Pentecost Sunday
    return days


def holiday_set(years: list[int]) -> set[date]:
    out: set[date] = set()
    for y in years:
        out |= slovenian_holidays(y)
    return out


# ------------------------------------------------------------------- reindexing
def regular_grid(df: pd.DataFrame, ffill_limit: int = 4) -> pd.DataFrame:
    """Reindex every station onto a gap-free 15-minute grid.

    Lag features are positional, so a missing bucket would silently shift them.
    Short gaps (<= ``ffill_limit`` buckets, i.e. 1 hour) are forward-filled;
    longer gaps stay NaN and are dropped downstream.
    """
    if df.empty:
        return df
    df = df.sort_values(["station_id", "bucket"])
    grid = pd.date_range(df["bucket"].min(), df["bucket"].max(), freq=f"{BUCKET_MIN}min", tz="UTC")
    stations = df["station_id"].unique()
    full = pd.MultiIndex.from_product([stations, grid], names=["station_id", "bucket"])
    out = (
        df.set_index(["station_id", "bucket"])
        .reindex(full)
        .groupby(level="station_id")
        .ffill(limit=ffill_limit)
    )
    # capacity is static metadata: fill it across the whole station series.
    out["capacity"] = out.groupby(level="station_id")["capacity"].transform(
        lambda s: s.ffill().bfill()
    )
    return out.reset_index()


# ---------------------------------------------------------------------- builder
def build_features(df: pd.DataFrame, with_targets: bool = True) -> pd.DataFrame:
    """Turn raw 15-minute buckets into the model matrix.

    Input columns: bucket, station_id, avg_bikes, avg_docks, capacity,
    temp_c, precip_mm, wind_kmh.

    Output adds ``FEATURE_COLUMNS`` and, when ``with_targets``, one
    ``y_{h}`` target and one ``naive_{h}`` seasonal-naive prediction per horizon.
    """
    if df.empty:
        return df.assign(**{c: pd.Series(dtype="float64") for c in FEATURE_COLUMNS})

    out = regular_grid(df).copy()
    out["bikes"] = out["avg_bikes"].astype("float64")
    out["docks"] = out["avg_docks"].astype("float64")
    grp = out.groupby("station_id", sort=False)["bikes"]

    for k in LAG_BUCKETS:
        out[f"lag_{k}"] = grp.shift(k)
    for k in (1, 2, 4):
        out[f"diff_{k}"] = out["bikes"] - out[f"lag_{k}"]
    for w in ROLL_WINDOWS:
        rolled = grp.rolling(window=w, min_periods=2)
        out[f"roll_mean_{w}"] = rolled.mean().reset_index(level=0, drop=True)
        out[f"roll_std_{w}"] = rolled.std().reset_index(level=0, drop=True)

    cap = out["capacity"].astype("float64").replace(0.0, np.nan)
    out["capacity"] = cap.fillna(0.0)
    out["occupancy"] = (out["bikes"] / cap).clip(0.0, 1.0).fillna(0.0)

    # Weather is hourly; carry each reading across its four buckets.
    for col in ("temp_c", "precip_mm", "wind_kmh"):
        out[col] = out.groupby("station_id", sort=False)[col].ffill().bfill()
        out[col] = out[col].fillna(out[col].median() if out[col].notna().any() else 0.0)
    out["is_wet"] = (out["precip_mm"] > 0.1).astype("int8")

    ts = out["bucket"].dt
    out["hour"] = ts.hour.astype("int16")
    out["dow"] = ts.dayofweek.astype("int16")
    out["is_weekend"] = (out["dow"] >= 5).astype("int8")
    years = sorted({int(y) for y in ts.year.unique()})
    hols = holiday_set(years)
    out["is_holiday"] = out["bucket"].dt.date.map(lambda d: int(d in hols)).astype("int8")

    tod = ts.hour * 60 + ts.minute
    out["tod_sin"] = np.sin(2 * np.pi * tod / 1440.0)
    out["tod_cos"] = np.cos(2 * np.pi * tod / 1440.0)
    out["dow_sin"] = np.sin(2 * np.pi * out["dow"] / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * out["dow"] / 7.0)

    if with_targets:
        by_station = out.groupby("station_id", sort=False)["bikes"]
        for horizon, steps in HORIZON_BUCKETS.items():
            out[f"y_{horizon}"] = by_station.shift(-steps)
            # Seasonal naive: the value observed at the target time one week back.
            out[f"naive_{horizon}"] = by_station.shift(BUCKETS_PER_WEEK - steps)

    return out


def training_rows(feat: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Rows usable for one horizon: features, target and baseline all present."""
    needed = [*FEATURE_COLUMNS, f"y_{horizon}", f"naive_{horizon}"]
    return feat.dropna(subset=needed)
