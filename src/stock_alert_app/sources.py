from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import feedparser
import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TIMEOUT = 20.0

# Simple in-memory RSS cache: {url: (articles, expiry_ts)}
_RSS_CACHE: dict[str, tuple[list[Article], float]] = {}
_RSS_CACHE_TTL = 600  # 10 minutes


@dataclass
class Article:
    title: str
    url: str
    summary: str
    source: str
    published_at: str
    query: str

    @property
    def searchable_text(self) -> str:
        return f"{self.title} {self.summary}"


def google_news_url(query: str, country_code: str = "US") -> str:
    q = quote(query)
    ceid = f"{country_code}:en"
    return (
        f"https://news.google.com/rss/search?q={q}"
        f"&hl=en&gl={country_code}&ceid={ceid}"
    )


def yahoo_finance_url(symbol: str, region: str = "US") -> str:
    return (
        f"https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={quote(symbol)}&region={region}&lang=en-US"
    )


def newsapi_url(query: str, api_key: str, language: str = "en") -> str:
    q = quote(query)
    return f"https://newsapi.org/v2/everything?q={q}&language={language}&apiKey={api_key}"


def _parse_feed(text: str, query: str) -> list[Article]:
    parsed = feedparser.parse(text)
    articles: list[Article] = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        source = ""
        if "source" in entry and hasattr(entry.source, "title"):
            source = entry.source.title
        elif "media_credit" in entry:
            source = str(entry.media_credit)
        articles.append(
            Article(
                title=entry.get("title", "").strip(),
                url=link.strip(),
                summary=(entry.get("summary", "") or "").strip(),
                source=source,
                published_at=(entry.get("published", "") or entry.get("updated", "") or ""),
                query=query,
            )
        )
    return articles


def _fetch_rss_cached(url: str, query: str) -> list[Article]:
    now = time.time()
    if url in _RSS_CACHE:
        articles, expiry = _RSS_CACHE[url]
        if now < expiry:
            logger.debug("RSS cache hit for %s (%d articles)", url, len(articles))
            # Update query on cached articles
            for a in articles:
                a.query = query
            return articles
    articles = fetch_rss(url, query)
    _RSS_CACHE[url] = (articles, now + _RSS_CACHE_TTL)
    return articles


def fetch_rss(url: str, query: str) -> list[Article]:
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
        articles = _parse_feed(resp.text, query)
        logger.debug("Fetched %d articles from %s", len(articles), url)
        return articles
    except httpx.HTTPError as exc:
        logger.warning("RSS fetch failed for %s: %s", url, exc)
        return []


def fetch_google_news(query: str, country_code: str = "US") -> list[Article]:
    return _fetch_rss_cached(google_news_url(query, country_code), query)


def fetch_financial_feeds(feed_urls: list[str], fallback_query: str = "") -> list[Article]:
    articles: list[Article] = []
    for url in feed_urls:
        articles.extend(_fetch_rss_cached(url, fallback_query or url))
    return articles


def fetch_yahoo_finance(symbol: str, region: str = "US", query: str = "") -> list[Article]:
    return _fetch_rss_cached(yahoo_finance_url(symbol, region), query or symbol)


def fetch_newsapi(query: str, api_key: str) -> list[Article]:
    if not api_key:
        return []
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(newsapi_url(query, api_key))
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("NewsAPI fetch failed for %r: %s", query, exc)
        return []
    articles: list[Article] = []
    for item in data.get("articles", []):
        url = item.get("url", "")
        if not url:
            continue
        articles.append(
            Article(
                title=(item.get("title") or "").strip(),
                url=url.strip(),
                summary=(item.get("description") or "").strip(),
                source=(item.get("source") or {}).get("name", "") if isinstance(item.get("source"), dict) else "",
                published_at=item.get("publishedAt") or "",
                query=query,
            )
        )
    return articles


def clear_rss_cache() -> None:
    """Clear the RSS cache. Useful for testing or forced refresh."""
    global _RSS_CACHE
    _RSS_CACHE.clear()
