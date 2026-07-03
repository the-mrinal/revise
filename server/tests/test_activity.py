from datetime import date, timedelta

from database import _bucket_event_days, _compute_streaks


# --- _bucket_event_days ---

def test_bucket_counts_per_utc_day():
    ts = [
        "2026-07-01T09:00:00+00:00",
        "2026-07-01T23:59:59+00:00",
        "2026-07-02T00:00:01+00:00",
    ]
    assert _bucket_event_days(ts) == {"2026-07-01": 2, "2026-07-02": 1}


def test_bucket_ignores_falsy_entries():
    assert _bucket_event_days(["2026-07-01T09:00:00Z", None, ""]) == {"2026-07-01": 1}


def test_bucket_empty():
    assert _bucket_event_days([]) == {}


# --- _compute_streaks ---

def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_streaks_empty():
    assert _compute_streaks(set()) == (0, 0)


def test_streak_ending_today():
    assert _compute_streaks({_d(2), _d(1), _d(0)}) == (3, 3)


def test_streak_ending_yesterday_still_alive():
    assert _compute_streaks({_d(2), _d(1)}) == (2, 2)


def test_streak_broken_two_days_ago_is_dead():
    current, longest = _compute_streaks({_d(4), _d(3), _d(2)})
    assert current == 0
    assert longest == 3


def test_gap_splits_streaks_longest_wins():
    dates = {_d(10), _d(9), _d(8), _d(7), _d(1), _d(0)}
    current, longest = _compute_streaks(dates)
    assert current == 2
    assert longest == 4
