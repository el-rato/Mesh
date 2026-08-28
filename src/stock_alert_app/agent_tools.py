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
import math
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


# ---------------------------------------------------------------------------
# Agent Workflow screening (the strategy-discovery capability, in-agent)
# ---------------------------------------------------------------------------

def parse_strategy_text(text: str) -> dict[str, Any]:
    """Best-effort extraction of structured criteria from a workflow description.

    Only extracts signals we can actually evaluate from stored terminal data.
    market_cap language is noted as UNVERIFIED (no reliable cap data exists) -- it
    is never fabricated or silently treated as a real filter.
    """
    import re as _re

    t = (text or "").lower()
    c: dict[str, Any] = {
        "name": "",
        "description": text or "",
        "market": None,
        "verdict": None,
        "momentum_min": None,
        "trend_bullish": None,
        "volume_ratio_min": None,
        "rsi_min": None,
        "rsi_max": None,
        "conviction_min": None,
        "news_min": None,
        "notes": [],
    }
    if _re.search(r"large[- ]?cap|small[- ]?cap|market cap|marketcap", t):
        c["notes"].append("market_cap criterion detected but market-cap data is unavailable; candidate selection did not use market capitalization")
    if "bullish" in t or "uptrend" in t or "positive trend" in t:
        c["trend_bullish"] = True
    if "bearish" in t or "downtrend" in t:
        c["trend_bullish"] = False
    if "strong momentum" in t or "high momentum" in t or "momentum" in t:
        c["momentum_min"] = 0.02
    if "increasing volume" in t or "above average volume" in t or "volume confirm" in t:
        c["volume_ratio_min"] = 1.2
    if "conviction" in t or "committee" in t:
        c["conviction_min"] = 0.6
    if "bullish" in t and "trend" not in t:
        c["verdict"] = "BULL"
    if "bearish" in t and "trend" not in t:
        c["verdict"] = "BEAR"
    m = _re.search(r"rsi\s*(?:above|over)\s*(\d+)", t)
    if m:
        c["rsi_min"] = float(m.group(1))
    m = _re.search(r"rsi\s*(?:below|under)\s*(\d+)", t)
    if m:
        c["rsi_max"] = float(m.group(1))
    return c


def _wf_clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _wf_num(value: Any, default: float | None = None) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _wf_required_inputs(criteria: dict[str, Any]) -> list[str]:
    req: list[str] = []
    if criteria.get("momentum_min") is not None:
        req.append("momentum")
    if criteria.get("trend_bullish") is not None:
        req.append("trend")
    if criteria.get("volume_ratio_min") is not None:
        req.append("volume")
    if criteria.get("rsi_min") is not None or criteria.get("rsi_max") is not None:
        req.append("rsi")
    if criteria.get("conviction_min") is not None:
        req.append("conviction")
    if criteria.get("news_min") is not None:
        req.append("news")
    return req


def _wf_score(a: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    matched: list[str] = []
    explanation: list[str] = []
    missing: list[str] = []
    parts: list[tuple[float, float, str]] = []
    weights_total = 0.0
    W = {"momentum": 0.25, "trend": 0.20, "rsi": 0.15, "volume": 0.10, "conviction": 0.20, "news": 0.10}

    mom = _wf_num(a.get("momentum_20"))
    if mom is None:
        missing.append("momentum")
    else:
        parts.append((_wf_clip(mom * 2.0), W["momentum"], "momentum"))
        weights_total += W["momentum"]
        matched.append("momentum")
        explanation.append(f"Momentum {mom:+.1%}")

    above = a.get("above_sma")
    if above is None:
        missing.append("trend")
    else:
        if above:
            parts.append((0.5, W["trend"], "trend"))
            explanation.append("Price above SMA50 (bullish trend)")
        else:
            parts.append((-0.5, W["trend"], "trend"))
            explanation.append("Price below SMA50 (bearish trend)")
        weights_total += W["trend"]
        matched.append("trend")

    rsi = _wf_num(a.get("rsi_14"))
    if rsi is None:
        missing.append("rsi")
    else:
        s = 0.6 if rsi >= 70 else (-0.3 if rsi <= 30 else _wf_clip((rsi - 50) / 40.0))
        parts.append((s, W["rsi"], "rsi"))
        weights_total += W["rsi"]
        matched.append("rsi")
        explanation.append(f"RSI {rsi:.0f}")

    vr = _wf_num(a.get("volume_ratio"))
    if vr is None:
        missing.append("volume")
    else:
        s = _wf_clip((vr - 1.0) * 0.8)
        explanation.append(f"Volume {vr:.1f}x average ({'confirmation' if vr >= 1.0 else 'subdued'})")
        parts.append((s, W["volume"], "volume"))
        weights_total += W["volume"]
        matched.append("volume")

    conf = _wf_num(a.get("confidence"))
    if conf is None:
        missing.append("conviction")
    else:
        parts.append((_wf_clip((conf - 0.5) * 2.0), W["conviction"], "conviction"))
        weights_total += W["conviction"]
        matched.append("conviction")
        explanation.append(f"Committee conviction {conf:.0%}")

    ns = _wf_num(a.get("news_score"))
    if ns is None:
        missing.append("news")
    else:
        parts.append((_wf_clip(ns * 2.0), W["news"], "news"))
        weights_total += W["news"]
        matched.append("news")
        explanation.append(f"News sentiment {ns:+.2f}")

    score = 0.0 if not parts or weights_total <= 0 else round((sum(s * w for s, w, _ in parts) / weights_total + 1.0) / 2.0 * 100.0, 1)
    req = _wf_required_inputs(criteria)
    missing_req = [m for m in req if m in missing]
    return {
        "score": score, "matched": matched, "explanation": explanation,
        "missing": missing, "evaluable": not missing_req, "missing_required": missing_req,
    }


def _wf_apply_gates(a: dict[str, Any], criteria: dict[str, Any]) -> bool:
    mom = _wf_num(a.get("momentum_20"))
    if criteria.get("momentum_min") is not None and (mom is None or mom < float(criteria["momentum_min"])):
        return False
    if criteria.get("trend_bullish") is not None:
        above = a.get("above_sma")
        if above is None or bool(above) != bool(criteria["trend_bullish"]):
            return False
    vr = _wf_num(a.get("volume_ratio"))
    if criteria.get("volume_ratio_min") is not None and (vr is None or vr < float(criteria["volume_ratio_min"])):
        return False
    rsi = _wf_num(a.get("rsi_14"))
    if criteria.get("rsi_min") is not None and (rsi is None or rsi < float(criteria["rsi_min"])):
        return False
    if criteria.get("rsi_max") is not None and (rsi is None or rsi > float(criteria["rsi_max"])):
        return False
    conf = _wf_num(a.get("confidence"))
    if criteria.get("conviction_min") is not None and (conf is None or conf < float(criteria["conviction_min"])):
        return False
    return True


def _wf_result(a: dict[str, Any], sc: dict[str, Any], status: str, market_cap_unverified: bool) -> dict[str, Any]:
    explanation = list(sc["explanation"])
    if market_cap_unverified:
        explanation.append("Market cap: UNVERIFIED (market-cap data unavailable)")
    return {
        "security_id": f"{a.get('market')}:{a.get('ticker')}",
        "market": a.get("market"),
        "ticker": a.get("ticker"),
        "company": a.get("company") or "",
        "score": sc["score"],
        "verdict": a.get("verdict"),
        "confidence": a.get("confidence"),
        "close": a.get("close"),
        "momentum_20": a.get("momentum_20"),
        "rsi_14": a.get("rsi_14"),
        "above_sma": a.get("above_sma"),
        "volume_ratio": a.get("volume_ratio"),
        "news_score": a.get("news_score"),
        "matched": sc["matched"],
        "explanation": explanation,
        "missing": sc["missing"],
        "missing_required": sc.get("missing_required", []),
        "status": status,
        "match_reason": (
            "Insufficient data for required inputs: " + ", ".join(sc.get("missing_required", []))
            if status == "not_evaluable"
            else ""
        ),
        "price_status": a.get("price_status") or (a.get("price") or {}).get("data_status", "ready"),
        "price_as_of": a.get("price_as_of") or (a.get("price") or {}).get("as_of", ""),
        "market_cap_unverified": market_cap_unverified,
    }


def screen_workflow(
    prompt: str | None = None,
    db: Database | None = None,
    market: str | None = None,
    limit: int = 30,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Screen the live universe for a workflow and return REAL qualifying securities.

    This is the Agent's strategy-discovery capability (no separate strategy
    engine). Cheap filters + technical signals come from the existing screener;
    expensive Research/Committee runs are NOT performed across the whole universe.
    """
    if criteria is None:
        criteria = parse_strategy_text(prompt or "")
    if market:
        criteria["market"] = market
    if criteria.get("market"):
        criteria["market"] = criteria["market"].upper()
    min_score = _wf_num(criteria.get("min_score"), 0.0) or 0.0
    market_cap_unverified = any("market_cap" in n for n in criteria.get("notes", []))

    from .screener import run as screener_run

    if db is None:
        db = _db()
    screened = screener_run(
        db,
        market=criteria.get("market") or None,
        verdict=criteria.get("verdict") or "",
        limit=500,
    )

    qualifying: list[dict[str, Any]] = []
    not_evaluable: list[dict[str, Any]] = []
    for a in screened:
        sc = _wf_score(a, criteria)
        if not sc["evaluable"]:
            not_evaluable.append(_wf_result(a, sc, "not_evaluable", market_cap_unverified))
            continue
        if not _wf_apply_gates(a, criteria):
            continue
        if sc["score"] < min_score:
            continue
        qualifying.append(_wf_result(a, sc, "qualifying", market_cap_unverified))

    qualifying.sort(key=lambda r: r["score"], reverse=True)
    return {
        "workflow": criteria.get("description") or prompt or "",
        "criteria": criteria,
        "market_cap_unverified": market_cap_unverified,
        "universe_size": len(screened),
        "qualifying_count": len(qualifying),
        "qualifying": qualifying[: int(limit)],
        "not_evaluable": not_evaluable[:50],
    }


def tool_workflow(args: dict) -> dict:
    """Agent tool: run a workflow prompt against the universe and return candidates."""
    prompt = str(args.get("prompt", "")) or None
    market = str(args.get("market", "")).upper() or None
    limit = int(args.get("limit", 30))
    criteria = args.get("criteria")
    return screen_workflow(prompt=prompt, market=market, limit=limit, criteria=criteria)


TOOLS["workflow"] = (
    tool_workflow,
    "Run a WORKFLOW prompt against the live universe and return REAL qualifying "
    "securities (each with a canonical market:ticker id), ranked by a composite of "
    "available signals, plus a separate NOT_EVALUABLE list for names that could not "
    "be judged due to missing data. This IS the Agent's strategy screening -- no "
    "separate strategy system exists. Use it when the user asks to find stocks "
    "matching momentum/trend/volume/conviction criteria.",
    {
        "prompt": "Free-text workflow, e.g. 'strong momentum, bullish trend, increasing volume'. REQUIRED if no criteria.",
        "market": "Optional market filter (e.g. NASDAQ).",
        "limit": "Max qualifying results (default 30).",
        "criteria": "Optional pre-structured criteria dict (advanced).",
    },
)


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
