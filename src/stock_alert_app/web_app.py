from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .db import Database, utc_now

logger = logging.getLogger(__name__)

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


_initialized_dbs: set[str] = set()


def _db() -> Database:
    db = Database(settings.db_path)
    key = str(db.path.resolve())
    if key not in _initialized_dbs:
        db.init_schema()
        _initialized_dbs.add(key)
    return db


def _analysis_context(
    db: Database, market: str | None = None
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], dict]:
    """Load stored verdicts + latest price snapshots + markets for analysis."""
    from .analysis import stock_analysis  # noqa: F401 (kept as the canonical builder)
    from .markets import load_markets

    rows = db.latest_verdicts(market=market)
    snaps = {
        f"{s['market']}:{s['ticker'].upper()}": s
        for s in db.latest_price_snapshots(market=market)
    }
    return rows, snaps, load_markets(settings.markets_dir)


@app.get("/api/search")
def search_tickers(
    q: str = "",
    market: str | None = None,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Search the whole supported universe (configured + dynamically discovered).

    Configured 'featured' tickers rank first; results are enriched with the
    canonical stored analysis (committee verdict) when one already exists.
    """
    query = (q or "").strip()
    if not query:
        return []

    from . import instruments
    from .analysis import stock_analysis

    db = _db()
    rows, snaps, markets = _analysis_context(db, market)
    latest = {(r["market"], r["ticker"].upper()): r for r in rows}
    results = instruments.search_universe(query, limit=limit, market_filter=market)
    out: list[dict[str, object]] = []
    for item in results:
        row = latest.get((item.get("market"), (item.get("ticker") or "").upper()))
        if row:
            analysis = stock_analysis(
                row, snaps.get(f"{row['market']}:{row['ticker'].upper()}"), markets
            )
            out.append(
                {
                    **item,
                    "verdict": analysis["verdict"],
                    "confidence": analysis["confidence"],
                    "combined_score": analysis["combined_score"],
                    "reason": analysis["reason"],
                }
            )
        else:
            out.append(item)
    return out


def _dossier_target(
    symbol: str, market: str, ticker: str
) -> tuple[dict[str, object], str]:
    """Resolve a dossier request into (instrument, resolved_symbol) or raise 422/404."""
    if symbol:
        from . import instruments

        item = instruments.resolve_symbol(symbol)
        if item is None:
            raise HTTPException(
                status_code=404, detail=f"Could not resolve symbol {symbol!r}"
            )
        full = item["symbol"]
        return item, full

    if market and ticker:
        from .markets import load_markets
        from .resolve import resolve_for_fetch

        m = load_markets(settings.markets_dir).get(market.upper())
        if m is None:
            raise HTTPException(status_code=422, detail=f"Unknown market {market!r}")
        try:
            tkr = m.get_ticker(ticker)
            company = tkr.name or ""
            composed = f"{ticker.upper()}{tkr.yahoo_suffix or m.yahoo_suffix}"
        except KeyError:
            company = ""
            composed = f"{ticker.upper()}{m.yahoo_suffix}"
        # Prefer the resolver-validated symbol (handles ticker changes without
        # substituting a different security); fall back to the composed symbol
        # so stored-snapshot dossiers keep working.
        full = resolve_for_fetch(m.code, ticker.upper(), company) or composed
        item: dict[str, object] = {
            "market": m.code,
            "ticker": ticker.upper(),
            "symbol": full,
            "company": company,
            "exchange": m.name,
            "quote_type": "EQUITY",
            "supported": True,
            "featured": ticker.upper() in m.tickers,
            "source": "local",
        }
        return item, full

    raise HTTPException(status_code=422, detail="Provide symbol OR market + ticker")


@app.get("/api/dossier")
def stock_dossier(
    symbol: str = "",
    market: str = "",
    ticker: str = "",
    fresh: bool = False,
) -> dict[str, object]:
    """Full stock dossier: verdict + committee + bull/bear + model + news + 13F.

    ``fresh=false`` (default) reuses a stored verdict when one exists (fast, no
    network); ``fresh=true`` runs the complete live pipeline via ``live_verdict``.
    """
    from . import dossier, institutional

    item, full = _dossier_target(symbol, market, ticker)
    if not item.get("supported"):
        raise HTTPException(
            status_code=422,
            detail=f"{item.get('symbol')} ({item.get('exchange') or 'unknown exchange'}) is not on a supported exchange",
        )

    mkt = item["market"]
    tkr = item["ticker"]
    db = _db()

    stored = None
    try:
        rows = db.latest_verdicts(market=mkt)
        stored = next((r for r in rows if r["ticker"].upper() == tkr.upper()), None)
    except Exception:
        stored = None

    verdict_dict: dict[str, object]
    computed_at = ""
    if fresh or stored is None:
        from .verdict import live_verdict

        try:
            # No forced symbol: live_verdict validates through the symbol
            # resolution layer before any price/LSTM work.
            v = live_verdict(mkt, tkr, item.get("company") or "")
        except Exception as exc:
            logger.exception("Dossier live verdict failed for %s", full)
            raise HTTPException(
                status_code=503, detail=f"Analysis failed for {full}: {exc}"
            )
        if v is None:
            detail = "no data available"
            try:
                from .resolve import resolution, status_label

                res = resolution(mkt, tkr, item.get("company") or "")
                note = res.get("note") or status_label(str(res.get("status")))
                if note:
                    detail = note
            except Exception:
                pass
            raise HTTPException(
                status_code=404,
                detail=f"Data unavailable: {detail}",
            )
        # A resolvable-but-unknown symbol (e.g. a delisted ticker) yields a
        # no-data verdict: treat it as not found rather than a bogus 200.
        if v.price is None and not v.news_available:
            raise HTTPException(
                status_code=404,
                detail=f"Data unavailable for {full}",
            )
        verdict_dict = v.as_dict()
        computed_at = utc_now()
        fresh = True
    else:
        from .analysis import snapshot_price, technical_from_snapshot, verdict_row_to_dict

        verdict_dict = verdict_row_to_dict(stored)
        computed_at = stored.get("decided_at") or ""
        snap = None
        try:
            snap = db.latest_price_snapshot(mkt, tkr)
        except Exception:
            snap = None
        price = snapshot_price(snap)
        if price is not None:
            verdict_dict["price"] = price
            technical_score, technical_reasons = technical_from_snapshot(snap)
            verdict_dict["technical"] = {
                "score": technical_score,
                "reasons": technical_reasons,
            }

    institutional_data = institutional.ticker_institutional(tkr, db)

    return {
        "instrument": item,
        "verdict": verdict_dict,
        "committee": dossier.committee_signals(verdict_dict, institutional_data),
        "factors": dossier.bull_bear_factors(verdict_dict, institutional_data),
        "institutional": institutional_data,
        "news": db.recent_news(mkt, tkr, limit=50),
        "computed_at": computed_at,
        "fresh": fresh,
    }


@app.get("/api/scanner")
def scanner(
    verdict: str = "",
    market: str = "",
    signal_lstm: str = "",
    min_confidence: float = 0.0,
    min_momentum: float = -1.0,
    min_technical: float = -1.0,
    min_news: float = -1.0,
    sort: str = "combined",
    limit: int = 100,
) -> list[dict[str, object]]:
    """Scan the analyzed universe (stored verdicts + price snapshots) by signal.

    The universe is whatever has actually been scored by the pipeline — configured
    markets plus any dynamically searched/analyzed symbols — so it grows over time
    instead of being a hardcoded list.
    """
    from .analysis import stock_analysis

    db = _db()
    rows, snaps, markets = _analysis_context(db, market or None)

    out: list[dict[str, object]] = []
    for r in rows:
        analysis = stock_analysis(
            r, snaps.get(f"{r['market']}:{r['ticker'].upper()}"), markets
        )
        if verdict and analysis["verdict"] != verdict.upper():
            continue
        if signal_lstm and analysis["lstm"]["signal"] != signal_lstm.upper():
            continue
        if min_confidence > 0 and (analysis["confidence"] or 0.0) < min_confidence:
            continue
        if min_technical > -1 and analysis["technical"]["score"] < min_technical:
            continue
        if min_news > -1 and (analysis["news"] or {}).get("score", -1.0) < min_news:
            continue
        if min_momentum > -1 and analysis["momentum_20"] < min_momentum:
            continue
        out.append(analysis)

    sort_keys = {
        "combined": lambda x: (x["combined_score"] is not None, x["combined_score"] or 0.0),
        "confidence": lambda x: (x["confidence"] is not None, x["confidence"] or 0.0),
        "momentum": lambda x: x["momentum_20"],
        "prop_up": lambda x: x["lstm"]["probability_up"] or 0.0,
    }
    key_fn = sort_keys.get(sort, sort_keys["combined"])
    out.sort(key=key_fn, reverse=True)
    return out[:limit]


@app.post("/api/refresh")
def refresh_data() -> dict[str, object]:
    """Run the background refresh cycle (fast price refresh + slow LSTM/news)."""
    from . import refresh

    db = _db()
    db.init_schema()
    return refresh.run_refresh(db)


@app.get("/api/refresh/status")
def refresh_status() -> dict[str, object]:
    """Return the current background refresh status/timings."""
    from . import refresh

    return refresh.refresh_status()


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
            "yahoo_suffix": m.yahoo_suffix,
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
            verdicts = {
                k: v for k, v in verdicts.items() if v.ticker.upper() == ticker.upper()
            }
        return {k: v.as_dict() for k, v in verdicts.items()}

    from .analysis import stock_analysis

    rows, snaps, markets = _analysis_context(db, market)
    if ticker:
        rows = [r for r in rows if r["ticker"].upper() == ticker.upper()]
    return {
        f"{r['market']}:{r['ticker']}": stock_analysis(
            r, snaps.get(f"{r['market']}:{r['ticker'].upper()}"), markets
        )
        for r in rows
    }


@app.get("/api/news")
def get_news(market: str, ticker: str, limit: int = 50) -> list[dict[str, object]]:
    db = _db()
    return db.recent_news(market, ticker, limit=limit)


@app.get("/api/watchlist")
def get_watchlist() -> list[dict[str, object]]:
    from .analysis import stock_analysis

    db = _db()
    db.init_schema()
    rows, snaps, markets = _analysis_context(db)
    latest = {(r["market"], r["ticker"].upper()): r for r in rows}
    out: list[dict[str, object]] = []
    for w in db.watchlist():
        item: dict[str, object] = {
            "market": w["market"],
            "ticker": w["ticker"],
            "company": w["company"],
            "added_at": w["added_at"],
        }
        row = latest.get((w["market"], w["ticker"].upper()))
        if row:
            analysis = stock_analysis(
                row, snaps.get(f"{row['market']}:{row['ticker'].upper()}"), markets
            )
            item["verdict"] = analysis["verdict"]
            item["confidence"] = analysis["confidence"]
            item["news_score"] = analysis["news_score"]
            item["price_score"] = analysis["price_score"]
            item["combined_score"] = analysis["combined_score"]
            item["reason"] = analysis["reason"]
            item["decided_at"] = analysis["decided_at"]
        out.append(item)
    return out


@app.post("/api/watchlist")
def add_watchlist(item: WatchItem) -> dict[str, object]:
    if not item.market or not item.ticker:
        raise HTTPException(status_code=422, detail="market and ticker are required")
    db = _db()
    db.init_schema()
    added = db.add_to_watchlist(item.market, item.ticker, item.company)
    response: dict[str, object] = {
        "added": added,
        "market": item.market.upper(),
        "ticker": item.ticker.upper(),
    }
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
        analysis = run_agent_analysis(
            market_code=market,
            ticker=ticker,
            company=company,
            provider=provider,
            model=model,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return analysis.as_dict()


@app.get("/api/reddit")
def reddit_recommendations(
    subreddits: str = "",
    limit: int = 50,
    time_filter: str = "day",
    min_mentions: int = 2,
    min_score: int = 10,
) -> dict[str, object]:
    """Reddit stock recommendations from subreddit scanning."""
    sub_list = (
        [s.strip() for s in subreddits.split(",") if s.strip()] if subreddits else None
    )
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


@app.get("/api/lstm/batch-predict")
def lstm_batch_predict(
    symbols: str,
    period: str = "2y",
    window: int = 30,
) -> dict[str, object]:
    """Get LSTM price predictions for multiple symbols."""
    from .models.price_lstm import batch_predict_lstm

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    results = batch_predict_lstm(symbol_list, period=period, window=window)
    return {k: v.as_dict() for k, v in results.items()}


@app.get("/api/lstm/train")
def lstm_train(
    symbol: str,
    period: str = "2y",
    window: int = 30,
    epochs: int = 25,
    batch_size: int = 32,
    lr: float = 1e-3,
) -> dict[str, object]:
    """Train LSTM price prediction model for a symbol."""
    from .models.price_lstm import train_price_lstm

    res = train_price_lstm(
        symbol,
        period=period,
        window=window,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
    )
    if res is None:
        raise HTTPException(status_code=404, detail=f"Could not train for {symbol}")
    return res.as_dict()


@app.get("/api/funds")
def hedge_funds() -> list[dict[str, object]]:
    """Latest 13F summary for each tracked hedge fund."""
    from .institutional import fund_summaries

    return fund_summaries(_db())


@app.get("/api/funds/refresh")
def refresh_hedge_funds() -> dict[str, object]:
    """Fetch the latest 13F filings from SEC EDGAR for all tracked funds."""
    from .institutional import fund_summaries, run_institutional_fetch

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
def chart_data(
    market: str, ticker: str, range: str = "1mo", symbol: str = ""
) -> dict[str, object]:
    """Stock chart OHLC data for any ticker at a given range (1d/1w/1mo/1y/all).

    ``symbol`` optionally overrides the provider symbol (exact resolution from
    dynamic symbol discovery); otherwise it is composed from market + suffix.
    """
    from .indexes import index_history as fetch_history
    from .markets import load_markets

    if symbol:
        sym = symbol.upper()
    else:
        markets = load_markets(settings.markets_dir)
        m = markets.get(market.upper())
        suffix = m.yahoo_suffix if m else ""
        sym = f"{ticker.upper()}{suffix}"
    rows = fetch_history(sym, range)
    return {
        "market": market.upper(),
        "ticker": ticker.upper(),
        "symbol": sym,
        "range": range,
        "data": rows,
    }


@app.get("/", include_in_schema=False)
def index():
    assets = _ui_assets_dir()
    f = assets / "index.html"
    if f.is_file():
        return FileResponse(f)
    return HTMLResponse(
        "<html><body style='background:#000;color:#f5a623;font-family:monospace'>"
        "STOCKVERDICT — frontend not built. Run <code>npm run build</code> in "
        "<code>frontend/</code>, then restart.</body></html>",
        status_code=503,
    )


# SPA fallback: serve the built React app from frontend/dist; any unmatched path
# falls back to index.html so client-side routes work.
if (FRONTEND_DIR / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
