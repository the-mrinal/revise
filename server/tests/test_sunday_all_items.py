"""All items' Sunday column: where Sunday put each save (sunday_placements),
the `sunday` field in the item list, its labels, and the CSV export."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import database
import db
import main as main_module
import sunday
from test_auth import U1, U2
from test_sunday_link import SECRET, SUNDAY, TOKEN, bearer

DASHBOARD = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


@pytest.fixture
def app(local_db, monkeypatch):
    monkeypatch.setenv("SUNDAY_URL", SUNDAY)
    monkeypatch.setenv("REVISE_SUNDAY_SECRET", SECRET)
    database.ensure_user(U1, "one@example.com")
    database.ensure_user(U2, "two@example.com")
    sunday.save_link(U1, TOKEN, "ananya")
    return TestClient(main_module.app)


@pytest.fixture
def answers(monkeypatch):
    """Sunday's answers to saves, in order; each is a dict or a raw string."""
    queue = []

    def post(url, json=None, headers=None, timeout=None):
        answer = queue.pop(0)
        if isinstance(answer, str):
            return httpx.Response(200, text=answer)
        return httpx.Response(200, json=answer)

    monkeypatch.setattr(sunday.httpx, "post", post)
    return queue


def save(app, slug, user=U1, rating=4):
    r = app.post("/api/questions", headers=bearer(user), json={
        "url": f"https://leetcode.com/problems/{slug}/", "title": slug, "self_rating": rating,
    })
    assert r.status_code == 200
    return r.json()["id"]


def placements(user_id=U1):
    return db.fetch_all(
        "SELECT question_id, placed, week, module FROM sunday_placements WHERE user_id = %s "
        "ORDER BY question_id", (user_id,))


def items(app, user=U1):
    r = app.get("/api/questions", headers=bearer(user))
    assert r.status_code == 200
    return {q["id"]: q for q in r.json()}


# --- keeping Sunday's answer -------------------------------------------------------------


def test_sundays_answer_is_kept_and_the_newest_wins(app, answers):
    qid = save(app, "two-sum")
    answers.append({"placed": "week", "week": 4, "module": 2})
    assert sunday.send_pending() == 1
    assert placements() == [{"question_id": qid, "placed": "week", "week": 4, "module": 2}]

    app.post(f"/api/questions/{qid}/review", json={"self_rating": 5}, headers=bearer(U1))
    answers.append({"placed": "own", "week": 5, "module": None})
    assert sunday.send_pending() == 1
    assert placements() == [{"question_id": qid, "placed": "own", "week": 5, "module": None}]


@pytest.mark.parametrize("reply", ["not json", {"ok": True}, {"placed": "somewhere", "week": 1}])
def test_a_reply_that_doesnt_read_is_ignored_and_the_save_still_counts(app, answers, reply):
    save(app, "two-sum")
    answers.append(reply)
    assert sunday.send_pending() == 1
    assert db.fetch_all("SELECT * FROM sunday_outbox") == []
    assert placements() == []


def test_disconnecting_from_revise_removes_the_placements(app, answers):
    save(app, "two-sum")
    answers.append({"placed": "week", "week": 1, "module": 1})
    sunday.send_pending()
    assert placements()
    sunday.remove_link_for_user(U1)
    assert placements() == []


def test_disconnecting_from_sunday_removes_the_placements(app, answers):
    save(app, "two-sum")
    answers.append({"placed": "week", "week": 1, "module": 1})
    sunday.send_pending()
    assert sunday.remove_link_by_token(TOKEN) is True
    assert placements() == []


def test_a_401_from_sunday_removes_the_placements(app, answers, monkeypatch):
    save(app, "two-sum")
    answers.append({"placed": "week", "week": 1, "module": 1})
    sunday.send_pending()
    save(app, "3sum")
    monkeypatch.setattr(sunday.httpx, "post", lambda *a, **k: httpx.Response(401))
    sunday.send_pending()
    assert placements() == []


# --- the field in the item list ----------------------------------------------------------


def test_each_item_says_where_it_went(app, answers):
    sunday.remove_link_for_user(U1)
    before = save(app, "two-sum")
    sunday.save_link(U1, TOKEN, "ananya")
    in_week = save(app, "longest-substring-without-repeating-characters")
    own = save(app, "subarray-product-less-than-k")
    skipped = save(app, "3sum")
    answers.extend([
        {"placed": "week", "week": 4, "module": 2},
        {"placed": "own", "week": 4},
        {"placed": "skipped", "week": None, "module": None},
    ])
    assert sunday.send_pending() == 3
    sending = save(app, "valid-anagram")  # queued, Sunday hasn't answered yet

    got = {qid: q["sunday"] for qid, q in items(app).items()}
    assert got == {
        before: {"placed": "before"},
        in_week: {"placed": "week", "week": 4, "module": 2},
        own: {"placed": "own", "week": 4, "module": None},
        skipped: {"placed": "skipped", "week": None, "module": None},
        sending: {"placed": "sending"},
    }


def test_a_timer_started_since_connecting_is_still_saved_before_connecting(app):
    """Only a rated save goes to Sunday, so a timer start alone isn't sending."""
    sunday.remove_link_for_user(U1)
    qid = save(app, "two-sum")
    sunday.save_link(U1, TOKEN, "ananya")
    app.post("/api/questions", headers=bearer(U1),
             json={"url": "https://leetcode.com/problems/two-sum/", "title": "two-sum"})
    assert items(app)[qid]["sunday"] == {"placed": "before"}


def test_nothing_changes_for_a_person_not_linked(app):
    save(app, "two-sum", user=U2)
    save(app, "3sum", user=U2)
    got = app.get("/api/questions", headers=bearer(U2)).json()
    raw = db.fetch_all(
        f"SELECT {database.COLUMNS} FROM questions WHERE user_id = %s ORDER BY solved_at DESC", (U2,))
    assert all("sunday" not in q for q in got)
    assert [set(q) for q in got] == [set(r) for r in raw]


def test_nothing_changes_after_disconnecting(app, answers):
    save(app, "two-sum")
    answers.append({"placed": "week", "week": 1, "module": 1})
    sunday.send_pending()
    sunday.remove_link_for_user(U1)
    assert all("sunday" not in q for q in items(app).values())


def test_nothing_changes_without_sunday_set_up(app, monkeypatch):
    save(app, "two-sum")
    monkeypatch.delenv("SUNDAY_URL")
    assert all("sunday" not in q for q in items(app).values())


# --- the page: labels, the column, the CSV export ------------------------------------------

NODE = shutil.which("node")


def _function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


def run_page(questions: list[dict]) -> dict:
    """Run the dashboard's own renderTable and exportCSV on these items,
    with a small stand-in for the DOM; returns the table and the CSV."""
    src = DASHBOARD.read_text()
    code = "\n".join(_function(src, n) for n in ("renderTable", "sundayPlace", "exportCSV", "esc"))
    script = """
const els = {};
function el(id) { return els[id] || (els[id] = {id, value: '', hidden: false, innerHTML: '', textContent: ''}); }
let csvOut = null;
globalThis.document = {
  getElementById: el,
  createElement(tag) {
    if (tag === 'div') {
      return { set textContent(v) { this.innerHTML = String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); } };
    }
    return { click() {} };
  },
};
globalThis.Blob = class { constructor(parts) { csvOut = parts.join(''); } };
globalThis.URL = { createObjectURL: () => 'blob:' };
function historyBtn() { return ''; }
let sortKey = 'solved_at', sortDir = -1;
let allQuestions = %s;
%s
renderTable();
exportCSV();
console.log(JSON.stringify({table: el('tableBody').innerHTML, col_hidden: el('sundayCol').hidden, csv: csvOut}));
""" % (json.dumps(questions), code)
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


QUESTIONS = [
    {"id": 1, "title": "Two Sum", "url": "https://leetcode.com/problems/two-sum/", "platform": "leetcode",
     "difficulty": "easy", "self_rating": 4, "solution_source": "self", "attempts": 1, "time_taken": 12,
     "notes": 'say "hi", then go', "solved_at": "2026-09-20T10:00:00+00:00", "next_review": "2026-10-02"},
    {"id": 2, "title": "3Sum", "url": "https://leetcode.com/problems/3sum/", "platform": "leetcode",
     "difficulty": "medium", "self_rating": 3, "solution_source": "hint", "attempts": 2, "time_taken": None,
     "notes": None, "solved_at": "2026-09-21T10:00:00+00:00", "next_review": "2026-09-28"},
]

EXPECTED_CSV = "\n".join([
    "title,url,platform,difficulty,self_rating,solution_source,attempts,time_taken,notes,solved_at,next_review",
    '"Two Sum","https://leetcode.com/problems/two-sum/","leetcode","easy","4","self","1","12",'
    '"say ""hi"", then go","2026-09-20T10:00:00+00:00","2026-10-02"',
    '"3Sum","https://leetcode.com/problems/3sum/","leetcode","medium","3","hint","2","","",'
    '"2026-09-21T10:00:00+00:00","2026-09-28"',
])


@pytest.mark.skipif(not NODE, reason="needs node to run the page's own script")
def test_the_column_and_csv_for_a_person_not_linked():
    page = run_page(QUESTIONS)
    assert page["col_hidden"] is True
    assert "Sunday" not in page["table"] and "before connecting" not in page["table"]
    assert page["table"].count("<td") == 2 * 10
    assert page["csv"] == EXPECTED_CSV


@pytest.mark.skipif(not NODE, reason="needs node to run the page's own script")
@pytest.mark.parametrize("placement, label", [
    ({"placed": "week", "week": 4, "module": 2}, "week 4 › 2"),
    ({"placed": "own", "week": 4, "module": None}, "own pick · week 4"),
    ({"placed": "skipped", "week": None, "module": None}, "not added to Sunday"),
    ({"placed": "sending"}, "sending…"),
    ({"placed": "before"}, "saved before connecting"),
])
def test_the_column_and_csv_for_a_linked_person(placement, label):
    linked = [{**q, "sunday": placement} for q in QUESTIONS]
    page = run_page(linked)
    assert page["col_hidden"] is False
    assert page["table"].count("<td") == 2 * 11
    cells = re.findall(r"<td>(.*?)</td>", page["table"], re.S)
    assert sum(label in c for c in cells) == 2
    assert page["csv"] == EXPECTED_CSV
