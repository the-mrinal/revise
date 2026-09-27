"""Phase 2 sign-in: Revise sessions, the Supabase bridge, GitHub, merging."""

import json
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from jose import jwt

import auth
import cutover
import database
import github_oauth
import main as main_module

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
GH = {"id": "9001", "login": "octo", "name": "Octo", "avatar_url": "https://avatars.test/9001",
      "emails": ["one@example.com", "octo@users.noreply.github.com"]}


@pytest.fixture
def app(local_db):
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    return TestClient(main_module.app)


@pytest.fixture
def github(monkeypatch):
    """GitHub configured, with its two calls faked; returns the fake account."""
    account = dict(GH)
    monkeypatch.setattr(github_oauth, "CLIENT_ID", "cid")
    monkeypatch.setattr(github_oauth, "CLIENT_SECRET", "csecret")
    monkeypatch.setattr(github_oauth, "exchange_code", lambda code, uri: f"gh-token-{code}")
    monkeypatch.setattr(github_oauth, "fetch_account", lambda token: dict(account))
    return account


def stored_tokens(resp) -> dict:
    """The tokens a signed-in page writes to localStorage('auth')."""
    m = re.search(r"setItem\('auth', (\".*?\")\);", resp.text)
    assert m, resp.text
    return json.loads(json.loads(m.group(1)))


def next_path(resp) -> str:
    return json.loads(re.search(r"location\.replace\((\".*?\")\)", resp.text).group(1))


def supabase_token(sub, email, exp_in=3600):
    """An access token shaped like Supabase's legacy HS256 ones."""
    return jwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + exp_in,
         "iss": "https://project.supabase.co/auth/v1"},
        auth.SUPABASE_JWT_SECRET, algorithm="HS256",
    )


def github_sign_in(client, next_="/dashboard", link=None):
    params = {"next": next_}
    if link:
        params["link"] = link
    r = client.get("/api/auth/github/login", params=params, follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get("/api/auth/github/callback", params={"code": "c1", "state": state})


# --- tokens and sessions -----------------------------------------------------------


def test_revise_tokens_work_like_the_old_ones(app):
    tokens = auth.start_session(U1, "one@example.com")
    assert tokens["refresh_token"].startswith("rv_")
    claims = jwt.get_unverified_claims(tokens["access_token"])
    assert (claims["sub"], claims["email"], claims["aud"]) == (U1, "one@example.com", "authenticated")
    r = app.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200 and r.json()["user_id"] == U1


def test_supabase_access_tokens_still_accepted(app):
    r = app.get("/api/me", headers={"Authorization": f"Bearer {supabase_token(U1, 'one@example.com')}"})
    assert r.status_code == 200


def test_purpose_tokens_are_not_access_tokens(app):
    link = auth.sign_purpose_token({"sub": U1}, "github-link", 300)
    assert app.get("/api/me", headers={"Authorization": f"Bearer {link}"}).status_code == 401


def test_refresh_keeps_the_refresh_token_and_slides_expiry(app):
    tokens = auth.start_session(U1, "one@example.com")
    r = app.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["refresh_token"] == tokens["refresh_token"]  # shared by dashboard + extension
    assert jwt.get_unverified_claims(r.json()["access_token"])["sub"] == U1


def test_logout_ends_the_session(app):
    tokens = auth.start_session(U1, "one@example.com")
    assert app.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 200
    r = app.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_expired_and_unknown_sessions_are_rejected(app):
    tokens = auth.start_session(U1, "one@example.com")
    database.db.execute("UPDATE sessions SET expires_at = now() - interval '1 day'")
    assert app.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert app.post("/api/auth/refresh", json={"refresh_token": "rv_nope"}).status_code == 401


# --- the bridge from Supabase sessions -----------------------------------------------


def test_supabase_refresh_token_becomes_a_revise_session(app, monkeypatch):
    calls = []

    def fake_supabase_refresh(token):
        calls.append(token)
        return {"access_token": supabase_token(U1, "one@example.com"), "refresh_token": "new-supa"}

    monkeypatch.setattr(auth, "refresh_session", fake_supabase_refresh)
    first = app.post("/api/auth/refresh", json={"refresh_token": "old-supa-token"})
    assert first.status_code == 200 and first.json()["refresh_token"].startswith("rv_")

    # The extension holds the same Supabase token: it gets its own Revise
    # session without asking Supabase again (which would trip reuse detection).
    second = app.post("/api/auth/refresh", json={"refresh_token": "old-supa-token"})
    assert second.status_code == 200 and second.json()["refresh_token"].startswith("rv_")
    assert second.json()["refresh_token"] != first.json()["refresh_token"]
    assert calls == ["old-supa-token"]
    for tokens in (first.json(), second.json()):
        me = app.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.json()["user_id"] == U1


def test_dead_supabase_refresh_token_is_401(app, monkeypatch):
    def fail(token):
        raise RuntimeError("Invalid Refresh Token: Already Used")

    monkeypatch.setattr(auth, "refresh_session", fail)
    assert app.post("/api/auth/refresh", json={"refresh_token": "dead"}).status_code == 401


def test_magic_link_callback_hands_out_a_revise_session(app, monkeypatch):
    monkeypatch.setattr(main_module, "exchange_code_for_session", lambda h, t: {
        "access_token": supabase_token(U1, "one@example.com"), "refresh_token": "supa-rt"})
    r = app.get("/api/auth/callback", params={"token_hash": "h", "type": "magiclink"})
    tokens = stored_tokens(r)
    assert tokens["refresh_token"].startswith("rv_") and next_path(r) == "/dashboard"


def test_magic_link_only_for_existing_accounts_once_github_is_on(app, github, monkeypatch):
    seen = {}
    monkeypatch.setattr(main_module, "send_magic_link",
                        lambda email, allow_new_accounts: seen.update(allow=allow_new_accounts))
    assert app.post("/api/auth/magic-link", json={"email": "x@example.com"}).status_code == 200
    assert seen["allow"] is False
    assert app.get("/api/auth/config").json() == {"github": True, "new_github_accounts": True}


# --- GitHub sign-in ---------------------------------------------------------------------


def test_github_hidden_until_configured(app):
    assert app.get("/api/auth/config").json()["github"] is False
    assert app.get("/api/auth/github/login", follow_redirects=False).status_code == 503


def test_first_github_sign_in_connects_the_account_with_that_email(app, github):
    r = github_sign_in(app)
    tokens = stored_tokens(r)
    assert jwt.get_unverified_claims(tokens["access_token"])["sub"] == U1
    assert next_path(r) == "/dashboard?github=connected"
    me = app.get("/api/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert me["github"] == {"available": True, "login": "octo"}
    assert database.get_profile(U1)["avatar_url"] == "https://avatars.test/9001"

    again = github_sign_in(app)  # returning user
    assert jwt.get_unverified_claims(stored_tokens(again)["access_token"])["sub"] == U1
    assert next_path(again) == "/dashboard?github=signed-in"


def test_unverified_or_unknown_emails_never_match(app, github):
    github["emails"] = ["someone.else@example.com"]
    r = github_sign_in(app)
    assert "New to Revise?" in r.text and "setItem('auth'" not in r.text


def test_new_account_after_asking(app, github):
    github["emails"] = ["new@example.com"]
    r = github_sign_in(app, next_="/prep")
    pending = re.search(r"name=pending value='([^']+)'", r.text).group(1)
    created = app.post("/api/auth/github/complete", data={"pending": pending})
    tokens = stored_tokens(created)
    new_id = jwt.get_unverified_claims(tokens["access_token"])["sub"]
    assert new_id not in (U1, U2) and next_path(created) == "/prep?github=new"
    assert database.get_user(new_id)["email"] == "new@example.com"
    # Posting the same page twice doesn't make a second account.
    twice = app.post("/api/auth/github/complete", data={"pending": pending})
    assert jwt.get_unverified_claims(stored_tokens(twice)["access_token"])["sub"] == new_id


def test_no_new_accounts_while_data_is_on_supabase(app, github, monkeypatch):
    monkeypatch.setattr(cutover, "current_target", lambda: "supabase")
    github["emails"] = ["new@example.com"]
    r = github_sign_in(app)
    assert "disabled" in r.text and "New accounts open in a few days" in r.text
    pending = re.search(r"name=pending value='([^']+)'", r.text).group(1)
    assert app.post("/api/auth/github/complete", data={"pending": pending}).status_code == 503
    assert app.get("/api/auth/config").json()["new_github_accounts"] is False


def test_connect_github_from_a_signed_in_account(app, github):
    github["emails"] = ["different@example.com"]
    tokens = auth.start_session(U2, "two@example.com")
    url = app.post("/api/auth/github/link",
                   headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()["url"]
    link = parse_qs(urlparse(url).query)["link"][0]
    r = github_sign_in(app, link=link)
    assert jwt.get_unverified_claims(stored_tokens(r)["access_token"])["sub"] == U2
    assert next_path(r) == "/dashboard?github=linked"
    assert database.get_identity_user("github", "9001") == U2


def test_a_github_account_links_to_one_revise_account_only(app, github):
    github_sign_in(app)  # links 9001 to U1 by email
    link = auth.sign_purpose_token({"sub": U2}, "github-link", 300)
    r = github_sign_in(app, link=link)
    assert r.status_code == 400 and "already in use" in r.text
    assert database.get_identity_user("github", "9001") == U1


def test_callback_rejects_forged_or_stale_state(app, github):
    app.get("/api/auth/github/login", follow_redirects=False)
    r = app.get("/api/auth/github/callback", params={"code": "c1", "state": "forged"})
    assert r.status_code == 400 and "setItem('auth'" not in r.text
    fresh = TestClient(main_module.app)  # no state cookie at all
    assert fresh.get("/api/auth/github/callback", params={"code": "c", "state": "s"}).status_code == 400


def test_next_can_only_point_inside_revise(app, github):
    r = github_sign_in(app, next_="https://evil.example/")
    assert next_path(r).startswith("/dashboard?")
    r = github_sign_in(app, next_="//evil.example/")
    assert next_path(r).startswith("/dashboard?")


# --- merging duplicate accounts -------------------------------------------------------


def test_merge_users_moves_everything_and_deletes_the_duplicate(app):
    q = database.insert_question(U2, {"url": "https://x.test/q"})
    database.insert_event(U2, q["id"], "created")
    database.insert_user_platform(U2, {"name": "Brilliant", "url_pattern": "brilliant.org"})
    database.upsert_user_settings(U1, {"revision_queue_size": 9})
    database.upsert_user_settings(U2, {"revision_queue_size": 3})
    database.link_identity(U2, "github", "77", "dup", None)
    session = auth.start_session(U2, "two@example.com")

    moved = database.merge_users(U2, U1)
    assert moved["questions"] == 1 and moved["user_identities"] == 1
    assert [r["url"] for r in database.get_all_questions(U1)] == ["https://x.test/q"]
    assert database.get_user_settings(U1)["revision_queue_size"] == 9  # the kept account's own wins
    assert database.get_identity_user("github", "77") == U1
    assert database.get_user(U2) is None
    refreshed = auth.refresh(session["refresh_token"])  # their session carries over
    assert jwt.get_unverified_claims(refreshed["access_token"])["sub"] == U1


def test_admin_merge_route(app):
    database.set_user_admin(U1, True)
    tokens = auth.start_session(U1, "one@example.com")
    database.ensure_user("33333333-3333-3333-3333-333333333333", "three@example.com")
    r = app.post("/api/admin/merge-users", json={"from_email": "three@example.com", "into_email": "two@example.com"},
                 headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200 and r.json()["into"]["email"] == "two@example.com"
    assert database.find_user_by_email("three@example.com") is None
    assert database.get_recent_audit()[0]["action"] == "merge"
