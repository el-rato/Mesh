"""Small event-storage seam used by replay and future live persistence."""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from .events import MarketEvent


class EventStore(Protocol):
    def append(self, event: MarketEvent) -> bool: ...
    def get_events(self, symbol: str = "", from_timestamp: str = "", to_timestamp: str = "", types: Iterable[str] | None = None, limit: int = 5000) -> list[MarketEvent]: ...
    def get_next_event(self) -> MarketEvent | None: ...
    def get_previous_event(self) -> MarketEvent | None: ...


class InMemoryEventStore:
    def __init__(self, events: Iterable[MarketEvent] = ()):
        self._events: list[MarketEvent] = []
        self._ids: set[str] = set()
        self._cursor = 0
        for event in events:
            self.append(event)

    def append(self, event: MarketEvent) -> bool:
        if event.event_id in self._ids:
            return False
        self._ids.add(event.event_id)
        self._events.append(event)
        self._events.sort(key=lambda item: (item.event_timestamp, item.sequence, item.event_id))
        return True

    def get_events(self, symbol: str = "", from_timestamp: str = "", to_timestamp: str = "", types: Iterable[str] | None = None, limit: int = 5000) -> list[MarketEvent]:
        wanted = {str(t).upper() for t in types} if types else None
        return [
            event for event in self._events
            if (not symbol or event.symbol == symbol.upper())
            and (not from_timestamp or event.event_timestamp >= from_timestamp)
            and (not to_timestamp or event.event_timestamp <= to_timestamp)
            and (not wanted or event.event_type in wanted)
        ]

    def get_next_event(self) -> MarketEvent | None:
        if self._cursor >= len(self._events):
            return None
        event = self._events[self._cursor]
        self._cursor += 1
        return event

    def get_previous_event(self) -> MarketEvent | None:
        if self._cursor <= 0:
            return None
        self._cursor -= 1
        return self._events[self._cursor]


class SQLiteEventStore:
    """Persistent adapter for the event-store seam."""

    def __init__(self, db: Any):
        self.db = db

    def append(self, event: MarketEvent) -> bool:
        return self.db.append_market_event(event)

    def get_events(self, symbol: str = "", from_timestamp: str = "", to_timestamp: str = "", types: Iterable[str] | None = None, limit: int = 5000) -> list[MarketEvent]:
        return self.db.market_events(symbol, from_timestamp, to_timestamp, types, limit=limit)

    def get_next_event(self) -> MarketEvent | None:
        raise NotImplementedError("persistent cursors are query-scoped; use get_events")

    def get_previous_event(self) -> MarketEvent | None:
        raise NotImplementedError("persistent cursors are query-scoped; use get_events")
