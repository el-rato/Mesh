from __future__ import annotations

import logging
import time

import httpx

from .config import settings
from .markets import load_markets

logger = logging.getLogger(__name__)

#: Yahoo Finance search endpoint — Yahoo is already the market-data provider
#: (yfinance dependency in price.py / models.price_lstm), so this reuses it.
_YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

_TIMEOUT = 20.0
_CACHE_TTL = 900.0  # Search metadata is stable; cache per query for 15 min.
_CACHE: dict[str, tuple[float, list[dict]]] = {}

#: Quote types that are not single stocks (indices, funds, non-equities).
_NON_EQUITY_TYPES = {
    "INDEX",
    "MUTUALFUND",
    "FUTURE",
    "CURRENCY",
    "CRYPTOCURRENCY",
    "OPTION",
    "ETMF",
}

#: Recognised Yahoo suffixes the app does NOT cover. Known so an unsupported
#: exchange is reported clearly instead of the suffix being misread.
_UNSUPPORTED_SUFFIXES = {
    ".NS",
    ".NSE",
    ".KQ",
    ".PA",
    ".AS",
    ".BR",
    ".MI",
    ".MC",
    ".VI",
    ".ST",
    ".HE",
    ".NZ",
    ".SS",
    ".SZ",
    ".OL",
    ".IC",
    ".IS",
    ".IR",
    ".LS",
    ".AT",
    ".CO",
    ".MX",
    ".SA",
    ".TW",
    ".SN",
    ".TA",
    ".JK",
    ".BK",
    ".KL",
    ".F",
    ".BE",
    ".MU",
    ".DU",
    ".HM",
    ".HA",
    ".SW",
    ".WA",
    ".VN",
    ".SG",
    ".TL",
    ".TWO",
}

#: Single US market config uses an empty Yahoo suffix; symbol without a
#: recognised exchange suffix is treated as US-listed.
_US_FALLBACK_MARKET = "NYSE"


def clear_search_cache() -> None:
    """Clear the in-memory search cache (useful for tests / forced refresh)."""
    _CACHE.clear()


def _yahoo_search(query: str, count: int = 20) -> list[dict]:
    """Query the Yahoo Finance symbols search API, with a per-query cache."""
    q = (query or "").strip()
    if not q:
        return []
    now = time.time()
    hit = _CACHE.get(q)
    if hit and hit[0] > now:
        return hit[1]
    params = {
        "q": q,
        "quotesCount": count,
        "newsCount": 0,
        "listsCount": 0,
        "enableFuzzyQuery": "true",
    }
    try:
        with httpx.Client(
            timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            resp = client.get(_YAHOO_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        quotes = data.get("quotes") or []
        _CACHE[q] = (now + _CACHE_TTL, quotes)
        return quotes
    except Exception as exc:  # network failure, bad JSON, rate limiting
        logger.warning("Yahoo search failed for %r: %s", q, exc)
        return []


def _suffix_market_code(suffix: str, markets: dict) -> str | None:
    """Map a Yahoo exchange suffix to a configured market code.

    An empty suffix never maps here; it falls through to the US-list check in
    ``_to_instrument`` so foreign symbols are not misread as US-listed.
    """
    key = (suffix or "").upper()
    if not key:
        return None
    for code, m in markets.items():
        if (m.yahoo_suffix or "").upper() == key:
            return code
    return None


def _split_symbol(full: str) -> tuple[str, str]:
    """Split a Yahoo symbol into (ticker, exchange_suffix).

    Only a trailing suffix that matches a known exchange (configured or known
    unsupported) is treated as an exchange marker; anything else (e.g. US share
    classes like ``BF.B``) is kept as part of the ticker.
    """
    full = (full or "").strip().upper()
    if "." not in full:
        return full, ""
    base, tail = full.rsplit(".", 1)
    suffix = "." + tail
    markets = load_markets(settings.markets_dir)
    known = _UNSUPPORTED_SUFFIXES | {
        (m.yahoo_suffix or "").upper() for m in markets.values()
    } - {""}
    if suffix in known:
        return base, suffix
    return full, ""


def _exchange_label(suffix: str, quote: dict | None) -> str:
    if suffix:
        return (quote or {}).get("exchDisp") or suffix
    return (quote or {}).get("exchDisp") or "US"


def _is_us_listed(quote: dict | None) -> bool | None:
    """True if the quote is US-listed, False if clearly foreign, None if unknown."""
    if not quote:
        return None
    exch = (quote.get("exchange") or "").upper()
    disp = (quote.get("exchDisp") or "").upper()
    if exch in {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "PHL", "BTS", "BTSX"}:
        return True
    for marker in ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", " OTC", "PINK"):
        if marker in disp:
            return True
    return False


def unsupported(
    symbol: str, ticker: str, company: str, quote: dict | None, suffix: str
) -> dict:
    return {
        "market": None,
        "ticker": ticker,
        "symbol": symbol,
        "company": company,
        "exchange": _exchange_label(suffix, quote),
        "quote_type": (quote or {}).get("quoteType") or "",
        "supported": False,
        "featured": False,
        "source": "yahoo",
    }


def _to_instrument(
    symbol: str, company: str, quote: dict | None, markets: dict
) -> dict:
    """Resolve one Yahoo quote into a {market, ticker, symbol, ...} dict."""
    ticker, suffix = _split_symbol(symbol)
    code = _suffix_market_code(suffix, markets)
    if code is None and not suffix:
        if _is_us_listed(quote) is False:
            return unsupported(symbol, ticker, company, quote, suffix)
        code = _US_FALLBACK_MARKET  # US-listed (no suffix) or unknown — default US
    if code is None:
        return unsupported(symbol, ticker, company, quote, suffix)
    return {
        "market": code,
        "ticker": ticker,
        "symbol": symbol,
        "company": company,
        "exchange": _exchange_label(suffix, quote),
        "quote_type": (quote or {}).get("quoteType") or "",
        "supported": True,
        "featured": False,
        "source": "yahoo",
    }


def _search_score(
    query: str, symbol: str, company: str, keywords: list[str] = None
) -> int | None:
    """Ranking: lower is better. None means no match."""
    sym = symbol.upper()
    name = (company or "").upper()
    if sym == query:
        return 0
    if name == query:
        return 1
    if sym.startswith(query):
        return 2
    if query in sym:
        return 3
    if name.startswith(query):
        return 4
    if query in name:
        return 5
    for kw in keywords or []:
        if kw and query in kw.upper():
            return 6
    return None


def _local_search(query: str, markets: dict, market_filter: str | None) -> list[dict]:
    """Search the configured 'featured' tickers (fast, offline)."""
    scored: list[tuple[int, dict]] = []
    for m in markets.values():
        if market_filter and m.code.upper() != market_filter.upper():
            continue
        suffix = m.yahoo_suffix or ""
        for sym, tkr in m.tickers.items():
            score = _search_score(query, sym, tkr.name or "", tkr.keywords)
            if score is None:
                continue
            scored.append(
                (
                    score,
                    {
                        "market": m.code,
                        "ticker": sym,
                        "symbol": sym + suffix,
                        "company": tkr.name or "",
                        "exchange": m.name,
                        "quote_type": "EQUITY",
                        "supported": True,
                        "featured": True,
                        "source": "local",
                    },
                )
            )
    scored.sort(key=lambda p: (p[0], p[1]["ticker"]))
    return [d for _, d in scored]


def _remote_search(query: str, markets: dict, market_filter: str | None) -> list[dict]:
    """Search the full universe via the live provider (dynamic discovery)."""
    quotes = _yahoo_search(query)
    ranked: list[tuple[int, dict]] = []
    for q in quotes:
        qtype = (q.get("quoteType") or "").upper()
        if qtype in _NON_EQUITY_TYPES:
            continue
        # Secondary guard on the display type: warrants/mutual funds/units can
        # surface under an EQUITY quoteType but are not plain stocks/ETFs.
        tdisp = (q.get("typeDisp") or "").strip().lower()
        if tdisp and tdisp not in ("equity", "etf"):
            continue
        symbol = (q.get("symbol") or "").strip()
        if not symbol:
            continue
        company = (q.get("longname") or q.get("shortname") or "").strip()
        item = _to_instrument(symbol, company, q, markets)
        if (
            item["supported"]
            and market_filter
            and item["market"].upper() != market_filter.upper()
        ):
            continue
        score = _search_score(query, item["ticker"], company)
        if score is None:
            score = _search_score(query, item["symbol"], company)
        if score is None:
            score = 5  # provider fuzzy match without a scoring hit — still relevant
        ranked.append((score, item))
    ranked.sort(key=lambda p: (p[0], p[1]["ticker"]))
    return [d for _, d in ranked]


def search_universe(
    query: str, limit: int = 10, market_filter: str | None = None
) -> list[dict]:
    """Combine configured ('featured') + live (dynamic) results, deduplicated.

    Configured matches rank first so known securities with stored verdicts are
    preferred; Yahoo matches expand the universe to anything the provider knows.
    Never raises: on provider failure it degrades to local-only results.
    """
    q = (query or "").strip().upper()
    if not q:
        return []
    markets = load_markets(settings.markets_dir)
    out: list[dict] = []
    seen: set[str] = set()
    for item in _local_search(q, markets, market_filter) + _remote_search(
        q, markets, market_filter
    ):
        key = item["symbol"].upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def resolve_symbol(symbol: str) -> dict | None:
    """Resolve a full Yahoo symbol into market/ticker/company metadata.

    Company name is taken from cached search results when available (search
    always precedes analysis, so this is usually populated); otherwise the ticker
    is used. Invalid input returns None; unsupported exchanges come back with
    ``supported=False`` so callers can report them gracefully.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    markets = load_markets(settings.markets_dir)
    quote = None
    for _, quotes in _CACHE.values():
        for q in quotes:
            if (q.get("symbol") or "").upper() == sym:
                quote = q
                break
        if quote:
            break
    company = ""
    if quote:
        company = (quote.get("longname") or quote.get("shortname") or "").strip()
    return _to_instrument(sym, company, quote, markets)
