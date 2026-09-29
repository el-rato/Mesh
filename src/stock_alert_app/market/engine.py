"""In-process event distribution with symbol/type filters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .events import MarketEvent


@dataclass(frozen=True, slots=True)
class _Subscription:
    symbol: str | None
    types: frozenset[str] | None
    handler: Callable[[MarketEvent], None]


class MarketEventEngine:
    def __init__(self):
        self._subscriptions: list[_Subscription] = []

    def subscribe(self, symbol: str | None = None, types: Iterable[str] | None = None, handler: Callable[[MarketEvent], None] | None = None) -> Callable[[], None]:
        if handler is None and callable(types):
            handler, types = types, None
        if handler is None:
            raise TypeError("handler is required")
        sub = _Subscription(symbol.upper() if symbol else None, frozenset(str(t).upper() for t in types) if types else None, handler)
        self._subscriptions.append(sub)
        return lambda: self._subscriptions.remove(sub) if sub in self._subscriptions else None

    def publish(self, event: MarketEvent) -> int:
        delivered = 0
        for sub in tuple(self._subscriptions):
            if sub.symbol and sub.symbol != event.symbol.upper():
                continue
            if sub.types and event.event_type not in sub.types:
                continue
            sub.handler(event)
            delivered += 1
        return delivered
