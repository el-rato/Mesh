from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import praw
import prawcore

from .config import settings
from .sentiment.scorers import FinBERTScorer, LexiconScorer

logger = logging.getLogger(__name__)

TICKER_PATTERN = re.compile(r"\b[A-Z]{1,5}\b")
COMMON_FALSE_POSITIVES = {
    "THE",
    "AND",
    "FOR",
    "ARE",
    "BUT",
    "NOT",
    "YOU",
    "ALL",
    "CAN",
    "HAS",
    "HAD",
    "WAS",
    "THIS",
    "THAT",
    "WITH",
    "FROM",
    "THEY",
    "HAVE",
    "BEEN",
    "WERE",
    "WHEN",
    "YOUR",
    "THERE",
    "WHICH",
    "THEIR",
    "WOULD",
    "COULD",
    "SHOULD",
    "ABOUT",
    "AFTER",
    "BEFORE",
    "MORE",
    "SOME",
    "VERY",
    "WHAT",
    "WHERE",
    "WHO",
    "WHY",
    "HOW",
    "IF",
    "THEN",
    "ELSE",
    "THAN",
    "ITS",
    "IT'S",
    "DON'T",
    "WON'T",
    "CAN'T",
    "I'M",
    "YOU'RE",
    "WE'RE",
    "DD",
    "YOLO",
    "FOMO",
    "HODL",
    "ATH",
    "OTM",
    "ITM",
    "ATM",
    "CEO",
    "CFO",
    "SEC",
    "FDA",
    "USA",
    "EU",
    "UK",
    "GDP",
    "CPI",
    "FED",
    "IMO",
    "TLDR",
    "EDIT",
    "OP",
    "PS",
    "FYI",
    "ETF",
    "IPO",
    "EPS",
    "PE",
    "ROE",
    "ROI",
    "YOY",
    "QOQ",
    "GAAP",
    "EBITDA",
    "FCF",
}

SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "SecurityAnalysis",
    "ValueInvesting",
    "StockMarket",
    "pennystocks",
    "Shortsqueeze",
    "SPACs",
    "options",
]


@dataclass
class RedditPost:
    id: str
    subreddit: str
    title: str
    selftext: str
    score: int
    num_comments: int
    created_utc: float
    author: str
    url: str
    permalink: str
    tickers: list[str]
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"


@dataclass
class RedditRecommendation:
    ticker: str
    company: str
    mentions: int
    total_score: int
    avg_sentiment: float
    sentiment_label: str
    subreddits: list[str]
    top_posts: list[dict]
    bullish_signals: list[str]
    bearish_signals: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "mentions": self.mentions,
            "total_score": self.total_score,
            "avg_sentiment": round(self.avg_sentiment, 4),
            "sentiment_label": self.sentiment_label,
            "subreddits": self.subreddits,
            "top_posts": self.top_posts[:5],
            "bullish_signals": self.bullish_signals,
            "bearish_signals": self.bearish_signals,
        }


class RedditScanner:
    def __init__(self) -> None:
        self.client_id = settings.reddit_client_id
        self.client_secret = settings.reddit_client_secret
        self.user_agent = settings.reddit_user_agent
        self._reddit: praw.Reddit | None = None
        try:
            self.scorer = FinBERTScorer()
        except RuntimeError:
            self.scorer = LexiconScorer()

    def _get_reddit(self) -> praw.Reddit | None:
        if self._reddit is not None:
            return self._reddit
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Reddit API credentials not configured. Add REDDIT_CLIENT_ID and "
                "REDDIT_CLIENT_SECRET to .env (get them from "
                "https://www.reddit.com/prefs/apps, create a 'script' app)."
            )
        try:
            self._reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent,
            )
            return self._reddit
        except Exception as exc:
            logger.error("Failed to initialize Reddit client: %s", exc)
            return None

    def _extract_tickers(self, text: str) -> list[str]:
        if not text:
            return []
        words = TICKER_PATTERN.findall(text.upper())
        tickers = [w for w in words if w not in COMMON_FALSE_POSITIVES and len(w) >= 2]
        return list(dict.fromkeys(tickers))

    def _score_sentiment(self, text: str) -> tuple[float, str]:
        if not text:
            return 0.0, "neutral"
        try:
            result = self.scorer.score(text[:2000])
            return result.score, result.label
        except Exception:
            return 0.0, "neutral"

    def _scan_subreddit(
        self, subreddit_name: str, limit: int = 100, time_filter: str = "day"
    ) -> list[RedditPost]:
        reddit = self._get_reddit()
        if not reddit:
            return []

        posts: list[RedditPost] = []
        try:
            subreddit = reddit.subreddit(subreddit_name)
            for submission in subreddit.top(time_filter=time_filter, limit=limit):
                if (
                    submission.stickied
                    or submission.is_self
                    and not submission.selftext
                ):
                    continue

                full_text = f"{submission.title} {submission.selftext or ''}"
                tickers = self._extract_tickers(full_text)
                if not tickers:
                    continue

                sentiment_score, sentiment_label = self._score_sentiment(full_text)

                posts.append(
                    RedditPost(
                        id=submission.id,
                        subreddit=subreddit_name,
                        title=submission.title,
                        selftext=submission.selftext or "",
                        score=submission.score,
                        num_comments=submission.num_comments,
                        created_utc=submission.created_utc,
                        author=str(submission.author)
                        if submission.author
                        else "[deleted]",
                        url=submission.url,
                        permalink=f"https://reddit.com{submission.permalink}",
                        tickers=tickers,
                        sentiment_score=sentiment_score,
                        sentiment_label=sentiment_label,
                    )
                )
        except prawcore.exceptions.PrawcoreException as exc:
            logger.warning("Reddit API error for r/%s: %s", subreddit_name, exc)
        except Exception as exc:
            logger.error("Unexpected error scanning r/%s: %s", subreddit_name, exc)

        return posts

    def scan(
        self,
        subreddits: list[str] | None = None,
        limit_per_sub: int = 50,
        time_filter: str = "day",
        min_mentions: int = 2,
        min_score: int = 10,
    ) -> list[RedditRecommendation]:
        """Scan subreddits and return aggregated recommendations."""
        subs = subreddits or SUBREDDITS
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Reddit API credentials not configured. Add REDDIT_CLIENT_ID and "
                "REDDIT_CLIENT_SECRET to .env (get them from "
                "https://www.reddit.com/prefs/apps, create a 'script' app)."
            )
        all_posts: list[RedditPost] = []

        for sub in subs:
            posts = self._scan_subreddit(
                sub, limit=limit_per_sub, time_filter=time_filter
            )
            all_posts.extend(posts)

        # Aggregate by ticker
        ticker_data: dict[str, dict] = {}
        for post in all_posts:
            for ticker in post.tickers:
                if ticker not in ticker_data:
                    ticker_data[ticker] = {
                        "posts": [],
                        "total_score": 0,
                        "sentiments": [],
                        "subreddits": set(),
                        "bullish_signals": [],
                        "bearish_signals": [],
                    }
                data = ticker_data[ticker]
                data["posts"].append(post)
                data["total_score"] += post.score
                data["sentiments"].append(post.sentiment_score)
                data["subreddits"].add(post.subreddit)

                if post.sentiment_score > 0.2:
                    data["bullish_signals"].append(
                        f"r/{post.subreddit}: {post.title[:80]}"
                    )
                elif post.sentiment_score < -0.2:
                    data["bearish_signals"].append(
                        f"r/{post.subreddit}: {post.title[:80]}"
                    )

        # Build recommendations
        recommendations: list[RedditRecommendation] = []
        for ticker, data in ticker_data.items():
            if len(data["posts"]) < min_mentions:
                continue
            if data["total_score"] < min_score:
                continue

            avg_sentiment = (
                sum(data["sentiments"]) / len(data["sentiments"])
                if data["sentiments"]
                else 0.0
            )
            if avg_sentiment > 0.15:
                sentiment_label = "bullish"
            elif avg_sentiment < -0.15:
                sentiment_label = "bearish"
            else:
                sentiment_label = "neutral"

            top_posts = sorted(data["posts"], key=lambda p: p.score, reverse=True)[:5]
            recommendations.append(
                RedditRecommendation(
                    ticker=ticker,
                    company="",  # Could be enriched from company_tickers.json
                    mentions=len(data["posts"]),
                    total_score=data["total_score"],
                    avg_sentiment=avg_sentiment,
                    sentiment_label=sentiment_label,
                    subreddits=list(data["subreddits"]),
                    top_posts=[
                        {
                            "title": p.title,
                            "score": p.score,
                            "subreddit": p.subreddit,
                            "sentiment": p.sentiment_label,
                            "url": p.permalink,
                        }
                        for p in top_posts
                    ],
                    bullish_signals=data["bullish_signals"][:5],
                    bearish_signals=data["bearish_signals"][:5],
                )
            )

        recommendations.sort(key=lambda r: (r.mentions, r.total_score), reverse=True)
        return recommendations


def run_reddit_scan(
    subreddits: list[str] | None = None,
    limit_per_sub: int = 50,
    time_filter: str = "day",
    min_mentions: int = 2,
    min_score: int = 10,
) -> list[RedditRecommendation]:
    scanner = RedditScanner()
    return scanner.scan(subreddits, limit_per_sub, time_filter, min_mentions, min_score)
