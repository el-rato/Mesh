"""Chronological historical trading replay.

A trader operates the terminal during a user-defined historical period:

* No information available after the current historical timestamp is ever used
  (the strict information boundary).
* Decisions are made strictly in time order; execution fills at the first bar
  AFTER the decision timestamp.
* Signal availability is explicit (AVAILABLE / NO_DATA / ERROR). A missing
  signal is never treated as a directional vote.
* The LSTM is unavailable during replay: its saved weights were trained on data
  that includes the replay window, so using it would leak the future. It is
  honestly marked NO_DATA. GBM + momentum are trained/computed only on bars up
  to the current timestamp and are therefore time-safe.
* One consistent provider dataset is used for the whole run (historical.fetch).
* The replay is fully isolated from the live Paper Portfolio.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from . import historical
from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

TIMEFRAME_MIN = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
#: Calendar days of warmup history fetched BEFORE the replay start so indicators
#: (SMA-50/200, momentum-20, RSI) have a causal history. Only bars inside the
#: replay window produce decisions.
WARMUP_DAYS = {"5m": 12, "15m": 18, "30m": 24, "1h": 30, "1d": 320}
_MIN_HISTORY_BARS = 30
_FORWARD_HORIZONS = (5, 15, 30, 60)
_NEUTRAL_BAND = 0.05


def clear_cache() -> None:
    historical.clear_cache()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_dataset(
    db: Database, market: str, ticker: str, timeframe: str, start: str, end: str
):
    """Load the replay dataset (warmup + window) through the provider chain."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    fetch_start = (start_dt - timedelta(days=WARMUP_DAYS.get(timeframe, 15))).strftime("%Y-%m-%d")
    result = historical.fetch(db, market, ticker, fetch_start, end, timeframe, min_rows=_MIN_HISTORY_BARS)
    return result.rows, result


def _load_regime_rows(db: Database, market: str, start: str, end: str) -> list[dict[str, Any]]:
    """Benchmark index history for [start, end] (daily, time-safe)."""
    try:
        from .indexes import MARKET_INDEXES
        from .markets import load_markets

        m = load_markets(settings.markets_dir).get(market)
        suffix = (m.yahoo_suffix if m else "") or ""
        idx = MARKET_INDEXES.get(market, [{}])[0].get("symbol")
        if not idx:
            return []
        if suffix and not idx.endswith(suffix):
            idx = f"{idx}{suffix}"
        return historical.fetch_symbol(idx, start, end, timeframe="1d", min_rows=20)
    except Exception as exc:
        logger.warning("Replay regime preload failed for %s: %s", market, exc)
        return []


# ---------------------------------------------------------------------------
# Causal indicator frame (row i uses only rows <= i)
# ---------------------------------------------------------------------------


def _indicator_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["t"]).sort_values("t").drop_duplicates("t").reset_index(drop=True)
    c = df["close"].astype(float)
    df["momentum_20"] = c.pct_change(20).fillna(0.0)
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)
    rsi = rsi.mask(avg_loss.eq(0), 100.0).fillna(50.0)
    df["rsi_14"] = rsi
    df["sma_50"] = c.rolling(50).mean()
    df["sma_200"] = c.rolling(200).mean()
    df["above_sma_50"] = c > df["sma_50"]
    return df


def _num(value: Any) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _price_state_at(df: pd.DataFrame, i: int, market: str, ticker: str):
    from .price import PriceState

    row = df.iloc[i]
    sma50 = _num(row.get("sma_50"))
    sma200 = _num(row.get("sma_200"))
    trend = (sma50 - sma200) / sma200 if sma200 else 0.0
    return PriceState(
        market=market,
        ticker=ticker,
        close=float(row["close"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        volume=int(_num(row.get("volume"))),
        momentum_20=_num(row.get("momentum_20")),
        rsi_14=_num(row.get("rsi_14")) or 50.0,
        sma_50=sma50,
        sma_200=sma200,
        trend_50_200=trend,
        price_above_sma_50=bool(row.get("above_sma_50")) and sma50 > 0,
    )


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Time-safe signals (historical information only)
# ---------------------------------------------------------------------------


def _quant_ensemble(df: pd.DataFrame, price_dict: dict[str, Any], ts: str, gbm_cache: dict[str, Any]):
    """Aggregate GBM + momentum (time-safe). LSTM is excluded during replay.

    ``gbm_cache`` is keyed by the trading day: the daily-direction GBM model is
    recomputed at the first decision of each day (bars up to that moment) and
    reused for the rest of the session. This keeps the signal time-safe (never
    uses future bars) while avoiding an O(n) retrain on every intraday decision.
    """
    from . import signals

    day = ts[:10]
    if day in gbm_cache:
        gbm = gbm_cache[day]
    else:
        try:
            gbm_df = df[["open", "high", "low", "close", "volume"]].rename(
                columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
            )
            gbm = signals.gbm_signal(gbm_df)
        except Exception as exc:
            gbm = signals.SignalResult("gbm", status="error", explanation=[f"gbm failed: {exc}"])
        gbm_cache[day] = gbm
    gbm.analyzed_at = ts
    try:
        momentum = signals.momentum_signal(price_dict)
    except Exception as exc:
        momentum = signals.SignalResult("momentum", status="error", explanation=[f"momentum failed: {exc}"])
    momentum.analyzed_at = ts
    models = [gbm, momentum]
    available = [m for m in models if m.status == "ok" and m.score is not None and m.confidence is not None]
    weights = {"gbm": settings.model_weight_gbm, "momentum": settings.model_weight_momentum}
    if not available:
        return signals.SignalResult(
            "quantitative_ensemble", status="no_data", analyzed_at=ts,
            explanation=["no time-safe quantitative model available (LSTM excluded during replay)"],
        ), models
    numerator = 0.0
    denominator = 0.0
    for m in available:
        eff = m.confidence * weights.get(m.model_name, 0.0)
        numerator += m.score * eff
        denominator += eff
    if denominator <= 0:
        return signals.SignalResult(
            "quantitative_ensemble", status="no_data", analyzed_at=ts,
            explanation=["no time-safe quantitative model available (LSTM excluded during replay)"],
        ), models
    score = _clamp(numerator / denominator)
    conf = sum(m.confidence * weights.get(m.model_name, 0.0) for m in available) / max(
        1e-9, sum(weights.get(m.model_name, 0.0) for m in available)
    )
    ensemble = signals.SignalResult(
        "quantitative_ensemble",
        signals.direction_of(score),
        round(score, 4),
        round(min(1.0, conf), 4),
        None,
        "ok",
        analyzed_at=ts,
        explanation=[f"{len(available)}/{len(models)} time-safe models available"],
    )
    return ensemble, models


def _news_at(db: Database, market: str, ticker: str, ts: datetime) -> dict[str, Any]:
    """News articles with published_at <= ts (historical information only)."""
    rows = db.recent_news(market, ticker, limit=500)
    scored: list[tuple[str, str, str, float]] = []
    scores: list[float] = []
    for r in rows:
        published = str(r.get("published_at") or "")
        try:
            pub_dt = datetime.fromisoformat(published)
        except (ValueError, TypeError):
            continue
        if pub_dt > ts:
            continue
        s = _num(r.get("sentiment_score"))
        if not s:
            continue
        scores.append(_clamp(s))
        scored.append((str(r.get("title") or ""), str(r.get("source") or ""), str(r.get("sentiment_label") or ""), _clamp(s)))
    if not scored:
        return {"available": False, "score": 0.0, "label": "", "articles": [], "confidence": None}
    score = _clamp(sum(scores) / len(scores))
    label = "bullish" if score > _NEUTRAL_BAND else "bearish" if score < -_NEUTRAL_BAND else "neutral"
    return {
        "available": True,
        "score": score,
        "label": label,
        "articles": scored,
        "confidence": round(min(1.0, 0.2 + len(scores) / 20.0), 4),
    }


def _research_at(db: Database, market: str, ticker: str, ts: datetime, news: dict[str, Any]):
    """Researcher: brief from historical evidence only, else explicit NO_DATA."""
    from . import research as research_mod

    if not news["available"]:
        brief = research_mod.no_data_brief(ticker=ticker, company=ticker)
        brief.analyzed_at = ts.isoformat()
        return brief
    brief = research_mod.build_brief(
        ticker=ticker,
        company=ticker,
        market=market,
        news_score=news["score"],
        news_label=news["label"],
        article_count=len(news["articles"]),
        evidence=news["articles"],
    )
    brief.analyzed_at = ts.isoformat()
    return brief


def _regime_at(idx_df: pd.DataFrame, ts: Any) -> dict[str, Any]:
    """Market regime from benchmark index closes <= ts (time-safe)."""
    from . import signals

    if idx_df is None or idx_df.empty or "t" not in idx_df.columns:
        return signals.SignalResult("market_regime", status="no_data",
                                    explanation=["no benchmark index data before decision timestamp"])
    ts_ts = pd.Timestamp(ts)
    pos = int(idx_df["t"].searchsorted(ts_ts, side="right")) - 1
    if pos < 21:
        return signals.SignalResult("market_regime", status="no_data",
                                    explanation=["insufficient index history before decision timestamp"])
    closes = idx_df["close"].astype(float).to_numpy()[: pos + 1]
    if len(closes) < 21:
        return signals.SignalResult("market_regime", status="no_data",
                                    explanation=["insufficient index history before decision timestamp"])
    last = closes[-1]
    sma50 = float(sum(closes[-50:]) / 50.0) if len(closes) >= 50 else float(sum(closes) / len(closes))
    sma200 = float(sum(closes[-200:]) / 200.0) if len(closes) >= 200 else sma50
    mom20 = (closes[-1] - closes[-21]) / (abs(closes[-21]) + 1e-9)
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
    return signals.SignalResult(
        "market_regime",
        signals.direction_of(score),
        round(score, 4),
        round(min(1.0, abs(score) + 0.25), 4),
        None,
        "ok",
        analyzed_at="",
        explanation=explanation,
    )


def _social_no_data(ts: str):
    from . import signals

    return signals.SignalResult(
        "social_momentum", status="no_data",
        analyzed_at=ts,
        explanation=["social momentum cannot be reconstructed for a historical timestamp"],
    )


def _build_signal_block(
    db: Database, market: str, ticker: str, df: pd.DataFrame, i: int, idx_df: pd.DataFrame | None,
    gbm_cache: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the canonical signal block as of bar i (causal, no look-ahead)."""
    ts = df.iloc[i]["t"]
    ts_iso = ts.isoformat()
    price = _price_state_at(df, i, market, ticker)
    price_dict = price.as_dict()

    from . import signals
    from .verdict import _normalize_price

    technical_score, technical_reasons = _normalize_price(price)
    quant, models = _quant_ensemble(df.iloc[: i + 1].reset_index(drop=True), price_dict, ts_iso, gbm_cache)
    news = _news_at(db, market, ticker, ts.to_pydatetime())
    research = _research_at(db, market, ticker, ts.to_pydatetime(), news)
    regime = _regime_at(idx_df, ts) if idx_df is not None and not idx_df.empty else signals.SignalResult(
        "market_regime", status="no_data", analyzed_at=ts_iso, explanation=["no benchmark index data preloaded"]
    )
    regime.analyzed_at = ts_iso
    social = _social_no_data(ts_iso)

    vdict = {
        "market": market,
        "ticker": ticker,
        "quantitative": {
            "status": quant.status,
            "score": quant.score,
            "confidence": quant.confidence,
            "model_name": quant.model_name,
        },
        "models": [m.as_dict() for m in models],
        "technical": {
            "score": technical_score,
            "confidence": min(1.0, abs(technical_score) + 0.5),
            "available": True,
            "reasons": technical_reasons,
        },
        "news_available": news["available"],
        "news_score": news["score"],
        "news": (
            {"confidence": news["confidence"], "article_count": len(news["articles"]), "label": news["label"]}
            if news["available"]
            else None
        ),
        "social": {"status": social.status, "score": social.score, "confidence": social.confidence},
        "market_regime": {"status": regime.status, "score": regime.score, "confidence": regime.confidence},
        "price": price_dict,
        "research": research.as_dict() if research else None,
        "decided_at": ts_iso,
    }
    return vdict


# ---------------------------------------------------------------------------
# Trader decision layer
# ---------------------------------------------------------------------------


class _Portfolio:
    """Single-security portfolio with cash / position / realized P&L."""

    def __init__(self, capital: float, size_ratio: float, fee: float, slip: float,
                 max_gross: float, short_margin: float) -> None:
        self.starting = float(capital)
        self.cash = float(capital)
        self.direction: str | None = None
        self.qty: float = 0.0
        self.entry: float = 0.0
        self.realized: float = 0.0
        self.size_ratio = size_ratio
        self.fee = fee
        self.slip = slip
        self.max_gross = max_gross
        self.short_margin = short_margin

    def equity(self, px: float) -> float:
        if self.direction == "LONG":
            return self.cash + self.qty * px
        if self.direction == "SHORT":
            return self.cash - self.qty * px
        return self.cash

    def gross_exposure(self, px: float) -> float:
        return self.qty * px if self.direction else 0.0

    def exposure_pct(self, px: float) -> float:
        eq = self.equity(px)
        return self.gross_exposure(px) / eq if eq > 0 else 0.0

    def _fill_px(self, px: float, side: str) -> float:
        if side == "BUY":
            return px * (1.0 + self.slip)
        if side == "SELL":
            return px * (1.0 - self.slip)
        if side == "SHORT":
            return px * (1.0 - self.slip)
        return px * (1.0 + self.slip)  # COVER

    def _desired_qty(self, px: float) -> int:
        eq = self.equity(px)
        target = self.size_ratio * eq
        held = self.qty * px if self.direction else 0.0
        additional = max(0.0, target - held)
        return max(1, int(additional / px)) if px > 0 else 0

    def _cap_open_qty(self, raw: int, px: float, direction: str) -> int:
        qty = raw
        while qty > 0:
            if direction == "LONG":
                cost = px * qty + self.fee
                if cost > self.cash + 1e-9:
                    qty -= 1
                    continue
                long_val = px * qty
                proj_cash = self.cash - cost
                proj_equity = proj_cash + long_val
            else:  # SHORT
                short_val = px * qty
                proj_cash = self.cash
                proj_equity = proj_cash - short_val
                if self.short_margin * short_val > self.cash + 1e-9:
                    qty -= 1
                    continue
            if self.max_gross * max(proj_equity, 1.0) < (px * qty + self.gross_exposure(px)):
                qty -= 1
                continue
            return qty
        return 0

    def open_long(self, px: float, ts: str) -> dict[str, Any] | None:
        if self.direction == "SHORT":
            return None
        raw = self._desired_qty(px)
        qty = self._cap_open_qty(raw, px, "LONG")
        if qty < 1:
            return None
        fill = self._fill_px(px, "BUY")
        cost = fill * qty + self.fee
        if cost > self.cash + 1e-9:
            qty = int((self.cash - self.fee) / fill) if fill > 0 else 0
            if qty < 1:
                return None
            cost = fill * qty + self.fee
        self.cash -= cost
        if self.direction == "LONG":
            self.entry = (self.entry * self.qty + fill * qty) / (self.qty + qty)
            self.qty += qty
        else:
            self.entry = fill
            self.qty = float(qty)
            self.direction = "LONG"
        return self._order("BUY", qty, fill, ts)

    def open_short(self, px: float, ts: str) -> dict[str, Any] | None:
        if self.direction == "LONG":
            return None
        raw = self._desired_qty(px)
        qty = self._cap_open_qty(raw, px, "SHORT")
        if qty < 1:
            return None
        fill = self._fill_px(px, "SHORT")
        self.cash += fill * qty - self.fee
        if self.direction == "SHORT":
            self.entry = (self.entry * self.qty + fill * qty) / (self.qty + qty)
            self.qty += qty
        else:
            self.entry = fill
            self.qty = float(qty)
            self.direction = "SHORT"
        return self._order("SHORT", qty, fill, ts)

    def close(self, px: float, ts: str) -> dict[str, Any] | None:
        if not self.direction or self.qty <= 1e-9:
            return None
        qty = self.qty
        side = "SELL" if self.direction == "LONG" else "COVER"
        fill = self._fill_px(px, side)
        if side == "SELL":
            self.cash += fill * qty - self.fee
            pnl = (fill - self.entry) * qty - self.fee
        else:
            self.cash -= fill * qty + self.fee
            pnl = (self.entry - fill) * qty - self.fee
        self.realized += pnl
        order = self._order(side, qty, fill, ts, pnl)
        self.direction = None
        self.qty = 0.0
        self.entry = 0.0
        return order

    def reduce(self, px: float, ts: str, fraction: float = 0.5) -> dict[str, Any] | None:
        if not self.direction or self.qty <= 1e-9:
            return None
        qty = max(1, int(self.qty * fraction))
        if qty > self.qty:
            qty = int(self.qty)
        side = "SELL" if self.direction == "LONG" else "COVER"
        fill = self._fill_px(px, side)
        if side == "SELL":
            self.cash += fill * qty - self.fee
            pnl = (fill - self.entry) * qty - self.fee
        else:
            self.cash -= fill * qty + self.fee
            pnl = (self.entry - fill) * qty - self.fee
        self.realized += pnl
        self.qty -= qty
        order = self._order(side, qty, fill, ts, pnl)
        if self.qty <= 1e-9:
            self.direction = None
            self.qty = 0.0
            self.entry = 0.0
        return order

    def _order(self, side: str, qty: float, price: float, ts: str, pnl: float | None = None) -> dict[str, Any]:
        return {
            "side": side,
            "quantity": round(qty, 6),
            "price": round(price, 6),
            "fee": round(self.fee, 4),
            "executed_at": ts,
            "direction": "SHORT" if side in ("SHORT", "COVER") else "LONG",
            "pnl": round(pnl, 2) if pnl is not None else None,
        }

    def close_all(self, px: float, ts: str) -> dict[str, Any] | None:
        return self.close(px, ts)


def _signal_summary(committee: dict[str, Any]) -> tuple[int, int]:
    verdict = committee.get("verdict")
    aligned = total = 0
    for s in committee.get("signals", []):
        if not s.get("available"):
            continue
        total += 1
        if verdict in ("BULL", "BEAR") and s.get("state") == verdict:
            aligned += 1
    return aligned, total


def _trader_decision(
    committee: dict[str, Any],
    price_px: float,
    port: _Portfolio,
    bull_th: float,
    bear_th: float,
) -> dict[str, Any]:
    """Trader action from the committee verdict + conviction + portfolio/risk.

    Returns action (BUY/SELL/SHORT/COVER/HOLD/INCREASE/REDUCE/REVERSE/NO_TRADE),
    a short tag, and rationale phrases built from the actual signals.
    """
    verdict = committee.get("verdict") or "NEUTRAL"
    conv = _num(committee.get("confidence"))
    aligned, total = _signal_summary(committee)
    pos_dir = port.direction
    equity = port.equity(price_px)
    exposure = port.exposure_pct(price_px)
    target_pct = port.size_ratio
    reasons: list[str] = []
    action = "HOLD"
    tag = ""

    def _open_text(side: str) -> str:
        if side == "BUY":
            return (f"Committee is {verdict} with {conv:.0%} conviction (entry threshold {bull_th:.0%}); "
                    f"{aligned}/{total} available signals align. Exposure {exposure:.0%} of equity stays within risk "
                    f"limits, so the trader opens a long position.")
        return (f"Committee is {verdict} with {conv:.0%} conviction (entry threshold {bear_th:.0%}); "
                f"{aligned}/{total} available signals align. Short margin is available, so the trader opens a short position.")

    if pos_dir is None:
        if verdict == "BULL" and conv >= bull_th:
            action = "BUY"
            reasons.append(_open_text("BUY"))
        elif verdict == "BEAR" and conv >= bear_th:
            action = "SHORT"
            reasons.append(_open_text("SHORT"))
        else:
            action = "NO_TRADE"
            reasons.append(
                f"Committee is {verdict} with {conv:.0%} conviction (thresholds {bull_th:.0%}/{bear_th:.0%}); "
                f"signals are {aligned}/{total} aligned. Expected edge is insufficient to justify increasing exposure, "
                f"so the trader holds cash."
            )
    elif pos_dir == "LONG":
        if verdict == "BEAR" and conv >= bear_th:
            action = "SELL"
            tag = "EXIT LONG"
            reasons.append(
                f"Committee direction reversed to {verdict} ({conv:.0%} conviction). The trader exits the existing "
                f"long ({port.qty:.0f} @ {port.entry:.2f}) rather than holding against the new signal."
            )
        elif verdict == "BEAR" and bear_th - 0.15 <= conv < bear_th:
            action = "REDUCE"
            tag = "REDUCE LONG 50%"
            reasons.append(
                f"Committee is turning {verdict} ({conv:.0%}) but conviction is below the full exit threshold "
                f"{bear_th:.0%}. The trader trims the long by half to reduce risk."
            )
        elif verdict == "BULL" and conv >= bull_th:
            if exposure < target_pct * 0.7:
                action = "INCREASE"
                tag = "INCREASE LONG"
                reasons.append(
                    f"Committee stays {verdict} ({conv:.0%}) and exposure ({exposure:.0%}) is below the target "
                    f"{target_pct:.0%}; the trader adds to the long within risk limits."
                )
            else:
                action = "HOLD"
                reasons.append(
                    f"Committee stays {verdict} ({conv:.0%}) with exposure at {exposure:.0%}; the trader maintains "
                    f"the existing long position."
                )
        else:
            action = "HOLD"
            reasons.append(
                f"Committee is {verdict} ({conv:.0%}); no action is warranted on the existing long position."
            )
    else:  # SHORT
        if verdict == "BULL" and conv >= bull_th:
            action = "COVER"
            tag = "EXIT SHORT"
            reasons.append(
                f"Committee direction reversed to {verdict} ({conv:.0%} conviction). The trader covers the existing "
                f"short ({port.qty:.0f} @ {port.entry:.2f}) rather than holding against the new signal."
            )
        elif verdict == "BULL" and bull_th - 0.15 <= conv < bull_th:
            action = "REDUCE"
            tag = "REDUCE SHORT 50%"
            reasons.append(
                f"Committee is turning {verdict} ({conv:.0%}) but conviction is below the full exit threshold "
                f"{bull_th:.0%}. The trader trims the short by half."
            )
        elif verdict == "BEAR" and conv >= bear_th:
            if exposure < target_pct * 0.7:
                action = "INCREASE"
                tag = "INCREASE SHORT"
                reasons.append(
                    f"Committee stays {verdict} ({conv:.0%}) and exposure ({exposure:.0%}) is below the target "
                    f"{target_pct:.0%}; the trader adds to the short within margin limits."
                )
            else:
                action = "HOLD"
                reasons.append(
                    f"Committee stays {verdict} ({conv:.0%}) with exposure at {exposure:.0%}; the trader maintains "
                    f"the existing short position."
                )
        else:
            action = "HOLD"
            reasons.append(
                f"Committee is {verdict} ({conv:.0%}); no action is warranted on the existing short position."
            )

    return {"action": action, "tag": tag, "reasons": reasons,
            "aligned": aligned, "total": total, "conviction_pct": round(conv * 100.0, 1)}


# ---------------------------------------------------------------------------
# Forward returns (evaluation only — never used for decisions)
# ---------------------------------------------------------------------------


def _forward_returns(df: pd.DataFrame, i: int) -> dict[str, float | None]:
    ref = float(df.iloc[i]["close"])
    if not ref:
        return {f"p{h}": None for h in _FORWARD_HORIZONS}
    ts = df.iloc[i]["t"]
    out: dict[str, float | None] = {f"p{h}": None for h in _FORWARD_HORIZONS}
    for j in range(i + 1, len(df)):
        dt = df.iloc[j]["t"]
        if dt is None:
            continue
        for h in _FORWARD_HORIZONS:
            if out[f"p{h}"] is None and dt >= ts + timedelta(minutes=h):
                out[f"p{h}"] = round(float(df.iloc[j]["close"]) / ref - 1.0, 6)
        if all(v is not None for v in out.values()):
            break
    return out


def _correctness(verdict: str, forward: dict[str, float | None]) -> int | None:
    fwd = forward.get("p30") or forward.get("p15") or forward.get("p5") or forward.get("p60")
    if fwd is None or verdict not in ("BULL", "BEAR"):
        return None
    return 1 if (fwd > 0) == (verdict == "BULL") else 0


# ---------------------------------------------------------------------------
# Summary / metrics
# ---------------------------------------------------------------------------


def _summary(capital: float, decisions: list[dict[str, Any]], port: _Portfolio, final_px: float) -> dict[str, Any]:
    ending = port.equity(final_px)
    return_pct = (ending / capital - 1.0) * 100.0 if capital else 0.0
    curve = [d["equity"] for d in decisions]
    peak = capital
    max_dd = 0.0
    for v in [capital, *curve]:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0)

    orders = [o for d in decisions for o in d.get("orders", [])]
    closing = [o for o in orders if o["side"] in ("SELL", "COVER")]
    wins = sum(1 for o in closing if (o.get("pnl") or 0) > 0)
    long_pnl = sum(o.get("pnl") or 0 for o in closing if o["side"] == "SELL")
    short_pnl = sum(o.get("pnl") or 0 for o in closing if o["side"] == "COVER")

    bulls = [d for d in decisions if d["verdict"] == "BULL"]
    bears = [d for d in decisions if d["verdict"] == "BEAR"]
    no_trades = [d for d in decisions if d["action"] in ("NO_TRADE", "HOLD")]
    bull_acc = None
    bull_n = 0
    if bulls:
        correct = [d for d in bulls if d.get("correct") is not None]
        bull_n = len(correct)
        bull_acc = round(sum(int(d["correct"]) for d in correct) / bull_n, 4) if bull_n else None
    bear_acc = None
    bear_n = 0
    if bears:
        correct = [d for d in bears if d.get("correct") is not None]
        bear_n = len(correct)
        bear_acc = round(sum(int(d["correct"]) for d in correct) / bear_n, 4) if bear_n else None

    convs = [d["conviction"] for d in decisions if d.get("conviction") is not None]
    fwd_sums: dict[str, list[float]] = {f"p{h}": [] for h in _FORWARD_HORIZONS}
    for d in decisions:
        for h in _FORWARD_HORIZONS:
            v = d.get("forward", {}).get(f"p{h}")
            if v is not None:
                fwd_sums[f"p{h}"].append(v)

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 6) if vals else None

    no_trade_ret = [d.get("forward", {}).get("p30") for d in no_trades if d.get("forward", {}).get("p30") is not None]
    no_trade_outcome = _avg([float(v) for v in no_trade_ret]) if no_trade_ret else None

    return {
        "starting_capital": round(capital, 2),
        "ending_equity": round(ending, 2),
        "return_pct": round(return_pct, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "trades": len(closing),
        "orders": len(orders),
        "win_rate": round(wins / len(closing), 4) if closing else None,
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "decisions": len(decisions),
        "evaluable_decisions": sum(1 for d in decisions if d.get("evaluatable")),
        "not_evaluable_decisions": sum(1 for d in decisions if not d.get("evaluatable")),
        "bull_decisions": len(bulls),
        "bear_decisions": len(bears),
        "no_trade_decisions": len(no_trades),
        "actions": {
            a: sum(1 for d in decisions if d["action"] == a)
            for a in ("BUY", "SELL", "SHORT", "COVER", "HOLD", "NO_TRADE", "INCREASE", "REDUCE")
            if any(d["action"] == a for d in decisions)
        },
        "avg_conviction": round(sum(convs) / len(convs), 1) if convs else None,
        "bull_accuracy": bull_acc,
        "bull_n": bull_n,
        "bear_accuracy": bear_acc,
        "bear_n": bear_n,
        "no_trade_forward_30m": no_trade_outcome,
        "forward_5m": _avg(fwd_sums["p5"]),
        "forward_15m": _avg(fwd_sums["p15"]),
        "forward_30m": _avg(fwd_sums["p30"]),
        "forward_60m": _avg(fwd_sums["p60"]),
    }


# ---------------------------------------------------------------------------
# Chronological replay engine
# ---------------------------------------------------------------------------


def _portfolio_snapshot(port: _Portfolio, px: float, ts: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "cash": round(port.cash, 2),
        "equity": round(port.equity(px), 2),
        "position_direction": port.direction,
        "position_qty": round(port.qty, 6) if port.qty else 0.0,
        "position_entry": round(port.entry, 6) if port.entry else None,
        "realized_pnl": round(port.realized, 2),
        "exposure_pct": round(port.exposure_pct(px) * 100.0, 2),
        "gross_exposure": round(port.gross_exposure(px), 2),
    }


def run(
    db: Database,
    market: str,
    ticker: str,
    start: str,
    end: str,
    timeframe: str = "15m",
    decision_interval: str = "15m",
    capital: float = 100000.0,
    bull_threshold: float = 70.0,
    bear_threshold: float = 70.0,
    size_ratio: float = 0.25,
    store: bool = True,
) -> dict[str, Any]:
    """Run a chronological historical replay for [start, end]."""
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except (TypeError, ValueError):
        return {"status": "error", "reason": "dates must be YYYY-MM-DD", "security_id": f"{market}:{ticker}"}
    if end_dt < start_dt:
        return {"status": "error", "reason": "end date must be on or after start date", "security_id": f"{market}:{ticker}"}
    if timeframe not in TIMEFRAME_MIN:
        return {"status": "error", "reason": f"unsupported timeframe {timeframe!r}", "security_id": f"{market}:{ticker}"}
    if decision_interval not in TIMEFRAME_MIN:
        return {"status": "error", "reason": f"unsupported decision interval {decision_interval!r}", "security_id": f"{market}:{ticker}"}
    if capital <= 0:
        return {"status": "error", "reason": "starting capital must be positive", "security_id": f"{market}:{ticker}"}

    bull_th = float(bull_threshold) / 100.0
    bear_th = float(bear_threshold) / 100.0
    bull_th = max(0.0, min(1.0, bull_th))
    bear_th = max(0.0, min(1.0, bear_th))

    rows, source = _load_dataset(db, market, ticker, timeframe, start, end)
    data_source = source.as_dict() if source else {"status": "error", "provider": "", "rows": []}
    if not rows:
        return {
            "status": "no_data",
            "security_id": f"{market}:{ticker}",
            "reason": (source.error or "no provider returned data for the requested range") if source else "no provider returned data",
            "data_source": data_source,
        }

    df = _indicator_frame(rows)
    if len(df) < _MIN_HISTORY_BARS:
        return {
            "status": "partial",
            "security_id": f"{market}:{ticker}",
            "reason": f"only {len(df)} bars available for the requested range",
            "data_source": data_source,
        }

    # Time-safe benchmark index for the market-regime signal.
    regime_rows = _load_regime_rows(db, market, start, end)
    idx_df = _indicator_frame(regime_rows) if regime_rows else None

    tf_min = TIMEFRAME_MIN[timeframe]
    interval_min = TIMEFRAME_MIN[decision_interval]
    bars_step = max(1, interval_min // tf_min)

    port = _Portfolio(
        capital=float(capital),
        size_ratio=float(size_ratio),
        fee=float(settings.paper_commission),
        slip=float(settings.paper_slippage),
        max_gross=float(settings.paper_max_gross_ratio),
        short_margin=float(settings.paper_short_margin),
    )

    run_id = f"RP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{ticker.upper()}"
    decisions: list[dict[str, Any]] = []
    gbm_cache: dict[str, Any] = {}
    n = len(df)

    for i in range(_MIN_HISTORY_BARS, n - 1, bars_step):
        ts = df.iloc[i]["t"]
        if ts is None:
            continue
        ts_dt = ts.to_pydatetime()
        if ts_dt < start_dt:
            continue
        if ts_dt.date() > end_dt.date():
            break

        ref_px = float(df.iloc[i]["close"])
        vdict = _build_signal_block(db, market, ticker, df, i, idx_df, gbm_cache)
        from .dossier import committee_decision, committee_signals

        committee = committee_signals(vdict)
        rich = committee_decision(vdict)
        trader = _trader_decision(committee, ref_px, port, bull_th, bear_th)
        action = trader["action"]

        # NOT_EVALUABLE: with no committee output there is no defensible basis
        # to trade at this timestamp. The decision is recorded honestly as
        # unevaluable (never a fabricated NEUTRAL trade).
        evaluatable = committee.get("verdict") not in (None, "N/A")
        if not evaluatable:
            action = "NO_TRADE"
            trader["action"] = "NO_TRADE"
            trader["reasons"] = [
                "NOT_EVALUABLE: no committee signals were available at this historical timestamp "
                "(no time-safe quant, technical, news, or regime data), so no trade can be justified."
            ]

        # Execution at the FIRST bar strictly after the decision timestamp.
        fill_px = float(df.iloc[i + 1]["open"])
        exec_time = df.iloc[i + 1]["t"].isoformat()
        before = _portfolio_snapshot(port, ref_px, ts.isoformat())

        orders: list[dict[str, Any]] = []
        if action in ("BUY", "SHORT", "INCREASE"):
            if action == "INCREASE":
                if port.direction == "SHORT":
                    order = port.open_short(fill_px, exec_time)
                else:
                    order = port.open_long(fill_px, exec_time)
            elif action == "BUY":
                order = port.open_long(fill_px, exec_time)
            else:  # SHORT
                order = port.open_short(fill_px, exec_time)
            if order:
                orders.append(order)
        elif action == "REDUCE":
            order = port.reduce(fill_px, exec_time, 0.5)
            if order:
                orders.append(order)
        elif action in ("SELL", "COVER"):
            order = port.close_all(fill_px, exec_time)
            if order:
                orders.append(order)
            elif not orders:
                # Position was already closed; record an honest HOLD-equivalent.
                action = "HOLD"
                trader["reasons"] = ["The trader has no open position to exit at this timestamp."]

        after_px = float(df.iloc[i + 1]["close"])
        after = _portfolio_snapshot(port, after_px, exec_time)
        forward = _forward_returns(df, i)
        correct = _correctness(committee.get("verdict"), forward)
        verdict = committee.get("verdict") or "NEUTRAL"
        conviction = trader["conviction_pct"]
        research = vdict.get("research") or {}
        if research.get("status") != "ok":
            trader["reasons"].append("Research unavailable for this timestamp.")

        decision = {
            "decision_id": f"{run_id}-{len(decisions):04d}",
            "ts": ts.isoformat(),
            "security": f"{market}:{ticker}",
            "verdict": verdict,
            "conviction": conviction,
            "action": action,
            "tag": trader.get("tag", ""),
            # Per-decision data sufficiency: READY when the committee could be
            # evaluated, NOT_EVALUABLE when required historical data was missing.
            "status": "READY" if evaluatable else "NOT_EVALUABLE",
            "evaluatable": evaluatable,
            "reference_price": round(ref_px, 6),
            "execution_price": round(fill_px, 6),
            "orders": orders,
            "quantity": round(sum(o["quantity"] for o in orders), 6),
            "signal_states": {s["key"]: s.get("state") for s in committee.get("signals", [])},
            "signal_timestamps": {s["key"]: ts.isoformat() for s in committee.get("signals", [])},
            "signal_confidences": {
                s["key"]: (round(s["confidence"], 4) if s.get("confidence") is not None else None)
                for s in committee.get("signals", [])
            },
            "signal_statuses": {s["key"]: ("AVAILABLE" if s.get("available") else "NO_DATA") for s in committee.get("signals", [])},
            "signal_scores": {
                s["key"]: (round(s["score"], 4) if s.get("score") is not None else None)
                for s in committee.get("signals", [])
            },
            "signal_alignment": f"{trader['aligned']}/{trader['total']}",
            "research": research,
            "portfolio_before": before,
            "portfolio_after": after,
            "reason": " ".join(trader["reasons"]),
            "rationale": trader["reasons"],
            "committee_thesis": rich.get("thesis", ""),
            "bull_case": rich.get("bull_case", []),
            "bear_case": rich.get("bear_case", []),
            "neutral_case": rich.get("neutral_case", []),
            "key_evidence": rich.get("key_evidence", []),
            "disagreements": rich.get("disagreements", []),
            "forecast_range": rich.get("forecast_range"),
            "why": rich.get("why", ""),
            "forward": forward,
            "correct": correct,
        }
        decisions.append(decision)
        equity_ts = ts.isoformat()
        equity_val = port.equity(float(df.iloc[i + 1]["close"]))
        decisions[-1]["equity"] = round(equity_val, 2)
        decisions[-1]["portfolio_after"]["equity"] = round(equity_val, 2)
        decisions[-1]["portfolio_after"]["ts"] = exec_time

    final_px = float(df.iloc[n - 1]["close"])
    summary = _summary(float(capital), decisions, port, final_px)

    if store:
        try:
            db.insert_replay_run(
                run_id, market, ticker, start, end, timeframe, interval_min,
                float(capital), json.dumps(summary),
            )
            for d in decisions:
                db.insert_replay_decision(
                    run_id, d["decision_id"], market, ticker, d["ts"], d["action"], d["verdict"],
                    d["conviction"], d["reference_price"], d["execution_price"],
                    d["quantity"], d["portfolio_after"]["cash"], d["equity"],
                    d["portfolio_after"]["position_direction"], d["portfolio_after"]["position_qty"],
                    d["reason"], json.dumps(d),
                )
        except Exception as exc:
            logger.warning("Failed to persist replay %s: %s", run_id, exc)

    return {
        "status": "ok",
        "mode": "replay",
        "security_id": f"{market}:{ticker}",
        "run_id": run_id,
        "start_date": start,
        "end_date": end,
        "timeframe": timeframe,
        "decision_interval": decision_interval,
        "data_source": data_source,
        "isolated": True,
        **summary,
        "equity_curve": [{"t": d["ts"], "equity": d["equity"]} for d in decisions],
        "decisions_log": decisions,
    }
