"""FastAPI server for Revise."""

import html
import secrets
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal, Optional
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from supabase_auth.errors import AuthApiError

from auth import (
    SERVER_URL,
    SessionExpired,
    end_session,
    exchange_code_for_session,
    get_current_claims,
    get_current_user_id,
    read_purpose_token,
    send_magic_link,
    sign_purpose_token,
    start_session,
    verify_token,
)
from auth import refresh as auth_refresh_tokens
from database import (
    AVATAR_DIR,
    count_revisions_done_today,
    decrement_attempts,
    delete_latest_attempt_event,
    delete_question,
    delete_user_platform,
    ensure_user_profile,
    find_by_url,
    find_user_by_email,
    get_activity_heatmap,
    get_all_questions,
    get_flex_stats,
    get_profile,
    get_recent_audit,
    get_question,
    get_question_events,
    get_questions_activity_summary,
    get_revisions_due,
    get_stats,
    get_today_activity,
    get_user_activity,
    get_user_features,
    get_user_platforms,
    get_user_settings,
    grant_feature,
    increment_attempts,
    insert_event,
    insert_question,
    insert_user_platform,
    is_user_admin,
    list_all_users,
    log_access_event,
    merge_duplicates,
    merge_duplicates_for_question,
    create_user,
    ensure_user,
    get_identity_user,
    get_user,
    get_user_identity,
    link_identity,
    merge_users,
    record_sign_in,
    refresh_identity,
    revoke_feature,
    set_avatar_if_missing,
    users_matching_emails_without,
    set_user_admin,
    update_profile,
    update_question,
    update_question_schedule,
    upload_avatar,
    upsert_user_settings,
)
from patterns import (
    PATTERNS,
    PROBLEM_SLUGS,
    extract_leetcode_number,
    get_all_pattern_labels,
    get_pattern_for_url,
)
import auth_pages
import cutover
import db
import github_oauth
import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher = cutover.start_watcher()
    yield
    cutover.stop_watcher()
    if watcher:
        watcher.join()
    db.close()


app = FastAPI(title="Revise", lifespan=lifespan)

# Holds API requests (instead of failing them) while the database is being
# switched; see cutover.py.
app.middleware("http")(cutover.gate)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Named features that can be granted per-user via the admin panel (/admin).
# Add a feature here, then gate its page/endpoint on get_user_features().
FEATURES = ["research"]


def require_admin(claims: dict = Depends(get_current_claims)) -> dict:
    """Dependency allowing only admin users. Returns the JWT claims."""
    ensure_user_profile(claims["sub"], claims.get("email"))
    if not is_user_admin(claims["sub"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return claims


def require_feature(feature: str):
    """Dependency factory: allow only users granted `feature` (admins bypass).

    Use to gate a protected endpoint, e.g.:
        @app.get("/api/some-thing")
        def some_thing(user_id: str = Depends(require_feature("research"))):
            ...
    """

    def dependency(user_id: str = Depends(get_current_user_id)) -> str:
        if is_user_admin(user_id) or feature in get_user_features(user_id):
            return user_id
        raise HTTPException(status_code=403, detail=f"Requires '{feature}' access")

    return dependency


PLATFORM_PATTERNS = {
    "leetcode": r"leetcode\.com",
    "codechef": r"codechef\.com",
    "hackerrank": r"hackerrank\.com",
    "codeforces": r"codeforces\.com",
    "geeksforgeeks": r"geeksforgeeks\.org",
    "interviewbit": r"interviewbit\.com",
    "atcoder": r"atcoder\.jp",
    "neetcode": r"neetcode\.io",
    "algomonster": r"algo\.monster",
    "designgurus": r"designgurus\.io",
}


def normalize_url(url: str) -> str:
    """Strip query params, fragments, and trailing sub-paths like /description/."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # LeetCode: keep only /problems/<slug>
    m = re.match(r"(/problems/[^/]+)", path)
    if m and "leetcode.com" in parsed.netloc:
        path = m.group(1)
    return urlunparse((parsed.scheme, parsed.netloc, path + "/", "", "", ""))


def detect_platform(url: str, user_platforms: list[dict] | None = None) -> str:
    if user_platforms:
        for p in user_platforms:
            if re.search(p["url_pattern"], url, re.IGNORECASE):
                return p["name"]
    for platform, pattern in PLATFORM_PATTERNS.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "other"


# --- Request models ---

VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def normalize_difficulty(v: Optional[str]) -> Optional[str]:
    """Difficulty is stored lowercase-only; imported data used to carry
    capitalized values ('Easy') which split the stats into duplicate buckets."""
    if v is None:
        return None
    v = v.strip().lower()
    if not v:
        return None
    if v not in VALID_DIFFICULTIES:
        raise ValueError("difficulty must be one of: easy, medium, hard")
    return v


class QuestionIn(BaseModel):
    url: str
    title: Optional[str] = None
    difficulty: Optional[str] = None
    self_rating: Optional[int] = Field(default=None, ge=1, le=5)
    time_taken: Optional[int] = None
    notes: Optional[str] = None
    question_type: Optional[str] = "dsa"
    # Defaults to "self" so extension versions that predate the field keep working.
    solution_source: Literal["self", "hint", "solution"] = "self"

    _normalize_difficulty = field_validator("difficulty", mode="before")(
        classmethod(lambda cls, v: normalize_difficulty(v))
    )


class QuestionUpdate(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    difficulty: Optional[str] = None
    self_rating: Optional[int] = Field(default=None, ge=1, le=5)
    time_taken: Optional[int] = None
    notes: Optional[str] = None
    pattern: Optional[str] = None
    question_type: Optional[str] = None
    approach: Optional[str] = None
    mistakes: Optional[str] = None
    time_complexity: Optional[str] = None
    space_complexity: Optional[str] = None
    solution_source: Optional[Literal["self", "hint", "solution"]] = None

    _normalize_difficulty = field_validator("difficulty", mode="before")(
        classmethod(lambda cls, v: normalize_difficulty(v))
    )


class ReviewIn(BaseModel):
    self_rating: int = Field(ge=1, le=5)
    solution_source: Literal["self", "hint", "solution"] = "self"


class MagicLinkRequest(BaseModel):
    email: str


class PlatformIn(BaseModel):
    name: str
    url_pattern: str


class SettingsUpdate(BaseModel):
    # 0 = unlimited. Capped to keep a single day's queue sane.
    revision_queue_size: Optional[int] = Field(default=None, ge=0, le=500)
    # FSRS target retention: how much you want to remember at review time.
    desired_retention: Optional[float] = Field(default=None, ge=0.7, le=0.99)


class RefreshRequest(BaseModel):
    refresh_token: str


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    platform_links: Optional[dict[str, str]] = None
    timezone: Optional[str] = None


class FeatureToggle(BaseModel):
    feature: str
    granted: bool


class AdminToggle(BaseModel):
    is_admin: bool


class GrantByEmail(BaseModel):
    email: str
    feature: str


class MergeUsers(BaseModel):
    from_email: str
    into_email: str


# --- Auth endpoints (no auth required) ---


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@app.get("/api/auth/config")
def auth_config():
    """What the sign-in screens should offer."""
    return {
        "github": github_oauth.configured(),
        # GitHub-only accounts need our own database: on Supabase, every
        # question must belong to a Supabase Auth user.
        "new_github_accounts": github_oauth.configured() and cutover.current_target() == "local",
    }


@app.post("/api/auth/magic-link")
def auth_magic_link(req: MagicLinkRequest):
    try:
        send_magic_link(req.email, allow_new_accounts=not github_oauth.configured())
    except AuthApiError as e:
        if e.status == 429:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts — please wait a minute and try again.",
            )
        if "signups not allowed" in (e.message or "").lower():
            raise HTTPException(
                status_code=404,
                detail="No Revise account uses this email. New here? Sign in with GitHub.",
            )
        raise HTTPException(status_code=400, detail=e.message)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts — please wait a minute and try again.",
            )
        raise HTTPException(status_code=502, detail="Auth service error — please try again.")
    return {"message": "Magic link sent!"}


@app.get("/api/auth/callback")
def auth_callback(
    request: Request,
    token_hash: str = Query(None),
    type: str = Query(None),
):
    # Tokens must never ride the URL into /dashboard: the browser extension's
    # content script races the page for the hash fragment and can strip it
    # before the dashboard reads it (users with the old extension lost every
    # login this way). Instead this same-origin callback page writes the tokens
    # straight into localStorage("auth") — where the dashboard already looks —
    # and redirects to a clean URL with nothing left to steal.

    # PKCE flow: Supabase sends token_hash & type as query params
    if token_hash and type:
        try:
            tokens = exchange_code_for_session(token_hash, type)
        except AuthApiError:
            return HTMLResponse(
                "<p>This sign-in link has expired or was already used. "
                '<a href="/">Request a new one</a>.</p>',
                status_code=401,
            )
        try:
            claims = verify_token(tokens["access_token"])
            ensure_user(claims["sub"], claims.get("email"))
            record_sign_in(claims["sub"], claims.get("email"))
            # Hand out a Revise session rather than the Supabase one.
            tokens = start_session(claims["sub"], claims.get("email"), _user_agent(request))
        except Exception as e:  # the Supabase session still works (and bridges later)
            print(f"[auth] could not start a Revise session: {e}")
        return auth_pages.signed_in(tokens, "/dashboard")

    # Implicit flow: Supabase sends tokens in the URL fragment (#access_token=...)
    # Fragments aren't sent to the server, so an inline script moves them to
    # localStorage. It runs synchronously during parse, before the extension's
    # async hash-strip can land.
    return HTMLResponse(
        """<script>
(function () {
  var p = new URLSearchParams(location.hash.substring(1));
  var at = p.get("access_token"), rt = p.get("refresh_token");
  if (at && rt) {
    localStorage.setItem("auth", JSON.stringify({ access_token: at, refresh_token: rt }));
    location.replace("/dashboard");
  } else {
    location.replace("/dashboard" + location.hash);
  }
})();
</script>"""
    )


@app.post("/api/auth/refresh")
def auth_refresh(req: RefreshRequest, request: Request):
    try:
        return auth_refresh_tokens(req.refresh_token, _user_agent(request))
    except SessionExpired as e:
        # Unknown / revoked / expired. 401 tells clients the session is dead
        # (sign in again) — a 500 here reads as transient and sends them into
        # a retry loop.
        raise HTTPException(status_code=401, detail=f"Session expired: {e}")


@app.post("/api/auth/logout")
def auth_logout(req: RefreshRequest):
    end_session(req.refresh_token)
    return {"ok": True}


# --- Sign in with GitHub ---

GITHUB_STATE_COOKIE = "revise_github_state"
GITHUB_COOKIE_PATH = "/api/auth/github"


def _github_redirect_uri() -> str:
    return f"{SERVER_URL}/api/auth/github/callback"


@app.get("/api/auth/github/login")
def github_login(next: str = Query("/dashboard"), link: str = Query(None)):
    """Start signing in with GitHub, or (with a link token from
    /api/auth/github/link) connect GitHub to the signed-in account."""
    if not github_oauth.configured():
        return auth_pages.message("GitHub sign-in isn't set up yet", "Use the email link for now.", 503)
    link_sub = None
    if link:
        claims = read_purpose_token(link, "github-link")
        if not claims:
            return auth_pages.message("That link has expired", "Go back to Revise and click Connect GitHub again.")
        link_sub = claims["sub"]
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(github_oauth.authorize_url(_github_redirect_uri(), state), status_code=302)
    resp.set_cookie(
        GITHUB_STATE_COOKIE,
        sign_purpose_token({"state": state, "next": auth_pages.safe_next(next), "link_sub": link_sub},
                           "github-state", 600),
        max_age=600, httponly=True, secure=SERVER_URL.startswith("https://"),
        samesite="lax", path=GITHUB_COOKIE_PATH,
    )
    return resp


def _finish_github_sign_in(request: Request, user_id: str, account: dict, next_path: str, outcome: str):
    record_sign_in(user_id, None)
    if account.get("avatar_url"):
        set_avatar_if_missing(user_id, account["avatar_url"])
    email = (get_user(user_id) or {}).get("email")
    tokens = start_session(user_id, email, _user_agent(request))
    return auth_pages.signed_in(tokens, auth_pages.with_param(next_path, "github", outcome))


@app.get("/api/auth/github/callback")
def github_callback(request: Request, code: str = Query(None), state: str = Query(None),
                    error: str = Query(None)):
    cookie = read_purpose_token(request.cookies.get(GITHUB_STATE_COOKIE, ""), "github-state")
    if error:
        resp = auth_pages.message("GitHub sign-in was cancelled", "Nothing changed.", 400)
    elif not cookie or not code or not state or not secrets.compare_digest(state, cookie["state"]):
        resp = auth_pages.message("That sign-in attempt expired", "Please try signing in again.", 400)
    else:
        resp = _github_callback(request, code, cookie)
    resp.delete_cookie(GITHUB_STATE_COOKIE, path=GITHUB_COOKIE_PATH)
    return resp


def _github_callback(request: Request, code: str, cookie: dict):
    next_path, link_sub = cookie["next"], cookie.get("link_sub")
    try:
        account = github_oauth.fetch_account(github_oauth.exchange_code(code, _github_redirect_uri()))
    except (github_oauth.GitHubError, httpx.HTTPError) as e:
        print(f"[github] sign-in failed: {e}")
        return auth_pages.message("GitHub didn't respond as expected", "Please try again in a minute.", 502)
    primary_email = account["emails"][0] if account["emails"] else None
    linked_to = get_identity_user("github", account["id"])

    if link_sub:  # Connect GitHub, from a signed-in account
        if linked_to and linked_to != link_sub:
            return auth_pages.message(
                "That GitHub account is already in use",
                f"@{account['login']} is connected to a different Revise account.",
            )
        existing = get_user_identity(link_sub, "github")
        if not linked_to and existing:
            return auth_pages.message(
                "Already connected",
                f"This Revise account is connected to GitHub @{existing['login']} already.",
            )
        if not linked_to:
            link_identity(link_sub, "github", account["id"], account["login"], primary_email)
        return _finish_github_sign_in(request, link_sub, account, next_path, "linked")

    if linked_to:  # a returning GitHub user
        refresh_identity("github", account["id"], account["login"], primary_email)
        return _finish_github_sign_in(request, linked_to, account, next_path, "signed-in")

    # First GitHub sign-in: an existing account with one of their verified
    # emails is theirs; connect it.
    matches = users_matching_emails_without("github", account["emails"])
    if len(matches) == 1:
        link_identity(matches[0], "github", account["id"], account["login"], primary_email)
        return _finish_github_sign_in(request, matches[0], account, next_path, "connected")

    # Nobody matches: ask before creating a new, empty account.
    pending = sign_purpose_token(
        {"github": {k: account[k] for k in ("id", "login", "avatar_url")},
         "email": primary_email, "next": next_path},
        "github-pending", 900,
    )
    new_allowed = cutover.current_target() == "local"
    create = (
        f"<form method=post action='/api/auth/github/complete'>"
        f"<input type=hidden name=pending value='{html.escape(pending)}'>"
        f"<button class=primary {'' if new_allowed else 'disabled'}>Create a new Revise account</button></form>"
    )
    if not new_allowed:
        create += "<p class=note>New accounts open in a few days, after a short upgrade.</p>"
    login = html.escape(account["login"] or "")
    return auth_pages.page(
        "New to Revise?",
        f"<p>No Revise account matches GitHub @{login} or its verified emails.</p>"
        "<p>If you already use Revise with another email, sign in with that email's link first, "
        "then choose <b>Connect GitHub</b>. Your questions stay in one account.</p>"
        "<a class=secondary href='/dashboard?signin=email'>I already have an account</a>" + create,
    )


@app.post("/api/auth/github/complete")
def github_complete(request: Request, pending: str = Form(...)):
    """Create the account the 'New to Revise?' page offered."""
    claims = read_purpose_token(pending, "github-pending")
    if not claims:
        return auth_pages.message("That page expired", "Please sign in with GitHub again.")
    account = claims["github"]
    user_id = get_identity_user("github", account["id"])
    if not user_id:  # not created meanwhile (e.g. a double click)
        if cutover.current_target() != "local":
            return auth_pages.message("New accounts open soon", "Please try again in a few days.", 503)
        user_id = create_user(claims.get("email"), "github")
        link_identity(user_id, "github", account["id"], account["login"], claims.get("email"))
    return _finish_github_sign_in(request, user_id, account, auth_pages.safe_next(claims.get("next")), "new")


@app.post("/api/auth/github/link")
def github_link(claims: dict = Depends(get_current_claims)):
    """Where to send the browser to connect GitHub to this account."""
    if not github_oauth.configured():
        raise HTTPException(503, "GitHub sign-in isn't set up yet")
    token = sign_purpose_token({"sub": claims["sub"]}, "github-link", 300)
    return {"url": f"/api/auth/github/login?next=/dashboard&link={token}"}


# --- Identity & access ---


@app.get("/api/me")
def me(claims: dict = Depends(get_current_claims)):
    """Identity + access for the current user. Also upserts the profile so the
    admin panel can list users who have signed in at least once."""
    user_id = claims["sub"]
    email = claims.get("email")
    ensure_user_profile(user_id, email)
    github = get_user_identity(user_id, "github")
    return {
        "user_id": user_id,
        "email": email,
        "is_admin": is_user_admin(user_id),
        "features": get_user_features(user_id),
        "github": {
            "available": github_oauth.configured(),
            "login": github["login"] if github else None,
        },
    }


# --- Profile (display name, avatar, platform profile links) ---


@app.get("/api/profile")
def read_profile(claims: dict = Depends(get_current_claims)):
    ensure_user_profile(claims["sub"], claims.get("email"))
    return get_profile(claims["sub"])


@app.put("/api/profile")
def save_profile(p: ProfileUpdate, user_id: str = Depends(get_current_user_id)):
    updates = {}
    if p.display_name is not None:
        updates["display_name"] = p.display_name.strip()[:80] or None
    if p.platform_links is not None:
        links = {}
        for name, link in p.platform_links.items():
            name = (name or "").strip().lower()
            link = (link or "").strip()
            if not name or not link:
                continue  # empty link = remove it
            if not link.startswith(("http://", "https://")):
                link = "https://" + link
            links[name] = link[:500]
        updates["platform_links"] = links
    if p.timezone is not None:
        tz = p.timezone.strip()
        try:
            ZoneInfo(tz)
        except Exception:
            raise HTTPException(400, f"Unknown timezone: {tz}")
        updates["timezone"] = tz
    if not updates:
        raise HTTPException(400, "No fields to update")
    return update_profile(user_id, updates)


MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


@app.post("/api/profile/avatar")
async def save_avatar(file: UploadFile = File(...), user_id: str = Depends(get_current_user_id)):
    ext = AVATAR_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(400, "Avatar must be a PNG, JPEG, WebP, or GIF image")
    content = await file.read(MAX_AVATAR_BYTES + 1)
    if not content:
        raise HTTPException(400, "Empty file")
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "Avatar must be 5 MB or smaller")
    try:
        url = upload_avatar(user_id, content, file.content_type, ext)
    except Exception as e:
        print(f"[avatar] upload failed for {user_id}: {e}")
        raise HTTPException(502, "Avatar upload failed. Please try again.")
    update_profile(user_id, {"avatar_url": url})
    return {"avatar_url": url}


# --- Admin panel (admin role required) ---


@app.get("/api/admin/features")
def admin_list_features(_: dict = Depends(require_admin)):
    return FEATURES


@app.get("/api/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    return list_all_users()


@app.get("/api/admin/audit")
def admin_audit(_: dict = Depends(require_admin)):
    return get_recent_audit()


@app.get("/api/admin/users/{uid}/activity")
def admin_user_activity(uid: str, _: dict = Depends(require_admin)):
    return get_user_activity(uid)


@app.post("/api/admin/users/{uid}/features")
def admin_set_feature(uid: str, body: FeatureToggle, claims: dict = Depends(require_admin)):
    if body.feature not in FEATURES:
        raise HTTPException(400, f"Unknown feature: {body.feature}")
    if body.granted:
        grant_feature(uid, body.feature)
    else:
        revoke_feature(uid, body.feature)
    log_access_event(
        claims["sub"], claims.get("email"), uid,
        "grant" if body.granted else "revoke", feature=body.feature,
    )
    return {"ok": True}


@app.post("/api/admin/users/{uid}/admin")
def admin_set_admin(uid: str, body: AdminToggle, claims: dict = Depends(require_admin)):
    if uid == claims["sub"] and not body.is_admin:
        raise HTTPException(400, "You can't remove your own admin access")
    set_user_admin(uid, body.is_admin)
    log_access_event(
        claims["sub"], claims.get("email"), uid,
        "make_admin" if body.is_admin else "remove_admin",
    )
    return {"ok": True}


@app.post("/api/admin/grant-by-email")
def admin_grant_by_email(body: GrantByEmail, claims: dict = Depends(require_admin)):
    if body.feature not in FEATURES:
        raise HTTPException(400, f"Unknown feature: {body.feature}")
    user = find_user_by_email(body.email)
    if not user:
        raise HTTPException(404, "No user has signed in with that email yet")
    grant_feature(user["user_id"], body.feature)
    log_access_event(
        claims["sub"], claims.get("email"), user["user_id"],
        "grant", feature=body.feature, target_email=user["email"],
    )
    return {"ok": True, "user_id": user["user_id"], "email": user["email"]}


@app.post("/api/admin/merge-users")
def admin_merge_users(body: MergeUsers, claims: dict = Depends(require_admin)):
    """Fold a duplicate account (from) into the one to keep (into)."""
    source, target = find_user_by_email(body.from_email), find_user_by_email(body.into_email)
    if not source or not target:
        raise HTTPException(404, "Both emails must belong to existing accounts")
    if source["user_id"] == target["user_id"]:
        raise HTTPException(400, "Those are the same account")
    moved = merge_users(source["user_id"], target["user_id"])
    log_access_event(
        claims["sub"], claims.get("email"), target["user_id"], "merge",
        feature=f"from {source['email']}", target_email=target["email"],
    )
    return {"ok": True, "moved": moved, "into": target}


# --- Protected API endpoints ---


@app.get("/api/questions/lookup")
def lookup_question(url: str, user_id: str = Depends(get_current_user_id)):
    normalized = normalize_url(url)
    existing = find_by_url(user_id, normalized)
    if not existing:
        return None
    return existing


@app.post("/api/questions")
def create_question(q: QuestionIn, user_id: str = Depends(get_current_user_id)):
    url = normalize_url(q.url)
    existing = find_by_url(user_id, url)
    if existing:
        updated = increment_attempts(user_id, existing["id"], q.title)
        insert_event(
            user_id, existing["id"], "attempted",
            self_rating=q.self_rating, time_taken=q.time_taken,
            solution_source=q.solution_source,
        )
        # was_existing lets the extension's Cancel roll back the right way:
        # delete a fresh row, but only undo the attempt bump on an old one.
        return {**updated, "was_existing": True}

    user_plats = get_user_platforms(user_id)
    platform = detect_platform(url, user_plats)
    settings = get_user_settings(user_id)
    # No rating yet on a timer-start capture: schedule with a neutral grade
    # but store self_rating as NULL so stats only reflect real reviews.
    schedule = scheduler.initial_schedule(
        q.self_rating, q.solution_source,
        desired_retention=settings["desired_retention"], params=settings["fsrs_params"],
    )

    # Auto-detect DSA pattern for LeetCode problems
    pattern_label = get_pattern_for_url(url) if platform == "leetcode" else None

    data = {
        "url": url,
        "title": q.title,
        "platform": platform,
        "difficulty": q.difficulty,
        "self_rating": q.self_rating,
        "time_taken": q.time_taken,
        "notes": q.notes,
        "next_review": schedule["next_review"],
        "pattern": pattern_label,
        "question_type": q.question_type,
        "solution_source": q.solution_source,
    }
    question = insert_question(user_id, data)
    update_question_schedule(user_id, question["id"], schedule)
    updated = get_question(user_id, question["id"])
    insert_event(
        user_id, question["id"], "created",
        self_rating=q.self_rating, time_taken=q.time_taken,
        solution_source=q.solution_source,
        interval=schedule["interval"], next_review=schedule["next_review"],
        stability=schedule["stability"], fsrs_difficulty=schedule["fsrs_difficulty"],
        fsrs_state=schedule["fsrs_state"],
    )
    return {**updated, "was_existing": False}


@app.get("/api/questions")
def list_questions(user_id: str = Depends(get_current_user_id)):
    return get_all_questions(user_id)


@app.get("/api/revisions/today")
def revisions_today(user_id: str = Depends(get_current_user_id)):
    settings = get_user_settings(user_id)
    quota = settings.get("revision_queue_size")
    today = date.today().isoformat()
    # 0 / unset means no daily cap — surface every due revision.
    if not quota or quota <= 0:
        due = get_revisions_due(user_id, today, limit=None)
    else:
        # Fixed daily quota: only fill the slots not already used by today's
        # completed revisions, so finishing one shrinks the queue rather than
        # pulling in a replacement. Once the quota is met, the queue is empty.
        remaining = quota - count_revisions_done_today(user_id)
        if remaining <= 0:
            return []
        due = get_revisions_due(user_id, today, limit=remaining)
    # Attach the 15 source x rating outcomes so the dashboard can show
    # "if I pick this, when does it come back" without duplicating FSRS math.
    for q in due:
        q["schedule_preview"] = scheduler.preview(
            q,
            desired_retention=settings["desired_retention"],
            params=settings["fsrs_params"],
        )
    return due


@app.get("/api/activity-summary")
def activity_summary(user_id: str = Depends(get_current_user_id)):
    """Per-question revision summary (revision_count, last_revised_at) from the
    audit log — used by the dashboard to split items into new vs revised."""
    return get_questions_activity_summary(user_id)


@app.get("/api/settings")
def read_settings(user_id: str = Depends(get_current_user_id)):
    return get_user_settings(user_id)


@app.put("/api/settings")
def write_settings(s: SettingsUpdate, user_id: str = Depends(get_current_user_id)):
    # Only write the fields the client sent, so a queue-size save from an
    # older client can't reset the retention setting (and vice versa).
    updates = {k: v for k, v in s.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No settings to update")
    return upsert_user_settings(user_id, updates)


@app.post("/api/questions/{qid}/review")
def review_question(qid: int, review: ReviewIn, user_id: str = Depends(get_current_user_id)):
    # Auto-merge duplicates for this question's URL before reviewing
    qid = merge_duplicates_for_question(user_id, qid) or qid
    question = get_question(user_id, qid)
    if not question:
        raise HTTPException(404, "Question not found")
    settings = get_user_settings(user_id)
    result = scheduler.apply_review(
        question, review.self_rating, review.solution_source,
        desired_retention=settings["desired_retention"], params=settings["fsrs_params"],
    )
    update_question_schedule(
        user_id, qid, result, set_reviewed=True, solution_source=review.solution_source,
    )
    insert_event(
        user_id, qid, "reviewed",
        self_rating=review.self_rating, solution_source=review.solution_source,
        interval=result["interval"], next_review=result["next_review"],
        stability=result["stability"], fsrs_difficulty=result["fsrs_difficulty"],
        fsrs_state=result["fsrs_state"],
    )
    return get_question(user_id, qid)


@app.get("/api/questions/{qid}/history")
def question_history(qid: int, user_id: str = Depends(get_current_user_id)):
    """Audit log for a single question: every solve / review / re-attempt."""
    question = get_question(user_id, qid)
    if not question:
        raise HTTPException(404, "Question not found")
    return {"question": question, "events": get_question_events(user_id, qid)}


@app.put("/api/questions/{qid}")
def edit_question(qid: int, q: QuestionUpdate, user_id: str = Depends(get_current_user_id)):
    existing = get_question(user_id, qid)
    if not existing:
        raise HTTPException(404, "Question not found")
    updates = {}
    for field in ["url", "title", "difficulty", "self_rating", "time_taken", "notes", "pattern", "question_type", "approach", "mistakes", "time_complexity", "space_complexity", "solution_source"]:
        val = getattr(q, field)
        if val is not None:
            updates[field] = val
    if "url" in updates:
        user_plats = get_user_platforms(user_id)
        updates["platform"] = detect_platform(updates["url"], user_plats)
    if not updates:
        raise HTTPException(400, "No fields to update")
    return update_question(user_id, qid, updates)


@app.get("/api/activity/today")
def activity_today(user_id: str = Depends(get_current_user_id)):
    return get_today_activity(user_id)


@app.get("/api/activity/heatmap")
def activity_heatmap(user_id: str = Depends(get_current_user_id)):
    """Per-day activity detail for the last ~53 weeks, in the user's timezone:
    {date: {total, new, revised, difficulty: {easy/medium/hard/unknown: n}}}."""
    return get_activity_heatmap(user_id)


@app.get("/api/stats")
def stats(user_id: str = Depends(get_current_user_id)):
    return get_stats(user_id)


@app.get("/api/platforms")
def list_platforms(user_id: str = Depends(get_current_user_id)):
    user_plats = get_user_platforms(user_id)
    builtin = [{"name": name, "url_pattern": pattern, "builtin": True} for name, pattern in PLATFORM_PATTERNS.items()]
    custom = [{**p, "builtin": False} for p in user_plats]
    return builtin + custom


@app.post("/api/platforms")
def add_platform(p: PlatformIn, user_id: str = Depends(get_current_user_id)):
    return insert_user_platform(user_id, {"name": p.name, "url_pattern": p.url_pattern})


@app.delete("/api/platforms/{platform_id}")
def remove_platform(platform_id: int, user_id: str = Depends(get_current_user_id)):
    deleted = delete_user_platform(user_id, platform_id)
    if not deleted:
        raise HTTPException(404, "Platform not found")
    return {"ok": True}


@app.post("/api/questions/{qid}/cancel-attempt")
def cancel_attempt(qid: int, user_id: str = Depends(get_current_user_id)):
    """Undo a timer-start on an already-tracked question: roll back the
    attempt counter and the 'attempted' event that POST /questions logged.
    (For a question the start newly created, the extension DELETEs it instead.)"""
    question = get_question(user_id, qid)
    if not question:
        raise HTTPException(404, "Question not found")
    decrement_attempts(user_id, qid)
    delete_latest_attempt_event(user_id, qid)
    return {"ok": True}


@app.delete("/api/questions/{qid}")
def remove_question(qid: int, user_id: str = Depends(get_current_user_id)):
    deleted = delete_question(user_id, qid)
    if not deleted:
        raise HTTPException(404, "Question not found")
    return {"ok": True}


@app.post("/api/questions/merge-duplicates")
def merge_dupes(user_id: str = Depends(get_current_user_id)):
    merge_duplicates(user_id)
    return {"ok": True}


# --- Pattern endpoints ---


@app.get("/api/patterns")
def patterns_overview(user_id: str = Depends(get_current_user_id)):
    """Full patterns data with user's progress overlaid."""
    questions = get_all_questions(user_id)

    # Build set of tracked problem numbers and their data
    tracked: dict[int, dict] = {}
    for q in questions:
        if q.get("platform") != "leetcode":
            continue
        num = extract_leetcode_number(q["url"])
        if num is not None:
            tracked[num] = q

    categories = []
    overall_solved = 0
    overall_total = 0

    for cat_name, cat_patterns in PATTERNS.items():
        cat_solved = 0
        cat_total = 0
        patterns_list = []

        for pat_name, problem_nums in cat_patterns.items():
            problems = []
            pat_solved = 0
            for num in problem_nums:
                slug = PROBLEM_SLUGS.get(num)
                problem_data = {
                    "number": num,
                    "slug": slug,
                    "title": slug.replace("-", " ").title() if slug else f"Problem {num}",
                    "url": f"https://leetcode.com/problems/{slug}/" if slug else None,
                    "tracked": num in tracked,
                }
                if num in tracked:
                    problem_data["next_review"] = tracked[num].get("next_review")
                    problem_data["self_rating"] = tracked[num].get("self_rating")
                    pat_solved += 1
                problems.append(problem_data)

            cat_solved += pat_solved
            cat_total += len(problem_nums)
            patterns_list.append({
                "name": pat_name,
                "problems": problems,
                "solved_count": pat_solved,
                "total_count": len(problem_nums),
            })

        overall_solved += cat_solved
        overall_total += cat_total
        categories.append({
            "name": cat_name,
            "patterns": patterns_list,
            "solved_count": cat_solved,
            "total_count": cat_total,
        })

    return {
        "categories": categories,
        "overall": {"solved": overall_solved, "total": overall_total},
    }


@app.get("/api/patterns/recommend")
def patterns_recommend(user_id: str = Depends(get_current_user_id)):
    """Recommend next 5 problems to solve based on pattern coverage."""
    questions = get_all_questions(user_id)
    tracked_nums: set[int] = set()
    for q in questions:
        if q.get("platform") != "leetcode":
            continue
        num = extract_leetcode_number(q["url"])
        if num is not None:
            tracked_nums.add(num)

    # Categorize patterns: partially done (priority 1) and untouched (priority 2)
    partial = []  # (category, pattern, unsolved_nums)
    untouched = []

    for cat_name, cat_patterns in PATTERNS.items():
        for pat_name, problem_nums in cat_patterns.items():
            solved_in_pattern = [n for n in problem_nums if n in tracked_nums]
            unsolved = [n for n in problem_nums if n not in tracked_nums]
            if not unsolved:
                continue
            if solved_in_pattern:
                partial.append((cat_name, pat_name, unsolved))
            else:
                untouched.append((cat_name, pat_name, unsolved))

    recommendations = []
    for source in [partial, untouched]:
        for cat_name, pat_name, unsolved in source:
            for num in unsolved:
                if len(recommendations) >= 5:
                    break
                slug = PROBLEM_SLUGS.get(num)
                recommendations.append({
                    "number": num,
                    "slug": slug,
                    "title": slug.replace("-", " ").title() if slug else f"Problem {num}",
                    "url": f"https://leetcode.com/problems/{slug}/" if slug else None,
                    "pattern": f"{cat_name} > {pat_name}",
                })
            if len(recommendations) >= 5:
                break
        if len(recommendations) >= 5:
            break

    return recommendations


@app.get("/api/patterns/list")
def patterns_list():
    """Flat list of all pattern labels for dropdown menus."""
    return get_all_pattern_labels()


# --- Pages ---


@app.get("/", response_class=HTMLResponse)
def landing():
    with open("templates/landing.html") as f:
        return f.read()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    with open("templates/dashboard.html") as f:
        return f.read()


@app.get("/prep", response_class=HTMLResponse)
def prep_page():
    with open("templates/prep.html") as f:
        return f.read()


@app.get("/flex", response_class=HTMLResponse)
def flex_page():
    with open("templates/flex.html") as f:
        return f.read()


@app.get("/flex/{user_id}", response_class=HTMLResponse)
def flex_page_user(user_id: str):
    with open("templates/flex.html") as f:
        return f.read()


@app.get("/research", response_class=HTMLResponse)
def research_page():
    with open("templates/research.html") as f:
        return f.read()


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    # Access is enforced client-side (checks /api/me.is_admin) and server-side
    # on every /api/admin/* call via the require_admin dependency.
    with open("templates/admin.html") as f:
        return f.read()


@app.get("/api/flex/{user_id}")
def flex_stats(user_id: str):
    stats = get_flex_stats(user_id)
    if stats is None or stats.get("total_solved", 0) == 0:
        # Still return the empty stats so the frontend can show the roast
        return stats or {"total_solved": 0}
    return stats


# Shared static assets for templates (e.g. the question-history modal)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Profile pictures uploaded through /api/profile/avatar
os.makedirs(AVATAR_DIR, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")

# Static file serving for research docs (markdown + SVG diagrams)
# Check Docker path first (research-data/ copied in during build), then local dev path
_research_candidates = [
    os.path.join(os.path.dirname(__file__), "research-data"),
    os.path.join(os.path.dirname(__file__), "..", "thoughts", "shared", "research"),
]
for _candidate in _research_candidates:
    if os.path.isdir(_candidate):
        app.mount(
            "/research-assets",
            StaticFiles(directory=_candidate),
            name="research-assets",
        )
        break
