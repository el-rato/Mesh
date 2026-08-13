"""Multi-source historical data service with fallback.

Provides a single ``fetch`` interface used by the backtester. A configurable
provider chain is tried in order (primary -> secondary -> tertiary); each
provider's output is validated before acceptance. Data is never fabricated,
interpolated, or copied from another ticker. One backtest run uses ONE data
source (no mid-run provider switching).

Providers:
* ``primary``   — yfinance via ``indexes.index_history`` (period-based)
* ``secondary`` — yfinance direct with explicit start/end/interval
* ``tertiary``  — Stooq daily CSV (independent provider; daily timeframe only)
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import yfinance as yf

from .config import settings
from .db import Database
from .universe import symbol_for

logger = logging.getLogger(__name__)

SUCCESS = "SUCCESS"
FALLBACK_SUCCESS = "FALLBACK_SUCCESS"
NO_DATA = "NO_DATA"
UNSUPPORTED = "UNSUPPORTED"
PARTIAL = "PARTIAL"
ERROR = "ERROR"

#: Cache TTLs (seconds): long for successful/valid datasets, short for
#: temporary provider errors so an outage does not permanently poison the cache.
_SUCCESS_TTL = 3600
_ERROR_TTL = 60
_CACHE_MAX = 256

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _cache.clear()


@dataclass
class HistoricalDataResult:
    status: str = NO_DATA
    provider: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    requested_start: str = ""
    requested_end: str = ""
    actual_start: str | None = None
    actual_end: str | None = None
    timeframe: str = ""
    fallback_used: bool = False
    attempted_providers: list[str] = field(default_factory=list)
    provider_errors: dict[str, str] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "rows": self.rows,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "actual_start": self.actual_start,
            "actual_end": self.actual_end,
            "timeframe": self.timeframe,
            "fallback_used": self.fallback_used,
            "attempted_providers": list(self.attempted_providers),
            "provider_errors": dict(self.provider_errors),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

_TIMEFRAME_RANGE = {"5m": "1d", "30m": "1w", "1h": "1mo", "1d": "6mo"}
_INTERVALS = {"5m", "15m", "30m", "1h", "1d"}


def _provider_primary(symbol: str, market: str, ticker: str, start: str, end: str, timeframe: str) -> dict[str, Any]:
    """yfinance via index_history (period-based). Does not support 15m."""
    if timeframe not in _TIMEFRAME_RANGE:
        return {"status": "unsupported", "rows": []}
    from .indexes import index_history

    rows = index_history(symbol, _TIMEFRAME_RANGE[timeframe])
    return {"status": "ok", "rows": rows, "provider": "primary"}


def _provider_secondary(symbol: str, market: str, ticker: str, start: str, end: str, timeframe: str) -> dict[str, Any]:
    """yfinance direct with explicit start/end/interval (supports 15m)."""
    if timeframe not in _INTERVALS:
        return {"status": "unsupported", "rows": []}
    try:
        end_dt = datetime.fromisoformat(end) + timedelta(days=1)
        df = yf.Ticker(symbol).history(
            start=start, end=end_dt.strftime("%Y-%m-%d"), interval=timeframe, auto_adjust=True
        )
    except Exception as exc:
        return {"status": "error", "rows": [], "error": str(exc)}
    if df is None or df.empty:
        return {"status": "not_found", "rows": []}
    rows = _df_to_rows(df)
    return {"status": "ok", "rows": rows, "provider": "secondary"}


_SUFFIX_COUNTRY = {
    "": "us", ".L": "uk", ".DE": "de", ".PA": "fr", ".AS": "nl", ".MI": "it",
    ".SW": "ch", ".BO": "in", ".NS": "in", ".HK": "hk", ".KS": "kr", ".KQ": "kr",
    ".T": "jp", ".AX": "au", ".TO": "ca", ".SI": "sg",
}


def _stooq_code(market: str, ticker: str) -> str | None:
    from .markets import load_markets

    m = load_markets(settings.markets_dir).get(market)
    suffix = (m.yahoo_suffix if m else "") or ""
    country = _SUFFIX_COUNTRY.get(suffix.upper() if suffix else "")
    if not country:
        return None
    return f"{ticker.lower()}.{country}"


def _provider_tertiary(symbol: str, market: str, ticker: str, start: str, end: str, timeframe: str) -> dict[str, Any]:
    """Stooq daily CSV — an independent provider for daily data only."""
    if timeframe != "1d":
        return {"status": "unsupported", "rows": []}
    code = _stooq_code(market, ticker)
    if not code:
        return {"status": "unsupported", "rows": [], "error": "no stooq symbol mapping"}
    url = f"https://stooq.com/q/d/l/?s={code}&i=d&d1={start}&d2={end}"
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception as exc:
        return {"status": "error", "rows": [], "error": str(exc)}
    rows: list[dict[str, Any]] = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for r in reader:
            try:
                close = float(r.get("Close"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "date": f"{r.get('Date','')} 00:00",
                "open": float(r.get("Open", close)),
                "high": float(r.get("High", close)),
                "low": float(r.get("Low", close)),
                "close": close,
                "volume": int(float(r.get("Volume", 0) or 0)),
            })
    except Exception as exc:
        return {"status": "error", "rows": [], "error": f"stooq parse: {exc}"}
    if not rows:
        return {"status": "not_found", "rows": []}
    return {"status": "ok", "rows": rows, "provider": "tertiary"}


_PROVIDERS = {
    "primary": _provider_primary,
    "secondary": _provider_secondary,
    "tertiary": _provider_tertiary,
}


def _df_to_rows(df) -> list[dict[str, Any]]:
    rows = []
    for idx, row in df.iterrows():
        try:
            close = float(row.get("Close"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "date": idx.strftime("%Y-%m-%d %H:%M"),
            "open": float(row.get("Open", close)),
            "high": float(row.get("High", close)),
            "low": float(row.get("Low", close)),
            "close": close,
            "volume": int(float(row.get("Volume", 0) or 0)),
        })
    return rows


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}


def _parse_ts(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def validate_rows(rows: list[dict[str, Any]], start: str, end: str, timeframe: str, min_rows: int) -> dict[str, Any]:
    """Validate a provider's data. Returns ok / partial / invalid / insufficient."""
    if not rows:
        return {"status": "invalid", "reason": "empty"}
    cleaned: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in rows:
        dt = _parse_ts(str(r.get("date", "")))
        if dt is None:
            return {"status": "invalid", "reason": "bad timestamp"}
        try:
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        except (KeyError, TypeError, ValueError):
            return {"status": "invalid", "reason": "missing OHLC"}
        if not all(v > 0 for v in (o, h, l, c)):
            return {"status": "invalid", "reason": "non-positive price"}
        if not (h >= l and h >= max(o, c) - 1e-9 and l <= min(o, c) + 1e-9):
            return {"status": "invalid", "reason": "impossible OHLC"}
        key = int(dt.timestamp())
        if key in seen:
            continue  # duplicate timestamps are dropped
        seen.add(key)
        cleaned.append(r)
    if not cleaned:
        return {"status": "invalid", "reason": "no valid rows"}
    cleaned.sort(key=lambda r: _parse_ts(str(r["date"])))
    if len(cleaned) < min_rows:
        return {"status": "insufficient", "reason": f"only {len(cleaned)} rows (min {min_rows})"}

    first = _parse_ts(str(cleaned[0]["date"]))
    last = _parse_ts(str(cleaned[-1]["date"]))
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    grace = timedelta(minutes=max(_TIMEFRAME_MINUTES.get(timeframe, 30) * 3, 60))
    overlap = last >= start_dt and first <= end_dt
    complete = overlap and first <= start_dt + grace and last >= end_dt - grace
    return {
        "status": "ok" if complete else "partial",
        "rows": cleaned,
        "actual_start": cleaned[0]["date"],
        "actual_end": cleaned[-1]["date"],
        "overlap": bool(overlap),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _cache_set(key: str, payload: dict[str, Any], ttl: float) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (time.time() + ttl, payload)


def fetch(
    db: Database,
    market: str,
    ticker: str,
    start: str,
    end: str,
    timeframe: str,
    min_rows: int = 35,
) -> HistoricalDataResult:
    """Fetch historical data via the configured provider chain.

    Tries providers in ``settings.historical_providers`` order, validating each.
    Returns the first acceptable dataset; the backtester uses it for the whole
    run (one data source per run). Never fabricates data.
    """
    symbol = symbol_for(db, market, ticker)
    security_id = f"{market}:{ticker.upper()}"
    attempted: list[str] = []
    errors: dict[str, str] = {}

    for name in settings.historical_providers:
        attempted.append(name)
        cache_key = f"{security_id}|{start}|{end}|{timeframe}|{name}"
        cached = _cache_get(cache_key)
        if cached is not None:
            raw = cached
        else:
            provider = _PROVIDERS.get(name)
            if provider is None:
                errors[name] = "unknown provider"
                continue
            try:
                raw = provider(symbol, market, ticker, start, end, timeframe)
            except Exception as exc:
                raw = {"status": "error", "rows": [], "error": str(exc)}
            ttl = _SUCCESS_TTL if raw.get("status") in ("ok", "unsupported") else _ERROR_TTL
            _cache_set(cache_key, raw, ttl)

        if raw.get("status") == "unsupported":
            errors[name] = raw.get("error") or "timeframe/range not supported"
            continue
        if raw.get("status") == "error":
            errors[name] = raw.get("error") or "temporary error"
            continue
        if raw.get("status") == "not_found":
            errors[name] = "no data returned"
            continue

        valid = validate_rows(raw.get("rows", []), start, end, timeframe, min_rows)
        if valid["status"] in ("invalid", "insufficient"):
            errors[name] = f"rejected: {valid['reason']}"
            continue
        if valid["status"] == "partial" and not valid["overlap"]:
            errors[name] = "data does not overlap requested period"
            continue

        fallback_used = len(attempted) > 1
        result = HistoricalDataResult(
            status=FALLBACK_SUCCESS if fallback_used else SUCCESS,
            provider=name,
            rows=valid["rows"],
            requested_start=start,
            requested_end=end,
            actual_start=valid["actual_start"],
            actual_end=valid["actual_end"],
            timeframe=timeframe,
            fallback_used=fallback_used,
            attempted_providers=list(attempted),
            provider_errors=dict(errors),
        )
        if valid["status"] == "partial":
            result.status = PARTIAL
        return result

    return HistoricalDataResult(
        status=NO_DATA,
        requested_start=start,
        requested_end=end,
        timeframe=timeframe,
        attempted_providers=attempted,
        provider_errors=errors,
        error="No provider returned sufficient data for this range.",
    )


def fetch_symbol(
    symbol: str,
    start: str,
    end: str,
    timeframe: str = "1d",
    min_rows: int = 30,
    providers: tuple[str, ...] = ("secondary", "tertiary"),
) -> list[dict[str, Any]]:
    """Fetch an arbitrary provider symbol (e.g. a benchmark index) for an exact
    historical range, bypassing the universe symbol mapping.

    Only explicit-range providers are used because a period-relative provider
    (e.g. "last 6 months") cannot honor an arbitrary past window without
    leaking future data. Returns validated rows (or [] on failure).
    """
    attempted: list[str] = []
    for name in providers:
        attempted.append(name)
        provider = _PROVIDERS.get(name)
        if provider is None:
            continue
        key = f"SYM|{symbol}|{start}|{end}|{timeframe}|{name}"
        cached = _cache_get(key)
        if cached is not None:
            raw = cached
        else:
            try:
                raw = provider(symbol, "", "", start, end, timeframe)
            except Exception as exc:
                raw = {"status": "error", "rows": [], "error": str(exc)}
            ttl = _SUCCESS_TTL if raw.get("status") in ("ok", "unsupported") else _ERROR_TTL
            _cache_set(key, raw, ttl)
        if raw.get("status") == "ok":
            valid = validate_rows(raw.get("rows", []), start, end, timeframe, min_rows)
            if valid["status"] in ("ok", "partial") and valid["overlap"]:
                return valid["rows"]
    return []
