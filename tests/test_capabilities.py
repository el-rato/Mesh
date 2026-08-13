"""Tests for the canonical market capability model + committee status handling."""

from __future__ import annotations

from stock_alert_app import capabilities as cap
from stock_alert_app.config import settings
from stock_alert_app.dossier import committee_decision, committee_signals
from stock_alert_app.markets import load_markets


def test_capability_map_has_every_key():
    m = cap.capability_map({}, price=True, news=True)
    assert list(m.keys()) == list(cap.CAPABILITIES)
    assert m["price"] == "AVAILABLE"
    assert m["historical_price"] == "AVAILABLE"
    assert m["news"] == "AVAILABLE"
    assert m["social"] == "NO_DATA"
    assert m["fundamentals"] == "NO_DATA"
    assert m["institutional"] == "NO_DATA"
    assert m["research"] == "AVAILABLE"


def test_normalize_state():
    assert cap.normalize_state(True) == "AVAILABLE"
    assert cap.normalize_state(False) == "NO_DATA"
    assert cap.normalize_state(None) == "NO_DATA"
    assert cap.normalize_state("AVAILABLE") == "AVAILABLE"
    assert cap.normalize_state("NO_DATA") == "NO_DATA"
    assert cap.normalize_state("ERROR") == "ERROR"
    assert cap.normalize_state("STALE") == "STALE"
    assert cap.normalize_state("ok") == "AVAILABLE"
    assert cap.normalize_state("unavailable") == "NO_DATA"
    assert cap.normalize_state("garbage") == "NO_DATA"


def test_market_capabilities_honest():
    markets = load_markets(settings.markets_dir)
    nyse = markets["NYSE"].as_dict()
    epa = markets["EPA"].as_dict()
    # US: 13F institutional is available; Europe: not.
    assert nyse["capabilities"]["institutional"] == "AVAILABLE"
    assert epa["capabilities"]["institutional"] == "NO_DATA"
    # Price/news are available (Yahoo + news feeds configured).
    assert nyse["capabilities"]["price"] == "AVAILABLE"
    assert epa["capabilities"]["price"] == "AVAILABLE"
    # Fundamentals/social are genuinely unavailable (no provider).
    assert nyse["capabilities"]["fundamentals"] == "NO_DATA"
    assert nyse["capabilities"]["social"] == "NO_DATA"
    # market_id + exchange are canonical and do not break the code.
    assert nyse["market_id"] == "NYSE"
    assert epa["exchange"] == "Euronext Paris"


def _verdict(**kw):
    quant = {"status": "ok", "score": 0.6, "confidence": 0.7}
    social = {"status": "no_data", "score": None}
    regime = {"status": "no_data", "score": None}
    v = {
        "quantitative": quant,
        "social": social,
        "market_regime": regime,
        "technical": {"score": 0.2, "available": True},
        "news_score": 0.3,
        "news_available": True,
        "price": {"close": 100.0},
    }
    v.update(kw)
    return v


def test_committee_signal_status_available_and_no_data():
    dec = committee_decision(_verdict(), stale=False)
    by = {s["key"]: s for s in dec["contributing_signals"]}
    assert by["quant"]["status"] == "AVAILABLE"
    assert by["social"]["status"] == "NO_DATA"
    assert by["regime"]["status"] == "NO_DATA"


def test_committee_signal_status_stale():
    dec = committee_decision(_verdict(), stale=True)
    by = {s["key"]: s for s in dec["contributing_signals"]}
    assert by["quant"]["status"] == "STALE"
    assert by["technical"]["status"] == "STALE"
    # Unavailable signals stay NO_DATA (not STALE).
    assert by["social"]["status"] == "NO_DATA"


def test_missing_signal_is_not_bullish_or_bearish():
    # A missing news signal must not flip a bullish technical/quant into bear.
    v = _verdict(quant_score=0.8, news_available=False)
    c = committee_signals(v, None)
    assert c["verdict"] == "BULL"
    by = {s["key"]: s for s in c["signals"]}
    assert by["news"]["available"] is False
    assert by["news"]["state"] == "N/A"
