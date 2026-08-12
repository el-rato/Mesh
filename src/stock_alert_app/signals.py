"""Model-agnostic Signal Engine.

The quantitative layer is a generic ensemble of models, each producing a
``SignalResult``. The Investment Committee consumes these generic signals
instead of LSTM-specific objects.

Models:
* ``lstm``        — existing price LSTM (models.price_lstm)
* ``gbm``         — lightweight gradient-boosted decision-stump ensemble (numpy)
* ``momentum``    — momentum/trend model from price/technical state

A model that fails or has no data returns ``status`` NO_DATA/ERROR and is
excluded from the ensemble. No unavailable model is treated as bullish,
bearish, or neutral.

Also provides the Social Momentum signal (Reddit API) and a Market Regime
signal (benchmark index trend), both returning NO_DATA when the data is not
available rather than fabricating a value.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

OK = "ok"
NO_DATA = "no_data"
ERROR = "error"

_NEUTRAL_BAND = 0.05


@dataclass
class SignalResult:
    model_name: str
    direction: str | None = None
    score: float | None = None
    confidence: float | None = None
    prediction: float | None = None
    status: str = NO_DATA
    analyzed_at: str = ""
    explanation: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "direction": self.direction,
            "score": self.score,
            "confidence": self.confidence,
            "prediction": self.prediction,
            "status": self.status,
            "analyzed_at": self.analyzed_at,
            "explanation": list(self.explanation),
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def direction_of(score: float | None) -> str | None:
    if score is None:
        return None
    if score > _NEUTRAL_BAND:
        return "BULL"
    if score < -_NEUTRAL_BAND:
        return "BEAR"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Quantitative models
# ---------------------------------------------------------------------------


def lstm_signal(symbol: str) -> SignalResult:
    """LSTM price model (one model inside the quantitative ensemble)."""
    from .models.price_lstm import predict_price_lstm

    analyzed_at = _now_iso()
    try:
        result = predict_price_lstm(symbol)
    except Exception as exc:
        logger.warning("LSTM model failed for %s: %s", symbol, exc)
        return SignalResult("lstm", status=ERROR, analyzed_at=analyzed_at, explanation=[f"LSTM failed: {exc}"])
    if result is None:
        return SignalResult("lstm", status=NO_DATA, analyzed_at=analyzed_at, explanation=["LSTM produced no prediction"])
    prob = getattr(result, "probability_up", None)
    ret = getattr(result, "predicted_return", None)
    conf = getattr(result, "confidence", None)
    if _finite(prob):
        score = _clamp(2.0 * (float(prob) - 0.5))
    elif _finite(ret):
        score = _clamp(math.tanh(float(ret) * 20.0))
    else:
        score = 0.0
    explanation = []
    if _finite(prob):
        explanation.append(f"P(up) {float(prob):.1%}")
    if _finite(ret):
        explanation.append(f"predicted return {float(ret):+.2%}")
    return SignalResult(
        "lstm",
        direction_of(score),
        round(score, 4),
        round(float(conf), 4) if _finite(conf) else None,
        float(ret) if _finite(ret) else None,
        OK,
        analyzed_at,
        explanation,
    )


def _mom(series, lag: int, idx: int) -> float:
    if idx < lag:
        return 0.0
    base = abs(series[idx - lag]) + 1e-9
    return (series[idx] - series[idx - lag]) / base


def _stump_predict(x, stump, lr):
    _, f, thr, left, right = stump
    return lr * (left if x[f] <= thr else right)


def gbm_signal(df: Any) -> SignalResult:
    """Gradient-boosted decision-stump ensemble over momentum features.

    Trains a small set of shallow trees on the symbol's own recent OHLCV to
    predict next-day direction. Lightweight (numpy only) and deterministic.
    """
    try:
        import numpy as np
    except ImportError:
        return SignalResult("gbm", status=NO_DATA, analyzed_at=_now_iso(), explanation=["numpy unavailable"])
    analyzed_at = _now_iso()
    if df is None or getattr(df, "empty", True) or len(df) < 60:
        return SignalResult("gbm", status=NO_DATA, analyzed_at=analyzed_at, explanation=["insufficient history"])
    closes = df["Close"].astype(float).to_numpy()
    if not np.all(np.isfinite(closes)) or len(closes) < 60:
        return SignalResult("gbm", status=NO_DATA, analyzed_at=analyzed_at, explanation=["invalid history"])

    n = len(closes)
    feats = [_mom(closes, lag, i) for lag in (1, 5, 10, 20) for i in range(n)]
    X = np.column_stack(
        [
            np.asarray(feats[0:n]),
            np.asarray(feats[n : 2 * n]),
            np.asarray(feats[2 * n : 3 * n]),
            np.asarray(feats[3 * n : 4 * n]),
        ]
    )
    ret1 = np.diff(closes, prepend=closes[0]) / (np.abs(closes) + 1e-9)
    X = np.column_stack([X, np.convolve(np.abs(ret1), np.ones(10) / 10, mode="same")])
    y = np.sign(np.diff(closes, prepend=closes[0]))  # next-day sign (aligned)

    train_end = n - 30
    if train_end < 30:
        return SignalResult("gbm", status=NO_DATA, analyzed_at=analyzed_at, explanation=["insufficient history"])
    Xtr, ytr = X[:train_end], y[1 : train_end + 1]

    lr = 0.1
    trees: list = []
    residual = ytr.astype(float) - float(np.mean(ytr))
    prev_err = None
    for _ in range(24):
        best = None
        for f in range(Xtr.shape[1]):
            order = np.argsort(Xtr[:, f])
            xs = Xtr[order, f]
            ys = residual[order]
            cum = np.cumsum(ys)
            cum2 = np.cumsum(ys * ys)
            total = cum[-1]
            total2 = cum2[-1]
            for i in range(1, len(ys)):
                lc, rc = i, len(ys) - i
                ls = cum[i - 1]
                rs = total - ls
                lss = cum2[i - 1]
                rss = total2 - lss
                err = (lss - ls * ls / lc) + (rss - rs * rs / rc)
                if best is None or err < best[0]:
                    best = (err, f, (xs[i - 1] + xs[i]) / 2, ls / lc, rs / rc)
        if best is None:
            break
        trees.append(best)
        if prev_err is not None and best[0] >= prev_err:
            break
        prev_err = best[0]
        residual = residual - np.array([_stump_predict(x, best, lr) for x in Xtr])

    # directional accuracy on the held-out tail
    if not trees:
        return SignalResult("gbm", status=NO_DATA, analyzed_at=analyzed_at, explanation=["could not train"])
    preds = np.array([sum(_stump_predict(x, t, 1.0) for t in trees) for x in X[train_end:]])
    tail = y[train_end + 1 :]
    acc = float(np.mean(np.sign(preds[: len(tail)]) == tail)) if len(tail) else 0.5

    latest = sum(_stump_predict(X[-1], t, 1.0) for t in trees)
    score = _clamp(latest * 2.0)
    conf = min(1.0, 0.35 + 0.65 * max(0.0, abs(acc - 0.5) * 2.0))
    return SignalResult(
        "gbm",
        direction_of(score),
        round(score, 4),
        round(conf, 4),
        round(latest, 6),
        OK,
        analyzed_at,
        explanation=[f"directional accuracy {acc:.0%}"],
    )


def momentum_signal(price: dict[str, Any] | None) -> SignalResult:
    """Momentum/trend model computed from the technical/price state."""
    analyzed_at = _now_iso()
    if not price:
        return SignalResult("momentum", status=NO_DATA, analyzed_at=analyzed_at, explanation=["no price state"])
    score = 0.0
    if _finite(price.get("momentum_20")):
        score += _clamp(float(price["momentum_20"]) * 2.0)
    if _finite(price.get("trend_50_200")):
        score += _clamp(float(price["trend_50_200"]) * 2.0)
    if _finite(price.get("rsi_14")):
        rsi = float(price["rsi_14"])
        if rsi >= 70:
            score -= 0.2
        elif rsi <= 30:
            score += 0.2
    if price.get("above_sma_50") is True:
        score += 0.2
    elif price.get("above_sma_50") is False:
        score -= 0.2
    score = _clamp(score)
    explanation = []
    if _finite(price.get("momentum_20")):
        explanation.append(f"20d momentum {float(price['momentum_20']):+.1%}")
    if _finite(price.get("rsi_14")):
        explanation.append(f"RSI {float(price['rsi_14']):.0f}")
    return SignalResult(
        "momentum",
        direction_of(score),
        round(score, 4),
        round(min(1.0, abs(score) + 0.3), 4),
        None,
        OK,
        analyzed_at,
        explanation,
    )


def quantitative_ensemble(
    yahoo_symbol: str,
    price: dict[str, Any] | None = None,
    history_df: Any = None,
    weights: dict[str, float] | None = None,
) -> tuple[SignalResult, list[SignalResult]]:
    """Run the quantitative models and aggregate the available ones.

    Returns ``(ensemble, models)``. The ensemble is a confidence-weighted mean
    (``score * confidence * weight`` normalised by available weight). Missing
    models are excluded, never treated as a directional vote.
    """
    models = [
        lstm_signal(yahoo_symbol),
        gbm_signal(history_df),
        momentum_signal(price),
    ]
    w = weights or {
        "lstm": settings.model_weight_lstm,
        "gbm": settings.model_weight_gbm,
        "momentum": settings.model_weight_momentum,
    }
    numerator = 0.0
    denominator = 0.0
    for signal in models:
        if signal.status != OK or signal.score is None or signal.confidence is None:
            continue
        weight = w.get(signal.model_name, 0.0)
        effective = signal.confidence * weight
        numerator += signal.score * effective
        denominator += effective
    analyzed_at = _now_iso()
    if denominator <= 0:
        ensemble = SignalResult(
            "quantitative_ensemble", status=NO_DATA, analyzed_at=analyzed_at,
            explanation=["no quantitative model available"],
        )
    else:
        score = _clamp(numerator / denominator)
        available = [s for s in models if s.status == OK and s.confidence is not None]
        total_w = sum(w.get(s.model_name, 0.0) for s in available) or 1.0
        conf = sum(s.confidence * w.get(s.model_name, 0.0) for s in available) / total_w
        ensemble = SignalResult(
            "quantitative_ensemble",
            direction_of(score),
            round(score, 4),
            round(min(1.0, conf), 4),
            None,
            OK,
            analyzed_at,
            explanation=[f"{len(available)}/{len(models)} models available"],
        )
    return ensemble, models


# ---------------------------------------------------------------------------
# Social Momentum (Reddit API, official and approved)
# ---------------------------------------------------------------------------

_SOCIAL_SUBREDDITS = ["wallstreetbets", "stocks", "investing"]

#: Bounded TTL cache + per-key in-flight locks for the (expensive) social fetch,
#: mirroring the market-regime cache pattern but with a max entry count and safe
#: FIFO eviction so memory cannot grow without bound.
_SOCIAL_MAX = 512
_social_cache: dict[str, tuple[float, SignalResult]] = {}
_social_locks: dict[str, threading.Lock] = {}
_social_lock_users: dict[str, int] = {}
_social_lock_guard = threading.Lock()


def _social_evict(now: float) -> None:
    """Remove expired entries and enforce max sizes (idle locks only)."""
    for key in [k for k, (ts, _) in list(_social_cache.items()) if now - ts >= settings.social_cache_ttl]:
        _social_cache.pop(key, None)
    while len(_social_cache) > _SOCIAL_MAX:
        _social_cache.pop(next(iter(_social_cache)), None)
    if len(_social_locks) > _SOCIAL_MAX:
        for key in list(_social_locks):
            if _social_lock_users.get(key, 0) == 0:
                _social_locks.pop(key, None)
                _social_lock_users.pop(key, None)
                if len(_social_locks) <= _SOCIAL_MAX:
                    break


def _social_lock(key: str) -> threading.Lock:
    with _social_lock_guard:
        _social_evict(time.time())
        lock = _social_locks.setdefault(key, threading.Lock())
        _social_lock_users[key] = _social_lock_users.get(key, 0) + 1
        return lock


def _social_unlock(key: str) -> None:
    with _social_lock_guard:
        users = _social_lock_users.get(key, 0) - 1
        if users > 0:
            _social_lock_users[key] = users
        else:
            _social_lock_users.pop(key, None)


def clear_social_cache() -> None:
    """Clear the social momentum cache (mainly for tests / forced refresh)."""
    with _social_lock_guard:
        _social_cache.clear()


def social_momentum_signal(ticker: str, company: str = "") -> SignalResult:
    """TTL-cached social momentum (cache hit) with a per-key in-flight guard
    (prevents duplicate concurrent requests for the same security).

    Only OK and NO_DATA results are cached; transient ERROR results are not
    cached so they can be retried. NO_DATA is never coerced to NEUTRAL.
    """
    key = f"{ticker.upper()}:{company.upper()}"
    now = time.time()
    hit = _social_cache.get(key)
    if hit and now - hit[0] < settings.social_cache_ttl:
        return hit[1]
    lock = _social_lock(key)
    try:
        with lock:
            hit = _social_cache.get(key)
            if hit and now - hit[0] < settings.social_cache_ttl:
                return hit[1]
            result = _social_momentum_impl(ticker, company)
            if result.status in (OK, NO_DATA):
                with _social_lock_guard:
                    _social_cache[key] = (time.time(), result)
                    _social_evict(time.time())
            return result
    finally:
        _social_unlock(key)


def _social_momentum_impl(ticker: str, company: str = "") -> SignalResult:
    """Social momentum from public Reddit discussion via the official API.

    Measures CHANGE in attention: mention velocity, engagement velocity,
    sentiment, sentiment change, and source diversity. Returns NO_DATA when
    credentials are not configured, the provider fails, or nothing is found.
    """
    analyzed_at = _now_iso()
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return SignalResult("social_momentum", status=NO_DATA, analyzed_at=analyzed_at, explanation=["Reddit API not configured"])
    try:
        from .reddit_scanner import RedditScanner

        scanner = RedditScanner()
        reddit = scanner._get_reddit()
        if reddit is None:
            return SignalResult("social_momentum", status=ERROR, analyzed_at=analyzed_at, explanation=["Reddit client unavailable"])
        query = ticker if ticker.isalpha() else (company or ticker)
        now = time.time()
        recent: list[dict[str, Any]] = []
        prior: list[dict[str, Any]] = []
        import prawcore

        for sub_name in _SOCIAL_SUBREDDITS:
            try:
                for submission in reddit.subreddit(sub_name).search(query, sort="new", time_filter="week", limit=25):
                    text = f"{submission.title} {submission.selftext or ''}"
                    if query.upper() not in text.upper() and ticker.upper() not in text.upper():
                        continue
                    record = {
                        "created": float(submission.created_utc),
                        "engagement": float(submission.score) + float(submission.num_comments),
                        "sentiment": scanner._score_sentiment(text)[0],
                        "subreddit": sub_name,
                    }
                    if record["created"] >= now - 24 * 3600:
                        recent.append(record)
                    elif record["created"] >= now - 72 * 3600:
                        prior.append(record)
            except (prawcore.exceptions.PrawcoreException, Exception):
                continue
    except Exception as exc:
        logger.warning("Social momentum failed for %s: %s", ticker, exc)
        return SignalResult("social_momentum", status=ERROR, analyzed_at=analyzed_at, explanation=[f"social fetch failed: {exc}"])

    if not recent and not prior:
        return SignalResult("social_momentum", status=NO_DATA, analyzed_at=analyzed_at, explanation=["no social discussion found"])

    def _stats(records: list[dict]) -> dict:
        if not records:
            return {"mentions": 0, "engagement": 0.0, "sentiment": 0.0, "sources": 0}
        return {
            "mentions": len(records),
            "engagement": sum(r["engagement"] for r in records) / len(records),
            "sentiment": sum(r["sentiment"] for r in records) / len(records),
            "sources": len({r["subreddit"] for r in records}),
        }

    rec, pri = _stats(recent), _stats(prior)
    total_mentions = rec["mentions"] + pri["mentions"]
    mention_change = (rec["mentions"] - pri["mentions"]) / (total_mentions + 1e-6)
    eng_denom = rec["engagement"] + pri["engagement"] + 1e-6
    engagement_change = (rec["engagement"] - pri["engagement"]) / eng_denom
    sentiment_change = rec["sentiment"] - pri["sentiment"]
    diversity = min(1.0, rec["sources"] / 3.0)

    score = _clamp(
        0.3 * mention_change
        + 0.2 * engagement_change
        + 0.3 * rec["sentiment"]
        + 0.2 * sentiment_change
        + 0.05 * (diversity - 0.5)
    )
    confidence = min(1.0, total_mentions / 10.0 + 0.2)
    explanation = [
        f"mentions recent {rec['mentions']} vs prior {pri['mentions']}",
        f"sentiment {rec['sentiment']:+.2f} (change {sentiment_change:+.2f})",
        f"sources {rec['sources']} subreddits",
    ]
    return SignalResult(
        "social_momentum",
        direction_of(score),
        round(score, 4),
        round(confidence, 4),
        None,
        OK,
        analyzed_at,
        explanation,
    )


# ---------------------------------------------------------------------------
# Market Regime (benchmark index trend)
# ---------------------------------------------------------------------------

_regime_cache: dict[str, tuple[float, SignalResult]] = {}
_REGIME_TTL = 15 * 60


def market_regime_signal(market_code: str) -> SignalResult:
    """Simple regime from the market's primary benchmark index trend (real data)."""
    analyzed_at = _now_iso()
    now = time.time()
    cached = _regime_cache.get(market_code)
    if cached and now - cached[0] < _REGIME_TTL:
        return cached[1]
    try:
        from .indexes import MARKET_INDEXES, index_history

        indexes = MARKET_INDEXES.get(market_code, [])
        if not indexes:
            return SignalResult("market_regime", status=NO_DATA, analyzed_at=analyzed_at, explanation=["no benchmark configured"])
        rows = index_history(indexes[0]["symbol"], "6mo")
        closes = [r["close"] for r in rows if r.get("close") is not None]
        if len(closes) < 60:
            result = SignalResult("market_regime", status=NO_DATA, analyzed_at=analyzed_at, explanation=["insufficient index history"])
            _regime_cache[market_code] = (now, result)
            return result
        last = closes[-1]
        sma50 = sum(closes[-50:]) / 50.0
        sma200 = sum(closes[-200:]) / 200.0 if len(closes) >= 200 else sma50
        mom20 = (closes[-1] - closes[-21]) / (abs(closes[-21]) + 1e-9) if len(closes) > 21 else 0.0
        score = 0.0
        explanation = []
        if last > sma50:
            score += 0.4
            explanation.append("index above 50d")
        else:
            score -= 0.4
            explanation.append("index below 50d")
        if sma50 > sma200:
            score += 0.3
            explanation.append("uptrend (50>200)")
        else:
            score -= 0.3
            explanation.append("downtrend (50<200)")
        score = _clamp(score + _clamp(mom20 * 1.5))
        result = SignalResult(
            "market_regime",
            direction_of(score),
            round(score, 4),
            round(min(1.0, abs(score) + 0.25), 4),
            None,
            OK,
            analyzed_at,
            explanation,
        )
        _regime_cache[market_code] = (now, result)
        return result
    except Exception as exc:
        logger.warning("Market regime failed for %s: %s", market_code, exc)
        return SignalResult("market_regime", status=ERROR, analyzed_at=analyzed_at, explanation=["regime fetch failed"])
