"""Tests for the Agent Workflow screening + Portfolio Groups.

These exercise the data-reliability + Agent-workflow-discovery + groups workflow
using an in-memory database seeded with deterministic price snapshots + verdicts,
so no network/provider calls are required. The Agent Workflow is the strategy
system: screening lives in ``agent_tools.screen_workflow`` (no separate engine).
"""

from __future__ import annotations

import tempfile
import pathlib
import uuid

from stock_alert_app.db import Database
from stock_alert_app.agent_tools import parse_strategy_text, screen_workflow


def _seed(db: Database) -> None:
    db.insert_price_snapshot("NASDAQ", "NVDA", close=120.0, momentum_20=0.08, rsi_14=68.0, sma_50=100.0, data_status="ready")
    db.insert_price_snapshot("NASDAQ", "AAPL", close=190.0, momentum_20=0.01, rsi_14=52.0, sma_50=185.0, data_status="ready")
    # A security with only a STALE snapshot (refresh failed) must still surface.
    db.insert_price_snapshot("NASDAQ", "MSFT", close=400.0, momentum_20=0.03, rsi_14=55.0, sma_50=390.0, data_status="stale", as_of="2020-01-01T00:00:00+00:00")

    for mkt, tkr, verd, conf in [
        ("NASDAQ", "NVDA", "BULL", 0.8),
        ("NASDAQ", "AAPL", "NEUTRAL", 0.5),
        ("NASDAQ", "MSFT", "BULL", 0.7),
    ]:
        db.insert_verdict(mkt, tkr, verd, conf, 0.1, 0.2, 0.3, "News: bullish (3 articles)", technical_score=0.4)
        db.upsert_security(mkt, tkr, symbol=tkr, company=tkr, source="configured")


def _db() -> Database:
    p = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    db = Database(p)
    db.init_schema()
    _seed(db)
    return db


def test_parse_strategy_text_extracts_criteria_and_caps_unverified():
    c = parse_strategy_text("large-cap stocks with strong momentum, bullish trend, increasing volume and high committee conviction")
    assert c["trend_bullish"] is True
    assert c["momentum_min"] == 0.02
    assert c["volume_ratio_min"] == 1.2
    assert c["conviction_min"] == 0.6
    # Market-cap language is noted as UNVERIFIED, never fabricated as a filter.
    assert any("market_cap" in n for n in c["notes"])
    res = screen_workflow("large-cap stocks with strong momentum, bullish trend", db=_db())
    assert res["market_cap_unverified"] is True
    for q in res["qualifying"]:
        assert any("UNVERIFIED" in e for e in q["explanation"])


def test_workflow_returns_real_securities_with_security_id():
    db = _db()
    res = screen_workflow("momentum + trend", criteria={"momentum_min": 0.02, "trend_bullish": True}, db=db, limit=10)
    ids = {q["security_id"] for q in res["qualifying"]}
    assert "NASDAQ:NVDA" in ids  # real data, not invented
    for q in res["qualifying"]:
        assert q["security_id"]
        assert isinstance(q["score"], (int, float))
        assert q["explanation"]


def test_workflow_stale_data_surfaces_with_stale_status():
    db = _db()
    res = screen_workflow("any", db=db, limit=50)
    msft = next((q for q in res["qualifying"] if q["security_id"] == "NASDAQ:MSFT"), None)
    assert msft is not None
    assert msft["price_status"] == "stale"
    assert msft["price_as_of"]


def test_workflow_not_evaluable_when_required_input_missing():
    db = _db()
    res = screen_workflow("volume confirm", criteria={"volume_ratio_min": 1.5}, db=db, limit=50)
    assert res["not_evaluable"], "expected NOT_EVALUABLE, got none"
    for n in res["not_evaluable"]:
        assert "volume" in n["missing_required"]
        assert n["match_class"] == "NOT_EVALUABLE"
        assert n["status"] in ("READY", "STALE", "NO_DATA", "NOT_EVALUABLE", "ERROR")


def test_workflow_results_expose_match_classes_and_contract():
    db = _db()
    res = screen_workflow(
        "momentum + trend", criteria={"momentum_min": 0.5}, db=db, limit=50
    )
    seen = {r["match_class"] for r in res["not_matching"]}
    assert "DOES_NOT_MATCH" in seen
    required = {
        "security_id", "ticker", "market", "company", "reasoning",
        "matched", "signals", "as_of", "status", "match_class",
    }
    for bucket in (res["qualifying"], res["not_matching"], res["not_evaluable"]):
        for r in bucket:
            assert required <= set(r)
            assert r["status"] in ("READY", "STALE", "NO_DATA", "NOT_EVALUABLE", "ERROR")
            assert r["match_class"] in ("MATCH", "DOES_NOT_MATCH", "NOT_EVALUABLE")
            assert r["reasoning"]


def test_portfolio_groups_crud_and_no_duplicates():
    p = pathlib.Path(tempfile.mkdtemp()) / "g.db"
    db = Database(p)
    db.init_schema()
    gid = f"grp_{uuid.uuid4().hex[:8]}"
    g = db.create_group(gid, "AI Leaders")
    assert g["group_id"] == gid
    db.add_to_group(gid, "NASDAQ", "NVDA")
    db.add_to_group(gid, "NASDAQ", "NVDA")  # dedup
    db.add_to_group(gid, "NASDAQ", "MSFT")
    g = db.get_group(gid)
    assert set(g["security_ids"]) == {"NASDAQ:NVDA", "NASDAQ:MSFT"}
    db.rename_group(gid, "AI + MSFT")
    assert db.get_group(gid)["name"] == "AI + MSFT"
    db.remove_from_group(gid, "NASDAQ", "NVDA")
    assert "NASDAQ:NVDA" not in db.get_group(gid)["security_ids"]
    assert db.delete_group(gid) is True
    assert db.get_group(gid) is None


def test_agent_workflow_group_metadata():
    p = pathlib.Path(tempfile.mkdtemp()) / "s.db"
    db = Database(p)
    db.init_schema()
    gid = f"grp_{uuid.uuid4().hex[:8]}"
    g = db.create_group(gid, "From Workflow", source="agent_workflow", strategy_name="momentum + trend")
    assert g["source"] == "agent_workflow"
    assert g["strategy_name"] == "momentum + trend"
    assert g["created_from_strategy_at"]
