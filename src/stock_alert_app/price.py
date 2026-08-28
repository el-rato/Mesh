from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    #: Data freshness state: "ready" (fresh) or "stale" (last-known-good kept
    #: after a failed/partial refresh). Never fabricated.
    data_status: str = "ready"
    #: When stale, the timestamp the value was originally valid (ISO). Empty for
    #: fresh data.
    as_of: str = ""

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
            "data_status": self.data_status,
            "as_of": self.as_of,
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
        data_status=state.data_status,
        as_of=state.as_of,
    )


def _stale_from_snapshot(market_code: str, ticker: str, db: Database) -> PriceState | None:
    """Build a STALE PriceState from the last-known-good snapshot.

    Used when a live refresh fails: we KEEP the previous valid values (marked
    STALE with ``as_of``) instead of dropping them and reverting the security to
    NO_DATA. Returns None when there is genuinely no prior data.
    """
    snap = db.latest_price_snapshot(market_code, ticker)
    if not snap or snap.get("close") is None:
        return None
    return PriceState(
        market=market_code,
        ticker=ticker,
        close=float(snap.get("close") or 0.0),
        open=float(snap.get("open") or 0.0),
        high=float(snap.get("high") or 0.0),
        low=float(snap.get("low") or 0.0),
        volume=int(snap.get("volume") or 0),
        momentum_20=float(snap.get("momentum_20") or 0.0),
        rsi_14=float(snap.get("rsi_14") or 50.0),
        sma_50=float(snap.get("sma_50") or 0.0),
        data_status="stale",
        as_of=snap.get("as_of") or snap.get("fetched_at") or "",
    )


def _fetch_one(market: Market, db: Database, symbol: str) -> tuple[str, PriceState] | None:
    from .resolve import OK, resolve

    tkr = market.tickers[symbol]
    res = resolve(market.code, symbol, tkr.name)
    if res["status"] != OK:
        # Provider could not validate a live symbol (not found / unavailable /
        # temporary error / possibly delisted). Fall back to last-known-good data
        # rather than discarding valid history; it is labelled STALE downstream.
        return symbol, _stale_from_snapshot(market.code, symbol, db)
    yahoo_symbol = res["symbol"]
    try:
        df = fetch_history(yahoo_symbol, period="6mo")
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", yahoo_symbol, exc)
        return symbol, _stale_from_snapshot(market.code, symbol, db)
    state = build_price_state(market.code, symbol, df)
    if state is None:
        logger.warning("No price data for %s", yahoo_symbol)
        return symbol, _stale_from_snapshot(market.code, symbol, db)
    return symbol, state


def fetch_market_prices(
    market: Market, db: Database, max_workers: int = 12
) -> dict[str, PriceState]:
    """Fetch prices for every ticker in a market concurrently (Fincept-style).

    Sequential per-symbol network calls were the bottleneck that made the
    scanner "stick" on a large universe; a bounded thread pool fetches the
    whole universe at once. A short-lived pool is created per market so its
    lifecycle is tied to the fetch and no thread lingers between cycles.
    """
    states: dict[str, PriceState] = {}
    symbols = list(market.tickers.keys())
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one, market, db, s) for s in symbols]
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001 - one ticker never aborts the batch
                logger.warning("Price worker failed: %s", exc)
                continue
            if res is None:
                continue
            symbol, state = res
            if state is None:
                # Genuinely no usable data has ever been obtained: leave as
                # NO_DATA (no snapshot row), never fabricate a value.
                continue
            store_price_state(db, state)
            states[symbol] = state
    return states


def run_price_fetch(
    market_codes: Iterable[str] | None = None,
    db_path: str | None = None,
) -> dict[str, PriceState]:
    from .ingest import _load_markets  # reuse market loading without duplicating
    from .markets import scan_market_codes

    markets = _load_markets()
    db = Database(db_path or settings.db_path)
    db.init_schema()

    codes = list(market_codes) if market_codes else scan_market_codes()
    states: dict[str, PriceState] = {}
    for code in codes:
        market = markets.get(code)
        if not market:
            logger.warning("Unknown market %s, skipping", code)
            continue
        fetched = fetch_market_prices(market, db)
        states.update(fetched)
    return states
