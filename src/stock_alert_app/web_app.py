from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import Database

app = FastAPI(title="StockVerdict", version="0.1.0")

UI_DIR = Path(__file__).resolve().parent / "web"


def _db() -> Database:
    return Database(settings.db_path)


@app.get("/api/markets")
def list_markets() -> list[dict[str, object]]:
    from .markets import load_markets

    markets = load_markets(settings.markets_dir)
    return [
        {
            "code": m.code,
            "name": m.name,
            "country": m.country,
            "currency": m.currency,
            "tickers": sorted(m.tickers.keys()),
        }
        for m in markets.values()
    ]


@app.get("/api/verdicts")
def get_verdicts(
    market: str | None = None,
    ticker: str | None = None,
    live: bool = False,
) -> dict[str, dict[str, object]]:
    db = _db()
    if live:
        from .verdict import run_verdicts

        market_codes = [market] if market else None
        verdicts = run_verdicts(market_codes=market_codes)
        if ticker:
            verdicts = {k: v for k, v in verdicts.items() if v.ticker.upper() == ticker.upper()}
        return {k: v.as_dict() for k, v in verdicts.items()}

    rows = db.latest_verdicts(market=market)
    if ticker:
        rows = [r for r in rows if r["ticker"].upper() == ticker.upper()]
    return {
        f"{r['market']}:{r['ticker']}": {
            "market": r["market"],
            "ticker": r["ticker"],
            "verdict": r["verdict"],
            "confidence": r["confidence"],
            "news_score": r["news_score"],
            "price_score": r["price_score"],
            "combined_score": r["combined_score"],
            "reason": [r["reason"]] if r["reason"] else [],
            "decided_at": r["decided_at"],
        }
        for r in rows
    }


@app.get("/api/news")
def get_news(market: str, ticker: str, limit: int = 50) -> list[dict[str, object]]:
    db = _db()
    return db.recent_news(market, ticker, limit=limit)


@app.get("/api/history/{market}/{ticker}")
def get_history(market: str, ticker: str) -> list[dict[str, object]]:
    db = _db()
    return db.recent_verdicts(market, ticker, limit=100)


@app.get("/api/discover")
def discover(
    market: str | None = None,
    min_score: float = 0.2,
    max_results: int = 20,
) -> list[dict[str, object]]:
    from .discover import discover_from_feeds

    market_codes = [market] if market else list(settings.default_markets)
    results = discover_from_feeds(
        market_codes,
        min_score=min_score,
        max_new_per_cycle=max_results,
        use_lexicon=True,
    )
    return [
        {
            "market": d.market,
            "ticker": d.ticker,
            "company": d.company,
            "score": d.score,
            "headlines": d.headlines[:5],
            "matched_keywords": d.matched_keywords,
        }
        for d in results
    ]


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")