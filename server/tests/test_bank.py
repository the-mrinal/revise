"""GET /api/patterns/bank, and the Research page reading the same problem data."""

import json
import re

from fastapi.testclient import TestClient

import main as main_module
from patterns import PATTERNS, PROBLEMS, PROBLEMS_DATA


def anon():
    """A client with no sign-in (no auth override, no Authorization header)."""
    main_module.app.dependency_overrides.clear()
    return TestClient(main_module.app)


def test_bank_needs_no_sign_in_and_has_the_whole_sheet():
    r = anon().get("/api/patterns/bank")
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert len(groups) == 15
    assert sum(len(g["patterns"]) for g in groups) == 94


def test_every_problem_has_a_title_and_a_difficulty():
    groups = anon().get("/api/patterns/bank").json()["groups"]
    problems = [p for g in groups for pat in g["patterns"] for p in pat["problems"]]
    assert len({p["number"] for p in problems}) == 413
    for p in problems:
        assert set(p) == {"number", "title", "slug", "difficulty"}
        assert p["title"], p
        assert p["slug"], p
        assert p["difficulty"] in {"Easy", "Medium", "Hard"}, p


def test_bank_follows_the_sheet_order():
    groups = anon().get("/api/patterns/bank").json()["groups"]
    assert [g["name"] for g in groups] == list(PATTERNS)
    first = groups[0]["patterns"][0]
    assert first["name"] == "Converging"
    assert first["problems"][0] == {
        "number": 11,
        "title": "Container With Most Water",
        "slug": "container-with-most-water",
        "difficulty": "Medium",
    }


def test_problems_file_has_its_fetch_date():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PROBLEMS_DATA["fetched_on"])


def test_research_page_embeds_the_same_problem_data():
    r = anon().get("/research")
    assert r.status_code == 200
    assert "/*PROBLEMS_JSON*/" not in r.text
    m = re.search(r"const PROBLEMS = (\{.*?\});\n", r.text)
    assert m, "the page has no problem data"
    embedded = json.loads(m.group(1))
    assert {int(k): v for k, v in embedded.items()} == PROBLEMS
