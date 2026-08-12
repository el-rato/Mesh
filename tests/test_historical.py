"""Tests for the multi-source historical data service (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from datetime import datetime, timedelta

import pytest

from stock_alert_app import historical


@pytest.fixture(autouse=True)
def _fresh_cache():
    historical.clear_cache()
    yield
    historical.clear_cache()


def _settings(chain):
    return SimpleNamespace(historical_providers=tuple(chain))


def _rows(n=40, start_day=1, step_days=1):
    base = datetime(2026, 1, start_day)
    out = []
    for i in range(n):
        out.append({
            "date": (base + timedelta(days=i * step_days)).strftime("%Y-%m-%d 00:00"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
        })
    return out


def _stub(good_status="ok", rows=None, err=None):
    return lambda symbol, market, ticker, start, end, timeframe: {
        "status": good_status, "rows": rows or [], "error": err,
    }


def test_primary_success(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {"primary": _stub(rows=rows)})
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.SUCCESS
    assert r.provider == "primary"
    assert not r.fallback_used
    assert len(r.rows) == len(rows)


def test_fallback_to_secondary(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {
        "primary": _stub("unsupported"),
        "secondary": _stub(rows=rows),
    })
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.FALLBACK_SUCCESS
    assert r.provider == "secondary"
    assert r.fallback_used
    assert r.attempted_providers == ["primary", "secondary"]


def test_no_data_when_all_providers_fail(monkeypatch):
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {
        "primary": _stub("error", err="boom"),
        "secondary": _stub("error", err="kaput"),
    })
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.NO_DATA
    assert r.attempted_providers == ["primary", "secondary"]
    assert "boom" in r.provider_errors["primary"]


def test_invalid_data_is_rejected_and_falls_back(monkeypatch):
    rows = _rows()
    bad = [dict(rows[0], high=10, low=90)]  # impossible OHLC
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {
        "primary": _stub(rows=bad),
        "secondary": _stub(rows=rows),
    })
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.FALLBACK_SUCCESS
    assert r.provider == "secondary"


def test_partial_overlap_returns_partial(monkeypatch):
    # Only 3 of the requested 30 days are covered -> partial, but accepted.
    rows = _rows(n=5, start_day=28, step_days=1)  # covers 2026-01-28..02-01, overlaps end only
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {"primary": _stub(rows=rows)})
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.PARTIAL
    assert len(r.rows) == 5


def test_duplicate_timestamps_are_dropped(monkeypatch):
    rows = _rows(n=40)
    rows.append(dict(rows[0]))
    monkeypatch.setattr(historical, "settings", _settings(("primary", "secondary")))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {"primary": _stub(rows=rows)})
    r = historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert r.status == historical.SUCCESS
    assert len(r.rows) == 40


def test_success_is_cached_not_re_fetched(monkeypatch):
    rows = _rows()
    calls = {"n": 0}

    def counting_provider(symbol, market, ticker, start, end, timeframe):
        calls["n"] += 1
        return {"status": "ok", "rows": rows}

    historical.clear_cache()
    monkeypatch.setattr(historical, "settings", _settings(("primary",)))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: "TEST")
    monkeypatch.setattr(historical, "_PROVIDERS", {"primary": counting_provider})
    historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert calls["n"] == 1


def test_resolves_canonical_symbol(monkeypatch):
    seen = {}
    monkeypatch.setattr(historical, "settings", _settings(("primary",)))
    monkeypatch.setattr(historical, "symbol_for", lambda db, m, t: seen.setdefault("sym", f"{m}:{t}") or f"{m}:{t}")
    monkeypatch.setattr(historical, "_PROVIDERS", {"primary": _stub(rows=_rows())})
    historical.fetch(None, "NYSE", "T", "2026-01-01", "2026-01-30", "1d", min_rows=3)
    assert seen["sym"] == "NYSE:T"
