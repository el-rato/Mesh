from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .scorers import SentimentResult

RELIABLE_SOURCES = {
    "reuters",
    "bloomberg",
    "wall street journal",
    "financial times",
    "cnbc",
    "marketwatch",
    "barron's",
    "the economist",
    "axios",
    "associated press",
    "yahoo finance",
    "investing.com",
    "nikkei",
    "nikkei asia",
    "morningstar",
    "fortune",
    "forbes",
    "business standard",
    "the hindu businessline",
    "moneycontrol",
    "bbc business",
    "the guardian business",
    "al jazeera business",
    "the straits times",
    "south china morning post",
    "les echos",
    "handelsblatt",
    "caixin",
    "coindesk",
    "techcrunch",
    "mining.com",
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
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
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


def aggregate_sentiment(
    scores: list[tuple[SentimentResult, str, str]],
    half_life_hours: float = 72.0,
) -> SourceSentiment:
    """Aggregate per-article sentiment into one weighted score.

    Args:
        scores: iterable of (SentimentResult, source, published_at) tuples.
        half_life_hours: recency decay half-life.

    Returns:
        A SourceSentiment combining recency- and source-reliability weighting.
    """
    now = datetime.now(timezone.utc)
    total_weight = 0.0
    weighted_sum = 0.0
    pos = neg = neu = 0
    confidence_sum = 0.0
    count = len(scores)

    parsed_times: list[datetime | None] = []
    for result, source, published in scores:
        published_at = _parse_time(published)
        w = _source_weight(source) * _recency_weight(published_at, now, half_life_hours)
        total_weight += w
        weighted_sum += result.score * w
        confidence_sum += 1.0 - abs(result.score)
        parsed_times.append(published_at)
        if result.label == "positive":
            pos += 1
        elif result.label == "negative":
            neg += 1
        else:
            neu += 1

    if count == 0 or total_weight == 0:
        return SourceSentiment(0.0, "neutral", count, pos, neg, neu, 0.0, 0.0)

    score = max(-1.0, min(1.0, weighted_sum / total_weight))
    if score > 0.15:
        label = "bullish"
    elif score < -0.15:
        label = "bearish"
    else:
        label = "neutral"

    freshness = sum(_recency_weight(p, now, half_life_hours) for p in parsed_times) / count
    return SourceSentiment(
        score=round(score, 4),
        label=label,
        article_count=count,
        positive_count=pos,
        negative_count=neg,
        neutral_count=neu,
        avg_confidence=round(confidence_sum / count, 4),
        freshness=round(freshness, 4),
    )
