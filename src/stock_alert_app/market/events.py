"""Immutable, provider-neutral market events.

Provider payloads are converted at this seam. Consumers only receive the
canonical event envelope and a plain JSON payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _freeze(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    # Payload values are provider-normalized JSON primitives. A mapping proxy
    # prevents accidental mutation while preserving a JSON-friendly to_dict.
    return MappingProxyType(dict(payload))


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    event_type: str
    symbol: str
    exchange: str
    event_timestamp: str
    received_timestamp: str
    source: str
    sequence: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_timestamp", _timestamp(self.event_timestamp))
        object.__setattr__(self, "received_timestamp", _timestamp(self.received_timestamp))
        object.__setattr__(self, "payload", _freeze(self.payload))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "eventType": self.event_type,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "eventTimestamp": self.event_timestamp,
            "receivedTimestamp": self.received_timestamp,
            "source": self.source,
            "sequence": self.sequence,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }


class TradeEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="TRADE", **kwargs)


class QuoteEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="QUOTE", **kwargs)


class CandleEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="CANDLE", **kwargs)


class MarketStatusEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="MARKET_STATUS", **kwargs)


class CorporateActionEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="CORPORATE_ACTION", **kwargs)


class NewsEvent(MarketEvent):
    def __init__(self, **kwargs: Any):
        super().__init__(event_type="NEWS", **kwargs)


def event_from_bar(bar: Mapping[str, Any], exchange: str, symbol: str, source: str = "historical") -> CandleEvent:
    """Normalize one historical OHLCV row into a deterministic candle event."""
    ts = _timestamp(bar.get("date") or bar.get("timestamp"))
    payload = {
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume") or 0),
    }
    raw = json.dumps([exchange, symbol.upper(), ts, bar.get("timeframe", ""), payload], sort_keys=True, separators=(",", ":"))
    event_id = "CANDLE-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return CandleEvent(
        event_id=event_id,
        symbol=symbol.upper(),
        exchange=exchange.upper(),
        event_timestamp=ts,
        received_timestamp=ts,
        source=source,
        sequence=0,
        payload=payload,
        metadata={"timeframe": bar.get("timeframe", "")},
    )


def event_from_price_state(state: Any, source: str = "price") -> QuoteEvent:
    """Create a live quote event from the canonical ``PriceState`` model."""
    received = _timestamp(datetime.now(timezone.utc))
    payload = {
        "price": float(state.close),
        "open": float(state.open),
        "high": float(state.high),
        "low": float(state.low),
        "volume": float(state.volume),
        "change_pct": float(state.change_pct),
    }
    raw = json.dumps([state.market, state.ticker.upper(), received, payload], sort_keys=True, separators=(",", ":"))
    event_id = "QUOTE-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return QuoteEvent(
        event_id=event_id,
        symbol=state.ticker.upper(),
        exchange=state.market.upper(),
        event_timestamp=received,
        received_timestamp=received,
        source=source,
        payload=payload,
        metadata={"asOf": state.as_of, "dataStatus": state.data_status},
    )
