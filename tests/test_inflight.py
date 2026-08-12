from __future__ import annotations

import threading
import time as _t

import pytest

from stock_alert_app import verdict as verdict_module


@pytest.fixture(autouse=True)
def _reset_inflight():
    verdict_module._live_inflight.clear()
    yield
    verdict_module._live_inflight.clear()


def _run_in_threads(fn, n=2):
    barrier = threading.Barrier(n)
    results = []
    errors = []

    def target():
        try:
            barrier.wait()
            results.append(fn())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


class TestLiveVerdictInflightGuard:
    def test_same_security_analyzed_once(self, monkeypatch):
        calls = {"n": 0}

        def fake_impl(market_code, ticker, company="", db_path=None, yahoo_symbol=None):
            calls["n"] += 1
            _t.sleep(0.05)
            return "DONE"

        monkeypatch.setattr(verdict_module, "_live_verdict_impl", fake_impl)
        results, errors = _run_in_threads(
            lambda: verdict_module.live_verdict("NYSE", "AAPL")
        )
        assert calls["n"] == 1  # only one analysis ran
        assert errors == []
        assert results == ["DONE", "DONE"]
        assert verdict_module._live_inflight == {}  # cleared after success

    def test_different_securities_run_concurrently(self, monkeypatch):
        calls = {"n": 0}

        def fake_impl(market_code, ticker, company="", db_path=None, yahoo_symbol=None):
            calls["n"] += 1
            _t.sleep(0.05)
            return f"{market_code}:{ticker}"

        monkeypatch.setattr(verdict_module, "_live_verdict_impl", fake_impl)
        r1, e1 = _run_in_threads(lambda: verdict_module.live_verdict("NYSE", "AAPL"))
        r2, e2 = _run_in_threads(lambda: verdict_module.live_verdict("LSE", "ULVR"))
        assert calls["n"] == 2  # AAPL and ULVR both analyzed
        assert r1 == ["NYSE:AAPL", "NYSE:AAPL"]
        assert r2 == ["LSE:ULVR", "LSE:ULVR"]
        assert not e1 and not e2

    def test_inflight_cleared_after_exception(self, monkeypatch):
        calls = {"n": 0}

        def failing_impl(market_code, ticker, company="", db_path=None, yahoo_symbol=None):
            calls["n"] += 1
            raise ValueError("boom")

        monkeypatch.setattr(verdict_module, "_live_verdict_impl", failing_impl)
        with pytest.raises(ValueError):
            verdict_module.live_verdict("NYSE", "AAPL")
        assert verdict_module._live_inflight == {}  # cleared after exception
        # a subsequent call retries (no leaked entry / deadlock)
        with pytest.raises(ValueError):
            verdict_module.live_verdict("NYSE", "AAPL")
        assert calls["n"] == 2

    def test_second_waiter_reuses_running_analysis(self, monkeypatch):
        calls = {"n": 0}

        def fake_impl(market_code, ticker, company="", db_path=None, yahoo_symbol=None):
            calls["n"] += 1
            _t.sleep(0.05)
            return "RESULT"

        monkeypatch.setattr(verdict_module, "_live_verdict_impl", fake_impl)
        # Start one analysis, then a second request while it is in flight.
        first_done = threading.Event()

        def first():
            verdict_module.live_verdict("NYSE", "AAPL")
            first_done.set()

        t = threading.Thread(target=first)
        t.start()
        _t.sleep(0.01)  # ensure the first is in flight
        result = verdict_module.live_verdict("NYSE", "AAPL")  # waiter reuses
        t.join()
        assert result == "RESULT"
        assert calls["n"] == 1
