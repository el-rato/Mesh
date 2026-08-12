"""Backtest + fast simulation engine.

Two modes share the same execution core:

* FAST SIMULATION — quick demonstration using historical OHLCV and a
  price-derived committee view. Clearly a DEMONSTRATION, not validated.
* BACKTEST — rigorous historical evaluation with strict information cutoff
  (each decision only sees bars up to its timestamp) and immutable decision
  snapshots stored in the DB.

Simulation is isolated from the live Paper Portfolio. It reuses the existing
Committee, signal engine, price-state builder, and paper-trading accounting
(paper.positions_from_orders / _cash_from_orders / realized_per_order).
No real orders are possible.
"""

from __future__ import annotations

import logging
import math
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from . import paper
from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: Period presets -> (timeframe, calendar window days, decision step in bars).
_PERIODS = {
    "quick": ("15m", 1, 3),
    "day": ("30m", 1, 1),
    "week": ("30m", 6, 1),
    "custom": ("1h", 30, 1),
}
_STEP_MIN = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}


def clear_cache() -> None:
    from . import historical

    historical.clear_cache()


def _load_dataset(db: Database, market: str, ticker: str, timeframe: str, window_days: int):
    """Load historical bars through the multi-provider service.

    Returns (bars, historical.HistoricalDataResult). One backtest run uses
    exactly one dataset; no data is fabricated or swapped mid-run.
    """
    from . import historical

    end = datetime.now(UTC).strftime("%Y-%m-%d")
    start = (datetime.now(UTC) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    result = historical.fetch(db, market, ticker, start, end, timeframe, min_rows=35)
    return result.rows, result


def _price_state(market: str, ticker: str, bars: list[dict[str, Any]], i: int):
    """PriceState from the window of bars available up to index i (no look-ahead)."""
    sub = bars[: i + 1]
    if len(sub) < 31:
        return None
    df = pd.DataFrame(
        [
            {"Open": b["open"], "High": b["high"], "Low": b["low"], "Close": b["close"], "Volume": b["volume"]}
            for b in sub
        ]
    )
    from .price import build_price_state

    return build_price_state(market, ticker, df)


def _trend_score(bars: list[dict[str, Any]], i: int, lookback: int) -> float:
    if i < lookback:
        return 0.0
    prev = float(bars[i - lookback]["close"])
    cur = float(bars[i]["close"])
    return (cur - prev) / prev if prev else 0.0


def _committee_view(market: str, ticker: str, bars: list[dict[str, Any]], i: int, step: int):
    """Committee opinion computable up to index i (no look-ahead).

    Only price data is available historically (no news/social/13F reconstruction
    yet), so the opinion is a short-window price-trend signal. The real committee
    code is still used for the signal breakdown; the demo conviction scales trend
    strength so the configurable strategy thresholds are meaningful.
    """
    lookback = max(3, step)
    trend = _trend_score(bars, i, lookback)
    score = max(-1.0, min(1.0, trend * 20.0))  # 1% move -> 0.2
    direction = "BULL" if score > 0.05 else "BEAR" if score < -0.05 else "NEUTRAL"
    demo_conviction = round(max(0.0, min(100.0, 50.0 + abs(score) * 150.0)), 1)
    from .dossier import committee_signals

    vdict = {
        "quantitative": {
            "status": "ok", "score": score, "confidence": min(1.0, abs(score) * 2.5 + 0.45),
            "direction": direction, "model_name": "quantitative_ensemble",
        },
        "technical": {"score": score, "confidence": min(1.0, abs(score) + 0.5), "available": True},
        "news_available": False,
        "news_score": 0.0,
        "social": {"status": "no_data"},
        "market_regime": {"status": "no_data"},
        "price": {},
    }
    committee = committee_signals(vdict)
    return {"verdict": direction, "conviction": demo_conviction, "signals": committee.get("signals", [])}


def _decide(view: dict[str, Any], bull_th: float, bear_th: float) -> str | None:
    conv = view["conviction"]
    verdict = view["verdict"]
    if verdict == "BULL" and conv >= bull_th:
        return "LONG"
    if verdict == "BEAR" and conv >= bear_th:
        return "SHORT"
    return None


def _equity(capital: float, orders: list[dict[str, Any]], bars: list[dict[str, Any]], i: int, market: str, ticker: str) -> float:
    cash = paper._cash_from_orders(capital, orders)
    positions = paper.positions_from_orders(orders)
    pos = positions.get((market, ticker.upper()))
    mv = 0.0
    if pos and pos["qty"] > 0 and i < len(bars):
        px = float(bars[i]["close"])
        mv = px * pos["qty"] if pos["direction"] == "LONG" else -px * pos["qty"]
    return cash + mv


def _forward_returns(bars: list[dict[str, Any]], i: int, step_min: int) -> dict[str, float | None]:
    out: dict[str, float | None] = {"p5": None, "p15": None, "p30": None, "p60": None}
    for h in (5, 15, 30, 60):
        j = i + max(1, h // max(step_min, 1))
        if j < len(bars):
            out[f"p{h}"] = float(bars[j]["close"])
    return out


def _run(
    db: Database,
    market: str,
    ticker: str,
    period: str,
    capital: float,
    bull_th: float,
    bear_th: float,
    size_ratio: float,
    mode: str,
) -> dict[str, Any]:
    timeframe, window_days, step = _PERIODS.get(period, _PERIODS["quick"])
    bars, source = _load_dataset(db, market, ticker, timeframe, window_days)
    data_source = source.as_dict() if source else {"status": "error", "provider": "", "rows": []}
    if len(bars) < 35:
        return {
            "status": "no_data" if not bars else "partial",
            "security_id": f"{market}:{ticker}",
            "reason": (source.error or "insufficient historical data") if source else "no provider returned data",
            "data_source": data_source,
        }
    step_min = _STEP_MIN.get(timeframe, 30)

    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    position = None
    position_qty = 0.0
    fee = settings.paper_commission

    def close_position(i: int, action: str) -> None:
        nonlocal position, position_qty
        if not position or position_qty <= 0:
            return
        px = float(bars[i]["close"]) * (1.0 + settings.paper_slippage)
        oid = f"SIM-{secrets.token_hex(3).upper()}"
        orders.append({"order_id": oid, "market": market, "ticker": ticker.upper(), "side": action, "quantity": position_qty, "price": px, "fee": fee, "executed_at": bars[i]["date"]})
        trades.append({"time": bars[i]["date"], "action": action, "side": position, "qty": position_qty, "price": round(px, 4), "committee": f"{'BULL' if position == 'LONG' else 'BEAR'}", "pnl": None, "order_id": oid})
        position = None
        position_qty = 0.0

    i = 31
    while i < len(bars):
        view = _committee_view(market, ticker, bars, i, step)
        verdict = view["verdict"]
        conv = view["conviction"]
        decisions.append({"time": bars[i]["date"], "verdict": verdict, "conviction": round(conv, 2), "signals": view["signals"]})
        target = _decide(view, bull_th, bear_th)
        px = float(bars[i]["close"]) * (1.0 + settings.paper_slippage)

        if target == "LONG":
            if position == "SHORT":
                close_position(i, "COVER")
            if position != "LONG":
                eq = _equity(capital, orders, bars, i, market, ticker)
                qty = max(1, int(size_ratio * eq / px))
                oid = f"SIM-{secrets.token_hex(3).upper()}"
                orders.append({"order_id": oid, "market": market, "ticker": ticker.upper(), "side": "BUY", "quantity": qty, "price": px, "fee": fee, "executed_at": bars[i]["date"]})
                trades.append({"time": bars[i]["date"], "action": "BUY", "side": "LONG", "qty": qty, "price": round(px, 4), "committee": f"BULL {conv:.0f}", "pnl": None, "order_id": oid})
                position = "LONG"
                position_qty = float(qty)
        elif target == "SHORT":
            if position == "LONG":
                close_position(i, "SELL")
            if position != "SHORT":
                eq = _equity(capital, orders, bars, i, market, ticker)
                qty = max(1, int(size_ratio * eq / px))
                oid = f"SIM-{secrets.token_hex(3).upper()}"
                orders.append({"order_id": oid, "market": market, "ticker": ticker.upper(), "side": "SHORT", "quantity": qty, "price": px, "fee": fee, "executed_at": bars[i]["date"]})
                trades.append({"time": bars[i]["date"], "action": "SHORT", "side": "SHORT", "qty": qty, "price": round(px, 4), "committee": f"BEAR {conv:.0f}", "pnl": None, "order_id": oid})
                position = "SHORT"
                position_qty = float(qty)
        else:
            close_position(i, "COVER" if position == "SHORT" else "SELL")

        equity.append({"t": bars[i]["date"], "equity": round(_equity(capital, orders, bars, i, market, ticker), 2)})
        if mode == "backtest":
            ref = float(bars[i]["close"])
            fwd = _forward_returns(bars, i, step_min)
            snapshots.append({
                "ts": bars[i]["date"], "verdict": verdict, "conviction": conv,
                "reference_price": ref, "signals": view["signals"],
                "forward": fwd, "correct": _correct(verdict, ref, fwd),
            })
        i += step

    if position:
        close_position(len(bars) - 1, "COVER" if position == "SHORT" else "SELL")

    realized = paper.realized_per_order(orders)
    for t in trades:
        if t["pnl"] is None and t["order_id"] in realized:
            t["pnl"] = round(realized[t["order_id"]], 2)
    final_equity = _equity(capital, orders, bars, len(bars) - 1, market, ticker)

    positions = paper.positions_from_orders(orders)
    closing = [o for o in orders if o["side"] in ("SELL", "COVER")]
    wins = [r for r in realized.values() if r > 0]
    losses = [r for r in realized.values() if r < 0]
    long_pnl = sum(realized[o["order_id"]] for o in closing if o["side"] == "SELL")
    short_pnl = sum(realized[o["order_id"]] for o in closing if o["side"] == "COVER")

    drawdown = 0.0
    peak = capital
    for e in equity:
        peak = max(peak, e["equity"])
        drawdown = min(drawdown, e["equity"] / peak - 1.0)

    result = {
        "status": "ok",
        "mode": mode,
        "security_id": f"{market}:{ticker}",
        "data_source": data_source,
        "starting_capital": round(capital, 2),
        "ending_equity": round(final_equity, 2),
        "return_pct": round((final_equity / capital - 1.0) * 100.0, 4) if capital else 0.0,
        "trades": len(closing),
        "win_rate": round(len(wins) / len(closing), 4) if closing else None,
        "max_drawdown_pct": round(drawdown * 100.0, 4),
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "decisions": len(decisions),
        "bull_decisions": sum(1 for d in decisions if d["verdict"] == "BULL"),
        "bear_decisions": sum(1 for d in decisions if d["verdict"] == "BEAR"),
        "neutral_decisions": sum(1 for d in decisions if d["verdict"] == "NEUTRAL"),
        "avg_conviction": round(sum(d["conviction"] for d in decisions) / len(decisions), 2) if decisions else None,
        "equity_curve": equity,
        "trades_log": trades,
        "decisions_log": decisions,
        "isolated": True,
    }
    if mode == "backtest":
        result["snapshots"] = snapshots
        result["metrics"] = _backtest_metrics(snapshots)
    return result


def _correct(verdict: str | None, ref: float, fwd: dict[str, float | None]) -> int | None:
    forward = fwd.get("p30") or fwd.get("p15") or fwd.get("p5") or fwd.get("p60")
    if forward is None or not ref:
        return None
    move = forward / ref - 1.0
    if verdict == "BULL":
        return 1 if move > 0 else 0
    if verdict == "BEAR":
        return 1 if move < 0 else 0
    return None


def _backtest_metrics(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    acc = {"bull": [0, 0], "bear": [0, 0]}
    convs: list[float] = []
    fwd_sums: dict[str, list[float]] = {k: [] for k in ("p5", "p15", "p30", "p60")}
    buckets: dict[str, list[float]] = {}
    for s in snapshots:
        c = s.get("conviction") or 0.0
        convs.append(c)
        if s.get("correct") is not None:
            key = "bull" if s["verdict"] == "BULL" else "bear" if s["verdict"] == "BEAR" else None
            if key:
                acc[key][1] += 1
                acc[key][0] += int(s["correct"])
        ref = s.get("reference_price")
        for k in fwd_sums:
            v = s.get("forward", {}).get(k)
            if v and ref:
                fwd_sums[k].append(v / ref - 1.0)
        b = "50-60" if c < 60 else "60-70" if c < 70 else "70-80" if c < 80 else "80-100" if c < 100 else "90-100"
        buckets.setdefault(b, []).append(ref and (s.get("forward", {}).get("p30") or s.get("forward", {}).get("p15")) / ref - 1.0 if ref and (s.get("forward", {}).get("p30") or s.get("forward", {}).get("p15")) else None)

    def _avg(vals: list[float | None]) -> float | None:
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 6) if v else None

    return {
        "bull_accuracy": round(acc["bull"][0] / acc["bull"][1], 4) if acc["bull"][1] else None,
        "bull_n": acc["bull"][1],
        "bear_accuracy": round(acc["bear"][0] / acc["bear"][1], 4) if acc["bear"][1] else None,
        "bear_n": acc["bear"][1],
        "avg_conviction": round(sum(convs) / len(convs), 2) if convs else None,
        "forward_5m": _avg(fwd_sums["p5"]),
        "forward_15m": _avg(fwd_sums["p15"]),
        "forward_30m": _avg(fwd_sums["p30"]),
        "forward_60m": _avg(fwd_sums["p60"]),
        "conviction_buckets": [{**{"bucket": b, "avg_30m": _avg(buckets[b]), "n": len([x for x in buckets[b] if x is not None])}} for b in ("50-60", "60-70", "70-80", "80-100") if buckets.get(b)],
    }


def run(
    db: Database,
    market: str,
    ticker: str,
    period: str = "quick",
    capital: float = 100000.0,
    mode: str = "sim",
    bull_threshold: float = 70.0,
    bear_threshold: float = 70.0,
    size_ratio: float = 0.25,
) -> dict[str, Any]:
    """Entry point shared by FAST SIMULATION and BACKTEST."""
    return _run(db, market, ticker, period, float(capital), float(bull_threshold), float(bear_threshold), float(size_ratio), mode)
