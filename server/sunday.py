"""Connecting a Revise account to Sunday (sunday.thelabs.wtf).

Sunday's server calls the two /api/sunday routes with the shared secret
REVISE_SUNDAY_SECRET: connect (find or make the account, keep Sunday's token,
answer a 5-minute one-time sign-in link that ends back on Sunday) and unlink.
That secret lets Sunday sign anyone in, so these two routes are the only
place it is accepted. Without it, or without SUNDAY_URL, every /api/sunday
route answers 404 and Revise behaves as if Sunday didn't exist.

The signed-in person's own Disconnect (Settings) removes the link here, then
tells Sunday with the token it gave us.
"""

import hmac
import os
import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

import cutover
import db
from auth import SERVER_URL, get_current_user_id, sign_purpose_token
from database import (
    create_user,
    find_user_by_email,
    get_identity_user,
    get_user_identity,
    link_identity,
    refresh_identity,
)

SIGN_IN_LINK_SECONDS = 300


def sunday_url() -> str:
    return os.environ.get("SUNDAY_URL", "").strip().rstrip("/")


def _secret() -> str:
    return os.environ.get("REVISE_SUNDAY_SECRET", "").strip()


def enabled() -> bool:
    return bool(_secret() and sunday_url())


def valid_next(next_url: str | None) -> bool:
    """A sign-in may end on Sunday, and nowhere else off Revise."""
    base = sunday_url()
    if not base or not next_url:
        return False
    return next_url == base or next_url.startswith(base + "/")


# --- The link itself ---


def get_link(user_id: str) -> dict | None:
    """{login, connected_at} while this account is connected, else None."""
    if not enabled():
        return None
    row = db.fetch_one(
        "SELECT sunday_login, connected_at FROM sunday_links WHERE user_id = %s", (user_id,)
    )
    return {"login": row["sunday_login"], "connected_at": row["connected_at"]} if row else None


def save_link(user_id: str, token: str, login: str) -> None:
    """Connecting again replaces the token Sunday gave last time."""
    db.execute(
        "INSERT INTO sunday_links (user_id, sunday_token, sunday_login) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET sunday_token = EXCLUDED.sunday_token, "
        "sunday_login = EXCLUDED.sunday_login, connected_at = now()",
        (user_id, token, login),
    )


def remove_link_by_token(token: str) -> bool:
    return db.execute("DELETE FROM sunday_links WHERE sunday_token = %s", (token,)) == 1


def remove_link_for_user(user_id: str) -> str | None:
    """Remove the link; returns the token it held, if there was one."""
    row = db.fetch_one(
        "DELETE FROM sunday_links WHERE user_id = %s RETURNING sunday_token", (user_id,)
    )
    return row["sunday_token"] if row else None


# --- Routes ---

router = APIRouter()


def require_enabled() -> None:
    if not enabled():
        raise HTTPException(404, "Not Found")


def require_secret(x_revise_secret: str = Header("")) -> None:
    require_enabled()
    if not hmac.compare_digest(x_revise_secret.encode(), _secret().encode()):
        raise HTTPException(401, "Wrong secret")


class ConnectIn(BaseModel):
    github_id: int | str
    login: str = Field(min_length=1)
    email: str = Field(min_length=3)
    token: str = Field(min_length=16)


class UnlinkIn(BaseModel):
    token: str = Field(min_length=1)


def _find_or_make_user(github_id: str, login: str, email: str) -> str:
    """The account for this GitHub id, else the one with this email, else a
    new one; the GitHub id ends up linked, as a first GitHub sign-in does."""
    user_id = get_identity_user("github", github_id)
    if user_id:
        refresh_identity("github", github_id, login, email)
        return user_id
    found = find_user_by_email(email)
    if found:
        user_id = found["user_id"]
        # An account already linked to another GitHub account keeps that one.
        if not get_user_identity(user_id, "github"):
            link_identity(user_id, "github", github_id, login, email)
        return user_id
    if cutover.current_target() != "local":
        raise HTTPException(503, "New Revise accounts can't be made yet")
    user_id = create_user(email, "github")
    link_identity(user_id, "github", github_id, login, email)
    return user_id


@router.post("/api/sunday/connect", dependencies=[Depends(require_secret)])
def connect(body: ConnectIn):
    user_id = _find_or_make_user(str(body.github_id), body.login, body.email.strip())
    save_link(user_id, body.token, body.login)
    token = sign_purpose_token(
        {"sub": user_id, "jti": secrets.token_urlsafe(16)}, "one-time-sign-in", SIGN_IN_LINK_SECONDS
    )
    next_url = quote(sunday_url() + "/revise", safe="")
    return {"revise_user_id": user_id,
            "signin_url": f"{SERVER_URL}/api/auth/one-time?token={token}&next={next_url}"}


@router.post("/api/sunday/unlink", dependencies=[Depends(require_secret)])
def unlink(body: UnlinkIn):
    return {"ok": True, "removed": remove_link_by_token(body.token)}


@router.post("/api/sunday/disconnect", dependencies=[Depends(require_enabled)])
def disconnect(user_id: str = Depends(get_current_user_id)):
    """Disconnect from Revise's Settings. Sunday is told, but if it can't be
    reached the link is gone here anyway; its next call gets a 401."""
    token = remove_link_for_user(user_id)
    if token:
        try:
            httpx.delete(f"{sunday_url()}/api/revise/link",
                         headers={"Authorization": f"Bearer {token}"}, timeout=5)
        except httpx.HTTPError as e:
            print(f"[sunday] could not tell Sunday about a disconnect: {e}")
    return {"ok": True}
