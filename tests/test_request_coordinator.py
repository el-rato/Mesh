from __future__ import annotations

import threading

import pandas as pd

from stock_alert_app.request_coordinator import (
    MarketDataSnapshot,
    ProviderPolicy,
    RequestCoordinator,
    TokenBucket,
)


def _bars(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": range(100, 100 + rows),
            "High": range(102, 102 + rows),
            "Low": range(99, 99 + rows),
            "Close": range(101, 101 + rows),
            "Volume": range(1000, 1000 + rows),
        },
        index=pd.date_range("2026-01-01", periods=rows, freq="D"),
    )


class _Provider:
    name = "test"
    supports_batch = False

    def __init__(self):
        self.calls: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_symbol = ""
        self.error: Exception | None = None

    def enabled(self) -> bool:
        return True

    def supports(self, period: str, interval: str) -> bool:
        return interval == "1d"

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol == self.block_symbol:
            self.started.set()
            self.release.wait(timeout=2)
        if self.error is not None:
            raise self.error
        return _bars()


def _coordinator(provider: _Provider, **kwargs) -> RequestCoordinator:
    return RequestCoordinator(
        [provider],
        policies={provider.name: ProviderPolicy(600, 1000, 20, failure_threshold=2)},
        max_retries=0,
        **kwargs,
    )


def test_concurrent_identical_requests_make_one_provider_call() -> None:
    provider = _Provider()
    provider.block_symbol = "AAPL"
    coordinator = _coordinator(provider)
    results: list[MarketDataSnapshot] = []
    try:
        threads = [threading.Thread(target=lambda: results.append(coordinator.get_snapshot("AAPL"))) for _ in range(2)]
        for thread in threads:
            thread.start()
        assert provider.started.wait(timeout=1)
        provider.release.set()
        for thread in threads:
            thread.join(timeout=2)
        assert provider.calls == ["AAPL"]
        assert len(results) == 2
    finally:
        coordinator.shutdown()


def test_token_bucket_respects_quota() -> None:
    now = [0.0]
    bucket = TokenBucket(2, 1.0, clock=lambda: now[0])
    assert bucket.try_consume()
    assert bucket.try_consume()
    assert not bucket.try_consume()
    assert bucket.wait_time() == 1.0
    now[0] += 1.0
    assert bucket.try_consume()


def test_priority_queue_runs_deep_analysis_first() -> None:
    provider = _Provider()
    provider.block_symbol = "BLOCK"
    coordinator = _coordinator(provider)
    try:
        active = threading.Thread(target=lambda: coordinator.get_snapshot("BLOCK"))
        low = threading.Thread(target=lambda: coordinator.get_snapshot("LOW", priority="background"))
        high = threading.Thread(target=lambda: coordinator.get_snapshot("HIGH", priority="deep_analysis"))
        active.start()
        assert provider.started.wait(timeout=1)
        low.start()
        high.start()
        provider.release.set()
        for thread in (active, low, high):
            thread.join(timeout=2)
        assert provider.calls == ["BLOCK", "HIGH", "LOW"]
    finally:
        coordinator.shutdown()


def test_circuit_breaker_opens_and_recovers() -> None:
    class RateLimitError(RuntimeError):
        status_code = 429

    now = [0.0]
    provider = _Provider()
    provider.error = RateLimitError("rate limit")
    coordinator = _coordinator(
        provider,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        cooldown_s=10,
    )
    try:
        coordinator.get_snapshot("ONE")
        coordinator.get_snapshot("TWO")
        coordinator.get_snapshot("THREE")
        assert provider.calls == ["ONE", "TWO"]
        assert coordinator.status()[0]["cooling_down"] is True
        now[0] += 11
        provider.error = None
        recovered = coordinator.get_snapshot("FOUR")
        assert not recovered.frame.empty
        assert provider.calls[-1] == "FOUR"
        assert coordinator.status()[0]["cooling_down"] is False
    finally:
        coordinator.shutdown()


def test_stale_cache_serves_while_revalidating() -> None:
    now = [0.0]
    provider = _Provider()
    coordinator = _coordinator(provider, fresh_ttl=1, stale_ttl=10, clock=lambda: now[0])
    try:
        first = coordinator.get_snapshot("AAPL")
        assert first.stale is False
        now[0] = 2.0
        provider.block_symbol = "AAPL"
        stale = coordinator.get_snapshot("AAPL")
        assert stale.stale is True
        assert stale.cache_status == "stale_cache"
        assert provider.started.wait(timeout=1)
        provider.release.set()
    finally:
        coordinator.shutdown()


def test_mesh_analysis_models_share_one_snapshot(monkeypatch) -> None:
    from stock_alert_app import signals, verdict
    from stock_alert_app.models import price_lstm

    frame = _bars(240)
    snapshot = MarketDataSnapshot(
        symbol="AAPL",
        period="2y",
        interval="1d",
        frame=frame,
        provider="test",
        fetched_at=0,
        data_timestamp="2026-08-28T00:00:00",
    )
    seen: list[pd.DataFrame | None] = []

    def fake_predict(symbol, period="2y", window=30, horizon=1, history_df=None):
        seen.append(history_df)
        return None

    monkeypatch.setattr(price_lstm, "predict_price_lstm", fake_predict)
    monkeypatch.setattr(
        signals,
        "social_momentum_signal",
        lambda *args: signals.SignalResult("social", status="no_data"),
    )
    monkeypatch.setattr(
        signals,
        "market_regime_signal",
        lambda *args, **kwargs: signals.SignalResult("regime", status="no_data"),
    )

    verdict.build_verdict(
        "NYSE",
        "AAPL",
        None,
        None,
        yahoo_symbol="AAPL",
        market_data_snapshot=snapshot,
        request_priority="deep_analysis",
    )

    assert len(seen) == 3
    assert all(history is frame for history in seen)
