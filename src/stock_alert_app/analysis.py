"""Canonical stock analysis builder.

Single source of truth for the analysis payload that powers Overview, Scanner,
Search, Watchlist, and the Stock Dossier stored path. Given a stored verdict row
(and optionally the latest price snapshot + institutional data) it produces the
same committee verdict, signal breakdown, factors, and timestamps everywhere, so
no two surfaces can disagree.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .dossier import bull_bear_factors, committee_signals
from .price import PriceState

_NEUTRAL_BAND = 0.05


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None and math.isfinite(float(value)) else default
    except (TypeError, ValueError):
        return default


def signal_from_lstm(row: dict[str, Any]) -> str:
    """BULL/BEAR from the stored LSTM score, or N/A when the model was unavailable."""
    score = float(row.get("lstm_score") or 0.0)
    prob = row.get("lstm_probability_up")
    p_up = float(prob) if prob is not None else None
    if score == 0.0 and p_up is None:
        return "N/A"
    return "BULL" if (score > 0 or (p_up is not None and p_up >= 0.5)) else "BEAR"


def _signal_agreement_from_row(row: dict[str, Any]) -> str:
    try:
        reason = str(row.get("reason") or "")
    except Exception:
        return "unknown"
    for token in ("Signal agreement: strong", "moderate", "mixed", "weak", "none"):
        if token in reason:
            return token.replace("Signal agreement: ", "")
    return "unknown"


def _news_available_from_reason(reason: str) -> bool:
    """Detect a usable news signal from a stored verdict's reason.

    Handles both the current engine format (``News: bearish (N articles, ...)``)
    and the legacy format (``Auxiliary News Sentiment: bullish (+0.154)``) so a
    valid news label is never reported as N/A.
    """
    reason = reason or ""
    if "News: unavailable" in reason:
        return False
    if "Auxiliary News Sentiment: None" in reason:
        return False
    if "News:" in reason:
        return True
    if "Auxiliary News Sentiment:" in reason:
        return True
    return False


def _news_label_from_reason(reason: str) -> str | None:
    m = re.search(r"(?:News:|Auxiliary News Sentiment:)\s*(\w+)", reason or "")
    return m.group(1).lower() if m else None


def _news_count_from_reason(reason: str) -> int | None:
    m = re.search(r"\((\d+)\s+articles", reason or "")
    return int(m.group(1)) if m else None


def verdict_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Map a stored verdict row to the canonical verdict block (mirrors Verdict.as_dict)."""
    reason = str(row.get("reason") or "")
    news_available = _news_available_from_reason(reason)
    news_label = _news_label_from_reason(reason)
    news_count = _news_count_from_reason(reason)
    return {
        "market": row["market"],
        "ticker": row["ticker"],
        "verdict": row["verdict"],
        "confidence": row["confidence"],
        "news_score": row["news_score"],
        "price_score": row["price_score"],
        "combined_score": row["combined_score"],
        "reason": [reason] if reason else [],
        "decided_at": row["decided_at"],
        "forecast_horizon": "1 trading day",
        "signal_agreement": _signal_agreement_from_row(row),
        "lstm": {
            "score": row["lstm_score"],
            "probability_up": row["lstm_probability_up"],
            "predicted_return": row["lstm_predicted_return"],
            "model_confidence": row["lstm_confidence"],
            "metrics": {},
            "model_version": "",
            "signal": signal_from_lstm(row),
        },
        "technical": {
            "score": row["technical_score"],
        },
        "news_available": news_available,
        "news_label": news_label,
        "news": (
            {
                "score": float(row.get("news_score") or 0.0),
                "label": news_label or "neutral",
                "article_count": news_count,
                "avg_confidence": None,
                "freshness": None,
            }
            if news_available
            else None
        ),
    }


def snapshot_price(snap: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map a stored price_snapshots row to PriceState.as_dict() shape."""
    if not snap:
        return None
    sma = float(snap.get("sma_50") or 0.0)
    close = float(snap.get("close") or 0.0)
    return {
        "symbol": snap.get("ticker") or "",
        "close": close,
        "open": float(snap.get("open") or 0.0),
        "high": float(snap.get("high") or 0.0),
        "low": float(snap.get("low") or 0.0),
        "volume": int(snap.get("volume") or 0),
        "momentum_20": float(snap.get("momentum_20") or 0.0),
        "rsi_14": float(snap.get("rsi_14") or 50.0),
        "sma_50": sma,
        "sma_200": 0.0,
        "trend_50_200": 0.0,
        "above_sma_50": close >= sma if sma else None,
    }


def technical_from_snapshot(snap: dict[str, Any] | None) -> tuple[float, list[str]]:
    """Recompute the technical signal from a price snapshot using the same
    normalization as the live verdict engine."""
    from .verdict import _normalize_price

    if not snap:
        return 0.0, ["no price snapshot available"]
    close = float(snap.get("close") or 0.0)
    sma = float(snap.get("sma_50") or 0.0)
    state = PriceState(
        market=snap.get("market") or "",
        ticker=snap.get("ticker") or "",
        close=close,
        open=float(snap.get("open") or 0.0),
        high=float(snap.get("high") or 0.0),
        low=float(snap.get("low") or 0.0),
        volume=int(snap.get("volume") or 0),
        momentum_20=float(snap.get("momentum_20") or 0.0),
        rsi_14=float(snap.get("rsi_14") or 50.0),
        sma_50=sma,
    )
    score, reasons = _normalize_price(state)
    return round(score, 4), reasons


def apply_canonical(
    vdict: dict[str, Any],
    institutional: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Overwrite a verdict dict with the single canonical committee result.

    The Investment Committee is the ONE verdict authority: the final verdict,
    confidence, and combined score are taken from ``committee_signals`` (which
    re-normalizes across whatever signals are available and re-weights them),
    and the same committee/factors dicts are attached for every surface.
    """
    committee = committee_signals(vdict, institutional)
    factors = bull_bear_factors(vdict, institutional)
    vdict["verdict"] = committee["verdict"]
    vdict["confidence"] = committee["confidence"]
    vdict["combined_score"] = committee["score"]
    vdict["committee"] = committee
    vdict["factors"] = factors
    return vdict


def stock_analysis(
    row: dict[str, Any],
    snap: dict[str, Any] | None = None,
    markets: dict[str, Any] | None = None,
    institutional: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical analysis payload for a stored verdict row.

    The committee verdict/confidence/score are recomputed from the latest price
    snapshot (technical signal refreshed) so every surface shows the same result.
    """
    v = verdict_row_to_dict(row)
    price = snapshot_price(snap)
    if price is not None:
        v["price"] = price
        technical_score, technical_reasons = technical_from_snapshot(snap)
        v["technical"] = {"score": technical_score, "reasons": technical_reasons}

    apply_canonical(v, institutional)

    decided_at = row.get("decided_at") or ""
    price_fetched_at = (snap or {}).get("fetched_at") or ""
    analyzed_at = max(decided_at, price_fetched_at) or ""
    symbol = str(row["ticker"])
    company = ""
    if markets:
        m = markets.get(row["market"])
        if m is not None:
            symbol = f"{row['ticker']}{m.yahoo_suffix or ''}"
            try:
                company = m.get_ticker(row["ticker"]).name or ""
            except KeyError:
                company = ""

    news = v.get("news") or {}
    momentum = _num(price.get("momentum_20")) if price else 0.0
    rsi = _num(price.get("rsi_14"), 50.0) if price else 50.0
    close = _num(price.get("close")) if price else 0.0

    committee = v.get("committee") or {}
    factors = v.get("factors") or {}

    return {
        "market": row["market"],
        "ticker": row["ticker"],
        "symbol": symbol,
        "company": company,
        "verdict": committee.get("verdict"),
        "confidence": committee.get("confidence"),
        "combined_score": committee.get("score"),
        "committee": committee,
        "factors": factors,
        "reason": v["reason"],
        "decided_at": decided_at,
        "price_fetched_at": price_fetched_at,
        "updated_at": analyzed_at,
        "analyzed_at": analyzed_at,
        "news_score": float(row.get("news_score") or 0.0),
        "price_score": float(row.get("price_score") or 0.0),
        "news_available": bool(v.get("news_available")),
        "signal_agreement": v["signal_agreement"],
        "forecast_horizon": v["forecast_horizon"],
        "lstm": v["lstm"],
        "technical": v["technical"],
        "news": news or {"score": 0.0},
        "price": price,
        "momentum_20": momentum,
        "rsi_14": rsi,
        "close": close,
    }
