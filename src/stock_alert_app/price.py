from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from .config import settings
from .db import Database
from .markets import Market

logger = logging.getLogger(__name__)


@dataclass
class PriceState:
    market: str
    ticker: str
    close: float
    open: float
    high: float
    low: float
    volume: int
    momentum_20: float
    rsi_14: float
    sma_50: float
    sma_200: float = 0.0
    trend_50_200: float = 0.0
    price_above_sma_50: bool = False

    def as_dict(self) -> dict[str, float | str | int | bool]:
        return {
            "symbol": self.ticker,
            "close": self.close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "momentum_20": round(self.momentum_20, 4),
            "rsi_14": round(self.rsi_14, 4),
            "sma_50": round(self.sma_50, 4),
            "sma_200": round(self.sma_200, 4),
            "trend_50_200": round(self.trend_50_200, 4),
            "above_sma_50": self.price_above_sma_50,
        }


def fetch_history(
    symbol: str, period: str = "6mo", interval: str = "1d"
) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty or "Close" not in df.columns:
        return pd.DataFrame()
    return df


def _safe_mean(values: list[float]) -> float:
    valid = [v for v in values if v is not None and not math.isnan(v)]
    return sum(valid) / len(valid) if valid else 0.0


def compute_rsi(closes: list[float], window: int = 14) -> float:
    if len(closes) < window + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def build_price_state(market: str, symbol: str, df: pd.DataFrame) -> PriceState | None:
    if df.empty or len(df) < 2:
        return None
    df_valid = df.dropna(subset=["Close"])
    if df_valid.empty:
        return None
    closes = df_valid["Close"].tolist()
    if not closes:
        return None

    last = df_valid.iloc[-1]
    close = float(last["Close"])
    open_ = float(last.get("Open", close))
    high = float(last.get("High", close))
    low = float(last.get("Low", close))
    volume = int(float(last.get("Volume", 0)))

    momentum_20 = (closes[-1] - closes[-21]) / closes[-21] if len(closes) > 21 else 0.0
    rsi_14 = compute_rsi(closes)
    sma_50 = _safe_mean(closes[-50:]) if len(closes) >= 50 else _safe_mean(closes)
    sma_200 = _safe_mean(closes[-200:]) if len(closes) >= 200 else 0.0
    trend_50_200 = (sma_50 - sma_200) / sma_200 if sma_200 else 0.0
    above_sma_50 = close > sma_50 if sma_50 else False

    return PriceState(
        market=market,
        ticker=symbol,
        close=close,
        open=open_,
        high=high,
        low=low,
        volume=volume,
        momentum_20=momentum_20,
        rsi_14=rsi_14,
        sma_50=sma_50,
        sma_200=sma_200,
        trend_50_200=trend_50_200,
        price_above_sma_50=above_sma_50,
    )


def store_price_state(db: Database, state: PriceState) -> None:
    db.insert_price_snapshot(
        market=state.market,
        ticker=state.ticker,
        close=state.close,
        open=state.open,
        high=state.high,
        low=state.low,
        volume=state.volume,
        momentum_20=state.momentum_20,
        rsi_14=state.rsi_14,
        sma_50=state.sma_50,
    )


def fetch_market_prices(market: Market, db: Database) -> dict[str, PriceState]:
    from .resolve import resolve_for_fetch

    states: dict[str, PriceState] = {}
    for symbol in market.tickers:
        tkr = market.tickers[symbol]
        yahoo_symbol = resolve_for_fetch(market.code, symbol, tkr.name)
        if not yahoo_symbol:
            # Unavailable/invalid symbols are skipped quietly; the resolver caches
            # the outcome so they are not re-requested every cycle.
            continue
        try:
            df = fetch_history(yahoo_symbol, period="6mo")
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", yahoo_symbol, exc)
            continue
        state = build_price_state(market.code, symbol, df)
        if state is None:
            logger.warning("No price data for %s", yahoo_symbol)
            continue
        store_price_state(db, state)
        states[symbol] = state
    return states


def run_price_fetch(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
) -> dict[str, PriceState]:
    from .ingest import _load_markets  # reuse market loading without duplicating

    markets = _load_markets()
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else list(settings.default_markets)
    states: dict[str, PriceState] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            logger.warning("Unknown market %s, skipping", code)
            continue
        fetched = fetch_market_prices(market, db)
        states.update(fetched)
    return states
