from __future__ import annotations

import logging
import queue
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

# ---------------------------------------------------------------------------
# On-demand analysis "warmer"
#
# The scanner (and dossier) frequently meet securities that have no stored
# analysis yet — every dynamically discovered ticker, plus configured tickers
# whose first pass hit a transient provider error. The periodic refresh cycle
# eventually covers them, but the user is left staring at N/A / "NO DATA" rows
# (and a dossier that has to compute live on every open) in the meantime.
#
# The warmer runs ``live_verdict`` in the background for these securities so
# their data is ready by the time the user clicks through. It is fully
# deduplicated (in-flight + queued) and bounded so a single scanner open can
# never hammer the price/news/LSTM providers.
# ---------------------------------------------------------------------------
_warm_queue: "queue.Queue[dict[str, object]]" = queue.Queue(maxsize=4000)
_warm_pending: set[tuple[str, str]] = set()
_warm_lock = threading.Lock()
_warm_started = False


def _start_warm_workers() -> None:
    global _warm_started
    if _warm_started:
        return
    _warm_started = True
    workers = max(1, min(8, settings.scanner_refresh_batch or 4))
    for _ in range(workers):
        threading.Thread(target=_warm_worker, name="analysis-warmer", daemon=True).start()


def _warm_worker() -> None:
    while True:
        item = _warm_queue.get()
        if item is None:
            _warm_queue.task_done()
            break
        market = str(item["market"])
        ticker = str(item["ticker"])
        company = str(item.get("company") or "")
        yahoo_symbol = item.get("yahoo_symbol") or None
        db_path = item.get("db_path")
        key = (market, ticker.upper())
        try:
            from .resolve import resolve_for_fetch

            resolved = resolve_for_fetch(market, ticker, company)
        except Exception as exc:  # never let one bad symbol kill the worker
            logger.debug("Warm resolve failed for %s:%s: %s", market, ticker, exc)
            resolved = None
        try:
            if resolved:
                if key not in _in_flight:
                    _in_flight.add(key)
                    try:
                        from .verdict import live_verdict

                        live_verdict(
                            market,
                            ticker,
                            company,
                            db_path=db_path,
                            yahoo_symbol=resolved,
                        )
                    except Exception as exc:
                        logger.warning("Warm analysis failed for %s:%s: %s", market, ticker, exc)
                    finally:
                        _in_flight.discard(key)
        finally:
            with _warm_lock:
                _warm_pending.discard(key)
            _warm_queue.task_done()


def enqueue_analysis(
    db_path: str,
    market: str,
    ticker: str,
    company: str = "",
    yahoo_symbol: str | None = None,
) -> bool:
    """Queue a background ``live_verdict`` for a security that lacks data.

    Returns True if it was newly queued, False if it is already queued / running
    or the queue is full. Idempotent and cheap, so callers (e.g. the scanner)
    can invoke it freely on every page load.
    """
    _start_warm_workers()
    key = (market, ticker.upper())
    with _warm_lock:
        if key in _in_flight or key in _warm_pending:
            return False
        if _warm_queue.qsize() >= 4000:
            return False
        _warm_pending.add(key)
    try:
        _warm_queue.put_nowait(
            {
                "market": market,
                "ticker": ticker,
                "company": company,
                "yahoo_symbol": yahoo_symbol,
                "db_path": db_path,
            }
        )
    except Exception:
        with _warm_lock:
            _warm_pending.discard(key)
        return False
    return True


def is_warming(market: str, ticker: str) -> bool:
    """Whether a security is queued or currently being analyzed by the warmer."""
    key = (market, ticker.upper())
    with _warm_lock:
        return key in _in_flight or key in _warm_pending


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
    """Configured markets + watchlist + full universe -> (market, ticker, company, yahoo_symbol)."""
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
    # The dynamic universe (discovered / searched tickers) is otherwise never
    # analyzed: the slow refresh would leave them as permanent N/A / NO_DATA in
    # the scanner and force a live compute on every dossier open. Include them so
    # they are scored on the same periodic cadence as configured markets.
    for sec in db.all_securities():
        mkt = sec.get("market") or ""
        tkr = sec.get("ticker") or ""
        if not mkt or not tkr:
            continue
        suffix = (markets.get(mkt).yahoo_suffix if markets.get(mkt) else "") or ""
        candidates.append((mkt, tkr, sec.get("company") or "", sec.get("symbol") or (tkr + suffix)))
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
