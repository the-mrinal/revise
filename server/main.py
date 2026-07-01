"""FastAPI server for Revise."""

import os
import re
from datetime import date
from typing import Optional
from urllib.parse import urlparse, urlunparse

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import (
    exchange_code_for_session,
    get_current_claims,
    get_current_user_id,
    refresh_session,
    send_magic_link,
)
from database import (
    count_revisions_done_today,
    delete_question,
    delete_user_platform,
    ensure_user_profile,
    find_by_url,
    find_user_by_email,
    get_all_questions,
    get_flex_stats,
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
    merge_duplicates,
    merge_duplicates_for_question,
    revoke_feature,
    set_user_admin,
    update_question,
    update_question_sm2,
    upsert_user_settings,
)
from patterns import (
    PATTERNS,
    PROBLEM_SLUGS,
    PROBLEM_TO_PATTERN,
    extract_leetcode_number,
    get_all_pattern_labels,
    get_pattern_for_url,
)
from sm2 import sm2

app = FastAPI(title="Revise")

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


class QuestionIn(BaseModel):
    url: str
    title: Optional[str] = None
    difficulty: Optional[str] = None
    self_rating: int = Field(ge=1, le=5)
    time_taken: Optional[int] = None
    notes: Optional[str] = None
    question_type: Optional[str] = "dsa"


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


class ReviewIn(BaseModel):
    self_rating: int = Field(ge=1, le=5)


class MagicLinkRequest(BaseModel):
    email: str


class PlatformIn(BaseModel):
    name: str
    url_pattern: str


class SettingsUpdate(BaseModel):
    # 0 = unlimited. Capped to keep a single day's queue sane.
    revision_queue_size: int = Field(ge=0, le=500)


class RefreshRequest(BaseModel):
    refresh_token: str


class FeatureToggle(BaseModel):
    feature: str
    granted: bool


class AdminToggle(BaseModel):
    is_admin: bool


class GrantByEmail(BaseModel):
    email: str
    feature: str


# --- Auth endpoints (no auth required) ---


@app.post("/api/auth/magic-link")
def auth_magic_link(req: MagicLinkRequest):
    send_magic_link(req.email)
    return {"message": "Magic link sent!"}


@app.get("/api/auth/callback")
def auth_callback(
    token_hash: str = Query(None),
    type: str = Query(None),
):
    # PKCE flow: Supabase sends token_hash & type as query params
    if token_hash and type:
        tokens = exchange_code_for_session(token_hash, type)
        redirect_url = (
            f"/dashboard#access_token={tokens['access_token']}"
            f"&refresh_token={tokens['refresh_token']}"
        )
        return RedirectResponse(url=redirect_url)

    # Implicit flow: Supabase sends tokens in the URL fragment (#access_token=...)
    # Fragments aren't sent to the server, so serve a page that forwards them.
    return HTMLResponse(
        "<script>location.replace('/dashboard' + location.hash)</script>"
    )


@app.post("/api/auth/refresh")
def auth_refresh(req: RefreshRequest):
    tokens = refresh_session(req.refresh_token)
    return tokens


# --- Identity & access ---


@app.get("/api/me")
def me(claims: dict = Depends(get_current_claims)):
    """Identity + access for the current user. Also upserts the profile so the
    admin panel can list users who have signed in at least once."""
    user_id = claims["sub"]
    email = claims.get("email")
    ensure_user_profile(user_id, email)
    return {
        "user_id": user_id,
        "email": email,
        "is_admin": is_user_admin(user_id),
        "features": get_user_features(user_id),
    }


# --- Admin panel (admin role required) ---


@app.get("/api/admin/features")
def admin_list_features(_: dict = Depends(require_admin)):
    return FEATURES


@app.get("/api/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    return list_all_users()


@app.get("/api/admin/users/{uid}/activity")
def admin_user_activity(uid: str, _: dict = Depends(require_admin)):
    return get_user_activity(uid)


@app.post("/api/admin/users/{uid}/features")
def admin_set_feature(uid: str, body: FeatureToggle, _: dict = Depends(require_admin)):
    if body.feature not in FEATURES:
        raise HTTPException(400, f"Unknown feature: {body.feature}")
    if body.granted:
        grant_feature(uid, body.feature)
    else:
        revoke_feature(uid, body.feature)
    return {"ok": True}


@app.post("/api/admin/users/{uid}/admin")
def admin_set_admin(uid: str, body: AdminToggle, claims: dict = Depends(require_admin)):
    if uid == claims["sub"] and not body.is_admin:
        raise HTTPException(400, "You can't remove your own admin access")
    set_user_admin(uid, body.is_admin)
    return {"ok": True}


@app.post("/api/admin/grant-by-email")
def admin_grant_by_email(body: GrantByEmail, _: dict = Depends(require_admin)):
    if body.feature not in FEATURES:
        raise HTTPException(400, f"Unknown feature: {body.feature}")
    user = find_user_by_email(body.email)
    if not user:
        raise HTTPException(404, "No user has signed in with that email yet")
    grant_feature(user["user_id"], body.feature)
    return {"ok": True, "user_id": user["user_id"], "email": user["email"]}


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
        )
        return updated

    user_plats = get_user_platforms(user_id)
    platform = detect_platform(url, user_plats)
    sm2_result = sm2(q.self_rating, 2.5, 1, 0)

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
        "next_review": sm2_result["next_review"],
        "pattern": pattern_label,
        "question_type": q.question_type,
    }
    question = insert_question(user_id, data)
    update_question_sm2(user_id, question["id"], sm2_result)
    updated = get_question(user_id, question["id"])
    insert_event(
        user_id, question["id"], "created",
        self_rating=q.self_rating, time_taken=q.time_taken,
        interval=sm2_result["interval"], repetitions=sm2_result["repetitions"],
        easiness_factor=sm2_result["easiness_factor"], next_review=sm2_result["next_review"],
    )
    return updated


@app.get("/api/questions")
def list_questions(user_id: str = Depends(get_current_user_id)):
    return get_all_questions(user_id)


@app.get("/api/revisions/today")
def revisions_today(user_id: str = Depends(get_current_user_id)):
    quota = get_user_settings(user_id).get("revision_queue_size")
    today = date.today().isoformat()
    # 0 / unset means no daily cap — surface every due revision.
    if not quota or quota <= 0:
        return get_revisions_due(user_id, today, limit=None)
    # Fixed daily quota: only fill the slots not already used by today's
    # completed revisions, so finishing one shrinks the queue rather than
    # pulling in a replacement. Once the quota is met, the queue is empty.
    remaining = quota - count_revisions_done_today(user_id)
    if remaining <= 0:
        return []
    return get_revisions_due(user_id, today, limit=remaining)


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
    return upsert_user_settings(user_id, {"revision_queue_size": s.revision_queue_size})


@app.post("/api/questions/{qid}/review")
def review_question(qid: int, review: ReviewIn, user_id: str = Depends(get_current_user_id)):
    # Auto-merge duplicates for this question's URL before reviewing
    qid = merge_duplicates_for_question(user_id, qid) or qid
    question = get_question(user_id, qid)
    if not question:
        raise HTTPException(404, "Question not found")
    result = sm2(
        review.self_rating,
        question["easiness_factor"],
        question["interval"],
        question["repetitions"],
    )
    update_question_sm2(user_id, qid, result, set_reviewed=True)
    insert_event(
        user_id, qid, "reviewed",
        self_rating=review.self_rating,
        interval=result["interval"], repetitions=result["repetitions"],
        easiness_factor=result["easiness_factor"], next_review=result["next_review"],
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
    for field in ["url", "title", "difficulty", "self_rating", "time_taken", "notes", "pattern", "question_type", "approach", "mistakes", "time_complexity", "space_complexity"]:
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
