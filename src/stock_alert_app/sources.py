from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
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

# Drop articles older than this (prevents stale 2017-era Google News results).
MAX_ARTICLE_AGE_DAYS = 7

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")


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


def _strip_html(text: str) -> str:
    """Remove HTML tags, decode entities, collapse whitespace."""
    if not text:
        return ""
    no_tags = _TAG_RE.sub("", text)
    decoded = unescape(no_tags)
    return _MULTI_SPACE_RE.sub(" ", decoded).strip()


def _parse_date(raw: str) -> str:
    """Parse an RSS/Atom date string into ISO 8601 (UTC).

    feedparser already normalises most pubDate/Atom timestamps into a
    struct_time on entry.published_parsed. Fall back to raw string, then empty.
    """
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass
    return raw.strip()


def _is_within_age_limit(published_iso: str) -> bool:
    """True if the article date is within MAX_ARTICLE_AGE_DAYS of now (or unparseable)."""
    if not published_iso:
        return True  # keep unknown dates — sort them last
    try:
        dt = datetime.fromisoformat(published_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt >= datetime.now(UTC) - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    except (ValueError, TypeError):
        return True  # unparseable → keep, sort last


# ---------------------------------------------------------------------------
# Fincept-style free RSS feed catalog
# ---------------------------------------------------------------------------
# Curated from Fincept's NewsService_Feeds.cpp default_feeds().  These are
# free, public RSS feeds that require no API key.  Grouped by category so the
# ingestor can fan them out across the universe.

GLOBAL_RSS_FEEDS: list[dict[str, str]] = [
    # Tier 1 — Wire Services & Regulators
    {"url": "https://www.sec.gov/news/pressreleases.rss", "source": "SEC", "category": "REGULATORY"},
    {"url": "https://www.federalreserve.gov/feeds/press_all.xml", "source": "FEDERAL RESERVE", "category": "REGULATORY"},
    {"url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "source": "UN", "category": "GEOPOLITICS"},

    # Tier 2 — Major Financial Media
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "source": "MARKETWATCH", "category": "MARKETS"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "source": "CNBC", "category": "MARKETS"},
    {"url": "https://seekingalpha.com/market_currents.xml", "source": "SEEKING ALPHA", "category": "MARKETS"},
    {"url": "https://www.investing.com/rss/news.rss", "source": "INVESTING.COM", "category": "MARKETS"},

    # Tier 2 — Global News
    {"url": "http://feeds.bbci.co.uk/news/world/rss.xml", "source": "BBC", "category": "GEOPOLITICS"},
    {"url": "http://feeds.bbci.co.uk/news/business/rss.xml", "source": "BBC", "category": "MARKETS"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "source": "AL JAZEERA", "category": "GEOPOLITICS"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "source": "NYT", "category": "GEOPOLITICS"},
    {"url": "https://www.theguardian.com/world/rss", "source": "GUARDIAN", "category": "GEOPOLITICS"},

    # Tier 2 — Tech
    {"url": "https://techcrunch.com/feed/", "source": "TECHCRUNCH", "category": "TECH"},
    {"url": "https://www.wired.com/feed/rss", "source": "WIRED", "category": "TECH"},

    # Tier 2 — Asia
    {"url": "https://www.scmp.com/rss/91/feed", "source": "SCMP", "category": "ASIA"},
    {"url": "https://www.thehindu.com/business/feeder/default.rss", "source": "THE HINDU", "category": "MARKETS"},

    # Tier 2 — Central Banks
    {"url": "https://www.ecb.europa.eu/rss/press.html", "source": "ECB", "category": "REGULATORY"},
    {"url": "https://www.bankofengland.co.uk/rss/news", "source": "BOE", "category": "REGULATORY"},

    # Tier 3 — Economic / Macro
    {"url": "https://feeds.feedburner.com/zerohedge/feed", "source": "ZEROHEDGE", "category": "ECONOMIC"},

    # Tier 3 — Tech (additional)
    {"url": "https://feeds.arstechnica.com/arstechnica/index", "source": "ARS TECHNICA", "category": "TECH"},
    {"url": "https://www.theverge.com/rss/index.xml", "source": "THE VERGE", "category": "TECH"},

    # Tier 2 — Commodities
    {"url": "https://oilprice.com/rss/main", "source": "OILPRICE", "category": "ENERGY"},
]


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def google_news_url(query: str, country_code: str = "US") -> str:
    q = quote(query)
    ceid = f"{country_code}:en"
    return (
        f"https://news.google.com/rss/search?q={q}&hl=en&gl={country_code}&ceid={ceid}"
    )


def yahoo_finance_url(symbol: str, region: str = "US") -> str:
    return (
        f"https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={quote(symbol)}&region={region}&lang=en-US"
    )


def newsapi_url(query: str, api_key: str, language: str = "en") -> str:
    q = quote(query)
    return (
        f"https://newsapi.org/v2/everything?q={q}&language={language}&apiKey={api_key}"
    )


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


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
        # Use feedparser's parsed timestamp when available (struct_time → ISO).
        raw_date = ""
        if entry.get("published_parsed"):
            try:
                import time as _t

                dt = datetime.fromtimestamp(
                    _t.mktime(entry.published_parsed), tz=UTC
                )
                raw_date = dt.isoformat()
            except (ValueError, TypeError):
                raw_date = entry.get("published", "") or entry.get("updated", "")
        else:
            raw_date = entry.get("published", "") or entry.get("updated", "")
        published_iso = _parse_date(raw_date)
        # Filter out stale articles (e.g. 2017 Google News results).
        if not _is_within_age_limit(published_iso):
            continue
        articles.append(
            Article(
                title=_strip_html(entry.get("title", "")),
                url=link.strip(),
                summary=_strip_html(entry.get("summary", "") or ""),
                source=_strip_html(source) if source else "",
                published_at=published_iso,
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


def fetch_financial_feeds(
    feed_urls: list[str], fallback_query: str = ""
) -> list[Article]:
    articles: list[Article] = []
    for url in feed_urls:
        articles.extend(_fetch_rss_cached(url, fallback_query or url))
    return articles


def fetch_global_feeds() -> list[Article]:
    """Fetch all Fincept-style global RSS feeds (no API key required).

    Returns articles with an empty ``query`` (they are not ticker-specific).
    The ingestor's ``classify`` step matches them to tickers by name.
    """
    articles: list[Article] = []
    for feed in GLOBAL_RSS_FEEDS:
        feed_articles = _fetch_rss_cached(feed["url"], feed["url"])
        # Override the source name with the curated one when the feed itself
        # doesn't provide a <source> element.
        for a in feed_articles:
            if not a.source:
                a.source = feed["source"]
        articles.extend(feed_articles)
    return articles


def fetch_yahoo_finance(
    symbol: str, region: str = "US", query: str = ""
) -> list[Article]:
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
        published_iso = _parse_date(item.get("publishedAt") or "")
        if not _is_within_age_limit(published_iso):
            continue
        articles.append(
            Article(
                title=_strip_html(item.get("title") or ""),
                url=url.strip(),
                summary=_strip_html(item.get("description") or ""),
                source=(item.get("source") or {}).get("name", "")
                if isinstance(item.get("source"), dict)
                else "",
                published_at=published_iso,
                query=query,
            )
        )
    return articles


def clear_rss_cache() -> None:
    """Clear the RSS cache. Useful for testing or forced refresh."""
    global _RSS_CACHE
    _RSS_CACHE.clear()
