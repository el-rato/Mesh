from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stock_alert_app import simulation
from stock_alert_app.db import Database


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def _bars(closes):
    out = []
    start = datetime(2026, 1, 1, 9, 30)
    for i, c in enumerate(closes):
        t = start + timedelta(minutes=5 * i)
        out.append({"date": t.strftime("%Y-%m-%d %H:%M"), "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1000})
    return out


def _rising(n=80, step=0.005):
    c = 100.0
    closes = []
    for _ in range(n):
        closes.append(c)
        c *= 1 + step
    return closes


def _falling(n=80, step=0.005):
    c = 100.0
    closes = []
    for _ in range(n):
        closes.append(c)
        c *= 1 - step
    return closes


def _source_result(bars):
    from stock_alert_app.historical import HistoricalDataResult

    return HistoricalDataResult(
        status="SUCCESS", provider="primary", rows=bars, timeframe="15m",
        requested_start="2026-01-01", requested_end="2026-01-02",
    )


class TestSimulation:
    def test_no_data(self, tmp_path, monkeypatch):
        from stock_alert_app.historical import HistoricalDataResult

        monkeypatch.setattr(
            simulation, "_load_dataset",
            lambda db, m, t, tf, wd: ([], HistoricalDataResult(status="NO_DATA", provider="", timeframe=tf)),
        )
        res = simulation.run(_db(tmp_path), "NYSE", "T", mode="sim")
        assert res["status"] == "no_data"

    def test_deterministic(self, tmp_path, monkeypatch):
        bars = _bars(_rising())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        a = simulation.run(_db(tmp_path), "NYSE", "T", mode="sim")
        b = simulation.run(_db(tmp_path), "NYSE", "T", mode="sim")
        assert a["return_pct"] == b["return_pct"]
        assert a["trades"] == b["trades"]
        assert a["ending_equity"] == b["ending_equity"]

    def test_long_pnl_correct(self, tmp_path, monkeypatch):
        bars = _bars(_rising())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        res = simulation.run(_db(tmp_path), "NYSE", "T", mode="sim", bull_threshold=70)
        assert res["status"] == "ok"
        assert res["long_pnl"] > 0
        assert res["return_pct"] > 0
        assert res["trades"] >= 1

    def test_short_pnl_correct(self, tmp_path, monkeypatch):
        bars = _bars(_falling())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        res = simulation.run(_db(tmp_path), "NYSE", "T", mode="sim", bull_threshold=70, bear_threshold=70)
        assert res["status"] == "ok"
        assert res["short_pnl"] > 0
        assert res["trades"] >= 1

    def test_no_invalid_positions_and_isolated(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        bars = _bars(_rising())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        simulation.run(db, "NYSE", "T", mode="sim")
        # Simulation is isolated: no paper session created, no paper orders written.
        assert db.active_portfolio() is None
        assert db.paper_orders() == []

    def test_backtest_look_ahead(self, tmp_path, monkeypatch):
        bars = _bars(_rising())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        res = simulation.run(_db(tmp_path), "NYSE", "T", mode="backtest", bull_threshold=70)
        assert res["status"] == "ok"
        for s in res.get("snapshots", []):
            ref = s["reference_price"]
            # forward prices must be strictly AFTER the decision bar (look-ahead guard)
            fwd = [v for v in s.get("forward", {}).values() if v is not None]
            assert all(v > ref for v in fwd)  # rising market -> all forward prices above ref
        assert res["metrics"]["bull_n"] > 0
        assert res["metrics"]["forward_15m"] is not None

    def test_forward_returns_use_post_decision_bars(self):
        bars = _bars(_rising(40))
        i = 10
        fwd = simulation._forward_returns(bars, i, 5)
        assert fwd["p5"] == bars[i + 1]["close"]  # first bar after decision
        assert fwd["p60"] == bars[i + 12]["close"]


class TestBacktestSnapshots:
    def test_run_returns_immutable_snapshots(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        bars = _bars(_rising())
        monkeypatch.setattr(simulation, "_load_dataset", lambda db, m, t, tf, wd: (bars, _source_result(bars)))
        res = simulation.run(db, "NYSE", "T", mode="backtest", bull_threshold=70)
        assert res["status"] == "ok"
        snaps = res.get("snapshots", [])
        assert len(snaps) > 0
        for s in snaps:
            assert s["verdict"] in ("BULL", "BEAR", "NEUTRAL")
            assert s["reference_price"] is not None
            assert "forward" in s and "p30" in s["forward"]

    def test_snapshot_insert_is_immutable(self, tmp_path):
        db = _db(tmp_path)
        db.insert_backtest_snapshot("R1", "D1", "NYSE", "T", "2026-01-01T10:00:00", "BULL", 75.0, 100.0, "[]", "{}", 1)
        db.insert_backtest_snapshot("R1", "D1", "NYSE", "T", "2026-01-01T10:00:00", "BULL", 75.0, 100.0, "[]", "{}", 1)
        with db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM backtest_snapshots WHERE run_id='R1' AND decision_id='D1'").fetchone()["c"]
        assert n == 1  # duplicate decision is ignored -> immutable
