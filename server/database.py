"""Database queries for Revise (Postgres via psycopg; see db.py).

Every function returns rows shaped like the Supabase REST API returned them,
so callers and the browser code are unaffected by the move off Supabase.
"""

import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import db

# IANA zone used when a user hasn't picked one (or before migration 007).
DEFAULT_TIMEZONE = "Asia/Kolkata"

# Where avatar images live; served at /avatars/... by main.py.
AVATAR_DIR = os.environ.get("AVATAR_DIR", "/data/avatars")

COLUMNS = (
    'id, user_id, url, title, platform, difficulty, self_rating, time_taken, '
    'notes, solved_at, easiness_factor, "interval", repetitions, next_review, '
    'last_reviewed, attempts, pattern, question_type, '
    'approach, mistakes, time_complexity, space_complexity, '
    'stability, fsrs_difficulty, fsrs_state, solution_source'
)


EVENT_COLUMNS = (
    'id, question_id, event_type, self_rating, time_taken, "interval", '
    'repetitions, easiness_factor, next_review, reconstructed, created_at, '
    'solution_source, stability, fsrs_difficulty, fsrs_state'
)

# Fields an event may carry beyond the always-present user/question/type.
_EVENT_FIELDS = (
    "self_rating", "time_taken", "interval", "repetitions",
    "easiness_factor", "next_review", "reconstructed", "created_at",
    "solution_source", "stability", "fsrs_difficulty", "fsrs_state",
)


# --- Users ---

# (database target, user_id) pairs already known to exist in users, so the
# per-request ensure_user check costs one query per user per process.
_known_users: set[tuple[str | None, str]] = set()


def ensure_user(user_id: str, email: str | None = None) -> None:
    """Make sure the users row exists for an authenticated user.

    Accounts created through Supabase Auth after the users table was filled
    arrive here on their first API call, before anything that references
    users(id) is written."""
    key = (db.current_pool_target(), user_id)
    if key in _known_users:
        return
    db.execute(
        "INSERT INTO users (id, email) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email "
        "WHERE EXCLUDED.email IS NOT NULL AND users.email IS DISTINCT FROM EXCLUDED.email",
        (user_id, email),
    )
    _known_users.add(key)


def record_sign_in(user_id: str, email: str | None = None) -> None:
    """Note a completed sign-in (shown and sorted on in the admin panel)."""
    db.execute(
        "INSERT INTO users (id, email, last_sign_in_at) VALUES (%s, %s, now()) "
        "ON CONFLICT (id) DO UPDATE SET last_sign_in_at = now(), "
        "email = COALESCE(EXCLUDED.email, users.email)",
        (user_id, email),
    )


# --- Questions and their event log ---


def insert_event(user_id: str, question_id: int, event_type: str, **fields) -> None:
    """Append a row to the per-question audit log. Best-effort: never raises."""
    try:
        row = {"user_id": user_id, "question_id": question_id, "event_type": event_type}
        for key in _EVENT_FIELDS:
            if fields.get(key) is not None:
                row[key] = fields[key]
        db.insert("question_events", row, returning="id")
    except Exception as e:  # logging must never break a save
        print(f"[events] failed to log {event_type} for q{question_id}: {e}")


def get_question_events(user_id: str, qid: int) -> list[dict]:
    return db.fetch_all(
        f"SELECT {EVENT_COLUMNS} FROM question_events "
        "WHERE user_id = %s AND question_id = %s ORDER BY created_at ASC",
        (user_id, qid),
    )


def insert_question(user_id: str, data: dict) -> dict:
    return db.insert("questions", {**data, "user_id": user_id})


def get_all_questions(user_id: str) -> list[dict]:
    rows = db.fetch_all(
        f"SELECT {COLUMNS} FROM questions WHERE user_id = %s ORDER BY solved_at DESC",
        (user_id,),
    )
    import sunday  # imports this module, so not at the top

    if rows and sunday.enabled():
        _add_sunday_placement(user_id, rows)
    return rows


def _add_sunday_placement(user_id: str, rows: list[dict]) -> None:
    """For an account connected to Sunday only, each item gains `sunday`:
    {placed: week|own|skipped, week, module} as Sunday answered its newest
    save; {placed: "sending"} for a save since connecting that Sunday hasn't
    answered yet; {placed: "before"} when nothing was saved since connecting.
    An account not connected gets its items exactly as before."""
    found = db.fetch_all(
        "SELECT q.id, p.placed, p.week, p.module, EXISTS ("
        "  SELECT 1 FROM question_events e WHERE e.user_id = q.user_id"
        "  AND e.question_id = q.id AND e.self_rating IS NOT NULL"
        "  AND e.created_at >= l.connected_at) AS since_connecting "
        "FROM questions q JOIN sunday_links l ON l.user_id = q.user_id "
        "LEFT JOIN sunday_placements p ON p.user_id = q.user_id AND p.question_id = q.id "
        "WHERE q.user_id = %s",
        (user_id,),
    )
    by_id = {}
    for f in found:
        if f["placed"]:
            by_id[f["id"]] = {"placed": f["placed"], "week": f["week"], "module": f["module"]}
        else:
            by_id[f["id"]] = {"placed": "sending" if f["since_connecting"] else "before"}
    if not by_id:  # not connected
        return
    for row in rows:
        row["sunday"] = by_id.get(row["id"], {"placed": "before"})


def get_question(user_id: str, qid: int) -> dict | None:
    return db.fetch_one(
        f"SELECT {COLUMNS} FROM questions WHERE user_id = %s AND id = %s",
        (user_id, qid),
    )


def update_question_schedule(
    user_id: str,
    qid: int,
    data: dict,
    set_reviewed: bool = False,
    solution_source: str | None = None,
):
    """Write the FSRS schedule fields computed by scheduler.py.

    interval is still written (derived days) for event rows and the history
    UI; easiness_factor/repetitions stay frozen at their pre-FSRS values.
    """
    update_data = {
        "stability": data["stability"],
        "fsrs_difficulty": data["fsrs_difficulty"],
        "fsrs_state": data["fsrs_state"],
        "interval": data["interval"],
        "next_review": data["next_review"],
    }
    if solution_source:
        update_data["solution_source"] = solution_source
    if set_reviewed:
        update_data["last_reviewed"] = datetime.utcnow().isoformat()
    db.update("questions", update_data, {"user_id": user_id, "id": qid}, returning="id")


def get_revisions_due(
    user_id: str, target_date: str | None = None, limit: int | None = None
) -> list[dict]:
    target = target_date or date.today().isoformat()
    query = (
        f"SELECT {COLUMNS} FROM questions WHERE user_id = %s AND next_review <= %s "
        # Cards due the same day come oldest first. (Supabase returned them in
        # whatever order Postgres stored them, which changed with every review.)
        "ORDER BY next_review ASC, id ASC"
    )
    params: tuple = (user_id, target)
    # limit None or <= 0 means "no cap" — surface every due revision.
    if limit and limit > 0:
        query += " LIMIT %s"
        params += (limit,)
    return db.fetch_all(query, params)


def _first_solve_days(user_id: str, qids) -> dict[int, str]:
    """Earliest 'created'/'reviewed' event day (UTC, YYYY-MM-DD) per question."""
    events = db.fetch_all(
        "SELECT question_id, created_at FROM question_events "
        "WHERE user_id = %s AND question_id = ANY(%s) "
        "AND event_type IN ('created', 'reviewed') ORDER BY created_at ASC",
        (user_id, list(qids)),
    )
    first_day: dict[int, str] = {}
    for e in events:
        qid = e["question_id"]
        day = (e.get("created_at") or "")[:10]
        if not day:
            continue
        if qid not in first_day or day < first_day[qid]:
            first_day[qid] = day
    return first_day


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
    reviewed = db.fetch_all(
        "SELECT question_id FROM question_events "
        "WHERE user_id = %s AND event_type = 'reviewed' AND created_at >= %s",
        (user_id, f"{today}T00:00:00"),
    )
    qids = {r["question_id"] for r in reviewed}
    if not qids:
        return 0
    # Find each candidate question's first-ever solve day from the event log.
    first_day = _first_solve_days(user_id, qids)
    # Only count questions first solved before today (genuine revisions).
    return sum(1 for qid in qids if first_day.get(qid, today) < today)


def update_question(user_id: str, qid: int, data: dict) -> dict | None:
    rows = db.update("questions", data, {"user_id": user_id, "id": qid})
    return rows[0] if rows else None


def delete_question(user_id: str, qid: int) -> bool:
    return len(db.delete("questions", {"user_id": user_id, "id": qid})) > 0


def get_today_activity(user_id: str) -> list[dict]:
    today = date.today().isoformat()
    since = f"{today}T00:00:00"
    # Rows where solved_at or last_reviewed is today
    rows_data = db.fetch_all(
        f"SELECT {COLUMNS} FROM questions "
        "WHERE user_id = %s AND (solved_at >= %s OR last_reviewed >= %s)",
        (user_id, since, since),
    )

    # A question is NEW today only if today is its first-ever solve session.
    # We decide from the audit log: the earliest 'created'/'reviewed' event.
    # The extension logs a 'reviewed' event even on the first solve, so the
    # timestamp alone is unreliable — the event log is the source of truth.
    qids = [r["id"] for r in rows_data]
    first_event_date = _first_solve_days(user_id, qids) if qids else {}

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
    events = db.fetch_all(
        "SELECT question_id, created_at FROM question_events "
        "WHERE user_id = %s AND event_type IN ('created', 'reviewed') "
        "ORDER BY created_at ASC",
        (user_id,),
    )

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
    """The user's IANA timezone, defaulting to IST."""
    try:
        row = db.fetch_one("SELECT timezone FROM user_profiles WHERE user_id = %s", (user_id,))
        if row and row.get("timezone"):
            return row["timezone"]
    except Exception as e:
        print(f"[profile] timezone read failed: {e}")
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


def _aggregate_heatmap_days(
    pairs: set[tuple[int, str]],
    first_day: dict[int, str],
    difficulty_by_qid: dict[int, str | None],
) -> dict[str, dict]:
    """Distinct (question, local-day) pairs -> per-day detail:

        {"YYYY-MM-DD": {"total": 3, "new": 1, "revised": 2,
                        "difficulty": {"easy": 1, "medium": 2}}}

    A question counts as new on its first-ever solve day and revised on any
    later day. Questions with no difficulty set land in the "unknown" bucket;
    only nonzero buckets are emitted. Pure so the bucketing rules are testable
    without a database.
    """
    out: dict[str, dict] = {}
    for qid, day in pairs:
        entry = out.setdefault(day, {"total": 0, "new": 0, "revised": 0, "difficulty": {}})
        entry["total"] += 1
        # An unknown first day means the question's origin predates the event
        # log and its row is gone (deleted); its earliest sighting acts as new.
        entry["new" if first_day.get(qid, day) == day else "revised"] += 1
        diff = difficulty_by_qid.get(qid) or "unknown"
        entry["difficulty"][diff] = entry["difficulty"].get(diff, 0) + 1
    return out


def get_activity_heatmap(user_id: str, days: int = 371) -> dict[str, dict]:
    """Per-day activity detail for the last ~53 weeks, bucketed into the
    user's profile timezone. Each day counts the distinct questions touched,
    split into new solves vs revisions and by difficulty (see
    _aggregate_heatmap_days for the shape).

    'attempted' events are excluded — they're timer-start artifacts and would
    double-count against the 'created'/'reviewed' rows written on finish.
    """
    tz_name = get_user_timezone(user_id)
    zone = _safe_zone(tz_name)
    # One extra day of slack so a UTC cutoff can't clip events that fall
    # inside the window once shifted into a UTC+N zone.
    cutoff = (date.today() - timedelta(days=days + 1)).isoformat()
    pairs: set[tuple[int, str]] = set()  # distinct (question_id, local day)
    for r in db.fetch_all(
        "SELECT question_id, created_at FROM question_events "
        "WHERE user_id = %s AND event_type IN ('created', 'reviewed') AND created_at >= %s "
        "ORDER BY created_at ASC",
        (user_id, cutoff),
    ):
        day = _to_local_day(r.get("created_at"), zone)
        if day and r.get("question_id") is not None:
            pairs.add((r["question_id"], day))
    if not pairs:
        return {}

    # First-ever solve day and difficulty per involved question. The 'created'
    # event is written once at first solve and may predate the window, so it's
    # fetched by question id, not by date; rows older than the event log fall
    # back to solved_at.
    qids = sorted({qid for qid, _ in pairs})
    first_day: dict[int, str] = {}
    difficulty_by_qid: dict[int, str | None] = {}
    solved_at_by_qid: dict[int, str | None] = {}
    for r in db.fetch_all(
        "SELECT question_id, created_at FROM question_events "
        "WHERE user_id = %s AND event_type = 'created' AND question_id = ANY(%s)",
        (user_id, qids),
    ):
        day = _to_local_day(r.get("created_at"), zone)
        qid = r["question_id"]
        if day and (qid not in first_day or day < first_day[qid]):
            first_day[qid] = day
    for r in db.fetch_all(
        "SELECT id, difficulty, solved_at FROM questions WHERE user_id = %s AND id = ANY(%s)",
        (user_id, qids),
    ):
        difficulty_by_qid[r["id"]] = r.get("difficulty")
        solved_at_by_qid[r["id"]] = r.get("solved_at")
    for qid in qids:
        if qid not in first_day:
            day = _to_local_day(solved_at_by_qid.get(qid), zone)
            if day:
                first_day[qid] = day

    return _aggregate_heatmap_days(pairs, first_day, difficulty_by_qid)


def find_by_url(user_id: str, url: str) -> dict | None:
    return db.fetch_one(
        f"SELECT {COLUMNS} FROM questions WHERE user_id = %s AND url = %s ORDER BY id LIMIT 1",
        (user_id, url),
    )


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
        db.execute(
            "DELETE FROM question_events WHERE id = ("
            " SELECT id FROM question_events"
            " WHERE user_id = %s AND question_id = %s AND event_type = 'attempted'"
            " ORDER BY created_at DESC, id DESC LIMIT 1)",
            (user_id, qid),
        )
    except Exception as e:
        print(f"[events] failed to delete attempted event for q{qid}: {e}")


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip query params, fragments, sub-paths.

    One problem, one address, whichever of its pages was open:
    - LeetCode: /problems/<slug>/ (drops /description/, /submissions/ ...).
    - CSES: a task's Submit, Statistics and Hacking tabs become
      /problemset/task/<N>/ (a save from the Submit page kept "submit/N").
      Not result/<N>: that number is a submission, not a task.
    - Codeforces: contest/<N>/problem/<X> becomes problemset/problem/<N>/<X>,
      with <X> in capitals, as Sunday matches it.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "leetcode.com":
        m = re.match(r"(/problems/[^/]+)", path)
        if m:
            path = m.group(1)
    elif host == "cses.fi":
        m = re.search(r"/(?:task|submit|stats|hack)/(\d+)$", path)
        if m:
            path = f"/problemset/task/{m.group(1)}"
    elif host == "codeforces.com":
        m = re.match(r"/(?:contest/(\d+)/problem|problemset/problem/(\d+))/(\w+)$", path)
        if m:
            path = f"/problemset/problem/{m.group(1) or m.group(2)}/{m.group(3).upper()}"
    return urlunparse((parsed.scheme, parsed.netloc, path + "/", "", "", ""))


def _merge_url_group(url: str, rows: list[dict]):
    """Merge a group of duplicate rows sharing the same normalized URL."""
    if len(rows) < 2:
        return
    # Keep the most-worked row (repetitions froze when FSRS replaced SM-2)
    rows.sort(key=lambda r: (r.get("attempts") or 0, r.get("solved_at") or ""), reverse=True)
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
    db.update("questions", update_data, {"id": keep["id"]}, returning="id")
    for other in others:
        db.delete("questions", {"id": other["id"]})


def merge_duplicates(user_id: str):
    """Consolidate duplicate URL entries for a user."""
    all_rows = get_all_questions(user_id)
    by_url: dict[str, list[dict]] = {}
    for row in all_rows:
        key = _normalize_url(row["url"])
        by_url.setdefault(key, []).append(row)

    for url, rows in by_url.items():
        _merge_url_group(url, rows)


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
    _merge_url_group(norm_url, dupes)
    # Return the surviving ID (most attempts, same key as _merge_url_group)
    dupes.sort(key=lambda r: (r.get("attempts") or 0, r.get("solved_at") or ""), reverse=True)
    return dupes[0]["id"]


DEFAULT_REVISION_QUEUE_SIZE = 20
DEFAULT_DESIRED_RETENTION = 0.9

_SETTINGS_DEFAULTS = {
    "revision_queue_size": DEFAULT_REVISION_QUEUE_SIZE,
    "desired_retention": DEFAULT_DESIRED_RETENTION,
    "fsrs_params": None,
}


def get_user_settings(user_id: str) -> dict:
    """Return the user's settings, falling back to defaults if none are stored."""
    stored = db.fetch_one(
        "SELECT revision_queue_size, desired_retention, fsrs_params "
        "FROM user_settings WHERE user_id = %s",
        (user_id,),
    )
    if stored:
        return {
            "revision_queue_size": stored.get("revision_queue_size", DEFAULT_REVISION_QUEUE_SIZE),
            "desired_retention": stored.get("desired_retention") or DEFAULT_DESIRED_RETENTION,
            "fsrs_params": stored.get("fsrs_params"),
        }
    return dict(_SETTINGS_DEFAULTS)


def upsert_user_settings(user_id: str, data: dict) -> dict:
    """Insert or update the user's settings row and return the stored values."""
    row = {"user_id": user_id, **data}
    stored = db.upsert("user_settings", row, conflict=("user_id",)) or row
    return {
        "revision_queue_size": stored.get("revision_queue_size"),
        "desired_retention": stored.get("desired_retention", DEFAULT_DESIRED_RETENTION),
        "fsrs_params": stored.get("fsrs_params"),
    }


def get_user_platforms(user_id: str) -> list[dict]:
    return db.fetch_all(
        "SELECT id, user_id, name, url_pattern, created_at FROM user_platforms "
        "WHERE user_id = %s ORDER BY created_at ASC",
        (user_id,),
    )


def insert_user_platform(user_id: str, data: dict) -> dict:
    row = {"user_id": user_id, "name": data["name"], "url_pattern": data["url_pattern"]}
    return db.insert("user_platforms", row)


def delete_user_platform(user_id: str, platform_id: int) -> bool:
    return len(db.delete("user_platforms", {"user_id": user_id, "id": platform_id})) > 0


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
    row = db.fetch_one(
        "SELECT user_id, email, is_admin FROM user_profiles WHERE user_id = %s", (user_id,)
    )
    if row:
        # Backfill/refresh the cached email if we learned it from the token.
        if email and row.get("email") != email:
            db.update("user_profiles", {"email": email}, {"user_id": user_id}, returning="user_id")
            row["email"] = email
        return row
    # Race-safe insert: ON CONFLICT DO NOTHING so two concurrent first-logins
    # can't 500, and an existing is_admin is never clobbered back to false.
    row = {"user_id": user_id, "email": email, "is_admin": False}
    db.upsert("user_profiles", row, conflict=("user_id",), ignore_duplicates=True)
    return row


PROFILE_COLUMNS = "user_id, email, display_name, avatar_url, platform_links, timezone"


def get_profile(user_id: str) -> dict:
    """The user's public-facing profile fields (plus cached email). Users with
    no profile row yet get an empty profile."""
    row = db.fetch_one(
        f"SELECT {PROFILE_COLUMNS} FROM user_profiles WHERE user_id = %s", (user_id,)
    )
    if row:
        row["platform_links"] = row.get("platform_links") or {}
        row["timezone"] = row.get("timezone") or DEFAULT_TIMEZONE
        return row
    return {
        "user_id": user_id,
        "email": None,
        "display_name": None,
        "avatar_url": None,
        "platform_links": {},
        "timezone": DEFAULT_TIMEZONE,
    }


def update_profile(user_id: str, fields: dict) -> dict:
    db.upsert("user_profiles", {"user_id": user_id, **fields}, conflict=("user_id",))
    return get_profile(user_id)


def upload_avatar(user_id: str, content: bytes, content_type: str, ext: str) -> str:
    """Save the avatar under AVATAR_DIR and return the URL it's served at.

    The path is stable per user (overwritten on re-upload, other formats
    removed); a version query param busts browser caches."""
    folder = os.path.join(AVATAR_DIR, user_id)
    os.makedirs(folder, exist_ok=True)
    name = f"avatar.{ext}"
    tmp = os.path.join(folder, f".{name}.tmp")
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, os.path.join(folder, name))
    for other in os.listdir(folder):
        if other.startswith("avatar.") and other != name:
            os.remove(os.path.join(folder, other))
    return f"/avatars/{user_id}/{name}?v={int(datetime.utcnow().timestamp())}"


def is_user_admin(user_id: str) -> bool:
    row = db.fetch_one("SELECT is_admin FROM user_profiles WHERE user_id = %s", (user_id,))
    return bool(row and row.get("is_admin"))


def set_user_admin(user_id: str, is_admin: bool) -> None:
    db.upsert("user_profiles", {"user_id": user_id, "is_admin": is_admin}, conflict=("user_id",))


def get_user_features(user_id: str) -> list[str]:
    """Return the list of feature names granted to this user."""
    rows = db.fetch_all("SELECT feature FROM feature_access WHERE user_id = %s", (user_id,))
    return [r["feature"] for r in rows]


def grant_feature(user_id: str, feature: str) -> None:
    db.upsert(
        "feature_access",
        {"user_id": user_id, "feature": feature},
        conflict=("user_id", "feature"),
    )


def revoke_feature(user_id: str, feature: str) -> None:
    db.delete("feature_access", {"user_id": user_id, "feature": feature})


def find_user_by_email(email: str) -> dict | None:
    """Look up a user by email (case-insensitive).

    Returns {user_id, email} or None if nobody has signed up with that email."""
    row = db.fetch_one(
        "SELECT id, email FROM users WHERE lower(email) = lower(%s) ORDER BY created_at LIMIT 1",
        (email.strip(),),
    )
    return {"user_id": row["id"], "email": row["email"]} if row else None


def list_all_users() -> list[dict]:
    """List every user with their admin flag and granted features, most
    recently signed in first. Used by the admin panel."""
    users = db.fetch_all(
        "SELECT u.id AS user_id, u.email, u.last_sign_in_at, "
        "COALESCE(p.is_admin, false) AS is_admin, "
        "COALESCE(ARRAY(SELECT f.feature FROM feature_access f "
        "WHERE f.user_id = u.id ORDER BY f.feature), '{}') AS features "
        "FROM users u LEFT JOIN user_profiles p ON p.user_id = u.id"
    )
    # Signed-in-most-recently first, unknown last.
    users.sort(key=lambda x: (x["last_sign_in_at"] or ""), reverse=True)
    return users


def get_auth_email(user_id: str) -> str | None:
    """A user's email from the users table. None on any failure."""
    try:
        row = db.fetch_one("SELECT email FROM users WHERE id = %s", (user_id,))
        return row["email"] if row else None
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
        db.insert(
            "access_audit",
            {
                "actor_id": actor_id,
                "actor_email": actor_email,
                "target_id": target_id,
                "target_email": target_email or get_auth_email(target_id),
                "action": action,
                "feature": feature,
            },
            returning="id",
        )
    except Exception as e:  # audit must never break the operation
        print(f"[audit] failed to log {action} by {actor_email}: {e}")


def get_recent_audit(limit: int = 50) -> list[dict]:
    """Most-recent access-control changes, newest first."""
    try:
        return db.fetch_all(
            "SELECT * FROM access_audit ORDER BY created_at DESC LIMIT %s", (limit,)
        )
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


# --- Sign-in: Revise sessions, linked GitHub accounts, bridged Supabase tokens ---


def get_user(user_id: str) -> dict | None:
    return db.fetch_one("SELECT id, email, created_at, source FROM users WHERE id = %s", (user_id,))


def create_user(email: str | None, source: str) -> str:
    """A brand-new account (e.g. first GitHub sign-in). Returns its id."""
    return db.fetch_one(
        "INSERT INTO users (id, email, source) VALUES (gen_random_uuid(), %s, %s) RETURNING id",
        (email, source),
    )["id"]


def insert_session(session_hash: str, user_id: str, expires_at: str, user_agent: str | None) -> None:
    db.insert(
        "sessions",
        {"id": session_hash, "user_id": user_id, "expires_at": expires_at,
         "user_agent": (user_agent or "")[:300] or None},
        returning="id",
    )


def use_session(session_hash: str, new_expiry: str) -> dict | None:
    """Look up a live session and slide its expiry. None if unknown, revoked
    or expired."""
    return db.fetch_one(
        "UPDATE sessions SET last_used_at = now(), expires_at = %s "
        "WHERE id = %s AND revoked_at IS NULL AND expires_at > now() "
        "RETURNING user_id",
        (new_expiry, session_hash),
    )


def revoke_session(session_hash: str) -> None:
    db.execute(
        "UPDATE sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
        (session_hash,),
    )


def use_legacy_token(token_hash: str) -> dict | None:
    """A Supabase refresh token we've already exchanged (or imported)."""
    return db.fetch_one(
        "UPDATE legacy_refresh_tokens SET last_used_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL RETURNING user_id",
        (token_hash,),
    )


def remember_legacy_token(token_hash: str, user_id: str, source: str) -> None:
    db.execute(
        "INSERT INTO legacy_refresh_tokens (token_hash, user_id, source, last_used_at) "
        "VALUES (%s, %s, %s, now()) ON CONFLICT (token_hash) DO NOTHING",
        (token_hash, user_id, source),
    )


def revoke_legacy_token(token_hash: str) -> None:
    db.execute(
        "UPDATE legacy_refresh_tokens SET revoked_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL",
        (token_hash,),
    )


def get_identity_user(provider: str, provider_user_id: str) -> str | None:
    row = db.fetch_one(
        "SELECT user_id FROM user_identities WHERE provider = %s AND provider_user_id = %s",
        (provider, provider_user_id),
    )
    return row["user_id"] if row else None


def get_user_identity(user_id: str, provider: str) -> dict | None:
    return db.fetch_one(
        "SELECT provider_user_id, login, email, linked_at FROM user_identities "
        "WHERE user_id = %s AND provider = %s",
        (user_id, provider),
    )


def link_identity(user_id: str, provider: str, provider_user_id: str,
                  login: str | None, email: str | None) -> None:
    db.execute(
        "INSERT INTO user_identities (user_id, provider, provider_user_id, login, email) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, provider, provider_user_id, login, email),
    )


def refresh_identity(provider: str, provider_user_id: str, login: str | None, email: str | None) -> None:
    """Usernames and emails change on GitHub; the numeric id doesn't."""
    db.execute(
        "UPDATE user_identities SET login = %s, email = COALESCE(%s, email) "
        "WHERE provider = %s AND provider_user_id = %s",
        (login, email, provider, provider_user_id),
    )


def users_matching_emails_without(provider: str, emails: list[str]) -> list[str]:
    """Ids of users whose email is one of `emails` and who have no identity
    from `provider` linked yet."""
    if not emails:
        return []
    rows = db.fetch_all(
        "SELECT u.id FROM users u WHERE lower(u.email) = ANY(%s) AND NOT EXISTS ("
        " SELECT 1 FROM user_identities i WHERE i.user_id = u.id AND i.provider = %s)",
        ([e.strip().lower() for e in emails], provider),
    )
    return [r["id"] for r in rows]


def set_avatar_if_missing(user_id: str, avatar_url: str) -> None:
    db.execute(
        "INSERT INTO user_profiles (user_id, avatar_url) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET avatar_url = EXCLUDED.avatar_url "
        "WHERE user_profiles.avatar_url IS NULL",
        (user_id, avatar_url),
    )


def merge_users(from_id: str, into_id: str) -> dict:
    """Move everything one account owns into another, then delete the first.

    For someone who ended up with two accounts (say, a new one from signing
    in with GitHub under a different email). Settings, profile and platforms
    already set on the surviving account win; the rest moves over. All in
    one transaction."""
    if from_id == into_id:
        raise ValueError("Can't merge an account into itself")
    moved = {}
    with db.get_pool().connection() as conn, conn.transaction():
        for table in ("questions", "question_events", "sessions", "legacy_refresh_tokens"):
            cur = conn.execute(
                f"UPDATE {table} SET user_id = %s WHERE user_id = %s", (into_id, from_id)
            )
            moved[table] = cur.rowcount
        moved["user_platforms"] = conn.execute(
            "UPDATE user_platforms f SET user_id = %s WHERE f.user_id = %s AND NOT EXISTS ("
            " SELECT 1 FROM user_platforms t WHERE t.user_id = %s AND t.name = f.name)",
            (into_id, from_id, into_id),
        ).rowcount
        moved["feature_access"] = conn.execute(
            "INSERT INTO feature_access (user_id, feature) SELECT %s, feature FROM feature_access "
            "WHERE user_id = %s ON CONFLICT (user_id, feature) DO NOTHING",
            (into_id, from_id),
        ).rowcount
        for table in ("user_settings", "user_profiles"):
            moved[table] = conn.execute(
                f"UPDATE {table} SET user_id = %s WHERE user_id = %s AND NOT EXISTS ("
                f" SELECT 1 FROM {table} WHERE user_id = %s)",
                (into_id, from_id, into_id),
            ).rowcount
        # An identity moves only if the surviving account has none from that provider.
        moved["user_identities"] = conn.execute(
            "UPDATE user_identities f SET user_id = %s WHERE f.user_id = %s AND NOT EXISTS ("
            " SELECT 1 FROM user_identities t WHERE t.user_id = %s AND t.provider = f.provider)",
            (into_id, from_id, into_id),
        ).rowcount
        # Whatever didn't move (duplicates) goes with the old account.
        if conn.execute("DELETE FROM users WHERE id = %s", (from_id,)).rowcount != 1:
            raise ValueError("The account to merge from doesn't exist")
    _known_users.clear()
    return moved


def use_sign_in_link(jti: str, user_id: str) -> bool:
    """Record a one-time sign-in link as used. False if it already was."""
    return db.execute(
        "INSERT INTO used_sign_in_links (jti, user_id) VALUES (%s, %s) ON CONFLICT (jti) DO NOTHING",
        (jti, user_id),
    ) == 1
