"""Authentication and session management.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib, per-user random salt).
Sessions are server-side: the client receives an opaque random token in an
HTTP-only cookie; only the SHA-256 hash of that token is stored in the DB, so a
database leak does not expose usable session tokens. The authenticated user is
always derived from the session, never from client-provided fields.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Cookie, HTTPException, Request

from .config import settings
from .db import Database, utc_now

SESSION_COOKIE = "sv_session"
SESSION_TTL_HOURS = 24 * 30  # 30 days
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db: Database, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    db.create_session(_token_hash(token), user_id, expires)
    return token


def resolve_user(db: Database, token: str | None) -> dict[str, Any] | None:
    """Return the user for a session token, or None if invalid/expired."""
    if not token:
        return None
    session = db.get_session(_token_hash(token))
    if not session:
        return None
    try:
        if datetime.fromisoformat(session["expires_at"]) <= datetime.now(UTC):
            return None
    except (ValueError, TypeError):
        return None
    return db.get_user_by_id(session["user_id"])


def clear_session(db: Database, token: str | None) -> None:
    if token:
        db.delete_session(_token_hash(token))


def current_user(
    request: Request, sv_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    """FastAPI dependency: authenticated user from the session cookie."""
    from .web_app import _db

    db = _db()
    user = resolve_user(db, sv_session)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    request.state.user_id = user["id"]
    return user


def public_user(request: Request, sv_session: str | None = Cookie(default=None)) -> dict[str, Any] | None:
    """Optional user: returns None for anonymous requests (public endpoints)."""
    from .web_app import _db

    return resolve_user(_db(), sv_session)
