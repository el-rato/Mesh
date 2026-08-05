from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .scorers import SentimentResult

RELIABLE_SOURCES = {
    "reuters", "bloomberg", "wall street journal", "wsj", "financial times",
    "ft", "cnbc", "marketwatch", "barron's", "the economist", "axios",
    "associated press", "ap", "yahoo finance", "investing.com",
}


@dataclass
class SourceSentiment:
    score: float
    label: str
    article_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    avg_confidence: float
    freshness: float = 0.0

    def as_dict(self) -> dict[str, float | str | int]:
        return {
            "score": round(self.score, 4),
            "label": self.label,
            "article_count": self.article_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "neutral_count": self.neutral_count,
            "avg_confidence": round(self.avg_confidence, 4),
            "freshness": round(self.freshness, 4),
        }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None


def _source_weight(source: str) -> float:
    s = source.lower().strip()
    if not s:
        return 0.5
    for rel in RELIABLE_SOURCES:
        if rel in s:
            return 1.5
    return 1.0


def _recency_weight(published_at: datetime | None, now: datetime, half_life_hours: float = 72.0) -> float:
    if published_at is None:
        return 0.5
    elapsed_hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-elapsed_hours / half_life_hours)


def aggregate_sentiment(scores: list[tuple[SentimentResult, str, str]]) -> None:
    raise NotImplementedError(
        "use the aggregate_sentiment() module function defined in this file"
    )