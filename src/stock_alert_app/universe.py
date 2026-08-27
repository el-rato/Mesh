"""Canonical security universe.

Single registry for every security the terminal knows about — configured markets,
previously discovered tickers, watchlist items, and dynamically searched/analyzed
symbols. Search, Scanner, Watchlist and Dossier all read the same registry.

A security with missing data stays discoverable with ``data_status = no_data``
instead of silently disappearing.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)


def ensure_seeded(db: Database) -> None:
    """Seed the canonical universe from configured markets + watchlist.

    Idempotent via INSERT ... ON CONFLICT (configured rows keep source). All
    writes happen in a single transaction so the universe can be registered
    without opening a DB connection per ticker.
    """
    from .markets import load_markets

    markets = load_markets(settings.markets_dir)
    rows: list[dict[str, Any]] = []
    for market in markets.values():
        for symbol, tkr in market.tickers.items():
            rows.append(
                {
                    "market": market.code,
                    "ticker": symbol,
                    "symbol": symbol + (tkr.yahoo_suffix or market.yahoo_suffix),
                    "company": tkr.name or "",
                    "exchange": market.name,
                    "currency": market.currency,
                    "source": "configured",
                }
            )
    # Watchlist items join too.
    for w in db.watchlist():
        rows.append(
            {
                "market": w["market"],
                "ticker": w["ticker"],
                "company": w.get("company") or "",
                "source": "watchlist",
            }
        )
    db.upsert_securities_bulk(rows)


def register(
    db: Database,
    market: str,
    ticker: str,
    symbol: str = "",
    company: str = "",
    exchange: str = "",
    currency: str = "",
    source: str = "discovered",
    data_status: str = "no_data",
    last_analysis_at: str = "",
) -> None:
    """Register a security (called when a symbol is actually searched/analyzed)."""
    try:
        db.upsert_security(
            market=market,
            ticker=ticker,
            symbol=symbol,
            company=company,
            exchange=exchange,
            currency=currency,
            source=source,
            data_status=data_status,
            last_analysis_at=last_analysis_at,
        )
    except Exception as exc:  # never let a registry write break analysis
        logger.debug("register security failed %s:%s: %s", market, ticker, exc)


def universe(db: Database, market: str | None = None) -> list[dict[str, Any]]:
    """The canonical universe (seeded once, then the full securities registry)."""
    ensure_seeded(db)
    return db.all_securities(market=market)


def symbol_for(db: Database, market: str, ticker: str) -> str:
    """Provider symbol for a security (canonical symbol-resolution layer)."""
    sec = db.securities_map().get((market, ticker.upper()))
    if sec and sec.get("symbol"):
        return sec["symbol"]
    from .markets import load_markets

    m = load_markets(settings.markets_dir).get(market)
    return f"{ticker}{m.yahoo_suffix if m else ''}"
