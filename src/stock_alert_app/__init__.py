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

    sentiment = sub.add_parser("sentiment", help="score unscored news and aggregate per ticker")
    sentiment.add_argument(
        "--no-finbert", action="store_true",
        help="use the lexicon scorer instead of FinBERT",
    )
    sentiment.add_argument(
        "--json", action="store_true", default=False,
        help="print aggregated sentiment as JSON",
    )

    verdict = sub.add_parser("verdict", help="fetch prices and emit bull/bear verdicts per ticker")
    verdict.add_argument(
        "--market", nargs="*", default=None,
        help="market codes to analyze (default: all configured markets)",
    )
    verdict.add_argument(
        "--no-finbert", action="store_true",
        help="use the lexicon scorer instead of FinBERT",
    )
    verdict.add_argument(
        "--json", action="store_true", default=False,
        help="print verdicts as JSON",
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

    if args.command == "sentiment":
        from .sentiment.pipeline import run_sentiment

        result = run_sentiment(prefer_finbert=not args.no_finbert)
        print(f"Scored {result.scored} headlines with {result.headlines and 'sentiment pipeline' or 'model'}")
        rows = sorted(result.headlines.items(), key=lambda kv: kv[1].score, reverse=True)
        if args.json:
            payload = {k: v.as_dict() for k, v in rows}
            print(json.dumps(payload, indent=2))
        else:
            for key, agg in rows:
                    marker = {"bullish": "BULL", "bearish": "BEAR", "neutral": "NEUT"}[agg.label]
                    print(
                        f"  {key:<16} {marker:<6} score={agg.score:+.3f} "
                        f"articles={agg.article_count} pos={agg.positive_count} "
                        f"neg={agg.negative_count} neu={agg.neutral_count}"
                    )
        return

    if args.command == "verdict":
        from .verdict import run_verdicts

        verdicts = run_verdicts(
            market_codes=args.market,
            prefer_finbert=not args.no_finbert,
        )
        if args.json:
            print(json.dumps({k: v.as_dict() for k, v in verdicts.items()}, indent=2))
        else:
            for key in sorted(verdicts):
                v = verdicts[key]
                print(
                    f"  {key:<16} {v.verdict:<6} conf={v.confidence:.2f} "
                    f"combined={v.combined_score:+.3f} news={v.news_score:+.3f} "
                    f"price={v.price_score:+.3f}"
                )
                print(f"      reason: {v.reason}")
        return

    scaffold()


if __name__ == "__main__":
    main()