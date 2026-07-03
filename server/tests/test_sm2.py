from datetime import date, timedelta

from sm2 import sm2


def test_failed_review_resets_schedule():
    result = sm2(self_rating=1, easiness_factor=2.5, interval=10, repetitions=4)
    assert result["interval"] == 1
    assert result["repetitions"] == 0
    assert result["next_review"] == (date.today() + timedelta(days=1)).isoformat()


def test_rating_2_maps_to_failing_quality():
    result = sm2(self_rating=2, easiness_factor=2.5, interval=6, repetitions=2)
    assert result["repetitions"] == 0
    assert result["interval"] == 1


def test_first_successful_review_gives_one_day():
    result = sm2(self_rating=4, easiness_factor=2.5, interval=1, repetitions=0)
    assert result["repetitions"] == 1
    assert result["interval"] == 1


def test_second_successful_review_gives_six_days():
    result = sm2(self_rating=4, easiness_factor=2.5, interval=1, repetitions=1)
    assert result["repetitions"] == 2
    assert result["interval"] == 6


def test_third_review_multiplies_interval_by_ef():
    result = sm2(self_rating=5, easiness_factor=2.5, interval=6, repetitions=2)
    assert result["repetitions"] == 3
    # rating 5 → quality 5 → EF += 0.1
    assert result["easiness_factor"] == 2.6
    assert result["interval"] == round(6 * 2.6)


def test_easiness_factor_never_drops_below_floor():
    result = sm2(self_rating=1, easiness_factor=1.3, interval=1, repetitions=0)
    assert result["easiness_factor"] == 1.3


def test_rating_3_succeeds_but_lowers_ef():
    result = sm2(self_rating=3, easiness_factor=2.5, interval=1, repetitions=1)
    assert result["repetitions"] == 2
    # quality 3 → EF + (0.1 - 2*(0.08 + 2*0.02)) = EF - 0.14
    assert result["easiness_factor"] == 2.36
