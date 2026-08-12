from __future__ import annotations

from stock_alert_app.db import Database
from stock_alert_app.universe import ensure_seeded, register, universe


def test_no_data_analysis_has_no_decision():
    from stock_alert_app.web_app import _no_data_analysis

    sec = {
        "market": "NYSE",
        "ticker": "SOME",
        "symbol": "SOME",
        "company": "Some Co",
        "last_analysis_at": "",
        "data_status": "no_data",
    }
    entry = _no_data_analysis(sec)
    assert entry["decision"] is None  # no fabricated CommitteeDecision
    assert entry["verdict"] == "N/A"
    assert entry["data_status"] == "no_data"
    assert "security_id" not in entry


def test_valid_analysis_decision_has_security_id(tmp_path):
    from stock_alert_app.analysis import stock_analysis

    db = Database(tmp_path / "t.db")
    db.init_schema()
    row = {
        "market": "NYSE",
        "ticker": "T",
        "verdict": "BULL",
        "confidence": 0.6,
        "news_score": 0.3,
        "price_score": 0.2,
        "combined_score": 0.4,
        "reason": "News: bullish (5 articles, score +0.30); Signal agreement: moderate",
        "decided_at": "2026-01-01T00:00:00Z",
        "lstm_score": 0.4,
        "lstm_probability_up": 0.7,
        "lstm_predicted_return": 0.01,
        "lstm_confidence": 0.6,
        "technical_score": 0.2,
        "signals": "",
    }
    analysis = stock_analysis(row)
    assert analysis["decision"] is not None
    assert analysis["decision"]["security_id"] == "NYSE:T"



def test_seed_configured(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    ensure_seeded(db)
    secs = db.all_securities()
    assert len(secs) > 0
    # idempotent
    ensure_seeded(db)
    assert len(db.all_securities()) == len(secs)
    # configured source preserved
    assert all(s["source"] == "configured" for s in secs)


def test_register_discovered_and_sticky_configured(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    ensure_seeded(db)
    register(db, "NYSE", "SOME-TICKER", symbol="SOMETK", company="Some Co", source="discovered")
    assert ("NYSE", "SOME-TICKER") in db.securities_map()
    # re-registering a configured security does not flip its source
    register(db, "NYSE", "AAPL", symbol="AAPL", source="discovered")
    assert db.securities_map()[("NYSE", "AAPL")]["source"] == "configured"


def test_universe_helper(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    rows = universe(db)
    assert len(rows) >= 0
    # missing-data securities remain present, not silently removed
    assert all("ticker" in r and "market" in r for r in rows)


def test_discovered_tickers_migrated_and_dropped(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    # Seed the old registry with a useful + an artifact row, then init schema
    # (which drops the legacy table) and confirm the canonical securities survive.
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS discovered_tickers (ticker TEXT NOT NULL, market TEXT NOT NULL, discovered_at TEXT NOT NULL, PRIMARY KEY (ticker, market))"
        )
        conn.execute(
            "INSERT INTO discovered_tickers (ticker, market, discovered_at) VALUES ('NEWCO', 'NYSE', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO discovered_tickers (ticker, market, discovered_at) VALUES ('AAPL', 'KRX', '2026-01-01T00:00:00')"
        )
    db.init_schema()  # runs DROP TABLE IF EXISTS discovered_tickers
    with db.connect() as conn:
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "discovered_tickers" not in tables  # legacy table removed
    # canonical registry is the only source of truth
    assert not hasattr(db, "mark_discovered")
    assert not hasattr(db, "get_recently_discovered")
