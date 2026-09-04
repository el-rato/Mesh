"""Central logging configuration (structured when LOG_JSON=1)."""

from __future__ import annotations

import json
import logging
import sys

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("request_id", "path", "method", "status_code"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    """Idempotent root logging setup driven by LOG_LEVEL / LOG_JSON."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    from .config import settings

    level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn mirrors the app level so access logs stay consistent.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers[:] = [handler]
        logging.getLogger(name).setLevel(level)
    _CONFIGURED = True
