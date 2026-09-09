from stock_alert_app.market_evidence import SignalEvidence
from stock_alert_app.signal_fusion import FusionConfig, fuse_signals

import numpy as np
import pandas as pd


def ev(engine: str, score: float, confidence: float = 0.8, signal: str | None = None):
    return SignalEvidence(
        engine=engine,
        signal=signal or ("BULL" if score > 0 else "BEAR" if score < 0 else "NEUTRAL"),
        score=score,
        confidence=confidence,
        reasons=(f"{engine} evidence",),
    )


def test_strong_ml_alone_cannot_create_strong_mesh_signal():
    result = fuse_signals(
        {"1D": {"probability_up": 0.99, "calibrated": True, "reliability": 0.9}},
        [],
    )
    assert result.direction == "UNCERTAIN"
    assert result.confidence <= 0.35


def test_conflicting_directional_engines_are_explicit():
    result = fuse_signals(
        {},
        [ev("trend", 0.9), ev("momentum", -0.9), ev("volume", 0.7), ev("candlesticks", -0.8)],
    )
    assert result.direction == "CONFLICTED"
    assert result.contradiction_score >= 0.42
    assert result.supporting_evidence
    assert result.opposing_evidence


def test_high_volatility_changes_risk_not_direction():
    result = fuse_signals({}, [ev("trend", .8), ev("volatility", .9, signal="HIGH_VOL")])
    assert result.direction == "UNCERTAIN"
    assert result.risk_level == "HIGH"
    assert all(row["category"] != "volatility" for row in result.supporting_evidence)
    assert result.opposing_evidence[-1]["category"] == "volatility"


def test_regime_weights_are_configurable_and_forecast_gaps_are_honest():
    cfg = FusionConfig(regime_multipliers={"RANGE": {"trend": 0.1, "candlesticks": 3.0}})
    result = fuse_signals(
        {"1D": {"probability_up": 0.6, "calibrated": True, "reliability": 0.6}},
        [ev("market_regime", 0.0, signal="RANGE"), ev("trend", 0.8), ev("candlesticks", -0.8)],
        config=cfg,
    )
    assert result.regime == "RANGE"
    assert result.score < 0
    assert [row["status"] for row in result.forecasts] == ["ok", "no_data", "no_data"]


def test_market_data_to_verdict_payload_uses_real_fusion_outputs(monkeypatch):
    from stock_alert_app import signals
    from stock_alert_app.models.price_lstm import LSTMResult
    from stock_alert_app.price import PriceState
    from stock_alert_app.verdict import build_verdict

    index = pd.date_range("2025-01-01", periods=100, tz="UTC")
    close = np.linspace(80.0, 110.0, 100)
    history = pd.DataFrame({
        "Open": close - .2, "High": close + .8, "Low": close - .8,
        "Close": close, "Volume": np.linspace(1_000, 1_500, 100),
    }, index=index)
    prediction = LSTMResult("ABC", .01, .72, .72, "BULL", model_version="v-test", as_of=index[-1].isoformat())
    monkeypatch.setattr("stock_alert_app.models.price_lstm.predict_price_lstm", lambda symbol: prediction)
    monkeypatch.setattr(signals, "social_momentum_signal", lambda *args: signals.SignalResult("social", status="no_data"))
    monkeypatch.setattr(signals, "market_regime_signal", lambda *args: signals.SignalResult("regime", status="no_data"))
    price = PriceState("NYSE", "ABC", 110, 109.8, 110.8, 109.2, 1500, .08, 60, 104, 95, .09, True)
    payload = build_verdict("NYSE", "ABC", None, price, yahoo_symbol="ABC", history_df=history).as_dict()
    mesh = payload["mesh_signal"]
    assert mesh["forecasts"][0]["probability_up"] == .72
    assert mesh["confidence"] != .72
    assert {row["category"] for row in mesh["evidence"]} >= {"ml", "trend", "momentum", "volume", "volatility", "candlesticks", "market_regime"}
