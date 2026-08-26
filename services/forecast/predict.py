"""Serving path: turn a station's recent history into +15/+30/+60 min forecasts."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from services.common.features import FEATURE_COLUMNS, build_features
from services.common.models import BUCKET_MIN, HORIZONS_MIN, Prediction, PredictResponse, utcnow
from services.common.storage.base import Store

log = logging.getLogger("urbanpulse.predict")

#: Enough history to fill the 7-day lag plus a margin for gaps.
LOOKBACK_DAYS = 8


class ModelNotTrained(RuntimeError):
    """Raised when no booster has been persisted for a horizon."""


class Forecaster:
    """Loads one LightGBM booster per horizon and serves point forecasts.

    When no model file exists the forecaster degrades to persistence (the current
    reading carried forward) and says so in the response's ``model`` field, so an
    un-trained deployment is obvious rather than silently wrong.
    """

    def __init__(self, model_dir: str | Path, store: Store) -> None:
        self.model_dir = Path(model_dir)
        self.store = store
        self._boosters: dict[int, lgb.Booster] = {}
        self._load()

    def _load(self) -> None:
        for horizon in HORIZONS_MIN:
            path = self.model_dir / f"lgbm_h{horizon}.txt"
            if not path.exists():
                log.warning("no model at %s; horizon %d will use persistence", path, horizon)
                continue
            self._boosters[horizon] = lgb.Booster(model_file=str(path))
        if self._boosters:
            log.info("loaded %d boosters from %s", len(self._boosters), self.model_dir)

    @property
    def ready(self) -> bool:
        return len(self._boosters) == len(HORIZONS_MIN)

    @property
    def model_name(self) -> str:
        if self.ready:
            return "lightgbm"
        if self._boosters:
            return "lightgbm+persistence"
        return "persistence"

    def reload(self) -> None:
        self._boosters.clear()
        self._load()

    # ----------------------------------------------------------------- predict
    def latest_features(self, station_id: str) -> pd.DataFrame | None:
        """Most recent fully-populated feature row for one station, or None."""
        since = utcnow() - timedelta(days=LOOKBACK_DAYS)
        raw = self.store.training_frame(station_id=station_id, since=since)
        if raw.empty:
            return None
        feat = build_features(raw, with_targets=False)
        usable = feat.dropna(subset=FEATURE_COLUMNS)
        if usable.empty:
            # Not enough history for the weekly lag — fall back to the newest row
            # we do have, so the /predict endpoint still returns the current state.
            tail = feat.dropna(subset=["bikes"])
            return tail.tail(1) if not tail.empty else None
        return usable.tail(1)

    def predict(self, station_id: str) -> PredictResponse:
        row = self.latest_features(station_id)
        if row is None:
            raise KeyError(f"no history for station {station_id!r}")

        as_of = row["bucket"].iloc[0].to_pydatetime()
        current = float(row["bikes"].iloc[0])
        capacity = int(row["capacity"].iloc[0])
        complete = bool(row[FEATURE_COLUMNS].notna().all(axis=1).iloc[0])

        predictions: list[Prediction] = []
        used_model = False
        for horizon in HORIZONS_MIN:
            booster = self._boosters.get(horizon)
            if booster is not None and complete:
                value = float(booster.predict(row[FEATURE_COLUMNS])[0])
                used_model = True
            else:
                value = current
            if capacity > 0:
                value = min(max(value, 0.0), float(capacity))
            predictions.append(
                Prediction(
                    horizon_min=horizon,
                    predicted_bikes=round(value, 2),
                    target_ts=as_of + timedelta(minutes=horizon),
                )
            )

        name = self.model_name if used_model else "persistence"
        return PredictResponse(
            station_id=station_id,
            as_of=as_of,
            current_bikes=int(round(current)),
            capacity=capacity,
            model=name,
            predictions=predictions,
        )


def bucket_floor(ts, minutes: int = BUCKET_MIN):
    """Round a timestamp down to the aggregate bucket boundary."""
    return ts - timedelta(
        minutes=ts.minute % minutes, seconds=ts.second, microseconds=ts.microsecond
    )
