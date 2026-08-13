"""Tests for the chronological historical replay engine (no network).

The dataset loader and regime preload are monkeypatched with synthetic bars, so
every test is deterministic and offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from stock_alert_app import replay
from stock_alert_app.db import Database


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


class _FakeSource:
    error = ""

    def as_dict(self):
        return {
            "status": "SUCCESS",
            "provider": "secondary",
            "rows": [{"date": "2025-01-02 00:00"}],
            "requested_start": "2024-01-01",
            "requested_end": "2025-06-30",
            "timeframe": "1d",
            "fallback_used": True,
            "attempted_providers": ["primary", "secondary"],
            "provider_errors": {"primary": "no overlap"},
            "error": "",
        }


def _closes(n=270, up=True, step=0.004):
    out = []
    px = 100.0
    for i in range(n):
        px *= (1 + step) if up else (1 - step)
        out.append(px)
    return out


def _osc_closes(n=270, up=True):
    out = []
    px = 100.0
    for i in range(n):
        if up:
            px *= 1.006 if i % 5 != 4 else 0.990
        else:
            px *= 0.994 if i % 5 != 4 else 1.010
        out.append(px)
    return out


def _bars_from_closes(closes, start="2024-10-01", step_minutes=1440):
    base = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i, c in enumerate(closes):
        t = base + timedelta(minutes=step_minutes * i)
        out.append({
            "date": t.strftime("%Y-%m-%d %H:%M"),
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 10000,
        })
    return out


@pytest.fixture
def patch_data(monkeypatch):
    def _apply(bars, regime=None, source=None):
        monkeypatch.setattr(
            replay, "_load_dataset",
            lambda db, m, t, tf, s, e: (bars, source if source is not None else _FakeSource()),
        )
        monkeypatch.setattr(replay, "_load_regime_rows", lambda *a, **k: regime or [])
    return _apply


def _run(db, **kw):
    defaults = dict(
        timeframe="1d", decision_interval="1d", capital=100000.0,
        bull_threshold=50.0, bear_threshold=50.0, size_ratio=0.25, store=False,
    )
    defaults.update(kw)
    return replay.run(db, "NYSE", "TEST", "2025-01-02", "2025-06-30", **defaults)


class TestReplayEngine:
    def test_user_defined_dates_respected(self, tmp_path, patch_data):
        bars = _bars_from_closes(_osc_closes())
        patch_data(bars)
        r = _run(_db(tmp_path))
        assert r["status"] == "ok"
        assert r["start_date"] == "2025-01-02"
        assert r["end_date"] == "2025-06-30"
        assert r["decisions_log"]
        for d in r["decisions_log"]:
            day = datetime.fromisoformat(d["ts"]).date()
            assert datetime(2025, 1, 2).date() <= day <= datetime(2025, 6, 30).date()

    def test_chronological_replay(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path))
        ts = [datetime.fromisoformat(d["ts"]) for d in r["decisions_log"]]
        assert ts == sorted(ts)
        assert len(set(ts)) == len(ts)

    def test_no_future_leakage(self, tmp_path, patch_data):
        # A catastrophic drop on the FINAL dataset bar must never influence a
        # decision. Shared decisions must be byte-identical with/without it.
        base = _closes(270, up=True)
        crash = list(base)
        crash[-1] = crash[-2] * 0.5
        with_crash = _bars_from_closes(crash)
        without_crash = _bars_from_closes(crash[:-1])

        patch_data(with_crash)
        r1 = _run(_db(tmp_path), bull_threshold=40.0, bear_threshold=40.0)
        patch_data(without_crash)
        r2 = _run(_db(tmp_path), bull_threshold=40.0, bear_threshold=40.0)

        n = min(len(r1["decisions_log"]), len(r2["decisions_log"]))
        assert n > 0
        for a, b in zip(r1["decisions_log"][:n], r2["decisions_log"][:n]):
            for key in ("ts", "action", "verdict", "conviction", "reference_price",
                        "execution_price", "quantity", "reason"):
                assert a[key] == b[key]
        # The crash only changes end-of-replay mark-to-market (honest), not decisions.
        assert r1["ending_equity"] != r2["ending_equity"]
        # No decision is evaluated at the crash bar.
        last_ts = datetime.fromisoformat(r1["decisions_log"][-1]["ts"])
        assert last_ts < datetime.fromisoformat(with_crash[-1]["date"])

    def test_signal_timestamps_at_decision_time(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path))
        for d in r["decisions_log"]:
            for key, st in d["signal_timestamps"].items():
                assert st == d["ts"]
            for status in d["signal_statuses"].values():
                assert status in ("AVAILABLE", "NO_DATA", "ERROR")

    def test_research_no_data_handled(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path))
        assert r["decisions_log"]
        for d in r["decisions_log"]:
            assert d["research"].get("status") == "no_data"
        assert any("Research unavailable for this timestamp." in d["reason"] for d in r["decisions_log"])

    def test_signal_no_data_not_a_vote(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path))
        d = r["decisions_log"][0]
        # Social/news/regime are unavailable historically and must be NO_DATA.
        for key in ("social", "regime", "news"):
            assert d["signal_statuses"][key] == "NO_DATA"
        # Alignment counts ONLY available signals (never 5).
        aligned, total = (int(x) for x in d["signal_alignment"].split("/"))
        assert total in (1, 2)
        assert 0 <= aligned <= total

    def test_buy_decision(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes(up=True)))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        buys = [d for d in r["decisions_log"] if d["action"] == "BUY"]
        assert buys
        d = buys[0]
        assert d["portfolio_before"]["position_direction"] is None
        assert d["portfolio_after"]["position_direction"] == "LONG"
        assert d["quantity"] > 0

    def test_sell_decision(self, tmp_path, patch_data):
        # Rising then falling: a long is opened and later exited.
        base = _osc_closes(270, up=True)
        closes = base[:135] + [c for c in _osc_closes(135, up=False)]
        patch_data(_bars_from_closes(closes))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        sells = [d for d in r["decisions_log"] if d["action"] == "SELL"]
        assert sells
        d = sells[0]
        assert d["portfolio_before"]["position_direction"] == "LONG"
        assert d["portfolio_after"]["position_direction"] is None

    def test_short_decision(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes(up=False)))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        shorts = [d for d in r["decisions_log"] if d["action"] == "SHORT"]
        assert shorts
        d = shorts[0]
        assert d["portfolio_after"]["position_direction"] == "SHORT"
        assert d["quantity"] > 0

    def test_cover_decision(self, tmp_path, patch_data):
        # Falling then rising: a short is opened and later covered.
        base = _osc_closes(270, up=False)
        closes = base[:135] + [c for c in _osc_closes(135, up=True)]
        patch_data(_bars_from_closes(closes))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        covers = [d for d in r["decisions_log"] if d["action"] == "COVER"]
        assert covers
        d = covers[0]
        assert d["portfolio_before"]["position_direction"] == "SHORT"
        assert d["portfolio_after"]["position_direction"] is None
        assert d["orders"] and d["orders"][0]["pnl"] is not None

    def test_no_trade_decision(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path), bull_threshold=95.0, bear_threshold=95.0)
        no_trades = [d for d in r["decisions_log"] if d["action"] == "NO_TRADE"]
        assert no_trades
        assert any("below" in d["reason"] or "insufficient" in d["reason"] for d in no_trades)

    def test_position_management_never_impossible(self, tmp_path, patch_data):
        base = _osc_closes(270, up=True)
        closes = base[:135] + [c for c in _osc_closes(135, up=False)]
        patch_data(_bars_from_closes(closes))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        prev_dir = None
        for d in r["decisions_log"]:
            action = d["action"]
            after_dir = d["portfolio_after"]["position_direction"]
            assert d["portfolio_after"]["position_qty"] >= 0
            if action == "BUY":
                assert after_dir == "LONG"
            if action == "SHORT":
                assert after_dir == "SHORT"
            if action == "SELL":
                assert after_dir is None and d["portfolio_after"]["position_qty"] == 0
            if action == "COVER":
                assert after_dir is None and d["portfolio_after"]["position_qty"] == 0
            if prev_dir == "LONG" and after_dir == "SHORT":
                assert any(x["action"] == "SELL" for x in r["decisions_log"] if x["ts"] <= d["ts"])
            if prev_dir == "SHORT" and after_dir == "LONG":
                assert any(x["action"] == "COVER" for x in r["decisions_log"] if x["ts"] <= d["ts"])
            prev_dir = after_dir

    def test_capital_limits(self, tmp_path, patch_data):
        # Tiny capital + normal prices: cash must never go negative.
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(_db(tmp_path), capital=500.0, bull_threshold=40.0, bear_threshold=40.0)
        assert r["decisions_log"]
        for d in r["decisions_log"]:
            assert d["portfolio_after"]["cash"] >= -1e-6
            for o in d["orders"]:
                assert o["price"] * o["quantity"] + o["fee"] <= 500.0 + 1e-6 or o["side"] in ("SELL", "COVER")

    def test_slippage_and_commission(self, tmp_path, patch_data):
        from stock_alert_app.config import settings

        bars = _bars_from_closes(_osc_closes(270, up=True))
        patch_data(bars)
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        buys = [d for d in r["decisions_log"] if d["action"] == "BUY"]
        assert buys
        d = buys[0]
        # BUY fills at the FIRST bar after the decision timestamp, plus slippage.
        times = [datetime.strptime(b["date"], "%Y-%m-%d %H:%M") for b in bars]
        dec = datetime.fromisoformat(d["ts"])
        nxt = next((i for i, t in enumerate(times) if t > dec), None)
        assert nxt is not None
        expected = bars[nxt]["open"] * (1 + settings.paper_slippage)
        assert d["orders"][0]["price"] == pytest.approx(expected, abs=1e-6)
        # Commission is deducted from cash.
        fee = settings.paper_commission
        qty = d["orders"][0]["quantity"]
        cost = d["orders"][0]["price"] * qty + fee
        assert d["portfolio_after"]["cash"] == pytest.approx(d["portfolio_before"]["cash"] - cost, abs=0.01)

    def test_explanation_uses_actual_signals(self, tmp_path, patch_data):
        patch_data(_bars_from_closes(_osc_closes(up=True)))
        r = _run(_db(tmp_path), bull_threshold=50.0, bear_threshold=50.0)
        buys = [d for d in r["decisions_log"] if d["action"] == "BUY"]
        no_trades = [d for d in r["decisions_log"] if d["action"] == "NO_TRADE"]
        assert buys and no_trades
        b = buys[0]
        assert "BULL" in b["reason"] and "conviction" in b["reason"].lower()
        assert f"{int(b['conviction'])}" in b["reason"]
        assert "align" in b["reason"].lower()
        # Explanations differ per decision (not generic/identical).
        assert b["reason"] != no_trades[0]["reason"]

    def test_does_not_touch_paper_portfolio(self, tmp_path, patch_data):
        db = _db(tmp_path)
        patch_data(_bars_from_closes(_osc_closes()))
        r = _run(db, store=True)
        assert r["status"] == "ok"
        assert db.active_portfolio() is None
        assert db.paper_orders() == []
        assert db.decision_snapshots() == []
        # The replay persisted its OWN immutable records.
        runs = db.replay_runs()
        assert len(runs) == 1
        assert len(db.replay_decisions(runs[0]["run_id"])) == len(r["decisions_log"])

    def test_provider_fallback_surfaces(self, tmp_path, monkeypatch):
        bars = _bars_from_closes(_osc_closes())
        monkeypatch.setattr(
            replay, "_load_dataset",
            lambda db, m, t, tf, s, e: (bars, _FakeSource()),
        )
        monkeypatch.setattr(replay, "_load_regime_rows", lambda *a, **k: [])
        r = _run(_db(tmp_path))
        assert r["status"] == "ok"
        assert r["data_source"]["attempted_providers"] == ["primary", "secondary"]
        assert r["data_source"]["fallback_used"] is True

        class _EmptySource:
            error = "no provider returned data for the requested range"

            def as_dict(self):
                return {"status": "NO_DATA", "provider": "", "rows": [],
                        "attempted_providers": ["primary", "secondary", "tertiary"], "error": self.error}

        monkeypatch.setattr(replay, "_load_dataset", lambda db, m, t, tf, s, e: ([], _EmptySource()))
        r2 = _run(_db(tmp_path))
        assert r2["status"] == "no_data"
        assert r2["reason"]

    def test_reproducible(self, tmp_path, patch_data):
        bars = _bars_from_closes(_osc_closes())
        patch_data(bars)
        r1 = _run(_db(tmp_path))
        r2 = _run(_db(tmp_path))
        keys = ("ts", "action", "verdict", "conviction", "reference_price",
                "execution_price", "quantity", "reason", "signal_alignment")
        assert [tuple(d[k] for k in keys) for d in r1["decisions_log"]] == \
               [tuple(d[k] for k in keys) for d in r2["decisions_log"]]
        for k in ("return_pct", "ending_equity", "trades", "long_pnl", "short_pnl", "max_drawdown_pct"):
            assert r1[k] == r2[k]

    def test_decision_interval(self, tmp_path, patch_data):
        # 5m bars, decide every 3 bars -> 15m spacing.
        n = 220
        bars = _bars_from_closes(_osc_closes(n), start="2025-01-02", step_minutes=5)
        patch_data(bars)
        r = replay.run(
            _db(tmp_path), "NYSE", "TEST", "2025-01-02", "2025-01-10",
            timeframe="5m", decision_interval="15m", capital=100000.0,
            bull_threshold=50.0, bear_threshold=50.0, store=False,
        )
        assert r["status"] == "ok"
        ts = [datetime.fromisoformat(d["ts"]) for d in r["decisions_log"]]
        for a, b in zip(ts, ts[1:]):
            assert (b - a).total_seconds() == 15 * 60
