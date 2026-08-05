from __future__ import annotations

import argparse
import json

from .config import settings
from .db import Database
from .markets import load_markets


def scaffold() -> None:
    settings.ensure_dirs()
    markets = load_markets(settings.markets_dir)

    db = Database(settings.db_path)
    db.init_schema()

    print("StockVerdict scaffold ready")
    print(f"  data dir : {settings.data_dir}")
    print(f"  db path  : {settings.db_path}")
    print(f"  markets  : {', '.join(sorted(markets))}")
    for code, market in markets.items():
        symbols = ", ".join(market.tickers.keys())
        print(f"    {code}: {market.name} ({len(market.tickers)} tickers: {symbols})")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-alert-app")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scaffold", help="initialize dirs, schema, and show registry")

    ingest = sub.add_parser("ingest", help="fetch news from sources")
    ingest.add_argument(
        "--market", nargs="*", default=None,
        help="market codes to ingest (default: all configured markets)",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        from .ingest import run_ingest

        results = run_ingest(market_codes=args.market)
        for code, res in results.items():
            print(
                f"  {code}: fetched={res.fetched} fetched_total={res.classified} "
                f"inserted={res.inserted} duplicate={res.duplicate}"
            )
        return

    scaffold()


if __name__ == "__main__":
    main()