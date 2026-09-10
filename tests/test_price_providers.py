from __future__ import annotations

import logging

import pandas as pd

from stock_alert_app.price_providers import (
    AlphaVantageProvider,
    PriceProvider,
    TwelveDataProvider,
    fetch_ohlcv,
    provider_status,
)


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000.0, 1100.0],
        },
        index=pd.date_range("2026-01-01", periods=2, freq="D"),
    )


def test_dossier_chart_uses_yfinance_before_api_key_providers() -> None:
    assert [row["name"] for row in provider_status()[:3]] == [
        "yfinance",
        "twelvedata",
        "alphavantage",
    ]


def test_keyed_backups_disabled_without_keys(monkeypatch) -> None:
    import types

    import stock_alert_app.price_providers as pp

    for var in ("ALPHA_VANTAGE_API_KEY", "ALPHA_VANTAGE_KEY", "TWELVE_DATA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pp, "_settings", types.SimpleNamespace())
    assert TwelveDataProvider().enabled() is False
    assert AlphaVantageProvider().enabled() is False
    assert TwelveDataProvider().fetch("AAPL", "1mo", "1d").empty


def test_keyed_backups_enabled_with_keys(monkeypatch) -> None:
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-test-key")
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "av-test-key")
    assert TwelveDataProvider().enabled() is True
    assert AlphaVantageProvider().enabled() is True


def test_twelvedata_backup_parses_time_series(monkeypatch) -> None:
    import json

    import stock_alert_app.price_providers as pp

    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-test-key")
    payload = {
        "values": [
            {"datetime": "2026-01-02", "open": "100", "high": "102", "low": "99", "close": "101", "volume": "1000"},
            {"datetime": "2026-01-03", "open": "101", "high": "103", "low": "100", "close": "102", "volume": "1100"},
        ]
    }
    monkeypatch.setattr(pp, "_http_text", lambda url, timeout=12.0: json.dumps(payload))
    df = TwelveDataProvider().fetch("AAPL", "1mo", "1d")
    assert list(df["Close"]) == [101.0, 102.0]


def test_alphavantage_backup_parses_daily_series(monkeypatch) -> None:
    import json

    import stock_alert_app.price_providers as pp

    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "av-test-key")
    payload = {
        "Time Series (Daily)": {
            "2026-01-03": {"1. open": "101", "2. high": "103", "3. low": "100", "4. close": "102", "5. volume": "1100"},
            "2026-01-02": {"1. open": "100", "2. high": "102", "3. low": "99", "4. close": "101", "5. volume": "1000"},
        }
    }
    monkeypatch.setattr(pp, "_http_text", lambda url, timeout=12.0: json.dumps(payload))
    df = AlphaVantageProvider().fetch("AAPL", "1mo", "1d")
    assert list(df["Close"]) == [101.0, 102.0]


def test_expected_404_continues_without_warning(caplog) -> None:
    class MissingProvider(PriceProvider):
        name = "test_missing_404"

        def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
            raise RuntimeError("404 not found")

    class GoodProvider(PriceProvider):
        name = "test_good_after_404"

        def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
            return _bars()

    with caplog.at_level(logging.WARNING, logger="stock_alert_app.price_providers"):
        result = fetch_ohlcv("MISSING", providers=[MissingProvider(), GoodProvider()])

    assert not result.empty
    assert not any("test_missing_404" in record.message for record in caplog.records)


def test_non_404_provider_error_remains_visible(caplog) -> None:
    class BrokenProvider(PriceProvider):
        name = "test_broken_network"

        def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
            raise RuntimeError("connection reset")

    class GoodProvider(PriceProvider):
        name = "test_good_after_network"

        def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
            return _bars()

    with caplog.at_level(logging.WARNING, logger="stock_alert_app.price_providers"):
        result = fetch_ohlcv("NETWORK", providers=[BrokenProvider(), GoodProvider()])

    assert not result.empty
    assert any("test_broken_network" in record.message for record in caplog.records)
