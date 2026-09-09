from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import torch

from stock_alert_app.models import price_lstm


def _ohlcv(n: int = 120) -> np.ndarray:
    close = 100.0 + np.arange(n) * 0.2 + np.sin(np.arange(n) / 4.0)
    open_ = close * 0.999
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    volume = 1_000.0 + np.arange(n) * 3.0
    return np.column_stack([open_, high, low, close, volume]).astype(np.float32)


def test_features_are_causal() -> None:
    original = _ohlcv()
    changed = original.copy()
    changed[80:, 4] *= 100.0

    before = price_lstm._feature_matrix(original)
    after = price_lstm._feature_matrix(changed)

    np.testing.assert_allclose(before[:80], after[:80])


def test_target_and_inference_window_are_aligned() -> None:
    arr = _ohlcv()
    features = price_lstm._feature_matrix(arr)
    X, y = price_lstm.prepare_features(arr, window=30, horizon=1)
    latest = price_lstm.prepare_inference_window(arr, window=30)

    assert y[0] == pytest.approx(np.log(arr[60, 3]) - np.log(arr[59, 3]))
    np.testing.assert_allclose(X[-1, -1], features[-2])
    np.testing.assert_allclose(latest[0, -1], features[-1])


def test_per_symbol_split_is_chronological() -> None:
    X = np.arange(100, dtype=np.float32).reshape(100, 1, 1)
    y = np.arange(100, dtype=np.float32)
    X_tr, y_tr, X_val, y_val, X_test, y_test = price_lstm._chronological_split(X, y)

    assert (len(X_tr), len(X_val), len(X_test)) == (80, 10, 10)
    assert y_tr[-1] < y_val[0] < y_test[0]


def test_global_split_keeps_dates_out_of_earlier_partitions() -> None:
    timestamps = np.tile(np.arange(50, dtype=np.int64), 2)
    X = timestamps.astype(np.float32).reshape(100, 1, 1)
    y = timestamps.astype(np.float32)

    _, y_tr, _, y_val, _, y_test = price_lstm._chronological_split(
        X, y, timestamps
    )

    assert y_tr.max() < y_val.min() < y_test.min()


def test_scaler_is_fit_only_on_training_data() -> None:
    train = np.arange(24, dtype=np.float32).reshape(4, 3, 2)
    future = np.full((2, 3, 2), 1_000_000.0, dtype=np.float32)
    _, scaler = price_lstm.scale_features(train, fit=True)
    mean_before = scaler.mean.copy()

    price_lstm.scale_features(future, scaler=scaler, fit=False)

    np.testing.assert_array_equal(scaler.mean, mean_before)


def test_unfitted_scaler_fails_closed() -> None:
    with pytest.raises(ValueError, match="not been fitted"):
        price_lstm.scale_features(
            np.ones((1, 2, len(price_lstm.FEATURE_NAMES)), dtype=np.float32),
            scaler=price_lstm.RobustStandardScaler(),
        )


def test_malformed_and_stale_market_data_are_rejected(monkeypatch) -> None:
    arr = _ohlcv()
    columns = ["Open", "High", "Low", "Close", "Volume"]
    fresh = pd.DataFrame(
        arr,
        columns=columns,
        index=pd.date_range(end=datetime.now(UTC), periods=len(arr), freq="B"),
    )
    unordered = fresh.iloc[::-1]
    monkeypatch.setattr(
        "stock_alert_app.price_providers.fetch_ohlcv", lambda *args, **kwargs: unordered
    )
    assert price_lstm.fetch_history_array("TEST") is None

    stale = fresh.copy()
    stale.index = pd.date_range(end="2020-01-01", periods=len(arr), freq="B")
    monkeypatch.setattr(
        "stock_alert_app.price_providers.fetch_ohlcv", lambda *args, **kwargs: stale
    )
    assert price_lstm.fetch_history_array("TEST") is None


def test_artifact_version_mismatch_fails_closed_without_training(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "model.pt"
    scaler_path = tmp_path / "scaler.pkl"
    metadata_path = tmp_path / "metadata.json"
    model_path.write_bytes(b"model")
    scaler_path.write_bytes(b"scaler")
    metadata_path.write_text(
        '{"model_version":"obsolete","trained_at":"2099-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(price_lstm, "GLOBAL_MODEL_PATH", model_path)
    monkeypatch.setattr(price_lstm, "GLOBAL_SCALER_PATH", scaler_path)
    monkeypatch.setattr(price_lstm, "GLOBAL_METADATA_PATH", metadata_path)
    monkeypatch.setattr(price_lstm, "_global_model", None)
    monkeypatch.setattr(price_lstm, "_global_scaler", None)
    monkeypatch.setattr(price_lstm, "_global_metadata", None)
    monkeypatch.setattr(
        price_lstm,
        "train_global_lstm",
        lambda **kwargs: pytest.fail("inference must not trigger training"),
    )

    assert price_lstm._ensure_global("2y", 30, 1) == (None, None)


def test_prediction_uses_calibration_and_exposes_artifact_metadata(monkeypatch) -> None:
    class FixedModel:
        def __call__(self, value):
            return torch.tensor([[0.01]], dtype=torch.float32)

    scaler = price_lstm.RobustStandardScaler()
    scaler.mean = np.zeros(len(price_lstm.FEATURE_NAMES), dtype=np.float32)
    scaler.scale = np.ones(len(price_lstm.FEATURE_NAMES), dtype=np.float32)
    monkeypatch.setattr(
        price_lstm, "_ensure_global", lambda period, window, horizon: (FixedModel(), scaler)
    )
    monkeypatch.setattr(
        price_lstm,
        "_validated_history",
        lambda symbol, period: (
            _ohlcv(),
            "2026-09-09T00:00:00+00:00",
            np.arange(120),
        ),
    )
    monkeypatch.setattr(
        price_lstm,
        "_global_metadata",
        {
            "model_version": price_lstm.MODEL_VERSION,
            "calibration_slope": 100.0,
            "calibration_intercept": 0.0,
            "metrics": {"mse": 0.1, "mae": 0.2, "directional_accuracy": 0.6},
        },
    )

    result = price_lstm.predict_price_lstm("TEST")

    assert result is not None
    assert result.probability_up == pytest.approx(0.7310586)
    assert result.signal == "BULL"
    assert result.model_version == price_lstm.MODEL_VERSION
    assert result.forecast_horizon == "1 trading day"
    assert result.as_of == "2026-09-09T00:00:00+00:00"
