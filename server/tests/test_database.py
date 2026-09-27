"""database.py against a real Postgres (see conftest: TEST_DATABASE_URL)."""

import json
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

import database
import db
import migrate
from conftest import make_supabase_shaped

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def users(local_db):
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    return U1, U2


def _question(**overrides):
    return {
        "url": "https://leetcode.com/problems/two-sum/",
        "title": "Two Sum",
        "platform": "leetcode",
        "difficulty": "easy",
        "self_rating": 4,
        "time_taken": 12,
        **overrides,
    }


# --- value shaping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "dt, expected",
    [
        (datetime(2026, 3, 8, 21, 40, 5, 506179, timezone.utc), "2026-03-08T21:40:05.506179+00:00"),
        (datetime(2026, 3, 8, 21, 40, 5, 500000, timezone.utc), "2026-03-08T21:40:05.5+00:00"),
        (datetime(2026, 3, 8, 21, 40, 5, 0, timezone.utc), "2026-03-08T21:40:05+00:00"),
        (datetime(2026, 3, 8, 21, 40, 5, 120, timezone.utc), "2026-03-08T21:40:05.00012+00:00"),
    ],
)
def test_timestamps_render_like_postgres_json(dt, expected):
    assert db._iso_timestamp(dt) == expected


def test_timestamps_match_postgres_own_json_rendering(local_db):
    """The shaping must equal what Supabase's REST API (Postgres to_json) returned."""
    rows = db.fetch_all(
        "SELECT ts, to_json(ts)#>>'{}' AS pg_json FROM (VALUES "
        "('2026-03-08 21:40:05.506179+00'::timestamptz), ('2026-03-08 21:40:05.5+00'), "
        "('2026-03-08 21:40:05+00'), ('2026-01-01 00:00:00.000001+05:30')) v(ts)"
    )
    for r in rows:
        assert r["ts"] == r["pg_json"]


def test_dates_uuids_and_jsonb_are_api_shaped(users):
    q = database.insert_question(U1, _question(next_review="2026-10-01"))
    assert q["next_review"] == "2026-10-01"
    assert q["user_id"] == U1
    assert isinstance(q["id"], int)
    database.upsert_user_settings(U1, {"fsrs_params": {"w": [0.4, 1.2]}})
    assert database.get_user_settings(U1)["fsrs_params"] == {"w": [0.4, 1.2]}


# --- migrations ------------------------------------------------------------------


def test_migrations_are_idempotent(local_db):
    assert migrate.migrate(local_db, "local") == []


def test_migrations_on_supabase_shaped_db_only_add_users(make_database):
    url = make_database()
    make_supabase_shaped(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email, last_sign_in_at) VALUES "
            "(%s, 'one@example.com', '2026-09-01T10:00:00Z'), (%s, 'two@example.com', NULL)",
            (U1, U2),
        )
        conn.execute("INSERT INTO questions (user_id, url) VALUES (%s, 'https://x.test/')", (U1,))
    applied = migrate.migrate(url, "supabase")
    assert applied == ["000_baseline.sql", "001_users_from_supabase_auth.sql"]
    with psycopg.connect(url) as conn:
        users = conn.execute("SELECT id::text, email FROM users ORDER BY email").fetchall()
        assert users == [(U1, "one@example.com"), (U2, "two@example.com")]
        # Existing tables keep their Supabase foreign keys and their rows.
        fk = conn.execute(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE conrelid = 'public.questions'::regclass AND contype = 'f'"
        ).fetchone()[0]
        assert fk == "auth.users"
        assert conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 1


# --- users -----------------------------------------------------------------------


def test_ensure_user_creates_and_refreshes_email(local_db):
    database.ensure_user(U1, "old@example.com")
    database._known_users.clear()
    database.ensure_user(U1, "new@example.com")
    database._known_users.clear()
    database.ensure_user(U1, None)  # a token without email never erases it
    assert database.get_auth_email(U1) == "new@example.com"
    assert database.find_user_by_email("NEW@example.com ") == {"user_id": U1, "email": "new@example.com"}
    assert database.find_user_by_email("nobody@example.com") is None


def test_writes_for_unknown_user_are_rejected(local_db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        database.insert_question(U1, _question())


def test_record_sign_in(local_db):
    database.record_sign_in(U1, "one@example.com")
    [u] = database.list_all_users()
    assert u["user_id"] == U1 and u["last_sign_in_at"]


# --- questions -------------------------------------------------------------------


def test_question_crud(users):
    q = database.insert_question(U1, _question())
    assert database.get_question(U1, q["id"])["title"] == "Two Sum"
    assert database.get_question(U2, q["id"]) is None  # scoped to its owner
    updated = database.update_question(U1, q["id"], {"notes": "hash map"})
    assert updated["notes"] == "hash map"
    assert database.update_question(U2, q["id"], {"notes": "x"}) is None
    assert database.find_by_url(U1, q["url"])["id"] == q["id"]
    assert database.increment_attempts(U1, q["id"], title="Two Sum!")["attempts"] == 2
    assert database.decrement_attempts(U1, q["id"])["attempts"] == 1
    assert database.decrement_attempts(U1, q["id"])["attempts"] == 1  # never below 1
    assert database.delete_question(U2, q["id"]) is False
    assert database.delete_question(U1, q["id"]) is True
    assert database.get_question(U1, q["id"]) is None


def test_defaults_match_supabase(users):
    q = database.insert_question(U1, {"url": "https://x.test/p"})
    assert (q["easiness_factor"], q["interval"], q["repetitions"], q["attempts"], q["question_type"]) == (
        2.5, 1, 0, 1, "dsa",
    )
    assert q["solved_at"]


def test_get_all_questions_newest_first(users):
    a = database.insert_question(U1, _question(url="https://x.test/a", solved_at="2026-09-01T10:00:00Z"))
    b = database.insert_question(U1, _question(url="https://x.test/b", solved_at="2026-09-02T10:00:00Z"))
    database.insert_question(U2, _question(url="https://x.test/c"))
    assert [r["id"] for r in database.get_all_questions(U1)] == [b["id"], a["id"]]


def test_more_than_1000_questions_all_returned(users):
    """Supabase's REST API stopped at 1,000 rows; SQL has no such cap."""
    with db.get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO questions (user_id, url) SELECT %s, 'https://x.test/' || g "
            "FROM generate_series(1, 1205) g",
            (U1,),
        )
    assert len(database.get_all_questions(U1)) == 1205


def test_schedule_and_revisions_due(users):
    q1 = database.insert_question(U1, _question(url="https://x.test/1", next_review="2026-09-01"))
    q2 = database.insert_question(U1, _question(url="https://x.test/2", next_review="2026-09-03"))
    database.insert_question(U1, _question(url="https://x.test/3", next_review="2026-12-01"))
    due = database.get_revisions_due(U1, "2026-09-05")
    assert [r["id"] for r in due] == [q1["id"], q2["id"]]
    assert len(database.get_revisions_due(U1, "2026-09-05", limit=1)) == 1
    assert len(database.get_revisions_due(U1, "2026-09-05", limit=0)) == 2

    database.update_question_schedule(
        U1, q1["id"],
        {"stability": 3.2, "fsrs_difficulty": 5.1, "fsrs_state": 2, "interval": 4, "next_review": "2026-09-09"},
        set_reviewed=True, solution_source="hint",
    )
    r = database.get_question(U1, q1["id"])
    assert (r["stability"], r["fsrs_state"], r["interval"], r["next_review"], r["solution_source"]) == (
        3.2, 2, 4, "2026-09-09", "hint",
    )
    assert r["last_reviewed"]


def test_events_log_and_rollback(users):
    q = database.insert_question(U1, _question())
    database.insert_event(U1, q["id"], "created", self_rating=4, time_taken=12)
    database.insert_event(U1, q["id"], "attempted")
    database.insert_event(U1, q["id"], "attempted")
    events = database.get_question_events(U1, q["id"])
    assert [e["event_type"] for e in events] == ["created", "attempted", "attempted"]
    assert events[0]["self_rating"] == 4 and events[0]["reconstructed"] is False
    database.delete_latest_attempt_event(U1, q["id"])
    assert [e["event_type"] for e in database.get_question_events(U1, q["id"])] == ["created", "attempted"]
    assert database.get_question_events(U2, q["id"]) == []


def test_insert_event_never_raises(users):
    database.insert_event(U1, 999999, "created")  # unknown question: logged, not raised


def test_today_activity_and_revision_counts(users):
    today = date.today()
    yesterday = (today - timedelta(days=3)).isoformat()
    new_q = database.insert_question(U1, _question(url="https://x.test/new"))
    old_q = database.insert_question(
        U1, _question(url="https://x.test/old", solved_at=f"{yesterday}T08:00:00Z")
    )
    database.insert_event(U1, new_q["id"], "created")
    database.insert_event(U1, old_q["id"], "created", created_at=f"{yesterday}T08:00:00Z")
    database.insert_event(U1, old_q["id"], "reviewed")
    database.update_question(U1, old_q["id"], {"last_reviewed": datetime.utcnow().isoformat()})

    activity = {r["id"]: r["activity_type"] for r in database.get_today_activity(U1)}
    assert activity == {new_q["id"]: "NEW", old_q["id"]: "REVISION"}
    assert database.count_revisions_done_today(U1) == 1
    summary = database.get_questions_activity_summary(U1)
    assert summary[old_q["id"]]["revision_count"] == 1
    assert summary[new_q["id"]]["revision_count"] == 0


def test_heatmap(users):
    q = database.insert_question(U1, _question(difficulty="medium"))
    database.insert_event(U1, q["id"], "created", created_at="2026-01-10T06:00:00Z")
    database.insert_event(U1, q["id"], "reviewed", created_at="2026-01-12T06:00:00Z")
    database.insert_event(U1, q["id"], "attempted", created_at="2026-01-13T06:00:00Z")
    heat = database.get_activity_heatmap(U1, days=(date.today() - date(2026, 1, 1)).days)
    assert heat["2026-01-10"] == {"total": 1, "new": 1, "revised": 0, "difficulty": {"medium": 1}}
    assert heat["2026-01-12"]["revised"] == 1
    assert "2026-01-13" not in heat  # attempted events don't count
    assert database.get_activity_heatmap(U2) == {}


def test_merge_duplicates(users):
    a = database.insert_question(U1, _question(url="https://leetcode.com/problems/two-sum/description/", attempts=3))
    database.insert_question(U1, _question(url="https://leetcode.com/problems/two-sum/?tab=x", attempts=1))
    survivor = database.merge_duplicates_for_question(U1, a["id"])
    assert survivor == a["id"]
    [row] = database.get_all_questions(U1)
    assert row["url"] == "https://leetcode.com/problems/two-sum/" and row["attempts"] == 2


# --- settings, platforms, stats ------------------------------------------------------


def test_settings_defaults_and_partial_upsert(users):
    assert database.get_user_settings(U1) == {
        "revision_queue_size": 20, "desired_retention": 0.9, "fsrs_params": None,
    }
    database.upsert_user_settings(U1, {"revision_queue_size": 5})
    stored = database.upsert_user_settings(U1, {"desired_retention": 0.85})
    # Updating one setting leaves the other alone, as Supabase's upsert did.
    assert stored["revision_queue_size"] == 5 and stored["desired_retention"] == 0.85


def test_platforms(users):
    p = database.insert_user_platform(U1, {"name": "Brilliant", "url_pattern": "brilliant.org"})
    assert [x["name"] for x in database.get_user_platforms(U1)] == ["Brilliant"]
    assert database.delete_user_platform(U2, p["id"]) is False
    assert database.delete_user_platform(U1, p["id"]) is True


def test_stats_and_flex(users):
    database.insert_question(U1, _question(url="https://x.test/1", difficulty="Easy"))
    database.insert_question(U1, _question(url="https://x.test/2", difficulty="hard", self_rating=2))
    stats = database.get_stats(U1)
    assert stats["total"] == 2 and stats["by_difficulty"] == {"easy": 1, "hard": 1}
    flex = database.get_flex_stats(U1)
    assert flex["total_solved"] == 2 and flex["recent_solves"]
    assert database.get_flex_stats(U2) == {"total_solved": 0}
    assert database.get_user_activity(U1)["total"] == 2


# --- profiles, avatars, access control -----------------------------------------------


def test_profiles(users):
    assert database.get_profile(U1)["timezone"] == "Asia/Kolkata"
    database.ensure_user_profile(U1, "one@example.com")
    database.set_user_admin(U1, True)
    database.ensure_user_profile(U1, "one+new@example.com")  # never resets is_admin
    assert database.is_user_admin(U1) is True
    p = database.update_profile(U1, {"display_name": "One", "platform_links": {"leetcode": "https://l.test/u"}})
    assert p["display_name"] == "One" and p["platform_links"] == {"leetcode": "https://l.test/u"}
    assert p["email"] == "one+new@example.com"
    assert database.get_user_timezone(U1) == "Asia/Kolkata"


def test_avatar_upload_replaces_old_format(users, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "AVATAR_DIR", str(tmp_path))
    database.upload_avatar(U1, b"png", "image/png", "png")
    url = database.upload_avatar(U1, b"jpg", "image/jpeg", "jpg")
    assert url.startswith(f"/avatars/{U1}/avatar.jpg?v=")
    assert sorted(p.name for p in (tmp_path / U1).iterdir()) == ["avatar.jpg"]


def test_features_admin_list_and_audit(users):
    database.grant_feature(U1, "research")
    database.grant_feature(U1, "research")  # idempotent
    assert database.get_user_features(U1) == ["research"]
    database.log_access_event(U2, "two@example.com", U1, "grant", feature="research")
    [entry] = database.get_recent_audit()
    assert entry["target_email"] == "one@example.com" and entry["action"] == "grant"
    listing = {u["user_id"]: u for u in database.list_all_users()}
    assert listing[U1]["features"] == ["research"] and listing[U2]["features"] == []
    database.revoke_feature(U1, "research")
    assert database.get_user_features(U1) == []


def test_refuses_to_serve_empty_local_while_supabase_configured(local_db, monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused.invalid/db")
    with pytest.raises(SystemExit, match="Refusing to start"):
        migrate.refuse_empty_local()
    database.ensure_user(U1, "one@example.com")
    migrate.refuse_empty_local()  # has data: fine
    monkeypatch.delenv("SUPABASE_DB_URL")
    migrate.refuse_empty_local()


def test_whole_number_floats_render_like_postgres(users):
    """Supabase's API returned 3 for a float8 of 3.0; so must we."""
    q = database.insert_question(U1, _question())
    database.update_question(U1, q["id"], {"stability": 3.0, "easiness_factor": 2.5})
    row = database.get_question(U1, q["id"])
    assert json.dumps([row["stability"], row["easiness_factor"]]) == "[3, 2.5]"


def test_due_cards_on_the_same_day_come_oldest_first(users):
    ids = [database.insert_question(U1, _question(url=f"https://x.test/{i}", next_review="2026-09-01"))["id"]
           for i in range(3)]
    database.update_question(U1, ids[0], {"notes": "touched"})  # moves the row in storage
    assert [r["id"] for r in database.get_revisions_due(U1, "2026-09-02")] == ids
