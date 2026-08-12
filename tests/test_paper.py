from __future__ import annotations

import json

import pytest

from stock_alert_app import paper
from stock_alert_app.analysis import stock_analysis
from stock_alert_app.db import Database


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def _analysis_dict():
    return stock_analysis(
        {
            "market": "NYSE",
            "ticker": "T",
            "verdict": "BULL",
            "confidence": 0.6,
            "news_score": 0.3,
            "price_score": 0.2,
            "combined_score": 0.4,
            "reason": "News: bullish (5 articles, score +0.30); Signal agreement: moderate",
            "decided_at": "2026-01-01T10:00:00",
            "lstm_score": 0.4,
            "lstm_probability_up": 0.7,
            "lstm_predicted_return": 0.01,
            "lstm_confidence": 0.6,
            "technical_score": 0.2,
            "signals": "",
        }
    )


class TestDecisionSnapshots:
    def test_security_id_present(self, tmp_path):
        db = _db(tmp_path)
        did = paper.record_decision_snapshot(db, "NYSE", "T", _analysis_dict())
        assert did is not None
        snap = db.decision_snapshots()[0]
        assert snap["security_id"] == "NYSE:T"
        decision = json.loads(snap["decision_json"])
        assert decision["security_id"] == "NYSE:T"

    def test_snapshots_are_immutable(self, tmp_path):
        db = _db(tmp_path)
        paper.record_decision_snapshot(db, "NYSE", "T", _analysis_dict())
        first = db.decision_snapshots()
        # Re-recording with the same decided_at must not overwrite / duplicate.
        inserted = db.insert_decision_snapshot(
            "DEC-OTHER", "NYSE", "T", "2026-01-01T10:00:00", "BULL", 0.5, None, None, "{}"
        )
        assert inserted is False
        assert len(db.decision_snapshots()) == len(first)

    def test_new_decision_creates_new_snapshot(self, tmp_path):
        db = _db(tmp_path)
        d1 = _analysis_dict()
        paper.record_decision_snapshot(db, "NYSE", "T", d1)
        d2 = _analysis_dict()
        d2["decided_at"] = "2026-01-01T11:00:00"
        paper.record_decision_snapshot(db, "NYSE", "T", d2)
        assert len(db.decision_snapshots(ticker="T")) == 2


class TestEvaluation:
    def test_only_post_decision_prices_used(self):
        snap = {"decided_at": "2026-01-01T10:00:00", "verdict": "BULL"}
        # A bar BEFORE the decision must never be used as the reference.
        bars = [
            {"date": "2026-01-01 09:30", "close": 90.0},
            {"date": "2026-01-01 10:05", "close": 100.12},
            {"date": "2026-01-01 10:15", "close": 100.31},
            {"date": "2026-01-01 11:00", "close": 100.67},
        ]
        r = paper.evaluate_snapshot(snap, bars)
        assert r["status"] == "ok"
        assert r["reference_price"] == 100.12  # not the 09:30 bar
        assert r["prices"]["p60"] == 100.67
        assert r["correct"] == 1

    def test_missing_forward_data_is_no_data(self):
        snap = {"decided_at": "2026-01-01T10:00:00", "verdict": "BULL"}
        r = paper.evaluate_snapshot(snap, [{"date": "2026-01-01 09:30", "close": 90.0}])
        assert r["status"] == "no_data"
        assert r["reference_price"] is None

    def test_no_look_ahead_on_direction(self):
        # BULL decision; only bars after the timestamp count. Late bar is lower.
        snap = {"decided_at": "2026-01-01T10:00:00", "verdict": "BULL"}
        bars = [
            {"date": "2026-01-01 10:05", "close": 100.0},
            {"date": "2026-01-01 11:00", "close": 99.0},
        ]
        r = paper.evaluate_snapshot(snap, bars)
        assert r["correct"] == 0  # fell after decision -> BULL wrong


class TestPaperPortfolio:
    def test_buy_sell_close_and_pnl(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        monkeypatch.setattr(
            "stock_alert_app.db.Database.latest_price_snapshot",
            lambda self, m, t: {"close": 110.0, "fetched_at": "2026-01-01T12:00:00"},
        )
        paper.ensure_session(db)

        buy = paper.paper_order(db, "NYSE", "T", "BUY", 10, decision_id="DEC-X", reason="Committee BULL")
        assert buy["decision_id"] == "DEC-X"  # journal link
        assert buy["side"] == "BUY" and buy["direction"] == "LONG"
        exec_price = buy["price"]  # 100 * (1+slippage)
        assert exec_price > 100.0

        state = paper.portfolio_state(db, record_equity=False)
        assert state["positions"][0]["qty"] == 10
        assert state["positions"][0]["direction"] == "LONG"
        assert state["positions"][0]["unrealized"] > 0  # price 110 > entry
        # cash = 100000 - qty*price - fee
        assert state["cash"] == pytest.approx(100000.0 - 10 * exec_price - 1.0)
        # equity = cash + long market value (short none)
        assert state["equity"] == pytest.approx(state["cash"] + state["long_value"])

        paper.paper_order(db, "NYSE", "T", "SELL", 5)
        state = paper.portfolio_state(db, record_equity=False)
        assert state["positions"][0]["qty"] == 5
        # SELL also executes at exec_price: entry basis excludes fee, so
        # realized = (exec_price - entry)*5 - fee = -fee.
        fee = 1.0
        sell_realized = -fee
        assert state["positions"][0]["realized"] == pytest.approx(sell_realized)

        paper.paper_order(db, "NYSE", "T", "CLOSE", 99)  # quantity ignored -> closes all
        state = paper.portfolio_state(db, record_equity=False)
        assert state["positions"] == []  # fully closed
        close_realized = -fee
        assert state["total_pnl"] == pytest.approx(sell_realized + close_realized)

    def test_cannot_sell_more_than_held(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        paper.ensure_session(db)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "SELL", 5)

    def test_no_execution_price_is_no_data(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: None)
        paper.ensure_session(db)
        with pytest.raises(LookupError):
            paper.paper_order(db, "NYSE", "T", "BUY", 1)


class TestShortSelling:
    def test_short_and_cover(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        monkeypatch.setattr(
            "stock_alert_app.db.Database.latest_price_snapshot",
            lambda self, m, t: {"close": 90.0, "fetched_at": "2026-01-01T12:00:00"},
        )
        paper.ensure_session(db)
        short = paper.paper_order(db, "NYSE", "T", "SHORT", 10)
        assert short["side"] == "SHORT" and short["direction"] == "SHORT"
        px = short["price"]  # ~100.05
        # short proceeds increase cash
        state = paper.portfolio_state(db, record_equity=False)
        assert state["cash"] == pytest.approx(100000.0 + 10 * px - 1.0)
        assert state["positions"][0]["direction"] == "SHORT"
        # mark price 90 -> unrealized positive for a short
        assert state["positions"][0]["unrealized"] > 0

        cover = paper.paper_order(db, "NYSE", "T", "COVER", 5)
        state = paper.portfolio_state(db, record_equity=False)
        assert state["positions"][0]["qty"] == 5
        # cover realized = (entry - cover_price)*5 - fee = -fee (same exec price)
        assert state["positions"][0]["realized"] == pytest.approx(-1.0)

        paper.paper_order(db, "NYSE", "T", "COVER", 5)
        assert paper.portfolio_state(db, record_equity=False)["positions"] == []

    def test_direction_reversal_rejected(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        paper.ensure_session(db)
        paper.paper_order(db, "NYSE", "T", "BUY", 5)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "SHORT", 5)  # cannot short while long
        paper.paper_order(db, "NYSE", "T", "SELL", 5)  # close long
        paper.paper_order(db, "NYSE", "T", "SHORT", 5)  # now short is valid
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "BUY", 5)  # cannot buy while short


class TestValidation:
    def test_insufficient_cash(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 1000.0)
        paper.ensure_session(db)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "BUY", 1000)  # $1M > $100k cash

    def test_invalid_quantity(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        paper.ensure_session(db)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "BUY", -1)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "BUY", float("nan"))

    def test_close_nonexistent_position(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        paper.ensure_session(db)
        with pytest.raises(ValueError):
            paper.paper_order(db, "NYSE", "T", "CLOSE", 10)

    def test_multiple_fills_weighted_entry(self, tmp_path, monkeypatch):
        prices = iter([100.0, 120.0])
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: next(prices))
        db = _db(tmp_path)
        paper.ensure_session(db)
        paper.paper_order(db, "NYSE", "T", "BUY", 10)
        paper.paper_order(db, "NYSE", "T", "BUY", 5)
        state = paper.portfolio_state(db, record_equity=False)
        p = state["positions"][0]
        assert p["qty"] == 15
        # weighted entry: (10*100.05 + 5*120.06)/15
        assert p["entry"] == pytest.approx((10 * 100.05 + 5 * 120.06) / 15)

    def test_portfolio_identity(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: 100.0)
        monkeypatch.setattr(
            "stock_alert_app.db.Database.latest_price_snapshot",
            lambda self, m, t: {"close": 110.0, "fetched_at": "2026-01-01T12:00:00"},
        )
        paper.ensure_session(db)
        paper.paper_order(db, "NYSE", "T", "BUY", 10)
        paper.paper_order(db, "NYSE", "U", "SHORT", 5)
        state = paper.portfolio_state(db, record_equity=False)
        # equity = cash + long MV - short MV
        assert state["equity"] == pytest.approx(state["cash"] + state["long_value"] - state["short_value"])
        assert state["gross_exposure"] == pytest.approx(state["long_value"] + state["short_value"])
        assert state["net_exposure"] == pytest.approx(state["long_value"] - state["short_value"])

    def test_quote_no_data(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        monkeypatch.setattr(paper, "_execution_price", lambda db, sym, m, t: None)
        q = paper.quote(db, "NYSE", "T")
        assert q["status"] == "no_data" and q["price"] is None


class TestPerformance:
    def test_metrics_derived(self, tmp_path):
        db = _db(tmp_path)
        d = _analysis_dict()
        paper.record_decision_snapshot(db, "NYSE", "T", d)
        # inject an evaluation for the snapshot
        snap = db.decision_snapshots()[0]
        db.insert_decision_evaluation(
            snap["decision_id"], 100.0, {"p5": 100.5, "p15": 100.5, "p30": 100.5, "p60": 100.5, "close": 100.5}, 1, "ok"
        )
        perf = paper.performance(db)
        assert perf["decisions"] >= 1
        assert perf["evaluated"] == 1
        assert perf["directional_accuracy"] == 1.0
        assert perf["conviction_buckets"] and perf["conviction_buckets"][0]["n"] >= 1
