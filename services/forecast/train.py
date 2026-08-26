"""Train the LightGBM availability forecaster and evaluate it honestly.

Evaluation is rolling-origin cross-validation: the training window expands, the
test window slides forward, and a gap equal to the forecast horizon sits between
them so no training target overlaps a test period. Each fold is scored against a
seasonal-naive baseline on exactly the same rows.

    python -m services.forecast.train --folds 4
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from services.common.config import Settings, get_settings
from services.common.features import (
    BUCKET_MIN,
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    build_features,
    training_rows,
)
from services.common.models import HORIZONS_MIN
from services.common.storage import make_store
from services.forecast.baseline import mae, rmse, skill

log = logging.getLogger("urbanpulse.train")

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results"

LGB_PARAMS: dict[str, object] = {
    "objective": "regression",
    "metric": "l1",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.85,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "num_threads": 0,
    "verbose": -1,
    "seed": 42,
}
NUM_ROUNDS = 400
"""Fixed boosting rounds: early stopping on the test fold would leak into the score."""


@dataclass
class FoldResult:
    fold: int
    train_rows: int
    test_rows: int
    test_start: str
    test_end: str
    model_mae: float
    model_rmse: float
    baseline_mae: float
    baseline_rmse: float


@dataclass
class HorizonResult:
    horizon_min: int
    folds: list[FoldResult] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        m_mae = float(np.mean([f.model_mae for f in self.folds]))
        m_rmse = float(np.mean([f.model_rmse for f in self.folds]))
        b_mae = float(np.mean([f.baseline_mae for f in self.folds]))
        b_rmse = float(np.mean([f.baseline_rmse for f in self.folds]))
        return {
            "horizon_min": self.horizon_min,
            "folds": len(self.folds),
            "model_mae": round(m_mae, 4),
            "model_rmse": round(m_rmse, 4),
            "baseline_mae": round(b_mae, 4),
            "baseline_rmse": round(b_rmse, 4),
            "mae_skill_vs_baseline": round(skill(m_mae, b_mae), 4),
            "rmse_skill_vs_baseline": round(skill(m_rmse, b_rmse), 4),
            "test_rows": int(sum(f.test_rows for f in self.folds)),
        }


def rolling_origin_splits(
    buckets: pd.Series, n_folds: int, horizon_min: int
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Return (train_end, test_start, test_end) per fold.

    The span of available buckets is cut into ``n_folds + 1`` equal blocks. Fold i
    trains on everything up to ``train_end`` and tests on block i+1. ``train_end``
    is pulled back by the horizon so a training row's target never lands inside
    the test window.
    """
    lo, hi = buckets.min(), buckets.max()
    block = (hi - lo) / (n_folds + 1)
    gap = pd.Timedelta(minutes=horizon_min + BUCKET_MIN)
    splits = []
    for i in range(n_folds):
        test_start = lo + block * (i + 1)
        test_end = lo + block * (i + 2)
        splits.append((test_start - gap, test_start, test_end))
    return splits


def fit_model(train: pd.DataFrame, horizon: int, num_rounds: int = NUM_ROUNDS) -> lgb.Booster:
    dataset = lgb.Dataset(
        train[FEATURE_COLUMNS],
        label=train[f"y_{horizon}"],
        categorical_feature=CATEGORICAL_COLUMNS,
        free_raw_data=True,
    )
    return lgb.train(LGB_PARAMS, dataset, num_boost_round=num_rounds)


def cross_validate(feat: pd.DataFrame, horizon: int, n_folds: int) -> HorizonResult:
    rows = training_rows(feat, horizon)
    result = HorizonResult(horizon_min=horizon)
    if rows.empty:
        log.warning("horizon %d: no usable rows", horizon)
        return result

    for i, (train_end, test_start, test_end) in enumerate(
        rolling_origin_splits(rows["bucket"], n_folds, horizon), start=1
    ):
        train = rows[rows["bucket"] <= train_end]
        test = rows[(rows["bucket"] > test_start) & (rows["bucket"] <= test_end)]
        if train.empty or test.empty:
            log.warning("horizon %d fold %d: empty split, skipping", horizon, i)
            continue

        booster = fit_model(train, horizon)
        pred = booster.predict(test[FEATURE_COLUMNS])
        truth = test[f"y_{horizon}"].to_numpy()
        naive = test[f"naive_{horizon}"].to_numpy()

        fold = FoldResult(
            fold=i,
            train_rows=len(train),
            test_rows=len(test),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            model_mae=round(mae(truth, pred), 4),
            model_rmse=round(rmse(truth, pred), 4),
            baseline_mae=round(mae(truth, naive), 4),
            baseline_rmse=round(rmse(truth, naive), 4),
        )
        result.folds.append(fold)
        log.info(
            "h=%2dmin fold %d  train=%6d test=%6d  MAE %.3f (naive %.3f)  RMSE %.3f (naive %.3f)",
            horizon,
            i,
            fold.train_rows,
            fold.test_rows,
            fold.model_mae,
            fold.baseline_mae,
            fold.model_rmse,
            fold.baseline_rmse,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=NUM_ROUNDS)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    settings: Settings = get_settings()
    store = make_store(settings)

    started = time.perf_counter()
    raw = store.training_frame()
    if raw.empty:
        log.error("no observations found — run `make seed` first")
        return 1
    log.info("loaded %d aggregate buckets for %d stations", len(raw), raw["station_id"].nunique())

    feat = build_features(raw, with_targets=True)
    log.info("built %d feature rows x %d features", len(feat), len(FEATURE_COLUMNS))

    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summaries = []
    fold_rows = []
    for horizon in HORIZONS_MIN:
        res = cross_validate(feat, horizon, args.folds)
        if not res.folds:
            continue
        summaries.append(res.summary())
        for f in res.folds:
            fold_rows.append({"horizon_min": horizon, **vars(f)})

        # Final model: refit on every usable row and persist for serving.
        rows = training_rows(feat, horizon)
        booster = fit_model(rows, horizon, args.rounds)
        out = model_dir / f"lgbm_h{horizon}.txt"
        booster.save_model(str(out))
        log.info("saved %s (%d training rows)", out, len(rows))

    elapsed = time.perf_counter() - started
    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "backend": settings.backend,
        "n_folds": args.folds,
        "boosting_rounds": args.rounds,
        "bucket_minutes": BUCKET_MIN,
        "n_features": len(FEATURE_COLUMNS),
        "n_stations": int(raw["station_id"].nunique()),
        "aggregate_buckets": int(len(raw)),
        "observation_count": store.observation_count(),
        "train_seconds": round(elapsed, 2),
        "cv_scheme": "rolling-origin, expanding train window, horizon-sized gap",
        "baseline": "seasonal naive (same time of day, one week earlier)",
        "horizons": summaries,
    }
    (RESULTS_DIR / "cv_metrics.json").write_text(json.dumps(payload, indent=2) + "\n")
    pd.DataFrame(fold_rows).to_csv(RESULTS_DIR / "cv_folds.csv", index=False)

    print("\nhorizon  model_MAE  naive_MAE  MAE_skill  model_RMSE  naive_RMSE")
    for s in summaries:
        print(
            f"{s['horizon_min']:>5}m  {s['model_mae']:>9.3f}  {s['baseline_mae']:>9.3f}"
            f"  {s['mae_skill_vs_baseline']:>+8.1%}  {s['model_rmse']:>10.3f}"
            f"  {s['baseline_rmse']:>10.3f}"
        )
    log.info("wrote results/cv_metrics.json and results/cv_folds.csv in %.1fs", elapsed)
    store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
