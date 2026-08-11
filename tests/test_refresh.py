from __future__ import annotations

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


class TestRunRefresh:
    def test_first_cycle_runs_fast_and_slow(self, monkeypatch):
        _reset_state()
        calls: list[str] = []
        monkeypatch.setattr(refresh, "run_fast_refresh", lambda db: calls.append("fast"))
        monkeypatch.setattr(refresh, "run_slow_refresh", lambda db: calls.append("slow"))
        db = Database(":memory:")

        result = refresh.run_refresh(db)

        assert calls == ["fast", "slow"]
        assert result["running"] is False
        assert result["last_fast_at"] is not None
        assert result["last_slow_at"] is not None

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
