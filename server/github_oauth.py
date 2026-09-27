"""Sign in with GitHub: the OAuth web flow's calls to GitHub.

Only the numeric account id is used as identity (usernames can change), and
only verified email addresses are trusted for matching existing accounts.
The GitHub access token is used for two reads and then thrown away.
"""

import os
from urllib.parse import urlencode

import httpx

CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
SCOPES = "read:user user:email"
AUTH_BASE = "https://github.com"
API_BASE = "https://api.github.com"


class GitHubError(Exception):
    pass


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def authorize_url(redirect_uri: str, state: str) -> str:
    query = urlencode(
        {"client_id": CLIENT_ID, "redirect_uri": redirect_uri, "scope": SCOPES,
         "state": state, "allow_signup": "true"}
    )
    return f"{AUTH_BASE}/login/oauth/authorize?{query}"


def exchange_code(code: str, redirect_uri: str) -> str:
    resp = httpx.post(
        f"{AUTH_BASE}/login/oauth/access_token",
        data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "code": code, "redirect_uri": redirect_uri},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    body = resp.json() if resp.status_code == 200 else {}
    if not body.get("access_token"):
        raise GitHubError(body.get("error_description") or f"token exchange failed ({resp.status_code})")
    return body["access_token"]


def _get(path: str, token: str):
    resp = httpx.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise GitHubError(f"GET {path}: {resp.status_code}")
    return resp.json()


def fetch_account(token: str) -> dict:
    """{id, login, name, avatar_url, emails: [verified addresses, primary first]}"""
    user = _get("/user", token)
    emails = [e for e in _get("/user/emails", token) if e.get("verified")]
    emails.sort(key=lambda e: not e.get("primary"))
    return {
        "id": str(user["id"]),
        "login": user.get("login"),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
        "emails": [e["email"] for e in emails],
    }
