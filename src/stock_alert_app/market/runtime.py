"""Process-local live event distribution backed by the SQLite event store."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .engine import MarketEventEngine
from .events import MarketEvent
from .store import SQLiteEventStore

_engine = MarketEventEngine()


def publish(db, event: MarketEvent) -> bool:
    inserted = SQLiteEventStore(db).append(event)
    if inserted:
        _engine.publish(event)
    return inserted


def subscribe(symbol: str | None = None, types: Iterable[str] | None = None, handler: Callable[[MarketEvent], None] | None = None):
    return _engine.subscribe(symbol=symbol, types=types, handler=handler)
