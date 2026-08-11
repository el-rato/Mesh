from __future__ import annotations

import math
from typing import Any

#: Thresholds mirroring verdict._signal_label so committee rows and verdict
#: reasons agree with each other.
_NEUTRAL_BAND = 0.05
_SIGNAL_WEIGHTS = {
    "quant": 0.60,
    "technical": 0.25,
    "news": 0.15,
    "institutional": 0.10,
}


def _is_finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _num(value: Any, default: float = 0.0) -> float:
    if _is_finite(value):
        return float(value)
    return default


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def signal_state(score: Any) -> str:
    """Map a normalized [-1, +1] signal score to BULL / BEAR / NEUTRAL."""
    s = _num(score)
    if s > _NEUTRAL_BAND:
        return "BULL"
    if s < -_NEUTRAL_BAND:
        return "BEAR"
    return "NEUTRAL"


def _inst_state(inst: dict[str, Any] | None) -> str | None:
    """Institutional stance from real 13F data; None when unavailable."""
    if not inst:
        return None
    buys = _num(inst.get("buy_count"))
    sells = _num(inst.get("sell_count"))
    if buys > sells:
        return "BULL"
    if sells > buys:
        return "BEAR"
    return "NEUTRAL"


def committee_signals(
    verdict: dict[str, Any] | None,
    institutional: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compact investment-committee signal table built from stored verdict data.

    ``verdict`` may come from a live ``Verdict.as_dict()`` or a DB row mapped to
    the same shape. Every signal is derived from real scores; nothing is
    invented, and unavailable signals are marked ``N/A``.
    """
    v = verdict or {}
    lstm = v.get("lstm") or {}
    technical = v.get("technical") or {}

    lstm_score = _num(lstm.get("score"))
    lstm_available = _is_finite(lstm.get("score")) and (
        lstm.get("score") != 0.0 or _is_finite(lstm.get("probability_up"))
    )
    lstm_confidence = lstm.get("model_confidence")
    technical_score = _num(technical.get("score"))
    technical_available = bool(technical.get("available", v.get("price") is not None)) and _is_finite(technical.get("score"))
    news_available = bool(v.get("news_available")) and _is_finite(v.get("news_score"))
    news_score = _num(v.get("news_score"))
    news_data = v.get("news") or {}
    news_confidence = news_data.get("confidence", news_data.get("avg_confidence"))

    inst_score = 0.0
    inst_available = False
    if institutional:
        buys = _num(institutional.get("buy_count"))
        sells = _num(institutional.get("sell_count"))
        activity = buys + sells
        if activity > 0:
            inst_score = _clamp((buys - sells) / activity)
            inst_available = True

    specs = [
        ("quant", "QUANT/LSTM", lstm_score, lstm_confidence, lstm_available),
        ("technical", "TECHNICAL", technical_score, technical.get("confidence"), technical_available),
        ("news", "NEWS", news_score, news_confidence, news_available),
        ("institutional", "INSTITUTIONAL", inst_score, None, inst_available),
    ]
    signals: list[dict[str, Any]] = []
    denominator = 0.0
    numerator = 0.0
    confidence_total = 0.0
    for key, label, score, explicit_confidence, available in specs:
        if available:
            confidence = _num(explicit_confidence, abs(score))
            confidence = max(0.0, min(1.0, confidence))
            weight = _SIGNAL_WEIGHTS[key]
            effective = confidence * weight
            denominator += effective
            numerator += score * effective
            confidence_total += effective * confidence
            direction = signal_state(score)
        else:
            confidence = None
            weight = _SIGNAL_WEIGHTS[key]
            direction = "N/A"
        signals.append(
            {
                "key": key,
                "label": label,
                "state": direction,
                "direction": direction,
                "available": available,
                "score": round(score, 4) if available else None,
                "confidence": round(confidence, 4) if confidence is not None else None,
                "weight": weight,
                "contribution": None,
            }
        )

    if denominator:
        for signal in signals:
            if signal["available"]:
                signal["contribution"] = round(
                    signal["score"] * signal["confidence"] * signal["weight"] / denominator,
                    4,
                )

    if denominator <= 0:
        final_score = None
        final_state = "N/A"
        final_confidence = None
    else:
        final_score = _clamp(numerator / denominator)
        final_state = signal_state(final_score)
        weighted_confidence = confidence_total / denominator
        directional = [s["score"] for s in signals if s["available"] and abs(s["score"]) > _NEUTRAL_BAND]
        disagreement = abs(sum(directional)) / sum(abs(d) for d in directional) if directional else 1.0
        strength = min(1.0, abs(final_score) / 0.25)
        final_confidence = round(max(0.0, min(1.0, weighted_confidence * (0.5 + 0.5 * disagreement) * (0.5 + 0.5 * strength))), 4)

    why: list[str] = []
    for signal in signals:
        if not signal["available"]:
            why.append(f"No {signal['label'].lower()} signal available")
        elif signal["state"] != "NEUTRAL":
            strength = "strongly" if abs(signal["score"]) >= 0.6 else "moderately"
            why.append(f"{signal['label'].title()} {strength} {signal['state'].lower()}")
    if final_state != "N/A" and len([s for s in signals if s["available"]]) > 1:
        directions = {s["state"] for s in signals if s["available"] and s["state"] != "NEUTRAL"}
        if len(directions) > 1:
            why.append("Signals disagree; final confidence reduced")
    if not why:
        why.append("No committee signals available")

    return {
        "signals": signals,
        "verdict": final_state,
        "score": round(final_score, 4) if final_score is not None else None,
        "confidence": final_confidence,
        "why": why,
    }


def _pct(value: Any, signed: bool = False, digits: int = 1) -> str:
    f = float(value) * 100.0
    sign = "+" if signed and f > 0 else ""
    return f"{sign}{f:.{digits}f}%"


def bull_bear_factors(
    verdict: dict[str, Any] | None,
    institutional: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Strongest bull/bear case factors, generated strictly from real signals.

    LSTM + technical + news + (real) institutional factors only. No fabricated
    valuation or fundamental claims are ever emitted.
    """
    v = verdict or {}
    lstm = v.get("lstm") or {}
    price = v.get("price") or {}
    bull: list[str] = []
    bear: list[str] = []

    # ---- LSTM ----
    lstm_score = _num(lstm.get("score"))
    prob = lstm.get("probability_up")
    ret = lstm.get("predicted_return")
    if _is_finite(lstm_score) and abs(lstm_score) > _NEUTRAL_BAND:
        prob_s = f"{_pct(prob)}" if _is_finite(prob) else "n/a"
        ret_s = f"{_pct(ret, signed=True)}" if _is_finite(ret) else "n/a"
        text = f"LSTM predicts upside (P(up) {prob_s}, predicted return {ret_s})" if lstm_score > 0 else f"LSTM predicts downside (P(up) {prob_s}, predicted return {ret_s})"
        (bull if lstm_score > 0 else bear).append(text)

    # ---- Technical / price ----
    if _is_finite(price.get("momentum_20")):
        mom = _num(price.get("momentum_20"))
        if mom > _NEUTRAL_BAND:
            bull.append(f"Positive 20d momentum ({_pct(mom)})")
        elif mom < -_NEUTRAL_BAND:
            bear.append(f"Negative 20d momentum ({_pct(mom)})")

    if _is_finite(price.get("rsi_14")):
        rsi = _num(price.get("rsi_14"))
        if rsi >= 70:
            bear.append(f"RSI overbought ({rsi:.0f})")
        elif rsi <= 30:
            bull.append(f"RSI oversold, mean-reversion setup ({rsi:.0f})")

    above = price.get("above_sma_50")
    if above is True:
        bull.append("price above 50-day MA")
    elif above is False:
        bear.append("price below 50-day MA")

    trend = _num(price.get("trend_50_200"))
    if trend > 0:
        bull.append("50d above 200d (uptrend)")
    elif trend < 0:
        bear.append("50d below 200d (downtrend)")

    # ---- News ----
    if bool(v.get("news_available")):
        news_score = _num(v.get("news_score"))
        label = (v.get("news_label") or "").lower() or signal_state(news_score).lower()
        news = v.get("news") or {}
        count = news.get("article_count")
        count_s = f" {int(count)} articles" if _is_finite(count) else ""
        if news_score > 0.15:
            bull.append(f"Positive news sentiment ({label}{count_s})")
        elif news_score < -0.15:
            bear.append(f"Negative news sentiment ({label}{count_s})")

    # ---- Institutional (real 13F activity only) ----
    if institutional:
        buys = int(_num(institutional.get("buy_count")))
        sells = int(_num(institutional.get("sell_count")))
        if buys > 0:
            bull.append(f"{buys} tracked funds building positions")
        if sells > 0:
            bear.append(f"{sells} tracked funds trimming positions")

    return {"bull": bull, "bear": bear}
