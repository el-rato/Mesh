from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: In-process cache of chart series so repeated views (dossier, ticker strip,
#: scanner thumbnails) don't re-hit the live provider and trip rate limits.
_HISTORY_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_HISTORY_TTL = 1800.0  # 30 minutes

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
