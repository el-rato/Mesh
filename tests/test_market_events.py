from datetime import datetime

from stock_alert_app.db import Database
from stock_alert_app.market import InMemoryEventStore, MarketEventEngine, ReplayClock, SQLiteEventStore, event_from_bar


def _bar(ts, close):
    return {"date": ts, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10}


def test_events_are_normalized_immutable_and_ordered():
    late = event_from_bar(_bar("2025-01-02T10:01:00", 101), "NYSE", "aapl")
    early = event_from_bar(_bar("2025-01-02T10:00:00", 100), "NYSE", "aapl")
    store = InMemoryEventStore([late, early, early])
    assert [e.payload["close"] for e in store.get_events(symbol="AAPL")] == [100.0, 101.0]
    assert early.event_timestamp.endswith("Z")
    try:
        early.payload["close"] = 0
    except TypeError:
        pass
    else:
        raise AssertionError("event payload must be immutable")


def test_event_engine_filters_and_replay_clock_controls():
    event = event_from_bar(_bar("2025-01-02T10:00:00", 100), "NYSE", "AAPL")
    seen = []
    engine = MarketEventEngine()
    engine.subscribe(symbol="AAPL", types=["CANDLE"], handler=seen.append)
    engine.publish(event)
    assert seen == [event]

    clock = ReplayClock(datetime(2025, 1, 2, 10), datetime(2025, 1, 2, 11))
    clock.set_speed(5)
    clock.step_forward(30)
    assert clock.now().hour == 10 and clock.now().second == 30
    clock.seek(datetime(2025, 1, 2, 12))
    assert clock.now().hour == 11


def test_sqlite_event_store_is_append_only_and_queryable(tmp_path):
    db = Database(tmp_path / "events.db")
    db.init_schema()
    event = event_from_bar(_bar("2025-01-02T10:00:00", 100), "NYSE", "AAPL")
    store = SQLiteEventStore(db)
    assert store.append(event) is True
    assert store.append(event) is False
    assert [item.event_id for item in store.get_events(symbol="AAPL")] == [event.event_id]
