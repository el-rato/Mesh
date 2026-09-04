"""Migration CLI: python -m stock_alert_app.migrate [status|upgrade]"""

from __future__ import annotations

import argparse
import json
import sys

from .config import settings
from .db import Database
from . import migrations


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m stock_alert_app.migrate")
    parser.add_argument("command", choices=["status", "upgrade"], nargs="?", default="status")
    args = parser.parse_args()

    settings.validate_runtime()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init_schema()  # idempotent baseline schema + legacy migrations

    if args.command == "status":
        print(json.dumps(migrations.status(db), indent=2))
        return 0

    applied_now = migrations.upgrade(db)
    st = migrations.status(db)
    print(json.dumps({"applied_now": applied_now, **st}, indent=2))
    return 0 if st["up_to_date"] else 1


if __name__ == "__main__":
    sys.exit(main())
