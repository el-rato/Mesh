"""Resilient price-data provider chain (defence against yfinance rate limits).

Historically the terminal fetched all OHLCV from ``yfinance`` alone. When
Yahoo rate-limits the client (empty frames / ``YFRateLimitError``), the whole
pipeline stalls. This module introduces a small provider abstraction with
several real backends and a **fallback chain** with a per-provider circuit
breaker:

* ``yfinance``   — primary (as before)
* ``stooq``      — key-free CSV download, works for most global exchanges
* ``alphavantage`` / ``twelvedata`` — optional API-key backends (used only
  when a key is configured in the environment)

``fetch_ohlcv`` tries each enabled provider in order and returns the first
non-empty frame. A provider that throws (or is explicitly rate-limited) is
"cooled down" for a short window so the chain stops hammering a provider that
is clearly throttling us, and falls straight through to the next one.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import urllib.request
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

#: How long a provider is skipped after a rate-limit / hard failure (seconds).
#: Tunable via STOCK_ALERT_PROVIDER_COOLDOWN (see config.settings).
try:
    from .config import settings as _settings

    _COOLDOWN = float(getattr(_settings, "provider_cooldown_s", 120))
except Exception:  # pragma: no cover - standalone import safety
    _COOLDOWN = 120.0
#: Short cooldown for a generic network error (less certain to be throttling).
_ERROR_COOLDOWN = 30.0
#: Per-request HTTP timeout for providers we fetch over the network ourselves.
_HTTP_TIMEOUT = 12.0

# Yahoo-style suffix -> Stooq country suffix. Stooq uses lower-case dotted
# suffixes (e.g. ``aapl.us``, ``sie.de``, ``infy.in``). Unknown suffixes fall
# back to ``.us``. Multiple candidates are tried so a symbol is found even if
# the exact suffix mapping is imperfect.
_STOOQ_SUFFIX = {
    "": "us",
    "BO": "bo",
    "NS": "in",
    "L": "uk",
    "DE": "de",
    "PA": "pa",
    "TO": "ca",
    "T": "jp",
    "AX": "ax",
    "HK": "hk",
    "SI": "sg",
    "SW": "ch",
    "SX": "es",
    "AS": "nl",
    "BR": "br",
    "HE": "he",
    "ST": "st",
    "VX": "vx",
    "SA": "sa",
    "OL": "ol",
    "TA": "ta",
    "KO": "ko",
    "AR": "ar",
}


class PriceProvider:
    """A pluggable OHLCV source. Subclasses return a DataFrame with columns
    Open/High/Low/Close/Volume indexed by Date, or an empty frame on failure."""

    name: str = "base"

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError

    def enabled(self) -> bool:
        return True


def _empty() -> pd.DataFrame:
    return pd.DataFrame()


def _http_text(url: str, timeout: float = _HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "stock-alert-app/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


class YFinanceProvider(PriceProvider):
    name = "yfinance"

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        try:
            import yfinance as yf
        except Exception as exc:  # noqa: BLE001
            logger.debug("yfinance unavailable: %s", exc)
            return _empty()
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        except Exception as exc:  # noqa: BLE001 - covers YFRateLimitError + network
            # Re-raise a tagged error so the chain can mark a rate limit.
            _record_failure(self.name, rate_limited=_is_rate_limit(exc))
            raise
        if df is None or df.empty or "Close" not in df.columns:
            return _empty()
        if len(df) < 2:
            return _empty()
        return df


class YahooChartProvider(PriceProvider):
    """Direct hit on Yahoo's v8 chart endpoint (query1.finance.yahoo.com).

    This is the SAME upstream data as yfinance but bypasses the cookie/crumb
    machinery that yfinance.history() uses — which is precisely what Yahoo tends
    to rate-limit. It therefore survives many throttling episodes that knock out
    the primary yfinance path, with no API key required.
    """

    name = "yahoo_chart"

    @staticmethod
    def _range(period: str) -> str:
        # Yahoo range tokens: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max
        p = (period or "").lower()
        if p in {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}:
            return p
        return "1y"

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{symbol}?range={self._range(period)}&interval=1d"
        )
        try:
            import json

            payload = json.loads(_http_text(url))
            results = (payload.get("chart") or {}).get("result") or []
            if not results:
                return _empty()
            r = results[0]
            ts = r.get("timestamp") or []
            q = (r.get("indicators") or {}).get("quote") or [{}]
            q = q[0]
            df = pd.DataFrame(
                {
                    "Open": q.get("open") or [],
                    "High": q.get("high") or [],
                    "Low": q.get("low") or [],
                    "Close": q.get("close") or [],
                    "Volume": q.get("volume") or [],
                },
                index=pd.to_datetime(pd.Series(ts), unit="s"),
            )
            df = df.dropna(subset=["Close"])
            if df.empty or len(df) < 2:
                return _empty()
            return df
        except Exception as exc:  # noqa: BLE001
            _record_failure(self.name, rate_limited="rate" in str(exc).lower() or "429" in str(exc))
            logger.debug("Yahoo chart failed for %s: %s", symbol, exc)
            return _empty()


class StooqProvider(PriceProvider):
    name = "stooq"

    @staticmethod
    def _candidates(symbol: str) -> list[str]:
        base, dot, suf = symbol.partition(".")
        cands: list[str] = []
        if dot:
            mapped = _STOOQ_SUFFIX.get(suf.upper())
            if mapped:
                cands.append(f"{base}.{mapped}")
        cands.append(f"{base}.us")
        cands.append(base)
        # de-dupe while preserving order
        seen = set()
        out = []
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        # Stooq only serves daily bars via the CSV endpoint; resample-equivalents
        # are not needed because the pipeline only consumes daily closes.
        for cand in self._candidates(symbol):
            url = f"https://stooq.com/q/d/l/?s={cand.lower()}&i=d"
            try:
                text = _http_text(url)
                df = pd.read_csv(io.StringIO(text))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Stooq fetch failed for %s: %s", cand, exc)
                continue
            if df is None or df.empty:
                continue
            df = self._normalise(df)
            if df is not None and not df.empty and len(df) >= 2:
                return df
        return _empty()

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame | None:
        cols = {c.lower(): c for c in df.columns}
        needed = ["open", "high", "low", "close", "volume"]
        if "close" not in cols:
            return None
        out = pd.DataFrame()
        out["Date"] = pd.to_datetime(df[cols["date"]]) if "date" in cols else pd.Series(range(len(df)))
        for name in ["open", "high", "low", "close", "volume"]:
            src = cols.get(name)
            if src is None:
                out[name.title()] = 0.0
            else:
                out[name.title()] = pd.to_numeric(df[src], errors="coerce")
        out = out.set_index("Date").sort_index()
        return out


class AlphaVantageProvider(PriceProvider):
    name = "alphavantage"

    def enabled(self) -> bool:
        return bool(os.getenv("ALPHA_VANTAGE_API_KEY"))

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if not key:
            return _empty()
        url = (
            "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
            f"&symbol={symbol}&outputsize=full&apikey={key}"
        )
        try:
            import json

            payload = json.loads(_http_text(url))
            series = payload.get("Time Series (Daily)")
            if not series:
                return _empty()
            rows = [{"Date": d, **{k.title(): float(v[k]) for k in v}} for d, v in series.items()]
            df = pd.DataFrame(rows).set_index("Date").sort_index()
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as exc:  # noqa: BLE001
            _record_failure(self.name, rate_limited="rate" in str(exc).lower())
            logger.debug("Alpha Vantage failed for %s: %s", symbol, exc)
            return _empty()


class TwelveDataProvider(PriceProvider):
    name = "twelvedata"

    def enabled(self) -> bool:
        return bool(os.getenv("TWELVE_DATA_API_KEY"))

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        key = os.getenv("TWELVE_DATA_API_KEY")
        if not key:
            return _empty()
        url = (
            "https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1day&outputsize=500&apikey={key}"
        )
        try:
            import json

            payload = json.loads(_http_text(url))
            vals = payload.get("values")
            if not vals:
                return _empty()
            df = pd.DataFrame(vals)
            df["Date"] = pd.to_datetime(df["datetime"])
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.set_index("Date").sort_index()
            return df
        except Exception as exc:  # noqa: BLE001
            logger.debug("Twelve Data failed for %s: %s", symbol, exc)
            return _empty()


# ---------------------------------------------------------------------------
# Provider registry + circuit breaker
# ---------------------------------------------------------------------------

_PROVIDERS: list[PriceProvider] = [
    YFinanceProvider(),
    YahooChartProvider(),
    StooqProvider(),
    AlphaVantageProvider(),
    TwelveDataProvider(),
]
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()


def _is_rate_limit(exc: Exception) -> bool:
    """Best-effort detection of a provider throttling response."""
    msg = str(exc).lower()
    if "yfratelimit" in msg or "rate limit" in msg or "429" in msg or "too many" in msg:
        return True
    # yfinance raises a specific error class when throttled.
    try:
        from yfinance.errors import YFRateLimitError  # type: ignore

        if isinstance(exc, YFRateLimitError):
            return True
    except Exception:
        pass
    return False


def _record_failure(name: str, rate_limited: bool = False) -> None:
    with _cooldown_lock:
        _cooldown_until[name] = time.time() + (_COOLDOWN if rate_limited else _ERROR_COOLDOWN)


def _record_success(name: str) -> None:
    with _cooldown_lock:
        _cooldown_until.pop(name, None)


def _provider_available(p: PriceProvider) -> bool:
    if not p.enabled():
        return False
    until = _cooldown_until.get(p.name)
    if until and time.time() < until:
        return False
    return True


def provider_status() -> list[dict[str, object]]:
    """Human-readable status for each provider (used by the UI/health checks)."""
    now = time.time()
    out = []
    for p in _PROVIDERS:
        until = _cooldown_until.get(p.name)
        out.append(
            {
                "name": p.name,
                "enabled": p.enabled(),
                "cooling_down": bool(until and now < until),
                "cooldown_remaining_s": round(until - now, 1) if until else 0.0,
            }
        )
    return out


def fetch_ohlcv(
    symbol: str,
    period: str = "6mo",
    interval: str = "1d",
    providers: Iterable[PriceProvider] | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV for ``symbol`` from the first provider that returns data.

    The returned frame has columns Open/High/Low/Close/Volume indexed by Date
    (matching the shape the rest of the pipeline expects from yfinance). Providers
    that error or are cooling down are skipped. Falls back to an empty frame if
    every backend fails.
    """
    chain = list(providers) if providers else _PROVIDERS
    for p in chain:
        if not _provider_available(p):
            continue
        try:
            df = p.fetch(symbol, period, interval)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Provider %s failed for %s: %s", p.name, symbol, exc)
            _record_failure(p.name, rate_limited=_is_rate_limit(exc))
            continue
        if df is not None and not df.empty and len(df) >= 2 and "Close" in df.columns:
            _record_success(p.name)
            logger.debug("Price for %s served by %s", symbol, p.name)
            return df
        # Empty/short frame: this provider had no data for the symbol. Try the next.
    return _empty()
