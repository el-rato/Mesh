"""Researcher layer.

Structures evidence for a security into a ``ResearchBrief``. The Researcher
gathers and organizes evidence; it does NOT make the investment decision.

Only information backed by real data is populated. Anything without a reliable
source is explicitly ``NO_DATA`` / unavailable — never fabricated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

NO_DATA = "no_data"
OK = "ok"


@dataclass
class ResearchBrief:
    ticker: str = ""
    company: str = ""
    exchange: str = ""
    market: str = ""
    currency: str = ""
    recent_developments: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    news_count: int = 0
    news_sentiment: float | None = None
    news_label: str = ""
    institutional: dict[str, Any] | None = None
    bull_evidence: list[str] = field(default_factory=list)
    bear_evidence: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    analyzed_at: str = ""
    freshness: float | None = None
    confidence: float | None = None
    status: str = NO_DATA

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "exchange": self.exchange,
            "market": self.market,
            "currency": self.currency,
            "recent_developments": list(self.recent_developments),
            "catalysts": list(self.catalysts),
            "risks": list(self.risks),
            "news_count": self.news_count,
            "news_sentiment": self.news_sentiment,
            "news_label": self.news_label,
            "institutional": self.institutional,
            "bull_evidence": list(self.bull_evidence),
            "bear_evidence": list(self.bear_evidence),
            "provenance": list(self.provenance),
            "analyzed_at": self.analyzed_at,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "status": self.status,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_brief(
    *,
    ticker: str = "",
    company: str = "",
    exchange: str = "",
    market: str = "",
    currency: str = "",
    news_score: float | None = None,
    news_label: str = "",
    article_count: int = 0,
    evidence: list[tuple[str, str, str, float]] | None = None,
    institutional: dict[str, Any] | None = None,
    bull_evidence: list[str] | None = None,
    bear_evidence: list[str] | None = None,
) -> ResearchBrief:
    """Build a ResearchBrief from real evidence.

    ``evidence`` is a list of ``(title, source, sentiment_label, score)`` for
    scored articles. Bull/bear evidence comes from the existing signal factors.
    """
    evidence = evidence or []
    catalysts: list[str] = []
    risks: list[str] = []
    developments: list[str] = []
    sources: set[str] = set()
    for title, source, label, score in evidence:
        if source:
            sources.add(source)
        text = title.strip()[:90] if title else ""
        if not text:
            continue
        if score >= 0.3:
            catalysts.append(text)
        elif score <= -0.3:
            risks.append(text)
        else:
            developments.append(text)

    has_news = bool(evidence) or (news_score is not None)
    status = OK if (has_news or institutional) else NO_DATA

    provenance = []
    if has_news:
        provenance.append(f"{article_count or len(evidence)} scored news items")
        if sources:
            provenance.append(f"sources: {', '.join(sorted(sources)[:5])}")
    if institutional:
        provenance.append(
            f"13F: {institutional.get('holding_funds', 0)} tracked funds hold"
        )

    # Research confidence from evidence breadth/agreement.
    confidence = None
    if status == OK:
        breadth = (1 if has_news else 0) + (1 if institutional else 0)
        conf = 0.3 + 0.35 * breadth
        if bull_evidence or bear_evidence:
            conf += 0.1
        confidence = round(min(1.0, conf), 4)

    return ResearchBrief(
        ticker=ticker,
        company=company,
        exchange=exchange,
        market=market,
        currency=currency,
        recent_developments=developments[:8],
        catalysts=catalysts[:8],
        risks=risks[:8],
        news_count=article_count or len(evidence),
        news_sentiment=news_score,
        news_label=news_label,
        institutional=institutional,
        bull_evidence=list(bull_evidence or []),
        bear_evidence=list(bear_evidence or []),
        provenance=provenance,
        analyzed_at=_now_iso(),
        freshness=0.5 if has_news else None,
        confidence=confidence,
        status=status,
    )


def no_data_brief(ticker: str = "", company: str = "") -> ResearchBrief:
    """Explicit unavailable brief (e.g. legacy rows, unanalysed securities)."""
    return ResearchBrief(ticker=ticker, company=company, analyzed_at=_now_iso(), status=NO_DATA)
