"""StockVerdict application package and web-server entry point."""

from __future__ import annotations

import argparse


def main() -> None:
    """Start the web application without restoring the removed CLI workflows."""
    parser = argparse.ArgumentParser(prog="stock-alert-app")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the StockVerdict web dashboard")
    serve.add_argument("--host", default=None, help="override HOST env (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="override PORT env (default 8000)")
    args = parser.parse_args()

    if args.command == "serve":
        from .config import settings
        from .logging_setup import setup_logging

        setup_logging()
        settings.validate_runtime()
        settings.ensure_dirs()
        import uvicorn

        uvicorn.run(
            "stock_alert_app.web_app:app",
            host=args.host or settings.serve_host,
            port=args.port or settings.serve_port,
        )
