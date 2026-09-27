"""The cutover path end to end: Supabase-shaped source -> our Postgres,
with the hold/switch mechanism driven the way the server drives it."""

import asyncio
import json
import threading
import time

import psycopg
import pytest

import copy_to_local
import cutover
import database
import db
import migrate
from conftest import make_supabase_shaped

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def two_databases(make_database, monkeypatch, tmp_path):
    """A populated Supabase-shaped source and an empty migrated destination."""
    src, dst = make_database(), make_database()
    make_supabase_shaped(src)
    with psycopg.connect(src, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email, last_sign_in_at) VALUES "
            "(%s, 'one@example.com', '2026-09-20T10:00:00.5Z'), (%s, 'two@example.com', NULL)",
            (U1, U2),
        )
    migrate.migrate(src, "supabase")
    migrate.migrate(dst, "local")

    monkeypatch.setenv("SUPABASE_DB_URL", src)
    monkeypatch.setenv("DATABASE_URL", dst)
    monkeypatch.setenv("DB_TARGET", "supabase")
    monkeypatch.setattr(cutover, "CONTROL", str(tmp_path / "control.json"))
    monkeypatch.setattr(cutover, "STATUS", str(tmp_path / "status.json"))
    db.close()
    database._known_users.clear()

    # Realistic data written through the app's own code, on the source.
    q = database.insert_question(U1, {"url": "https://leetcode.com/problems/two-sum/", "title": "Two Sum",
                                      "difficulty": "easy", "next_review": "2026-10-01"})
    database.insert_event(U1, q["id"], "created", self_rating=4, created_at="2026-09-01T08:00:00.123456Z")
    database.insert_event(U1, q["id"], "reviewed", stability=2.5)
    database.insert_question(U2, {"url": "https://example.com/lesson", "notes": "unicode ✓ 'quotes' \\ tabs\t"})
    database.upsert_user_settings(U1, {"revision_queue_size": 7, "fsrs_params": {"w": [0.1, 0.2]}})
    database.insert_user_platform(U2, {"name": "Brilliant", "url_pattern": "brilliant.org"})
    database.update_profile(U1, {"display_name": "One", "platform_links": {"leetcode": "https://l.test/u"}})
    database.grant_feature(U1, "research")
    database.log_access_event(U1, "one@example.com", U2, "grant", feature="research")
    # A deleted question leaves a gap in ids; the counter must still carry over.
    gone = database.insert_question(U2, {"url": "https://example.com/deleted"})
    database.delete_question(U2, gone["id"])
    db.close()
    return src, dst


def test_rehearsal_copies_everything_identically(two_databases, capsys):
    copy_to_local.rehearse()
    assert "Rehearsal passed" in capsys.readouterr().out

    _, dst = two_databases
    with psycopg.connect(dst) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 2
        # Foreign keys on our side point at our users table.
        fk = conn.execute(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE conrelid = 'public.questions'::regclass AND contype = 'f'"
        ).fetchone()[0]
        assert fk == "users"

    # New rows continue the id sequence instead of colliding.
    db.switch_target("local")
    q = database.insert_question(U1, {"url": "https://x.test/after"})
    assert q["id"] == 4


def test_rehearsal_can_repeat(two_databases):
    copy_to_local.rehearse()
    copy_to_local.rehearse()


def test_copy_detects_a_difference(two_databases, monkeypatch):
    real = copy_to_local.fingerprint

    def tampered(conn, table):
        count, digest = real(conn, table)
        is_local = conn.info.dbname == psycopg.conninfo.conninfo_to_dict(two_databases[1])["dbname"]
        return (count, "tampered") if table == "questions" and is_local else (count, digest)

    monkeypatch.setattr(copy_to_local, "fingerprint", tampered)
    with pytest.raises(RuntimeError, match="questions"):
        copy_to_local.copy_and_verify()


def _run_watcher(stop: threading.Event):
    while not stop.is_set():
        cutover._watch_once()
        time.sleep(0.05)


def test_run_holds_switches_and_releases(two_databases, capsys):
    db.switch_target("supabase")  # the "server" is serving from Supabase
    stop = threading.Event()
    watcher = threading.Thread(target=_run_watcher, args=(stop,), daemon=True)
    watcher.start()
    try:
        copy_to_local.run()
    finally:
        stop.set()
        watcher.join()
    out = capsys.readouterr().out
    assert "Serving from local Postgres" in out
    assert db.current_pool_target() == "local"
    assert cutover.current_target() == "local"  # survives a restart via control.json
    assert json.load(open(cutover.CONTROL))["hold"] is False
    assert database.get_user_settings(U1)["revision_queue_size"] == 7

    # And once local is live, a copy would destroy data: refused.
    with pytest.raises(SystemExit):
        copy_to_local.copy_and_verify()


def test_failed_verification_releases_on_supabase(two_databases, monkeypatch, capsys):
    db.switch_target("supabase")
    monkeypatch.setattr(copy_to_local, "copy_and_verify", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    stop = threading.Event()
    watcher = threading.Thread(target=_run_watcher, args=(stop,), daemon=True)
    watcher.start()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            copy_to_local.run()
    finally:
        stop.set()
        watcher.join()
    assert "Supabase (unchanged)" in capsys.readouterr().out
    assert db.current_pool_target() == "supabase"
    assert cutover._state["hold"] is False


# --- the request gate --------------------------------------------------------------


class _Req:
    def __init__(self, path):
        self.url = type("U", (), {"path": path})()


def test_gate_holds_requests_until_release(monkeypatch):
    monkeypatch.setitem(cutover._state, "hold", True)
    seen = []

    async def call_next(req):
        seen.append(req.url.path)
        return "ok"

    async def scenario():
        task = asyncio.ensure_future(cutover.gate(_Req("/api/questions"), call_next))
        passthrough = await cutover.gate(_Req("/api/auth/refresh"), call_next)
        await asyncio.sleep(0.3)
        assert seen == ["/api/auth/refresh"] and not task.done()  # held, not failed
        cutover._state["hold"] = False
        assert await task == "ok"
        return passthrough

    assert asyncio.run(scenario()) == "ok"
    assert seen == ["/api/auth/refresh", "/api/questions"]
    assert cutover._state["inflight"] == 0


def test_gate_gives_up_with_503_after_max_wait(monkeypatch):
    monkeypatch.setitem(cutover._state, "hold", True)
    monkeypatch.setattr(cutover, "HOLD_MAX_SECONDS", 0.2)

    async def call_next(req):
        raise AssertionError("must not run")

    resp = asyncio.run(cutover.gate(_Req("/api/stats"), call_next))
    assert resp.status_code == 503


def test_switch_waits_for_inflight_requests(two_databases, monkeypatch):
    db.switch_target("supabase")
    cutover._write_json(cutover.CONTROL, {"hold": True, "target": "local"})
    monkeypatch.setitem(cutover._state, "inflight", 1)
    cutover._watch_once()
    assert db.current_pool_target() == "supabase"  # a request is still running
    cutover._state["inflight"] = 0
    cutover._watch_once()
    assert db.current_pool_target() == "local"
