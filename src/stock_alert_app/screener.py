"""Stock screener engine.

"Something happened. Help me find the securities worth investigating."

Operates over the application's dynamic universe (configured + discovered
securities, never a hardcoded list) and reuses the existing Committee/analysis
data instead of recomputing signals independently. Each result is enriched with
the same canonical analysis used everywhere else.

Filtering + presets configure the SAME underlying filters, so presets are cheap
and discoverable.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .config import settings
from .db import Database, utc_now

logger = logging.getLogger(__name__)

#: Presets -> filter overrides. Each preset just configures the existing filters.
PRESETS: dict[str, dict[str, Any]] = {
    "strong_bullish": {
        "verdict": "BULL", "min_conviction": 0.65, "min_momentum": 0.02,
        "above_sma": "true",
    },
    "bearish": {
        "verdict": "BEAR", "min_conviction": 0.60, "max_momentum": 0.0,
    },
    "unusual_activity": {
        "min_move": 0.03, "min_volume_ratio": 1.5,
    },
    "high_conviction": {"min_conviction": 0.75},
    "signal_conflict": {"conflict": "true"},
    "reversals": {"reversal": "true"},
    "needs_research": {"no_data_only": True, "research": "false"},
}

SORTS = {
    "conviction": lambda x: x["confidence"] or 0.0,
    "momentum": lambda x: x["momentum_20"] or 0.0,
    "move": lambda x: x["price_move"] or 0.0,
    "volume": lambda x: x["volume_ratio"] or 0.0,
    "agreement": lambda x: x["agreement"] or 0.0,
    "combined": lambda x: (x["combined_score"] if x["combined_score"] is not None else 0.0),
}


def _num(value: Any) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dir(score: Any) -> str | None:
    s = _num(score)
    if s > 0.05:
        return "BULL"
    if s < -0.05:
        return "BEAR"
    return "NEUTRAL"


def _match(value: str | None, query: str) -> bool:
    return query in (value or "").lower()


def _enrich(analysis: dict[str, Any], prev_price: dict[str, Any] | None) -> dict[str, Any]:
    """Add screener-specific fields derived from stored data (no new network)."""
    latest_price = analysis.get("price") or {}
    price_move = None
    volume_ratio = None
    if prev_price:
        prev_close = _num(prev_price.get("close"))
        cur_close = _num(latest_price.get("close"))
        if prev_close:
            price_move = cur_close / prev_close - 1.0
        prev_vol = _num(prev_price.get("volume"))
        cur_vol = _num(latest_price.get("volume"))
        if prev_vol > 0 and cur_vol > 0:
            volume_ratio = cur_vol / prev_vol

    decision = analysis.get("decision") or {}
    signals = decision.get("signals") or {}
    available = [s for s in signals.values() if s.get("status") == "AVAILABLE"]
    verdict = decision.get("verdict") or analysis.get("verdict")
    aligned = sum(1 for s in available if s.get("direction") == verdict) if verdict in ("BULL", "BEAR") else 0
    agreement = aligned / len(available) if available else None

    quant = analysis.get("quantitative") or {}
    news = analysis.get("news") or {}
    regime = analysis.get("market_regime") or {}
    technical = analysis.get("technical") or {}

    analysis["price_move"] = round(price_move, 6) if price_move is not None else None
    analysis["volume_ratio"] = round(volume_ratio, 6) if volume_ratio is not None else None
    analysis["above_sma"] = bool((latest_price or {}).get("above_sma_50"))
    analysis["agreement"] = agreement
    analysis["agreement_n"] = len(available)
    analysis["signal_dir"] = {
        "quant": quant.get("direction"),
        "technical": _dir(technical.get("score")),
        "news": _dir(news.get("score") if news else None),
        "social": (analysis.get("social") or {}).get("direction"),
        "regime": regime.get("direction"),
    }
    analysis["research_available"] = decision.get("research_status") == "ok"
    analysis["regime_direction"] = regime.get("direction")
    analysis["news_score"] = _num(news.get("score")) if news else None
    return analysis


def run(
    db: Database,
    market: str | None = None,
    q: str = "",
    sector: str = "",
    verdict: str = "",
    min_conviction: float = 0.0,
    min_momentum: float | None = None,
    max_momentum: float | None = None,
    min_move: float | None = None,
    min_volume_ratio: float | None = None,
    signal: str = "",
    signal_key: str = "quant",
    min_agreement: float | None = None,
    regime: str = "",
    above_sma: str = "",
    news_min: float | None = None,
    research: str = "",
    reversal: str = "",
    conflict: str = "",
    no_data_only: bool = False,
    sort: str = "combined",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Screen the dynamic universe with the given filters (presets resolved by
    the caller into filter values)."""
    from .analysis import stock_analysis
    from .universe import universe

    scanned_at = utc_now()
    query = (q or "").strip().lower()
    sector_query = (sector or "").strip().lower()
    mkt = market or None

    rows = db.latest_verdicts(market=mkt)
    latest = {(r["market"], r["ticker"].upper()): r for r in rows}
    prev_map = {
        (k[0], k[1]): prev
        for k, (_, prev) in db.verdict_pairs(market=mkt).items()
    }
    snap_pairs = db.price_snapshot_pairs(market=mkt)
    markets = {}
    try:
        from .markets import load_markets

        markets = load_markets(settings.markets_dir)
    except Exception:
        markets = {}

    out: list[dict[str, Any]] = []
    # Cap background analysis requests per screen so a single page load can never
    # hammer the price/news/LSTM providers. Deduped in the warmer, so this only
    # bounds the burst on the first open of a universe full of unanalyzed tickers.
    _WARM_CAP = 10
    warmed = 0

    def _warm(sec_market: str, sec_ticker: str, company: str, symbol: str) -> bool:
        nonlocal warmed
        if warmed >= _WARM_CAP:
            return False
        from . import refresh

        if refresh.enqueue_analysis(str(db.path), sec_market, sec_ticker, company, symbol or None):
            warmed += 1
            return True
        return False

    for sec in universe(db, mkt):
        sec_market = sec["market"]
        sec_ticker = sec["ticker"]
        sec_key = (sec_market, sec_ticker.upper())
        if query and not (
            _match(sec_ticker, query)
            or _match(sec.get("company"), query)
            or _match(sec.get("exchange"), query)
        ):
            continue
        if sector_query and not (
            _match(sec.get("exchange"), sector_query)
            or _match(sec.get("company"), sector_query)
        ):
            continue

        row = latest.get(sec_key)
        snap, prev_snap = snap_pairs.get(sec_key, (None, None))
        if row:
            analysis = stock_analysis(row, snap, markets)
            analysis["data_status"] = "ok"
            analysis["security"] = sec
            analysis["security_id"] = f"{sec_market}:{sec_ticker}"
            analysis["last_price_update"] = (snap or {}).get("fetched_at")
            analysis["scanner_updated_at"] = scanned_at
            # A stored verdict can still be empty (transient provider error on
            # the first pass): treat it as NO_DATA and warm it so the dossier
            # stops showing "no available data" on every open.
            if analysis.get("verdict") is None and analysis.get("price") is None:
                analysis["data_status"] = "no_data"
                from . import refresh

                analysis["warming"] = bool(refresh.is_warming(sec_market, sec_ticker)) or _warm(
                    sec_market, sec_ticker, sec.get("company") or "", sec.get("symbol") or ""
                )
        else:
            # No stored verdict yet. Warm it in the background (root cause of
            # permanent N/A rows): once analyzed it gains lightweight signal data
            # and appears in the default view automatically. Until then it stays
            # out of the default scanner — only the NO_DATA / NEEDS RESEARCH
            # preset surfaces unanalyzed securities explicitly, keeping the
            # screener lightweight (price/change/volume/ranking only).
            from . import refresh

            _warm(sec_market, sec_ticker, sec.get("company") or "", sec.get("symbol") or "")
            if not no_data_only:
                continue
            analysis = {
                "market": sec_market, "ticker": sec_ticker,
                "symbol": sec.get("symbol") or sec_ticker,
                "company": sec.get("company") or "",
                "verdict": None, "confidence": None, "combined_score": None,
                "price": None, "momentum_20": None, "decision": {},
                "quantitative": {}, "market_regime": {}, "social": {},
                "technical": {}, "news": None, "data_status": "no_data",
                "security": sec,
                "security_id": f"{sec_market}:{sec_ticker}",
                "last_price_update": (snap or {}).get("fetched_at"),
                "scanner_updated_at": scanned_at,
            }
            analysis["warming"] = bool(refresh.is_warming(sec_market, sec_ticker)) or True
            # Fall through to the shared enrichment + filter + append block below
            # so no_data rows still respect verdict / conviction / move filters.

        analysis = _enrich(analysis, prev_snap)
        prev_row = prev_map.get(sec_key)
        analysis["reversal"] = bool(
            prev_row
            and prev_row["verdict"] in ("BULL", "BEAR")
            and analysis["verdict"] in ("BULL", "BEAR")
            and prev_row["verdict"] != analysis["verdict"]
        )

        # ---- Filters ----
        if verdict and str(analysis.get("verdict") or "") != verdict.upper():
            continue
        if min_conviction > 0 and (analysis.get("confidence") or 0.0) < min_conviction:
            continue
        if min_momentum is not None and (analysis.get("momentum_20") or 0.0) < min_momentum:
            continue
        if max_momentum is not None and (analysis.get("momentum_20") or 0.0) > max_momentum:
            continue
        if min_move is not None:
            pm = analysis.get("price_move")
            if pm is None or pm < min_move:
                continue
        if min_volume_ratio is not None:
            vr = analysis.get("volume_ratio")
            if vr is None or vr < min_volume_ratio:
                continue
        if signal:
            sig_dir = analysis.get("signal_dir", {}).get(signal_key)
            if sig_dir != signal.upper():
                continue
        if min_agreement is not None:
            ag = analysis.get("agreement")
            if ag is None or ag < min_agreement:
                continue
        if regime:
            if analysis.get("regime_direction") != regime.upper():
                continue
        if above_sma:
            want = above_sma.lower() == "true"
            if analysis.get("above_sma") is not want:
                continue
        if news_min is not None:
            ns = analysis.get("news_score")
            if ns is None or ns < news_min:
                continue
        if research:
            want = research.lower() == "true"
            if analysis.get("research_available") is not want:
                continue
        if reversal and not analysis.get("reversal"):
            continue
        if conflict:
            dirs = [d for d in analysis.get("signal_dir", {}).values() if d in ("BULL", "BEAR")]
            if len({d for d in dirs}) < 2:
                continue
        out.append(analysis)

    key_fn = SORTS.get(sort, SORTS["combined"])
    out.sort(key=key_fn, reverse=True)
    return out[: int(limit)]


def apply_preset(preset: str) -> dict[str, Any]:
    """Resolve a named preset into filter overrides (they configure existing
    filters — there is no separate preset code path)."""
    return dict(PRESETS.get(preset, {}))
