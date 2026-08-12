from __future__ import annotations

import time

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from stock_alert_app import resolve


@pytest.fixture(autouse=True)
def _reset():
    resolve.clear_cache()
    yield
    resolve.clear_cache()


def _df():
    return pd.DataFrame(
        {"Open": [1, 2], "High": [2, 3], "Low": [0, 1], "Close": [1.5, 2.5], "Volume": [10, 20]}
    )


class TestResolve:
    def test_valid_ticker_resolves(self, monkeypatch):
        monkeypatch.setattr(resolve, "fetch_history", lambda sym, period="5d": _df())
        assert resolve.resolve_for_fetch("NYSE", "AAPL") == "AAPL"

    def test_invalid_ticker_symbol_not_found(self, monkeypatch):
        monkeypatch.setattr(resolve, "fetch_history", lambda sym, period="5d": pd.DataFrame())
        monkeypatch.setattr(resolve, "_yahoo_search", lambda q, count=15: [])
        result = resolve.resolve("NYSE", "ZZZZINVALID", "")
        assert result["status"] == resolve.SYMBOL_NOT_FOUND
        assert resolve.resolve_for_fetch("NYSE", "ZZZZINVALID", "") is None

    def test_corporate_action_no_substitution(self, monkeypatch):
        # Empty history + company found only under a different security -> the
        # resolver must NOT substitute and reports data unavailable.
        monkeypatch.setattr(resolve, "fetch_history", lambda sym, period="5d": pd.DataFrame())
        monkeypatch.setattr(
            resolve,
            "_yahoo_search",
            lambda q, count=15: [
                {"symbol": "TMCV.BO", "quoteType": "EQUITY", "longname": "Tata Motors Commercial Vehicles Limited"}
            ],
        )
        result = resolve.resolve("BSE", "TATAMOTORS", "Tata Motors Limited")
        assert result["status"] == resolve.DATA_UNAVAILABLE
        assert result["symbol"] == ""
        assert "corporate action" in result["note"]

    def test_rate_limit_is_temporary(self, monkeypatch):
        def raiser(sym, period="5d"):
            raise YFRateLimitError("rate limited")

        monkeypatch.setattr(resolve, "fetch_history", raiser)
        result = resolve.resolve("NYSE", "X", "")
        assert result["status"] == resolve.TEMPORARY_PROVIDER_ERROR

    def test_repeated_failures_are_cached(self, monkeypatch):
        calls = {"n": 0}

        def empty(sym, period="5d"):
            calls["n"] += 1
            return pd.DataFrame()

        monkeypatch.setattr(resolve, "fetch_history", empty)
        monkeypatch.setattr(resolve, "_yahoo_search", lambda q, count=15: [])
        resolve.resolve("NYSE", "BAD", "")
        resolve.resolve("NYSE", "BAD", "")
        assert calls["n"] == 1  # second call served from cache

    def test_unknown_market(self):
        result = resolve.resolve("NOPE", "AAPL", "")
        assert result["status"] == resolve.SYMBOL_NOT_FOUND

    def test_rename_with_same_name_is_accepted(self, monkeypatch):
        # A genuine ticker change where the provider name still matches exactly.
        monkeypatch.setattr(
            resolve,
            "search_universe",
            lambda q, limit=15, market_filter="": [
                {"symbol": "NEWCO.NEW", "company": "Acme Corp", "supported": True}
            ],
        )
        monkeypatch.setattr(
            resolve,
            "fetch_history",
            lambda sym, period="5d": _df() if sym == "NEWCO.NEW" else pd.DataFrame(),
        )
        result = resolve.resolve("NYSE", "OLDCO", "Acme Corp")
        assert result["status"] == resolve.OK
        assert result["symbol"] == "NEWCO.NEW"
