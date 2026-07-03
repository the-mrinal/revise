"""Supabase database queries for Revise."""

import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from supabase import create_client

# IANA zone used when a user hasn't picked one (or before migration 007).
DEFAULT_TIMEZONE = "Asia/Kolkata"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

_client = None


def get_client():
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


COLUMNS = (
    "id, user_id, url, title, platform, difficulty, self_rating, time_taken, "
    "notes, solved_at, easiness_factor, interval, repetitions, next_review, "
    "last_reviewed, attempts, pattern, question_type, "
    "approach, mistakes, time_complexity, space_complexity"
)


EVENT_COLUMNS = (
    "id, question_id, event_type, self_rating, time_taken, interval, "
    "repetitions, easiness_factor, next_review, reconstructed, created_at"
)

# Fields an event may carry beyond the always-present user/question/type.
_EVENT_FIELDS = (
    "self_rating", "time_taken", "interval", "repetitions",
    "easiness_factor", "next_review", "reconstructed", "created_at",
)


def insert_event(user_id: str, question_id: int, event_type: str, **fields) -> None:
    """Append a row to the per-question audit log. Best-effort: never raises."""
    try:
        client = get_client()
        row = {"user_id": user_id, "question_id": question_id, "event_type": event_type}
        for key in _EVENT_FIELDS:
            if fields.get(key) is not None:
                row[key] = fields[key]
        client.table("question_events").insert(row).execute()
    except Exception as e:  # logging must never break a save
        print(f"[events] failed to log {event_type} for q{question_id}: {e}")


def get_question_events(user_id: str, qid: int) -> list[dict]:
    client = get_client()
    result = (
        client.table("question_events")
        .select(EVENT_COLUMNS)
        .eq("user_id", user_id)
        .eq("question_id", qid)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def insert_question(user_id: str, data: dict) -> dict:
    client = get_client()
    row = {**data, "user_id": user_id}
    result = client.table("questions").insert(row).execute()
    return result.data[0]


def get_all_questions(user_id: str) -> list[dict]:
    client = get_client()
    result = (
        client.table("questions")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .order("solved_at", desc=True)
        .execute()
    )
    return result.data


def get_question(user_id: str, qid: int) -> dict | None:
    client = get_client()
    result = (
        client.table("questions")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .eq("id", qid)
        .execute()
    )
    return result.data[0] if result.data else None


def update_question_sm2(user_id: str, qid: int, data: dict, set_reviewed: bool = False):
    client = get_client()
    update_data = {
        "easiness_factor": data["easiness_factor"],
        "interval": data["interval"],
        "repetitions": data["repetitions"],
        "next_review": data["next_review"],
    }
    if set_reviewed:
        update_data["last_reviewed"] = datetime.utcnow().isoformat()
    (
        client.table("questions")
        .update(update_data)
        .eq("user_id", user_id)
        .eq("id", qid)
        .execute()
    )


def get_revisions_due(
    user_id: str, target_date: str | None = None, limit: int | None = None
) -> list[dict]:
    target = target_date or date.today().isoformat()
    client = get_client()
    query = (
        client.table("questions")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .lte("next_review", target)
        .order("next_review", desc=False)
    )
    # limit None or <= 0 means "no cap" — surface every due revision.
    if limit and limit > 0:
        query = query.limit(limit)
    return query.execute().data


def count_revisions_done_today(user_id: str) -> int:
    """Count distinct questions genuinely revised today.

    A revision is a 'reviewed' event on a calendar day after the question's
    first-ever solve day — the same definition used elsewhere (the extension
    logs a 'reviewed' event even on a first solve, so first solves must be
    excluded). Used to enforce the daily revision cap: the queue surfaces at
    most (queue_size - this) cards, so completing a revision shrinks the queue
    instead of pulling in a replacement.
    """
    today = date.today().isoformat()
    client = get_client()
    reviewed = (
        client.table("question_events")
        .select("question_id")
        .eq("user_id", user_id)
        .eq("event_type", "reviewed")
        .gte("created_at", f"{today}T00:00:00")
        .execute()
    ).data
    qids = {r["question_id"] for r in reviewed}
    if not qids:
        return 0
    # Find each candidate question's first-ever solve day from the event log.
    events = (
        client.table("question_events")
        .select("question_id, created_at")
        .eq("user_id", user_id)
        .in_("question_id", list(qids))
        .in_("event_type", ["created", "reviewed"])
        .order("created_at", desc=False)
        .execute()
    ).data
    first_day: dict[int, str] = {}
    for e in events:
        qid = e["question_id"]
        day = (e.get("created_at") or "")[:10]
        if not day:
            continue
        if qid not in first_day or day < first_day[qid]:
            first_day[qid] = day
    # Only count questions first solved before today (genuine revisions).
    return sum(1 for qid in qids if first_day.get(qid, today) < today)


def update_question(user_id: str, qid: int, data: dict) -> dict | None:
    client = get_client()
    result = (
        client.table("questions")
        .update(data)
        .eq("user_id", user_id)
        .eq("id", qid)
        .execute()
    )
    return result.data[0] if result.data else None


def delete_question(user_id: str, qid: int) -> bool:
    client = get_client()
    result = (
        client.table("questions")
        .delete()
        .eq("user_id", user_id)
        .eq("id", qid)
        .execute()
    )
    return len(result.data) > 0


def get_today_activity(user_id: str) -> list[dict]:
    today = date.today().isoformat()
    client = get_client()
    # Fetch rows where solved_at or last_reviewed is today
    result = (
        client.table("questions")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .or_(f"solved_at.gte.{today}T00:00:00,last_reviewed.gte.{today}T00:00:00")
        .execute()
    )
    rows_data = result.data

    # A question is NEW today only if today is its first-ever solve session.
    # We decide from the audit log: the earliest 'created'/'reviewed' event.
    # The extension logs a 'reviewed' event even on the first solve, so the
    # timestamp alone is unreliable — the event log is the source of truth.
    qids = [r["id"] for r in rows_data]
    first_event_date: dict[int, str] = {}
    if qids:
        events = (
            client.table("question_events")
            .select("question_id, created_at")
            .eq("user_id", user_id)
            .in_("question_id", qids)
            .in_("event_type", ["created", "reviewed"])
            .order("created_at", desc=False)
            .execute()
        )
        for e in events.data:
            qid = e["question_id"]
            day = (e.get("created_at") or "")[:10]
            if not day:
                continue
            if qid not in first_event_date or day < first_event_date[qid]:
                first_event_date[qid] = day

    rows = []
    for r in rows_data:
        first_day = first_event_date.get(r["id"])
        if first_day is not None:
            activity_type = "NEW" if first_day == today else "REVISION"
        else:
            # Fallback when no event log exists yet (pre-backfill): a first
            # solve dated today is NEW; activity on an older question is REVISION.
            activity_type = "NEW" if (r.get("solved_at") or "")[:10] == today else "REVISION"
        rows.append({**r, "activity_type": activity_type})
    # Sort: most recent activity first
    rows.sort(
        key=lambda r: r.get("last_reviewed") or r.get("solved_at") or "",
        reverse=True,
    )
    return rows


def get_questions_activity_summary(user_id: str) -> dict:
    """Per-question revision summary derived from the audit log.

    The extension logs a 'reviewed' event even on a first solve, so a question's
    last_reviewed timestamp can't tell a genuine revision from the original
    solve. The event log can: a *revision* is any review on a calendar day after
    the question's first-ever solve day. Returns a dict keyed by question id:

        { qid: {first_solved_on, revision_count, last_revised_at} }
    """
    client = get_client()
    # TODO: PostgREST caps responses at 1000 rows, so heavy users lose the
    # oldest events here; paginate like get_activity_heatmap does.
    events = (
        client.table("question_events")
        .select("question_id, created_at")
        .eq("user_id", user_id)
        .in_("event_type", ["created", "reviewed"])
        .order("created_at", desc=False)
        .execute()
    ).data

    by_q: dict[int, list[str]] = defaultdict(list)
    for e in events:
        ts = e.get("created_at")
        if ts:
            by_q[e["question_id"]].append(ts)

    summary: dict[int, dict] = {}
    for qid, times in by_q.items():
        times.sort()
        first_day = times[0][:10]
        revision_times = [t for t in times if t[:10] > first_day]
        revision_days = {t[:10] for t in revision_times}
        summary[qid] = {
            "first_solved_on": first_day,
            "revision_count": len(revision_days),
            "last_revised_at": max(revision_times) if revision_times else None,
        }
    return summary


def _safe_zone(tz_name: str | None) -> ZoneInfo:
    """ZoneInfo for tz_name, falling back to the default on bad/missing values."""
    try:
        return ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _to_local_day(ts: str, zone: ZoneInfo) -> str | None:
    """ISO timestamp -> YYYY-MM-DD in the given zone (None if unparseable).

    Supabase returns timestamptz as '+00:00'-suffixed ISO; tolerate 'Z' and
    naive strings (treated as UTC) for older/reconstructed rows.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(zone).date().isoformat()


def _bucket_event_days(timestamps: list, tz_name: str = DEFAULT_TIMEZONE) -> dict[str, int]:
    """Bucket ISO timestamps into local-day (YYYY-MM-DD) -> count.

    Days are local to the user's profile timezone — the same convention the
    streak logic uses, so the heatmap lights the squares streaks count.
    """
    zone = _safe_zone(tz_name)
    counts: dict[str, int] = defaultdict(int)
    for ts in timestamps:
        day = _to_local_day(ts, zone)
        if day:
            counts[day] += 1
    return dict(counts)


def get_user_timezone(user_id: str) -> str:
    """The user's IANA timezone, defaulting to IST (also pre-migration 007)."""
    try:
        client = get_client()
        result = (
            client.table("user_profiles")
            .select("timezone")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data and result.data[0].get("timezone"):
            return result.data[0]["timezone"]
    except Exception as e:
        print(f"[profile] timezone read failed (run migration 007?): {e}")
    return DEFAULT_TIMEZONE


def _compute_streaks(activity_dates: set[str], today: date | None = None) -> tuple[int, int]:
    """(current_streak, longest_streak) from a set of YYYY-MM-DD strings.

    The current streak counts consecutive days ending today or yesterday
    (yesterday keeps a streak alive until the day is actually missed).
    `today` should be the user's local today so the cutoff matches the
    timezone the dates were bucketed in.
    """
    if not activity_dates:
        return 0, 0
    sorted_dates = sorted(set(date.fromisoformat(d) for d in activity_dates))

    longest_streak = 0
    streak = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] - sorted_dates[i - 1] == timedelta(days=1):
            streak += 1
        else:
            longest_streak = max(longest_streak, streak)
            streak = 1
    longest_streak = max(longest_streak, streak)

    current_streak = 0
    if today is None:
        today = date.today()
    if sorted_dates[-1] >= today - timedelta(days=1):
        current_streak = 1
        for i in range(len(sorted_dates) - 2, -1, -1):
            if sorted_dates[i + 1] - sorted_dates[i] == timedelta(days=1):
                current_streak += 1
            else:
                break
    return current_streak, longest_streak


def get_activity_heatmap(user_id: str, days: int = 371) -> dict[str, int]:
    """Daily activity counts (solves + revisions) for the last ~53 weeks,
    bucketed into the user's profile timezone.

    'attempted' events are excluded — they're timer-start artifacts and would
    double-count against the 'created'/'reviewed' rows written on finish.
    """
    tz_name = get_user_timezone(user_id)
    client = get_client()
    # One extra day of slack so a UTC cutoff can't clip events that fall
    # inside the window once shifted into a UTC+N zone.
    cutoff = (date.today() - timedelta(days=days + 1)).isoformat()
    timestamps: list[str] = []
    start, page = 0, 1000  # PostgREST silently truncates at 1000 rows/request
    while True:
        rows = (
            client.table("question_events")
            .select("created_at")
            .eq("user_id", user_id)
            .in_("event_type", ["created", "reviewed"])
            .gte("created_at", cutoff)
            .order("created_at", desc=False)
            .range(start, start + page - 1)
            .execute()
        ).data
        timestamps.extend(r.get("created_at") for r in rows)
        if len(rows) < page:
            break
        start += page
    return _bucket_event_days(timestamps, tz_name)


def find_by_url(user_id: str, url: str) -> dict | None:
    client = get_client()
    result = (
        client.table("questions")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .eq("url", url)
        .execute()
    )
    return result.data[0] if result.data else None


def increment_attempts(user_id: str, qid: int, title: str | None = None) -> dict:
    # Fetch current, increment, update
    question = get_question(user_id, qid)
    if not question:
        raise ValueError(f"Question {qid} not found")
    update_data = {"attempts": (question.get("attempts") or 1) + 1}
    if title:
        update_data["title"] = title
    return update_question(user_id, qid, update_data)


def decrement_attempts(user_id: str, qid: int) -> dict | None:
    """Roll back one attempt bump (never below 1). Used when a timer-start on
    an existing question is cancelled."""
    question = get_question(user_id, qid)
    if not question:
        return None
    attempts = max(1, (question.get("attempts") or 1) - 1)
    return update_question(user_id, qid, {"attempts": attempts})


def delete_latest_attempt_event(user_id: str, qid: int) -> None:
    """Drop the newest 'attempted' event for a question. Best-effort: the
    counter rollback matters more than the log entry."""
    try:
        client = get_client()
        rows = (
            client.table("question_events")
            .select("id")
            .eq("user_id", user_id)
            .eq("question_id", qid)
            .eq("event_type", "attempted")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data
        if rows:
            client.table("question_events").delete().eq("id", rows[0]["id"]).execute()
    except Exception as e:
        print(f"[events] failed to delete attempted event for q{qid}: {e}")


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip query params, fragments, sub-paths."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    m = re.match(r"(/problems/[^/]+)", path)
    if m and "leetcode.com" in parsed.netloc:
        path = m.group(1)
    return urlunparse((parsed.scheme, parsed.netloc, path + "/", "", "", ""))


def _merge_url_group(client, url: str, rows: list[dict]):
    """Merge a group of duplicate rows sharing the same normalized URL."""
    if len(rows) < 2:
        return
    # Keep the row with highest repetitions
    rows.sort(key=lambda r: (r.get("repetitions") or 0, r.get("solved_at") or ""), reverse=True)
    keep = rows[0]
    others = rows[1:]

    total_time = sum(r.get("time_taken") or 0 for r in rows)
    most_recent = max(rows, key=lambda r: r.get("solved_at") or "")

    update_data = {
        "url": url,  # normalized URL
        "attempts": len(rows),
        "time_taken": total_time if total_time > 0 else None,
        "title": most_recent.get("title"),
        "difficulty": most_recent.get("difficulty"),
        "self_rating": most_recent.get("self_rating"),
        "notes": most_recent.get("notes"),
    }
    client.table("questions").update(update_data).eq("id", keep["id"]).execute()
    for other in others:
        client.table("questions").delete().eq("id", other["id"]).execute()


def merge_duplicates(user_id: str):
    """Consolidate duplicate URL entries for a user."""
    all_rows = get_all_questions(user_id)
    by_url: dict[str, list[dict]] = {}
    for row in all_rows:
        key = _normalize_url(row["url"])
        by_url.setdefault(key, []).append(row)

    client = get_client()
    for url, rows in by_url.items():
        _merge_url_group(client, url, rows)


def merge_duplicates_for_question(user_id: str, qid: int) -> int | None:
    """Merge duplicates for a single question's URL. Returns the surviving question ID."""
    question = get_question(user_id, qid)
    if not question:
        return qid
    norm_url = _normalize_url(question["url"])
    all_rows = get_all_questions(user_id)
    dupes = [r for r in all_rows if _normalize_url(r["url"]) == norm_url]
    if len(dupes) < 2:
        return qid
    client = get_client()
    _merge_url_group(client, norm_url, dupes)
    # Return the surviving ID (highest repetitions)
    dupes.sort(key=lambda r: (r.get("repetitions") or 0, r.get("solved_at") or ""), reverse=True)
    return dupes[0]["id"]


DEFAULT_REVISION_QUEUE_SIZE = 20


def get_user_settings(user_id: str) -> dict:
    """Return the user's settings, falling back to defaults if none are stored."""
    client = get_client()
    result = (
        client.table("user_settings")
        .select("revision_queue_size")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return {"revision_queue_size": DEFAULT_REVISION_QUEUE_SIZE}


def upsert_user_settings(user_id: str, data: dict) -> dict:
    """Insert or update the user's settings row and return the stored values."""
    client = get_client()
    row = {"user_id": user_id, **data}
    result = (
        client.table("user_settings")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    stored = result.data[0] if result.data else row
    return {"revision_queue_size": stored.get("revision_queue_size")}


def get_user_platforms(user_id: str) -> list[dict]:
    client = get_client()
    result = (
        client.table("user_platforms")
        .select("id, user_id, name, url_pattern, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def insert_user_platform(user_id: str, data: dict) -> dict:
    client = get_client()
    row = {"user_id": user_id, "name": data["name"], "url_pattern": data["url_pattern"]}
    result = client.table("user_platforms").insert(row).execute()
    return result.data[0]


def delete_user_platform(user_id: str, platform_id: int) -> bool:
    client = get_client()
    result = (
        client.table("user_platforms")
        .delete()
        .eq("user_id", user_id)
        .eq("id", platform_id)
        .execute()
    )
    return len(result.data) > 0


def _norm_difficulty(value: str | None) -> str:
    """Bucket key for stats: lowercase so legacy capitalized rows ('Easy')
    can't split into a duplicate bucket alongside 'easy'."""
    return (value or "").strip().lower() or "unknown"


def get_stats(user_id: str) -> dict:
    all_rows = get_all_questions(user_id)
    total = len(all_rows)

    by_difficulty: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    ratings = []
    due_today = 0
    today = date.today().isoformat()

    for r in all_rows:
        diff = _norm_difficulty(r.get("difficulty"))
        by_difficulty[diff] = by_difficulty.get(diff, 0) + 1

        plat = r.get("platform") or "unknown"
        by_platform[plat] = by_platform.get(plat, 0) + 1

        if r.get("self_rating"):
            ratings.append(r["self_rating"])

        if r.get("next_review") and r["next_review"] <= today:
            due_today += 1

    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0

    return {
        "total": total,
        "by_difficulty": by_difficulty,
        "by_platform": by_platform,
        "due_today": due_today,
        "avg_rating": avg_rating,
    }


def get_flex_stats(user_id: str) -> dict | None:
    """Compute public-safe stats for the flex/show-off page. Returns None if no questions."""
    from patterns import PATTERNS, extract_leetcode_number

    all_rows = get_all_questions(user_id)
    total = len(all_rows)
    if total == 0:
        return {"total_solved": 0}

    zone = _safe_zone(get_user_timezone(user_id))

    by_difficulty: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    ratings = []
    total_time_mins = 0
    total_reviews = 0
    activity_dates: set[str] = set()

    for r in all_rows:
        diff = _norm_difficulty(r.get("difficulty"))
        by_difficulty[diff] = by_difficulty.get(diff, 0) + 1

        plat = r.get("platform") or "unknown"
        by_platform[plat] = by_platform.get(plat, 0) + 1

        if r.get("self_rating"):
            ratings.append(r["self_rating"])

        if r.get("time_taken"):
            total_time_mins += r["time_taken"]

        total_reviews += (r.get("attempts") or 1)

        # Collect activity dates (user-local days) for streak calculation
        solved_day = _to_local_day(r.get("solved_at"), zone)
        if solved_day:
            activity_dates.add(solved_day)
        reviewed_day = _to_local_day(r.get("last_reviewed"), zone)
        if reviewed_day:
            activity_dates.add(reviewed_day)

    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
    total_time_hours = round(total_time_mins / 60, 1)

    # Streak calculation, anchored to the user's local today
    current_streak, longest_streak = _compute_streaks(
        activity_dates, today=datetime.now(zone).date()
    )

    # Pattern stats
    tracked_nums: set[int] = set()
    for q in all_rows:
        if q.get("platform") != "leetcode":
            continue
        num = extract_leetcode_number(q["url"])
        if num is not None:
            tracked_nums.add(num)

    total_categories = len(PATTERNS)
    mastered = 0
    started = 0
    cat_progress: list[tuple[str, float]] = []

    for cat_name, cat_patterns in PATTERNS.items():
        cat_total = sum(len(nums) for nums in cat_patterns.values())
        cat_solved = sum(1 for nums in cat_patterns.values() for n in nums if n in tracked_nums)
        pct = cat_solved / cat_total if cat_total > 0 else 0
        if pct >= 1.0:
            mastered += 1
        if pct > 0:
            started += 1
        cat_progress.append((cat_name, pct))

    cat_progress.sort(key=lambda x: x[1], reverse=True)
    top_patterns = [{"name": name, "pct": round(pct * 100)} for name, pct in cat_progress[:3] if pct > 0]

    # Fun title
    if mastered >= 12:
        title = "Pattern Grandmaster"
    elif total >= 200:
        title = "Grind Lord"
    elif mastered >= 8:
        title = "Pattern Crusher"
    elif current_streak >= 30:
        title = "Streak Machine"
    elif total >= 100:
        title = "Centurion"
    elif mastered >= 4:
        title = "Pattern Apprentice"
    elif total >= 50:
        title = "Half-Century Hero"
    elif current_streak >= 7:
        title = "Consistency King"
    elif total >= 20:
        title = "Getting Dangerous"
    elif total >= 10:
        title = "Warming Up"
    else:
        title = "Fresh Recruit"

    # Public profile bits: name, avatar, platform profile links.
    profile = get_profile(user_id)

    # Last two solves with time and rating (rows are already solved_at desc).
    recent_solves = [
        {
            "title": r.get("title") or r.get("url"),
            "url": r.get("url"),
            "platform": r.get("platform"),
            "difficulty": (r.get("difficulty") or "").strip().lower() or None,
            "self_rating": r.get("self_rating"),
            "time_taken": r.get("time_taken"),
            "solved_at": r.get("solved_at"),
        }
        for r in all_rows[:2]
    ]

    return {
        "display_name": profile.get("display_name"),
        "avatar_url": profile.get("avatar_url"),
        "platform_links": profile.get("platform_links") or {},
        "recent_solves": recent_solves,
        "total_solved": total,
        "by_difficulty": by_difficulty,
        "by_platform": by_platform,
        "avg_rating": avg_rating,
        "total_time_hours": total_time_hours,
        "total_reviews": total_reviews,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "patterns_mastered": mastered,
        "patterns_started": started,
        "total_categories": total_categories,
        "top_patterns": top_patterns,
        "title": title,
    }


# --- Access control: profiles, admin role, feature flags ---


def ensure_user_profile(user_id: str, email: str | None = None) -> dict:
    """Insert the user's profile row on first sight, refreshing the cached email.

    Never flips is_admin — that is managed explicitly via set_user_admin. Returns
    the stored profile ({user_id, email, is_admin})."""
    client = get_client()
    existing = (
        client.table("user_profiles")
        .select("user_id, email, is_admin")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        # Backfill/refresh the cached email if we learned it from the token.
        if email and row.get("email") != email:
            client.table("user_profiles").update(
                {"email": email}
            ).eq("user_id", user_id).execute()
            row["email"] = email
        return row
    # Race-safe insert: ON CONFLICT DO NOTHING so two concurrent first-logins
    # can't 500, and an existing is_admin is never clobbered back to false.
    row = {"user_id": user_id, "email": email, "is_admin": False}
    client.table("user_profiles").upsert(
        row, on_conflict="user_id", ignore_duplicates=True
    ).execute()
    return row


PROFILE_COLUMNS = "user_id, email, display_name, avatar_url, platform_links, timezone"
# Pre-migration-007 column set, so a fresh deploy still serves profiles.
LEGACY_PROFILE_COLUMNS = "user_id, email, display_name, avatar_url, platform_links"


def get_profile(user_id: str) -> dict:
    """The user's public-facing profile fields (plus cached email).

    Degrades gracefully if columns don't exist yet: retries without the
    timezone column (pre-007), and falls back to an empty profile if even
    the 006 columns are missing, so pages that embed profile data keep
    working."""
    client = get_client()
    for cols in (PROFILE_COLUMNS, LEGACY_PROFILE_COLUMNS):
        try:
            result = (
                client.table("user_profiles")
                .select(cols)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if result.data:
                row = result.data[0]
                row["platform_links"] = row.get("platform_links") or {}
                row["timezone"] = row.get("timezone") or DEFAULT_TIMEZONE
                return row
            break  # query worked, user simply has no row yet
        except Exception as e:
            print(f"[profile] read failed (run migrations 006/007?): {e}")
    return {
        "user_id": user_id,
        "email": None,
        "display_name": None,
        "avatar_url": None,
        "platform_links": {},
        "timezone": DEFAULT_TIMEZONE,
    }


def update_profile(user_id: str, fields: dict) -> dict:
    client = get_client()
    client.table("user_profiles").upsert(
        {"user_id": user_id, **fields}, on_conflict="user_id"
    ).execute()
    return get_profile(user_id)


def upload_avatar(user_id: str, content: bytes, content_type: str, ext: str) -> str:
    """Store the avatar in the public 'avatars' bucket and return its URL.

    The path is stable per user (overwritten on re-upload); a version query
    param busts browser caches."""
    client = get_client()
    path = f"{user_id}/avatar.{ext}"
    client.storage.from_("avatars").upload(
        path, content, {"content-type": content_type, "upsert": "true"}
    )
    url = client.storage.from_("avatars").get_public_url(path).rstrip("?")
    return f"{url}?v={int(datetime.utcnow().timestamp())}"


def is_user_admin(user_id: str) -> bool:
    client = get_client()
    result = (
        client.table("user_profiles")
        .select("is_admin")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(result.data and result.data[0].get("is_admin"))


def set_user_admin(user_id: str, is_admin: bool) -> None:
    client = get_client()
    client.table("user_profiles").upsert(
        {"user_id": user_id, "is_admin": is_admin},
        on_conflict="user_id",
    ).execute()


def get_user_features(user_id: str) -> list[str]:
    """Return the list of feature names granted to this user."""
    client = get_client()
    result = (
        client.table("feature_access")
        .select("feature")
        .eq("user_id", user_id)
        .execute()
    )
    return [r["feature"] for r in (result.data or [])]


def grant_feature(user_id: str, feature: str) -> None:
    client = get_client()
    client.table("feature_access").upsert(
        {"user_id": user_id, "feature": feature},
        on_conflict="user_id,feature",
    ).execute()


def revoke_feature(user_id: str, feature: str) -> None:
    client = get_client()
    (
        client.table("feature_access")
        .delete()
        .eq("user_id", user_id)
        .eq("feature", feature)
        .execute()
    )


def find_user_by_email(email: str) -> dict | None:
    """Look up an auth user by email (case-insensitive) via the admin API.

    Returns {user_id, email} or None if nobody has signed up with that email."""
    target = email.strip().lower()
    for u in _iter_auth_users():
        if (u.get("email") or "").strip().lower() == target:
            return {"user_id": u["id"], "email": u.get("email")}
    return None


def _iter_auth_users():
    """Yield every auth user as a plain dict, paging through the admin API."""
    client = get_client()
    page = 1
    while True:
        resp = client.auth.admin.list_users(page=page, per_page=200)
        # The supabase client returns either a list or an object with .users
        users = resp if isinstance(resp, list) else getattr(resp, "users", []) or []
        if not users:
            break
        for u in users:
            last = (
                getattr(u, "last_sign_in_at", None)
                if not isinstance(u, dict)
                else u.get("last_sign_in_at")
            )
            # The admin API returns a datetime; normalize to an ISO string so it
            # sorts consistently (never str-vs-datetime) and JSON-serializes.
            if hasattr(last, "isoformat"):
                last = last.isoformat()
            yield {
                "id": str(getattr(u, "id", None) or u["id"]),
                "email": getattr(u, "email", None) if not isinstance(u, dict) else u.get("email"),
                "last_sign_in_at": last,
            }
        if len(users) < 200:
            break
        page += 1


def list_all_users() -> list[dict]:
    """List every auth user with their admin flag and granted features.

    Used by the admin panel. Joins the Supabase auth user list with
    user_profiles (is_admin) and feature_access (features)."""
    client = get_client()
    profiles = {
        p["user_id"]: p
        for p in (
            client.table("user_profiles")
            .select("user_id, is_admin")
            .execute()
            .data
            or []
        )
    }
    features_by_user: dict[str, list[str]] = defaultdict(list)
    for row in (
        client.table("feature_access").select("user_id, feature").execute().data or []
    ):
        features_by_user[row["user_id"]].append(row["feature"])

    users = []
    for u in _iter_auth_users():
        uid = u["id"]
        users.append(
            {
                "user_id": uid,
                "email": u.get("email"),
                "last_sign_in_at": u.get("last_sign_in_at"),
                "is_admin": bool(profiles.get(uid, {}).get("is_admin")),
                "features": sorted(features_by_user.get(uid, [])),
            }
        )
    # Signed-in-most-recently first, unknown last.
    users.sort(key=lambda x: (x["last_sign_in_at"] or ""), reverse=True)
    return users


def get_auth_email(user_id: str) -> str | None:
    """Resolve a user's email from the auth admin API. None on any failure."""
    try:
        resp = get_client().auth.admin.get_user_by_id(user_id)
        user = getattr(resp, "user", resp)
        return getattr(user, "email", None)
    except Exception:
        return None


def log_access_event(
    actor_id: str,
    actor_email: str | None,
    target_id: str,
    action: str,
    feature: str | None = None,
    target_email: str | None = None,
) -> None:
    """Append an access-control change to the audit log. Best-effort: an audit
    failure must never break the actual grant/revoke it records."""
    try:
        get_client().table("access_audit").insert(
            {
                "actor_id": actor_id,
                "actor_email": actor_email,
                "target_id": target_id,
                "target_email": target_email or get_auth_email(target_id),
                "action": action,
                "feature": feature,
            }
        ).execute()
    except Exception as e:  # audit must never break the operation
        print(f"[audit] failed to log {action} by {actor_email}: {e}")


def get_recent_audit(limit: int = 50) -> list[dict]:
    """Most-recent access-control changes, newest first. Returns [] if the audit
    table isn't present yet (e.g. code deployed before migration 004)."""
    try:
        result = (
            get_client()
            .table("access_audit")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[audit] read failed: {e}")
        return []


def get_user_activity(user_id: str) -> dict:
    """Admin view: a compact activity summary for one user, in a single fetch.

    Totals + difficulty/platform breakdown + due-today + avg rating + when they
    were last active + their most recent solves."""
    rows = get_all_questions(user_id)
    by_difficulty: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    ratings: list[int] = []
    today = date.today().isoformat()
    due_today = 0
    last_active = None

    for r in rows:
        diff = _norm_difficulty(r.get("difficulty"))
        by_difficulty[diff] = by_difficulty.get(diff, 0) + 1
        plat = r.get("platform") or "unknown"
        by_platform[plat] = by_platform.get(plat, 0) + 1
        if r.get("self_rating"):
            ratings.append(r["self_rating"])
        if r.get("next_review") and r["next_review"] <= today:
            due_today += 1
        for ts in (r.get("solved_at"), r.get("last_reviewed")):
            if ts and (last_active is None or ts > last_active):
                last_active = ts

    recent = sorted(
        (r for r in rows if r.get("solved_at")),
        key=lambda r: r["solved_at"],
        reverse=True,
    )[:5]

    return {
        "total": len(rows),
        "by_difficulty": by_difficulty,
        "by_platform": by_platform,
        "due_today": due_today,
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
        "last_active": last_active,
        "recent": [
            {
                "title": r.get("title") or r.get("url"),
                "url": r.get("url"),
                "difficulty": r.get("difficulty"),
                "platform": r.get("platform"),
                "solved_at": r.get("solved_at"),
                "self_rating": r.get("self_rating"),
            }
            for r in recent
        ],
    }
