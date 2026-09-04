"""Production background worker / scheduler.

Runs the refresh pipeline on a fixed cadence so the API process stays light and
stateless-friendly (API + worker share the database; either can restart without
affecting the other):

* fast loop  — price snapshots + technicals (STOCK_ALERT_REFRESH_FAST, default 300s)
* slow loop  — LSTM / news / 13F refresh (STOCK_ALERT_REFRESH_SLOW, default 1800s)
* notifications scan — deterministic event keys, safe to repeat (every cycle)
* decision evaluations — committee performance measurement (throttled)

Isolation rules:
* Every task is wrapped: one failing provider/task degrades only that task.
* WORKER_MANAGED=1 tells the API to skip its in-process refresh top-ups.
* SIGINT / SIGTERM shut the loop down gracefully between tasks.

CLI:
    python -m stock_alert_app.worker            # run forever
    python -m stock_alert_app.worker --once     # one cycle, then exit (smoke/CI)
"""

from __future__ import annotations

import argparse
import logging
import socket
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any, Callable

from .config import settings
from .db import Database

logger = logging.getLogger("worker")

_stop = {"flag": False}


def _request_stop(signum: int, frame: Any) -> None:  # pragma: no cover - signal path
    logger.warning("received signal %s — finishing current task, then exiting", signum)
    _stop["flag"] = True


def _install_signal_handlers() -> None:
    for sig in (getattr(signal, "SIGTERM", None), signal.SIGINT):
        if sig is None:
            continue
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass


def _run_task(name: str, fn: Callable[[], Any], db: Database) -> None:
    started = time.monotonic()
    try:
        result = fn()
        elapsed = time.monotonic() - started
        summary = ""
        if isinstance(result, dict):
            interesting = {
                k: result[k]
                for k in ("fetched", "evaluated", "no_data", "count", "fast_refresh", "slow_refresh", "error")
                if k in result
            }
            summary = f" {interesting}" if interesting else ""
        logger.info("task %s ok in %.1fs%s", name, elapsed, summary)
    except Exception:
        # A failing task never kills the worker: the capability degrades, the
        # loop continues and the task is retried next cycle.
        logger.exception("task %s failed", name)


def run_forever(once: bool = False) -> int:
    setup_ok = True
    settings.validate_runtime()
    settings.ensure_dirs()
    # Bound any provider library without an explicit timeout (e.g. feedparser).
    socket.setdefaulttimeout(settings.http_timeout_s)
    from .logging_setup import setup_logging

    setup_logging()

    db = Database(settings.db_path)
    db.init_schema()

    from . import notifications, paper, refresh

    fast_interval = max(30, settings.scanner_refresh_fast)
    slow_interval = max(60, settings.scanner_refresh_slow)
    eval_every = max(1, slow_interval // max(fast_interval, 1))

    logger.info(
        "worker started: env=%s db=%s fast=%ss slow=%ss",
        settings.environment, settings.db_path, fast_interval, slow_interval,
    )

    cycle = 0
    last_slow = -slow_interval  # run the slow loop on the first cycle
    while not _stop["flag"]:
        cycle_start = time.monotonic()
        cycle += 1
        try:
            _run_task("fast_refresh", lambda: refresh.run_fast_refresh(db), db)
            if cycle_start - last_slow >= slow_interval:
                last_slow = cycle_start
                _run_task("slow_refresh", lambda: refresh.run_slow_refresh(db), db)
            _run_task("notifications_scan", lambda: notifications.scan(db), db)
            if cycle % eval_every == 0:
                _run_task("decision_evaluations", lambda: paper.refresh_evaluations(db), db)
        except Exception:  # pragma: no cover - belt & braces around the whole cycle
            logger.exception("cycle %s crashed (continuing)", cycle)

        if once:
            logger.info("--once complete after %s cycle(s)", cycle)
            return 0

        # Sleep in small slices so SIGTERM is honoured promptly.
        remaining = max(1.0, fast_interval - (time.monotonic() - cycle_start))
        deadline = time.monotonic() + remaining
        while not _stop["flag"] and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))

    logger.info("worker stopped cleanly at %s", datetime.now(UTC).isoformat())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m stock_alert_app.worker")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    _install_signal_handlers()
    try:
        return run_forever(once=args.once)
    except RuntimeError as exc:
        logger.error("worker cannot start: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
