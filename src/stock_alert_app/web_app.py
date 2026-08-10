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
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _ui_assets_dir() -> Path:
    if (FRONTEND_DIR / "index.html").is_file():
        return FRONTEND_DIR
    return UI_DIR


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
    provider: str = "gemini",
    model: str | None = None,
) -> dict[str, object]:
    """Return LLM trading recommendations.

    When live=1, orchestrates fresh data and asks the LLM now (may take a while).
    Otherwise returns the latest persisted recommendations.
    """
    db = _db()
    db.init_schema()
    if live:
        from .agent import run_agent

        try:
            market_codes = [market] if market else None
            recs = run_agent(market_codes=market_codes, provider=provider, model=model)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
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


@app.get("/api/analyze")
def analyze_ticker(
    market: str,
    ticker: str,
    company: str = "",
    provider: str = "gemini",
    model: str | None = None,
) -> dict[str, object]:
    """Deep-dive LLM analysis for a single, user-selected ticker."""
    if not market or not ticker:
        raise HTTPException(status_code=422, detail="market and ticker are required")
    from .agent import run_agent_analysis

    try:
        analysis = run_agent_analysis(market_code=market, ticker=ticker, company=company, provider=provider, model=model)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return analysis.as_dict()


@app.get("/api/risk")
def risk_analysis(
    tickers: str,
    period: str = "2y",
    risk_aversion: float = 3.0,
) -> dict[str, object]:
    """LSTM + Black-Litterman risk analysis for comma-separated tickers."""
    if not tickers:
        raise HTTPException(status_code=422, detail="tickers parameter required")
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    from .models import run_risk_analysis

    try:
        results = run_risk_analysis(ticker_list, period=period, risk_aversion=risk_aversion)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"results": [r.as_dict() for r in results]}


@app.get("/api/risk/portfolio")
def portfolio_risk(
    tickers: str,
    period: str = "2y",
    risk_aversion: float = 3.0,
) -> dict[str, object]:
    """Portfolio-level Black-Litterman optimization."""
    if not tickers:
        raise HTTPException(status_code=422, detail="tickers parameter required")
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    from .models import run_portfolio_risk_analysis

    try:
        result = run_portfolio_risk_analysis(ticker_list, period=period, risk_aversion=risk_aversion)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return result or {"error": "insufficient data"}


@app.get("/api/reddit")
def reddit_recommendations(
    subreddits: str = "",
    limit: int = 50,
    time_filter: str = "day",
    min_mentions: int = 2,
    min_score: int = 10,
) -> dict[str, object]:
    """Reddit stock recommendations from subreddit scanning."""
    sub_list = [s.strip() for s in subreddits.split(",") if s.strip()] if subreddits else None
    from .reddit_scanner import run_reddit_scan

    try:
        recs = run_reddit_scan(
            subreddits=sub_list,
            limit_per_sub=limit,
            time_filter=time_filter,
            min_mentions=min_mentions,
            min_score=min_score,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"recommendations": [r.as_dict() for r in recs]}


@app.get("/api/funds")
def hedge_funds() -> list[dict[str, object]]:
    """Latest 13F summary for each tracked hedge fund."""
    from .institutional import fund_summaries

    return fund_summaries(_db())


@app.get("/api/funds/refresh")
def refresh_hedge_funds() -> dict[str, object]:
    """Fetch the latest 13F filings from SEC EDGAR for all tracked funds."""
    from .institutional import run_institutional_fetch, fund_summaries

    filings = run_institutional_fetch()
    return {
        "fetched": [f.fund_name for f in filings],
        "summaries": fund_summaries(_db()),
    }


@app.get("/api/funds/{cik}")
def hedge_fund_detail(cik: str) -> dict[str, object]:
    """Detailed holdings + quarterly changes for one fund."""
    from .institutional import compute_quarterly_changes

    db = _db()
    db.init_schema()
    filings = db.fund_filings(cik=cik, limit=2)
    if not filings:
        raise HTTPException(status_code=404, detail="fund not found")
    latest = filings[0]
    changes = compute_quarterly_changes(cik, db)
    return {
        "cik": latest["cik"],
        "fund": latest["fund_name"],
        "form": latest["form"],
        "filing_date": latest["filing_date"],
        "period_of_report": latest["period_of_report"],
        "holdings": [
            {
                "cusip": h["cusip"],
                "issuer": h["issuer"],
                "ticker": h["ticker"],
                "value": h["value_thousands"],
                "shares": h["shares"],
                "shares_type": h["shares_type"],
                "put_call": h["put_call"],
                "pct_portfolio": h["pct_portfolio"],
            }
            for h in db.fund_holdings(latest["id"], limit=500)
        ],
        "changes": [
            {
                "ticker": c.ticker,
                "issuer": c.issuer,
                "action": c.action,
                "prev_shares": c.prev_shares,
                "curr_shares": c.curr_shares,
                "change_shares": c.change_shares,
                "change_pct": round(c.change_pct, 4),
                "value": c.value_thousands,
            }
            for c in changes
        ],
    }


@app.get("/api/indexes")
def get_indexes(market: str | None = None) -> list[dict[str, object]]:
    """Latest index snapshots, optionally filtered by market."""
    db = _db()
    db.init_schema()
    snapshots = db.latest_index_snapshots(market=market)
    return [
        {
            "market": s["market"],
            "symbol": s["symbol"],
            "name": s["name"],
            "close": s["close"],
            "open": s["open"],
            "high": s["high"],
            "low": s["low"],
            "volume": s["volume"],
            "change_pct": s["change_pct"],
            "fetched_at": s["fetched_at"],
        }
        for s in snapshots
    ]


@app.get("/api/indexes/refresh")
def refresh_indexes(market: str | None = None) -> list[dict[str, object]]:
    """Fetch fresh index snapshots and return them."""
    from .indexes import run_index_fetch

    codes = [market] if market else None
    snapshots = run_index_fetch(codes)
    return [s.as_dict() for s in snapshots]


@app.get("/api/indexes/{symbol}/history")
def index_history(symbol: str, range: str = "1mo") -> dict[str, object]:
    """OHLC history for an index at a given range (1d/1w/1mo/1y/all)."""
    from .indexes import index_history as fetch_history

    rows = fetch_history(symbol, range)
    return {"symbol": symbol, "range": range, "data": rows}


@app.get("/api/chart/{market}/{ticker}")
def chart_data(market: str, ticker: str, range: str = "1mo") -> dict[str, object]:
    """Stock chart OHLC data for any ticker at a given range (1d/1w/1mo/1y/all)."""
    from .indexes import index_history as fetch_history
    from .markets import load_markets

    markets = load_markets(settings.markets_dir)
    m = markets.get(market.upper())
    suffix = m.yahoo_suffix if m else ""
    symbol = f"{ticker.upper()}{suffix}"
    rows = fetch_history(symbol, range)
    return {"market": market.upper(), "ticker": ticker.upper(), "symbol": symbol, "range": range, "data": rows}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_ui_assets_dir() / "index.html")


# SPA fallback: serve built React assets from frontend/dist when present, or
# any unmatched path falls back to index.html so client-side routes work.
if (FRONTEND_DIR / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")