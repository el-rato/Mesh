from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

from ..capabilities import CAPABILITIES, capability_map


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str
    keywords: list[str] = field(default_factory=list)
    yahoo_suffix: str = ""

    @property
    def search_query(self) -> str:
        return " OR ".join([self.name] + self.keywords)


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    country: str
    currency: str
    timezone: str
    yahoo_suffix: str
    tickers: dict[str, Ticker]
    rss_queries: list[str] = field(default_factory=list)
    financial_feeds: list[str] = field(default_factory=list)
    session_open: str = "09:30"
    session_close: str = "16:00"
    enabled: bool = True
    # Canonical capability map (capability key -> AVAILABLE/NO_DATA/ERROR/STALE).
    capabilities: dict[str, str] = field(default_factory=dict)

    @property
    def market_id(self) -> str:
        """Canonical market id (== code); securities use ``market_id:ticker``."""
        return self.code

    @property
    def exchange(self) -> str:
        return self.name

    def get_ticker(self, symbol: str) -> Ticker:
        return self.tickers[symbol.upper()]

    def capability(self, key: str) -> str:
        return self.capabilities.get(key, "NO_DATA")

    def as_dict(self) -> dict[str, object]:
        caps = dict(self.capabilities)
        for key in CAPABILITIES:
            caps.setdefault(key, "NO_DATA")
        return {
            "code": self.code,
            "market_id": self.code,
            "name": self.name,
            "exchange": self.name,
            "country": self.country,
            "currency": self.currency,
            "timezone": self.timezone,
            "yahoo_suffix": self.yahoo_suffix,
            "session_open": self.session_open,
            "session_close": self.session_close,
            "enabled": self.enabled,
            "capabilities": caps,
            "tickers": sorted(self.tickers.keys()),
        }


def _social_configured() -> bool:
    """Social (Reddit) is a provider-level capability, not per-market."""
    from ..config import settings

    return bool(settings.reddit_client_id and settings.reddit_client_secret)


def load_market(path: Path) -> Market:
    data = json.loads(path.read_text(encoding="utf-8"))
    tickers = {
        symbol.upper(): Ticker(
            symbol=symbol.upper(),
            name=spec["name"],
            keywords=spec.get("keywords", []),
            yahoo_suffix=spec.get("yahoo_suffix", data.get("yahoo_suffix", "")),
        )
        for symbol, spec in data["tickers"].items()
    }
    session = data.get("session") or {}
    declared = data.get("capabilities") or data.get("coverage") or {}
    social = bool(declared.get("social")) if "social" in declared else _social_configured()
    has_price = bool(data.get("tickers"))
    capabilities = capability_map(
        declared,
        price=has_price,
        news=bool(data.get("financial_feeds") or data.get("rss_queries")),
        social=social,
        institutional=data.get("code") == "NYSE",
    )
    return Market(
        code=data["code"],
        name=data["name"],
        country=data["country"],
        currency=data["currency"],
        timezone=data["timezone"],
        yahoo_suffix=data.get("yahoo_suffix", ""),
        tickers=tickers,
        rss_queries=data.get("rss_queries", []),
        financial_feeds=data.get("financial_feeds", []),
        session_open=session.get("open", "09:30"),
        session_close=session.get("close", "16:00"),
        enabled=bool(data.get("enabled", True)),
        capabilities=capabilities,
    )


def load_markets(markets_dir: Path) -> dict[str, Market]:
    markets: dict[str, Market] = {}
    for path in sorted(markets_dir.glob("*.json")):
        market = load_market(path)
        markets[market.code] = market
    return markets


def enabled_markets(markets_dir: Path | None = None) -> dict[str, Market]:
    """Enabled markets (data-driven; a market is excluded only when disabled)."""
    from ..config import settings

    markets = load_markets(markets_dir or settings.markets_dir)
    return {code: m for code, m in markets.items() if m.enabled}


def enabled_market_codes(markets_dir: Path | None = None) -> list[str]:
    """Codes of enabled markets, used to drive the scanner/refresh universe."""
    return list(enabled_markets(markets_dir).keys())


def scan_market_codes(markets_dir: Path | None = None) -> list[str]:
    """Codes the scanner/refresh should process: enabled markets, with the
    legacy ``default_markets`` config as a fallback if nothing is enabled."""
    from ..config import settings

    codes = enabled_market_codes(markets_dir)
    return codes or list(settings.default_markets)


def _parse_time(value: str) -> dtime:
    return dtime.fromisoformat(value.strip())


def market_status(market: Market, now_utc: datetime) -> dict[str, object]:
    """Current status of a market in its own configured timezone/session.

    Weekends are closed. ``opened_at``/``closes_at`` are the local session times.
    """
    local = now_utc.astimezone(ZoneInfo(market.timezone))
    today = local.date()
    is_weekend = today.weekday() >= 5
    open_t = _parse_time(market.session_open)
    close_t = _parse_time(market.session_close)
    local_now = local.time()
    is_open = not is_weekend and open_t <= local_now < close_t
    opening_soon = (
        not is_weekend
        and not is_open
        and (datetime.combine(today, open_t) - local.replace(tzinfo=None)).total_seconds() <= 1800
    )
    return {
        "code": market.code,
        "name": market.name,
        "timezone": market.timezone,
        "status": "open" if is_open else "closed",
        "is_weekend": is_weekend,
        "opening_soon": opening_soon,
        "local_time": local.strftime("%H:%M"),
        "local_date": today.isoformat(),
        "opened_at": market.session_open,
        "closes_at": market.session_close,
    }
