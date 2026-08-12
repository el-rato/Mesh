from __future__ import annotations

import pytest

from stock_alert_app.db import Database


def _insert(db: Database, market: str, ticker: str, reason: str, news_score: float) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO verdicts
               (market, ticker, verdict, confidence, news_score, price_score,
                combined_score, reason, decided_at)
               VALUES (?, ?, 'BULL', 0.5, ?, 0.1, 0.3, ?, '2026-01-01T00:00:00')""",
            (market, ticker, news_score, reason),
        )
        return int(cur.lastrowid)


def _reason(db: Database, row_id: int) -> str:
    with db.connect() as conn:
        row = conn.execute("SELECT reason FROM verdicts WHERE id = ?", (row_id,)).fetchone()
        return row["reason"]


class TestVerdictReasonMigration:
    def test_legacy_reason_migrated(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.init_schema()
        row_id = _insert(db, "BSE", "T", "LSTM Model; Auxiliary News Sentiment: bullish (+0.154); 20d momentum", 0.154)
        db.init_schema()  # runs the migration
        reason = _reason(db, row_id)
        assert "Auxiliary News Sentiment:" not in reason
        assert "News: bullish" in reason
        assert "+0.15" in reason

    def test_legacy_none_becomes_unavailable(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.init_schema()
        row_id = _insert(db, "BSE", "T2", "Auxiliary News Sentiment: None (+0.000)", 0.0)
        db.init_schema()
        assert "News: unavailable" in _reason(db, row_id)

    def test_idempotent(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.init_schema()
        _insert(db, "BSE", "T", "Auxiliary News Sentiment: bearish (+0.154)", 0.154)
        changed_first = db.init_schema()
        changed_second = db.init_schema()
        assert changed_second == 0
        with db.connect() as conn:
            reasons = [r["reason"] for r in conn.execute("SELECT reason FROM verdicts").fetchall()]
        assert all("Auxiliary News Sentiment:" not in r for r in reasons)

    def test_canonical_rows_untouched(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.init_schema()
        row_id = _insert(db, "BSE", "T", "News: bearish (10 articles, score -0.21); Signal agreement: weak", -0.21)
        db.init_schema()
        reason = _reason(db, row_id)
        assert "Auxiliary News Sentiment:" not in reason
        assert "News: bearish (10 articles" in reason

    def test_historical_label_and_score_preserved(self, tmp_path):
        db = Database(tmp_path / "t.db")
        db.init_schema()
        row_id = _insert(db, "BSE", "T", "Auxiliary News Sentiment: bearish (+0.154)", 0.154)
        db.init_schema()
        reason = _reason(db, row_id)
        assert "bearish" in reason
        assert "0.15" in reason
        with db.connect() as conn:
            score = conn.execute("SELECT news_score FROM verdicts WHERE id = ?", (row_id,)).fetchone()["news_score"]
        assert score == pytest.approx(0.154)
