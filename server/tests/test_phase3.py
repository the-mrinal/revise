"""Phase 3: Revise without Supabase sign-in (SUPABASE_AUTH=off)."""

import time
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from jose import jwt

import auth
import database
import import_supabase_sessions
import main as main_module
from conftest import make_supabase_shaped
from test_auth import U1, U2, stored_tokens, supabase_token

SESSION = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture
def app(local_db):
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    return TestClient(main_module.app)


@pytest.fixture
def supabase_off(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_AUTH", False)

    def must_not_call(token):
        raise AssertionError("Supabase was called with sign-in switched off")

    monkeypatch.setattr(auth, "refresh_session", must_not_call)
    monkeypatch.setattr(main_module, "send_magic_link", must_not_call)


def test_supabase_tokens_refused_then_imported_session_takes_over(app, supabase_off):
    old_access = supabase_token(U1, "one@example.com")
    assert app.get("/api/me", headers={"Authorization": f"Bearer {old_access}"}).status_code == 401
    database.remember_legacy_token(auth.token_hash("imported-supa-rt"), U1, "import")
    r = app.post("/api/auth/refresh", json={"refresh_token": "imported-supa-rt"})
    assert r.status_code == 200 and r.json()["refresh_token"].startswith("rv_")
    me = app.get("/api/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["user_id"] == U1


def test_unknown_supabase_token_is_401_without_asking_supabase(app, supabase_off):
    assert app.post("/api/auth/refresh", json={"refresh_token": "never-seen"}).status_code == 401


def test_revise_sessions_unaffected(app, supabase_off):
    tokens = auth.start_session(U1, "one@example.com")
    assert app.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 200


def test_email_links_retired(app, supabase_off):
    r = app.post("/api/auth/magic-link", json={"email": "one@example.com"})
    assert r.status_code == 410 and "GitHub" in r.json()["detail"]
    assert app.get("/api/auth/callback", params={"token_hash": "h", "type": "magiclink"}).status_code == 410
    assert app.get("/api/auth/config").json()["email_links"] is False


def test_server_starts_without_supabase_settings(monkeypatch):
    """A fresh install with SUPABASE_AUTH=off needs no Supabase project."""
    import importlib

    for var in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET"):
        monkeypatch.delenv(var)
    monkeypatch.setenv("SUPABASE_AUTH", "off")
    try:
        reloaded = importlib.reload(auth)
        assert reloaded.SUPABASE_AUTH is False
        monkeypatch.setenv("SUPABASE_AUTH", "on")
        with pytest.raises(RuntimeError, match="SUPABASE_AUTH is on"):
            importlib.reload(auth)
    finally:
        monkeypatch.undo()
        importlib.reload(auth)


# --- one-time sign-in links ------------------------------------------------------------


def _admin_headers():
    database.set_user_admin(U1, True)
    return {"Authorization": f"Bearer {auth.start_session(U1, 'one@example.com')['access_token']}"}


def test_sign_in_link_works_once(app):
    r = app.post("/api/admin/sign-in-link", json={"email": "two@example.com"}, headers=_admin_headers())
    assert r.status_code == 200 and r.json()["expires_in_hours"] == 72
    token = parse_qs(urlparse(r.json()["url"]).query)["token"][0]
    first = app.get("/api/auth/one-time", params={"token": token})
    assert jwt.get_unverified_claims(stored_tokens(first)["access_token"])["sub"] == U2
    second = app.get("/api/auth/one-time", params={"token": token})
    assert second.status_code == 410 and "setItem('auth'" not in second.text
    assert database.get_recent_audit()[0]["action"] == "sign_in_link"


def test_sign_in_links_expire_and_need_an_admin(app):
    expired = jwt.encode(
        {"sub": U2, "jti": "x", "iss": "revise", "aud": "one-time-sign-in",
         "iat": int(time.time()) - 7200, "exp": int(time.time()) - 60},
        auth.REVISE_JWT_SECRET, algorithm="HS256",
    )
    assert app.get("/api/auth/one-time", params={"token": expired}).status_code == 401
    # An access token isn't a sign-in link, and a non-admin can't create one.
    access = auth.start_session(U2, "two@example.com")["access_token"]
    assert app.get("/api/auth/one-time", params={"token": access}).status_code == 401
    r = app.post("/api/admin/sign-in-link", json={"email": "one@example.com"},
                 headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 403


# --- importing Supabase's sessions -----------------------------------------------------


def test_import_copies_valid_sessions_and_their_parents(make_database, local_db, monkeypatch):
    src = make_database()
    make_supabase_shaped(src)
    with psycopg.connect(src, autocommit=True) as c:
        c.execute(
            "CREATE TABLE auth.sessions (id uuid PRIMARY KEY, not_after timestamptz);"
            "CREATE TABLE auth.refresh_tokens (token varchar, parent varchar, user_id varchar,"
            " revoked boolean, session_id uuid)"
        )
        c.execute("INSERT INTO auth.sessions VALUES (%s, NULL), (gen_random_uuid(), now() - interval '1 day')",
                  (SESSION,))
        expired_session = c.execute("SELECT id FROM auth.sessions WHERE not_after IS NOT NULL").fetchone()[0]
        c.execute(
            "INSERT INTO auth.refresh_tokens VALUES "
            "('live1', 'parent1', %s, false, %s),"      # valid, with the older token too
            "('parent1', NULL, %s, true, %s),"          # revoked itself, but kept as live1's parent
            "('gone', NULL, %s, true, %s),"             # revoked, nobody's parent
            "('stranger', NULL, '99999999-9999-9999-9999-999999999999', false, %s),"  # unknown user
            "('timeboxed', NULL, %s, false, %s)",       # session past its time limit
            (U1, SESSION, U1, SESSION, U2, SESSION, SESSION, U2, expired_session),
        )
    monkeypatch.setenv("SUPABASE_DB_URL", src)
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")

    import_supabase_sessions.main()
    stored = {r["token_hash"]: r["user_id"] for r in database.db.fetch_all(
        "SELECT token_hash, user_id FROM legacy_refresh_tokens")}
    assert stored == {auth.token_hash("live1"): U1, auth.token_hash("parent1"): U1}

    import_supabase_sessions.main()  # safe to run again
    assert len(database.db.fetch_all("SELECT 1 FROM legacy_refresh_tokens")) == 2
