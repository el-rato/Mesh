from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_alert_app.market_evidence import (
    SignalEvidence,
    build_market_evidence,
    candlestick_evidence,
    market_regime_evidence,
    momentum_evidence,
    trend_evidence,
    volatility_evidence,
    volume_evidence,
)


def _bars(n: int = 250, trend: float = 0.2, noise: float = 0.15) -> pd.DataFrame:
    x = np.arange(n, dtype=float)
    close = 100.0 + trend * x + noise * np.sin(x / 3.0)
    open_ = close - trend * 0.3
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.6,
            "Low": np.minimum(open_, close) - 0.6,
            "Close": close,
            "Volume": np.full(n, 1_000.0),
        },
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )


def _row(df: pd.DataFrame, pos: int, o: float, h: float, low: float, c: float, v: float = 1_000) -> None:
    df.iloc[pos] = [o, h, low, c, v]


def _pattern_frame(name: str) -> pd.DataFrame:
    bullish_context = name in {"bullish_engulfing", "hammer", "morning_star", "three_white_soldiers"}
    df = _bars(80, trend=-0.25 if bullish_context else 0.25, noise=0.0)
    level = float(df["Close"].iloc[-4])
    if name == "bullish_engulfing":
        _row(df, -2, level + 1, level + 1.2, level - 1.2, level - 1)
        _row(df, -1, level - 1.1, level + 1.5, level - 1.5, level + 1.2, 2_000)
    elif name == "bearish_engulfing":
        _row(df, -2, level - 1, level + 1.2, level - 1.2, level + 1)
        _row(df, -1, level + 1.1, level + 1.5, level - 1.5, level - 1.2, 2_000)
    elif name == "hammer":
        _row(df, -1, level - 1, level - 0.4, level - 4, level - 0.5, 2_000)
    elif name == "hanging_man":
        _row(df, -1, level + 1, level + 1.1, level - 3, level + 0.5, 2_000)
    elif name == "shooting_star":
        _row(df, -1, level + 0.5, level + 4, level - 0.1, level, 2_000)
    elif name == "morning_star":
        _row(df, -3, level + 1, level + 1.2, level - 1.2, level - 1)
        _row(df, -2, level - 1, level - 0.7, level - 1.4, level - 1.1)
        _row(df, -1, level - 0.8, level + 0.8, level - 1, level + 0.5, 2_000)
    elif name == "evening_star":
        _row(df, -3, level - 1, level + 1.2, level - 1.2, level + 1)
        _row(df, -2, level + 1, level + 1.4, level + 0.7, level + 1.1)
        _row(df, -1, level + 0.8, level + 1, level - 0.8, level - 0.5, 2_000)
    elif name == "three_white_soldiers":
        for pos, offset in zip((-3, -2, -1), (-1.0, 0.0, 1.0)):
            _row(df, pos, level + offset - 0.8, level + offset + 0.2, level + offset - 1, level + offset, 2_000)
    elif name == "three_black_crows":
        for pos, offset in zip((-3, -2, -1), (1.0, 0.0, -1.0)):
            _row(df, pos, level + offset + 0.8, level + offset + 1, level + offset - 0.2, level + offset, 2_000)
    return df


def test_all_engines_return_standard_evidence() -> None:
    results = build_market_evidence(_bars(), timeframe="1d")
    assert len(results) == 6
    for result in results:
        assert isinstance(result, SignalEvidence)
        assert result.timeframe == "1d"
        assert result.horizon
        assert -1.0 <= result.score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
        assert not hasattr(result, "action")


def test_indicator_engines_expose_required_metrics() -> None:
    frame = _bars()
    assert {"ema_20", "ema_50", "ema_200", "slope_20", "adx_14"} <= trend_evidence(frame).metrics.keys()
    assert {"rsi_14", "macd", "macd_signal", "roc_12"} <= momentum_evidence(frame).metrics.keys()
    assert {"relative_volume_20", "obv", "price_volume_divergence"} <= volume_evidence(frame).metrics.keys()
    assert {"atr_14", "realized_volatility_20", "bollinger_width_20"} <= volatility_evidence(frame).metrics.keys()


def test_engines_fail_closed_on_unordered_data() -> None:
    result = trend_evidence(_bars().iloc[::-1])
    assert result.status == "no_data"
    assert result.signal == "NO_DATA"


@pytest.mark.parametrize(
    "pattern",
    [
        "bullish_engulfing",
        "bearish_engulfing",
        "hammer",
        "hanging_man",
        "shooting_star",
        "morning_star",
        "evening_star",
        "three_white_soldiers",
        "three_black_crows",
    ],
)
def test_contextual_directional_patterns(pattern: str) -> None:
    result = candlestick_evidence(_pattern_frame(pattern))
    assert pattern in result.metrics["patterns"]
    assert {"context_trend_slope", "near_support", "near_resistance", "volume_confirmed"} <= result.metrics.keys()


def test_structural_candlestick_patterns_and_ratios() -> None:
    df = _bars(80)
    level = float(df["Close"].iloc[-2])
    _row(df, -2, level, level + 2, level - 2, level + 0.5)
    _row(df, -1, level + 0.2, level + 1, level - 1, level + 0.21)
    inside = candlestick_evidence(df)
    assert {"doji", "inside_bar"} <= set(inside.metrics["patterns"])

    _row(df, -1, level, level + 3, level - 3, level + 1, 2_000)
    assert "outside_bar" in candlestick_evidence(df).metrics["patterns"]

    _row(df, -1, level, level + 3.1, level - 0.1, level + 3, 2_000)
    marubozu = candlestick_evidence(df)
    assert "bullish_marubozu" in marubozu.metrics["patterns"]
    assert {"body_ratio", "upper_wick_ratio", "lower_wick_ratio", "close_position", "atr_normalized_range", "relative_volume", "gap"} <= marubozu.metrics.keys()


def test_interpretable_market_regimes() -> None:
    assert market_regime_evidence(_bars(trend=0.3, noise=0.05)).signal == "TRENDING_BULL"
    assert market_regime_evidence(_bars(trend=-0.2, noise=0.05)).signal == "TRENDING_BEAR"

    high = _bars(trend=0.0, noise=0.05)
    high.iloc[-20:, high.columns.get_loc("Close")] += np.tile([-8.0, 8.0], 10)
    high["Open"] = high["Close"]
    high["High"] = high["Close"] + 1
    high["Low"] = high["Close"] - 1
    assert market_regime_evidence(high).signal == "HIGH_VOL"

    assert market_regime_evidence(_bars(trend=0.0, noise=0.05)).signal == "RANGE"

    low = _bars(trend=0.0, noise=2.0)
    base = float(low["Close"].iloc[-21])
    low.iloc[-20:, low.columns.get_loc("Close")] = base + np.sin(np.arange(20)) / 10_000
    low["Open"] = low["Close"]
    low["High"] = low["Close"] + 0.2
    low["Low"] = low["Close"] - 0.2
    assert market_regime_evidence(low).signal == "LOW_VOL"

    risk_off = _bars(trend=-0.05, noise=0.05)
    risk_off.iloc[-20:, risk_off.columns.get_loc("Close")] -= np.linspace(0, 25, 20)
    risk_off["Open"] = risk_off["Close"]
    risk_off["High"] = risk_off["Close"] + 1
    risk_off["Low"] = risk_off["Close"] - 1
    assert market_regime_evidence(risk_off).signal == "RISK_OFF"


def test_no_future_data_changes_prior_indicators() -> None:
    frame = _bars()
    truncated = frame.iloc[:-1]
    changed = frame.copy()
    changed.iloc[-1] = [200, 220, 180, 210, 1_000_000]
    assert trend_evidence(truncated).metrics == trend_evidence(changed.iloc[:-1]).metrics
