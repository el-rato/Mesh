from __future__ import annotations

import math
from typing import Any

from .config import settings

#: Thresholds mirroring verdict._signal_label so committee rows and verdict
#: reasons agree with each other.
_NEUTRAL_BAND = 0.05


def _signal_weights() -> dict[str, float]:
    """Centralized Investment Committee signal weights (configurable)."""
    return {
        "quant": settings.quant_weight,
        "technical": settings.technical_weight,
        "news": settings.news_weight,
        "social": settings.social_weight,
        "regime": settings.regime_weight,
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


def committee_signals(
    verdict: dict[str, Any] | None,
    institutional: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Investment Committee over generic signals.

    Consumes Quantitative (ensemble), Technical, News, Social Momentum and
    Market Regime signals. Missing signals are excluded from the weighted
    aggregation (never a neutral vote); conflicting signals reduce conviction.
    """
    v = verdict or {}
    technical = v.get("technical") or {}

    quant = v.get("quantitative") or {}
    quant_score = _num(quant.get("score"))
    quant_available = quant.get("status") == "ok" and _is_finite(quant.get("score"))
    quant_confidence = quant.get("confidence")
    models = v.get("models") or []

    technical_score = _num(technical.get("score"))
    technical_available = bool(technical.get("available", v.get("price") is not None)) and _is_finite(technical.get("score"))

    news_available = bool(v.get("news_available")) and _is_finite(v.get("news_score"))
    news_score = _num(v.get("news_score"))
    news_data = v.get("news") or {}
    news_confidence = news_data.get("confidence", news_data.get("avg_confidence"))

    social = v.get("social") or {}
    social_score = _num(social.get("score"))
    social_available = social.get("status") == "ok" and _is_finite(social.get("score"))
    social_confidence = social.get("confidence")

    regime = v.get("market_regime") or {}
    regime_score = _num(regime.get("score"))
    regime_available = regime.get("status") == "ok" and _is_finite(regime.get("score"))
    regime_confidence = regime.get("confidence")

    weights = _signal_weights()
    specs = [
        ("quant", "QUANTITATIVE", quant_score, quant_confidence, quant_available),
        ("technical", "TECHNICAL", technical_score, technical.get("confidence"), technical_available),
        ("news", "NEWS", news_score, news_confidence, news_available),
        ("social", "SOCIAL MOMENTUM", social_score, social_confidence, social_available),
        ("regime", "MARKET REGIME", regime_score, regime_confidence, regime_available),
    ]
    signals: list[dict[str, Any]] = []
    denominator = 0.0
    numerator = 0.0
    confidence_total = 0.0
    for key, label, score, explicit_confidence, available in specs:
        if available:
            confidence = _num(explicit_confidence, abs(score))
            confidence = max(0.0, min(1.0, confidence))
            weight = weights[key]
            effective = confidence * weight
            denominator += effective
            numerator += score * effective
            confidence_total += effective * confidence
            direction = signal_state(score)
        else:
            confidence = None
            weight = weights[key]
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
                "models": models if key == "quant" else None,
            }
        )

    if denominator:
        for signal in signals:
            if signal["available"]:
                signal["contribution"] = round(
                    signal["score"] * signal["confidence"] * signal["weight"] / denominator,
                    4,
                )

    # News evidence for the UI (label + article count contributing to the score).
    for signal in signals:
        if signal["key"] == "news" and signal["available"]:
            count = news_data.get("article_count")
            signal["article_count"] = int(count) if _is_finite(count) else None
            label = str(v.get("news_label") or news_data.get("label") or "").lower()
            signal["sentiment"] = label or signal_state(news_score).lower()

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

    # ---- Quantitative ensemble (prefer it; fall back to the LSTM block) ----
    quant = v.get("quantitative") or {}
    quant_score = _num(quant.get("score"))
    if quant.get("status") == "ok" and _is_finite(quant.get("score")) and abs(quant_score) > _NEUTRAL_BAND:
        models = v.get("models") or []
        available = [m for m in models if m.get("status") == "ok"]
        detail = f"({len(available)} models)" if available else ""
        text = f"Quantitative model predicts upside {detail}" if quant_score > 0 else f"Quantitative model predicts downside {detail}"
        (bull if quant_score > 0 else bear).append(text)
    else:
        lstm_score = _num(lstm.get("score"))
        prob = lstm.get("probability_up")
        ret = lstm.get("predicted_return")
        if _is_finite(lstm_score) and abs(lstm_score) > _NEUTRAL_BAND:
            prob_s = f"{_pct(prob)}" if _is_finite(prob) else "n/a"
            ret_s = f"{_pct(ret, signed=True)}" if _is_finite(ret) else "n/a"
            text = f"Quantitative model predicts upside (P(up) {prob_s}, predicted return {ret_s})" if lstm_score > 0 else f"Quantitative model predicts downside (P(up) {prob_s}, predicted return {ret_s})"
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

    # ---- Social momentum ----
    social = v.get("social") or {}
    if social.get("status") == "ok" and _is_finite(social.get("score")):
        s_score = _num(social.get("score"))
        if s_score > _NEUTRAL_BAND:
            bull.append("Rising social attention / positive discussion")
        elif s_score < -_NEUTRAL_BAND:
            bear.append("Fading social attention / negative discussion")

    # ---- Market regime ----
    regime = v.get("market_regime") or {}
    if regime.get("status") == "ok" and _is_finite(regime.get("score")):
        r_score = _num(regime.get("score"))
        if r_score > _NEUTRAL_BAND:
            bull.append("Market regime supportive (index uptrend)")
        elif r_score < -_NEUTRAL_BAND:
            bear.append("Market regime unfavorable (index downtrend)")

    # ---- Institutional (real 13F activity only) ----
    if institutional:
        buys = int(_num(institutional.get("buy_count")))
        sells = int(_num(institutional.get("sell_count")))
        if buys > 0:
            bull.append(f"{buys} tracked funds building positions")
        if sells > 0:
            bear.append(f"{sells} tracked funds trimming positions")

    return {"bull": bull, "bear": bear}


def _thesis(signals: dict[str, Any], verdict: str | None) -> str:
    parts = []
    for key in ("quant", "technical", "news", "social", "regime"):
        s = signals.get(key)
        if s and s.get("available") and s.get("state") in ("BULL", "BEAR"):
            parts.append(f"{s['label'].lower()} {s['state'].lower()}")
    if not parts:
        return "Insufficient evidence to form a clear thesis."
    return f"Primary evidence ({'; '.join(parts)}) supports a {verdict} outlook."


def _key_disagreement(signals: dict[str, Any]) -> str | None:
    bulls = [
        s["label"] for s in signals.values()
        if s.get("available") and s.get("state") == "BULL"
    ]
    bears = [
        s["label"] for s in signals.values()
        if s.get("available") and s.get("state") == "BEAR"
    ]
    if bulls and bears:
        return f"{bulls[0].title()} is bullish while {bears[0].title()} is bearish."
    return None


def _disagreements(signals: dict[str, Any]) -> list[str]:
    """Every bull-vs-bear conflict among AVAILABLE signals (not just the first)."""
    bulls = [s["label"] for s in signals.values() if s.get("available") and s.get("state") == "BULL"]
    bears = [s["label"] for s in signals.values() if s.get("available") and s.get("state") == "BEAR"]
    out: list[str] = []
    for b in bulls:
        for r in bears:
            out.append(f"{b.title()} is bullish while {r.title()} is bearish.")
    return out


def _neutral_case(signals: dict[str, Any], factors: dict[str, Any], verdict: str | None) -> list[str]:
    """The case for standing aside, built only from real signal states."""
    why: list[str] = []
    available = [s for s in signals.values() if s.get("available")]
    states = {s.get("state") for s in available}
    if verdict == "NEUTRAL":
        if not available:
            why.append("No signals are available, so there is no evidence for a directional view.")
        elif len(states) == 1 and "NEUTRAL" in states:
            why.append("Every available signal is neutral — none offers directional evidence.")
        elif "BULL" in states and "BEAR" in states:
            why.append("Bullish and bearish signals conflict and offset within the neutral band.")
        else:
            why.append("Available signals are neutral on balance; the weighted score stays inside the neutral band.")
    else:
        # Counterfactual: what would have to be true for standing aside to be right.
        conflicts = _disagreements(signals)
        if conflicts:
            why.append(
                f"Standing aside would be justified while {conflicts[0][0].lower() + conflicts[0][1:]} "
                "— the conflict has not resolved in one direction."
            )
        weak = [s["label"] for s in available if s.get("confidence") is not None and s.get("confidence", 0) < 0.5]
        if weak:
            why.append(f"{', '.join(weak[:3])} carry low confidence, so the {verdict.lower()} view rests on thin evidence.")
        if len(available) <= 2:
            why.append("Only a minority of signals are available; standing aside would be the safer reading of this coverage.")
        if not why:
            why.append(f"The {verdict.lower()} case currently dominates on the available evidence; standing aside is not supported.")
    if not (factors.get("bull") or factors.get("bear")):
        why.append("No bull or bear factors were derivable from real data.")
    return why[:5]


def _key_evidence(signals: dict[str, Any], factors: dict[str, Any], committee: dict[str, Any]) -> list[dict[str, Any]]:
    """Top evidence items: highest |contribution| available signals + lead factors."""
    rows = [
        s for s in (committee.get("signals") or [])
        if s.get("available") and s.get("contribution") is not None
    ]
    rows.sort(key=lambda s: abs(s.get("contribution") or 0.0), reverse=True)
    out: list[dict[str, Any]] = []
    for s in rows[:3]:
        out.append({
            "signal": s.get("label"),
            "direction": s.get("state"),
            "score": s.get("score"),
            "confidence": s.get("confidence"),
            "contribution": s.get("contribution"),
        })
    if factors.get("bull"):
        out.append({"evidence": "bull", "text": factors["bull"][0]})
    if factors.get("bear"):
        out.append({"evidence": "bear", "text": factors["bear"][0]})
    return out


def _forecast_range(v: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
    """Honest forecast statement. Magnitudes are never turned into price targets."""
    quant = v.get("quantitative") or {}
    if quant.get("status") == "ok" and quant.get("score") is not None:
        score = float(quant["score"])
        strength = "strong" if abs(score) >= 0.6 else "moderate" if abs(score) >= 0.2 else "weak"
        lstm = v.get("lstm") or {}
        lstm_part = ""
        if lstm.get("predicted_return") is not None:
            lstm_part = f"; LSTM point estimate {lstm['predicted_return'] * 100:+.2f}% (one model, low reliability)"
        return {
            "supported": True,
            "horizon": v.get("forecast_horizon") or "1 trading day",
            "direction": "up" if score > 0 else "down",
            "strength": strength,
            "note": f"Ensemble score {score:+.2f} indicates a {strength} {('up' if score > 0 else 'down')} bias over the horizon. "
                    f"Scores are not price targets.{lstm_part}",
        }
    return {
        "supported": False,
        "horizon": v.get("forecast_horizon") or "1 trading day",
        "reason": "No quantitative ensemble output is available for this security, so no forecast range is claimed.",
    }


def _why_narrative(
    verdict: str | None,
    confidence: float | None,
    signals: dict[str, Any],
    factors: dict[str, Any],
) -> str:
    """Why the decision happened: alignment, exclusions and the decisive factor."""
    available = [s for s in signals.values() if s.get("available")]
    excluded = [s["label"] for s in signals.values() if not s.get("available")]
    bulls = sum(1 for s in available if s.get("state") == "BULL")
    bears = sum(1 for s in available if s.get("state") == "BEAR")
    neutrals = sum(1 for s in available if s.get("state") == "NEUTRAL")
    parts: list[str] = []
    parts.append(
        f"{len(available)} of {len(signals)} signals were available "
        f"({bulls} bullish, {bears} bearish, {neutrals} neutral)."
    )
    if excluded:
        parts.append(f"Excluded from the vote (no data): {', '.join(excluded[:4])} — excluded signals are never counted as neutral votes.")
    if verdict in ("BULL", "BEAR"):
        lead = factors.get("bull" if verdict == "BULL" else "bear")
        contra = factors.get("bear" if verdict == "BULL" else "bull")
        lead_txt = f" The decisive evidence: {lead[0]}." if lead else ""
        contra_txt = f" Counter-evidence noted: {contra[0]}." if contra else ""
        conf_txt = f" Conviction is {confidence * 100:.0f}%" if confidence is not None else ""
        parts.append(f"The committee concluded {verdict}.{lead_txt}{contra_txt}{conf_txt}.")
    else:
        parts.append("The committee concluded NEUTRAL: the weighted evidence does not clear the directional band.")
    return " ".join(parts)


def _view_changes_if(signals: dict[str, Any], verdict: str | None) -> str:
    if verdict not in ("BULL", "BEAR"):
        return "No clear invalidation condition without a directional view."
    opposite = "BEAR" if verdict == "BULL" else "BULL"
    opposing = [
        s for s in signals.values()
        if s.get("available") and s.get("state") == opposite
    ]
    if opposing:
        label = opposing[0]["label"].lower()
        return f"View would weaken if {label} strengthens against the {verdict.lower()} case."
    return f"View requires {verdict.lower()} signals to persist; watch for a shift in the strongest evidence."


def _signal_status(key: str, v: dict[str, Any], available: bool, stale: bool = False) -> str:
    """Explicit signal status (AVAILABLE / NO_DATA / ERROR / STALE), never
    inferred NEUTRAL. A STALE signal is available but out of date — it is still
    usable, but flagged so it is not mistaken for fresh evidence."""
    if available:
        return "STALE" if stale else "AVAILABLE"
    if key == "quant":
        st = (v.get("quantitative") or {}).get("status")
    elif key == "social":
        st = (v.get("social") or {}).get("status")
    elif key == "regime":
        st = (v.get("market_regime") or {}).get("status")
    else:
        st = None
    if st == "error":
        return "ERROR"
    return "NO_DATA"


def committee_decision(v: dict[str, Any] | None, stale: bool = False) -> dict[str, Any]:
    """Structured committee decision (thesis, cases, risks, catalysts, view-change).

    Deterministic and derived only from real signals/research; no fabricated
    precision. Missing signals are excluded; conflicts reduce conviction.
    Each signal carries an explicit status (AVAILABLE / NO_DATA / ERROR /
    STALE / DISABLED) so a missing signal is never mistaken for a neutral vote.
    """
    v = v or {}
    committee = v.get("committee") or committee_signals(v, None)
    factors = v.get("factors") or bull_bear_factors(v, None)
    research = v.get("research") or {}
    signals = {s["key"]: s for s in committee.get("signals", [])}
    verdict = committee.get("verdict")

    bull_case = list(factors.get("bull", [])) + list(research.get("bull_evidence") or [])
    bear_case = list(factors.get("bear", [])) + list(research.get("bear_evidence") or [])
    risks = list(factors.get("bear", [])) + list(research.get("risks") or [])
    catalysts = list(research.get("catalysts") or [])

    contributing = [
        {
            "key": k,
            "label": s["label"],
            "direction": s.get("state"),
            "score": s.get("score"),
            "confidence": s.get("confidence"),
            "status": _signal_status(k, v, s.get("available"), stale),
            "available": bool(s.get("available")),
        }
        for k, s in signals.items()
    ]

    security_id = f"{v.get('market') or ''}:{v.get('ticker') or ''}"
    signal_map = {}
    for key, canonical in (
        ("quant", "quantitative"),
        ("technical", "technical"),
        ("news", "news"),
        ("social", "social_momentum"),
        ("regime", "market_regime"),
    ):
        s = signals.get(key) or {}
        signal_map[canonical] = {
            "status": _signal_status(key, v, s.get("available"), stale),
            "direction": s.get("state"),
            "score": s.get("score"),
            "confidence": s.get("confidence"),
            "weight": s.get("weight"),
        }

    return {
        "security_id": security_id,
        "verdict": verdict,
        "conviction": committee.get("confidence"),
        "thesis": _thesis(signals, verdict),
        "bull_case": bull_case[:10],
        "bear_case": bear_case[:10],
        # Presentation additions (derived only from the SAME committee output —
        # no methodology, weight or renormalization change).
        "neutral_case": _neutral_case(signals, factors, verdict),
        "key_evidence": _key_evidence(signals, factors, committee),
        "disagreements": _disagreements(signals),
        "forecast_range": _forecast_range(v, signals),
        "why": _why_narrative(verdict, committee.get("confidence"), signals, factors),
        "key_disagreement": _key_disagreement(signals),
        "primary_risks": risks[:10],
        "catalysts": catalysts[:10],
        "view_changes_if": _view_changes_if(signals, verdict),
        "contributing_signals": contributing,
        "signals": signal_map,
        "research_confidence": research.get("confidence"),
        "research_status": research.get("status", "no_data"),
        "decision_timestamp": v.get("decided_at") or research.get("analyzed_at") or "",
        "status": "ok" if verdict not in (None, "N/A") else "no_data",
    }
