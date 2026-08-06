from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable

from .config import settings
from .db import Database
from .verdict import Verdict, run_verdicts

logger = logging.getLogger(__name__)

ACTIONS = ("BUY", "HOLD", "SELL", "AVOID")


@dataclass
class Recommendation:
    market: str
    ticker: str
    company: str
    action: str
    confidence: float
    rationale: str

    def as_dict(self) -> dict[str, str | float]:
        return {
            "market": self.market,
            "ticker": self.ticker,
            "company": self.company,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
        }


@dataclass
class AgentContext:
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    watchlist: list[dict] = field(default_factory=list)
    news: dict[str, list[dict]] = field(default_factory=dict)

    def to_compact_dict(self) -> dict:
        verdicts_out = []
        for key, v in self.verdicts.items():
            verdicts_out.append(
                {
                    "market": v.market,
                    "ticker": v.ticker,
                    "verdict": v.verdict,
                    "confidence": round(v.confidence, 4),
                    "news_score": round(v.news_score, 4),
                    "price_score": round(v.price_score, 4),
                    "combined_score": round(v.combined_score, 4),
                    "reason": v.reason,
                }
            )
        return {
            "verdicts": verdicts_out,
            "watchlist": self.watchlist,
            "top_news": self.news,
        }


def _latest_news_map(db: Database, verdicts: dict[str, Verdict], limit: int = 5) -> dict[str, list[dict]]:
    news_map: dict[str, list[dict]] = {}
    for key, v in verdicts.items():
        items = db.recent_news(v.market, v.ticker, limit=limit)
        news_map[key] = [
            {"title": n["title"], "source": n["source"], "sentiment": n.get("sentiment_label", "")}
            for n in items
        ]
    return news_map


def _load_agent_context(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
    include_news: bool = True,
) -> AgentContext:
    db = Database(db_path or settings.db_path)
    db.init_schema()

    verdicts = run_verdicts(market_codes=market_codes, db_path=db_path)
    watchlist = db.watchlist()
    news: dict[str, list[dict]] = {}
    if include_news:
        news = _latest_news_map(db, verdicts)

    return AgentContext(verdicts=verdicts, watchlist=watchlist, news=news)


class TradingAgent:
    """Decides BUY/HOLD/SELL/AVOID using Gemini over orchestrated market data."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        if not self.api_key:
            raise RuntimeError(
                "TradingAgent requires GEMINI_API_KEY. Set it in the environment or config."
            )
        from google import genai  # type: ignore

        self._client = genai.Client(api_key=self.api_key)

    @staticmethod
    def _build_prompt(context: AgentContext) -> str:
        return (
            "You are a professional portfolio trading agent. Use ONLY the provided data. "
            "For every ticker in 'verdicts', decide an action:\n"
            "- BUY: strong positive signals (bullish news + positive price momentum + reasonable valuation)\n"
            "- HOLD: mixed signals or neutral\n"
            "- SELL: sustained negative signals\n"
            "- AVOID: clearly deteriorating fundamentals/sentiment\n\n"
            "Rules:\n"
            "1. Base decisions on the verdict, news_score, price_score, combined_score, and the reason text.\n"
            "2. Tickers in 'watchlist' are ones the user cares about - still decide on data, do not force BUY.\n"
            "3. Never recommend buying a ticker with strongly negative combined_score.\n"
            "4. Confidence is 0..1. Be decisive - avoid all-HOLD outputs.\n"
            "5. Return ONLY JSON: "
            '{"recommendations": [{"market": str, "ticker": str, "action": '
            '"BUY|HOLD|SELL|AVOID", "confidence": float, "rationale": str}]}\n\n'
            "DATA:\n"
            + json.dumps(context.to_compact_dict(), default=str)
        )

    def decide(self, market_codes: Iterable[str] | None = None, db_path: str | None = None) -> list[Recommendation]:
        context = _load_agent_context(market_codes=market_codes, db_path=db_path, include_news=True)
        return self.decide_context(context)

    def decide_context(self, context: AgentContext) -> list[Recommendation]:
        prompt = self._build_prompt(context)
        logger.info("Asking Gemini (%s) for trading recommendations…", self.model)
        response = self._client.models.generate_content(model=self.model, contents=prompt)
        text = (response.text or "").strip()
        return self._parse(text)

    def _parse(self, text: str) -> list[Recommendation]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise RuntimeError(f"Gemini returned no JSON:\n{text[:500]}")
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Could not parse Gemini response: {exc}\n{text[:500]}") from exc

        recs: list[Recommendation] = []
        for item in data.get("recommendations", []):
            action = str(item.get("action", "")).upper()
            if action not in ACTIONS:
                continue
            recs.append(
                Recommendation(
                    market=str(item.get("market", "")).upper(),
                    ticker=str(item.get("ticker", "")).upper(),
                    company=str(item.get("company", "")),
                    action=action,
                    confidence=max(0.0, min(1.0, float(item.get("confidence", 0.0)))),
                    rationale=str(item.get("rationale", "")),
                )
            )
        return recs


def run_agent(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
    *,
    persist: bool = True,
) -> list[Recommendation]:
    agent = TradingAgent()
    recs = agent.decide(market_codes=market_codes, db_path=db_path)
    if persist:
        db = Database(db_path or settings.db_path)
        db.init_schema()
        db.insert_recommendations([r.as_dict() for r in recs])
        logger.info("Persisted %d recommendations", len(recs))
    return recs
