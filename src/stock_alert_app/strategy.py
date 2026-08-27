"""Rotation & thematic strategy analytics (Fincept-style baskets).

When a user asks the agent to "analyze the X rotation" or "formulate a trading
strategy for the biotech sector", the agent needs more than a Q&A lookup: it
needs a *basket* of securities for the theme, their relative strength/leadership,
breadth, and the news flow — then it can synthesize a setup and a watchlist.

This module resolves a theme into a basket (curated map, falling back to a live
universe search), gathers each constituent's real terminal data (committee
verdict, momentum, news), and computes a structured rotation snapshot including
rules-based "setup" and "what to watch" bullets. The LLM — or the offline local
responder — turns that scaffold into a plain-language strategy. Nothing here is
fabricated: every number comes from stored terminal state or a live verdict.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: Curated thematic baskets (US-listed, market code NYSE). The agent falls back
#: to a live universe search for any theme not listed here, so this map only
#: needs to cover the common cases — it is not a closed universe.
THEME_BASKETS: dict[str, list[dict[str, str]]] = {
    "healthcare": [
        {"market": "NYSE", "ticker": "JNJ"}, {"market": "NYSE", "ticker": "UNH"},
        {"market": "NYSE", "ticker": "PFE"}, {"market": "NYSE", "ticker": "MRK"},
        {"market": "NYSE", "ticker": "ABBV"}, {"market": "NYSE", "ticker": "LLY"},
        {"market": "NYSE", "ticker": "TMO"}, {"market": "NYSE", "ticker": "ABT"},
        {"market": "NYSE", "ticker": "CVS"}, {"market": "NYSE", "ticker": "CI"},
        {"market": "NYSE", "ticker": "HCA"}, {"market": "NYSE", "ticker": "AMGN"},
    ],
    "biotech": [
        {"market": "NYSE", "ticker": "XBI"}, {"market": "NYSE", "ticker": "IBB"},
        {"market": "NYSE", "ticker": "AMGN"}, {"market": "NYSE", "ticker": "GILD"},
        {"market": "NYSE", "ticker": "VRTX"}, {"market": "NYSE", "ticker": "REGN"},
        {"market": "NYSE", "ticker": "MRNA"}, {"market": "NYSE", "ticker": "BIIB"},
        {"market": "NYSE", "ticker": "INCY"}, {"market": "NYSE", "ticker": "BMRN"},
        {"market": "NYSE", "ticker": "NVAX"}, {"market": "NYSE", "ticker": "ILMN"},
    ],
    "healthcare & biotech": [
        {"market": "NYSE", "ticker": "JNJ"}, {"market": "NYSE", "ticker": "UNH"},
        {"market": "NYSE", "ticker": "PFE"}, {"market": "NYSE", "ticker": "MRK"},
        {"market": "NYSE", "ticker": "ABBV"}, {"market": "NYSE", "ticker": "LLY"},
        {"market": "NYSE", "ticker": "TMO"}, {"market": "NYSE", "ticker": "AMGN"},
        {"market": "NYSE", "ticker": "GILD"}, {"market": "NYSE", "ticker": "VRTX"},
        {"market": "NYSE", "ticker": "REGN"}, {"market": "NYSE", "ticker": "MRNA"},
        {"market": "NYSE", "ticker": "BIIB"}, {"market": "NYSE", "ticker": "INCY"},
        {"market": "NYSE", "ticker": "XBI"}, {"market": "NYSE", "ticker": "IBB"},
    ],
    "pharma": [
        {"market": "NYSE", "ticker": "PFE"}, {"market": "NYSE", "ticker": "MRK"},
        {"market": "NYSE", "ticker": "LLY"}, {"market": "NYSE", "ticker": "JNJ"},
        {"market": "NYSE", "ticker": "ABBV"}, {"market": "NYSE", "ticker": "NVO"},
        {"market": "NYSE", "ticker": "GSK"}, {"market": "NYSE", "ticker": "AZN"},
        {"market": "NYSE", "ticker": "BMY"}, {"market": "NYSE", "ticker": "SAN"},
    ],
    "semiconductors": [
        {"market": "NYSE", "ticker": "NVDA"}, {"market": "NYSE", "ticker": "AMD"},
        {"market": "NYSE", "ticker": "INTC"}, {"market": "NYSE", "ticker": "QCOM"},
        {"market": "NYSE", "ticker": "AVGO"}, {"market": "NYSE", "ticker": "TXN"},
        {"market": "NYSE", "ticker": "MU"}, {"market": "NYSE", "ticker": "AMAT"},
        {"market": "NYSE", "ticker": "LRCX"}, {"market": "NYSE", "ticker": "KLAC"},
        {"market": "NYSE", "ticker": "ASML"}, {"market": "NYSE", "ticker": "ARM"},
        {"market": "NYSE", "ticker": "MRVL"}, {"market": "NYSE", "ticker": "TSM"},
    ],
    "ai": [
        {"market": "NYSE", "ticker": "NVDA"}, {"market": "NYSE", "ticker": "MSFT"},
        {"market": "NYSE", "ticker": "GOOGL"}, {"market": "NYSE", "ticker": "META"},
        {"market": "NYSE", "ticker": "AMZN"}, {"market": "NYSE", "ticker": "AMD"},
        {"market": "NYSE", "ticker": "AVGO"}, {"market": "NYSE", "ticker": "PLTR"},
        {"market": "NYSE", "ticker": "SMCI"}, {"market": "NYSE", "ticker": "TSM"},
        {"market": "NYSE", "ticker": "ORCL"}, {"market": "NYSE", "ticker": "CRM"},
    ],
    "technology": [
        {"market": "NYSE", "ticker": "AAPL"}, {"market": "NYSE", "ticker": "MSFT"},
        {"market": "NYSE", "ticker": "GOOGL"}, {"market": "NYSE", "ticker": "AMZN"},
        {"market": "NYSE", "ticker": "META"}, {"market": "NYSE", "ticker": "NVDA"},
        {"market": "NYSE", "ticker": "AMD"}, {"market": "NYSE", "ticker": "AVGO"},
        {"market": "NYSE", "ticker": "CRM"}, {"market": "NYSE", "ticker": "ADBE"},
        {"market": "NYSE", "ticker": "ORCL"}, {"market": "NYSE", "ticker": "CSCO"},
        {"market": "NYSE", "ticker": "INTC"}, {"market": "NYSE", "ticker": "QCOM"},
    ],
    "financials": [
        {"market": "NYSE", "ticker": "JPM"}, {"market": "NYSE", "ticker": "BAC"},
        {"market": "NYSE", "ticker": "WFC"}, {"market": "NYSE", "ticker": "GS"},
        {"market": "NYSE", "ticker": "MS"}, {"market": "NYSE", "ticker": "C"},
        {"market": "NYSE", "ticker": "BLK"}, {"market": "NYSE", "ticker": "SCHW"},
        {"market": "NYSE", "ticker": "AXP"}, {"market": "NYSE", "ticker": "SPGI"},
        {"market": "NYSE", "ticker": "ICE"}, {"market": "NYSE", "ticker": "CME"},
    ],
    "banks": [
        {"market": "NYSE", "ticker": "JPM"}, {"market": "NYSE", "ticker": "BAC"},
        {"market": "NYSE", "ticker": "WFC"}, {"market": "NYSE", "ticker": "C"},
        {"market": "NYSE", "ticker": "GS"}, {"market": "NYSE", "ticker": "MS"},
        {"market": "NYSE", "ticker": "USB"}, {"market": "NYSE", "ticker": "PNC"},
        {"market": "NYSE", "ticker": "TFC"}, {"market": "NYSE", "ticker": "COF"},
    ],
    "energy": [
        {"market": "NYSE", "ticker": "XOM"}, {"market": "NYSE", "ticker": "CVX"},
        {"market": "NYSE", "ticker": "COP"}, {"market": "NYSE", "ticker": "SLB"},
        {"market": "NYSE", "ticker": "OXY"}, {"market": "NYSE", "ticker": "EOG"},
        {"market": "NYSE", "ticker": "PSX"}, {"market": "NYSE", "ticker": "VLO"},
        {"market": "NYSE", "ticker": "BP"}, {"market": "NYSE", "ticker": "SHEL"},
    ],
    "consumer": [
        {"market": "NYSE", "ticker": "AMZN"}, {"market": "NYSE", "ticker": "WMT"},
        {"market": "NYSE", "ticker": "COST"}, {"market": "NYSE", "ticker": "TGT"},
        {"market": "NYSE", "ticker": "HD"}, {"market": "NYSE", "ticker": "LOW"},
        {"market": "NYSE", "ticker": "NKE"}, {"market": "NYSE", "ticker": "MCD"},
        {"market": "NYSE", "ticker": "SBUX"}, {"market": "NYSE", "ticker": "PG"},
        {"market": "NYSE", "ticker": "KO"}, {"market": "NYSE", "ticker": "PEP"},
    ],
    "retail": [
        {"market": "NYSE", "ticker": "AMZN"}, {"market": "NYSE", "ticker": "WMT"},
        {"market": "NYSE", "ticker": "COST"}, {"market": "NYSE", "ticker": "TGT"},
        {"market": "NYSE", "ticker": "HD"}, {"market": "NYSE", "ticker": "LOW"},
        {"market": "NYSE", "ticker": "NKE"}, {"market": "NYSE", "ticker": "MCD"},
        {"market": "NYSE", "ticker": "SBUX"}, {"market": "NYSE", "ticker": "TJX"},
        {"market": "NYSE", "ticker": "ROST"}, {"market": "NYSE", "ticker": "DLTR"},
    ],
    "crypto": [
        {"market": "NYSE", "ticker": "COIN"}, {"market": "NYSE", "ticker": "MSTR"},
        {"market": "NYSE", "ticker": "RIOT"}, {"market": "NYSE", "ticker": "MARA"},
        {"market": "NYSE", "ticker": "BITF"}, {"market": "NYSE", "ticker": "HUT"},
        {"market": "NYSE", "ticker": "CLSK"}, {"market": "NYSE", "ticker": "MOGO"},
    ],
    "ev": [
        {"market": "NYSE", "ticker": "TSLA"}, {"market": "NYSE", "ticker": "RIVN"},
        {"market": "NYSE", "ticker": "LCID"}, {"market": "NYSE", "ticker": "NIO"},
        {"market": "NYSE", "ticker": "XPEV"}, {"market": "NYSE", "ticker": "LI"},
        {"market": "NYSE", "ticker": "F"}, {"market": "NYSE", "ticker": "GM"},
    ],
    "cloud": [
        {"market": "NYSE", "ticker": "AMZN"}, {"market": "NYSE", "ticker": "MSFT"},
        {"market": "NYSE", "ticker": "GOOGL"}, {"market": "NYSE", "ticker": "CRM"},
        {"market": "NYSE", "ticker": "ORCL"}, {"market": "NYSE", "ticker": "NET"},
        {"market": "NYSE", "ticker": "SNOW"}, {"market": "NYSE", "ticker": "DDOG"},
        {"market": "NYSE", "ticker": "ZS"}, {"market": "NYSE", "ticker": "MDB"},
    ],
    "fintech": [
        {"market": "NYSE", "ticker": "PYPL"}, {"market": "NYSE", "ticker": "SQ"},
        {"market": "NYSE", "ticker": "AFRM"}, {"market": "NYSE", "ticker": "COIN"},
        {"market": "NYSE", "ticker": "SOFI"}, {"market": "NYSE", "ticker": "HOOD"},
        {"market": "NYSE", "ticker": "UPST"}, {"market": "NYSE", "ticker": "V"},
        {"market": "NYSE", "ticker": "MA"}, {"market": "NYSE", "ticker": "AXP"},
    ],
    "aerospace & defense": [
        {"market": "NYSE", "ticker": "LMT"}, {"market": "NYSE", "ticker": "RTX"},
        {"market": "NYSE", "ticker": "NOC"}, {"market": "NYSE", "ticker": "BA"},
        {"market": "NYSE", "ticker": "GD"}, {"market": "NYSE", "ticker": "HII"},
        {"market": "NYSE", "ticker": "TXT"}, {"market": "NYSE", "ticker": "LDOS"},
    ],
    "real estate": [
        {"market": "NYSE", "ticker": "O"}, {"market": "NYSE", "ticker": "AMT"},
        {"market": "NYSE", "ticker": "PLD"}, {"market": "NYSE", "ticker": "CCI"},
        {"market": "NYSE", "ticker": "EQIX"}, {"market": "NYSE", "ticker": "PSA"},
        {"market": "NYSE", "ticker": "SPG"}, {"market": "NYSE", "ticker": "WELL"},
        {"market": "NYSE", "ticker": "DLR"}, {"market": "NYSE", "ticker": "AVB"},
    ],
    "utilities": [
        {"market": "NYSE", "ticker": "NEE"}, {"market": "NYSE", "ticker": "DUK"},
        {"market": "NYSE", "ticker": "SO"}, {"market": "NYSE", "ticker": "D"},
        {"market": "NYSE", "ticker": "AEP"}, {"market": "NYSE", "ticker": "EXC"},
        {"market": "NYSE", "ticker": "SRE"}, {"market": "NYSE", "ticker": "XEL"},
    ],
    "industrials": [
        {"market": "NYSE", "ticker": "CAT"}, {"market": "NYSE", "ticker": "DE"},
        {"market": "NYSE", "ticker": "HON"}, {"market": "NYSE", "ticker": "UNP"},
        {"market": "NYSE", "ticker": "UPS"}, {"market": "NYSE", "ticker": "GE"},
        {"market": "NYSE", "ticker": "RTX"}, {"market": "NYSE", "ticker": "FDX"},
    ],
    "materials": [
        {"market": "NYSE", "ticker": "LIN"}, {"market": "NYSE", "ticker": "SHW"},
        {"market": "NYSE", "ticker": "APD"}, {"market": "NYSE", "ticker": "ECL"},
        {"market": "NYSE", "ticker": "NEM"}, {"market": "NYSE", "ticker": "FCX"},
        {"market": "NYSE", "ticker": "DOW"}, {"market": "NYSE", "ticker": "DD"},
        {"market": "NYSE", "ticker": "NUE"}, {"market": "NYSE", "ticker": "VMC"},
    ],
    "gold & mining": [
        {"market": "NYSE", "ticker": "NEM"}, {"market": "NYSE", "ticker": "GOLD"},
        {"market": "NYSE", "ticker": "AEM"}, {"market": "NYSE", "ticker": "FNV"},
        {"market": "NYSE", "ticker": "WPM"}, {"market": "NYSE", "ticker": "AU"},
        {"market": "NYSE", "ticker": "KGC"}, {"market": "NYSE", "ticker": "RGLD"},
    ],
    "china": [
        {"market": "NYSE", "ticker": "BABA"}, {"market": "NYSE", "ticker": "JD"},
        {"market": "NYSE", "ticker": "PDD"}, {"market": "NYSE", "ticker": "NIO"},
        {"market": "NYSE", "ticker": "XPEV"}, {"market": "NYSE", "ticker": "LI"},
        {"market": "NYSE", "ticker": "BIDU"}, {"market": "NYSE", "ticker": "YUMC"},
        {"market": "NYSE", "ticker": "FUTU"}, {"market": "NYSE", "ticker": "TIGR"},
    ],
    "india": [
        {"market": "NYSE", "ticker": "INFY"}, {"market": "NYSE", "ticker": "WIT"},
        {"market": "NYSE", "ticker": "HDB"}, {"market": "NYSE", "ticker": "IBN"},
        {"market": "NYSE", "ticker": "RDY"},
    ],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 &]", " ", (s or "").lower()).strip()


def _dir(score: float | None) -> str:
    """Map a signed score to a Committee direction (Fincept thresholds)."""
    s = float(score) if isinstance(score, (int, float)) else 0.0
    if s > 0.05:
        return "BULL"
    if s < -0.05:
        return "BEAR"
    return "NEUTRAL"


#: Keyword fingerprints per canonical theme. Used to derive a basket from ANY
#: market's actual listed universe (not just the curated US baskets), so a
#: rotation can be analyzed in BSE, LSE, TSE, XETRA, HKEX, ... out of the box.
THEME_KEYWORDS: dict[str, list[str]] = {
    "healthcare": ["health", "healthcare", "medical", "hospital", "clinic", "care",
                   "diagnostic", "therap", "medicine", "wellness", "bioscience"],
    "biotech": ["biotech", "bio", "genomic", "genetic", "life science", "therapeutic"],
    "pharma": ["pharma", "drug", "medicine", "biologics", "therapeutic", "bioscience"],
    "semiconductors": ["semiconductor", "semicon", "chip", "wafer", "fab", "microchip",
                       "integrated circuit"],
    "ai": ["artificial intelligence", "machine learning", "neural", "robotics", " ai "],
    "technology": ["technolog", "software", "systems", "electronics", "computer",
                   "internet", "semiconductor", "data", "it services", "cloud"],
    "financials": ["financ", "bank", "banc", "capital", "credit", "invest", "asset",
                   "insurance", "holdings", "securities", "bourse"],
    "banks": ["bank", "banc", "credit", "savings", "trust", "financial"],
    "energy": ["energy", "oil", "gas", "petro", "coal", "solar", "wind", "power",
               "electric", "renewable", "utility"],
    "utilities": ["utility", "power", "electric", "water", "grid", "gas distribution"],
    "consumer": ["consumer", "retail", "food", "beverage", "apparel", "clothing",
                 "restaurant", "personal care", "household", "store"],
    "retail": ["retail", "store", "shop", "commerce", "marketplace", "consumer"],
    "crypto": ["crypto", "blockchain", "bitcoin", "digital asset", "web3", "ethereum"],
    "ev": ["electric vehicle", "ev", "automobile", "automotive", "auto", "car",
           "mobility", "battery"],
    "cloud": ["cloud", "saas", "software", "platform"],
    "fintech": ["fintech", "payment", "pay", "wallet", "blockchain", "digital bank"],
    "aerospace & defense": ["aerospace", "defense", "defence", "aircraft", "aviation",
                            "military", "space", "weapon"],
    "real estate": ["real estate", "realty", "property", "reit", "land", "housing", "estate"],
    "industrials": ["industrial", "machinery", "engineering", "construction",
                    "manufacturing", "logistics", "transport", "equipment"],
    "materials": ["material", "chemical", "mining", "metal", "cement", "steel",
                  "aluminum", "copper", "gold", "silver", "ore"],
    "gold & mining": ["mining", "gold", "silver", "copper", "coal", "iron", "ore", "metal", "mineral"],
    "china": ["china", "chinese"],
    "india": ["india", "indian"],
    "telecom": ["telecom", "telecommunication", "communication", "network", "mobile", "wireless"],
    "insurance": ["insurance", "assurance", "reinsurance"],
    "auto": ["automobile", "automotive", "auto", "car", "vehicle", "motor"],
}

#: Convenience aliases so common phrasings resolve to the curated baskets.
THEME_BASKETS["electric vehicle"] = THEME_BASKETS["ev"]
THEME_BASKETS["tech"] = THEME_BASKETS["technology"]
THEME_BASKETS["semis"] = THEME_BASKETS["semiconductors"]
THEME_BASKETS["health"] = THEME_BASKETS["healthcare"]

#: Loosely-related canonical themes that should share keyword fingerprints
#: (e.g. banks <-> financials, healthcare <-> pharma/biotech). Used to broaden
#: ticker matching WITHOUT the cross-theme leakage of scanning every keyword
#: against the raw theme string.
THEME_RELATED: dict[str, list[str]] = {
    "banks": ["financials"],
    "financials": ["banks"],
    "healthcare": ["pharma", "biotech"],
    "biotech": ["healthcare", "pharma"],
    "pharma": ["healthcare", "biotech"],
    "technology": ["semiconductors", "ai", "cloud"],
    "semiconductors": ["technology", "ai"],
    "ai": ["technology", "semiconductors"],
    "energy": ["utilities"],
    "utilities": ["energy"],
    "real estate": ["industrials"],
    "industrials": ["materials", "real estate"],
    "materials": ["gold & mining", "industrials"],
    "gold & mining": ["materials"],
    "ev": ["auto", "technology"],
    "auto": ["ev", "technology"],
    "fintech": ["financials", "banks"],
    "retail": ["consumer"],
    "consumer": ["retail"],
}


def list_themes() -> dict[str, list[str]]:
    """Theme names plus the supported markets for rotation analysis.

    Any theme can be scoped to a specific market (e.g. BSE, LSE, TSE), and the
    basket is derived from that market's actual listed universe.
    """
    from .markets import scan_market_codes

    return {
        "themes": sorted(THEME_BASKETS.keys()),
        "markets": scan_market_codes(settings.markets_dir),
    }


def _theme_keywords(theme: str) -> set[str]:
    """Collect the keyword fingerprint for a user theme string.

    Canonical themes are matched by NAME (not by scanning every keyword against
    the theme text), so a keyword like 'car' from the EV theme cannot leak into
    'healthcare'. The chosen canons' keyword lists are unioned, plus the literal
    words of the request as a fallback.
    """
    norm = _norm(theme)
    words = norm.split()
    matched: set[str] = set()
    for _canon in THEME_KEYWORDS:
        if _canon in norm or norm in _canon or set(_canon.split()).issubset(words):
            matched.add(_canon)
    # Expand to related themes (one hop) for broader ticker matching.
    expanded = set(matched)
    for _c in list(expanded):
        for _r in THEME_RELATED.get(_c, []):
            expanded.add(_r)
    kws: set[str] = set()
    for _c in expanded:
        kws.update(THEME_KEYWORDS.get(_c, []))
    if not kws:
        kws.update(w for w in words if w)
    return kws


def _basket_from_markets(
    market_codes: list[str] | None, keywords: set[str], limit: int = 40
) -> list[dict[str, str]]:
    """Derive a basket by keyword-matching each market's listed universe."""
    from .markets import load_markets, scan_market_codes

    md = settings.markets_dir
    codes = list(market_codes) if market_codes else scan_market_codes(md)
    markets = load_markets(md)
    basket: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for code in codes:
        m = markets.get(code)
        if not m:
            continue
        for sym, tkr in m.tickers.items():
            hay = " ".join(
                [
                    (tkr.name or ""),
                    " ".join(getattr(tkr, "keywords", []) or []),
                    sym,
                    (tkr.symbol or ""),
                ]
            ).lower()
            if any(k in hay for k in keywords):
                key = (code, sym.upper())
                if key in seen:
                    continue
                seen.add(key)
                basket.append(
                    {"market": code, "ticker": sym.upper(), "company": tkr.name or ""}
                )
                if len(basket) >= limit:
                    return basket
    return basket


def _curated_union(norm: str) -> list[dict[str, str]]:
    words = set(norm.split())
    matched = [k for k in THEME_BASKETS if words.issuperset(k.split())]
    if not matched:
        matched = [k for k in THEME_BASKETS if k in norm or norm in k]
    if not matched:
        return []
    seen: set[tuple[str, str]] = set()
    basket: list[dict[str, str]] = []
    for k in matched:
        for t in THEME_BASKETS[k]:
            key = (t["market"], t["ticker"])
            if key not in seen:
                seen.add(key)
                basket.append(dict(t))
    return basket


def _search_basket(theme: str, market: str | None) -> list[dict[str, str]]:
    try:
        from . import instruments

        results = instruments.search_universe(theme, limit=15, market_filter=market)
        if results:
            return [
                {"market": r["market"], "ticker": r["ticker"], "company": r.get("company", "")}
                for r in results
            ]
    except Exception as exc:
        logger.warning("Theme search failed for %r: %s", theme, exc)
    return []


def _resolve_basket(theme: str, market: str | None = None) -> tuple[list[dict[str, str]], str]:
    """Resolve a theme/rotation name into a basket of {market, ticker, company?}.

    Returns (basket, source) where source is 'curated', 'market-derived' or
    'search'. When a market is specified, the basket is drawn from that market's
    own listings; otherwise curated global leaders are preferred, falling back to
    an all-market keyword derivation and finally a live universe search.
    """
    norm = _norm(theme)
    keywords = _theme_keywords(theme)

    if market:
        # Scope to the requested market's listed universe, but also fold in any
        # curated leaders that are listed in this market so we never lose the
        # well-known names (e.g. TSLA for NYSE:electric vehicle).
        derived = _basket_from_markets([market], keywords, limit=60)
        cu = _curated_union(norm)
        cu = [t for t in cu if t["market"].upper() == market.upper()] if cu else []
        seen = {(b["market"], b["ticker"]) for b in derived}
        merged = list(derived)
        for t in cu:
            if (t["market"], t["ticker"]) not in seen:
                merged.append(t)
                seen.add((t["market"], t["ticker"]))
        if merged:
            return merged, ("market-derived" if derived else "curated")
        sb = _search_basket(theme, market)
        return (sb, "search") if sb else ([], "none")

    # No market: prefer curated global leaders, then derive across all markets.
    if norm in THEME_BASKETS:
        return [dict(t) for t in THEME_BASKETS[norm]], "curated"
    cu = _curated_union(norm)
    if cu:
        return cu, "curated"
    basket = _basket_from_markets(None, keywords, limit=40)
    if basket:
        return basket, "market-derived"
    sb = _search_basket(theme, None)
    return (sb, "search") if sb else ([], "none")


def _committee_score(c: dict[str, Any]) -> tuple[float | None, str | None]:
    """Fincept-style Committee score: weighted blend of available signals.

    Uses the configured multi-signal weights (LSTM / technical / news) and
    *renormalizes* over whichever signals are actually present for the security —
    a missing signal is never treated as a bullish or bearish input (Fincept
    principle: missing capabilities are renormalized, not fabricated).
    """
    w_lstm = getattr(settings, "lstm_weight", 0.60)
    w_tech = getattr(settings, "technical_weight", 0.25)
    w_news = getattr(settings, "news_weight", 0.15)

    parts: list[tuple[float, float]] = []
    # LSTM / quant direction (probability in [0,1] -> [-1,1], or already signed).
    p = c.get("lstm_probability_up")
    if p is None:
        p = c.get("lstm_score")
    if p is not None:
        try:
            pv = float(p)
            nv = pv * 2 - 1 if 0.0 <= pv <= 1.0 else max(-1.0, min(1.0, pv))
            parts.append((nv, w_lstm))
        except (TypeError, ValueError):
            pass
    # Technical / momentum direction.
    ts = c.get("technical_score")
    if ts is not None:
        try:
            tv = max(-1.0, min(1.0, float(ts)))
            parts.append((tv, w_tech))
        except (TypeError, ValueError):
            pass
    # News / sentiment direction.
    ns = c.get("news_score")
    if ns is not None:
        try:
            nv2 = max(-1.0, min(1.0, float(ns)))
            parts.append((nv2, w_news))
        except (TypeError, ValueError):
            pass
    if not parts:
        cs = c.get("combined_score")
        if cs is not None:
            try:
                return float(cs), _dir(cs)
            except (TypeError, ValueError):
                return None, None
        return None, None
    total_w = sum(w for _, w in parts)
    score = sum(val * w for val, w in parts) / total_w if total_w else 0.0
    return round(score, 4), _dir(score)


def _market_regime(db: Database) -> dict[str, Any]:
    """Derive a coarse market regime from stored index snapshots."""
    try:
        snaps = {s["symbol"]: s for s in db.latest_index_snapshots(market="NYSE")}
    except Exception:
        snaps = {}
    spx = snaps.get("^GSPC") or snaps.get("SPY")
    vix = snaps.get("^VIX")
    spx_chg = float(spx["change_pct"]) if spx and spx.get("change_pct") is not None else None
    vix_val = float(vix["close"]) if vix and vix.get("close") is not None else None
    label = "unknown"
    if spx_chg is not None:
        if spx_chg > 0.5:
            label = "risk-on / bullish"
        elif spx_chg < -0.5:
            label = "risk-off / bearish"
        else:
            label = "choppy / neutral"
        if vix_val and vix_val > 25:
            label += " (elevated vol)"
    return {"spx_change_pct": spx_chg, "vix": vix_val, "label": label}


def _constituent_from_stored(
    market: str, ticker: str, company: str, verdict_row: dict | None, snap: dict | None
) -> dict[str, Any]:
    """Build a constituent record from a stored verdict row + price snapshot."""
    out: dict[str, Any] = {
        "market": market,
        "ticker": ticker,
        "company": company or (verdict_row or {}).get("company", "") or "",
        "data_status": "no_data",
        "verdict": None,
        "confidence": None,
        "combined_score": None,
        "news_score": None,
        "price_score": None,
        "momentum_20": None,
        "rsi_14": None,
        "sma_50": None,
        "close": None,
        "above_sma": None,
        "relative_strength": None,
        "headlines": [],
    }
    if verdict_row:
        out.update(
            {
                "data_status": "ok",
                "verdict": verdict_row.get("verdict"),
                "confidence": verdict_row.get("confidence"),
                "combined_score": verdict_row.get("combined_score"),
                "news_score": verdict_row.get("news_score"),
                "price_score": verdict_row.get("price_score"),
                "lstm_probability_up": verdict_row.get("lstm_probability_up"),
                "technical_score": verdict_row.get("technical_score"),
            }
        )
    if snap:
        close = snap.get("close")
        sma = snap.get("sma_50")
        out.update(
            {
                "momentum_20": snap.get("momentum_20"),
                "rsi_14": snap.get("rsi_14"),
                "sma_50": sma,
                "close": close,
                "above_sma": (close > sma) if (close is not None and sma) else None,
            }
        )
    return out


def analyze_rotation(
    theme: str,
    market: str | None = None,
    tickers: list[str] | None = None,
    analyze: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Build a structured rotation snapshot for a theme/sector.

    Resolves the basket, gathers each constituent's real terminal data, and
    computes breadth, leadership/laggards, relative strength, news flow, and
    rules-based "setup" / "what to watch" bullets the agent can narrate.
    """
    if not theme and not tickers:
        return {"error": "provide a theme or a list of tickers"}

    db = Database(db_path or settings.db_path)
    db.init_schema()

    basket, source = _resolve_basket(theme, market) if theme else ([], "none")
    if tickers:
        for t in tickers:
            if ":" in t:
                mkt, tkr = t.split(":", 1)
            else:
                mkt, tkr = (market or "NYSE"), t
            basket.append({"market": mkt.upper(), "ticker": tkr.upper()})
    if market:
        basket = [b for b in basket if b["market"].upper() == market.upper()]
    if not basket:
        return {"error": f"could not resolve a basket for theme '{theme}'"}

    # Register constituents so future background refreshes cover them.
    for b in basket:
        try:
            db.upsert_security(b["market"], b["ticker"], symbol=b["ticker"], source="theme")
        except Exception:
            pass

    verdicts = {f"{r['market']}:{r['ticker'].upper()}": r for r in db.latest_verdicts()}
    snaps = {
        f"{s['market']}:{s['ticker'].upper()}": s for s in db.latest_price_snapshots()
    }

    constituents: list[dict[str, Any]] = []
    for b in basket:
        key = f"{b['market']}:{b['ticker'].upper()}"
        comp = _constituent_from_stored(
            b["market"], b["ticker"].upper(), b.get("company", ""),
            verdicts.get(key), snaps.get(key),
        )
        constituents.append(comp)

    # If nothing is stored yet, compute a bounded set of fresh live verdicts so
    # the rotation still returns real data (capped to avoid runaway latency).
    n_data = sum(1 for c in constituents if c["data_status"] == "ok")
    if n_data == 0:
        from .verdict import live_verdict

        for c in constituents[:12]:
            try:
                v = live_verdict(c["market"], c["ticker"], c["company"])
            except Exception as exc:
                logger.warning("Live verdict failed for %s:%s: %s", c["market"], c["ticker"], exc)
                v = None
            if v is None:
                continue
            c["data_status"] = "ok"
            c["verdict"] = v.verdict
            c["confidence"] = v.confidence
            c["combined_score"] = v.combined_score
            c["news_score"] = v.news_score
            c["price_score"] = v.price_score
            c["lstm_probability_up"] = getattr(v, "lstm_probability_up", None)
            c["technical_score"] = getattr(v, "technical_score", None)
            if v.price:
                c["momentum_20"] = v.price.momentum_20
                c["rsi_14"] = v.price.rsi_14
                c["sma_50"] = v.price.sma_50
                c["close"] = v.price.close
                c["above_sma"] = (
                    v.price.close > v.price.sma_50 if v.price.sma_50 else None
                )

    # News flow per constituent (top 2 each).
    for c in constituents:
        if c["data_status"] == "ok":
            try:
                rows = db.recent_news(c["market"], c["ticker"], limit=2)
                c["headlines"] = [
                    {
                        "title": r.get("title", ""),
                        "source": r.get("source", ""),
                        "sentiment": r.get("sentiment_label", "") or "",
                    }
                    for r in rows
                ]
            except Exception:
                c["headlines"] = []

    # ---- Aggregates ----
    with_data = [c for c in constituents if c["data_status"] == "ok"]
    moms = [c["momentum_20"] for c in with_data if c["momentum_20"] is not None]
    avg_mom = round(sum(moms) / len(moms), 4) if moms else None
    combined = [c["combined_score"] for c in with_data if c["combined_score"] is not None]
    avg_combined = round(sum(combined) / len(combined), 4) if combined else None
    news = [c["news_score"] for c in with_data if c["news_score"] is not None]
    avg_news = round(sum(news) / len(news), 4) if news else None

    for c in with_data:
        c["relative_strength"] = (
            round(c["momentum_20"] - avg_mom, 4)
            if (c["momentum_20"] is not None and avg_mom is not None)
            else None
        )

    # ---- Fincept Investment Committee synthesis ----
    for c in with_data:
        cs, cv = _committee_score(c)
        c["committee_score"] = cs
        c["committee_verdict"] = cv

    comm_scores = [c["committee_score"] for c in with_data if c["committee_score"] is not None]
    avg_committee = round(sum(comm_scores) / len(comm_scores), 4) if comm_scores else None
    bull_c = sum(1 for c in with_data if c.get("committee_verdict") == "BULL")
    bear_c = sum(1 for c in with_data if c.get("committee_verdict") == "BEAR")
    neutral_c = sum(1 for c in with_data if c.get("committee_verdict") == "NEUTRAL")
    pct_bull_committee = round(bull_c / len(with_data), 3) if with_data else 0.0
    if avg_committee is not None:
        if avg_committee > 0.1 and pct_bull_committee >= 0.5:
            group_committee = "BULL"
        elif avg_committee < -0.1:
            group_committee = "BEAR"
        else:
            group_committee = "NEUTRAL"
    else:
        group_committee = "NEUTRAL"

    by_comm = sorted(with_data, key=lambda c: c["committee_score"] or -1e9, reverse=True)
    comm_leaders = by_comm[:3]
    comm_laggards = list(reversed(by_comm[-3:])) if len(by_comm) >= 3 else []

    regime = _market_regime(db)

    # ---- Smart money (13F) context, stored filings only ----
    smart_money: list[dict[str, Any]] = []
    for c in with_data:
        try:
            from .institutional import ticker_institutional

            inst = ticker_institutional(c["ticker"], db)
        except Exception:
            inst = None
        if inst:
            c["institutional"] = {
                "holding_funds": inst.get("holding_funds"),
                "net": inst.get("net"),
            }
            if inst.get("net"):
                smart_money.append(
                    {"ticker": c["ticker"], "net": inst.get("net"), "funds": inst.get("holding_funds")}
                )
    smart_money.sort(key=lambda x: x["net"], reverse=True)

    bull = sum(1 for c in with_data if str(c["verdict"]).upper() == "BULL")
    bear = sum(1 for c in with_data if str(c["verdict"]).upper() == "BEAR")
    neutral = sum(1 for c in with_data if str(c["verdict"]).upper() == "NEUTRAL")
    pct_bull = round(bull / len(with_data), 3) if with_data else 0.0

    by_mom = sorted(with_data, key=lambda c: c["momentum_20"] or -1e9, reverse=True)
    leaders = by_mom[:3]
    laggards = list(reversed(by_mom[-3:])) if len(by_mom) >= 3 else []

    # Top news across the basket (most recent / most sentiment-laden first).
    flat_news = []
    for c in with_data:
        for h in c.get("headlines", []):
            flat_news.append({**h, "ticker": c["ticker"]})
    top_news = flat_news[:8]

    # ---- Rules-based narrative scaffold ----
    setup: list[str] = []
    if with_data:
        if pct_bull >= 0.6:
            breadth_label = "broad-based bullish leadership"
        elif pct_bull <= 0.3:
            breadth_label = "defensive / bearish tilt"
        else:
            breadth_label = "mixed, no clear consensus"
        setup.append(
            f"Breadth: {bull} bullish / {bear} bearish / {neutral} neutral "
            f"({int(pct_bull*100)}% bullish) — rotation shows {breadth_label}."
        )
        if avg_mom is not None:
            setup.append(
                f"Average 20-day momentum is {avg_mom:+.2%}"
                + (" (positive — the group is trending up)." if avg_mom > 0 else " (negative — the group is under pressure).")
            )
        if avg_news is not None:
            ns = "positive" if avg_news > 0.1 else ("negative" if avg_news < -0.1 else "balanced")
            setup.append(f"Average news sentiment is {avg_news:+.2f} ({ns} flow).")
        if leaders:
            setup.append(
                "Leadership: "
                + ", ".join(
                    f"{c['ticker']} ({c['momentum_20']:+.2%})" for c in leaders if c["momentum_20"] is not None
                )
                + "."
            )
        if laggards:
            setup.append(
                "Laggards: "
                + ", ".join(
                    f"{c['ticker']} ({c['momentum_20']:+.2%})" for c in laggards if c["momentum_20"] is not None
                )
                + "."
            )
        above = [c["ticker"] for c in with_data if c["above_sma"] is True]
        below = [c["ticker"] for c in with_data if c["above_sma"] is False]
        if above or below:
            setup.append(
                f"{len(above)} of {len(with_data)} hold above their SMA50 "
                f"({', '.join(above[:6]) or 'none'}); "
                f"{len(below)} sit below ({', '.join(below[:6]) or 'none'})."
            )
        # Fincept Investment Committee (multi-signal, renormalized) read.
        if avg_committee is not None:
            setup.append(
                f"Investment Committee (LSTM/technical/news, renormalized over available "
                f"signals): {group_committee} with avg score {avg_committee:+.2f} "
                f"({int(pct_bull_committee*100)}% of names bullish on the committee lens)."
            )
        if comm_leaders:
            setup.append(
                "Committee leadership: "
                + ", ".join(
                    f"{c['ticker']} ({c['committee_score']:+.2f})"
                    for c in comm_leaders
                    if c["committee_score"] is not None
                )
                + "."
            )
        if smart_money:
            top_sm = smart_money[0]
            setup.append(
                f"Smart-money (13F) tilt: {top_sm['ticker']} shows net "
                f"{'adds' if top_sm['net'] > 0 else 'trims'} across {top_sm['funds']} tracked funds"
                + (f"; {len(smart_money)} names with active 13F flows." if len(smart_money) > 1 else ".")
            )
        if regime.get("label") not in (None, "unknown"):
            setup.append(
                f"Market regime: {regime['label']} (S&P {regime.get('spx_change_pct')}%, "
                f"VIX {regime.get('vix')})."
            )
    else:
        setup.append("No stored analysis yet for this basket — run a refresh, then re-ask.")

    what_to_watch: list[str] = []
    if with_data:
        if leaders:
            what_to_watch.append(
                "Leadership confirmation: " + ", ".join(c["ticker"] for c in leaders)
                + " holding above their SMA50 and momentum is the signal the rotation is strengthening."
            )
        if laggards:
            what_to_watch.append(
                "Weak-link risk: a break below SMA50 in "
                + ", ".join(c["ticker"] for c in laggards)
                + " would flag fading participation."
            )
        what_to_watch.append(
            "Breadth trigger: a rise above 60% bullish confirms the rotation; a drop below 30% "
            "signals it is rolling over."
        )
        what_to_watch.append(
            "Committee trigger: a shift in the renormalized Committee score (LSTM/technical/news) "
            "for the leaders is the earliest confirmation the thesis is playing out or failing."
        )
        what_to_watch.append(
            "Catalysts / sentiment: " + ("fresh negative headlines would undermine the setup; "
            "positive flow would extend it." if avg_news is not None and avg_news < 0.1
            else "watch for negative surprises that could flip the news score.")
        )
        if regime.get("label") not in (None, "unknown"):
            what_to_watch.append(
                f"Regime check: in a {regime['label']} tape the rotation's edge shrinks if the "
                "broad index breaks its own SMA50 — size and conviction should respect that."
            )
        if smart_money:
            names = ", ".join(
                f"{s['ticker']}({'+' if s['net'] > 0 else ''}{s['net']})" for s in smart_money[:4]
            )
            what_to_watch.append(f"Institutional flows to track: {names}.")
        what_to_watch.append(
            "Market regime: a broad risk-off move (index/SMA breaks) would likely pull the group "
            "regardless of idiosyncratic strength."
        )
    if top_news:
        what_to_watch.append(
            "Most recent flow: " + "; ".join(f"{n['ticker']}: {n['title']}" for n in top_news[:3])
        )

    return {
        "theme": theme or "custom basket",
        "market": market,
        "resolved_from": source,
        "basket_size": len(basket),
        "covered": len(with_data),
        "constituents": constituents,
        "breadth": {"bull": bull, "bear": bear, "neutral": neutral, "pct_bull": pct_bull},
        "avg_momentum": avg_mom,
        "avg_combined": avg_combined,
        "avg_news": avg_news,
        "committee": {
            "verdict": group_committee,
            "avg_score": avg_committee,
            "pct_bull": pct_bull_committee,
            "bull": bull_c,
            "bear": bear_c,
            "neutral": neutral_c,
        },
        "regime": regime,
        "smart_money": smart_money[:8],
        "leaders": [
            {"ticker": c["ticker"], "momentum_20": c["momentum_20"], "verdict": c["verdict"]}
            for c in leaders
        ],
        "laggards": [
            {"ticker": c["ticker"], "momentum_20": c["momentum_20"], "verdict": c["verdict"]}
            for c in laggards
        ],
        "committee_leaders": [
            {
                "ticker": c["ticker"],
                "committee_score": c["committee_score"],
                "committee_verdict": c["committee_verdict"],
            }
            for c in comm_leaders
        ],
        "committee_laggards": [
            {
                "ticker": c["ticker"],
                "committee_score": c["committee_score"],
                "committee_verdict": c["committee_verdict"],
            }
            for c in comm_laggards
        ],
        "top_news": top_news,
        "setup": setup,
        "what_to_watch": what_to_watch,
    }
