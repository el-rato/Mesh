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

CREATE TABLE IF NOT EXISTS watchlist (
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (market, ticker)
);

CREATE TABLE IF NOT EXISTS agent_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    rationale TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_tickers (
    ticker TEXT NOT NULL,
    market TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (ticker, market)
);

CREATE TABLE IF NOT EXISTS fund_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    fund_name TEXT NOT NULL DEFAULT '',
    form TEXT NOT NULL DEFAULT '13F-HR',
    accession TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    period_of_report TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    UNIQUE(cik, accession)
);

CREATE TABLE IF NOT EXISTS fund_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_filing_id INTEGER NOT NULL REFERENCES fund_filings(id) ON DELETE CASCADE,
    cusip TEXT NOT NULL DEFAULT '',
    issuer TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    value_thousands REAL NOT NULL DEFAULT 0,
    shares REAL NOT NULL DEFAULT 0,
    shares_type TEXT NOT NULL DEFAULT 'SH',
    put_call TEXT NOT NULL DEFAULT '',
    pct_portfolio REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_fund_holdings_filing ON fund_holdings(fund_filing_id);
CREATE INDEX IF NOT EXISTS idx_fund_holdings_ticker ON fund_holdings(ticker);

CREATE TABLE IF NOT EXISTS index_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    close REAL NOT NULL DEFAULT 0,
    open REAL NOT NULL DEFAULT 0,
    high REAL NOT NULL DEFAULT 0,
    low REAL NOT NULL DEFAULT 0,
    volume INTEGER NOT NULL DEFAULT 0,
    change_pct REAL NOT NULL DEFAULT 0,
    UNIQUE(market, symbol, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_agent_recs_generated ON agent_recommendations(generated_at);
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

    def latest_verdicts(self, market: str | None = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if market:
                rows = conn.execute(
                    """SELECT * FROM verdicts v
                       WHERE v.market = ?
                         AND v.decided_at = (
                             SELECT MAX(v2.decided_at) FROM verdicts v2
                             WHERE v2.market = v.market AND v2.ticker = v.ticker
                         )
                       ORDER BY v.combined_score DESC""",
                    (market,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM verdicts v
                       WHERE v.decided_at = (
                           SELECT MAX(v2.decided_at) FROM verdicts v2
                           WHERE v2.market = v.market AND v2.ticker = v.ticker
                       )
                       ORDER BY v.combined_score DESC"""
                ).fetchall()
            return [dict(r) for r in rows]

    def add_to_watchlist(self, market: str, ticker: str, company: str = "") -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO watchlist (market, ticker, company, added_at)
                   VALUES (?, ?, ?, ?)""",
                (market, ticker.upper(), company, utc_now()),
            )
            return cur.rowcount > 0

    def remove_from_watchlist(self, market: str, ticker: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE market = ? AND ticker = ?",
                (market, ticker.upper()),
            )
            return cur.rowcount > 0

    def watchlist(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY added_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def watchlist_keys(self) -> set[tuple[str, str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT market, ticker FROM watchlist").fetchall()
            return {(r["market"], r["ticker"].upper()) for r in rows}

    def insert_recommendations(self, recommendations: List[Dict[str, Any]]) -> None:
        if not recommendations:
            return
        generated = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO agent_recommendations
                   (market, ticker, company, action, confidence, rationale, generated_at)
                   VALUES (:market, :ticker, :company, :action, :confidence, :rationale, :generated_at)""",
                [
                    {
                        "market": r.get("market", ""),
                        "ticker": r.get("ticker", "").upper(),
                        "company": r.get("company", ""),
                        "action": r.get("action", ""),
                        "confidence": float(r.get("confidence", 0.0)),
                        "rationale": r.get("rationale", ""),
                        "generated_at": generated,
                    }
                    for r in recommendations
                ],
            )

    def latest_recommendations(self, market: str | None = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if market:
                rows = conn.execute(
                    """SELECT * FROM agent_recommendations
                       WHERE market = ?
                         AND generated_at = (
                             SELECT MAX(generated_at) FROM agent_recommendations
                             WHERE market = ?
                         )
                       ORDER BY confidence DESC""",
                    (market, market),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM agent_recommendations
                       WHERE generated_at = (
                           SELECT MAX(generated_at) FROM agent_recommendations
                       )
                       ORDER BY confidence DESC"""
                ).fetchall()
            return [dict(r) for r in rows]

    def get_recently_discovered(self, cooldown_days: int = 7) -> set[str]:
        cutoff = datetime.now(timezone.utc).timestamp() - cooldown_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT ticker FROM discovered_tickers WHERE discovered_at > ?",
                (cutoff_iso,),
            ).fetchall()
            return {r["ticker"].upper() for r in rows}

    def mark_discovered(self, ticker: str, market: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO discovered_tickers (ticker, market, discovered_at) VALUES (?, ?, ?)",
                (ticker.upper(), market, utc_now()),
            )

    def upsert_fund_filing(
        self,
        cik: str,
        fund_name: str,
        form: str,
        accession: str,
        filing_date: str,
        period_of_report: str = "",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO fund_filings (cik, fund_name, form, accession, filing_date, period_of_report, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cik, accession) DO UPDATE SET
                     fund_name=excluded.fund_name, filing_date=excluded.filing_date,
                     period_of_report=excluded.period_of_report, fetched_at=excluded.fetched_at""",
                (cik, fund_name, form, accession, filing_date, period_of_report, utc_now()),
            )
            row = conn.execute(
                "SELECT id FROM fund_filings WHERE cik = ? AND accession = ?", (cik, accession)
            ).fetchone()
            return int(row["id"])

    def replace_fund_holdings(self, fund_filing_id: int, holdings: List[Dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM fund_holdings WHERE fund_filing_id = ?", (fund_filing_id,))
            conn.executemany(
                """INSERT INTO fund_holdings
                   (fund_filing_id, cusip, issuer, ticker, value_thousands, shares, shares_type, put_call, pct_portfolio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        fund_filing_id,
                        h.get("cusip", ""),
                        h.get("issuer", ""),
                        h.get("ticker", ""),
                        h.get("value_thousands", h.get("value", 0.0)),
                        float(h.get("shares", 0.0)),
                        h.get("shares_type", "SH"),
                        h.get("put_call", ""),
                        float(h.get("pct_portfolio", 0.0)),
                    )
                    for h in holdings
                ],
            )

    def fund_filings(self, cik: str | None = None, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if cik:
                rows = conn.execute(
                    "SELECT * FROM fund_filings WHERE cik = ? ORDER BY filing_date DESC LIMIT ?",
                    (cik, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM fund_filings ORDER BY filing_date DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def fund_holdings(self, fund_filing_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fund_holdings WHERE fund_filing_id = ? ORDER BY value_thousands DESC LIMIT ?",
                (fund_filing_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_index_snapshots(self, market: str | None = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if market:
                rows = conn.execute(
                    """SELECT * FROM index_snapshots s
                       WHERE s.market = ?
                         AND s.fetched_at = (
                             SELECT MAX(s2.fetched_at) FROM index_snapshots s2
                             WHERE s2.market = s.market AND s2.symbol = s.symbol
                         )""",
                    (market,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM index_snapshots s
                       WHERE s.fetched_at = (
                           SELECT MAX(s2.fetched_at) FROM index_snapshots s2
                           WHERE s2.market = s.market AND s2.symbol = s.symbol
                       )"""
                ).fetchall()
            return [dict(r) for r in rows]
