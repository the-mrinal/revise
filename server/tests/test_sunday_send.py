"""Sending saves to Sunday (sunday_outbox and its sender) and asking Where."""

import httpx
import pytest
from fastapi.testclient import TestClient

import database
import db
import main as main_module
import sunday
from test_auth import U1, U2
from test_sunday_link import SECRET, SUNDAY, TOKEN, bearer, link_row

URL = "https://leetcode.com/problems/two-sum/"


@pytest.fixture
def app(local_db, monkeypatch):
    monkeypatch.setenv("SUNDAY_URL", SUNDAY)
    monkeypatch.setenv("REVISE_SUNDAY_SECRET", SECRET)
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    sunday.save_link(U1, TOKEN, "ananya")
    return TestClient(main_module.app)


class FakeSunday:
    """Stands in for Sunday's server: records calls, answers from a list."""

    def __init__(self, monkeypatch):
        self.saves, self.wheres, self.answers = [], [], []
        monkeypatch.setattr(sunday.httpx, "post", self.post)
        monkeypatch.setattr(sunday.httpx, "get", self.get)

    def _answer(self):
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post(self, url, json=None, headers=None, timeout=None):
        assert url == f"{SUNDAY}/api/revise/saves"
        self.saves.append({"body": json, "auth": headers["Authorization"]})
        status = self._answer()
        return httpx.Response(status, json={"placed": "week", "week": 2, "module": 1})

    def get(self, url, params=None, headers=None, timeout=None):
        assert url == f"{SUNDAY}/api/revise/where"
        self.wheres.append({"url": params["url"], "auth": headers["Authorization"]})
        status = self._answer()
        return httpx.Response(status, json={"in_week": True, "week": 2, "module": 1, "group": "Arrays"})


@pytest.fixture
def fake(monkeypatch):
    return FakeSunday(monkeypatch)


def outbox(user_id=U1):
    return db.fetch_all("SELECT * FROM sunday_outbox WHERE user_id = %s ORDER BY id", (user_id,))


def events(qid):
    return db.fetch_all(
        "SELECT id FROM question_events WHERE question_id = %s AND self_rating IS NOT NULL ORDER BY id",
        (qid,),
    )


def start_timer(app, user=U1):
    """What the extension does when the timer starts: no rating yet."""
    r = app.post("/api/questions", json={"url": URL, "title": "Two Sum"}, headers=bearer(user))
    assert r.status_code == 200
    return r.json()["id"]


def finish(app, qid, user=U1):
    """What the extension does on Save: review, then the details."""
    h = bearer(user)
    r = app.post(f"/api/questions/{qid}/review", json={"self_rating": 4, "solution_source": "hint"}, headers=h)
    assert r.status_code == 200
    r = app.put(f"/api/questions/{qid}", headers=h, json={
        "self_rating": 4, "time_taken": 23, "solution_source": "hint",
        "approach": "hash map of seen values", "mistakes": "forgot the same index",
        "notes": "check the complement first",
    })
    assert r.status_code == 200


def test_a_timer_start_sends_nothing(app, fake):
    start_timer(app)
    assert outbox() == []


def test_one_save_per_event(app, fake):
    h = bearer(U1)
    r = app.post("/api/questions", headers=h, json={
        "url": URL, "title": "Two Sum", "difficulty": "Easy", "self_rating": 3, "time_taken": 15,
    })
    qid = r.json()["id"]
    app.post(f"/api/questions/{qid}/review", json={"self_rating": 5}, headers=h)

    ev = events(qid)
    assert len(ev) == 2
    assert [row["ref"] for row in outbox()] == [f"{qid}:{ev[0]['id']}", f"{qid}:{ev[1]['id']}"]

    assert sunday.send_pending() == 2
    assert outbox() == []
    first, second = (s["body"] for s in fake.saves)
    assert first["ref"] == f"{qid}:{ev[0]['id']}" and second["ref"] == f"{qid}:{ev[1]['id']}"
    assert first["url"] == URL and first["title"] == "Two Sum" and first["platform"] == "leetcode"
    assert first["difficulty"] == "easy" and first["pattern"]
    assert first["minutes"] == 15 and first["stars"] == 3 and first["how"] == "self"
    assert second["stars"] == 5 and second["minutes"] is None
    assert first["own_pick"] is False and first["at"] and first["due_on"]
    assert all(s["auth"] == f"Bearer {TOKEN}" for s in fake.saves)


def test_an_edit_resends_the_same_ref_with_the_three_lines(app, fake):
    qid = start_timer(app)
    app.post(f"/api/questions/{qid}/review", json={"self_rating": 4, "solution_source": "hint"},
             headers=bearer(U1))
    assert sunday.send_pending() == 1
    ref = fake.saves[0]["body"]["ref"]

    app.put(f"/api/questions/{qid}", headers=bearer(U1), json={
        "time_taken": 23, "approach": "hash map of seen values",
        "mistakes": "forgot the same index", "notes": "check the complement first",
    })
    assert sunday.send_pending() == 1
    body = fake.saves[1]["body"]
    assert body["ref"] == ref
    assert body["idea"] == "hash map of seen values"
    assert body["missed"] == "forgot the same index"
    assert body["lesson"] == "check the complement first"
    assert body["minutes"] == 23 and body["how"] == "hint" and body["stars"] == 4
    question = database.get_question(U1, qid)
    assert body["due_on"] == question["next_review"]


def test_an_edit_before_sending_replaces_the_waiting_save(app, fake):
    qid = start_timer(app)
    finish(app, qid)
    rows = outbox()
    assert len(rows) == 1 and rows[0]["body"]["idea"] == "hash map of seen values"
    assert sunday.send_pending() == 1 and len(fake.saves) == 1


def test_an_edit_of_a_question_saved_before_connecting_sends_nothing(app, fake):
    sunday.remove_link_for_user(U1)
    qid = start_timer(app)
    app.post(f"/api/questions/{qid}/review", json={"self_rating": 4}, headers=bearer(U1))
    sunday.save_link(U1, TOKEN, "ananya")
    app.put(f"/api/questions/{qid}", json={"notes": "later"}, headers=bearer(U1))
    assert outbox() == []


def test_a_save_is_retried_after_sunday_was_down(app, fake):
    qid = start_timer(app)
    finish(app, qid)
    fake.answers = [httpx.ConnectError("refused")]
    assert sunday.send_pending() == 0
    row = outbox()[0]
    assert row["tries"] == 1 and "ConnectError" in row["last_error"]

    # Not tried again before its wait is over.
    assert sunday.send_pending() == 0 and len(fake.saves) == 1

    db.execute("UPDATE sunday_outbox SET next_try_at = now()")
    fake.answers = [503]
    assert sunday.send_pending() == 0
    assert outbox()[0]["tries"] == 2

    db.execute("UPDATE sunday_outbox SET next_try_at = now()")
    assert sunday.send_pending() == 1
    assert outbox() == []
    assert fake.saves[0]["body"] == fake.saves[2]["body"]


def test_the_wait_grows_with_each_try(app):
    qid = start_timer(app)
    finish(app, qid)
    row = outbox()[0]
    waits = []
    for _ in range(3):
        sunday._try_later(row, "down")
        row = outbox()[0]
        waits.append(db.fetch_one(
            "SELECT extract(epoch FROM next_try_at - now())::int AS s FROM sunday_outbox")["s"])
    assert waits[0] < waits[1] < waits[2]


def test_a_401_removes_the_link(app, fake):
    qid = start_timer(app)
    finish(app, qid)
    app.post(f"/api/questions/{qid}/review", json={"self_rating": 5}, headers=bearer(U1))
    assert len(outbox()) == 2
    fake.answers = [401]
    assert sunday.send_pending() == 0
    assert link_row(U1) is None
    assert outbox() == []
    assert app.get("/api/me", headers=bearer(U1)).json()["sunday"] is None

    finish(app, qid)
    assert outbox() == []


def test_nothing_is_queued_for_a_person_not_linked(app, fake):
    qid = start_timer(app, U2)
    finish(app, qid, U2)
    app.post("/api/questions", headers=bearer(U2),
             json={"url": "https://leetcode.com/problems/3sum/", "self_rating": 3})
    assert db.fetch_all("SELECT * FROM sunday_outbox") == []
    assert sunday.send_pending() == 0 and fake.saves == []


def test_nothing_is_queued_or_sent_without_sunday_set_up(app, fake, monkeypatch):
    qid = start_timer(app)
    finish(app, qid)
    monkeypatch.delenv("SUNDAY_URL")
    qid2 = app.post("/api/questions", headers=bearer(U1),
                    json={"url": "https://leetcode.com/problems/3sum/", "self_rating": 3}).json()["id"]
    assert all(not row["ref"].startswith(f"{qid2}:") for row in outbox())
    assert sunday.send_pending() == 0 and fake.saves == []
    assert sunday.start_sender() is None


def test_the_sender_thread_sends_a_new_save_straight_away(app, fake):
    thread = sunday.start_sender()
    try:
        qid = start_timer(app)
        finish(app, qid)
        for _ in range(50):
            if fake.saves and not outbox():
                break
            thread.join(0.1)
        assert len(fake.saves) >= 1 and outbox() == []
    finally:
        sunday.stop_sender()
        thread.join(5)


# --- Where ------------------------------------------------------------------------------


def test_where_asks_sunday_with_the_token(app, fake):
    r = app.get("/api/sunday/where", params={"url": URL}, headers=bearer(U1))
    assert r.status_code == 200
    assert r.json() == {"in_week": True, "week": 2, "module": 1, "group": "Arrays"}
    assert fake.wheres == [{"url": URL, "auth": f"Bearer {TOKEN}"}]


def test_where_for_a_person_not_linked(app, fake):
    r = app.get("/api/sunday/where", params={"url": URL}, headers=bearer(U2))
    assert r.json() == {"linked": False}
    assert fake.wheres == []


def test_where_401_removes_the_link(app, fake):
    fake.answers = [401]
    r = app.get("/api/sunday/where", params={"url": URL}, headers=bearer(U1))
    assert r.json() == {"linked": False}
    assert link_row(U1) is None


def test_where_when_sunday_is_down(app, fake):
    fake.answers = [httpx.ConnectError("refused")]
    r = app.get("/api/sunday/where", params={"url": URL}, headers=bearer(U1))
    assert r.status_code == 502


def test_where_is_not_there_without_sunday_set_up(app, fake, monkeypatch):
    monkeypatch.delenv("REVISE_SUNDAY_SECRET")
    r = app.get("/api/sunday/where", params={"url": URL}, headers=bearer(U1))
    assert r.status_code == 404
