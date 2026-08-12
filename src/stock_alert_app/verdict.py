from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .db import Database
from .price import PriceState
from .sentiment.aggregate import SourceSentiment

logger = logging.getLogger(__name__)

BULL = "BULL"
BEAR = "BEAR"
NEUTRAL = "NEUTRAL"

#: What the LSTM price model is actually predicting (see models.price_lstm horizon).
FORECAST_HORIZON = "1 trading day"

#: Signals that act as abstention (zero influence on agreement/opposition).
_NEUTRAL_EPS = 1e-6


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


@dataclass
class Verdict:
    market: str
    ticker: str
    verdict: str
    #: Final verdict confidence (agreement + distance from neutral), NOT the LSTM model confidence.
    confidence: float
    news_score: float
    #: Technical (price) signal score. Kept as ``price_score`` for DB/frontend compatibility.
    price_score: float
    combined_score: float
    reason: str = ""

    sentiment: SourceSentiment | None = None
    price: PriceState | None = None

    # ---- Multi-signal breakdown (all normalized to [-1, +1]) ----
    lstm_score: float = 0.0
    lstm_probability_up: float | None = None
    lstm_predicted_return: float | None = None
    lstm_confidence: float | None = None
    lstm_metrics: dict[str, float] = field(default_factory=dict)
    lstm_model_version: str = ""
    technical_score: float = 0.0
    technical_reasons: list[str] = field(default_factory=list)
    news_available: bool = False
    news_label: str | None = None
    forecast_horizon: str = FORECAST_HORIZON
    signal_agreement: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "market": self.market,
            "ticker": self.ticker,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "news_score": round(self.news_score, 4),
            "price_score": round(self.price_score, 4),
            "combined_score": round(self.combined_score, 4),
            "reason": [self.reason] if self.reason else [],
            "forecast_horizon": self.forecast_horizon,
            "signal_agreement": self.signal_agreement,
            "lstm": {
                "score": round(self.lstm_score, 4),
                "probability_up": (
                    round(float(self.lstm_probability_up), 4)
                    if self.lstm_probability_up is not None
                    else None
                ),
                "predicted_return": (
                    round(float(self.lstm_predicted_return), 6)
                    if self.lstm_predicted_return is not None
                    else None
                ),
                "model_confidence": (
                    round(float(self.lstm_confidence), 4)
                    if self.lstm_confidence is not None
                    else None
                ),
                "metrics": dict(self.lstm_metrics),
                "model_version": self.lstm_model_version,
            },
            "technical": {
                "score": round(self.technical_score, 4),
                "reasons": list(self.technical_reasons),
            },
            "news_available": self.news_available,
        }
        if self.sentiment is not None:
            d["news"] = self.sentiment.as_dict()
        if self.price is not None:
            d["price"] = self.price.as_dict()
        return d


# ---------------------------------------------------------------------------
# Signal normalization helpers
# ---------------------------------------------------------------------------


def normalize_lstm_signal(lstm_res: Any) -> tuple[float, str | None]:
    """Map an LSTM prediction to a normalized score in [-1, +1].

    Uses ``probability_up`` when available (``2 * (p - 0.5)``), then falls back
    to ``predicted_return`` and finally to the discrete ``signal``. Returns the
    provenance (``None`` if the output is unusable/NaN).
    """
    if lstm_res is None:
        return 0.0, None

    prob = getattr(lstm_res, "probability_up", None)
    if _is_finite(prob):
        return _clamp(2.0 * (float(prob) - 0.5)), "probability_up"

    ret = getattr(lstm_res, "predicted_return", None)
    if _is_finite(ret):
        # A ~+/-5% one-day return is treated as a full-strength signal.
        return _clamp(math.tanh(float(ret) * 20.0)), "predicted_return"

    signal = getattr(lstm_res, "signal", None)
    if signal in (BULL, BEAR):
        return (1.0 if signal == BULL else -1.0), "signal"

    return 0.0, None


def normalize_news_score(sentiment: SourceSentiment | None) -> tuple[float, bool]:
    """Normalize news sentiment into [-1, +1], flagging availability separately."""
    if sentiment is None:
        return 0.0, False
    score = getattr(sentiment, "score", None)
    if not _is_finite(score):
        return 0.0, False
    return _clamp(float(score)), True


def _normalize_price(price: PriceState | None) -> tuple[float, list[str]]:
    """Contribute explicit technical score + reasons.

    Uses the pre-existing indicator logic unchanged: 20-day momentum, RSI,
    price vs SMA-50, and SMA-50 vs SMA-200 trend. Returned score is in [-1, +1].
    """
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

    return _clamp(score), reasons


# ---------------------------------------------------------------------------
# Combination + decision
# ---------------------------------------------------------------------------


def _signal_weights() -> dict[str, float]:
    return {
        "lstm": settings.lstm_weight,
        "technical": settings.technical_weight,
        "news": settings.news_weight,
    }


def combine_signals(
    lstm_score: float,
    technical_score: float,
    news_score: float,
    lstm_available: bool,
    technical_available: bool,
    news_available: bool,
) -> float:
    """Weighted mean of the available signals, renormalized by available weight."""
    weights = _signal_weights()
    values = {
        "lstm": (lstm_score, lstm_available),
        "technical": (technical_score, technical_available),
        "news": (news_score, news_available),
    }
    total = sum(w for key, w in weights.items() if values[key][1])
    if total <= 0.0:
        return 0.0
    combined = (
        sum(weights[key] * values[key][0] for key in weights if values[key][1]) / total
    )
    return _clamp(combined)


def _decide(combined: float) -> tuple[str, float]:
    """Map a combined score to a verdict. Confidence here is distance-only;
    the richer verdict confidence is computed separately (see _verdict_confidence)."""
    combined = _clamp(combined)
    if combined >= settings.bull_threshold:
        return BULL, abs(combined)
    if combined <= settings.bear_threshold:
        return BEAR, abs(combined)
    return NEUTRAL, 0.0


def _verdict_confidence(
    combined: float,
    lstm_score: float,
    technical_score: float,
    news_score: float,
    lstm_available: bool,
    technical_available: bool,
    news_available: bool,
) -> float:
    """Final verdict confidence in [0, 1].

    Considers: (1) distance of the combined score from neutral, (2) agreement
    between available signals, (3) how many signals were available. Disagreement
    actively reduces confidence even if one signal is strong by itself.
    """
    combined = _clamp(combined)
    if abs(combined) < _NEUTRAL_EPS:
        return 0.0

    signals: list[float] = []
    if lstm_available:
        signals.append(lstm_score)
    if technical_available:
        signals.append(technical_score)
    if news_available:
        signals.append(news_score)

    if not signals:
        return round(max(0.0, min(1.0, abs(combined))), 4)

    n = len(signals)
    agree = opposing = 0
    for s in signals:
        if abs(s) < _NEUTRAL_EPS:
            continue  # abstains
        if (s > 0) == (combined > 0):
            agree += 1
        else:
            opposing += 1

    distance = abs(combined)
    agreement = agree / n
    availability = n / 3.0
    conf = 0.5 * distance + 0.3 * agreement + 0.2 * availability

    if opposing:
        conf *= 1.0 - 0.35 * (opposing / n)

    # A single (or sparse) uncorroborated signal cannot imply high confidence.
    availability_cap = 0.35 + 0.65 * availability
    conf = min(conf, availability_cap)

    return round(max(0.0, min(1.0, conf)), 4)


def _agreement_label(agree: int, opposing: int, available: bool) -> str:
    if not available:
        return "none"
    if opposing == 0 and agree > 0:
        return "strong"
    total = agree + opposing
    if total == 0:
        return "neutral"
    ratio = agree / total
    if ratio >= 0.7:
        return "moderate"
    if ratio >= 0.4:
        return "mixed"
    return "weak"


def _signal_label(score: float) -> str:
    if score > 0.05:
        return "bullish"
    if score < -0.05:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Central decision engine
# ---------------------------------------------------------------------------


def build_verdict(
    market: str,
    ticker: str,
    sentiment: SourceSentiment | None,
    price: PriceState | None,
    yahoo_symbol: str = "",
) -> Verdict:
    """Combine LSTM, technical, and news signals into one verdict.

    This is the single decision engine used by ``run_verdicts`` and
    ``live_verdict``. Missing signals degrade gracefully: the ener renormalizes
    across whatever signals are available and never crashes on NaN output.
    """
    from .models.price_lstm import predict_price_lstm

    # ---- Signal 1: LSTM price model ----
    lstm_res = None
    if yahoo_symbol:
        try:
            lstm_res = predict_price_lstm(yahoo_symbol)
        except Exception as exc:
            logger.warning("LSTM prediction failed for %s: %s", yahoo_symbol, exc)
            lstm_res = None

    lstm_score = 0.0
    lstm_prob: float | None = None
    lstm_ret: float | None = None
    lstm_conf: float | None = None
    lstm_available = False
    if lstm_res is not None:
        lstm_score, source = normalize_lstm_signal(lstm_res)
        lstm_available = source is not None
        if lstm_available:
            prob = getattr(lstm_res, "probability_up", None)
            ret = getattr(lstm_res, "predicted_return", None)
            mconf = getattr(lstm_res, "confidence", None)
            if _is_finite(prob):
                lstm_prob = round(float(prob), 4)
            if _is_finite(ret):
                lstm_ret = round(float(ret), 6)
            if _is_finite(mconf):
                lstm_conf = round(float(mconf), 4)
        else:
            logger.warning(
                "Invalid/NaN LSTM output for %s:%s; using technical+news signals",
                market,
                ticker,
            )

    # ---- Model explainability: pass out fit metrics + version ----
    lstm_metrics: dict[str, float] = {}
    lstm_version = ""
    if lstm_res is not None:
        lstm_version = str(getattr(lstm_res, "model_version", "") or "")
        for key in ("mse", "mae", "directional_accuracy"):
            val = getattr(lstm_res, key, None)
            # Prediction-path results leave these at 0.0 (never measured); only
            # surface metrics that were actually computed by a training run.
            if _is_finite(val) and float(val) != 0.0:
                lstm_metrics[key] = round(float(val), 6)

    # ---- Signal 2: Technical (price) ----
    technical_score, technical_reasons = _normalize_price(price)
    technical_available = price is not None

    # ---- Signal 3: News sentiment ----
    news_score, news_available = normalize_news_score(sentiment)

    combined = combine_signals(
        lstm_score,
        technical_score,
        news_score,
        lstm_available,
        technical_available,
        news_available,
    )
    verdict, _ = _decide(combined)

    # Agreement statistics (for confidence + explanation).
    agree = opposing = 0
    for s in (lstm_score, technical_score, news_score):
        if not _is_finite(s) or abs(s) < _NEUTRAL_EPS:
            continue
        if (s > 0) == (combined > 0):
            agree += 1
        else:
            opposing += 1
    any_signal = lstm_available or technical_available or news_available
    agreement = _agreement_label(agree, opposing, any_signal)

    verdict_confidence = _verdict_confidence(
        combined,
        lstm_score,
        technical_score,
        news_score,
        lstm_available,
        technical_available,
        news_available,
    )

    # ---- Explainability: build the reason string ----
    reason_parts = [f"Forecast horizon: {FORECAST_HORIZON}"]
    if lstm_available:
        prob_str = f"{lstm_prob:.1%}" if lstm_prob is not None else "n/a"
        ret_str = f"{lstm_ret:+.2%}" if lstm_ret is not None else "n/a"
        conf_str = f"{lstm_conf:.0%}" if lstm_conf is not None else "n/a"
        reason_parts.append(
            f"LSTM: {_signal_label(lstm_score)} signal (P(Up) {prob_str}, "
            f"predicted return {ret_str}, model confidence {conf_str})"
        )
    else:
        reason_parts.append("LSTM: unavailable (technical + news signals only)")
    reason_parts.append(
        f"Technical: {_signal_label(technical_score)} ({technical_score:+.2f})"
    )
    if news_available:
        reason_parts.append(
            f"News: {sentiment.label} ({sentiment.article_count} articles, "
            f"score {news_score:+.2f})"
        )
    else:
        reason_parts.append("News: unavailable")
    reason_parts.append(f"Signal agreement: {agreement}")
    reason_parts.append(f"Final score: {combined:+.2f}")
    reason_parts.append(f"Final verdict: {verdict}")

    return Verdict(
        market=market,
        ticker=ticker,
        verdict=verdict,
        confidence=verdict_confidence,
        news_score=news_score,
        price_score=technical_score,
        combined_score=round(combined, 4),
        reason="; ".join(reason_parts),
        sentiment=sentiment,
        price=price,
        lstm_score=round(lstm_score, 4),
        lstm_probability_up=lstm_prob,
        lstm_predicted_return=lstm_ret,
        lstm_confidence=lstm_conf,
        lstm_metrics=lstm_metrics,
        lstm_model_version=lstm_version,
        technical_score=round(technical_score, 4),
        technical_reasons=technical_reasons,
        news_available=news_available,
        news_label=sentiment.label if sentiment is not None else None,
        forecast_horizon=FORECAST_HORIZON,
        signal_agreement=agreement,
    )


# ---------------------------------------------------------------------------
# Batch / live entry points — both funnel through build_verdict
# ---------------------------------------------------------------------------


def run_verdicts(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
) -> dict[str, Verdict]:
    """Analyze every configured symbol through the single per-stock path.

    Delegates to ``live_verdict`` (which resolves + validates the symbol, fetches
    price + news, runs LSTM, and stores the verdict) so there is exactly one
    analysis engine instead of a parallel batch implementation.
    """
    from .ingest import _load_markets

    markets = _load_markets()
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else list(settings.default_markets)

    verdicts: dict[str, Verdict] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            continue
        for symbol in market.tickers:
            key = f"{code}:{symbol}"
            ticker_spec = market.get_ticker(symbol)
            verdict = live_verdict(code, symbol, ticker_spec.name, db_path=db_path)
            if verdict is not None:
                verdicts[key] = verdict
    return verdicts


def live_verdict(
    market_code: str,
    ticker: str,
    company: str = "",
    db_path: str | None = None,
    yahoo_symbol: str | None = None,
) -> Verdict | None:
    """Compute (and store) a verdict for an arbitrary ticker on demand.

    Used for watchlist items that may not be registered in the market config.
    Falls back to lexicon scoring if FinBERT is unavailable.

    ``yahoo_symbol`` optionally overrides the symbol used to fetch price/LSTM
    data (e.g. an exact provider symbol like ``BF.B`` or ``0700.HK`` resolved
    by the dynamic symbol discovery). When omitted the symbol is resolved
    through the symbol-resolution layer (which validates the symbol before any
    price/LSTM work and skips unavailable securities).
    """
    from .ingest import _load_markets
    from .price import build_price_state, fetch_history, store_price_state
    from .resolve import resolve_for_fetch, resolution, status_label
    from .sentiment.aggregate import aggregate_sentiment
    from .sentiment.scorers import default_scorer
    from .sources import fetch_google_news, fetch_yahoo_finance

    markets = _load_markets()
    market = markets.get(market_code)
    if not market:
        logger.warning("live_verdict: unknown market %s", market_code)
        return None

    db = Database(db_path or settings.db_path)
    db.init_schema()
    ticker = ticker.upper()

    price = None
    if yahoo_symbol:
        yahoo_symbol = yahoo_symbol.upper()
    else:
        yahoo_symbol = resolve_for_fetch(market.code, ticker, company)
        if not yahoo_symbol:
            detail = resolution(market.code, ticker, company)
            logger.info(
                "live_verdict: skipping %s:%s (%s)",
                market.code,
                ticker,
                status_label(str(detail.get("status"))),
            )
            return None
    try:
        df = fetch_history(yahoo_symbol, period="6mo")
        price = build_price_state(market.code, ticker, df)
        if price:
            store_price_state(db, price)
    except Exception as exc:
        logger.warning("live_verdict: no price data for %s: %s", yahoo_symbol, exc)

    sentiment = None
    query = f"{company or ticker} stock"
    try:
        articles = fetch_google_news(query, market.country)
    except Exception as exc:
        logger.warning("live_verdict: news fetch failed for %s: %s", ticker, exc)
        articles = []
    if not articles:
        # Fall back to an existing second provider so a transient Google News
        # outage does not silently turn every stock into "no news".
        try:
            articles = fetch_yahoo_finance(yahoo_symbol, region=market.country, query=query)
        except Exception as exc:
            logger.warning("live_verdict: yahoo news fallback failed for %s: %s", ticker, exc)
            articles = []

    if articles:
        scorer = default_scorer()
        scores: list = []
        for art in articles:
            text = f"{art.title} {art.summary}".strip()
            if not text:
                continue
            try:
                result = scorer.score(text)
            except Exception:
                continue
            scores.append((result, art.source, art.published_at))
            # Persist the article + its per-article sentiment so the News tab can
            # show the exact evidence that contributed to the aggregate score.
            try:
                item_id = db.insert_news_item(
                    market=market.code,
                    ticker=ticker,
                    title=art.title,
                    url=art.url,
                    source=art.source,
                    summary=art.summary,
                    published_at=art.published_at,
                )
                if item_id is None:
                    item_id = db.find_news_item_id(market.code, ticker, art.url)
                if item_id is not None:
                    db.insert_sentiment(
                        news_item_id=item_id,
                        model=scorer.name,
                        score=result.score,
                        label=result.label,
                        positive=result.positive,
                        negative=result.negative,
                        neutral=result.neutral,
                    )
            except Exception:
                pass
        if scores:
            sentiment = aggregate_sentiment(scores)

    verdict = build_verdict(
        market.code, ticker, sentiment, price, yahoo_symbol=yahoo_symbol
    )
    db.insert_verdict(
        market=verdict.market,
        ticker=verdict.ticker,
        verdict=verdict.verdict,
        confidence=verdict.confidence,
        news_score=verdict.news_score,
        price_score=verdict.price_score,
        combined_score=verdict.combined_score,
        reason=verdict.reason,
        lstm_score=verdict.lstm_score,
        lstm_probability_up=verdict.lstm_probability_up,
        lstm_predicted_return=verdict.lstm_predicted_return,
        lstm_confidence=verdict.lstm_confidence,
        technical_score=verdict.technical_score,
    )
    logger.info("live_verdict: %s:%s -> %s", market.code, ticker, verdict.verdict)
    return verdict
