"""FSRS scheduler tests: rating mapping, solution-source caps, and schedule
behavior. All calls pin `now` and fuzzing is off, so results are deterministic."""

from datetime import datetime, timedelta, timezone

from fsrs import Rating

import scheduler

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)

# A mature card: reviewed recently, 20-day stability.
MATURE = {
    "stability": 20.0,
    "fsrs_difficulty": 5.0,
    "fsrs_state": 2,
    "next_review": "2026-07-09",
    "last_reviewed": "2026-06-19T10:00:00Z",
    "solved_at": "2026-05-01T10:00:00Z",
}

# A pre-FSRS row: no stability yet, SM-2 interval of 12 days.
LEGACY = {
    "stability": None,
    "interval": 12,
    "next_review": "2026-07-09",
    "last_reviewed": "2026-06-27T10:00:00Z",
    "solved_at": "2026-05-01T10:00:00Z",
}


# --- map_rating: the full 15-cell table ---

def test_map_rating_table():
    expected = {
        ("self", 1): Rating.Again, ("self", 2): Rating.Again, ("self", 3): Rating.Hard,
        ("self", 4): Rating.Good, ("self", 5): Rating.Easy,
        ("hint", 1): Rating.Again, ("hint", 2): Rating.Again, ("hint", 3): Rating.Hard,
        ("hint", 4): Rating.Hard, ("hint", 5): Rating.Hard,
        ("solution", 1): Rating.Again, ("solution", 2): Rating.Again,
        ("solution", 3): Rating.Again, ("solution", 4): Rating.Again,
        ("solution", 5): Rating.Again,
    }
    for (source, stars), rating in expected.items():
        assert scheduler.map_rating(stars, source) == rating, (source, stars)


# --- solution-source effects on scheduling ---

def test_saw_solution_comes_back_within_two_days_despite_five_stars():
    result = scheduler.apply_review(MATURE, 5, "solution", now=NOW)
    due = datetime.fromisoformat(result["next_review"]).date()
    assert (due - NOW.date()).days <= 2
    assert result["stability"] < MATURE["stability"]  # lapse shrinks memory


def test_hint_caps_at_hard_interval():
    hint5 = scheduler.apply_review(MATURE, 5, "hint", now=NOW)
    self3 = scheduler.apply_review(MATURE, 3, "self", now=NOW)  # self/3 is also Hard
    self5 = scheduler.apply_review(MATURE, 5, "self", now=NOW)
    assert hint5["interval"] == self3["interval"]
    assert hint5["interval"] < self5["interval"]


def test_never_due_today():
    for source in ("self", "hint", "solution"):
        for stars in range(1, 6):
            result = scheduler.apply_review(MATURE, stars, source, now=NOW)
            assert result["next_review"] > NOW.date().isoformat()
            assert result["interval"] >= 1


# --- schedule growth ---

def test_repeated_good_reviews_grow_the_interval():
    row = dict(MATURE)
    last_interval = 0
    when = NOW
    for _ in range(3):
        result = scheduler.apply_review(row, 4, "self", now=when)
        assert result["interval"] > last_interval
        last_interval = result["interval"]
        row.update(result)
        row["last_reviewed"] = when.isoformat()
        # next review happens right when the card comes due
        when = when + timedelta(days=result["interval"])


def test_easy_beats_good():
    good = scheduler.apply_review(MATURE, 4, "self", now=NOW)
    easy = scheduler.apply_review(MATURE, 5, "self", now=NOW)
    assert easy["interval"] > good["interval"]


def test_lower_retention_means_longer_intervals():
    relaxed = scheduler.apply_review(MATURE, 4, "self", desired_retention=0.8, now=NOW)
    strict = scheduler.apply_review(MATURE, 4, "self", desired_retention=0.95, now=NOW)
    assert relaxed["interval"] > strict["interval"]


# --- legacy rows (pre-FSRS) ---

def test_legacy_row_lazy_seed_produces_sane_schedule():
    result = scheduler.apply_review(LEGACY, 4, "self", now=NOW)
    assert result["stability"] > 0
    assert 1 <= result["fsrs_difficulty"] <= 10
    assert result["fsrs_state"] in (1, 2, 3)
    # Seeded from a 12-day interval, a Good review should land beyond a week.
    assert result["interval"] > 7


def test_legacy_row_solution_still_resets():
    result = scheduler.apply_review(LEGACY, 5, "solution", now=NOW)
    due = datetime.fromisoformat(result["next_review"]).date()
    assert (due - NOW.date()).days <= 2


# --- new questions ---

def test_initial_schedule_defaults_to_good_without_rating():
    unrated = scheduler.initial_schedule(None, "self", now=NOW)
    rated_good = scheduler.initial_schedule(4, "self", now=NOW)
    assert unrated == rated_good


def test_initial_schedule_with_solution_source():
    result = scheduler.initial_schedule(5, "solution", now=NOW)
    due = datetime.fromisoformat(result["next_review"]).date()
    assert (due - NOW.date()).days <= 2


# --- determinism & preview ---

def test_deterministic():
    a = scheduler.apply_review(MATURE, 4, "self", now=NOW)
    b = scheduler.apply_review(MATURE, 4, "self", now=NOW)
    assert a == b


def test_preview_covers_all_combos_and_matches_apply_review():
    p = scheduler.preview(MATURE, now=NOW)
    assert set(p.keys()) == {"self", "hint", "solution"}
    for source in p:
        assert set(p[source].keys()) == {"1", "2", "3", "4", "5"}
    applied = scheduler.apply_review(MATURE, 5, "hint", now=NOW)
    assert p["hint"]["5"]["due"] == applied["next_review"]
    assert p["hint"]["5"]["days"] == applied["interval"]
