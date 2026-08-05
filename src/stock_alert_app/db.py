from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    UNIQUE(market, ticker, url)
);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_item_id INTEGER NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    score REAL NOT NULL,
    label TEXT NOT NULL,
    positive REAL NOT NULL DEFAULT 0,
    negative REAL NOT NULL DEFAULT 0,
    neutral REAL NOT NULL DEFAULT 0,
    scored_at TEXT NOT NULL,
    UNIQUE(news_item_id, model)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    close REAL NOT NULL,
    open REAL NOT NULL DEFAULT 0,
    high REAL NOT NULL DEFAULT 0,
    low REAL NOT NULL DEFAULT 0,
    volume INTEGER NOT NULL DEFAULT 0,
    momentum_20 REAL NOT NULL DEFAULT 0,
    rsi_14 REAL NOT NULL DEFAULT 50,
    sma_50 REAL NOT NULL DEFAULT 0,
    UNIQUE(market, ticker, fetched_at)
);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    news_score REAL NOT NULL DEFAULT 0,
    price_score REAL NOT NULL DEFAULT 0,
    combined_score REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL,
    UNIQUE(market, ticker, decided_at)
);

CREATE INDEX IF NOT EXISTS idx_news_ticker ON news_items(market, ticker);
CREATE INDEX IF NOT EXISTS idx_verdicts_ticker ON verdicts(market, ticker);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def insert_news_item(
        self,
        market: str,
        ticker: str,
        title: str,
        url: str,
        source: str = "",
        summary: str = "",
        published_at: str = "",
    ) -> Optional[int]:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO news_items
                   (market, ticker, source, title, url, summary, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (market, ticker.upper(), source, title, url, summary, published_at, utc_now()),
            )
            if cur.rowcount == 0:
                return None
            return cur.lastrowid

    def insert_sentiment(
        self,
        news_item_id: int,
        model: str,
        score: float,
        label: str,
        positive: float = 0.0,
        negative: float = 0.0,
        neutral: float = 0.0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sentiment_scores
                   (news_item_id, model, score, label, positive, negative, neutral, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (news_item_id, model, score, label, positive, negative, neutral, utc_now()),
            )

    def insert_price_snapshot(
        self,
        market: str,
        ticker: str,
        close: float,
        open: float = 0.0,
        high: float = 0.0,
        low: float = 0.0,
        volume: int = 0,
        momentum_20: float = 0.0,
        rsi_14: float = 50.0,
        sma_50: float = 0.0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO price_snapshots
                   (market, ticker, fetched_at, close, open, high, low, volume, momentum_20, rsi_14, sma_50)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (market, ticker.upper(), utc_now(), close, open, high, low, volume, momentum_20, rsi_14, sma_50),
            )

    def insert_verdict(
        self,
        market: str,
        ticker: str,
        verdict: str,
        confidence: float,
        news_score: float,
        price_score: float,
        combined_score: float,
        reason: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO verdicts
                   (market, ticker, verdict, confidence, news_score, price_score, combined_score, reason, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (market, ticker.upper(), verdict, confidence, news_score, price_score, combined_score, reason, utc_now()),
            )

    def recent_news(self, market: str, ticker: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT n.*, s.score AS sentiment_score, s.label AS sentiment_label
                   FROM news_items n
                   LEFT JOIN sentiment_scores s ON s.news_item_id = n.id
                   WHERE n.market = ? AND n.ticker = ?
                   ORDER BY n.published_at DESC
                   LIMIT ?""",
                (market, ticker.upper(), limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_verdicts(self, market: str, ticker: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM verdicts
                   WHERE market = ? AND ticker = ?
                   ORDER BY decided_at DESC
                   LIMIT ?""",
                (market, ticker.upper(), limit),
            ).fetchall()
            return [dict(r) for r in rows]
