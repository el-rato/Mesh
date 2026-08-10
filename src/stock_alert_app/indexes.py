from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

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


# Range key -> (period, interval) mapping for chart-style history.
CHART_RANGES: dict[str, dict[str, str]] = {
    "1d": {"period": "1d", "interval": "5m"},
    "1w": {"period": "5d", "interval": "30m"},
    "1mo": {"period": "1mo", "interval": "1h"},
    "1y": {"period": "1y", "interval": "1d"},
    "all": {"period": "max", "interval": "1wk"},
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


def index_history(symbol: str, range_key: str = "1mo") -> list[dict[str, Any]]:
    """Return OHLC + volume series for an index/stock at a given range."""
    cfg = CHART_RANGES.get(range_key, CHART_RANGES["1mo"])
    df = yf.Ticker(symbol).history(period=cfg["period"], interval=cfg["interval"], auto_adjust=True)
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        rows.append(
            {
                "date": idx.strftime("%Y-%m-%d %H:%M"),
                "open": round(float(row.get("Open", 0.0)), 4),
                "high": round(float(row.get("High", 0.0)), 4),
                "low": round(float(row.get("Low", 0.0)), 4),
                "close": round(float(row.get("Close", 0.0)), 4),
                "volume": int(row.get("Volume", 0) or 0),
            }
        )
    return rows


def fetch_index_snapshots(market_codes: list[str] | None = None) -> list[IndexSnapshot]:
    """Fetch current snapshots for all benchmark indexes of the given markets."""
    codes = market_codes or list(MARKET_INDEXES.keys())
    snapshots: list[IndexSnapshot] = []
    for code in codes:
        indexes = MARKET_INDEXES.get(code, [])
        for idx in indexes:
            try:
                df = yf.Ticker(idx["symbol"]).history(period="1mo", interval="1d", auto_adjust=True)
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
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to fetch index %s: %s", idx["symbol"], exc)
    return snapshots


def run_index_fetch(market_codes: list[str] | None = None, db_path: str | None = None) -> list[IndexSnapshot]:
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
