from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: Process-wide refresh state + lock so overlapping refresh cycles are skipped
#: instead of hammering the price/news/LSTM APIs with duplicate requests.
_lock = threading.Lock()
_state: dict[str, object] = {
    "running": False,
    "last_fast_at": None,
    "last_slow_at": None,
    "last_error": "",
}
_in_flight: set[tuple[str, str]] = set()


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def refresh_status() -> dict[str, object]:
    now = time.time()
    last_fast = float(_state.get("last_fast_at") or 0)
    last_slow = float(_state.get("last_slow_at") or 0)
    return {
        "running": bool(_state.get("running")),
        "last_fast_at": _iso(last_fast),
        "last_slow_at": _iso(last_slow),
        "next_fast_in": max(0, settings.scanner_refresh_fast - int(now - last_fast)),
        "next_slow_in": max(0, settings.scanner_refresh_slow - int(now - last_slow)),
        "error": str(_state.get("last_error") or ""),
    }


def _candidates(db: Database) -> list[tuple[str, str, str, str]]:
    """Configured markets + watchlist -> (market, ticker, company, yahoo_symbol)."""
    from .markets import load_markets, scan_market_codes

    markets = load_markets(settings.markets_dir)
    candidates: list[tuple[str, str, str, str]] = []
    for code in scan_market_codes():
        market = markets.get(code)
        if not market:
            continue
        for sym, tkr in market.tickers.items():
            candidates.append((code, sym, tkr.name or "", sym + (tkr.yahoo_suffix or market.yahoo_suffix)))
    for item in db.watchlist():
        market = markets.get(item["market"])
        suffix = market.yahoo_suffix if market else ""
        candidates.append((item["market"], item["ticker"], item.get("company") or "", item["ticker"] + suffix))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str, str]] = []
    for candidate in candidates:
        key = (candidate[0], candidate[1].upper())
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def run_fast_refresh(db: Database) -> dict[str, object]:
    """Fetch current prices and store lightweight price snapshots (no LSTM/news)."""
    from .price import run_price_fetch
    from .markets import scan_market_codes

    states = run_price_fetch(market_codes=scan_market_codes(), db_path=str(db.path))
    return {"prices": len(states)}


def run_slow_refresh(db: Database) -> dict[str, object]:
    """Re-run the full verdict pipeline for stale symbols only (LSTM + news)."""
    from .resolve import resolve_for_fetch
    from .verdict import live_verdict

    cutoff = (datetime.now(UTC) - timedelta(seconds=settings.scanner_refresh_slow)).isoformat()
    latest = {(r["market"], r["ticker"].upper()): r for r in db.latest_verdicts()}
    stale: list[tuple[str, str, str, str]] = []
    for market, ticker, company, ysymbol in _candidates(db):
        row = latest.get((market, ticker.upper()))
        if row is None or row.get("decided_at", "") < cutoff:
            stale.append((market, ticker, company, ysymbol))

    analyzed = failed = skipped = 0
    for market, ticker, company, ysymbol in stale[: settings.scanner_refresh_batch]:
        key = (market, ticker.upper())
        if key in _in_flight:
            continue
        # Validate the symbol first so expensive LSTM/news work is never run
        # against known-invalid or temporarily unavailable securities.
        resolved = resolve_for_fetch(market, ticker, company)
        if not resolved:
            skipped += 1
            continue
        _in_flight.add(key)
        try:
            verdict = live_verdict(market, ticker, company, yahoo_symbol=resolved)
            if verdict is not None:
                analyzed += 1
        except Exception as exc:
            logger.warning("Slow refresh failed for %s:%s: %s", market, ticker, exc)
            failed += 1
        finally:
            _in_flight.discard(key)
    return {
        "analyzed": analyzed,
        "failed": failed,
        "skipped": skipped,
        "pending": max(0, len(stale) - settings.scanner_refresh_batch),
    }


def run_refresh(db: Database) -> dict[str, object]:
    """Run the refresh cycle. Fast refresh always when due; slow refresh only
    when its interval has elapsed. Never runs concurrently with itself."""
    acquired = _lock.acquire(blocking=False)
    if not acquired or _state.get("running"):
        if acquired:
            _lock.release()
        return {**refresh_status(), "skipped": True}
    _state["running"] = True
    try:
        now = time.time()
        last_fast = _state.get("last_fast_at")
        last_slow = _state.get("last_slow_at")
        fast_due = last_fast is None or (now - float(last_fast)) >= settings.scanner_refresh_fast
        slow_due = last_slow is None or (now - float(last_slow)) >= settings.scanner_refresh_slow

        if fast_due:
            try:
                run_fast_refresh(db)
                _state["last_fast_at"] = now
            except Exception as exc:
                _state["last_error"] = f"fast refresh: {exc}"
                logger.warning("Fast refresh failed: %s", exc)
        if slow_due:
            try:
                run_slow_refresh(db)
                _state["last_slow_at"] = now
            except Exception as exc:
                _state["last_error"] = f"slow refresh: {exc}"
                logger.warning("Slow refresh failed: %s", exc)
        # Market event detection rides the existing refresh cadence (idempotent
        # per deterministic event keys).
        try:
            from .notifications import scan as scan_notifications

            scan_notifications(db)
        except Exception as exc:
            logger.warning("Notification scan failed: %s", exc)
    finally:
        _state["running"] = False
        _lock.release()
    return refresh_status()
