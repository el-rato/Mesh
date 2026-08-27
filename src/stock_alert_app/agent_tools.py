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
import time
from typing import Any, Callable

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: Shared browser user-agent for direct HTTP search calls (Yahoo search API).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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


# ---------------------------------------------------------------------------
# Fincept-style broad search (worldwide assets + live news)
# ---------------------------------------------------------------------------
# The local `search_stocks` tool only matches the configured universe, which is
# far too rigid for open-ended agent questions. This tool mirrors Fincept
# Terminal's dynamic asset/news search: Yahoo's worldwide finance search API
# resolves ANY ticker/company/asset across global exchanges and returns related
# live news in one call; Google News RSS is the fallback when Yahoo is blocked.

_YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_CACHE_TTL = 300  # 5 minutes


def _yahoo_search(query: str, quotes_count: int, news_count: int) -> dict | None:
    import httpx

    try:
        with httpx.Client(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(
                _YAHOO_SEARCH_URL,
                params={
                    "q": query,
                    "quotesCount": quotes_count,
                    "newsCount": news_count,
                    "lang": "en-US",
                    "region": "US",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/parse safety
        logger.warning("Yahoo search failed for %r: %s", query, exc)
        return None
    quotes = [
        {
            "symbol": q.get("symbol", ""),
            "name": (q.get("shortname") or q.get("longname") or "").strip(),
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": q.get("quoteType", ""),
        }
        for q in (data.get("quotes") or [])
        if q.get("symbol")
    ]
    news = [
        {
            "title": (n.get("title") or "").strip(),
            "publisher": n.get("publisher", ""),
            "url": n.get("link", ""),
            "published_at": "",
        }
        for n in (data.get("news") or [])
        if n.get("title")
    ]
    return {"quotes": quotes, "news": news}


def _google_news_search(query: str, limit: int) -> list[dict]:
    from .sources import fetch_google_news

    arts = fetch_google_news(query, "US")
    return [
        {
            "title": a.title,
            "publisher": a.source or "Google News",
            "url": a.url,
            "published_at": a.published_at,
        }
        for a in arts[:limit]
    ]


def tool_web_search(args: dict) -> dict:
    """Fincept-style worldwide asset + news search for ANY query.

    Combines Yahoo Finance's global search API (every exchange/asset class)
    with a Google News fallback, so the agent can resolve companies, tickers,
    ETFs, FX, crypto and themes well beyond the terminal's own universe.
    """
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 8)), 20))
    if not query:
        return {"error": "query is required"}

    now = time.time()
    key = f"{query.lower()}|{limit}"
    cached = _SEARCH_CACHE.get(key)
    if cached and now < cached[0]:
        return dict(cached[1], cached=True)

    result: dict = {"query": query, "engine": "fincept_web"}
    y = _yahoo_search(query, quotes_count=limit, news_count=limit)
    if y is not None:
        result["assets"] = y["quotes"]
        result["news"] = y["news"]
    else:
        result["assets"] = []
        result["news"] = []
    if not result["news"]:
        result["news"] = _google_news_search(query, limit)
        if result["assets"]:
            result["engine"] = "yahoo+google_news"
        else:
            result["engine"] = "google_news"
    elif result["assets"]:
        result["engine"] = "yahoo"

    result["asset_count"] = len(result["assets"])
    result["news_count"] = len(result["news"])
    _SEARCH_CACHE[key] = (now + _SEARCH_CACHE_TTL, result)
    return result


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


def tool_list_themes(args: dict) -> dict:
    """List the thematic/sector baskets the agent can analyze as a rotation."""
    from .strategy import list_themes

    return {"themes": list_themes()}


def tool_research_ticker(args: dict) -> dict:
    """Deep-dive RESEARCH brief for one ticker (Researcher layer).

    Gathers the stored committee verdict, recent news as evidence, and 13F
    institutional activity, then returns a ResearchBrief-style structured pack:
    catalysts, risks, bull/bear evidence, provenance, plus the committee score.
    Use this when the user wants the *why* behind a name inside a strategy.
    """
    market = str(args.get("market", "")).upper()
    ticker = str(args.get("ticker", "")).upper()
    company = str(args.get("company", ""))
    if not ticker:
        return {"error": "ticker is required"}
    db = _db()
    if not market:
        secs = [s for s in db.all_securities() if s["ticker"].upper() == ticker]
        market = secs[0]["market"] if secs else ""
    if not market:
        return {"error": f"Could not resolve a market for ticker {ticker}. Provide 'market'."}

    rows = db.latest_verdicts(market=market)
    vrow = next((r for r in rows if r["ticker"].upper() == ticker), None)
    news = db.recent_news(market, ticker, limit=10)
    evidence = []
    for n in news:
        score = 0.0
        try:
            score = float(n.get("sentiment_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        evidence.append((n.get("title", ""), n.get("source", ""), n.get("sentiment_label", "") or "", score))
    inst = None
    try:
        from .institutional import ticker_institutional

        inst = ticker_institutional(ticker, db)
    except Exception:
        inst = None

    from . import research

    brief = research.build_brief(
        ticker=ticker,
        company=company or (vrow or {}).get("company", ""),
        market=market,
        news_score=(vrow or {}).get("news_score"),
        news_label=(vrow or {}).get("reason", ""),
        article_count=len(news),
        evidence=evidence,
        institutional=inst,
    )
    from .strategy import _committee_score

    comp = {
        "news_score": (vrow or {}).get("news_score"),
        "combined_score": (vrow or {}).get("combined_score"),
        "lstm_probability_up": (vrow or {}).get("lstm_probability_up"),
        "technical_score": (vrow or {}).get("technical_score"),
    }
    cs, cv = _committee_score(comp)
    return {
        "ticker": ticker,
        "market": market,
        "verdict": (vrow or {}).get("verdict"),
        "committee_score": cs,
        "committee_verdict": cv,
        "brief": brief.as_dict(),
        "institutional": inst,
    }


def tool_market_regime(args: dict) -> dict:
    """Current market REGIME derived from stored index snapshots (S&P, VIX).

    Returns the regime label (risk-on / risk-off / choppy, with a volatility tag)
    so the agent can frame a strategy within the prevailing macro tape.
    """
    from .strategy import _market_regime

    return _market_regime(_db())


def tool_analyze_rotation(args: dict) -> dict:
    """Analyze a sector/theme rotation: basket, breadth, leadership, setup, watch.

    Given a theme (e.g. 'Healthcare & Biotech', 'semiconductors', 'AI') or an
    explicit list of tickers, returns a structured rotation snapshot the agent
    uses to formulate a trading strategy (setup + what to watch).
    """
    theme = str(args.get("theme", "")).strip()
    market = str(args.get("market", "")).upper() or None
    tickers = args.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]
    analyze = bool(args.get("analyze", False))
    from .strategy import analyze_rotation

    try:
        result = analyze_rotation(theme=theme, market=market, tickers=list(tickers), analyze=analyze)
    except Exception as exc:
        logger.warning("Rotation analysis failed: %s", exc)
        return {"error": str(exc)}
    return result


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
    "web_search": (
        tool_web_search,
        "FINCEPT-STYLE worldwide search across ALL global exchanges and live news "
        "for ANY company, ticker, ETF, FX, crypto or theme — NOT limited to the "
        "local universe. Returns matching assets (symbol, name, exchange, type) "
        "and related news headlines with links. Use this FIRST whenever the user "
        "asks about a security, company or market topic that CONTEXT and the "
        "other tools cannot resolve, or when they want the broadest latest news.",
        {
            "query": "Free-form search text, e.g. 'rare earth miners', 'Tesla', 'ASML'. REQUIRED.",
            "limit": "Max assets + max news items (default 8, cap 20).",
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
    "list_themes": (
        tool_list_themes,
        "List the thematic/sector baskets available for rotation analysis "
        "(e.g. 'Healthcare & Biotech', 'semiconductors', 'AI', 'energy').",
        {},
    ),
    "research_ticker": (
        tool_research_ticker,
        "Deep-dive RESEARCH brief for a single ticker: committee verdict, news "
        "evidence, catalysts/risks, and 13F institutional activity (Researcher "
        "layer). Use it to explain the *why* behind a name when building or "
        "defending a strategy.",
        {
            "market": "Market code. Optional if ticker is unique in the universe.",
            "ticker": "Ticker symbol. REQUIRED.",
            "company": "Optional company name to aid resolution.",
        },
    ),
    "market_regime": (
        tool_market_regime,
        "Current market REGIME (risk-on / risk-off / choppy, with volatility tag) "
        "derived from stored index snapshots. Use to frame a strategy within the "
        "prevailing macro tape.",
        {},
    ),
    "analyze_rotation": (
        tool_analyze_rotation,
        "Analyze a sector/theme ROTATION and formulate the building blocks of a "
        "trading strategy. Given a theme name (or explicit tickers), returns the "
        "basket, breadth (bull/bear/neutral), leadership/laggards, average "
        "momentum & news sentiment, and rules-based 'setup' + 'what to watch' "
        "bullets. USE THIS when the user asks to analyze a rotation, sector, "
        "industry, or to formulate a trading strategy/setup/thesis.",
        {
            "theme": "Theme/sector name, e.g. 'Healthcare & Biotech', 'semiconductors'. REQUIRED if no tickers.",
            "market": "Optional market filter (e.g. NYSE).",
            "tickers": "Optional explicit list of tickers (or 'MARKET:TICKER') to build a custom basket.",
            "analyze": "true to force fresh live verdicts when stored coverage is thin (default false).",
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
