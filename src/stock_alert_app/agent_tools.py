"""Terminal tool-calling layer for the AI agent.

The chat agent only ever sees a static context blob. To let it answer when it
lacks information, it can call the same internal capabilities the rest of the
terminal uses -- NEWS RSS ingest/refresh, per-ticker news, universe search,
committee verdicts, fresh analysis, scanner, indexes, hedge-fund 13F data,
price history and Reddit sentiment.

Tools are intentionally provider-agnostic: every LLM backend (Gemini, Ollama,
OpenCode) and the offline local responder invoke the same ``run_tool`` entry
point. Results are compact, JSON-serialisable dicts so they can be fed straight
back into the model's context.

Nothing here fabricates data -- every tool reads from the live terminal state
(database, RSS feeds, yfinance, SEC EDGAR) and returns exactly what it finds.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

#: Cap on any single tool's serialised output so the model context stays bounded.
_MAX_TOOL_CHARS = 8_000


def _db(db_path: str | None = None) -> Database:
    db = Database(db_path or settings.db_path)
    db.init_schema()
    return db


def _compact(obj: Any) -> str:
    """JSON-encode a tool result, truncating to stay within the budget."""
    text = json.dumps(obj, default=str)
    if len(text) <= _MAX_TOOL_CHARS:
        return text
    return text[: _MAX_TOOL_CHARS - 1].rstrip() + "…"


def _news_for(market: str, ticker: str, limit: int = 8, db_path: str | None = None) -> list[dict]:
    db = _db(db_path)
    rows = db.recent_news(market, ticker, limit=limit)
    out = []
    for r in rows:
        out.append(
            {
                "title": r.get("title", ""),
                "source": r.get("source", ""),
                "sentiment": r.get("sentiment_label", "") or "",
                "url": r.get("url", ""),
                "published_at": r.get("published_at", ""),
            }
        )
    return out


def tool_get_news(args: dict) -> dict:
    """Recent stored news for a specific ticker (no network call)."""
    market = str(args.get("market", "")).upper()
    ticker = str(args.get("ticker", "")).upper()
    limit = int(args.get("limit", 8))
    if not ticker:
        return {"error": "ticker is required"}
    if not market:
        # Try to resolve the market from the known universe.
        secs = [s for s in _db().all_securities() if s["ticker"].upper() == ticker]
        market = secs[0]["market"] if secs else ""
    if not market:
        return {"error": f"Could not resolve a market for ticker {ticker}. Provide 'market'."}
    items = _news_for(market, ticker, limit)
    return {"market": market, "ticker": ticker, "count": len(items), "news": items}


def tool_refresh_news(args: dict) -> dict:
    """Fetch + store FRESH news for a ticker from the live RSS feeds, then return it.

    This is the on-demand NEWS RSS tool: when the agent has no coverage for a
    ticker (or the user wants the latest), it calls this and gets real, just
    ingested headlines.
    """
    market = str(args.get("market", "")).upper()
    ticker = str(args.get("ticker", "")).upper()
    limit = int(args.get("limit", 10))
    if not ticker:
        return {"error": "ticker is required"}
    from .markets import load_markets

    if not market:
        secs = [s for s in _db().all_securities() if s["ticker"].upper() == ticker]
        market = secs[0]["market"] if secs else ""
    if not market:
        return {"error": f"Could not resolve a market for ticker {ticker}. Provide 'market'."}
    mkts = load_markets(settings.markets_dir)
    if market not in mkts:
        return {"error": f"Unknown market {market}"}
    try:
        from .ingest import run_ticker_ingest

        res = run_ticker_ingest(market, ticker)
        fetched = res.fetched if res else 0
    except Exception as exc:  # network/parse safety -- never abort the chat
        logger.warning("Live news refresh failed for %s:%s: %s", market, ticker, exc)
        fetched = 0
    items = _news_for(market, ticker, limit)
    return {
        "market": market,
        "ticker": ticker,
        "refreshed": fetched,
        "count": len(items),
        "news": items,
    }


def tool_global_news(args: dict) -> dict:
    """Latest global headlines across the whole terminal (world/tech/crypto/macro)."""
    limit = int(args.get("limit", 12))
    db = _db()
    rows = db.recent_news_feed(limit=limit)
    out = [
        {
            "title": r.get("title", ""),
            "source": r.get("source", ""),
            "sentiment": r.get("sentiment_label", "") or "",
            "url": r.get("url", ""),
        }
        for r in rows
    ]
    return {"count": len(out), "news": out}


def tool_search(args: dict) -> dict:
    """Search the supported universe (configured + dynamically discovered)."""
    query = str(args.get("query", "")).strip()
    market = str(args.get("market", "")).upper() or None
    limit = int(args.get("limit", 8))
    if not query:
        return {"error": "query is required"}
    from . import instruments

    results = instruments.search_universe(query, limit=limit, market_filter=market)
    return {"query": query, "count": len(results), "results": results}


def tool_get_verdict(args: dict) -> dict:
    """Stored committee verdict for a single ticker (no live compute)."""
    market = str(args.get("market", "")).upper()
    ticker = str(args.get("ticker", "")).upper()
    if not ticker:
        return {"error": "ticker is required"}
    db = _db()
    if not market:
        secs = [s for s in db.all_securities() if s["ticker"].upper() == ticker]
        market = secs[0]["market"] if secs else ""
    if not market:
        return {"error": f"Could not resolve a market for ticker {ticker}. Provide 'market'."}
    rows = db.latest_verdicts(market=market)
    hit = next((r for r in rows if r["ticker"].upper() == ticker), None)
    if hit is None:
        return {"market": market, "ticker": ticker, "verdict": None, "note": "no stored verdict"}
    return {
        "market": hit.get("market"),
        "ticker": hit.get("ticker"),
        "verdict": hit.get("verdict"),
        "confidence": hit.get("confidence"),
        "news_score": hit.get("news_score"),
        "price_score": hit.get("price_score"),
        "combined_score": hit.get("combined_score"),
        "reason": hit.get("reason"),
    }


def tool_analyze(args: dict) -> dict:
    """Run a FRESH live analysis (verdict + factors + news) for a ticker."""
    market = str(args.get("market", "")).upper()
    ticker = str(args.get("ticker", "")).upper()
    company = str(args.get("company", ""))
    if not market or not ticker:
        return {"error": "market and ticker are required"}
    from .verdict import live_verdict

    try:
        v = live_verdict(market, ticker, company)
    except Exception as exc:
        logger.warning("Live analysis failed for %s:%s: %s", market, ticker, exc)
        return {"error": f"Analysis failed: {exc}"}
    if v is None:
        return {"market": market, "ticker": ticker, "status": "no_data"}
    db = _db()
    news = [
        {
            "title": n.get("title", ""),
            "source": n.get("source", ""),
            "sentiment": n.get("sentiment_label", "") or "",
        }
        for n in db.recent_news(market, ticker, limit=10)
    ]
    out = v.as_dict()
    out["news"] = news
    return out


def tool_scanner(args: dict) -> dict:
    """Top committee calls across the universe, optionally filtered."""
    market = str(args.get("market", "")).upper() or None
    verdict = str(args.get("verdict", "")).upper() or None
    limit = int(args.get("limit", 12))
    db = _db()
    rows = db.latest_verdicts(market=market)
    if verdict:
        rows = [r for r in rows if str(r.get("verdict", "")).upper() == verdict]
    rows = sorted(rows, key=lambda r: float(r.get("combined_score", 0) or 0), reverse=True)
    out = [
        {
            "market": r.get("market"),
            "ticker": r.get("ticker"),
            "verdict": r.get("verdict"),
            "confidence": r.get("confidence"),
            "combined_score": r.get("combined_score"),
        }
        for r in rows[:limit]
    ]
    return {"count": len(out), "results": out}


def tool_indexes(args: dict) -> dict:
    """Latest index snapshots (optionally filtered by market)."""
    market = str(args.get("market", "")).upper() or None
    db = _db()
    rows = db.latest_index_snapshots(market=market)
    out = [
        {
            "market": s["market"],
            "symbol": s["symbol"],
            "name": s["name"],
            "close": s["close"],
            "change_pct": s["change_pct"],
        }
        for s in rows
    ]
    return {"count": len(out), "indexes": out}


def tool_funds(args: dict) -> dict:
    """Hedge-fund 13F summaries (SEC EDGAR) tracked by the terminal."""
    from . import institutional

    summaries = institutional.fund_summaries(_db())
    return {"count": len(summaries), "funds": summaries}


def tool_price_history(args: dict) -> dict:
    """OHLC price history for a ticker at a range (1d/1w/1mo/1y/all)."""
    market = str(args.get("market", ""))
    ticker = str(args.get("ticker", "")).upper()
    rng = str(args.get("range", "1mo"))
    symbol = str(args.get("symbol", "")).upper()
    if not ticker and not symbol:
        return {"error": "ticker or symbol is required"}
    from .indexes import index_history
    from .markets import load_markets

    if not symbol and market:
        mkts = load_markets(settings.markets_dir)
        m = mkts.get(market.upper())
        suffix = m.yahoo_suffix if m else ""
        symbol = f"{ticker}{suffix}"
    if not symbol:
        symbol = ticker
    rows = index_history(symbol, rng)
    out = [
        {"date": r.get("date", ""), "close": r.get("close"), "volume": r.get("volume")}
        for r in rows[-60:]
    ]
    return {"symbol": symbol, "range": rng, "points": len(out), "history": out}


def tool_reddit(args: dict) -> dict:
    """Reddit stock sentiment from subreddit scanning."""
    subreddits = str(args.get("subreddits", ""))
    limit = int(args.get("limit", 30))
    time_filter = str(args.get("time_filter", "day"))
    from .reddit_scanner import run_reddit_scan

    try:
        recs = run_reddit_scan(
            subreddits=[s.strip() for s in subreddits.split(",") if s.strip()] or None,
            limit_per_sub=limit,
            time_filter=time_filter,
            min_mentions=2,
            min_score=10,
        )
    except RuntimeError as exc:
        return {"error": str(exc)}
    return {"count": len(recs), "recommendations": [r.as_dict() for r in recs]}


#: Each tool: name -> (handler, human description, parameter schema).
TOOLS: dict[str, tuple[Callable[[dict], dict], str, dict]] = {
    "get_news": (
        tool_get_news,
        "Get recent stored news headlines for a specific ticker (market + ticker).",
        {
            "market": "Market code, e.g. NYSE/NASDAQ. Optional if ticker is unique in the universe.",
            "ticker": "Ticker symbol, e.g. AAPL. REQUIRED.",
            "limit": "Max headlines to return (default 8).",
        },
    ),
    "refresh_news": (
        tool_refresh_news,
        "Fetch FRESH news for a ticker from the live RSS feeds (Google News / Yahoo), "
        "store it, and return the latest headlines. Use when you have no coverage or "
        "need the most recent news for a ticker.",
        {
            "market": "Market code, e.g. NYSE/NASDAQ. Optional if ticker is unique.",
            "ticker": "Ticker symbol, e.g. TSLA. REQUIRED.",
            "limit": "Max headlines to return (default 10).",
        },
    ),
    "global_news": (
        tool_global_news,
        "Latest global headlines across the whole terminal (world, tech, crypto, macro).",
        {"limit": "Max headlines to return (default 12)."},
    ),
    "search_stocks": (
        tool_search,
        "Search the supported universe for a ticker or company by name/keyword.",
        {
            "query": "Search term, e.g. 'Tesla' or 'AAPL'. REQUIRED.",
            "market": "Optional market filter.",
            "limit": "Max results (default 8).",
        },
    ),
    "get_verdict": (
        tool_get_verdict,
        "Stored committee verdict (BULL/BEAR/NEUTRAL) + scores for a single ticker.",
        {
            "market": "Market code. Optional if ticker is unique.",
            "ticker": "Ticker symbol. REQUIRED.",
        },
    ),
    "analyze_stock": (
        tool_analyze,
        "Run a FRESH live analysis (verdict, factors, news) for a ticker via the "
        "terminal's full pipeline. Use for deep-dive questions about a specific stock.",
        {
            "market": "Market code. REQUIRED.",
            "ticker": "Ticker symbol. REQUIRED.",
            "company": "Optional company name to aid resolution.",
        },
    ),
    "scanner": (
        tool_scanner,
        "Top committee calls across the universe, optionally filtered by market/verdict.",
        {
            "market": "Optional market filter.",
            "verdict": "Optional filter: BULL / BEAR / NEUTRAL.",
            "limit": "Max results (default 12).",
        },
    ),
    "get_indexes": (
        tool_indexes,
        "Latest index snapshots (optional market filter).",
        {"market": "Optional market filter."},
    ),
    "get_funds": (
        tool_funds,
        "Hedge-fund 13F holdings summaries (SEC EDGAR) tracked by the terminal.",
        {},
    ),
    "get_price_history": (
        tool_price_history,
        "OHLC price history for a ticker at a range (1d/1w/1mo/1y/all).",
        {
            "market": "Market code (used to compose the symbol).",
            "ticker": "Ticker symbol.",
            "symbol": "Optional explicit provider symbol (overrides market+ticker).",
            "range": "One of 1d/1w/1mo/1y/all (default 1mo).",
        },
    ),
    "reddit_sentiment": (
        tool_reddit,
        "Reddit stock sentiment aggregated from subreddit scanning.",
        {
            "subreddits": "Comma-separated subreddit list (optional).",
            "limit": "Per-subreddit limit (default 30).",
            "time_filter": "day/week/month (default day).",
        },
    ),
}


def run_tool(name: str, args: dict | str | None = None) -> dict:
    """Execute a named tool and return a compact result dict (always JSON-safe)."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name}"}
    handler, _desc, _schema = spec
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"error": f"invalid tool arguments (not JSON): {args!r}"}
    args = args or {}
    try:
        result = handler(args)
    except Exception as exc:  # a tool must never crash the chat
        logger.exception("Tool %s failed", name)
        return {"error": f"{name} failed: {exc}"}
    return {"tool": name, "result": result}


def tools_system_text() -> str:
    """Human-readable catalogue of tools for the model system prompt."""
    lines = ["You have access to the following TERMINAL TOOLS to fetch real data:"]
    for name, (_, desc, schema) in TOOLS.items():
        params = ", ".join(f"{k}: {v}" for k, v in schema.items()) or "(no parameters)"
        lines.append(f"- {name}: {desc}\n    params: {params}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool-call protocol (provider-agnostic, text-based)
# ---------------------------------------------------------------------------

#: The model signals a tool call with this single-line marker.
_TOOL_CALL_RE = re.compile(r"TOOL_CALL:\s*(\w+)\s*(\{.*\})", re.DOTALL)


def parse_tool_call(text: str) -> tuple[str, dict] | None:
    """Return (tool_name, args) if the model emitted a TOOL_CALL line, else None."""
    m = _TOOL_CALL_RE.search(text or "")
    if not m:
        return None
    name = m.group(1)
    try:
        args = json.loads(m.group(2))
    except json.JSONDecodeError:
        args = {}
    return name, (args if isinstance(args, dict) else {})


def tool_result_block(name: str, result: dict) -> str:
    payload = result.get("result", result)
    return f"TOOL_RESULT({name}): {_compact(payload)}"
