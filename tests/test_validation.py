import numpy as np
import pandas as pd
import pytest

from stock_alert_app.db import Database
from stock_alert_app.validation import (
    ExperimentalLogisticStacker,
    PredictionLedger,
    chronological_windows,
    evaluation_metrics,
    forward_outcomes,
    grouped_metrics,
    materially_better,
    qualified_meta_probability,
    stacking_vector,
    walk_forward_validate,
)


class MeanModel:
    def fit(self, x, y):
        self.p = float(np.mean(y))
        return self

    def predict_proba(self, x):
        p = np.full(len(x), self.p)
        return np.column_stack([1 - p, p])


def test_walk_forward_purges_labels_and_fits_preprocessing_on_train_only():
    index = pd.date_range("2025-01-01", periods=16, tz="UTC")
    features = pd.DataFrame({"x": np.arange(16, dtype=float)}, index=index)
    targets = pd.Series((np.arange(16) % 2).astype(float), index=index)
    fits = []

    class SpyScaler:
        def fit_transform(self, x):
            fits.append(x.copy())
            return x

        def transform(self, x):
            return x

    rows = walk_forward_validate(
        features,
        targets,
        model_factory=MeanModel,
        preprocessor_factory=SpyScaler,
        train_size=5,
        test_size=2,
        horizon=2,
    )
    assert fits[0][:, 0].tolist() == [0, 1, 2, 3, 4]
    assert rows[0]["as_of"] == index[7]
    assert rows[0]["train_end"] == index[4]
    assert all(row["train_end"] < row["as_of"] for row in rows)


def test_timestamp_and_lookahead_guards():
    index = pd.date_range("2025-01-01", periods=8)
    frame = pd.DataFrame({"x": range(8)}, index=index[::-1])
    targets = pd.Series(range(8), index=index[::-1])
    with pytest.raises(ValueError, match="chronological"):
        walk_forward_validate(frame, targets, model_factory=MeanModel, train_size=3)
    windows = list(chronological_windows(12, 5, 2, horizon=3))
    assert windows[0][0].stop + 3 == windows[0][1].start


def test_forward_labels_leave_unobservable_tail_empty():
    close = pd.Series([10.0, 11.0, 9.0, 12.0])
    labels = forward_outcomes(close, 2)
    assert labels.iloc[-2:].isna().all().all()
    assert labels.iloc[0]["return"] == pytest.approx(-0.1)


def test_prediction_is_persisted_before_outcome(tmp_path):
    db = Database(tmp_path / "mesh.db")
    db.init_schema()
    ledger = PredictionLedger(db)
    prediction_id = ledger.record(
        model="lstm", model_version="v1", symbol="ABC", horizon=1,
        as_of="2025-01-01T00:00:00+00:00", probability_up=0.7, regime="RANGE",
    )
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM prediction_ledger WHERE id=?", (prediction_id,)).fetchone()
    assert row["outcome_at"] is None
    with pytest.raises(ValueError, match="after"):
        ledger.settle(prediction_id, outcome_at="2025-01-01T00:00:00+00:00", actual_direction=1, realized_return=.02, mfe=.03, mae=-.01)
    ledger.settle(prediction_id, outcome_at="2025-01-02T00:00:00+00:00", actual_direction=1, realized_return=.02, mfe=.03, mae=-.01)
    reliability = ledger.reliability(model="lstm", model_version="v1", symbol="ABC", horizon=1, probability_up=.7, regime="RANGE", minimum_samples=2)
    assert reliability["status"] == "INSUFFICIENT_DATA"
    assert reliability["historical_hit_rate"] is None


def test_due_predictions_settle_only_from_later_candles(tmp_path):
    db = Database(tmp_path / "mesh.db")
    db.init_schema()
    ledger = PredictionLedger(db)
    ledger.record(
        model="lstm", model_version="v1", symbol="ABC", horizon=2,
        as_of="2025-01-01T00:00:00+00:00", probability_up=.7, regime="RANGE",
    )
    partial = pd.DataFrame({"Close": [100.0, 103.0]}, index=pd.date_range("2025-01-01", periods=2, tz="UTC"))
    assert ledger.settle_due("ABC", partial) == 0
    complete = pd.DataFrame({"Close": [100.0, 103.0, 98.0]}, index=pd.date_range("2025-01-01", periods=3, tz="UTC"))
    assert ledger.settle_due("ABC", complete) == 1
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM prediction_ledger").fetchone()
    assert row["realized_return"] == pytest.approx(-.02)
    assert row["mfe"] == pytest.approx(.03)
    assert row["mae"] == pytest.approx(-.02)


def test_long_horizons_have_independent_artifact_names_and_calibration_versions():
    from stock_alert_app.models.price_lstm import _artifact_paths, _model_version

    one_day = _artifact_paths(1)
    five_day = _artifact_paths(5)
    twenty_day = _artifact_paths(20)
    assert len({str(path) for paths in (one_day, five_day, twenty_day) for path in paths}) == 9
    assert _model_version(5) != _model_version(1)
    assert _model_version(20) != _model_version(5)


def test_loading_a_new_horizon_does_not_reuse_cached_one(monkeypatch, tmp_path):
    from stock_alert_app.models import price_lstm

    monkeypatch.setattr(price_lstm, "_artifact_paths", lambda horizon: (tmp_path / "m", tmp_path / "s", tmp_path / "meta"))
    monkeypatch.setattr(price_lstm, "_global_model", object())
    monkeypatch.setattr(price_lstm, "_global_scaler", object())
    monkeypatch.setattr(price_lstm, "_global_metadata", {"window": 30, "horizon": 1})
    assert price_lstm._load_global_artifacts(horizon=5) is False
    assert price_lstm._global_model is None


def test_metrics_grouping_and_meta_preference_are_deterministic():
    rows = [
        {"model": "m", "horizon": 1, "symbol": "A", "regime": "RANGE", "probability_up": .8, "actual_direction": 1, "realized_return": .02, "mfe": .03, "mae": -.01},
        {"model": "m", "horizon": 1, "symbol": "A", "regime": "RANGE", "probability_up": .2, "actual_direction": 0, "realized_return": -.01, "mfe": .01, "mae": -.02},
    ]
    metrics = evaluation_metrics(rows)
    assert metrics["mcc"] == 1.0
    assert metrics["brier_score"] == pytest.approx(.04)
    assert grouped_metrics(rows)[0]["confidence_bucket"] == "75-100"

    x = np.array([[-2.0, -1.0], [-1.0, -.5], [1.0, .5], [2.0, 1.0]])
    model = ExperimentalLogisticStacker(iterations=400).fit(x, np.array([0, 0, 1, 1]))
    assert model.predict_proba(np.array([[2.0, 1.0]]))[0, 1] > 0.5
    assert not materially_better(rows, rows, minimum_samples=2)
    assert stacking_vector(.6, {"trend": .2}).shape == (9,)
    assert qualified_meta_probability(enabled=False, stacker=model, features=x[0], meta_records=rows, fusion_records=rows, minimum_samples=2) is None
