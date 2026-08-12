"""Symbol resolution layer.

Resolves a configured ``(market, ticker)`` to a validated Yahoo symbol *before*
price/LSTM analysis, caches the result, and categorizes failures so unavailable
securities are skipped quietly instead of being mislabelled as "possibly
delisted" and re-requested every cycle.

Distinguishes:

* ``OK`` — a validated symbol returned price data.
* ``SYMBOL_NOT_FOUND`` — the security is unknown to the provider.
* ``DATA_UNAVAILABLE`` — the security exists but currently has no price data
  (includes corporate-action cases where the old symbol went quiet).
* ``TEMPORARY_PROVIDER_ERROR`` — rate limit / network / parse failure; retry soon.
* ``DELISTED`` — only when a previously-working symbol has been consistently
  missing for a long grace period (i.e. actually verified, never guessed).

A different security is never substituted silently: provider search results are
only accepted as alternates when they carry the *same* company name.
"""

from __future__ import annotations

import logging
import re
import time

from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

from .config import settings
from .instruments import _yahoo_search, search_universe
from .markets import load_markets
from .price import fetch_history

logger = logging.getLogger(__name__)

OK = "OK"
SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
TEMPORARY_PROVIDER_ERROR = "TEMPORARY_PROVIDER_ERROR"
DELISTED = "DELISTED"

STATUS_LABEL = {
    OK: "ok",
    SYMBOL_NOT_FOUND: "symbol not found",
    DATA_UNAVAILABLE: "data unavailable",
    TEMPORARY_PROVIDER_ERROR: "temporary provider error",
    DELISTED: "delisted/inactive",
}

#: Cache TTLs (seconds) per status.
_TTL: dict[str, int] = {
    OK: 15 * 60,
    SYMBOL_NOT_FOUND: 6 * 60 * 60,
    DATA_UNAVAILABLE: 6 * 60 * 60,
    TEMPORARY_PROVIDER_ERROR: 5 * 60,
    DELISTED: 24 * 60 * 60,
}

#: A symbol is only considered DELISTED after this long with no data following
#: a previously successful resolution.
_GRACE_AFTER_LOSS = 72 * 60 * 60

#: (market, ticker) -> {"status", "symbol", "company", "expires_at", "last_ok_at", "note"}
_cache: dict[tuple[str, str], dict[str, object]] = {}


def clear_cache() -> None:
    """Clear the resolution cache (mainly for tests / forced re-validation)."""
    _cache.clear()


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _same_security(configured: str, provider: str) -> bool:
    """True only when the provider name matches the configured company name.

    Strict so a demerged entity (e.g. TMCV vs Tata Motors Ltd) is never
    silently substituted for the original security.
    """
    a, b = _norm(configured), _norm(provider)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= len(longer) * 0.7 and (shorter in longer or longer in shorter)


def _store(
    key: tuple[str, str],
    status: str,
    symbol: str,
    company: str,
    now: float,
    note: str = "",
) -> dict[str, object]:
    prev = _cache.get(key)
    last_ok = prev.get("last_ok_at") if prev else None
    if (
        status in (DATA_UNAVAILABLE, SYMBOL_NOT_FOUND)
        and last_ok is not None
        and (now - float(last_ok)) > _GRACE_AFTER_LOSS
    ):
        status = DELISTED
        note = note or "no data for an extended period"
    entry: dict[str, object] = {
        "status": status,
        "symbol": symbol,
        "company": company,
        "expires_at": now + _TTL[status],
        "last_ok_at": now if status == OK else last_ok,
        "note": note,
    }
    _cache[key] = entry
    return {
        "status": status,
        "symbol": symbol,
        "company": company,
        "note": note,
    }


def _candidates(primary: str, company: str, market_code: str) -> list[str]:
    candidates = [primary]
    if company:
        try:
            for item in search_universe(company, limit=15, market_filter=market_code):
                symbol = item.get("symbol")
                if not symbol or symbol == primary or not item.get("supported"):
                    continue
                if _same_security(company, item.get("company") or ""):
                    candidates.append(symbol)
        except Exception:
            logger.debug("Provider search for alternates failed for %s", company, exc_info=True)
    seen: set[str] = set()
    out: list[str] = []
    for symbol in candidates:
        if symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _categorize_missing(symbol: str, company: str) -> tuple[str, str]:
    """Categorize a symbol whose history came back empty."""
    if not company:
        return SYMBOL_NOT_FOUND, "no provider match for this ticker"
    try:
        quotes = _yahoo_search(company, count=15)
    except Exception:
        return DATA_UNAVAILABLE, "provider search unavailable"
    if not quotes:
        return SYMBOL_NOT_FOUND, "security not found on the provider"
    for quote in quotes:
        if (quote.get("symbol") or "").upper() == symbol.upper():
            return DATA_UNAVAILABLE, "no price data currently available"
    # The company exists but under different symbols -> likely a corporate action
    # or a ticker change. Never substitute a different security.
    return DATA_UNAVAILABLE, "possible corporate action or symbol change"


def _categorize_fetch_exception(symbol: str, company: str, exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, YFRateLimitError):
        return TEMPORARY_PROVIDER_ERROR, "provider rate limit"
    if isinstance(exc, YFPricesMissingError):
        return _categorize_missing(symbol, company)
    return TEMPORARY_PROVIDER_ERROR, "temporary provider error"


def _validate_symbol(symbol: str, company: str) -> tuple[str, str]:
    try:
        df = fetch_history(symbol, period="5d")
    except Exception as exc:
        return _categorize_fetch_exception(symbol, company, exc)
    if df is None or df.empty:
        return _categorize_missing(symbol, company)
    return OK, ""


def _aggregate(statuses: list[str]) -> str:
    if not statuses:
        return SYMBOL_NOT_FOUND
    if TEMPORARY_PROVIDER_ERROR in statuses:
        return TEMPORARY_PROVIDER_ERROR
    if DATA_UNAVAILABLE in statuses:
        return DATA_UNAVAILABLE
    if SYMBOL_NOT_FOUND in statuses:
        return SYMBOL_NOT_FOUND
    return DATA_UNAVAILABLE


def resolve(market_code: str, ticker: str, company: str = "") -> dict[str, object]:
    """Return a validated Yahoo symbol for ``(market, ticker)``.

    Result: ``{"status", "symbol", "company", "note"}``. ``symbol`` is a
    validated Yahoo symbol only when ``status == OK``.
    """
    key = (market_code.upper(), ticker.upper())
    now = time.time()
    cached = _cache.get(key)
    if cached and float(cached["expires_at"]) > now:
        return {
            "status": cached["status"],
            "symbol": cached["symbol"],
            "company": company,
            "note": cached.get("note") or "",
        }

    markets = load_markets(settings.markets_dir)
    market = markets.get(market_code.upper())
    if market is None:
        return _store(key, SYMBOL_NOT_FOUND, "", company, now, note="unknown market")

    ticker = ticker.upper()
    try:
        configured = market.get_ticker(ticker)
        company = configured.name or company
        suffix = configured.yahoo_suffix or market.yahoo_suffix or ""
    except KeyError:
        suffix = market.yahoo_suffix or ""
    primary = ticker + suffix

    outcomes: list[tuple[str, str, str]] = []
    for symbol in _candidates(primary, company, market.code):
        status, note = _validate_symbol(symbol, company)
        outcomes.append((symbol, status, note))
        if status == OK:
            return _store(key, OK, symbol, company, now)
        if status == TEMPORARY_PROVIDER_ERROR:
            return _store(key, TEMPORARY_PROVIDER_ERROR, "", company, now, note=note)

    final = _aggregate([status for _, status, _ in outcomes])
    note = next((n for _, _, n in outcomes if n), "")
    if company and final == DATA_UNAVAILABLE:
        note = "possible corporate action or symbol change" if not note else note
    return _store(key, final, "", company, now, note=note)


def resolution(market_code: str, ticker: str, company: str = "") -> dict[str, object]:
    """Full resolution result (status/symbol/note) — for callers that need details."""
    return resolve(market_code, ticker, company)


def resolve_for_fetch(market_code: str, ticker: str, company: str = "") -> str | None:
    """Validated Yahoo symbol to fetch, or ``None`` to skip the security."""
    result = resolve(market_code, ticker, company)
    if result["status"] == OK:
        return str(result["symbol"])
    return None
