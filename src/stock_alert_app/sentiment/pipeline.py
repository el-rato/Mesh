from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from ..config import settings
from ..db import Database
from .aggregate import SourceSentiment, aggregate_sentiment
from .scorers import FinBERTScorer, LexiconScorer, LLMScorer, Scorer, SentimentResult

logger = logging.getLogger(__name__)


@dataclass
class ScoreResult:
    scored: int = 0
    skipped: int = 0
    headlines: dict[str, SourceSentiment] = field(default_factory=dict)


class SentimentPipeline:
    def __init__(
        self,
        db: Database,
        scorer: Scorer | None = None,
        *,
        prefer_finbert: bool = True,
    ) -> None:
        self.db = db
        if scorer is not None:
            self.scorer = scorer
        elif prefer_finbert:
            try:
                self.scorer = FinBERTScorer()
            except RuntimeError:
                logger.info("FinBERT unavailable, falling back to lexicon scorer")
                self.scorer = LexiconScorer()
        else:
            self.scorer = LexiconScorer()

    def model_name(self) -> str:
        return self.scorer.name

    def _score_if_unscored(self) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT n.id, n.title, n.summary
                   FROM news_items n
                   LEFT JOIN sentiment_scores s ON s.news_item_id = n.id AND s.model = ?
                   WHERE s.id IS NULL
                   ORDER BY n.id
                   LIMIT ?""",
                (self.model_name(), 500),
            ).fetchall()

        count = 0
        for row in rows:
            text = f"{row['title']} {row['summary']}".strip()
            result = self.scorer.score(text)
            if not text:
                continue
            self.db.insert_sentiment(
                news_item_id=row["id"],
                model=self.model_name(),
                score=result.score,
                label=result.label,
                positive=result.positive,
                negative=result.negative,
                neutral=result.neutral,
            )
            count += 1
        return count

    def run(self, *, limit: int = 500) -> ScoreResult:
        scored = self._score_unscored(limit=limit)
        result = ScoreResult(scored=scored)

        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT n.market, n.ticker, n.source, n.published_at,
                          s.score, s.label, s.positive, s.negative, s.neutral
                   FROM news_items n
                   JOIN sentiment_scores s ON s.news_item_id = n.id
                   WHERE s.model = ?
                   ORDER BY n.published_at DESC""",
                (self.model_name(),),
            ).fetchall()

        grouped: dict[tuple[str, str], list[tuple[SentimentResult, str, str]]] = {}
        for row in rows:
            key = (row["market"], row["ticker"])
            grouped.setdefault(key, []).append(
                (
                    SentimentResult(
                        score=row["score"],
                        label=row["label"],
                        positive=row["positive"],
                        negative=row["negative"],
                        neutral=row["neutral"],
                    ),
                    row["source"],
                    row["published_at"],
                )
            )

        for (market, ticker), items in grouped.items():
            hkey = f"{market}:{ticker}"
            result.headlines[hkey] = aggregate_sentiment(items)

        return result

    def _score_unscored(self, limit: int = 500) -> int:
        rows = self._unscored_rows(limit)
        count = 0
        for row in rows:
            text = f"{row['title']} {row['summary']}".strip()
            if not text:
                continue
            scored = self.scorer.score(text)
            self.db.insert_sentiment(
                news_item_id=row["id"],
                model=self.model_name(),
                score=scored.score,
                label=scored.label,
                positive=scored.positive,
                negative=scored.negative,
                neutral=scored.neutral,
            )
            count += 1
        return count

    def _unscored_rows(self, limit: int = 500):
        with self.db.connect() as conn:
            return conn.execute(
                """SELECT n.id, n.title, n.summary
                   FROM news_items n
                   LEFT JOIN sentiment_scores s ON s.news_item_id = n.id AND s.model = ?
                   WHERE s.id IS NULL
                   ORDER BY n.id
                   LIMIT ?""",
                (self.model_name(), limit),
            ).fetchall()

    def aggregate_all(self) -> dict[str, SourceSentiment]:
        return self.run().headlines


def make_llm_scorer() -> LLMScorer | None:
    if settings.llm_api_key:
        return LLMScorer(api_key=settings.llm_api_key, model=settings.llm_model)
    return None


def run_sentiment(db_path: str | None = None, use_llm: bool = False) -> dict[str, SourceSentiment]:
    db = Database(db_path or settings.db_path)
    db.init_schema()
    pipeline = SentimentPipeline(db)
    result = pipeline.run()
    logger.info("Scored %d headlines with %s", result.scored, pipeline.model_name())
    return result.headlines


def run_final_verdict(text: str) -> SentimentResult:
    """Score a combined narrative with the LLM if configured, else the default scorer."""
    llm = make_llm_scorer()
    if llm and settings.llm_model:
        return llm.score(text)
    return LexiconScorer().score(text)