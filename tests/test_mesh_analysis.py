from __future__ import annotations

import json

from stock_alert_app import verdict as verdict_module
from stock_alert_app import web_app


class _DB:
    def __init__(self):
        self.rows = []

    def latest_price_snapshot(self, market, ticker):
        return {"market": market, "ticker": ticker, "fetched_at": "data-1", "close": 100}

    def latest_verdicts(self, market=None):
        return list(self.rows)

    def update_verdict_signals(self, market, ticker, signals):
        self.rows = [{"market": market, "ticker": ticker, "decided_at": "analysis-1", "signals": signals}]


class _Verdict:
    price = object()
    news_available = False
    _mesh = {"direction": "BULL", "regime": "RANGE", "forecasts": [{"status": "ok"}]}
    signals_json = json.dumps({"mesh_signal": _mesh})

    def as_dict(self):
        return {"mesh_signal": dict(self._mesh)}


def _item():
    return {"market": "NYSE", "ticker": "AAPL", "symbol": "AAPL", "supported": True}


def test_dossier_get_stays_lightweight(monkeypatch):
    db = _DB()
    called = []
    monkeypatch.setattr(web_app, "_db", lambda: db)
    monkeypatch.setattr(web_app, "_dossier_target", lambda *args: (_item(), "AAPL"))
    monkeypatch.setattr(web_app, "_dossier_response", lambda *args, **kwargs: {"mesh_analysis": {"status": "IDLE"}})
    monkeypatch.setattr(verdict_module, "live_verdict", lambda *args, **kwargs: called.append(args))

    result = web_app.stock_dossier(market="NYSE", ticker="AAPL")

    assert result["mesh_analysis"]["status"] == "IDLE"
    assert called == []


def test_mesh_analysis_is_explicit_single_symbol_and_cached(monkeypatch):
    db = _DB()
    calls = []
    web_app._mesh_cache.clear()
    monkeypatch.setattr(web_app, "_db", lambda: db)
    monkeypatch.setattr(web_app, "_dossier_target", lambda *args: (_item(), "AAPL"))
    monkeypatch.setattr(web_app, "_dossier_response", lambda item, db, **kwargs: {
        "mesh_analysis": kwargs.get("mesh_meta") or {"status": "READY", "data_timestamp": "data-1"},
    })
    monkeypatch.setattr(verdict_module, "live_verdict", lambda market, ticker, company: calls.append((market, ticker)) or _Verdict())
    monkeypatch.setattr("stock_alert_app.analysis.apply_canonical", lambda payload: payload)

    first = web_app.run_mesh_analysis(web_app.MeshAnalysisRequest(market="NYSE", ticker="AAPL"))
    second = web_app.run_mesh_analysis(web_app.MeshAnalysisRequest(market="NYSE", ticker="AAPL"))

    assert calls == [("NYSE", "AAPL")]
    assert first["mesh_analysis"]["status"] == "READY"
    assert second["mesh_analysis"]["cache_hit"] is True
