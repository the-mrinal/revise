"""Connecting a Revise account to Sunday: /api/sunday/*, the one-time link's
`next`, /api/me's `sunday`, and Settings' Disconnect."""

import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from jose import jwt

import auth
import auth_pages
import database
import db
import main as main_module
import sunday
from test_auth import U1, U2, next_path, stored_tokens

SECRET = "sunday-shared-secret-for-tests"
SUNDAY = "https://sunday.test"
TOKEN = "sunday-token-0123456789abcdef"
HEADERS = {"X-Revise-Secret": SECRET}


@pytest.fixture
def app(local_db, monkeypatch):
    monkeypatch.setenv("SUNDAY_URL", SUNDAY + "/")
    monkeypatch.setenv("REVISE_SUNDAY_SECRET", SECRET)
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    return TestClient(main_module.app)


def connect(app, **body):
    body = {"github_id": 4242, "login": "ananya", "email": "ananya@example.com", "token": TOKEN, **body}
    return app.post("/api/sunday/connect", json=body, headers=HEADERS)


def link_row(user_id):
    return db.fetch_one("SELECT * FROM sunday_links WHERE user_id = %s", (user_id,))


def user_count():
    return db.fetch_one("SELECT count(*) AS n FROM users")["n"]


def bearer(user_id, email=None):
    return {"Authorization": f"Bearer {auth.start_session(user_id, email)['access_token']}"}


def one_time_token(user_id):
    return auth.sign_purpose_token({"sub": user_id, "jti": f"jti-{time.time_ns()}"}, "one-time-sign-in", 300)


# --- connect ------------------------------------------------------------------------------


def test_connect_makes_a_new_account_and_signs_it_in_back_to_sunday(app):
    before = user_count()
    r = connect(app)
    assert r.status_code == 200
    uid = r.json()["revise_user_id"]
    assert user_count() == before + 1
    assert database.get_user(uid)["email"] == "ananya@example.com"
    assert database.get_identity_user("github", "4242") == uid
    assert link_row(uid)["sunday_token"] == TOKEN and link_row(uid)["sunday_login"] == "ananya"

    url = r.json()["signin_url"]
    assert url.startswith(f"{auth.SERVER_URL}/api/auth/one-time?token=")
    assert url.endswith("&next=https%3A%2F%2Fsunday.test%2Frevise")
    token = parse_qs(urlparse(url).query)["token"][0]
    claims = jwt.get_unverified_claims(token)
    assert claims["aud"] == "one-time-sign-in" and claims["exp"] - claims["iat"] == 300

    page = app.get(url.removeprefix(auth.SERVER_URL))
    assert page.status_code == 200
    assert "Signed in. Taking you back to Sunday…" in page.text
    assert next_path(page) == f"{SUNDAY}/revise"
    assert "setTimeout" in page.text
    # The session is stored by the page's first script, before anything else.
    assert page.text.index("setItem('auth'") < page.text.index("<style>")
    tokens = stored_tokens(page)
    assert jwt.get_unverified_claims(tokens["access_token"])["sub"] == uid

    me = app.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert me["sunday"]["login"] == "ananya" and me["sunday"]["connected_at"]
    # The link works once.
    assert app.get(url.removeprefix(auth.SERVER_URL)).status_code == 410


def test_connect_finds_the_account_by_github_id_first(app):
    database.link_identity(U1, "github", "4242", "old-login", "one@example.com")
    before = user_count()
    r = connect(app, email="two@example.com")  # U2's email: the GitHub id wins
    assert r.status_code == 200 and r.json()["revise_user_id"] == U1
    assert user_count() == before
    assert link_row(U1) and not link_row(U2)
    assert database.get_user_identity(U1, "github")["login"] == "ananya"


def test_connect_finds_the_account_by_email(app):
    before = user_count()
    r = connect(app, email="Two@Example.com")
    assert r.status_code == 200 and r.json()["revise_user_id"] == U2
    assert user_count() == before
    assert database.get_identity_user("github", "4242") == U2
    assert link_row(U2)["sunday_token"] == TOKEN


def test_connecting_again_replaces_the_token(app):
    uid = connect(app).json()["revise_user_id"]
    assert connect(app, token="a-second-token-0123456789").json()["revise_user_id"] == uid
    assert link_row(uid)["sunday_token"] == "a-second-token-0123456789"


def test_connect_needs_the_secret(app):
    before = user_count()
    for headers in ({"X-Revise-Secret": "wrong"}, {}):
        r = app.post("/api/sunday/connect", headers=headers,
                     json={"github_id": 1, "login": "x", "email": "x@example.com", "token": TOKEN})
        assert r.status_code == 401
    assert user_count() == before
    assert db.fetch_one("SELECT count(*) AS n FROM sunday_links")["n"] == 0


def test_without_the_secret_the_routes_are_not_there(app, monkeypatch):
    monkeypatch.setenv("REVISE_SUNDAY_SECRET", "")
    assert app.post("/api/sunday/connect", json={}, headers={"X-Revise-Secret": ""}).status_code == 404
    assert app.post("/api/sunday/unlink", json={"token": TOKEN}).status_code == 404
    assert app.post("/api/sunday/disconnect", headers=bearer(U1)).status_code == 404
    assert app.get("/api/me", headers=bearer(U1, "one@example.com")).json()["sunday"] is None


# --- the one-time link's next ---------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "https://evil.test/revise",
    "https://sunday.test.evil.test/revise",
    "https://sunday.testevil/revise",
    "/dashboard?x=1",
    "javascript:alert(1)",
])
def test_next_outside_sunday_is_ignored(app, bad):
    r = app.get("/api/auth/one-time", params={"token": one_time_token(U2), "next": bad})
    assert r.status_code == 200
    assert next_path(r) == "/dashboard"
    assert "Sunday" not in r.text and "evil" not in r.text


def test_one_time_link_without_next_is_unchanged(app):
    r = app.get("/api/auth/one-time", params={"token": one_time_token(U2)})
    assert r.status_code == 200
    tokens = stored_tokens(r)
    assert jwt.get_unverified_claims(tokens["access_token"])["sub"] == U2
    # Byte for byte the page it has always been.
    assert r.content == auth_pages.signed_in(tokens, "/dashboard").body


# --- unlink and disconnect ------------------------------------------------------------------


def test_unlink_needs_the_secret_and_the_token(app):
    uid = connect(app).json()["revise_user_id"]
    r = app.post("/api/sunday/unlink", json={"token": TOKEN}, headers={"X-Revise-Secret": "wrong"})
    assert r.status_code == 401 and link_row(uid)
    r = app.post("/api/sunday/unlink", json={"token": "not-the-token"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["removed"] is False and link_row(uid)
    r = app.post("/api/sunday/unlink", json={"token": TOKEN}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["removed"] is True and not link_row(uid)
    assert app.get("/api/me", headers=bearer(uid)).json()["sunday"] is None


def test_disconnect_from_settings_tells_sunday(app, monkeypatch):
    calls = []
    monkeypatch.setattr(sunday.httpx, "delete", lambda url, **kw: calls.append((url, kw["headers"])))
    uid = connect(app).json()["revise_user_id"]
    r = app.post("/api/sunday/disconnect", headers=bearer(uid))
    assert r.status_code == 200 and not link_row(uid)
    assert calls == [(f"{SUNDAY}/api/revise/link", {"Authorization": f"Bearer {TOKEN}"})]
    # Not connected: nothing to tell.
    assert app.post("/api/sunday/disconnect", headers=bearer(uid)).status_code == 200
    assert len(calls) == 1


def test_disconnect_works_when_sunday_is_down(app, monkeypatch):
    def down(url, **kw):
        raise sunday.httpx.ConnectError("down")

    monkeypatch.setattr(sunday.httpx, "delete", down)
    uid = connect(app).json()["revise_user_id"]
    assert app.post("/api/sunday/disconnect", headers=bearer(uid)).status_code == 200
    assert not link_row(uid)


def test_me_says_null_when_not_connected(app):
    assert app.get("/api/me", headers=bearer(U1, "one@example.com")).json()["sunday"] is None
