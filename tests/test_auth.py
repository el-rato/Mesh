"""Authentication + market-expansion tests (no network)."""

from __future__ import annotations

import pytest

from stock_alert_app import auth, paper
from stock_alert_app.config import settings
from stock_alert_app.db import Database
from stock_alert_app.markets import enabled_market_codes, load_markets


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong password", h)


def test_password_hash_is_salted():
    h1 = auth.hash_password("same-password")
    h2 = auth.hash_password("same-password")
    assert h1 != h2
    assert auth.verify_password("same-password", h1)
    assert auth.verify_password("same-password", h2)


def test_verify_password_rejects_malformed():
    assert not auth.verify_password("x", "not-a-valid-hash")
    assert not auth.verify_password("x", "")


# ---------------------------------------------------------------------------
# User + session persistence
# ---------------------------------------------------------------------------


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    return db


def test_user_session_roundtrip(tmp_path):
    db = _db(tmp_path)
    db.create_user("u1", "A@Example.com", auth.hash_password("password123"))
    user = db.get_user_by_email("a@example.com")  # normalized to lowercase
    assert user is not None and user["id"] == "u1"

    token = auth.new_session(db, "u1")
    assert auth.resolve_user(db, token)["email"] == "a@example.com"
    assert auth.resolve_user(db, "bogus-token") is None

    auth.clear_session(db, token)
    assert auth.resolve_user(db, token) is None


# ---------------------------------------------------------------------------
# Paper ownership isolation
# ---------------------------------------------------------------------------


def test_paper_ownership_isolation(tmp_path):
    db = _db(tmp_path)
    db.upsert_paper_portfolio("SESS-A", 100000.0, user_id="userA")
    db.insert_paper_order(
        "ORD-1", "SESS-A", "LSE", "ULVR", "BUY", 100.0, 42.0, 1.0,
        "2024-01-01T10:00:00", None, "", user_id="userA",
    )

    # User A sees their own data.
    assert db.active_portfolio("userA") is not None
    assert len(db.paper_orders("SESS-A", "userA")) == 1

    # User B sees nothing of A's portfolio and gets their own fresh session.
    assert db.active_portfolio("userB") is None
    assert db.paper_orders("SESS-A", "userB") == []
    state_b = paper.portfolio_state(db, record_equity=False, user_id="userB")
    assert state_b["session_id"] != "SESS-A"
    assert state_b["positions"] == []


# ---------------------------------------------------------------------------
# Auth endpoints (register/login/me/protected) via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    import stock_alert_app.web_app as web_app
    from fastapi.testclient import TestClient

    db = Database(tmp_path / "t.db")
    db.init_schema()
    monkeypatch.setattr(web_app, "_db", lambda: db)
    return TestClient(web_app.app)


def test_register_login_me_logout(client):
    r = client.post("/api/auth/register", json={"email": "trader@example.com", "password": "password123"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "trader@example.com"

    # Session cookie persisted -> /me works on a fresh request.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "trader@example.com"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_login_invalid_credentials(client):
    client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"}).status_code == 200


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"email": "not-an-email", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@example.com", "password": "short"}).status_code == 422
    client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
    assert client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"}).status_code == 409


def test_protected_paper_route_requires_auth(client):
    assert client.get("/api/paper/portfolio").status_code == 401
    assert client.post("/api/paper/order", json={"market": "LSE", "ticker": "ULVR", "side": "BUY", "quantity": 1}).status_code == 401
    # Authenticated access is allowed (data may be empty, but not 401).
    client.post("/api/auth/register", json={"email": "b@example.com", "password": "password123"})
    assert client.get("/api/paper/portfolio").status_code == 200


# ---------------------------------------------------------------------------
# Market expansion
# ---------------------------------------------------------------------------


def test_markets_are_data_driven():
    markets = load_markets(settings.markets_dir)
    assert "EPA" in markets  # France (Euronext Paris)
    assert "SIX" in markets  # Switzerland
    assert "LSE" in markets and "NYSE" in markets
    for m in markets.values():
        assert m.as_dict()["coverage"]["price"] in (True, False)
    codes = enabled_market_codes()
    assert "EPA" in codes and "SIX" in codes and "LSE" in codes


def test_market_coverage_partial_support():
    markets = load_markets(settings.markets_dir)
    # Institutional (13F) is US-only; European markets expose it as unavailable.
    assert markets["EPA"].as_dict()["coverage"]["institutional"] is False
    assert markets["EPA"].as_dict()["coverage"]["price"] is True
