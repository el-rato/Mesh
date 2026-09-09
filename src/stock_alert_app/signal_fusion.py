from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .market_evidence import SignalEvidence, _native


@dataclass(frozen=True)
class FusionConfig:
    weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "ml": 0.22,
            "trend": 0.18,
            "momentum": 0.14,
            "volume": 0.10,
            "volatility": 0.08,
            "candlesticks": 0.10,
            "market_regime": 0.10,
            "relative_strength": 0.04,
            "market_context": 0.04,
        }
    )
    regime_multipliers: Mapping[str, Mapping[str, float]] = field(
        default_factory=lambda: {
            "TRENDING_BULL": {"trend": 1.35, "momentum": 1.15},
            "TRENDING_BEAR": {"trend": 1.35, "momentum": 1.15},
            "RANGE": {"trend": 0.70, "momentum": 0.80, "candlesticks": 1.25},
            "HIGH_VOL": {"ml": 0.70, "volatility": 1.35, "volume": 1.15},
            "LOW_VOL": {"volatility": 0.75, "trend": 1.10},
            "RISK_OFF": {"ml": 0.65, "market_regime": 1.50, "market_context": 1.35},
        }
    )
    neutral_band: float = 0.12
    directional_band: float = 0.16
    conflicted_threshold: float = 0.42
    minimum_directional_sources: int = 2


@dataclass(frozen=True)
class FusionResult:
    direction: str
    confidence: float
    agreement_score: float
    contradiction_score: float
    risk_level: str
    score: float
    regime: str
    regime_confidence: float
    supporting_evidence: tuple[dict[str, Any], ...]
    opposing_evidence: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    forecasts: tuple[dict[str, Any], ...]
    method: str = "weighted_fusion"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _row(item: SignalEvidence, weight: float, directional_score: float) -> dict[str, Any]:
    reasons = list(item.reasons)
    return {
        "category": item.engine,
        "direction": item.signal,
        "score": item.score,
        "confidence": item.confidence,
        "weight": round(weight, 4),
        "contribution": round(directional_score * item.confidence * weight, 4),
        "status": item.status,
        "reason": reasons[0] if reasons else item.signal.replace("_", " ").lower(),
        "reasons": reasons,
        "metrics": _native(dict(item.metrics)),
    }


def _ml_evidence(probabilities: Mapping[str, Mapping[str, Any]]) -> SignalEvidence | None:
    one_day = probabilities.get("1D") or {}
    probability = one_day.get("probability_up")
    if probability is None:
        return None
    p = _clamp(float(probability))
    calibrated = bool(one_day.get("calibrated"))
    confidence = _clamp(float(one_day.get("reliability", abs(p - 0.5) * 2.0)))
    if not calibrated:
        confidence = min(confidence, 0.45)
    score = 2.0 * p - 1.0
    signal = "BULL" if score > 0.05 else "BEAR" if score < -0.05 else "NEUTRAL"
    return SignalEvidence(
        engine="ml",
        signal=signal,
        score=score,
        confidence=confidence,
        horizon="1 trading day",
        metrics={
            "probability_up": p,
            "calibrated": calibrated,
            "model": one_day.get("model", ""),
            "model_version": one_day.get("model_version", ""),
        },
        reasons=(f"model P(up) {p:.1%}" + (" calibrated" if calibrated else " uncalibrated"),),
    )


def _forecasts(probabilities: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for horizon in ("1D", "5D", "20D"):
        source = probabilities.get(horizon) or {}
        probability = source.get("probability_up")
        if probability is None:
            rows.append({"horizon": horizon, "direction": "NO_DATA", "probability_up": None, "calibrated": False, "status": "no_data"})
            continue
        p = _clamp(float(probability))
        direction = "BULLISH" if p >= 0.55 else "BEARISH" if p <= 0.45 else "NEUTRAL"
        rows.append({
            "horizon": horizon,
            "direction": direction,
            "probability_up": round(p, 4),
            "calibrated": bool(source.get("calibrated")),
            "status": "ok",
            "model": source.get("model", ""),
            "model_version": source.get("model_version", ""),
            "as_of": source.get("as_of", ""),
        })
    return tuple(rows)


def fuse_signals(
    calibrated_probabilities: Mapping[str, Mapping[str, Any]],
    evidence: Iterable[SignalEvidence],
    *,
    relative_strength: SignalEvidence | None = None,
    market_context: SignalEvidence | None = None,
    config: FusionConfig | None = None,
) -> FusionResult:
    """Fuse independent evidence into an informational signal; never executes trades."""
    cfg = config or FusionConfig()
    items = list(evidence)
    if relative_strength is not None:
        items.append(relative_strength)
    if market_context is not None:
        items.append(market_context)
    ml = _ml_evidence(calibrated_probabilities)
    if ml is not None:
        items.append(ml)

    regime_item = next((x for x in items if x.engine == "market_regime" and x.status == "ok"), None)
    regime = regime_item.signal if regime_item else "UNKNOWN"
    regime_confidence = regime_item.confidence if regime_item else 0.0
    multipliers = cfg.regime_multipliers.get(regime, {})

    rows: list[dict[str, Any]] = []
    numerator = denominator = positive = negative = 0.0
    directional_sources = 0
    for item in items:
        if item.status != "ok" or item.engine not in cfg.weights:
            continue
        weight = max(0.0, float(cfg.weights[item.engine])) * float(multipliers.get(item.engine, 1.0))
        # Volatility is risk evidence, not a bullish/bearish vote.
        directional_score = 0.0 if item.engine == "volatility" else item.score
        row = _row(item, weight, directional_score)
        rows.append(row)
        effective = weight * item.confidence
        numerator += directional_score * effective
        denominator += effective
        magnitude = abs(directional_score) * effective
        if directional_score > 0.05:
            positive += magnitude
            directional_sources += 1
        elif directional_score < -0.05:
            negative += magnitude
            directional_sources += 1

    score = numerator / denominator if denominator else 0.0
    directional_total = positive + negative
    agreement = max(positive, negative) / directional_total if directional_total else 0.0
    contradiction = 2.0 * min(positive, negative) / directional_total if directional_total else 0.0
    coverage = min(1.0, len(rows) / max(1, len(cfg.weights)))
    confidence = (0.42 * abs(score) + 0.33 * agreement + 0.25 * coverage) * (1.0 - 0.65 * contradiction)
    confidence = min(confidence, 0.35 + 0.10 * max(0, directional_sources - 1))

    if not rows:
        direction = "NO_DATA"
    elif contradiction >= cfg.conflicted_threshold and positive > 0 and negative > 0:
        direction = "CONFLICTED"
    elif directional_sources < cfg.minimum_directional_sources:
        direction = "UNCERTAIN"
    elif abs(score) <= cfg.neutral_band:
        direction = "NEUTRAL"
    elif score >= cfg.directional_band and agreement >= 0.58:
        direction = "BULLISH"
    elif score <= -cfg.directional_band and agreement >= 0.58:
        direction = "BEARISH"
    else:
        direction = "UNCERTAIN"

    volatility_item = next((x for x in items if x.engine == "volatility" and x.status == "ok"), None)
    final_sign = 1 if score > 0 else -1 if score < 0 else 0
    supporting = [r for r in rows if final_sign and r["contribution"] * final_sign > 0.0]
    opposing = [r for r in rows if final_sign and r["contribution"] * final_sign < 0.0]
    if final_sign == 0:
        supporting = [r for r in rows if r["contribution"] > 0.0]
        opposing = [r for r in rows if r["contribution"] < 0.0]
    volatility_row = next((r for r in rows if r["category"] == "volatility"), None)
    if volatility_row and volatility_item:
        if volatility_item.signal == "HIGH_VOL":
            opposing.append(volatility_row)
        elif volatility_item.signal == "LOW_VOL":
            supporting.append(volatility_row)
    supporting.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    opposing.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)

    if regime in {"HIGH_VOL", "RISK_OFF"} or (volatility_item and volatility_item.signal == "HIGH_VOL") or contradiction >= 0.55:
        risk = "HIGH"
    elif regime == "LOW_VOL" and contradiction < 0.2 and coverage >= 0.5:
        risk = "LOW"
    else:
        risk = "MEDIUM"

    return FusionResult(
        direction=direction,
        confidence=round(_clamp(confidence), 4),
        agreement_score=round(_clamp(agreement), 4),
        contradiction_score=round(_clamp(contradiction), 4),
        risk_level=risk,
        score=round(max(-1.0, min(1.0, score)), 4),
        regime=regime,
        regime_confidence=round(_clamp(regime_confidence), 4),
        supporting_evidence=tuple(supporting[:5]),
        opposing_evidence=tuple(opposing[:5]),
        evidence=tuple(rows),
        forecasts=_forecasts(calibrated_probabilities),
    )
