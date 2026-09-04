"""Ordered, recorded database migrations.

Lightweight alternative to heavyweight migration tools, matching the existing
architecture: every migration is an idempotent Python function that receives the
Database handle and runs inside a transaction. Applied migrations are recorded
in ``schema_migrations`` so repeated starts are no-ops.

Rules (production safety):
* Migrations are APPEND-ONLY: never edit an applied migration — add a new one.
* Data-bearing legacy structures are never dropped blindly. The only genuinely
  dead structure removed so far (``discovered_tickers`` — superseded by the
  ``securities`` registry with its data migrated) stays recorded here as the
  audited baseline; user/trading tables are never touched by removals.
* Baseline (0001) is stamped automatically: ``Database.init_schema`` already
  applies the idempotent CREATE-IF-NOT-EXISTS schema + legacy migrations, so a
  fresh database and a decade-old database converge to the same recorded state.

CLI:
    python -m stock_alert_app.migrate status
    python -m stock_alert_app.migrate upgrade
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .db import Database, utc_now

logger = logging.getLogger(__name__)


def _m0002_prod_indexes(db: Database) -> None:
    """Production read-path indexes (idempotent)."""
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_price_snapshots_fetched ON price_snapshots(fetched_at)",
        "CREATE INDEX IF NOT EXISTS idx_verdicts_decided ON verdicts(decided_at)",
        "CREATE INDEX IF NOT EXISTS idx_securities_market ON securities(market)",
        "CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at)",
        "CREATE INDEX IF NOT EXISTS idx_pg_updated ON portfolio_groups(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_security ON notification_events(security_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pt_trades_ts ON pt_trades(portfolio_id, timestamp)",
    )
    with db.connect() as conn:
        for stmt in statements:
            conn.execute(stmt)


#: Ordered migration registry. APPEND-ONLY.
MIGRATIONS: list[tuple[str, str, Callable[[Database], Any]]] = [
    ("0001_baseline", "Baseline schema + audited legacy migrations (init_schema)", lambda db: None),
    ("0002_prod_indexes", "Production read-path indexes", _m0002_prod_indexes),
]

_BASELINE_ID = "0001_baseline"


def _ensure_table(db: Database) -> None:
    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   migration_id TEXT PRIMARY KEY,
                   name TEXT NOT NULL DEFAULT '',
                   applied_at TEXT NOT NULL
               )"""
        )


def applied(db: Database) -> set[str]:
    _ensure_table(db)
    with db.connect() as conn:
        rows = conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    return {r["migration_id"] for r in rows}


def pending(db: Database) -> list[tuple[str, str, Callable[[Database], Any]]]:
    done = applied(db)
    return [m for m in MIGRATIONS if m[0] not in done]


def upgrade(db: Database) -> list[str]:
    """Apply all pending migrations in order. Returns the ids applied."""
    _ensure_table(db)
    done = applied(db)
    applied_now: list[str] = []
    for mid, name, fn in MIGRATIONS:
        if mid in done:
            continue
        # Baseline is stamped, not executed: init_schema already ran the
        # idempotent baseline on every start (fresh AND legacy databases).
        if mid == _BASELINE_ID:
            with db.connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (migration_id, name, applied_at) VALUES (?, ?, ?)",
                    (mid, name, utc_now()),
                )
            applied_now.append(mid)
            logger.info("migration %s stamped (baseline)", mid)
            continue
        logger.info("applying migration %s: %s", mid, name)
        fn(db)
        with db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_id, name, applied_at) VALUES (?, ?, ?)",
                (mid, name, utc_now()),
            )
        applied_now.append(mid)
        logger.info("applied migration %s", mid)
    return applied_now


def status(db: Database) -> dict[str, Any]:
    done = applied(db)
    return {
        "applied": [mid for mid, _, _ in MIGRATIONS if mid in done],
        "pending": [{"id": mid, "name": name} for mid, name, _ in MIGRATIONS if mid not in done],
        "up_to_date": not any(mid not in done for mid, _, _ in MIGRATIONS),
    }
