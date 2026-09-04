from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import yfinance as yf

from .config import settings
from .db import Database

try:
    import pandas as pd
except Exception:  # pragma: no cover - pandas ships with yfinance
    pd = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 20.0

# Yahoo chart-API range tokens (daily interval) for the direct fallback.
_YAHOO_RANGE = {
    "1d": "1d", "1w": "5d", "1mo": "1mo", "3mo": "3mo",
    "6mo": "6mo", "1y": "1y", "all": "max",
}

logger = logging.getLogger(__name__)

#: In-process cache of chart series so repeated views (dossier, ticker strip,
#: scanner thumbnails) don't re-hit the live provider and trip rate limits.
#: TTL configurable via STOCK_ALERT_HISTORY_TTL.
try:
    _HISTORY_TTL = float(settings.chart_history_ttl)
except Exception:  # pragma: no cover
    _HISTORY_TTL = 1800.0  # 30 minutes

_HISTORY_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

#: Per-range fallback chain of (period, interval) tuples. Intraday ranges
#: (1d/1w) first try a fine interval, then progressively coarser ones, finally
#: daily — yfinance frequently returns empty intraday bars for non-US markets,
#: off-hours, or under rate limiting, and an empty result must not blank a chart.
_RANGE_FALLBACKS: dict[str, list[tuple[str, str]]] = {
    "1d": [("1d", "5m"), ("1d", "1h"), ("5d", "30m"), ("1mo", "1d")],
    "1w": [("5d", "30m"), ("1mo", "1d"), ("3mo", "1d")],
    "1mo": [("1mo", "1d"), ("3mo", "1d")],
    "3mo": [("3mo", "1d"), ("6mo", "1d")],
    "6mo": [("6mo", "1d"), ("1y", "1d")],
    "1y": [("1y", "1d"), ("2y", "1d")],
}


def _fetch_yf(symbol: str, period: str, interval: str) -> Any | None:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:  # network / rate-limit / invalid symbol
        logger.warning("Failed to load chart history for %s: %s", symbol, exc)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    return df


def _yahoo_variants(symbol: str) -> list[str]:
    """Suffix variants to try on a 404 (e.g. Indian tickers trade on both
    NSE `.NS` and BSE `.BO` — one may be absent from Yahoo while the other
    resolves). Cheap, best-effort coverage boost."""
    variants = [symbol]
    swaps = {".BO": ".NS", ".NS": ".BO", ".L": ".IL", ".IL": ".L"}
    for a, b in swaps.items():
        if symbol.upper().endswith(a):
            variants.append(symbol[: -len(a)] + b)
            break
    return variants


# Yahoo/yfinance exchange suffix -> Alpha Vantage exchange suffix.
_AV_SUFFIX = {
    ".AX": ".AX",    # ASX (Australia)
    ".BO": ".BOM",   # BSE (India)
    ".PA": ".PAR",   # Euronext Paris
    ".HK": ".HKG",   # Hong Kong
    ".KS": ".KRX",   # Korea
    ".L": ".LON",    # London
    ".SI": ".SGX",   # Singapore
    ".SW": ".SWX",   # Switzerland
    ".T": ".TSE",    # Tokyo
    ".TO": ".TOR",   # Toronto
    ".DE": ".DEX",   # Xetra / Frankfurt
    ".NS": ".NS",    # NSE (India) — Alpha Vantage uses .NS directly
    ".BOM": ".BO",   # reverse maps, just in case
    ".LON": ".L",
}


def _alpha_vantage_symbols(symbol: str) -> list[str]:
    """Alpha Vantage uses its own suffixes (e.g. BSE is `.BOM`, LSE is `.LON`),
    so emit the mapped form. Deduplicated, original first."""
    s = symbol.upper()
    out = [s]
    for yahoo_s, av_s in _AV_SUFFIX.items():
        if s.endswith(yahoo_s) and not s.endswith(av_s):
            cand = s[: -len(yahoo_s)] + av_s
            if cand not in out:
                out.append(cand)
            break
    return out


def _av_interval_for_range(range_key: str) -> str | None:
    """Intraday interval for short ranges; None => use daily series."""
    if range_key == "1d":
        return "5min"
    if range_key == "1w":
        return "60min"
    return None


def _fetch_alpha_vantage(symbol: str, range_key: str, api_key: str) -> Any | None:
    """Keyed backup (tier 3) via Alpha Vantage. Used only when yfinance and the
    direct Yahoo chart API both fail. Uses TIME_SERIES_INTRADAY for 1d/1w (true
    intraday) and TIME_SERIES_DAILY otherwise. Results are cached in-memory and
    in the DB so the free 25-requests/day quota is not exhausted by repeat views.
    Returns a DataFrame shaped like yfinance's, or None on failure / quota."""
    if pd is None:
        return None
    interval = _av_interval_for_range(range_key)
    func = "TIME_SERIES_INTRADAY" if interval else "TIME_SERIES_DAILY"
    series_key = f"Time Series ({interval})" if interval else "Time Series (Daily)"
    base_params = {"function": func, "apikey": api_key, "outputsize": "full"}
    if interval:
        base_params["interval"] = interval
    for sym in _alpha_vantage_symbols(symbol):
        params = {**base_params, "symbol": sym}
        try:
            resp = httpx.get(
                "https://www.alphavantage.co/query",
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            j = resp.json()
            series = j.get(series_key)
            if not series:
                note = j.get("Note") or j.get("Information") or "no series"
                logger.warning("Alpha Vantage no data for %s (%s): %s", sym, func, note)
                continue
            rows = []
            for date_str, ohlc in series.items():
                try:
                    rows.append({
                        "Date": pd.Timestamp(date_str),
                        "Open": float(ohlc.get("1. open", "nan")),
                        "High": float(ohlc.get("2. high", "nan")),
                        "Low": float(ohlc.get("3. low", "nan")),
                        "Close": float(ohlc.get("4. close", "nan")),
                        "Volume": float(ohlc.get("5. volume") or 0),
                    })
                except (TypeError, ValueError):
                    continue
            if not rows:
                continue
            return pd.DataFrame(rows).set_index("Date").sort_index()
        except Exception as exc:  # network / parse / quota
            logger.warning("Alpha Vantage fallback failed for %s: %s", sym, exc)
    return None


def _fetch_yahoo_direct(symbol: str, range_key: str) -> Any | None:
    """Backup to yfinance: call Yahoo's chart API directly (no yfinance wrapper),
    so a yfinance rate-limit/version break doesn't blank the chart. Tries both
    query hosts and a couple of suffix variants. Returns a DataFrame shaped like
    yfinance's, or None on failure."""
    if pd is None:
        return None
    rng = _YAHOO_RANGE.get(range_key, "1mo")
    tried = set()
    for sym in _yahoo_variants(symbol):
        if sym in tried:
            continue
        tried.add(sym)
        for host in ("query1", "query2"):
            url = (
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
                f"?range={rng}&interval=1d"
            )
            try:
                resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
                if resp.status_code == 404:
                    continue  # symbol not on this host — try the next variant/host
                resp.raise_for_status()
                result = resp.json().get("chart", {}).get("result")
                if not result:
                    continue
                r0 = result[0]
                ts = r0.get("timestamp")
                q = r0.get("indicators", {}).get("quote", [{}])[0]
                closes = q.get("close") or []
                if not ts or not closes:
                    continue
                n = len(ts)
                rows = []
                for i in range(n):
                    rows.append({
                        "Date": pd.Timestamp(ts[i], unit="s", tz="UTC"),
                        "Open": (q.get("open") or [None] * n)[i],
                        "High": (q.get("high") or [None] * n)[i],
                        "Low": (q.get("low") or [None] * n)[i],
                        "Close": closes[i],
                        "Volume": (q.get("volume") or [None] * n)[i],
                    })
                return pd.DataFrame(rows).set_index("Date").sort_index()
            except Exception as exc:  # network / rate-limit / invalid symbol
                logger.warning("Yahoo-direct fallback (%s) failed for %s: %s", host, sym, exc)
    return None


def _df_to_rows(df: Any) -> list[dict[str, Any]]:
    """Convert a yfinance DataFrame into the chart row shape used by the UI."""
    rows: list[dict[str, Any]] = []
    closes: list[float | None] = []
    for _, row in df.iterrows():
        close = row.get("Close")
        try:
            closes.append(float(close) if close is not None else None)
        except (TypeError, ValueError):
            closes.append(None)
    for position, (idx, row) in enumerate(df.iterrows()):
        close = closes[position]
        if close is None:
            continue
        try:
            open_ = float(row.get("Open", 0.0))
            high = float(row.get("High", 0.0))
            low = float(row.get("Low", 0.0))
            volume = int(row.get("Volume", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) for value in (open_, high, low, close)):
            continue

        def moving_average(window: int) -> float | None:
            values = [v for v in closes[max(0, position - window + 1) : position + 1] if v is not None]
            return round(sum(values) / window, 4) if len(values) == window else None

        rows.append(
            {
                "date": idx.strftime("%Y-%m-%d %H:%M"),
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": volume,
                "sma_50": moving_average(50),
                "sma_200": moving_average(200),
            }
        )
    return rows


def index_history(symbol: str, range_key: str = "1mo") -> list[dict[str, Any]]:
    """Return OHLC + volume series for an index/stock at a given range.

    Resilient by design: it walks a per-range fallback chain of (period,
    interval) pairs, serves a fresh in-memory/DB cache, and — as a last resort —
    returns the most recently persisted series for the symbol so the chart is
    never blank when the live provider is unavailable.
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return []
    range_key = range_key or "1mo"
    cache_key = (symbol.upper(), range_key)
    now = time.time()
    cached = _HISTORY_CACHE.get(cache_key)
    if cached and now - cached[0] < _HISTORY_TTL:
        return cached[1]

    candidates = _RANGE_FALLBACKS.get(range_key, _RANGE_FALLBACKS["1mo"])
    df = None
    for period, interval in candidates:
        df = _fetch_yf(symbol, period, interval)
        if df is not None and not getattr(df, "empty", True):
            break
    rows = _df_to_rows(df) if df is not None else []

    # Backup provider when yfinance returns nothing (rate-limited / offline):
    # call Yahoo's chart API directly (no yfinance wrapper). This recovers the
    # chart for symbols Yahoo still serves even when yfinance is throttled.
    if not rows:
        sdf = _fetch_yahoo_direct(symbol, range_key)
        if sdf is not None:
            rows = _df_to_rows(sdf)

    # Tier 3: keyed Alpha Vantage fallback. Covers symbols Yahoo doesn't serve
    # (e.g. some Indian/UK listings) without burning the yfinance quota. Cached so
    # the free 25-requests/day quota lasts.
    if not rows:
        av_key = settings.alpha_vantage_key or os.environ.get("ALPHA_VANTAGE_KEY") or ""
        if av_key:
            adf = _fetch_alpha_vantage(symbol, range_key, av_key)
            if adf is not None:
                rows = _df_to_rows(adf)

    if rows:
        _HISTORY_CACHE[cache_key] = (now, rows)
        try:
            Database(settings.db_path).upsert_price_history(symbol, range_key, rows)
        except Exception as exc:  # caching must never break the chart
            logger.warning("Failed to cache price history for %s: %s", symbol, exc)
        return rows

    # Live provider returned nothing — fall back to the last persisted series so
    # a previously-fetched chart still renders instead of "CHART UNAVAILABLE".
    try:
        stored = Database(settings.db_path).get_price_history(symbol, range_key)
    except Exception as exc:
        logger.warning("Failed to read cached price history for %s: %s", symbol, exc)
        stored = None
    if stored:
        _HISTORY_CACHE[cache_key] = (now, stored)
        return stored

    _HISTORY_CACHE[cache_key] = (now, [])
    return []

# Market code -> list of benchmark index funds (yahoo symbols).
MARKET_INDEXES: dict[str, list[dict[str, str]]] = {
    "NYSE": [
        {"name": "S&P 500", "symbol": "^GSPC"},
        {"name": "Dow Jones", "symbol": "^DJI"},
        {"name": "NASDAQ Composite", "symbol": "^IXIC"},
        {"name": "S&P 500 VIX", "symbol": "^VIX"},
    ],
    "BSE": [
        {"name": "S&P BSE Sensex", "symbol": "^BSESN"},
        {"name": "NIFTY 50", "symbol": "^NSEI"},
    ],
    "LSE": [
        {"name": "FTSE 100", "symbol": "^FTSE"},
        {"name": "FTSE 250", "symbol": "^FTMC"},
    ],
    "TSE": [
        {"name": "Nikkei 225", "symbol": "^N225"},
        {"name": "TOPIX", "symbol": "^TOPX"},
    ],
    "KRX": [
        {"name": "KOSPI", "symbol": "^KS11"},
        {"name": "KOSDAQ", "symbol": "^KQ11"},
    ],
    "HKEX": [
        {"name": "Hang Seng", "symbol": "^HSI"},
        {"name": "Hang Seng Tech", "symbol": "^HSTECH"},
    ],
    "ASX": [
        {"name": "S&P/ASX 200", "symbol": "^AXJO"},
    ],
    "XETRA": [
        {"name": "DAX", "symbol": "^GDAXI"},
        {"name": "MDAX", "symbol": "^MDAXI"},
        {"name": "TecDAX", "symbol": "^TDXP"},
    ],
    "TSX": [
        {"name": "S&P/TSX Composite", "symbol": "^GSPTSE"},
        {"name": "S&P/TSX 60", "symbol": "^TSE60"},
    ],
    "SGX": [
        {"name": "Straits Times", "symbol": "^STI"},
    ],
}


@dataclass
class IndexSnapshot:
    market: str
    symbol: str
    name: str
    close: float
    open: float
    high: float
    low: float
    volume: int
    change_pct: float
    fetched_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "symbol": self.symbol,
            "name": self.name,
            "close": round(self.close, 2),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "volume": self.volume,
            "change_pct": round(self.change_pct, 4),
            "fetched_at": self.fetched_at,
        }


def fetch_index_snapshots(market_codes: list[str] | None = None) -> list[IndexSnapshot]:
    """Fetch current snapshots for all benchmark indexes of the given markets."""
    codes = market_codes or list(MARKET_INDEXES.keys())
    snapshots: list[IndexSnapshot] = []
    for code in codes:
        indexes = MARKET_INDEXES.get(code, [])
        for idx in indexes:
            try:
                df = yf.Ticker(idx["symbol"]).history(
                    period="1mo", interval="1d", auto_adjust=True
                )
                if df is None or df.empty:
                    logger.warning("No data for index %s", idx["symbol"])
                    continue
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                close = float(last["Close"])
                prev_close = float(prev["Close"])
                change_pct = (close - prev_close) / prev_close if prev_close else 0.0
                snapshots.append(
                    IndexSnapshot(
                        market=code,
                        symbol=idx["symbol"],
                        name=idx["name"],
                        close=close,
                        open=float(last.get("Open", close)),
                        high=float(last.get("High", close)),
                        low=float(last.get("Low", close)),
                        volume=int(float(last.get("Volume", 0) or 0)),
                        change_pct=change_pct,
                        fetched_at=datetime.now(UTC).isoformat(),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to fetch index %s: %s", idx["symbol"], exc)
    return snapshots


def run_index_fetch(
    market_codes: list[str] | None = None, db_path: str | None = None
) -> list[IndexSnapshot]:
    db = Database(db_path or settings.db_path)
    db.init_schema()
    snapshots = fetch_index_snapshots(market_codes)
    with db.connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO index_snapshots
               (market, symbol, name, fetched_at, close, open, high, low, volume, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s.market,
                    s.symbol,
                    s.name,
                    s.fetched_at,
                    s.close,
                    s.open,
                    s.high,
                    s.low,
                    s.volume,
                    s.change_pct,
                )
                for s in snapshots
            ],
        )
    return snapshots
