from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from .config import settings
from .db import Database
from .markets import Market, Ticker, load_markets
from . import sources

logger = logging.getLogger(__name__)


def _load_markets() -> dict[str, Market]:
    return load_markets(settings.markets_dir)


@dataclass
class IngestResult:
    fetched: int = 0
    inserted: int = 0
    duplicate: int = 0
    classified: int = 0


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("https://"):
        url = "http://" + url[len("https://") :]
    return url.rstrip("/")


class MarketIngestor:
    def __init__(
        self,
        market: Market,
        db: Database,
        *,
        use_google: bool = True,
        use_yahoo: bool = False,
    ) -> None:
        self.market = market
        self.db = db
        self.use_google = use_google
        self.use_yahoo = use_yahoo
        self.country_code = market.country

    def _ticker_queries(self, ticker: Ticker) -> list[str]:
        return [ticker.search_query]

    def _collect(self) -> list[tuple[str | None, sources.Article]]:
        grouped: list[tuple[str | None, sources.Article]] = []

        if self.use_google:
            for query in self.market.rss_queries:
                for art in sources.fetch_google_news(query, self.country_code):
                    grouped.append((None, art))
            for ticker in self.market.tickers.values():
                for query in self._ticker_queries(ticker):
                    for art in sources.fetch_google_news(query, self.country_code):
                        grouped.append((ticker.symbol, art))

        if self.use_yahoo:
            for ticker in self.market.tickers.values():
                symbol = ticker.symbol + self.market.yahoo_suffix
                region = "US" if self.market.country == "US" else ""
                for art in sources.fetch_yahoo_finance(symbol, region=region, query=ticker.symbol):
                    grouped.append((ticker.symbol, art))

        if settings.news_api_key:
            for query in self.market.rss_queries:
                for art in sources.fetch_newsapi(query, settings.news_api_key):
                    grouped.append((None, art))

        return grouped

    def classify(self, article: sources.Article, hint: str | None = None) -> list[str]:
        text = article.searchable_text.lower()
        hits: list[str] = []
        for symbol, ticker in self.market.tickers.items():
            rgl = ticker.name.lower()
            if hint and symbol.upper() == hint:
                if rgl in text or symbol.lower() in text:
                    hits.append(symbol)
            elif rgl and rgl in text:
                hits.append(symbol)
        return hits

    def ingest(self) -> IngestResult:
        result = IngestResult()
        all_articles = self._collect()
        seen: set[str] = set()
        for hint, article in all_articles:
            seen_url = _normalize_url(article.url)
            if seen_url in seen:
                continue
            seen.add(seen_url)
            if not article.title:
                continue
            result.fetched += 1
            result.classified += 1
            matched = self.classify(article, hint)
            if not matched:
                continue
            for symbol in matched:
                inserted_id = self.db.insert_news_item(
                    market=self.market.code,
                    ticker=self.market.get_ticker(symbol).symbol,
                    title=article.title,
                    url=article.url,
                    source=article.source,
                    summary=article.summary,
                    published_at=article.published_at,
                )
                if inserted_id is not None:
                    result.inserted += 1
                else:
                    result.duplicate += 1
        return result


def run_ingest(market_codes: Iterable[str] | None = None, db_path: str | None = None) -> dict[str, IngestResult]:
    markets = load_markets(settings.markets_dir)
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else list(settings.default_markets)
    results: dict[str, IngestResult] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            logger.warning("Unknown market %s, skipping", code)
            continue
        ingestor = MarketIngestor(market, db)
        results[code] = ingestor.ingest()
    return results