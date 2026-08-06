from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .db import Database

app = FastAPI(title="StockVerdict", version="0.1.0")

UI_DIR = Path(__file__).resolve().parent / "web"


class WatchItem(BaseModel):
    market: str
    ticker: str
    company: str = ""


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


@app.get("/api/watchlist")
def get_watchlist() -> list[dict[str, object]]:
    db = _db()
    db.init_schema()
    latest = {f"{r['market']}:{r['ticker']}": r for r in db.latest_verdicts()}
    out: list[dict[str, object]] = []
    for w in db.watchlist():
        item: dict[str, object] = {
            "market": w["market"],
            "ticker": w["ticker"],
            "company": w["company"],
            "added_at": w["added_at"],
        }
        v = latest.get(f"{w['market']}:{w['ticker']}")
        if v:
            item["verdict"] = v["verdict"]
            item["confidence"] = v["confidence"]
            item["news_score"] = v["news_score"]
            item["price_score"] = v["price_score"]
            item["combined_score"] = v["combined_score"]
            item["reason"] = [v["reason"]] if v["reason"] else []
            item["decided_at"] = v["decided_at"]
        out.append(item)
    return out


@app.post("/api/watchlist")
def add_watchlist(item: WatchItem) -> dict[str, object]:
    if not item.market or not item.ticker:
        raise HTTPException(status_code=422, detail="market and ticker are required")
    db = _db()
    db.init_schema()
    added = db.add_to_watchlist(item.market, item.ticker, item.company)
    response: dict[str, object] = {"added": added, "market": item.market.upper(), "ticker": item.ticker.upper()}
    if added:
        from .verdict import live_verdict

        v = live_verdict(item.market, item.ticker, item.company)
        if v is not None:
            response["verdict"] = v.as_dict()
    return response


@app.delete("/api/watchlist")
def delete_watchlist(market: str, ticker: str) -> dict[str, object]:
    db = _db()
    removed = db.remove_from_watchlist(market, ticker)
    return {"removed": removed, "market": market.upper(), "ticker": ticker.upper()}


@app.get("/api/discover")
def discover(
    market: str | None = None,
    min_score: float = 0.2,
    min_articles: int = 5,
    max_results: int = 20,
) -> list[dict[str, object]]:
    from .discover import discover_from_feeds

    market_codes = [market] if market else list(settings.default_markets)
    results = discover_from_feeds(
        market_codes,
        min_score=min_score,
        min_articles=min_articles,
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
            "article_count": len(d.headlines),
            "matched_keywords": d.matched_keywords,
        }
        for d in results
    ]


@app.get("/api/agent")
def agent_recommendations(
    market: str | None = None,
    live: bool = False,
) -> dict[str, object]:
    """Return Gemini trading recommendations.

    When live=1, orchestrates fresh data and asks Gemini now (may take a while).
    Otherwise returns the latest persisted recommendations.
    """
    db = _db()
    db.init_schema()
    if live:
        from .agent import run_agent

        market_codes = [market] if market else None
        recs = run_agent(market_codes=market_codes)
        items = [r.as_dict() for r in recs]
        latest = db.latest_recommendations(market=market)
        return {
            "live": True,
            "generated_at": latest[-1]["generated_at"] if latest else "",
            "recommendations": items,
        }

    rows = db.latest_recommendations(market=market)
    generated = rows[-1]["generated_at"] if rows else ""
    return {
        "live": False,
        "generated_at": generated,
        "recommendations": [
            {
                "market": r["market"],
                "ticker": r["ticker"],
                "company": r["company"],
                "action": r["action"],
                "confidence": r["confidence"],
                "rationale": r["rationale"],
            }
            for r in rows
        ],
    }


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")