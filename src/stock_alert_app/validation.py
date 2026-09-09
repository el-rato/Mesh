from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from .db import Database, utc_now

STACKING_FEATURES = (
    "model_probability_up", "trend_score", "momentum_score", "volume_score",
    "volatility_score", "candlesticks_score", "market_regime_score",
    "relative_strength_score", "market_context_score",
)


def stacking_vector(model_probability_up: float, engine_scores: dict[str, float]) -> np.ndarray:
    values = [float(model_probability_up)] + [
        float(engine_scores.get(name.removesuffix("_score"), 0.0))
        for name in STACKING_FEATURES[1:]
    ]
    array = np.asarray(values, dtype=float)
    if not np.isfinite(array).all():
        raise ValueError("stacking inputs must be finite")
    return array


def confidence_bucket(probability_up: float) -> str:
    confidence = max(float(probability_up), 1.0 - float(probability_up))
    if confidence < 0.55:
        return "50-55"
    if confidence < 0.65:
        return "55-65"
    if confidence < 0.75:
        return "65-75"
    return "75-100"


class PredictionLedger:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        *,
        model: str,
        model_version: str,
        symbol: str,
        horizon: int,
        as_of: str,
        probability_up: float,
        regime: str = "UNKNOWN",
    ) -> int:
        probability = max(0.0, min(1.0, float(probability_up)))
        direction = "BULLISH" if probability >= 0.55 else "BEARISH" if probability <= 0.45 else "NEUTRAL"
        with self.database.connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO prediction_ledger
                   (model, model_version, symbol, horizon, as_of, probability_up,
                    direction, confidence_bucket, regime, predicted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (model, model_version, symbol, horizon, as_of, probability, direction,
                 confidence_bucket(probability), regime, utc_now()),
            )
            row = conn.execute(
                """SELECT id FROM prediction_ledger WHERE model=? AND model_version=?
                   AND symbol=? AND horizon=? AND as_of=?""",
                (model, model_version, symbol, horizon, as_of),
            ).fetchone()
        return int(row["id"])

    def settle(
        self,
        prediction_id: int,
        *,
        outcome_at: str,
        actual_direction: int,
        realized_return: float,
        mfe: float,
        mae: float,
    ) -> None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT as_of, outcome_at FROM prediction_ledger WHERE id=?", (prediction_id,)
            ).fetchone()
            if row is None:
                raise KeyError(prediction_id)
            if row["outcome_at"] is not None:
                raise ValueError("prediction outcome already recorded")
            if datetime.fromisoformat(outcome_at) <= datetime.fromisoformat(row["as_of"]):
                raise ValueError("outcome must occur after prediction as_of")
            conn.execute(
                """UPDATE prediction_ledger SET outcome_at=?, actual_direction=?,
                   realized_return=?, mfe=?, mae=? WHERE id=?""",
                (outcome_at, int(actual_direction), float(realized_return), float(mfe), float(mae), prediction_id),
            )

    def reliability(
        self, *, model: str, model_version: str, symbol: str, horizon: int,
        probability_up: float, regime: str, minimum_samples: int = 30,
    ) -> dict[str, Any]:
        bucket = confidence_bucket(probability_up)
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT probability_up, actual_direction, realized_return, mfe, mae,
                          outcome_at FROM prediction_ledger
                   WHERE model=? AND model_version=? AND symbol=? AND horizon=?
                     AND confidence_bucket=? AND regime=? AND outcome_at IS NOT NULL
                   ORDER BY outcome_at DESC LIMIT 250""",
                (model, model_version, symbol, horizon, bucket, regime),
            ).fetchall()]
        metrics = evaluation_metrics(rows)
        count = int(metrics["count"] or 0)
        return {
            "status": "INSUFFICIENT_DATA" if count < minimum_samples else "READY",
            "historical_hit_rate": metrics["hit_rate"] if count >= minimum_samples else None,
            "sample_count": count,
            "confidence_bucket": bucket,
            "regime": regime,
            "last_evaluated_at": rows[0]["outcome_at"] if rows else None,
        }

    def settle_due(self, symbol: str, history: pd.DataFrame) -> int:
        if history is None or history.empty or "Close" not in history:
            return 0
        index = pd.to_datetime(history.index, utc=True)
        close = history["Close"].to_numpy(dtype=float)
        if index.has_duplicates or not index.is_monotonic_increasing or not np.isfinite(close).all():
            raise ValueError("settlement history must be finite, unique and chronological")
        with self.database.connect() as conn:
            pending = [dict(row) for row in conn.execute(
                """SELECT id, as_of, horizon FROM prediction_ledger
                   WHERE symbol=? AND outcome_at IS NULL ORDER BY as_of""",
                (symbol.upper(),),
            ).fetchall()]
        settled = 0
        for row in pending:
            stamp = pd.Timestamp(row["as_of"])
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
            matches = np.flatnonzero(index == stamp)
            if len(matches) != 1:
                continue
            start = int(matches[0])
            end = start + int(row["horizon"])
            if end >= len(close) or index[end] <= stamp or close[start] <= 0.0:
                continue
            path = close[start + 1 : end + 1] / close[start] - 1.0
            realized = float(path[-1])
            try:
                self.settle(
                    int(row["id"]),
                    outcome_at=index[end].isoformat(),
                    actual_direction=int(realized > 0.0),
                    realized_return=realized,
                    mfe=float(np.max(path)),
                    mae=float(np.min(path)),
                )
                settled += 1
            except ValueError:
                continue
        return settled


def forward_outcomes(close: pd.Series, horizon: int) -> pd.DataFrame:
    """Targets use future bars; callers must never include these columns as features."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    future = close.shift(-horizon)
    returns = future / close - 1.0
    return pd.DataFrame({"target": (returns > 0).astype(float), "return": returns}, index=close.index).where(future.notna())


def chronological_windows(length: int, train_size: int, test_size: int, horizon: int = 1):
    if min(train_size, test_size, horizon) < 1:
        raise ValueError("window sizes and horizon must be positive")
    test_start = train_size + horizon
    while test_start < length:
        train_end = test_start - horizon
        yield slice(train_end - train_size, train_end), slice(test_start, min(length, test_start + test_size))
        test_start += test_size


def walk_forward_validate(
    features: pd.DataFrame,
    targets: pd.Series,
    *,
    model_factory: Callable[[], Any],
    train_size: int,
    test_size: int = 1,
    horizon: int = 1,
    preprocessor_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    if len(features) != len(targets):
        raise ValueError("features and targets must align")
    if features.index.has_duplicates or not features.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and chronological")
    if not features.index.equals(targets.index):
        raise ValueError("feature and target timestamps differ")
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise ValueError("features contain non-finite values")

    rows: list[dict[str, Any]] = []
    for train, test in chronological_windows(len(features), train_size, test_size, horizon):
        x_train = features.iloc[train].to_numpy(dtype=float)
        y_train = targets.iloc[train].to_numpy(dtype=float)
        valid_train = np.isfinite(y_train)
        x_train, y_train = x_train[valid_train], y_train[valid_train]
        if not len(y_train):
            continue
        x_test = features.iloc[test].to_numpy(dtype=float)
        if preprocessor_factory is not None:
            preprocessor = preprocessor_factory()
            x_train = preprocessor.fit_transform(x_train)
            x_test = preprocessor.transform(x_test)
        model = model_factory()
        model.fit(x_train, y_train)
        if hasattr(model, "predict_proba"):
            probability = np.asarray(model.predict_proba(x_test), dtype=float)[:, 1]
        else:
            probability = np.asarray(model.predict(x_test), dtype=float).reshape(-1)
        for offset, p in enumerate(probability):
            idx = test.start + offset
            actual = targets.iloc[idx]
            rows.append({
                "as_of": features.index[idx],
                "train_start": features.index[train.start],
                "train_end": features.index[train.stop - 1],
                "probability_up": max(0.0, min(1.0, float(p))),
                "actual_direction": int(actual) if np.isfinite(actual) else None,
            })
    return rows


def evaluation_metrics(records: Iterable[dict[str, Any]]) -> dict[str, float | int | None]:
    rows = [r for r in records if r.get("actual_direction") is not None]
    if not rows:
        return {k: None for k in ("mcc", "precision", "recall", "brier_score", "hit_rate", "returns", "sharpe", "max_drawdown", "mfe", "mae")} | {"count": 0}
    y = np.asarray([int(r["actual_direction"]) for r in rows])
    p = np.asarray([float(r["probability_up"]) for r in rows])
    pred = (p >= 0.5).astype(int)
    tp = int(np.sum((pred == 1) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    realized = np.asarray([float(r.get("realized_return") or 0.0) for r in rows])
    strategy = np.where(pred == 1, realized, -realized)
    equity = np.cumprod(1.0 + strategy)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity / peaks - 1.0
    std = float(strategy.std(ddof=0))
    return {
        "count": len(rows),
        "mcc": (tp * tn - fp * fn) / denom if denom else 0.0,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "brier_score": float(np.mean((p - y) ** 2)),
        "hit_rate": float(np.mean(pred == y)),
        "returns": float(equity[-1] - 1.0),
        "sharpe": float(strategy.mean() / std * math.sqrt(252)) if std else 0.0,
        "max_drawdown": float(drawdown.min()),
        "mfe": float(np.mean([float(r.get("mfe") or 0.0) for r in rows])),
        "mae": float(np.mean([float(r.get("mae") or 0.0) for r in rows])),
    }


def grouped_metrics(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("model", "horizon", "symbol", "confidence_bucket", "regime")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in records:
        bucket = row.get("confidence_bucket") or confidence_bucket(float(row["probability_up"]))
        key = tuple(bucket if name == "confidence_bucket" else row.get(name) for name in keys)
        groups.setdefault(key, []).append(row)
    return [{**dict(zip(keys, key)), **evaluation_metrics(group)} for key, group in groups.items()]


@dataclass
class ExperimentalLogisticStacker:
    learning_rate: float = 0.1
    iterations: int = 800
    l2: float = 0.01
    mean_: np.ndarray | None = None
    scale_: np.ndarray | None = None
    coefficients_: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> "ExperimentalLogisticStacker":
        values = np.asarray(x, dtype=float)
        target = np.asarray(y, dtype=float)
        self.mean_ = values.mean(axis=0)
        self.scale_ = values.std(axis=0)
        self.scale_[self.scale_ == 0.0] = 1.0
        z = (values - self.mean_) / self.scale_
        z = np.column_stack([np.ones(len(z)), z])
        weights = np.zeros(z.shape[1])
        for _ in range(self.iterations):
            probabilities = 1.0 / (1.0 + np.exp(-np.clip(z @ weights, -40.0, 40.0)))
            gradient = z.T @ (probabilities - target) / len(z)
            gradient[1:] += self.l2 * weights[1:]
            weights -= self.learning_rate * gradient
        self.coefficients_ = weights
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None or self.coefficients_ is None:
            raise ValueError("stacker has not been fitted")
        z = (np.asarray(x, dtype=float) - self.mean_) / self.scale_
        z = np.column_stack([np.ones(len(z)), z])
        p = 1.0 / (1.0 + np.exp(-np.clip(z @ self.coefficients_, -40.0, 40.0)))
        return np.column_stack([1.0 - p, p])


def materially_better(meta_records: Sequence[dict[str, Any]], fusion_records: Sequence[dict[str, Any]], minimum_samples: int = 50, brier_improvement: float = 0.02) -> bool:
    meta = evaluation_metrics(meta_records)
    fusion = evaluation_metrics(fusion_records)
    if int(meta["count"] or 0) < minimum_samples or int(fusion["count"] or 0) < minimum_samples:
        return False
    return bool(
        float(fusion["brier_score"]) - float(meta["brier_score"]) >= brier_improvement
        and float(meta["mcc"]) >= float(fusion["mcc"])
    )


def qualified_meta_probability(
    *, enabled: bool, stacker: ExperimentalLogisticStacker, features: np.ndarray,
    meta_records: Sequence[dict[str, Any]], fusion_records: Sequence[dict[str, Any]],
    minimum_samples: int = 50, brier_improvement: float = 0.02,
) -> float | None:
    if not enabled or not materially_better(meta_records, fusion_records, minimum_samples, brier_improvement):
        return None
    return float(stacker.predict_proba(np.asarray(features, dtype=float).reshape(1, -1))[0, 1])
