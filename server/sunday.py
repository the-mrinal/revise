"""Connecting a Revise account to Sunday (sunday.thelabs.wtf).

Sunday's server calls the two /api/sunday routes with the shared secret
REVISE_SUNDAY_SECRET: connect (find or make the account, keep Sunday's token,
answer a 5-minute one-time sign-in link that ends back on Sunday) and unlink.
That secret lets Sunday sign anyone in, so these two routes are the only
place it is accepted. Without it, or without SUNDAY_URL, every /api/sunday
route answers 404 and Revise behaves as if Sunday didn't exist.

The signed-in person's own Disconnect (Settings) removes the link here, then
tells Sunday with the token it gave us.

For a connected account, each solve or review (and each edit of its notes)
is put in sunday_outbox, and a background thread sends it to Sunday's
POST /api/revise/saves, retrying with a growing wait while Sunday is down.
The save in Revise never waits for Sunday. A 401 from Sunday means Sunday
has already disconnected, so the link is removed here too.
"""

import hmac
import os
import secrets
import threading
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

import cutover
import db
from auth import SERVER_URL, get_current_user_id, sign_purpose_token
from database import (
    create_user,
    find_user_by_email,
    get_identity_user,
    get_question,
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


# --- Sending saves ---

SEND_TIMEOUT_SECONDS = 10
POLL_SECONDS = 15
FIRST_WAIT_SECONDS = 30
LONGEST_WAIT_SECONDS = 3600
MAX_TRIES = 30  # about a day of retries once the wait reaches an hour

_stop = threading.Event()
_wake = threading.Event()


def _save_body(question: dict, event: dict, edited: bool) -> dict:
    """Sunday's save, from Revise's question row and one of its events.

    The rating, how it was solved and the due date are the event's. The time
    is the event's too; a review has none, and the extension's Save sends it
    straight after in an edit, so an edit takes the question's. The three
    notes are the question's (Revise keeps one set per question).
    solution_source is already Sunday's self/hint/solution.
    """
    minutes = event.get("time_taken")
    if minutes is None and edited:
        minutes = question.get("time_taken")
    return {
        "ref": f"{question['id']}:{event['id']}",
        "url": question["url"],
        "title": question.get("title"),
        "platform": question.get("platform"),
        "difficulty": question.get("difficulty"),
        "pattern": question.get("pattern"),
        "minutes": minutes,
        "how": event.get("solution_source") or "self",
        "stars": event["self_rating"],
        "idea": question.get("approach"),
        "missed": question.get("mistakes"),
        "lesson": question.get("notes"),
        "due_on": event.get("next_review") or question.get("next_review"),
        "at": event["created_at"],
        "own_pick": False,
    }


def queue_save(user_id: str, question_id: int, edited: bool = False) -> None:
    """Queue this question's newest rated event for Sunday, if the account
    is connected. Called after a solve, a review, or an edit (which sends the
    same ref again). Events from before connecting are never sent. Never
    raises: a problem here must not fail the save it follows."""
    if not enabled():
        return
    try:
        event = db.fetch_one(
            "SELECT e.id, e.self_rating, e.time_taken, e.solution_source, e.next_review, "
            "e.created_at FROM question_events e "
            "JOIN sunday_links l ON l.user_id = e.user_id "
            "WHERE e.user_id = %s AND e.question_id = %s AND e.self_rating IS NOT NULL "
            "AND e.created_at >= l.connected_at ORDER BY e.id DESC LIMIT 1",
            (user_id, question_id),
        )
        if not event:
            return
        question = get_question(user_id, question_id)
        if not question:
            return
        body = _save_body(question, event, edited)
        with db.get_pool().connection() as conn, conn.transaction():
            # A newer body for the same ref replaces one not sent yet.
            conn.execute("DELETE FROM sunday_outbox WHERE user_id = %s AND ref = %s",
                         (user_id, body["ref"]))
            conn.execute("INSERT INTO sunday_outbox (user_id, ref, body) VALUES (%s, %s, %s)",
                         (user_id, body["ref"], Jsonb(body)))
        _wake.set()
    except Exception as e:
        print(f"[sunday] could not queue a save for q{question_id}: {e}")


def _try_later(row: dict, error: str) -> None:
    tries = row["tries"] + 1
    if tries >= MAX_TRIES:
        print(f"[sunday] giving up on {row['ref']} after {tries} tries: {error}")
        db.execute("DELETE FROM sunday_outbox WHERE id = %s", (row["id"],))
        return
    wait = min(FIRST_WAIT_SECONDS * 2 ** (tries - 1), LONGEST_WAIT_SECONDS)
    db.execute(
        "UPDATE sunday_outbox SET tries = %s, last_error = %s, "
        "next_try_at = now() + make_interval(secs => %s) WHERE id = %s",
        (tries, error[:500], wait, row["id"]),
    )


def send_pending(limit: int = 50) -> int:
    """Send waiting saves, oldest first; returns how many Sunday took.

    Stops at the first save that is still waiting to be retried, or that
    fails now, so saves reach Sunday in the order they were made."""
    if not enabled():
        return 0
    rows = db.fetch_all(
        "SELECT o.id, o.user_id, o.ref, o.body, o.tries, o.next_try_at <= now() AS due, "
        "l.sunday_token FROM sunday_outbox o LEFT JOIN sunday_links l USING (user_id) "
        "ORDER BY o.id LIMIT %s",
        (limit,),
    )
    sent, unlinked = 0, set()
    for row in rows:
        token = row["sunday_token"]
        if not token or row["user_id"] in unlinked:  # disconnected since it was queued
            db.execute("DELETE FROM sunday_outbox WHERE id = %s", (row["id"],))
            continue
        if not row["due"]:
            break
        try:
            r = httpx.post(f"{sunday_url()}/api/revise/saves", json=row["body"],
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=SEND_TIMEOUT_SECONDS)
        except httpx.HTTPError as e:
            _try_later(row, f"{type(e).__name__}: {e}")
            break
        if r.status_code == 401:  # Sunday has disconnected this person
            remove_link_by_token(token)
            db.execute("DELETE FROM sunday_outbox WHERE user_id = %s", (row["user_id"],))
            unlinked.add(row["user_id"])
        elif r.is_success:
            db.execute("DELETE FROM sunday_outbox WHERE id = %s", (row["id"],))
            sent += 1
        elif r.status_code >= 500 or r.status_code in (408, 429):
            _try_later(row, f"HTTP {r.status_code}")
            break
        else:  # Sunday refused this save; sending it again won't change that
            print(f"[sunday] Sunday refused {row['ref']}: HTTP {r.status_code} {r.text[:200]}")
            db.execute("DELETE FROM sunday_outbox WHERE id = %s", (row["id"],))
    return sent


def start_sender() -> threading.Thread | None:
    """The background sender, started with the server when Sunday is set up."""
    if not enabled():
        return None
    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                send_pending()
            except Exception as e:  # keep sending; never take the server down
                print(f"[sunday] sender error: {e}")
            _wake.wait(POLL_SECONDS)
            _wake.clear()

    t = threading.Thread(target=loop, name="sunday-sender", daemon=True)
    t.start()
    return t


def stop_sender() -> None:
    _stop.set()
    _wake.set()


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


@router.get("/api/sunday/where", dependencies=[Depends(require_enabled)])
def where(url: str = Query(min_length=1), user_id: str = Depends(get_current_user_id)):
    """For the extension: is this problem in the person's Sunday week?
    Sunday's own answer, or {linked: false} for an account not connected."""
    row = db.fetch_one("SELECT sunday_token FROM sunday_links WHERE user_id = %s", (user_id,))
    if not row:
        return {"linked": False}
    token = row["sunday_token"]
    try:
        r = httpx.get(f"{sunday_url()}/api/revise/where", params={"url": url},
                      headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Sunday can't be reached: {type(e).__name__}")
    if r.status_code == 401:  # Sunday has disconnected this person
        remove_link_by_token(token)
        return {"linked": False}
    if not r.is_success:
        raise HTTPException(502, f"Sunday answered {r.status_code}")
    return r.json()
