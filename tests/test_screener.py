"""Tests for the stock screener engine (dynamic universe, no network)."""

from __future__ import annotations

import pytest

from stock_alert_app import screener
from stock_alert_app.db import Database
from stock_alert_app.universe import universe


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def _seed_verdict(db, market, ticker, verdict, lstm, lconf, technical, reason, news_score=0.0):
    db.insert_verdict(
        market, ticker, verdict, 0.8, news_score=news_score, price_score=technical,
        combined_score=0.5, reason=reason, lstm_score=lstm, lstm_confidence=lconf,
        lstm_probability_up=0.9 if lstm >= 0 else 0.1, technical_score=technical,
    )


def _seed_snapshots(db, market, ticker, closes, volumes, momentum):
    for close, vol in zip(closes, volumes):
        db.insert_price_snapshot(
            market=market, ticker=ticker, close=close, open=close,
            high=close * 1.01, low=close * 0.99, volume=vol,
            momentum_20=momentum, rsi_14=55.0, sma_50=close * 0.98,
        )


def _universe_with_data(tmp_path):
    """Seed the dynamic universe + two analyzed LSE securities + one NYSE."""
    db = _db(tmp_path)
    universe(db)  # seeds configured markets (LSE, NYSE, ...)
    _seed_verdict(db, "LSE", "ULVR", "BULL", 0.9, 0.9, 0.6,
                  "News: bullish (2 articles, score +0.40); Technical: bullish (0.60); Final verdict: BULL",
                  news_score=0.4)
    _seed_verdict(db, "LSE", "SHEL", "BEAR", -0.3, 0.5, -0.3,
                  "News: unavailable; Technical: bearish (-0.30); Final verdict: BEAR")
    _seed_verdict(db, "NYSE", "AAPL", "BULL", 0.6, 0.6, 0.3,
                  "News: unavailable; Technical: bullish (0.30); Final verdict: BULL")
    _seed_snapshots(db, "LSE", "ULVR", [40.0, 42.0], [30000, 100000], 0.05)   # +5% move, 3.3x volume
    _seed_snapshots(db, "LSE", "SHEL", [25.0, 24.0], [100000, 90000], -0.03)  # -4% move
    return db


class TestDynamicUniverse:
    def test_screener_uses_dynamic_universe(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE")
        ids = {f"{r['market']}:{r['ticker']}" for r in rows}
        assert "LSE:ULVR" in ids and "LSE:SHEL" in ids
        # Never a hardcoded list: NYSE securities are excluded by market filter.
        assert not any(r["market"] == "NYSE" for r in rows)

    def test_no_data_securities_are_discoverable(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", no_data_only=True)
        assert any(r["ticker"] == "AZN" for r in rows)
        assert any(r["verdict"] is None for r in rows)


class TestFilters:
    def test_verdict_filter(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", verdict="BULL")
        assert [r["ticker"] for r in rows] == ["ULVR"]

    def test_min_conviction_filter(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", min_conviction=0.7)
        assert [r["ticker"] for r in rows] == ["ULVR"]  # ULVR 0.756, SHEL 0.455

    def test_price_move_filter(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", min_move=0.03)
        assert [r["ticker"] for r in rows] == ["ULVR"]

    def test_unusual_volume_filter(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", min_volume_ratio=2.0)
        assert [r["ticker"] for r in rows] == ["ULVR"]

    def test_momentum_filter(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", min_momentum=0.0)
        assert [r["ticker"] for r in rows] == ["ULVR"]  # SHEL momentum is negative

    def test_company_search(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", q="unilever")
        assert [r["ticker"] for r in rows] == ["ULVR"]

    def test_ticker_search(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", q="ulvr")
        assert [r["ticker"] for r in rows] == ["ULVR"]

    def test_sort_by_conviction(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", sort="conviction")
        assert rows[0]["ticker"] == "ULVR"  # 0.756 > 0.455
        assert rows[-1]["ticker"] == "SHEL"


class TestResultsShape:
    def test_result_is_dossier_ready(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE")
        ulvr = next(r for r in rows if r["ticker"] == "ULVR")
        # Screener rows carry the fields needed to open the Dossier.
        for field in ("market", "ticker", "symbol", "company", "verdict", "confidence"):
            assert field in ulvr
        assert ulvr["price_move"] == pytest.approx(0.05, abs=1e-6)
        assert ulvr["volume_ratio"] == pytest.approx(100000 / 30000, abs=1e-3)
        assert ulvr["agreement"] is not None
        assert ulvr["research_available"] is not None


class TestPresets:
    def test_presets_configure_existing_filters(self):
        assert screener.apply_preset("high_conviction") == {"min_conviction": 0.75}
        p = screener.apply_preset("unusual_activity")
        assert p.get("min_volume_ratio") == 1.5 and p.get("min_move") == 0.03
        assert screener.apply_preset("reversals") == {"reversal": "true"}
        assert screener.apply_preset("needs_research")["no_data_only"] is True

    def test_preset_result(self, tmp_path):
        db = _universe_with_data(tmp_path)
        rows = screener.run(db, market="LSE", **screener.apply_preset("high_conviction"))
        assert [r["ticker"] for r in rows] == ["ULVR"]
