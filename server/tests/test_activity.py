from datetime import date, timedelta
from zoneinfo import ZoneInfo

from database import DEFAULT_TIMEZONE, _bucket_event_days, _compute_streaks, _to_local_day


# --- _to_local_day ---

def test_local_day_ist_shifts_late_utc_evening_to_next_day():
    # 20:30 UTC = 02:00 IST next day
    assert _to_local_day("2026-07-01T20:30:00+00:00", ZoneInfo("Asia/Kolkata")) == "2026-07-02"


def test_local_day_handles_z_suffix_and_naive_as_utc():
    zone = ZoneInfo("Asia/Kolkata")
    assert _to_local_day("2026-07-01T09:00:00Z", zone) == "2026-07-01"
    assert _to_local_day("2026-07-01T20:30:00", zone) == "2026-07-02"  # naive = UTC


def test_local_day_western_zone_shifts_early_utc_back_a_day():
    # 03:00 UTC = 23:00 previous day in New York (EDT)
    assert _to_local_day("2026-07-02T03:00:00+00:00", ZoneInfo("America/New_York")) == "2026-07-01"


def test_local_day_unparseable_returns_none():
    assert _to_local_day("not-a-date", ZoneInfo("UTC")) is None
    assert _to_local_day("", ZoneInfo("UTC")) is None


# --- _bucket_event_days ---

def test_bucket_counts_per_day_in_utc():
    ts = [
        "2026-07-01T09:00:00+00:00",
        "2026-07-01T23:59:59+00:00",
        "2026-07-02T00:00:01+00:00",
    ]
    assert _bucket_event_days(ts, "UTC") == {"2026-07-01": 2, "2026-07-02": 1}


def test_bucket_defaults_to_ist():
    assert DEFAULT_TIMEZONE == "Asia/Kolkata"
    ts = [
        "2026-07-01T09:00:00+00:00",  # 14:30 IST -> Jul 1
        "2026-07-01T20:30:00+00:00",  # 02:00 IST -> Jul 2
    ]
    assert _bucket_event_days(ts) == {"2026-07-01": 1, "2026-07-02": 1}


def test_bucket_bad_timezone_falls_back_to_default():
    ts = ["2026-07-01T20:30:00+00:00"]  # Jul 2 in IST
    assert _bucket_event_days(ts, "Not/AZone") == {"2026-07-02": 1}


def test_bucket_ignores_falsy_and_unparseable_entries():
    assert _bucket_event_days(["2026-07-01T09:00:00Z", None, "", "garbage"], "UTC") == {"2026-07-01": 1}


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


def test_streaks_respect_explicit_local_today():
    # It's already "tomorrow" in Auckland while the dates end on UTC-today:
    # with local today passed in, the streak is still alive.
    local_today = date.today() + timedelta(days=1)
    current, longest = _compute_streaks({_d(1), _d(0)}, today=local_today)
    assert current == 2
    assert longest == 2
