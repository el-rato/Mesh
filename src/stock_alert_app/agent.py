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
class StockAnalysis:
    market: str
    ticker: str
    company: str
    action: str
    confidence: float
    summary: str
    key_points: list[str]
    risks: list[str]
    catalysts: list[str]

    def as_dict(self) -> dict[str, str | float | list[str]]:
        return {
            "market": self.market,
            "ticker": self.ticker,
            "company": self.company,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "summary": self.summary,
            "key_points": self.key_points,
            "risks": self.risks,
            "catalysts": self.catalysts,
        }


@dataclass
class AgentContext:
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    watchlist: list[dict] = field(default_factory=list)
    news: dict[str, list[dict]] = field(default_factory=dict)

    def to_compact_dict(self, max_characters: int = 180_000) -> dict:
        """Compact context into a dict, truncating to stay within a token budget.

        Rough heuristic: ~4 chars per token, so max_characters keeps the data well
        under the model's context limit (e.g. 1M tokens for Gemini 3.x).
        """
        def _trunc(s: str, limit: int) -> str:
            return s if len(s) <= limit else s[:limit].rstrip() + "…"

        verdicts_out = []
        total = 0
        for key, v in self.verdicts.items():
            entry = {
                "market": v.market,
                "ticker": v.ticker,
                "verdict": v.verdict,
                "confidence": round(v.confidence, 4),
                "news_score": round(v.news_score, 4),
                "price_score": round(v.price_score, 4),
                "combined_score": round(v.combined_score, 4),
                "reason": _trunc(str(v.reason), 300),
            }
            total += sum(len(str(x)) for x in entry.values())
            if total > max_characters:
                break
            verdicts_out.append(entry)

        news_out: dict[str, list[dict]] = {}
        for key, items in self.news.items():
            kept = []
            for n in items:
                capped = {
                    "title": _trunc(str(n.get("title", "")), 200),
                    "source": str(n.get("source", ""))[:50],
                    "sentiment": str(n.get("sentiment", "")),
                }
                total += sum(len(str(x)) for x in capped.values())
                if total > max_characters:
                    break
                kept.append(capped)
            news_out[key] = kept

        return {
            "verdicts": verdicts_out,
            "watchlist": self.watchlist,
            "top_news": news_out,
        }


def _latest_news_map(db: Database, verdicts: dict[str, Verdict], limit: int = 3) -> dict[str, list[dict]]:
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
    """Decides BUY/HOLD/SELL/AVOID using an LLM over orchestrated market data."""

    def __init__(
        self,
        provider: str = "gemini",
        api_key: str | None = None,
        model: str | None = None,
        ollama_base_url: str | None = None,
    ) -> None:
        self.provider = provider.lower()
        if self.provider == "gemini":
            self.api_key = api_key or settings.gemini_api_key
            self.model = model or settings.gemini_model
            if not self.api_key:
                raise RuntimeError("TradingAgent requires GEMINI_API_KEY for Gemini provider.")
            from google import genai  # type: ignore
            self._client = genai.Client(api_key=self.api_key)
        elif self.provider == "ollama":
            self.ollama_base_url = ollama_base_url or settings.ollama_base_url
            self.model = model or settings.ollama_model
            self._client = None  # Not needed for Ollama
        else:
            raise ValueError(f"Unknown provider: {provider}. Use 'gemini' or 'ollama'.")

    @staticmethod
    def _build_prompt(context: AgentContext, max_tokens: int = 40_000) -> str:
        prompt_head = (
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
        )
        # ~4 chars per token; cap the data payload so the whole prompt stays under max_tokens.
        budget_chars = max_tokens * 4 - len(prompt_head)
        payload = json.dumps(context.to_compact_dict(max_characters=budget_chars), default=str)
        return prompt_head + payload

    def decide_context(self, context: AgentContext) -> list[Recommendation]:
        prompt = self._build_prompt(context)
        logger.info("Asking %s (%s) for trading recommendations…", self.provider, self.model)

        if self.provider == "gemini":
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            text = (response.text or "").strip()
        elif self.provider == "ollama":
            text = self._call_ollama(prompt)
        else:
            raise RuntimeError(f"Unknown provider: {self.provider}")

        return self._parse(text)

    def decide(self, market_codes: Iterable[str] | None = None, db_path: str | None = None) -> list[Recommendation]:
        context = _load_agent_context(market_codes=market_codes, db_path=db_path, include_news=True)
        return self.decide_context(context)

    def _call_ollama(self, prompt: str) -> str:
        import json
        import urllib.request

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 2000},
        }

        req = urllib.request.Request(
            f"{self.ollama_base_url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())

        return (data.get("response", "") or "").strip()

    def _parse(self, text: str) -> list[Recommendation]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise RuntimeError(f"{self.provider} returned no JSON:\n{text[:500]}")
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Could not parse {self.provider} response: {exc}\n{text[:500]}") from exc

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

    def analyze_ticker(
        self,
        market_code: str,
        ticker: str,
        company: str = "",
        db_path: str | None = None,
    ) -> StockAnalysis:
        """Deep-dive Gemini analysis for a single, user-selected ticker."""
        from .verdict import live_verdict

        db = Database(db_path or settings.db_path)
        db.init_schema()
        v = live_verdict(market_code, ticker, company, db_path=db_path)
        if v is None:
            raise RuntimeError(f"Could not build a verdict for {market_code}:{ticker}")
        ticker = v.ticker

        headlines = [
            {"title": n["title"], "source": n["source"], "sentiment": n.get("sentiment_label", "")}
            for n in db.recent_news(v.market, ticker, limit=10)
        ]

        data_payload = {
            "verdict": {
                "market": v.market,
                "ticker": v.ticker,
                "company": company or "",
                "verdict": v.verdict,
                "confidence": v.confidence,
                "news_score": v.news_score,
                "price_score": v.price_score,
                "combined_score": v.combined_score,
                "reason": v.reason,
            },
            "price": v.price.as_dict() if v.price else None,
            "news": headlines,
        }

        prompt = (
            f"You are a senior equity analyst. Analyze {ticker} ({company or market_code}) "
            "using ONLY the data below.\n"
            "Return ONLY JSON with this exact schema:\n"
            '{"action": "BUY|HOLD|SELL|AVOID", "confidence": float 0..1, '
            '"summary": str (2-4 sentences), "key_points": [str], "risks": [str], '
            '"catalysts": [str]}\n\n'
            "Guidance: the system verdict combines news sentiment and price momentum. "
            "BUY when signals are strongly positive, AVOID when clearly deteriorating, "
            "otherwise HOLD/SELL. Be decisive but grounded in the provided numbers.\n\n"
            "DATA:\n" + json.dumps(data_payload, default=str)
        )

        logger.info("Asking %s (%s) to analyze %s:%s…", self.provider, self.model, market_code, ticker)
        if self.provider == "gemini":
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            text = (response.text or "").strip()
        elif self.provider == "ollama":
            text = self._call_ollama(prompt)
        else:
            raise RuntimeError(f"Unknown provider: {self.provider}")
        return self._parse_analysis(text, market_code, ticker, company)

    def _parse_analysis(self, text: str, market: str, ticker: str, company: str) -> StockAnalysis:
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

        action = str(data.get("action", "HOLD")).upper()
        if action not in ACTIONS:
            action = "HOLD"
        return StockAnalysis(
            market=market.upper(),
            ticker=ticker.upper(),
            company=company,
            action=action,
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
            summary=str(data.get("summary", "")),
            key_points=[str(x) for x in data.get("key_points", [])],
            risks=[str(x) for x in data.get("risks", [])],
            catalysts=[str(x) for x in data.get("catalysts", [])],
        )


def run_agent(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
    *,
    persist: bool = True,
    provider: str = "gemini",
    model: str | None = None,
    ollama_base_url: str | None = None,
) -> list[Recommendation]:
    agent = TradingAgent(provider=provider, model=model, ollama_base_url=ollama_base_url)
    recs = agent.decide(market_codes=market_codes, db_path=db_path)
    if persist:
        db = Database(db_path or settings.db_path)
        db.init_schema()
        db.insert_recommendations([r.as_dict() for r in recs])
        logger.info("Persisted %d recommendations", len(recs))
    return recs


def run_agent_analysis(
    market_code: str,
    ticker: str,
    company: str = "",
    db_path: str | None = None,
    provider: str = "gemini",
    model: str | None = None,
    ollama_base_url: str | None = None,
) -> StockAnalysis:
    agent = TradingAgent(provider=provider, model=model, ollama_base_url=ollama_base_url)
    return agent.analyze_ticker(market_code, ticker, company, db_path=db_path)
