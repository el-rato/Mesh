"""Resilient price-data provider chain (defence against yfinance rate limits).

Historically the terminal fetched all OHLCV from ``yfinance`` alone. When
Yahoo rate-limits the client (empty frames / ``YFRateLimitError``), the whole
pipeline stalls. This module introduces a small provider abstraction with
several real backends and a **fallback chain** with a per-provider circuit
breaker:

* ``yfinance``   — primary (fast batch path, tried first)
* ``twelvedata`` / ``alphavantage`` — keyed backups, active only when
  ``TWELVE_DATA_API_KEY`` / ``ALPHA_VANTAGE_KEY`` is configured
* ``yahoo_chart`` — key-free direct Yahoo chart API (same upstream as yfinance)
* ``stooq``      — key-free CSV download, works for most global exchanges

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
from contextlib import contextmanager
from typing import Iterable

import pandas as pd

from .request_coordinator import MarketDataSnapshot, ProviderPolicy, RequestCoordinator

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
    supports_batch: bool = False
    supported_intervals: frozenset[str] | None = None

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError

    def enabled(self) -> bool:
        return True

    def supports(self, period: str, interval: str) -> bool:
        del period
        return self.supported_intervals is None or interval.lower() in self.supported_intervals

    def fetch_many(self, symbols: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
        return {symbol: self.fetch(symbol, period, interval) for symbol in symbols}


def _empty() -> pd.DataFrame:
    return pd.DataFrame()


def _api_key(setting_name: str, *env_names: str) -> str:
    """API key from settings first, then environment (both supported)."""
    try:
        val = getattr(_settings, setting_name, "") or ""
    except Exception:  # pragma: no cover - standalone import safety
        val = ""
    if val:
        return val
    for name in env_names:
        val = os.getenv(name) or ""
        if val:
            return val
    return ""


def _http_text(url: str, timeout: float = _HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "stock-alert-app/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


class YFinanceProvider(PriceProvider):
    name = "yfinance"
    supports_batch = True

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        try:
            import yfinance as yf
        except Exception as exc:  # noqa: BLE001
            logger.debug("yfinance unavailable: %s", exc)
            return _empty()
        try:
            # Ask yfinance to raise missing-ticker errors so expected 404s can
            # be classified here instead of being logged as errors upstream.
            with _quiet_yfinance_miss_logs():
                df = yf.Ticker(symbol).history(
                    period=period,
                    interval=interval,
                    auto_adjust=True,
                    raise_errors=True,
                )
        except Exception as exc:  # noqa: BLE001 - covers YFRateLimitError + network
            if _is_expected_miss(exc):
                logger.debug("YFinance has no listing for %s; trying fallback", symbol)
                return _empty()
            raise
        if df is None or df.empty or "Close" not in df.columns:
            return _empty()
        if len(df) < 2:
            return _empty()
        return df

    def fetch_many(self, symbols: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
        try:
            import yfinance as yf
        except Exception:
            return {}
        if len(symbols) == 1:
            return {symbols[0]: self.fetch(symbols[0], period, interval)}
        with _quiet_yfinance_miss_logs():
            frame = yf.download(
                tickers=" ".join(symbols),
                period=period,
                interval=interval,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        if frame is None or frame.empty:
            return {}
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                item = frame[symbol] if isinstance(frame.columns, pd.MultiIndex) else frame
            except KeyError:
                continue
            item = item.dropna(subset=["Close"]) if "Close" in item else _empty()
            if len(item) >= 2:
                result[symbol] = item
        return result


class YahooChartProvider(PriceProvider):
    """Direct hit on Yahoo's v8 chart endpoint (query1.finance.yahoo.com).

    This is the SAME upstream data as yfinance but bypasses the cookie/crumb
    machinery that yfinance.history() uses — which is precisely what Yahoo tends
    to rate-limit. It therefore survives many throttling episodes that knock out
    the primary yfinance path, with no API key required.
    """

    name = "yahoo_chart"
    supported_intervals = frozenset({"1d"})

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
            if _is_expected_miss(exc):
                logger.debug("Yahoo chart has no listing for %s; trying fallback", symbol)
                return _empty()
            raise


class StooqProvider(PriceProvider):
    name = "stooq"
    supported_intervals = frozenset({"1d"})

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
    """Keyed backup behind yfinance (active only when an API key is set)."""

    name = "alphavantage"
    supported_intervals = frozenset({"1d"})

    def enabled(self) -> bool:
        return bool(_api_key("alpha_vantage_key", "ALPHA_VANTAGE_API_KEY", "ALPHA_VANTAGE_KEY"))

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        key = _api_key("alpha_vantage_key", "ALPHA_VANTAGE_API_KEY", "ALPHA_VANTAGE_KEY")
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
                note = payload.get("Note") or payload.get("Information")
                if note:
                    raise RuntimeError(str(note))
                return _empty()
            rows = []
            for d, ohlc in series.items():
                try:
                    rows.append({
                        "Date": d,
                        "Open": float(ohlc.get("1. open", "nan")),
                        "High": float(ohlc.get("2. high", "nan")),
                        "Low": float(ohlc.get("3. low", "nan")),
                        "Close": float(ohlc.get("4. close", "nan")),
                        "Volume": float(ohlc.get("5. volume") or 0),
                    })
                except (TypeError, ValueError):
                    continue
            if not rows:
                return _empty()
            df = pd.DataFrame(rows).set_index("Date").sort_index()
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as exc:  # noqa: BLE001
            raise


class TwelveDataProvider(PriceProvider):
    """Keyed backup behind yfinance (active only when an API key is set)."""

    name = "twelvedata"
    supported_intervals = frozenset({"1d"})

    def enabled(self) -> bool:
        return bool(_api_key("twelve_data_key", "TWELVE_DATA_API_KEY"))

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        key = _api_key("twelve_data_key", "TWELVE_DATA_API_KEY")
        if not key:
            return _empty()
        url = (
            "https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1day&outputsize=500&apikey={key}"
        )
        try:
            import json

            payload = json.loads(_http_text(url))
            if payload.get("status") == "error":
                raise RuntimeError(str(payload.get("message") or payload.get("code") or "Twelve Data error"))
            vals = payload.get("values")
            if not vals:
                return _empty()
            raw = pd.DataFrame(vals)
            try:
                dates = pd.to_datetime(raw["datetime"])
                out = pd.DataFrame(
                    {
                        "Open": pd.to_numeric(raw["open"], errors="coerce").to_numpy(),
                        "High": pd.to_numeric(raw["high"], errors="coerce").to_numpy(),
                        "Low": pd.to_numeric(raw["low"], errors="coerce").to_numpy(),
                        "Close": pd.to_numeric(raw["close"], errors="coerce").to_numpy(),
                        "Volume": pd.to_numeric(raw["volume"], errors="coerce").to_numpy(),
                    },
                    index=dates.to_numpy(),
                )
            except KeyError:
                return _empty()
            out.index = pd.to_datetime(out.index)
            out.index.name = "Date"
            return out.sort_index()
        except Exception as exc:  # noqa: BLE001
            raise


# ---------------------------------------------------------------------------
# Provider registry + circuit breaker
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, PriceProvider] = {
    provider.name: provider
    for provider in (
        TwelveDataProvider(),
        AlphaVantageProvider(),
        YFinanceProvider(),
        YahooChartProvider(),
        StooqProvider(),
    )
}
_DEFAULT_PROVIDER_ORDER = "yfinance,twelvedata,alphavantage,yahoo_chart,stooq"
_provider_order = [
    name.strip().lower()
    for name in os.getenv("STOCK_ALERT_PROVIDER_ORDER", _DEFAULT_PROVIDER_ORDER).split(",")
    if name.strip().lower() in _PROVIDER_REGISTRY
]
_PROVIDERS: list[PriceProvider] = [_PROVIDER_REGISTRY[name] for name in dict.fromkeys(_provider_order)]
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


def _is_expected_miss(exc: Exception) -> bool:
    """Normal provider miss; callers should continue the fallback chain."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status == 404:
        return True
    return _is_expected_miss_message(str(exc))


def _is_expected_miss_message(message: str) -> bool:
    message = message.lower()
    return any(
        token in message
        for token in ("404", "not found", "no data found", "possibly delisted", "quote not found")
    )


@contextmanager
def _quiet_yfinance_miss_logs():
    """Hide yfinance's own error-level logs for expected missing tickers."""
    yf_logger = logging.getLogger("yfinance")

    class _ExpectedMissFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return not _is_expected_miss_message(record.getMessage())

    miss_filter = _ExpectedMissFilter()
    yf_logger.addFilter(miss_filter)
    try:
        yield
    finally:
        yf_logger.removeFilter(miss_filter)


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


def _limit(name: str, suffix: str, default: int) -> int:
    key = f"STOCK_ALERT_{name.upper()}_{suffix}"
    try:
        return max(1, int(os.getenv(key, str(default))))
    except ValueError:
        return default


_POLICY_DEFAULTS = {
    "twelvedata": (6, 700, 2),
    "alphavantage": (5, 25, 1),
    "yfinance": (30, 2000, 5),
    "yahoo_chart": (30, 2000, 5),
    "stooq": (20, 1000, 3),
}
_POLICIES = {
    name: ProviderPolicy(
        per_minute=_limit(name, "PER_MINUTE", defaults[0]),
        daily_budget=_limit(name, "DAILY_BUDGET", defaults[1]),
        burst=_limit(name, "BURST", defaults[2]),
        failure_threshold=_limit(name, "FAILURE_THRESHOLD", 3),
    )
    for name, defaults in _POLICY_DEFAULTS.items()
}
_COORDINATOR = RequestCoordinator(
    _PROVIDERS,
    policies=_POLICIES,
    fresh_ttl=float(os.getenv("STOCK_ALERT_MARKET_DATA_TTL", "300")),
    stale_ttl=float(os.getenv("STOCK_ALERT_MARKET_DATA_STALE_TTL", "3600")),
    cooldown_s=_COOLDOWN,
)


def provider_status() -> list[dict[str, object]]:
    """Human-readable status for each provider (used by the UI/health checks)."""
    return _COORDINATOR.status()


def fetch_market_data(
    symbol: str,
    period: str = "6mo",
    interval: str = "1d",
    *,
    priority: str = "foreground",
) -> MarketDataSnapshot:
    return _COORDINATOR.get_snapshot(symbol, period, interval, priority=priority)


def fetch_market_data_many(
    symbols: Iterable[str],
    period: str = "6mo",
    interval: str = "1d",
    *,
    priority: str = "background",
) -> dict[str, MarketDataSnapshot]:
    return _COORDINATOR.get_many(symbols, period, interval, priority=priority)


def fetch_ohlcv(
    symbol: str,
    period: str = "6mo",
    interval: str = "1d",
    providers: Iterable[PriceProvider] | None = None,
    *,
    priority: str = "foreground",
) -> pd.DataFrame:
    """Fetch OHLCV for ``symbol`` from the first provider that returns data.

    The returned frame has columns Open/High/Low/Close/Volume indexed by Date
    (matching the shape the rest of the pipeline expects from yfinance). Providers
    that error or are cooling down are skipped. Falls back to an empty frame if
    every backend fails.
    """
    if providers is None:
        return fetch_market_data(symbol, period, interval, priority=priority).frame
    chain = list(providers)
    for p in chain:
        if not _provider_available(p):
            continue
        try:
            df = p.fetch(symbol, period, interval)
        except Exception as exc:  # noqa: BLE001
            if _is_expected_miss(exc):
                logger.debug("Provider %s has no listing for %s; trying fallback", p.name, symbol)
            else:
                logger.warning("Provider %s failed for %s: %s", p.name, symbol, exc)
                _record_failure(p.name, rate_limited=_is_rate_limit(exc))
            continue
        if df is not None and not df.empty and len(df) >= 2 and "Close" in df.columns:
            _record_success(p.name)
            logger.debug("Price for %s served by %s", symbol, p.name)
            return df
        # Empty/short frame: this provider had no data for the symbol. Try the next.
    return _empty()
