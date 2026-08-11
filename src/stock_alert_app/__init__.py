"""StockVerdict application package and web-server entry point."""

from __future__ import annotations

import argparse


def main() -> None:
    """Start the web application without restoring the removed CLI workflows."""
    parser = argparse.ArgumentParser(prog="stock-alert-app")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the StockVerdict web dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("stock_alert_app.web_app:app", host=args.host, port=args.port)
