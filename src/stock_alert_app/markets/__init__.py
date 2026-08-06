from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str
    keywords: List[str] = field(default_factory=list)
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
    tickers: Dict[str, Ticker]
    rss_queries: List[str] = field(default_factory=list)
    financial_feeds: List[str] = field(default_factory=list)

    def get_ticker(self, symbol: str) -> Ticker:
        return self.tickers[symbol.upper()]


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
    )


def load_markets(markets_dir: Path) -> Dict[str, Market]:
    markets: Dict[str, Market] = {}
    for path in sorted(markets_dir.glob("*.json")):
        market = load_market(path)
        markets[market.code] = market
    return markets
