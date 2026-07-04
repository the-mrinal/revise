from database import _aggregate_heatmap_days


def test_new_vs_revised_split_by_first_day():
    pairs = {
        (1, "2026-07-01"),  # first solve
        (1, "2026-07-03"),  # revision two days later
        (2, "2026-07-03"),  # first solve
    }
    first_day = {1: "2026-07-01", 2: "2026-07-03"}
    out = _aggregate_heatmap_days(pairs, first_day, {1: "easy", 2: "hard"})

    assert out["2026-07-01"] == {"total": 1, "new": 1, "revised": 0, "difficulty": {"easy": 1}}
    assert out["2026-07-03"] == {"total": 2, "new": 1, "revised": 1, "difficulty": {"easy": 1, "hard": 1}}


def test_missing_difficulty_buckets_as_unknown():
    out = _aggregate_heatmap_days({(1, "2026-07-01")}, {1: "2026-07-01"}, {1: None})
    assert out["2026-07-01"]["difficulty"] == {"unknown": 1}

    out = _aggregate_heatmap_days({(1, "2026-07-01")}, {1: "2026-07-01"}, {})
    assert out["2026-07-01"]["difficulty"] == {"unknown": 1}


def test_unknown_first_day_counts_as_new():
    # Question row deleted / predates the event log: earliest sighting is new.
    out = _aggregate_heatmap_days({(9, "2026-07-02")}, {}, {})
    assert out["2026-07-02"]["new"] == 1
    assert out["2026-07-02"]["revised"] == 0


def test_question_solved_before_window_is_revised():
    out = _aggregate_heatmap_days(
        {(1, "2026-07-02")}, {1: "2025-01-15"}, {1: "medium"}
    )
    assert out["2026-07-02"] == {"total": 1, "new": 0, "revised": 1, "difficulty": {"medium": 1}}


def test_empty_input():
    assert _aggregate_heatmap_days(set(), {}, {}) == {}
