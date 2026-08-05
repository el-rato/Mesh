from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .alerts import Alert, build_notifiers
from .config import settings
from .db import Database

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    verdicts: dict[str, str] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _previous_verdicts(db: Database) -> dict[str, str]:
    return {f"{r['market']}:{r['ticker']}": r["verdict"] for r in db.latest_verdicts()}


def detect_changes(previous: dict[str, str], current: dict[str, str]) -> list[Alert]:
    alerts: list[Alert] = []
    for key, verdict in current.items():
        prev = previous.get(key)
        if prev == verdict:
            continue
        market, ticker = key.split(":", 1)
        if prev in ("BULL", "BEAR") and verdict in ("BULL", "BEAR") and prev != verdict:
            kind = "flip"
        elif verdict == "BULL" and prev != "BULL":
            kind = "new_bull"
        elif verdict == "BEAR" and prev != "BEAR":
            kind = "new_bear"
        else:
            kind = "verdict_change"
        message = f"{market}:{ticker} moved {prev or 'new'} -> {verdict}"
        alerts.append(Alert(kind=kind, market=market, ticker=ticker, verdict=verdict, previous=prev or "", message=message))
    return alerts


def run_cycle(market_codes: Iterable[str] | None = None, *, prefer_finbert: bool = True) -> CycleResult:
    from .ingest import run_ingest
    from .verdict import run_verdicts

    db = Database(settings.db_path)
    db.init_schema()
    result = CycleResult()

    try:
        run_ingest(market_codes=list(market_codes) if market_codes else None, db_path=str(settings.db_path))
    except Exception as exc:
        result.errors.append(f"ingest failed: {exc}")
        logger.exception("Ingest failed in cycle")

    previous = _previous_verdicts(db)

    try:
        verdicts = run_verdicts(
            market_codes=list(market_codes) if market_codes else None,
            db_path=str(settings.db_path),
            prefer_finbert=prefer_finbert,
        )
        result.verdicts = {k: v.verdict for k, v in verdicts.items()}
    except Exception as exc:
        result.errors.append(f"verdict failed: {exc}")
        logger.exception("Verdict failed in cycle")
        return result

    alerts = detect_changes(previous, result.verdicts)
    result.alerts = alerts

    notifiers = build_notifiers()
    for alert in alerts:
        for notifier in notifiers:
            try:
                notifier.send(alert)
            except Exception as exc:
                logger.warning("Notifier %s failed: %s", notifier.name(), exc)

    return result


def run_scheduler(
    interval_seconds: int = 3600,
    market_codes: Iterable[str] | None = None,
    *,
    once: bool = False,
    prefer_finbert: bool = True,
    skip_off_hours: bool = True,
) -> None:
    codes = list(market_codes) if market_codes else list(settings.default_markets)

    if once:
        result = run_cycle(codes, prefer_finbert=prefer_finbert)
        _report(result)
        return

    print(f"StockVerdict scheduler started (every {interval_seconds}s, markets: {', '.join(codes)})")
    while True:
        if skip_off_hours and _is_weekend():
            print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] weekend, sleeping {interval_seconds}s")
            time.sleep(interval_seconds)
            continue
        try:
            result = run_cycle(codes, prefer_finbert=prefer_finbert)
            _report(result)
        except Exception as exc:
            logger.exception("Scheduler cycle crashed")
            print(f"cycle error: {exc}")
        time.sleep(interval_seconds)


def _is_weekend() -> bool:
    return datetime.now(timezone.utc).weekday() >= 5


def _report(result: CycleResult) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] cycle: {len(result.verdicts)} verdicts, {len(result.alerts)} alerts, {len(result.errors)} errors")
    for alert in result.alerts:
        print(f"    ALERT {alert.kind}: {alert.market}:{alert.ticker} {alert.previous or 'new'}->{alert.verdict}")
    for err in result.errors:
        print(f"    ERROR {err}")