from __future__ import annotations

import threading

from stock_alert_app import refresh
from stock_alert_app.db import Database


def _reset_state() -> None:
    refresh._state.update(
        running=False, last_fast_at=None, last_slow_at=None, last_error=""
    )


class TestRefreshStatus:
    def test_status_shape(self):
        _reset_state()
        status = refresh.refresh_status()
        assert status["running"] is False
        assert "next_fast_in" in status
        assert "next_slow_in" in status
        assert "error" in status
        assert status["last_fast_at"] is None


class TestWarmWorkerLifecycle:
    def teardown_method(self):
        refresh.shutdown_warm_workers()

    def test_start_is_idempotent_and_shutdown_joins_workers(self):
        refresh.shutdown_warm_workers()
        refresh.enable_warm_workers()
        refresh._start_warm_workers()
        initial = list(refresh._warm_threads)

        refresh._start_warm_workers()

        assert refresh._warm_threads == initial
        assert all(thread.is_alive() for thread in initial)
        refresh.shutdown_warm_workers()
        assert refresh._warm_threads == []
        assert not refresh._warm_started
        assert not refresh._warm_accepting
        assert not any(
            thread.name.startswith("analysis-warmer") and thread.is_alive()
            for thread in threading.enumerate()
        )


class TestRunRefresh:
    def test_first_cycle_runs_fast_only(self, monkeypatch):
        _reset_state()
        calls: list[str] = []
        monkeypatch.setattr(refresh, "run_fast_refresh", lambda db: calls.append("fast"))
        monkeypatch.setattr(refresh, "run_slow_refresh", lambda db: calls.append("slow"))
        db = Database(":memory:")

        result = refresh.run_refresh(db)

        assert calls == ["fast"]
        assert result["running"] is False
        assert result["last_fast_at"] is not None
        assert result["last_slow_at"] is None

    def test_immediate_second_cycle_does_no_duplicate_work(self, monkeypatch):
        _reset_state()
        calls: list[str] = []
        monkeypatch.setattr(refresh, "run_fast_refresh", lambda db: calls.append("fast"))
        monkeypatch.setattr(refresh, "run_slow_refresh", lambda db: calls.append("slow"))
        db = Database(":memory:")

        refresh.run_refresh(db)
        calls.clear()
        second = refresh.run_refresh(db)

        # Neither interval has elapsed, so no expensive work runs again.
        assert calls == []
        assert second["running"] is False

    def test_concurrent_cycle_is_skipped(self, monkeypatch):
        _reset_state()
        monkeypatch.setattr(refresh, "run_fast_refresh", lambda db: None)
        monkeypatch.setattr(refresh, "run_slow_refresh", lambda db: None)
        db = Database(":memory:")

        refresh._state["running"] = True  # simulate an in-flight cycle
        result = refresh.run_refresh(db)

        assert result.get("skipped") is True
        refresh._state["running"] = False

    def test_fast_failure_is_recorded_not_fatal(self, monkeypatch):
        _reset_state()
        monkeypatch.setattr(
            refresh, "run_fast_refresh", lambda db: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        monkeypatch.setattr(refresh, "run_slow_refresh", lambda db: None)
        db = Database(":memory:")

        result = refresh.run_refresh(db)

        assert result["running"] is False
        assert "fast" in result["error"]
