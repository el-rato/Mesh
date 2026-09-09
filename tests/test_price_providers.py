from __future__ import annotations

import logging

import pandas as pd

from stock_alert_app.price_providers import PriceProvider, fetch_ohlcv


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
