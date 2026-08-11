from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .db import Database

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


def _db() -> Database:
    return Database(settings.db_path)


def _latest_verdict_map(db: Database) -> dict[tuple[str, str], dict[str, object]]:
    try:
        return {(r["market"], r["ticker"]): r for r in db.latest_verdicts()}
    except Exception:
        return {}


def _enrich_search_result(
    item: dict[str, object], latest: dict[tuple[str, str], dict[str, object]]
) -> dict[str, object]:
    market = item.get("market")
    ticker = item.get("ticker")
    v = latest.get((market, ticker)) if market and ticker else None
    return {
        **item,
        "verdict": v["verdict"] if v else None,
        "confidence": v["confidence"] if v else None,
        "combined_score": v["combined_score"] if v else None,
        "reason": v["reason"] if v else None,
    }


@app.get("/api/search")
def search_tickers(
    q: str = "",
    market: str | None = None,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Search the whole supported universe (configured + dynamically discovered).

    Configured 'featured' tickers rank first (they carry stored verdicts); the
    provider search expands results to any symbol/company the market data source
    knows. Enriched with the latest verdict when one is already stored.
    """
    query = (q or "").strip()
    if not query:
        return []

    from . import instruments

    db = _db()
    latest = _latest_verdict_map(db)
    results = instruments.search_universe(query, limit=limit, market_filter=market)
    return [_enrich_search_result(item, latest) for item in results]


def _dossier_target(
    symbol: str, market: str, ticker: str
) -> tuple[dict[str, object], str]:
    """Resolve a dossier request into (instrument, full_symbol) or raise 422/404."""
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

        m = load_markets(settings.markets_dir).get(market.upper())
        if m is None:
            raise HTTPException(status_code=422, detail=f"Unknown market {market!r}")
        try:
            tkr = m.get_ticker(ticker)
            company = tkr.name or ""
            full = f"{ticker.upper()}{tkr.yahoo_suffix or m.yahoo_suffix}"
        except KeyError:
            company = ""
            full = f"{ticker.upper()}{m.yahoo_suffix}"
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


def _snapshot_to_price_dict(snap: dict[str, object] | None) -> dict[str, object] | None:
    """Map a stored price_snapshots row to PriceState.as_dict() shape."""
    if not snap:
        return None
    sma = float(snap.get("sma_50") or 0.0)
    close = float(snap.get("close") or 0.0)
    return {
        "symbol": snap.get("ticker") or "",
        "close": close,
        "open": float(snap.get("open") or 0.0),
        "high": float(snap.get("high") or 0.0),
        "low": float(snap.get("low") or 0.0),
        "volume": int(snap.get("volume") or 0),
        "momentum_20": float(snap.get("momentum_20") or 0.0),
        "rsi_14": float(snap.get("rsi_14") or 50.0),
        "sma_50": sma,
        "sma_200": 0.0,
        "trend_50_200": 0.0,
        "above_sma_50": close >= sma if sma else None,
    }


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
            v = live_verdict(
                mkt, tkr, item.get("company") or "", yahoo_symbol=full
            )
        except Exception as exc:
            logger.exception("Dossier live verdict failed for %s", full)
            raise HTTPException(
                status_code=503, detail=f"Analysis failed for {full}: {exc}"
            )
        if v is None:
            raise HTTPException(
                status_code=404,
                detail=f"No price or news data could be gathered for {full}",
            )
        # A resolvable-but-unknown symbol (e.g. a delisted ticker) yields a
        # no-data verdict: treat it as not found rather than a bogus 200.
        if v.price is None and not v.news_available:
            raise HTTPException(
                status_code=404,
                detail=f"No price or news data could be gathered for {full}",
            )
        verdict_dict = v.as_dict()
        computed_at = ""
        fresh = True
    else:
        verdict_dict = _verdict_row_to_dict(stored)
        computed_at = stored.get("decided_at") or ""
        snap = None
        try:
            snap = db.latest_price_snapshot(mkt, tkr)
        except Exception:
            snap = None
        price = _snapshot_to_price_dict(snap)
        if price is not None:
            verdict_dict["price"] = price

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


def _lstm_signal_from_row(r: dict[str, object]) -> str:
    """BULL/BEAR from the stored LSTM score, or N/A when the model was unavailable."""
    score = float(r["lstm_score"] or 0.0)
    prob = r["lstm_probability_up"]
    p_up = float(prob) if prob is not None else None
    if score == 0.0 and p_up is None:
        return "N/A"
    return "BULL" if (score > 0 or (p_up is not None and p_up >= 0.5)) else "BEAR"


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
    from . import dossier
    from .markets import load_markets

    db = _db()
    markets = load_markets(settings.markets_dir)
    rows = db.latest_verdicts(market=market or None)
    snaps = {f"{s['market']}:{s['ticker'].upper()}": s for s in db.latest_price_snapshots(market=market or None)}

    out: list[dict[str, object]] = []
    for r in rows:
        lstm_score = float(r["lstm_score"] or 0.0)
        prob_up = r["lstm_probability_up"]
        p_up = float(prob_up) if prob_up is not None else None
        lstm_signal = _lstm_signal_from_row(r)
        if signal_lstm and lstm_signal != signal_lstm.upper():
            continue
        if min_technical > -1 and float(r["technical_score"] or 0.0) < min_technical:
            continue
        if min_news > -1 and float(r["news_score"] or 0.0) < min_news:
            continue
        snap = snaps.get(f"{r['market']}:{r['ticker'].upper()}")
        mom = float((snap or {}).get("momentum_20") or 0.0)
        if min_momentum > -1 and mom < min_momentum:
            continue

        committee_input = _verdict_row_to_dict(r)
        price = _snapshot_to_price_dict(snap)
        if price is not None:
            committee_input["price"] = price
        committee = dossier.committee_signals(committee_input, None)
        if verdict and committee["verdict"] != verdict.upper():
            continue
        if min_confidence > 0 and (committee["confidence"] or 0.0) < min_confidence:
            continue

        m = markets.get(r["market"])
        company = ""
        suffix = (m.yahoo_suffix if m else "") or ""
        if m:
            try:
                company = m.get_ticker(r["ticker"]).name or ""
            except KeyError:
                company = ""

        out.append(
            {
                "market": r["market"],
                "ticker": r["ticker"],
                "symbol": r["ticker"] + suffix,
                "company": company,
                "verdict": committee["verdict"],
                "confidence": committee["confidence"],
                "combined_score": committee["score"],
                "committee": committee,
                "lstm": {
                    "score": lstm_score,
                    "probability_up": p_up,
                    "signal": lstm_signal,
                },
                "technical": {"score": r["technical_score"]},
                "news": {"score": r["news_score"]},
                "momentum_20": mom,
                "rsi_14": float((snap or {}).get("rsi_14") or 50.0),
                "close": float((snap or {}).get("close") or 0.0),
            }
        )

    sort_keys = {
        "combined": lambda x: (x["combined_score"] is not None, x["combined_score"] or 0.0),
        "confidence": lambda x: (x["confidence"] is not None, x["confidence"] or 0.0),
        "momentum": lambda x: x["momentum_20"],
        "prop_up": lambda x: x["lstm"]["probability_up"] or 0.0,
    }
    key_fn = sort_keys.get(sort, sort_keys["combined"])
    out.sort(key=key_fn, reverse=True)
    return out[:limit]


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

    rows = db.latest_verdicts(market=market)
    if ticker:
        rows = [r for r in rows if r["ticker"].upper() == ticker.upper()]
    return {
        f"{r['market']}:{r['ticker']}": _verdict_row_to_dict(r) for r in rows
    }


def _verdict_row_to_dict(r: dict[str, object]) -> dict[str, object]:
    """Map a stored verdict row to the API shape returned to the UI (mirrors Verdict.as_dict)."""
    reason = str(r.get("reason") or "")
    news_available = "News:" in reason and "News: unavailable" not in reason
    return {
        "market": r["market"],
        "ticker": r["ticker"],
        "verdict": r["verdict"],
        "confidence": r["confidence"],
        "news_score": r["news_score"],
        "price_score": r["price_score"],
        "combined_score": r["combined_score"],
        "reason": [reason] if reason else [],
        "decided_at": r["decided_at"],
        "forecast_horizon": "1 trading day",
        "signal_agreement": _signal_agreement_from_row(r),
        "lstm": {
            "score": r["lstm_score"],
            "probability_up": r["lstm_probability_up"],
            "predicted_return": r["lstm_predicted_return"],
            "model_confidence": r["lstm_confidence"],
            "metrics": {},
            "model_version": "",
        },
        "technical": {
            "score": r["technical_score"],
        },
        "news_available": news_available,
    }


def _signal_agreement_from_row(row: dict[str, object]) -> str:
    try:
        reason = str(row.get("reason") or "")
    except Exception:
        return "unknown"
    for token in ("Signal agreement: strong", "moderate", "mixed", "weak", "none"):
        if token in reason:
            return token.replace("Signal agreement: ", "")
    return "unknown"


@app.get("/api/news")
def get_news(market: str, ticker: str, limit: int = 50) -> list[dict[str, object]]:
    db = _db()
    return db.recent_news(market, ticker, limit=limit)


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
