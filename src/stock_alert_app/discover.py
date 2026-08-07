from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import settings
from .db import Database
from .markets import Market, load_markets
from .sources import fetch_google_news, fetch_financial_feeds
from .sentiment.pipeline import SentimentPipeline
from .sentiment.scorers import LexiconScorer
from .ingest import MarketIngestor

logger = logging.getLogger(__name__)

COMPANY_TICKER_PATH = Path(__file__).resolve().parent / "data" / "company_tickers.json"


@dataclass
class DiscoveredTicker:
    ticker: str
    company: str
    market: str
    score: float
    headlines: list[str]
    matched_keywords: list[str]


def load_company_mapping() -> dict[str, list[str]]:
    with open(COMPANY_TICKER_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_reverse_mapping(company_map: dict[str, list[str]]) -> dict[str, str]:
    rev: dict[str, str] = {}
    for ticker, names in company_map.items():
        for name in names:
            rev[name.lower()] = ticker
    return rev


def extract_companies(text: str, reverse_map: dict[str, str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    text_lower = text.lower()
    for name, ticker in reverse_map.items():
        if re.search(rf"\b{re.escape(name)}\b", text_lower):
            found.append((ticker, name))
    return found


def discover_from_feeds(
    market_codes: Iterable[str],
    min_score: float = 0.25,
    max_new_per_cycle: int = 10,
    *,
    min_articles: int = 5,
    use_lexicon: bool = True,
    cooldown_days: int = 7,
) -> list[DiscoveredTicker]:
    company_map = load_company_mapping()
    reverse_map = build_reverse_mapping(company_map)
    markets = load_markets(settings.markets_dir)
    db = Database(settings.db_path)
    db.init_schema()

    scorer = LexiconScorer() if use_lexicon else None
    pipeline = SentimentPipeline(db, scorer=scorer, prefer_finbert=not use_lexicon)

    seen_tickers = _get_recently_discovered(db, cooldown_days)
    results: list[DiscoveredTicker] = []

    for code in market_codes:
        market = markets.get(code)
        if not market:
            continue

        ticker_hits: dict[str, list[str]] = {}

        for query in market.rss_queries:
            for art in fetch_google_news(query, market.country):
                full = f"{art.title} {art.summary}"
                matches = extract_companies(full, reverse_map)
                for ticker, name in matches:
                    if ticker in seen_tickers or ticker in market.tickers:
                        continue
                    ticker_hits.setdefault(ticker, []).append(f"{art.title} — {art.summary[:120]}")

        for art in fetch_financial_feeds(market.financial_feeds):
            full = f"{art.title} {art.summary}"
            matches = extract_companies(full, reverse_map)
            for ticker, name in matches:
                if ticker in seen_tickers or ticker in market.tickers:
                    continue
                ticker_hits.setdefault(ticker, []).append(f"{art.title} — {art.summary[:120]}")

        for ticker, headlines in ticker_hits.items():
            if len(headlines) < min_articles:
                continue
            combined = " | ".join(headlines)
            scored = pipeline.scorer.score(combined) if pipeline.scorer else LexiconScorer().score(combined)
            if scored.score >= min_score:
                results.append(DiscoveredTicker(
                    ticker=ticker,
                    company=company_map[ticker][0],
                    market=code,
                    score=scored.score,
                    headlines=headlines,
                    matched_keywords=[h.split("—")[0].strip() for h in headlines],
                ))
                seen_tickers.add(ticker)
                _mark_discovered(db, ticker, code)
                if len(results) >= max_new_per_cycle:
                    return results

    return results


def _get_recently_discovered(db: Database, cooldown_days: int) -> set[str]:
    return db.get_recently_discovered(cooldown_days)


def _mark_discovered(db: Database, ticker: str, market: str) -> None:
    db.mark_discovered(ticker, market)


def auto_register_tickers(discovered: list[DiscoveredTicker]) -> list[str]:
    markets = load_markets(settings.markets_dir)
    added: list[str] = []
    for d in discovered:
        market = markets.get(d.market)
        if not market or d.ticker in market.tickers:
            continue
        logger.info("Auto-registering %s (%s) on %s", d.ticker, d.company, d.market)
        added.append(f"{d.market}:{d.ticker}")
    return added