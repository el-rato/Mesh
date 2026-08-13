"""Terminal notification service + market event detector.

Detects significant events over the existing data (market sessions, committee
verdict changes, simulated trades) and records them as notifications with an
explicit severity (INFO / IMPORTANT / HIGH).

Every candidate event has a deterministic key; processed keys are persisted so
repeated polls never duplicate a notification (e.g. a market open fires exactly
once per session).

Events:
* ``market_open``        — a configured market's session has opened (INFO).
* ``committee_change``   — a fresh Committee verdict differs from its previous
                           verdict (IMPORTANT; HIGH on a BULL<->BEAR reversal).
* ``significant_trade``  — a paper trade whose notional is at/above the
                           configured threshold (HIGH), or any LONG<->SHORT
                           reversal regardless of size (HIGH).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

INFO = "INFO"
IMPORTANT = "IMPORTANT"
HIGH = "HIGH"

#: A committee change is only notified when the latest verdict is this fresh
#: (i.e. it was just recomputed); otherwise old history would flood the feed.
_CHANGE_FRESH_WINDOW = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(UTC)


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _emit(
    db: Database,
    key: str,
    severity: str,
    type_: str,
    title: str,
    message: str,
    security_id: str = "",
    market: str = "",
    ticker: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Mark the key processed and record the event once. Returns the event."""
    db.mark_notification_processed(key)
    import json

    inserted = db.insert_notification_event(
        key, severity, type_, title, message, security_id, market, ticker,
        json.dumps(payload or {}),
    )
    if not inserted:
        return None
    return {
        "event_key": key,
        "severity": severity,
        "type": type_,
        "title": title,
        "message": message,
        "security_id": security_id,
        "market": market,
        "ticker": ticker,
        "payload": payload or {},
    }


# ---------------------------------------------------------------------------
# Market open detection (once per market session)
# ---------------------------------------------------------------------------


def _market_open_events(db: Database, now: datetime) -> list[dict[str, Any]]:
    from .markets import enabled_market_codes, load_markets, market_status

    enabled = set(enabled_market_codes())
    events: list[dict[str, Any]] = []
    for market in load_markets(settings.markets_dir).values():
        if market.code not in enabled:
            continue
        status = market_status(market, now)
        if status["status"] != "open":
            continue
        key = f"market_open:{market.code}:{status['local_date']}"
        if db.is_notification_processed(key):
            continue
        event = _emit(
            db,
            key,
            INFO,
            "market_open",
            f"MARKET OPEN - {market.code}",
            f"{market.name} opened at {status['opened_at']} local time "
            f"({status['timezone']}). Market is now open.",
            market=market.code,
            payload={"market": market.code, "opened_at": status["opened_at"]},
        )
        if event:
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# Committee change detection
# ---------------------------------------------------------------------------


def _committee_change_events(db: Database, now: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cutoff = (now - _CHANGE_FRESH_WINDOW).isoformat()
    pairs = db.verdict_pairs()
    for (market, ticker), (latest, previous) in pairs.items():
        if latest is None or previous is None:
            continue
        if latest["verdict"] == previous["verdict"]:
            continue
        # Only a freshly recomputed verdict is a "change"; old rows are history.
        if latest.get("decided_at", "") < cutoff:
            continue
        prev_v = str(previous["verdict"])
        cur_v = str(latest["verdict"])
        decided_at = str(latest["decided_at"])
        sec = f"{market}:{ticker}"
        reversal = {prev_v, cur_v} == {"BULL", "BEAR"}
        severity = HIGH if reversal else IMPORTANT
        type_ = "committee_reversal" if reversal else "committee_change"
        title = f"{sec} COMMITTEE {prev_v} → {cur_v}"
        message = (
            f"The Committee view for {sec} changed from {prev_v} to {cur_v} "
            f"(conviction {float(latest.get('confidence') or 0.0) * 100:.0f}%)."
        )
        key = f"committee_change:{sec}:{prev_v}:{cur_v}:{decided_at}"
        if db.is_notification_processed(key):
            continue
        event = _emit(
            db, key, severity, type_, title, message, sec, market, ticker,
            payload={"previous_verdict": prev_v, "verdict": cur_v},
        )
        if event:
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# Significant trade detection
# ---------------------------------------------------------------------------


def _trade_events(db: Database) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    threshold = float(settings.notification_trade_threshold)
    orders = db.paper_orders()
    # Group by security so position transitions can be computed incrementally.
    by_sec: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in orders:
        by_sec.setdefault((o["market"], o["ticker"]), []).append(o)

    for (market, ticker), olist in by_sec.items():
        direction: str | None = None
        prev_start: str | None = None  # direction at the START of the previous order
        qty = 0.0
        for o in olist:
            key = f"trade:{o['order_id']}"
            before = direction
            side = o["side"]
            q = float(o["quantity"])
            if side == "BUY":
                direction, qty = "LONG", qty + q
            elif side == "SHORT":
                direction, qty = "SHORT", qty + q
            elif side == "SELL":
                qty -= q
                if qty <= 1e-9:
                    direction = None
            elif side == "COVER":
                qty -= q
                if qty <= 1e-9:
                    direction = None
            elif side == "CLOSE":
                direction, qty = None, 0.0
            after = direction

            # A reversal is either a single order flipping direction (defensive)
            # or a close-then-open of the opposite side across two orders.
            reversed_ = (
                (before is not None and after is not None and before != after)
                or (
                    before is None
                    and prev_start in ("LONG", "SHORT")
                    and after is not None
                    and after != prev_start
                )
            )
            if reversed_:
                from_dir = before or prev_start
                key = f"trade:{o['order_id']}"
                if db.is_notification_processed(key):
                    prev_start = before
                    continue
                notional = q * float(o["price"])
                sec = f"{market}:{ticker}"
                db.mark_notification_processed(key)
                event = _emit(
                    db, key, HIGH, "position_reversed",
                    f"{sec} REVERSED {from_dir} → {after}",
                    f"Trader reversed {sec} from {from_dir} to {after} "
                    f"({o['side']} {q:,.0f} @ {float(o['price']):,.2f}, notional {_money(notional)}).",
                    sec, market, ticker,
                    payload={"before": from_dir, "after": after, "notional": notional, "side": o["side"]},
                )
                if event:
                    events.append(event)
                prev_start = before
                continue

            if db.is_notification_processed(key):
                prev_start = before
                continue  # already emitted (or already known to be below threshold)

            notional = q * float(o["price"])
            sec = f"{market}:{ticker}"
            if notional < threshold:
                db.mark_notification_processed(key)  # small trades stay silent but seen
                prev_start = before
                continue
            opened = before is None and after is not None
            closed = before is not None and after is None
            verb = "opened" if opened else "closed" if closed else "adjusted"
            db.mark_notification_processed(key)
            event = _emit(
                db, key, HIGH, "significant_trade",
                f"SIGNIFICANT TRADE - {sec}",
                f"Trader {verb} a {after or 'position'} in {sec}: "
                f"{o['side']} {q:,.0f} @ {float(o['price']):,.2f} (notional {_money(notional)}).",
                sec, market, ticker,
                payload={"side": o["side"], "notional": notional, "direction": after},
            )
            if event:
                events.append(event)
            prev_start = before
    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(db: Database, now: datetime | None = None) -> dict[str, Any]:
    """Run the event detectors and persist any new notifications."""
    now = now or _now()
    new_events: list[dict[str, Any]] = []
    for detector in (_market_open_events, _committee_change_events, _trade_events):
        try:
            new_events.extend(detector(db, now) if detector is not _trade_events else detector(db))
        except Exception as exc:
            logger.warning("Notification detector %s failed: %s", detector.__name__, exc)
    return {"new": new_events, "count": len(new_events)}


def recent(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    return db.notifications(limit=limit)


def ack(db: Database, keys: list[str]) -> dict[str, Any]:
    return {"acked": db.ack_notifications(keys)}
