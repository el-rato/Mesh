from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import settings
from .db import Database
from .markets import Market
from .price import PriceState
from .sentiment.aggregate import SourceSentiment

logger = logging.getLogger(__name__)

BULL = "BULL"
BEAR = "BEAR"
NEUTRAL = "NEUTRAL"


@dataclass
class Verdict:
    market: str
    ticker: str
    verdict: str
    confidence: float
    news_score: float
    price_score: float
    combined_score: float
    reason: list[str]
    sentiment: SourceSentiment | None = None
    price: PriceState | None = None

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "market": self.market,
            "ticker": self.ticker,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "news_score": round(self.news_score, 4),
            "price_score": round(self.price_score, 4),
            "combined_score": round(self.combined_score, 4),
            "reason": self.reason,
        }
        if self.sentiment is not None:
            d["news"] = self.sentiment.as_dict()
        if self.price is not None:
            d["price"] = self.price.as_dict()
        return d


def _normalize_price(price: PriceState | None) -> tuple[float, list[str]]:
    if price is None:
        return 0.0, ["no price data available (news-only)"]

    score = 0.0
    reasons: list[str] = []

    mom = max(-0.35, min(0.35, price.momentum_20))
    score += mom
    if price.momentum_20 > 0.05:
        reasons.append(f"20d momentum +{price.momentum_20:.1%}")
    elif price.momentum_20 < -0.05:
        reasons.append(f"20d momentum {price.momentum_20:.1%}")

    if price.rsi_14 >= 70:
        score -= 0.15
        reasons.append(f"RSI overbought ({price.rsi_14:.0f})")
    elif price.rsi_14 <= 30:
        score += 0.10
        reasons.append(f"RSI oversold ({price.rsi_14:.0f})")

    if price.price_above_sma_50:
        score += 0.15
        reasons.append("price above 50-day MA")
    else:
        score -= 0.15
        reasons.append("price below 50-day MA")

    if price.sma_200:
        if price.trend_50_200 > 0:
            score += 0.10
            reasons.append("50d above 200d (uptrend)")
        else:
            score -= 0.10
            reasons.append("50d below 200d (downtrend)")

    return max(-1.0, min(1.0, score)), reasons


def _decide(combined: float) -> tuple[str, float]:
    bull_th = settings.bull_threshold
    bear_th = settings.bear_threshold
    if combined > bull_th:
        conf = min(1.0, (combined - bull_th) / (1.0 - bull_th))
        return BULL, round(conf, 4)
    if combined < bear_th:
        conf = min(1.0, (bear_th - combined) / (1.0 + bear_th))
        return BEAR, round(conf, 4)
    return NEUTRAL, 0.0


def build_verdict(
    market: str,
    ticker: str,
    sentiment: SourceSentiment | None,
    price: PriceState | None,
) -> Verdict:
    news_score = sentiment.score if sentiment else 0.0
    price_score, price_reasons = _normalize_price(price)

    price_present = price is not None
    if price_present:
        combined = settings.news_weight * news_score + settings.price_weight * price_score
    else:
        combined = news_score

    verdict, confidence = _decide(combined)

    news_desc = (
        f"{sentiment.label} ({sentiment.article_count} articles, score {news_score:+.3f})"
        if sentiment
        else "no sentiment data"
    )
    reason_parts = [f"news: {news_desc}"]
    reason_parts.extend(price_reasons)
    reason = "; ".join(reason_parts)

    return Verdict(
        market=market,
        ticker=ticker,
        verdict=verdict,
        confidence=confidence,
        news_score=news_score,
        price_score=price_score,
        combined_score=round(combined, 4),
        reason=reason,
        sentiment=sentiment,
        price=price,
    )


def run_verdicts(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
    prefer_finbert: bool = True,
) -> dict[str, Verdict]:
    from .ingest import _load_markets
    from .price import run_price_fetch
    from .sentiment.pipeline import run_sentiment

    markets = _load_markets()
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else list(settings.default_markets)

    prices = run_price_fetch(market_codes=codes, db_path=db_path)
    result = run_sentiment(db_path=db_path, prefer_finbert=prefer_finbert)
    sentiments = result.headlines

    verdicts: dict[str, Verdict] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            continue
        for symbol in market.tickers:
            key = f"{code}:{symbol}"
            sentiment = sentiments.get(key)
            price = prices.get(symbol)
            verdicts[key] = build_verdict(code, symbol, sentiment, price)
            db.insert_verdict(
                market=code,
                ticker=symbol,
                verdict=verdicts[key].verdict,
                confidence=verdicts[key].confidence,
                news_score=verdicts[key].news_score,
                price_score=verdicts[key].price_score,
                combined_score=verdicts[key].combined_score,
                reason=verdicts[key].reason,
            )
    return verdicts