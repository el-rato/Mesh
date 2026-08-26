from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from . import sources
from .config import settings
from .db import Database
from .markets import Market, Ticker, load_markets, scan_market_codes

logger = logging.getLogger(__name__)


def _load_markets() -> dict[str, Market]:
    return load_markets(settings.markets_dir)


def _enabled_codes() -> list[str]:
    return scan_market_codes(settings.markets_dir)


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
        use_financial_feeds: bool = True,
        use_global_feeds: bool = True,
    ) -> None:
        self.market = market
        self.db = db
        self.use_google = use_google
        self.use_yahoo = use_yahoo
        self.use_financial_feeds = use_financial_feeds
        self.use_global_feeds = use_global_feeds
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

        if self.use_financial_feeds and self.market.financial_feeds:
            for art in sources.fetch_financial_feeds(self.market.financial_feeds):
                grouped.append((None, art))

        if self.use_global_feeds:
            for art in sources.fetch_global_feeds():
                grouped.append((None, art))

        if self.use_yahoo:
            for ticker in self.market.tickers.values():
                symbol = ticker.symbol + self.market.yahoo_suffix
                region = "US" if self.market.country == "US" else ""
                for art in sources.fetch_yahoo_finance(
                    symbol, region=region, query=ticker.symbol
                ):
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

    def _store(self, grouped: list[tuple[str | None, sources.Article]]) -> IngestResult:
        result = IngestResult()
        seen: set[str] = set()
        for hint, article in grouped:
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

    def collect_ticker(self, ticker: Ticker) -> list[tuple[str | None, sources.Article]]:
        """Collect only the articles that target a single ticker (for on-demand lookup)."""
        grouped: list[tuple[str | None, sources.Article]] = []
        if self.use_google:
            for q in self._ticker_queries(ticker):
                for art in sources.fetch_google_news(q, self.country_code):
                    grouped.append((ticker.symbol, art))
        if self.use_yahoo:
            symbol = ticker.symbol + self.market.yahoo_suffix
            region = "US" if self.market.country == "US" else ""
            for art in sources.fetch_yahoo_finance(
                symbol, region=region, query=ticker.symbol
            ):
                grouped.append((ticker.symbol, art))
        return grouped

    def ingest_ticker(self, ticker: Ticker) -> IngestResult:
        """Fetch + store fresh news for a single ticker, classified only to it."""
        return self._store(self.collect_ticker(ticker))

    def ingest(self) -> IngestResult:
        return self._store(self._collect())


def run_ingest(
    market_codes: Iterable[str] | None = None, db_path: str | None = None
) -> dict[str, IngestResult]:
    markets = _load_markets()
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else _enabled_codes()
    results: dict[str, IngestResult] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            logger.warning("Unknown market %s, skipping", code)
            continue
        ingestor = MarketIngestor(market, db)
        results[code] = ingestor.ingest()
    return results


def run_ticker_ingest(
    market_code: str,
    ticker_symbol: str,
    db_path: str | None = None,
    *,
    use_google: bool = True,
    use_yahoo: bool = True,
) -> IngestResult | None:
    """Fetch + store fresh news for a single ticker (on-demand stock lookup)."""
    markets = _load_markets()
    market = markets.get(market_code)
    if not market:
        logger.warning("Unknown market %s", market_code)
        return None
    ticker = market.get_ticker(ticker_symbol)
    if not ticker:
        logger.warning("Unknown ticker %s:%s", market_code, ticker_symbol)
        return None
    db = Database(db_path or settings.db_path)
    db.init_schema()
    ingestor = MarketIngestor(
        market,
        db,
        use_google=use_google,
        use_yahoo=use_yahoo,
        use_financial_feeds=False,
        use_global_feeds=False,
    )
    return ingestor.ingest_ticker(ticker)


def ingest_global_news(db_path: str | None = None) -> dict[str, int]:
    """Fetch + store general (non-ticker) headlines from the global RSS feeds.

    Stored as ``market='GLOBAL'`` / ``ticker='NEWS'`` so the live news feed shows
    every new headline — world, tech, crypto, macro — not just those that match a
    tracked stock. Feeds are fetched concurrently and deduped by URL.
    """
    db = Database(db_path or settings.db_path)
    db.init_schema()
    articles = sources.fetch_global_feeds()
    inserted = duplicate = skipped = 0
    seen: set[str] = set()
    for a in articles:
        seen_url = _normalize_url(a.url)
        if seen_url in seen:
            continue
        seen.add(seen_url)
        if not a.title:
            skipped += 1
            continue
        iid = db.insert_news_item(
            market="GLOBAL",
            ticker="NEWS",
            title=a.title,
            url=a.url,
            source=a.source,
            summary=a.summary,
            published_at=a.published_at,
        )
        if iid is not None:
            inserted += 1
        else:
            duplicate += 1
    return {"fetched": len(articles), "inserted": inserted, "duplicate": duplicate, "skipped": skipped}
