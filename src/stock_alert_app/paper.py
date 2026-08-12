"""Paper research engine — decision snapshots, historical evaluation, and a
simulated intraday paper portfolio.

PAPER TRADING ONLY. No broker integration, no real orders, no real money.
Every order is a simulation recorded in the local database.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

_HORIZONS = (5, 15, 30, 60)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_decision_id() -> str:
    return f"DEC-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"


def _new_order_id() -> str:
    return f"ORD-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"


# ---------------------------------------------------------------------------
# Decision snapshots (immutable, append-only)
# ---------------------------------------------------------------------------


def record_decision_snapshot(
    db: Database, market: str, ticker: str, vdict: dict[str, Any]
) -> str | None:
    """Persist an immutable snapshot of a completed CommitteeDecision.

    Called from the single analysis path (live_verdict). Returns the new
    decision_id or None if a snapshot for this exact timestamp already exists.
    """
    from .dossier import committee_decision

    decision = committee_decision(vdict)
    verdict = decision.get("verdict")
    if verdict in (None, "N/A"):
        return None
    decided_at = decision.get("decision_timestamp") or _now_iso()
    price = (vdict.get("price") or {})
    reference_price = price.get("close")
    decision_id = _new_decision_id()
    inserted = db.insert_decision_snapshot(
        decision_id=decision_id,
        market=market,
        ticker=ticker,
        decided_at=decided_at,
        verdict=verdict,
        conviction=decision.get("conviction"),
        reference_price=reference_price,
        research_confidence=decision.get("research_confidence"),
        decision_json=json.dumps(decision),
    )
    return decision_id if inserted else None


# ---------------------------------------------------------------------------
# Historical evaluation (post-decision prices only, no look-ahead)
# ---------------------------------------------------------------------------


def _bar_time(bar: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(bar.get("date", ""))
    except (ValueError, TypeError):
        return None


def _symbol_for(db: Database, market: str, ticker: str) -> str:
    sec = db.securities_map().get((market, ticker.upper()))
    if sec and sec.get("symbol"):
        return sec["symbol"]
    from .markets import load_markets

    m = load_markets(settings.markets_dir).get(market)
    return f"{ticker}{m.yahoo_suffix if m else ''}"


def evaluate_snapshot(snapshot: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure a decision against the FIRST valid price after its timestamp.

    Only bars strictly after ``decided_at`` are used (no look-ahead). Forward
    prices at 5/15/30/60 minutes and end-of-session close. If no reference bar
    is available, the evaluation is NO_DATA.
    """
    try:
        decision_time = datetime.fromisoformat(snapshot["decided_at"])
    except (ValueError, TypeError):
        decision_time = None
    if decision_time is None:
        return {"status": "no_data", "reference_price": None, "correct": None}

    times = [_bar_time(b) for b in bars]
    ref_idx = next(
        (i for i, t in enumerate(times) if t is not None and t > decision_time), None
    )
    if ref_idx is None:
        return {"status": "no_data", "reference_price": None, "correct": None}

    reference = float(bars[ref_idx]["close"])
    prices: dict[str, float | None] = {"p5": None, "p15": None, "p30": None, "p60": None, "close": None}
    for h in _HORIZONS:
        idx = next(
            (
                i
                for i in range(ref_idx, len(times))
                if times[i] is not None and times[i] >= decision_time + timedelta(minutes=h)
            ),
            None,
        )
        if idx is not None:
            prices[f"p{h}"] = float(bars[idx]["close"])
    if bars:
        prices["close"] = float(bars[-1]["close"])

    # Longest available forward price for directional correctness.
    forward = next((prices[f"p{h}"] for h in (60, 30, 15, 5) if prices[f"p{h}"] is not None), prices["close"])
    correct = None
    verdict = snapshot.get("verdict")
    if forward is not None and reference:
        move = forward / reference - 1.0
        if verdict == "BULL":
            correct = 1 if move > 0 else 0
        elif verdict == "BEAR":
            correct = 1 if move < 0 else 0

    return {
        "status": "ok",
        "reference_price": round(reference, 6),
        "prices": {k: round(v, 6) if v is not None else None for k, v in prices.items()},
        "correct": correct,
    }


def refresh_evaluations(db: Database, force: bool = False) -> dict[str, Any]:
    """Evaluate recent snapshots once and cache the results. Does not rerun
    evaluation for already-evaluated decisions unless forced."""
    from .indexes import index_history

    evaluated = db.decision_evaluations()
    done = no_data = 0
    for snapshot in db.decision_snapshots(limit=200):
        if snapshot["decision_id"] in evaluated and not force:
            continue
        try:
            symbol = _symbol_for(db, snapshot["market"], snapshot["ticker"])
            bars = index_history(symbol, "1d")
            result = evaluate_snapshot(snapshot, bars)
        except Exception as exc:
            logger.warning("Evaluation failed for %s: %s", snapshot["decision_id"], exc)
            continue
        if result["status"] == "ok":
            db.insert_decision_evaluation(
                snapshot["decision_id"],
                result["reference_price"],
                result["prices"],
                result["correct"],
                "ok",
            )
            done += 1
        else:
            no_data += 1
    return {"evaluated": done, "no_data": no_data}


# ---------------------------------------------------------------------------
# Paper portfolio (simulation only)
# ---------------------------------------------------------------------------


def ensure_session(db: Database) -> dict[str, Any]:
    session = db.active_portfolio()
    if session is None:
        session_id = f"SESS-{int(time.time())}"
        db.upsert_paper_portfolio(session_id, settings.paper_starting_cash)
        session = db.active_portfolio()
    return session


def _execution_price(db: Database, symbol: str, market: str, ticker: str) -> float | None:
    """Latest available market price (intraday bar first, then stored snapshot)."""
    try:
        from .indexes import index_history

        bars = index_history(symbol, "1d")
        if bars:
            return float(bars[-1]["close"])
    except Exception:
        pass
    snap = None
    try:
        snap = db.latest_price_snapshot(market, ticker)
    except Exception:
        snap = None
    if snap and snap.get("close"):
        return float(snap["close"])
    return None


def paper_order(
    db: Database,
    market: str,
    ticker: str,
    side: str,
    quantity: float,
    decision_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Execute a simulated order at the latest available valid price.

    Long/short actions: BUY (open/increase long), SELL (reduce/close long),
    SHORT (open/increase short), COVER (reduce/close short), CLOSE (auto-maps to
    SELL/COVER). Direction reversal is rejected — close the current side first.

    Simulation assumptions (labelled): slippage on the execution price, a flat
    commission per order, short positions require ``paper_short_margin`` of the
    short value as margin, and gross exposure is capped by ``paper_max_gross_ratio``.
    NO_DATA (LookupError) if no valid price is available.
    """
    side = side.upper()
    if side not in ("BUY", "SELL", "SHORT", "COVER", "CLOSE"):
        raise ValueError(f"invalid side {side!r}")
    quantity = float(quantity)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("quantity must be a positive number")

    session = ensure_session(db)
    symbol = _symbol_for(db, market, ticker)
    price = _execution_price(db, symbol, market, ticker)
    if price is None or price <= 0 or not math.isfinite(price):
        raise LookupError(f"no valid execution price available for {market}:{ticker}")

    price = price * (1.0 + settings.paper_slippage)
    fee = settings.paper_commission
    ticker = ticker.upper()

    orders = db.paper_orders(session["session_id"])
    current = positions_from_orders(orders)
    held = current.get((market, ticker), {"direction": None, "qty": 0.0, "entry": 0.0, "realized": 0.0})

    if side == "CLOSE":
        if not held["direction"]:
            raise ValueError("no position to close")
        side = "SELL" if held["direction"] == "LONG" else "COVER"
        quantity = held["qty"]

    # Validate against the projected position before inserting anything.
    projected = positions_from_orders(
        orders
        + [
            {
                "side": side, "quantity": quantity, "price": price, "fee": fee,
                "market": market, "ticker": ticker,
            }
        ]
    )
    # Margin / cash checks (simulation assumptions).
    starting = float(session["starting_cash"])
    cash = _cash_from_orders(starting, orders + [{"side": side, "quantity": quantity, "price": price, "fee": fee}])
    if cash < -1e-6:
        raise ValueError("insufficient cash for this simulated order")
    gross = sum(p["qty"] * price for p in projected.values())
    equity = cash + _net_market_value(projected, {})
    if gross > settings.paper_max_gross_ratio * max(equity, 1.0):
        raise ValueError("order would exceed the simulated gross exposure limit")
    short_value = sum(
        p["qty"] * price for p in projected.values() if p["direction"] == "SHORT"
    )
    if cash < settings.paper_short_margin * short_value - 1e-6:
        raise ValueError("insufficient margin for the simulated short position")

    order = {
        "order_id": _new_order_id(),
        "session_id": session["session_id"],
        "market": market,
        "ticker": ticker,
        "side": side,
        "quantity": round(quantity, 6),
        "price": round(price, 6),
        "fee": round(fee, 4),
        "executed_at": _now_iso(),
        "decision_id": decision_id,
        "reason": reason,
        "direction": "SHORT" if side in ("SHORT", "COVER") else "LONG",
        "realized_pnl": 0.0,
    }
    db.insert_paper_order(
        order["order_id"], order["session_id"], market, ticker, side,
        order["quantity"], order["price"], order["fee"], order["executed_at"],
        decision_id, reason,
    )
    return order


def positions_from_orders(orders: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Derive LONG/SHORT positions (weighted-average cost basis) + realized P&L.

    Raises on an impossible sequence (e.g. SELL more than held) so invalid
    simulated orders are never silently created.
    """
    pos: dict[tuple[str, str], dict[str, Any]] = {}
    for o in orders:
        key = (o["market"], o["ticker"].upper())
        p = pos.setdefault(key, {"direction": None, "qty": 0.0, "entry": 0.0, "realized": 0.0})
        side = o["side"]
        if side == "CLOSE":
            continue
        q, pr, fee = float(o["quantity"]), float(o["price"]), float(o["fee"])
        if side == "BUY":
            if p["direction"] == "SHORT":
                raise ValueError(f"cannot BUY {key} while SHORT — cover first")
            p["direction"] = "LONG"
            cost = p["entry"] * p["qty"] + pr * q
            p["qty"] += q
            p["entry"] = cost / p["qty"] if p["qty"] else 0.0
        elif side == "SELL":
            if p["direction"] != "LONG" or p["qty"] < q - 1e-9:
                raise ValueError(f"cannot SELL {q} of {key} — long {p['qty']}")
            p["realized"] += (pr - p["entry"]) * q - fee
            p["qty"] -= q
            if p["qty"] <= 1e-9:
                p.update(direction=None, qty=0.0, entry=0.0)
        elif side == "SHORT":
            if p["direction"] == "LONG":
                raise ValueError(f"cannot SHORT {key} while LONG — close long first")
            p["direction"] = "SHORT"
            cost = p["entry"] * p["qty"] + pr * q
            p["qty"] += q
            p["entry"] = cost / p["qty"] if p["qty"] else 0.0
        elif side == "COVER":
            if p["direction"] != "SHORT" or p["qty"] < q - 1e-9:
                raise ValueError(f"cannot COVER {q} of {key} — short {p['qty']}")
            p["realized"] += (p["entry"] - pr) * q - fee
            p["qty"] -= q
            if p["qty"] <= 1e-9:
                p.update(direction=None, qty=0.0, entry=0.0)
    for p in pos.values():
        p["qty"] = round(p["qty"], 6)
    return pos


def _cash_from_orders(starting: float, orders: list[dict[str, Any]]) -> float:
    cash = starting
    for o in orders:
        q, pr, fee = float(o["quantity"]), float(o["price"]), float(o["fee"])
        if o["side"] == "BUY":
            cash -= q * pr + fee
        elif o["side"] in ("SELL", "CLOSE"):
            cash += q * pr - fee
        elif o["side"] == "SHORT":
            cash += q * pr - fee
        elif o["side"] == "COVER":
            cash -= q * pr + fee
    return cash


def _net_market_value(
    positions: dict[tuple[str, str], dict[str, Any]],
    prices: dict[tuple[str, str], float],
) -> float:
    """Long market value minus short market value (equity contribution)."""
    net = 0.0
    for key, p in positions.items():
        if p["qty"] <= 0:
            continue
        cur = prices.get(key) or p["entry"]
        if p["direction"] == "LONG":
            net += cur * p["qty"]
        elif p["direction"] == "SHORT":
            net -= cur * p["qty"]
    return net


def portfolio_state(db: Database, record_equity: bool = True) -> dict[str, Any]:
    """Current simulated portfolio: cash, exposures, positions, P&L, value.

    Position prices use the last stored price snapshot (lightweight; refreshed
    by the automatic refresh cycle). No look-ahead / fabricated prices.
    """
    session = ensure_session(db)
    orders = db.paper_orders(session["session_id"])
    positions = positions_from_orders(orders)
    prices: dict[tuple[str, str], float] = {}
    as_of: dict[tuple[str, str], str] = {}
    for key, p in positions.items():
        if p["qty"] <= 0:
            continue
        snap = db.latest_price_snapshot(key[0], key[1])
        if snap and snap.get("close"):
            prices[key] = float(snap["close"])
            as_of[key] = snap["fetched_at"]

    long_value = sum(p["qty"] * prices.get(key, p["entry"]) for key, p in positions.items() if p["direction"] == "LONG" and p["qty"] > 0)
    short_value = sum(p["qty"] * prices.get(key, p["entry"]) for key, p in positions.items() if p["direction"] == "SHORT" and p["qty"] > 0)
    gross = long_value + short_value
    net = long_value - short_value

    unrealized = 0.0
    position_list: list[dict[str, Any]] = []
    for key, p in positions.items():
        if p["qty"] <= 0:
            continue
        cur = prices.get(key, p["entry"])
        if p["direction"] == "LONG":
            upnl = (cur - p["entry"]) * p["qty"]
        else:
            upnl = (p["entry"] - cur) * p["qty"]
        unrealized += upnl
        position_list.append(
            {
                "market": key[0],
                "ticker": key[1],
                "security_id": f"{key[0]}:{key[1]}",
                "direction": p["direction"],
                "qty": p["qty"],
                "entry": round(p["entry"], 6),
                "price": round(cur, 6),
                "value": round(cur * p["qty"], 2),
                "unrealized": round(upnl, 2),
                "pnl_pct": round(upnl / (p["entry"] * p["qty"]) * 100, 4) if p["entry"] else 0.0,
                "realized": round(p["realized"], 2),
                "as_of": as_of.get(key, ""),
            }
        )

    cash = _cash_from_orders(float(session["starting_cash"]), orders)
    realized_total = sum(p["realized"] for p in positions.values())
    total_pnl = realized_total + unrealized
    equity = cash + net
    day_pct = (total_pnl / float(session["starting_cash"]) * 100) if session["starting_cash"] else 0.0

    if record_equity:
        _record_equity_point(db, session["session_id"], equity)

    return {
        "session_id": session["session_id"],
        "starting_cash": float(session["starting_cash"]),
        "currency": session.get("currency") or "USD",
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "long_value": round(long_value, 2),
        "short_value": round(short_value, 2),
        "gross_exposure": round(gross, 2),
        "net_exposure": round(net, 2),
        "realized_pnl": round(realized_total, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": round(total_pnl, 2),
        "day_pct": round(day_pct, 4),
        "open_positions": len(position_list),
        "trades_today": len([o for o in orders]),
        "positions": position_list,
    }


def _record_equity_point(db: Database, session_id: str, equity: float) -> None:
    """Record an equity point at most once per 60 seconds per session."""
    import time as _t

    last = db.last_equity_at(session_id)
    if last:
        try:
            from datetime import datetime as _dt

            if (_dt.now(UTC) - _dt.fromisoformat(last)).total_seconds() < 60:
                return
        except (ValueError, TypeError):
            pass
    db.insert_equity_point(session_id, round(equity, 2))


def quote(db: Database, market: str, ticker: str) -> dict[str, Any]:
    """Lightweight simulated quote (latest intraday bar, else stored snapshot)."""
    symbol = _symbol_for(db, market, ticker)
    price = _execution_price(db, symbol, market, ticker)
    if price is None:
        return {
            "market": market, "ticker": ticker.upper(), "symbol": symbol,
            "security_id": f"{market}:{ticker.upper()}", "price": None, "status": "no_data",
        }
    return {
        "market": market, "ticker": ticker.upper(), "symbol": symbol,
        "security_id": f"{market}:{ticker.upper()}", "price": round(price, 6), "status": "ok",
    }


def realized_per_order(orders: list[dict[str, Any]]) -> dict[str, float]:
    """Realized P&L attributed to each closing order (SELL/COVER)."""
    pos: dict[tuple[str, str], dict[str, Any]] = {}
    out: dict[str, float] = {}
    for o in orders:
        key = (o["market"], o["ticker"].upper())
        p = pos.setdefault(key, {"direction": None, "qty": 0.0, "entry": 0.0})
        side, q, pr, fee = o["side"], float(o["quantity"]), float(o["price"]), float(o["fee"])
        if side == "BUY":
            p["direction"] = "LONG"
            p["entry"] = (p["entry"] * p["qty"] + pr * q) / (p["qty"] + q)
            p["qty"] += q
        elif side == "SHORT":
            p["direction"] = "SHORT"
            p["entry"] = (p["entry"] * p["qty"] + pr * q) / (p["qty"] + q)
            p["qty"] += q
        elif side == "SELL":
            out[o["order_id"]] = (pr - p["entry"]) * q - fee
            p["qty"] -= q
            if p["qty"] <= 1e-9:
                p.update(direction=None, qty=0.0, entry=0.0)
        elif side == "COVER":
            out[o["order_id"]] = (p["entry"] - pr) * q - fee
            p["qty"] -= q
            if p["qty"] <= 1e-9:
                p.update(direction=None, qty=0.0, entry=0.0)
    return out


def stats(db: Database) -> dict[str, Any]:
    """Intraday trade statistics from realized P&L (min sample size enforced)."""
    session = ensure_session(db)
    orders = db.paper_orders(session["session_id"])
    rp = realized_per_order(orders)
    realized = [v for v in rp.values() if abs(v) > 1e-9]
    wins = [v for v in realized if v > 0]
    losses = [v for v in realized if v < 0]
    n = len(realized)
    result: dict[str, Any] = {
        "trades": n,
        "long_pnl": 0.0,
        "short_pnl": 0.0,
        "best": None, "worst": None, "win_rate": None,
        "avg_win": None, "avg_loss": None, "profit_factor": None,
    }
    # Attribute realized P&L to the closing side.
    for o in orders:
        rid = o.get("order_id")
        if rid in rp:
            if o["side"] == "SELL":
                result["long_pnl"] += rp[rid]
            elif o["side"] == "COVER":
                result["short_pnl"] += rp[rid]
    result["long_pnl"] = round(result["long_pnl"], 2)
    result["short_pnl"] = round(result["short_pnl"], 2)
    if n < max(2, settings.paper_min_stats or 3):
        return result  # too few observations — do not show meaningless stats
    result["best"] = round(max(realized), 2)
    result["worst"] = round(min(realized), 2)
    result["win_rate"] = round(len(wins) / n, 4)
    result["avg_win"] = round(sum(wins) / len(wins), 2) if wins else None
    result["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    result["profit_factor"] = round(gross_win / gross_loss, 4) if gross_loss else (None if not wins else float("inf"))
    return result


def risk(db: Database) -> dict[str, Any]:
    """Simple portfolio risk view + limit warnings (simulation assumptions)."""
    state = portfolio_state(db, record_equity=False)
    positions = state["positions"]
    equity = state["equity"]
    gross = state["gross_exposure"]
    largest = max(positions, key=lambda p: p["value"]) if positions else None
    warnings: list[str] = []
    if equity > 0 and gross > settings.paper_max_gross_ratio * equity:
        warnings.append(f"Gross exposure ({gross:.0f}) exceeds {settings.paper_max_gross_ratio:.1f}x equity")
    if largest and equity > 0:
        pct = largest["value"] / equity
        if pct > settings.paper_max_position_ratio:
            warnings.append(f"{largest['ticker']} is {pct:.0%} of equity (limit {settings.paper_max_position_ratio:.0%})")
    return {
        "gross_exposure": gross,
        "net_exposure": state["net_exposure"],
        "long_exposure": state["long_value"],
        "short_exposure": state["short_value"],
        "largest_position": largest,
        "largest_position_pct": round(largest["value"] / equity, 4) if largest and equity else None,
        "concentration": round(max((p["value"] for p in positions), default=0.0) / max(gross, 1.0), 4) if positions else 0.0,
        "warnings": warnings,
    }


def equity_history(db: Database) -> list[dict[str, Any]]:
    session = ensure_session(db)
    return db.equity_points(session["session_id"])


def leaderboard(db: Database) -> dict[str, Any]:
    """Simulated leaderboard: the local player plus clearly-labelled demo accounts.

    Demo competitors are deterministic, simulated accounts — never real users.
    """
    player = portfolio_state(db, record_equity=True)
    rows = [
        {
            "rank": 1,
            "name": "You",
            "is_demo": False,
            "equity": player["equity"],
            "return": round((player["equity"] - player["starting_cash"]) / player["starting_cash"] * 100, 2) if player["starting_cash"] else 0.0,
            "positions": player["open_positions"],
            "trades": player["trades_today"],
        }
    ]
    if settings.paper_demo_players:
        import hashlib

        seed = int(hashlib.sha256((player["session_id"] + settings.paper_session_end).encode()).hexdigest(), 16)
        for i, name in enumerate(("Demo Alpha", "Demo Beta", "Demo Gamma")):
            r = ((seed >> (i * 11)) % 1000) / 1000.0 * 12.0 - 2.0  # -2%..+10%
            rows.append(
                {
                    "rank": i + 2,
                    "name": name,
                    "is_demo": True,
                    "equity": round(player["starting_cash"] * (1 + r / 100.0), 2),
                    "return": round(r, 2),
                    "positions": None,
                    "trades": None,
                }
            )
    rows.sort(key=lambda r: r["equity"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"rows": rows, "demo_label": "DEMO COMPETITORS ARE SIMULATED ACCOUNTS — NOT REAL USERS."}


def end_session(db: Database) -> dict[str, Any]:
    """Liquidate all open positions at the final valid market price and return
    the session result (intraday simulation: no overnight positions)."""
    session = ensure_session(db)
    orders = db.paper_orders(session["session_id"])
    positions = positions_from_orders(orders)
    closed: list[dict[str, Any]] = []
    for (market, ticker), p in positions.items():
        if p["qty"] <= 0:
            continue
        side = "SELL" if p["direction"] == "LONG" else "COVER"
        try:
            order = paper_order(db, market, ticker, side, p["qty"], reason="session close")
        except (ValueError, LookupError):
            continue
        closed.append({"market": market, "ticker": ticker, "side": order["side"], "qty": order["quantity"], "price": order["price"]})
    final = portfolio_state(db, record_equity=True)
    final["closed_positions"] = closed
    return final


# ---------------------------------------------------------------------------
# Performance / research quality (derived from stored snapshots + evaluations)
# ---------------------------------------------------------------------------


def _conviction_bucket(conviction: float | None) -> str | None:
    if conviction is None:
        return None
    c = conviction * 100.0
    if c < 50:
        return "0-50"
    if c < 60:
        return "50-60"
    if c < 70:
        return "60-70"
    if c < 80:
        return "70-80"
    return "80-100"


def performance(db: Database) -> dict[str, Any]:
    snapshots = db.decision_snapshots(limit=1000)
    evaluations = db.decision_evaluations()

    bulls = bears = neut = evaluated_count = 0
    correct_count = 0
    correct_total = 0
    bucket_returns: dict[str, list[float]] = {}
    agreement_returns: dict[str, list[float]] = {}
    research_conf: list[float] = []
    coverage_sum = 0
    coverage_n = 0

    for snap in snapshots:
        verdict = snap.get("verdict")
        if verdict == "BULL":
            bulls += 1
        elif verdict == "BEAR":
            bears += 1
        else:
            neut += 1
        ev = evaluations.get(snap["decision_id"])
        if not ev or ev.get("status") != "ok" or not ev.get("reference_price"):
            continue
        evaluated_count += 1
        if ev.get("correct") is not None:
            correct_total += 1
            correct_count += int(ev["correct"])
        forward = ev.get("p30") or ev.get("p15") or ev.get("p60") or ev.get("p5")
        if forward and ev.get("reference_price"):
            ret = forward / ev["reference_price"] - 1.0
            bucket = _conviction_bucket(snap.get("conviction"))
            if bucket:
                bucket_returns.setdefault(bucket, []).append(ret)
            agreement = _agreement_key(snap)
            if agreement:
                agreement_returns.setdefault(agreement, []).append(ret)

        try:
            decision = json.loads(snap.get("decision_json") or "{}")
        except (ValueError, TypeError):
            decision = {}
        if snap.get("research_confidence") is not None:
            research_conf.append(float(snap["research_confidence"]))
        research = decision.get("research") or {}
        provenance = research.get("provenance") or []
        coverage_sum += len(provenance)
        coverage_n += 1

    def _stats(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"avg": None, "n": 0}
        return {"avg": round(sum(values) / len(values), 6), "n": len(values)}

    bucket_table = []
    for label in ("0-50", "50-60", "60-70", "70-80", "80-100"):
        if label in bucket_returns:
            bucket_table.append({"bucket": label, **_stats(bucket_returns[label])})
    agreement_table = [
        {"agreement": k, **_stats(v)}
        for k, v in sorted(agreement_returns.items(), key=lambda kv: len(kv[1]), reverse=True)
    ]

    return {
        "decisions": len(snapshots),
        "bull": bulls,
        "bear": bears,
        "neutral": neut,
        "evaluated": evaluated_count,
        "directional_accuracy": round(correct_count / correct_total, 4) if correct_total else None,
        "conviction_buckets": bucket_table,
        "agreement_returns": agreement_table,
        "research_confidence_avg": round(sum(research_conf) / len(research_conf), 4) if research_conf else None,
        "research_coverage_avg": round(coverage_sum / coverage_n, 2) if coverage_n else None,
        "research_n": coverage_n,
    }


def _agreement_key(snapshot: dict[str, Any]) -> str | None:
    """Signal agreement bucket (e.g. '4/5') from the stored decision signals."""
    try:
        decision = json.loads(snapshot.get("decision_json") or "{}")
    except (ValueError, TypeError):
        return None
    signals = decision.get("signals") or {}
    verdict = snapshot.get("verdict")
    if not signals or verdict not in ("BULL", "BEAR"):
        return None
    aligned = 0
    total = 0
    for s in signals.values():
        if s.get("status") != "AVAILABLE":
            continue
        total += 1
        if s.get("direction") == verdict:
            aligned += 1
    if total == 0:
        return None
    return f"{aligned}/{total}"
