from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignalEvidence:
    engine: str
    signal: str
    score: float
    confidence: float
    timeframe: str = "1d"
    horizon: str = "current completed bar"
    status: str = "ok"
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return _native(asdict(self))


def _native(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    return value


def _result(
    engine: str,
    signal: str,
    score: float,
    confidence: float,
    metrics: dict[str, Any],
    reasons: list[str],
    timeframe: str,
) -> SignalEvidence:
    return SignalEvidence(
        engine=engine,
        signal=signal,
        score=round(float(np.clip(score, -1.0, 1.0)), 4),
        confidence=round(float(np.clip(confidence, 0.0, 1.0)), 4),
        timeframe=timeframe,
        metrics=metrics,
        reasons=tuple(reasons),
    )


def _no_data(engine: str, reason: str, timeframe: str) -> SignalEvidence:
    return SignalEvidence(engine, "NO_DATA", 0.0, 0.0, timeframe, status="no_data", reasons=(reason,))


def _frame(data: pd.DataFrame, minimum: int) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close", "Volume"]
    if data is None or len(data) < minimum or any(c not in data for c in required):
        raise ValueError("insufficient OHLCV history")
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and chronological")
    frame = data[required].astype(float)
    values = frame.to_numpy()
    if not np.all(np.isfinite(values)):
        raise ValueError("OHLCV contains non-finite values")
    if np.any(values[:, :4] <= 0.0) or np.any(values[:, 4] < 0.0):
        raise ValueError("invalid OHLCV values")
    if np.any(frame["High"] < frame[["Open", "Low", "Close"]].max(axis=1)):
        raise ValueError("invalid high")
    if np.any(frame["Low"] > frame[["Open", "High", "Close"]].min(axis=1)):
        raise ValueError("invalid low")
    return frame


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _slope(series: pd.Series, lookback: int = 20) -> float:
    values = series.iloc[-lookback:].to_numpy(dtype=float)
    if len(values) < 2 or values[-1] == 0.0:
        return 0.0
    return float(np.polyfit(np.arange(len(values)), values, 1)[0] / abs(values[-1]))


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["Close"].shift(1)
    return pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous).abs(),
            (frame["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    return _true_range(frame).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def _adx(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low = frame["High"], frame["Low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    atr = _atr(frame, window).replace(0.0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / window, adjust=False, min_periods=window).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1 / window, adjust=False, min_periods=window).mean().fillna(0.0)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(loss != 0.0, 100.0).fillna(50.0)


def trend_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 60)
    except ValueError as exc:
        return _no_data("trend", str(exc), timeframe)
    close = frame["Close"]
    ema20, ema50 = _ema(close, 20).iloc[-1], _ema(close, 50).iloc[-1]
    ema200 = _ema(close, 200).iloc[-1] if len(frame) >= 200 else float("nan")
    slope = _slope(close)
    adx = float(_adx(frame).iloc[-1])
    ema_score = 0.0
    reasons: list[str] = []
    if ema20 > ema50 and (math.isnan(ema200) or ema50 > ema200):
        ema_score = 0.6
        reasons.append("EMA stack bullish")
    elif ema20 < ema50 and (math.isnan(ema200) or ema50 < ema200):
        ema_score = -0.6
        reasons.append("EMA stack bearish")
    slope_score = float(np.clip(slope * 80.0, -0.4, 0.4))
    score = ema_score + slope_score
    signal = "BULL" if score > 0.1 else "BEAR" if score < -0.1 else "NEUTRAL"
    confidence = 0.35 + min(adx, 50.0) / 100.0 + min(abs(score), 1.0) * 0.15
    return _result(
        "trend",
        signal,
        score,
        confidence,
        {"ema_20": ema20, "ema_50": ema50, "ema_200": ema200, "slope_20": slope, "adx_14": adx},
        reasons,
        timeframe,
    )


def momentum_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 35)
    except ValueError as exc:
        return _no_data("momentum", str(exc), timeframe)
    close = frame["Close"]
    rsi = float(_rsi(close).iloc[-1])
    macd_series = _ema(close, 12) - _ema(close, 26)
    macd = float(macd_series.iloc[-1])
    macd_signal = float(_ema(macd_series.dropna(), 9).iloc[-1])
    roc = float(close.iloc[-1] / close.iloc[-13] - 1.0)
    score = np.clip((rsi - 50.0) / 35.0, -1, 1) * 0.35
    score += np.clip((macd - macd_signal) / close.iloc[-1] * 100.0, -1, 1) * 0.35
    score += np.clip(roc * 8.0, -1, 1) * 0.3
    signal = "BULL" if score > 0.1 else "BEAR" if score < -0.1 else "NEUTRAL"
    return _result(
        "momentum",
        signal,
        score,
        0.4 + min(abs(score), 1.0) * 0.5,
        {"rsi_14": rsi, "macd": macd, "macd_signal": macd_signal, "roc_12": roc},
        [f"RSI {rsi:.1f}", f"ROC {roc:+.2%}"],
        timeframe,
    )


def volume_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 30)
    except ValueError as exc:
        return _no_data("volume", str(exc), timeframe)
    close, volume = frame["Close"], frame["Volume"]
    baseline = float(volume.iloc[-21:-1].mean())
    relative = float(volume.iloc[-1] / baseline) if baseline > 0 else 0.0
    direction = np.sign(close.diff().fillna(0.0).to_numpy())
    obv = pd.Series(np.cumsum(direction * volume.to_numpy()), index=frame.index)
    price_slope, obv_slope = _slope(close), _slope(obv.replace(0.0, np.nan).ffill().fillna(0.0))
    divergence = "NONE"
    if price_slope > 0 and obv_slope < 0:
        divergence = "BEARISH"
    elif price_slope < 0 and obv_slope > 0:
        divergence = "BULLISH"
    score = np.sign(price_slope) * min(abs(obv_slope) * 20.0, 0.5)
    score += 0.35 if divergence == "BULLISH" else -0.35 if divergence == "BEARISH" else 0.0
    score *= min(max(relative, 0.25), 2.0) / 1.25
    signal = "BULL" if score > 0.1 else "BEAR" if score < -0.1 else "NEUTRAL"
    return _result(
        "volume",
        signal,
        score,
        0.35 + min(abs(relative - 1.0), 1.0) * 0.25 + (0.2 if divergence != "NONE" else 0.0),
        {"relative_volume_20": relative, "obv": float(obv.iloc[-1]), "obv_slope_20": obv_slope, "price_volume_divergence": divergence},
        [f"relative volume {relative:.2f}x", f"divergence {divergence.lower()}"],
        timeframe,
    )


def volatility_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 80)
    except ValueError as exc:
        return _no_data("volatility", str(exc), timeframe)
    close = frame["Close"]
    atr = float(_atr(frame).iloc[-1])
    returns = np.log(close).diff()
    realized = float(returns.iloc[-20:].std(ddof=0) * math.sqrt(252))
    rolling = returns.rolling(20).std(ddof=0) * math.sqrt(252)
    baseline = float(rolling.iloc[-60:].median())
    vol_ratio = realized / baseline if baseline > 0 else 1.0
    mean20 = float(close.iloc[-20:].mean())
    std20 = float(close.iloc[-20:].std(ddof=0))
    width = 4.0 * std20 / mean20 if mean20 else 0.0
    score = float(np.clip((vol_ratio - 1.0) / 1.5, -1.0, 1.0))
    signal = "HIGH_VOL" if vol_ratio >= 1.5 else "LOW_VOL" if vol_ratio <= 0.65 else "NORMAL_VOL"
    return _result(
        "volatility",
        signal,
        score,
        0.45 + min(abs(vol_ratio - 1.0), 1.0) * 0.4,
        {"atr_14": atr, "atr_pct": atr / close.iloc[-1], "realized_volatility_20": realized, "volatility_ratio": vol_ratio, "bollinger_width_20": width},
        [f"realized volatility {realized:.1%}", f"Bollinger width {width:.1%}"],
        timeframe,
    )


def _candle_metrics(frame: pd.DataFrame) -> dict[str, float]:
    row, previous = frame.iloc[-1], frame.iloc[-2]
    span = max(row["High"] - row["Low"], 1e-12)
    body = abs(row["Close"] - row["Open"])
    upper = row["High"] - max(row["Open"], row["Close"])
    lower = min(row["Open"], row["Close"]) - row["Low"]
    atr = float(_atr(frame).iloc[-1])
    prior_volume = float(frame["Volume"].iloc[-21:-1].mean())
    return {
        "body_ratio": body / span,
        "upper_wick_ratio": upper / span,
        "lower_wick_ratio": lower / span,
        "close_position": (row["Close"] - row["Low"]) / span,
        "atr_normalized_range": span / atr if atr > 0 else 0.0,
        "relative_volume": row["Volume"] / prior_volume if prior_volume > 0 else 0.0,
        "gap": (row["Open"] - previous["Close"]) / previous["Close"],
    }


def _detect_patterns(frame: pd.DataFrame, metrics: dict[str, float]) -> list[str]:
    o, h, low, c = (frame[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    body = abs(c[-1] - o[-1])
    prior_body = abs(c[-2] - o[-2])
    trend = _slope(frame["Close"].iloc[:-1], min(20, len(frame) - 1))
    support = float(frame["Low"].iloc[-21:-1].min())
    resistance = float(frame["High"].iloc[-21:-1].max())
    near_support = low[-1] <= support * 1.01
    near_resistance = h[-1] >= resistance * 0.99
    volume_ok = metrics["relative_volume"] >= 1.0
    bullish_confirm = metrics["close_position"] >= 0.6
    bearish_confirm = metrics["close_position"] <= 0.4
    metrics.update(
        {
            "context_trend_slope": trend,
            "near_support": near_support,
            "near_resistance": near_resistance,
            "volume_confirmed": volume_ok,
            "bullish_confirmation": bullish_confirm,
            "bearish_confirmation": bearish_confirm,
        }
    )
    patterns: list[str] = []
    if c[-2] < o[-2] and c[-1] > o[-1] and o[-1] <= c[-2] and c[-1] >= o[-2] and trend < 0 and (volume_ok or near_support):
        patterns.append("bullish_engulfing")
    if c[-2] > o[-2] and c[-1] < o[-1] and o[-1] >= c[-2] and c[-1] <= o[-2] and trend > 0 and (volume_ok or near_resistance):
        patterns.append("bearish_engulfing")
    hammer_shape = metrics["lower_wick_ratio"] >= max(0.5, 2 * metrics["body_ratio"]) and metrics["upper_wick_ratio"] <= 0.2
    if hammer_shape and trend < 0 and near_support and bullish_confirm:
        patterns.append("hammer")
    if hammer_shape and trend > 0 and near_resistance and (c[-1] < o[-1] or metrics["relative_volume"] >= 1.2):
        patterns.append("hanging_man")
    if metrics["upper_wick_ratio"] >= max(0.5, 2 * metrics["body_ratio"]) and metrics["lower_wick_ratio"] <= 0.2 and trend > 0 and near_resistance and bearish_confirm:
        patterns.append("shooting_star")
    if metrics["body_ratio"] <= 0.1:
        patterns.append("doji")
    if h[-1] < h[-2] and low[-1] > low[-2]:
        patterns.append("inside_bar")
    if h[-1] > h[-2] and low[-1] < low[-2]:
        patterns.append("outside_bar")
    if metrics["body_ratio"] >= 0.85 and metrics["upper_wick_ratio"] <= 0.1 and metrics["lower_wick_ratio"] <= 0.1 and volume_ok and ((c[-1] > o[-1] and bullish_confirm) or (c[-1] < o[-1] and bearish_confirm)):
        patterns.append("bullish_marubozu" if c[-1] > o[-1] else "bearish_marubozu")
    small_middle = abs(c[-2] - o[-2]) <= max(prior_body, abs(c[-3] - o[-3])) * 0.5
    midpoint_first = (o[-3] + c[-3]) / 2.0
    if c[-3] < o[-3] and small_middle and c[-1] > o[-1] and c[-1] > midpoint_first and trend < 0 and bullish_confirm and (volume_ok or near_support):
        patterns.append("morning_star")
    if c[-3] > o[-3] and small_middle and c[-1] < o[-1] and c[-1] < midpoint_first and trend > 0 and bearish_confirm and (volume_ok or near_resistance):
        patterns.append("evening_star")
    bullish_three = all(c[i] > o[i] for i in (-3, -2, -1)) and c[-3] < c[-2] < c[-1] and all(abs(c[i] - o[i]) / max(h[i] - low[i], 1e-12) >= 0.55 for i in (-3, -2, -1))
    bearish_three = all(c[i] < o[i] for i in (-3, -2, -1)) and c[-3] > c[-2] > c[-1] and all(abs(c[i] - o[i]) / max(h[i] - low[i], 1e-12) >= 0.55 for i in (-3, -2, -1))
    if bullish_three and trend < 0 and volume_ok:
        patterns.append("three_white_soldiers")
    if bearish_three and trend > 0 and volume_ok:
        patterns.append("three_black_crows")
    return patterns


def candlestick_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 35)
    except ValueError as exc:
        return _no_data("candlesticks", str(exc), timeframe)
    metrics = _candle_metrics(frame)
    patterns = _detect_patterns(frame, metrics)
    bullish = sum(p in {"bullish_engulfing", "hammer", "morning_star", "three_white_soldiers", "bullish_marubozu"} for p in patterns)
    bearish = sum(p in {"bearish_engulfing", "hanging_man", "shooting_star", "evening_star", "three_black_crows", "bearish_marubozu"} for p in patterns)
    score = float(np.clip((bullish - bearish) * 0.35, -1.0, 1.0))
    signal = "BULL" if score > 0 else "BEAR" if score < 0 else "NEUTRAL"
    payload = {**metrics, "patterns": patterns}
    confidence = 0.25 + min(len(patterns), 3) * 0.15 + min(metrics["relative_volume"], 2.0) * 0.1
    return _result("candlesticks", signal, score, confidence, payload, patterns, timeframe)


def market_regime_evidence(data: pd.DataFrame, timeframe: str = "1d") -> SignalEvidence:
    try:
        frame = _frame(data, 80)
    except ValueError as exc:
        return _no_data("market_regime", str(exc), timeframe)
    trend = trend_evidence(frame, timeframe)
    momentum = momentum_evidence(frame, timeframe)
    volatility = volatility_evidence(frame, timeframe)
    close = frame["Close"]
    roc20 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    adx = float(trend.metrics["adx_14"])
    vol_ratio = float(volatility.metrics["volatility_ratio"])
    if trend.score < -0.35 and roc20 < -0.08 and vol_ratio >= 1.2:
        regime, score = "RISK_OFF", -1.0
    elif vol_ratio >= 1.5:
        regime, score = "HIGH_VOL", -0.2 if momentum.score < 0 else 0.0
    elif vol_ratio <= 0.3:
        regime, score = "LOW_VOL", 0.0
    elif adx >= 25 and trend.score > 0.2:
        regime, score = "TRENDING_BULL", 0.75
    elif adx >= 25 and trend.score < -0.2:
        regime, score = "TRENDING_BEAR", -0.75
    elif vol_ratio <= 0.65:
        regime, score = "LOW_VOL", 0.0
    else:
        regime, score = "RANGE", 0.0
    confidence = 0.5 + min(abs(trend.score), 1.0) * 0.2 + min(abs(vol_ratio - 1.0), 1.0) * 0.2
    return _result(
        "market_regime",
        regime,
        score,
        confidence,
        {"adx_14": adx, "trend_score": trend.score, "momentum_score": momentum.score, "volatility_ratio": vol_ratio, "roc_20": roc20},
        [regime.lower().replace("_", " ")],
        timeframe,
    )


def build_market_evidence(data: pd.DataFrame, timeframe: str = "1d") -> list[SignalEvidence]:
    """Run independent evidence engines. No result can place or imply a trade."""
    return [
        trend_evidence(data, timeframe),
        momentum_evidence(data, timeframe),
        volume_evidence(data, timeframe),
        volatility_evidence(data, timeframe),
        candlestick_evidence(data, timeframe),
        market_regime_evidence(data, timeframe),
    ]
