"""FSRS scheduling engine (successor to sm2.py).

Wraps py-fsrs with this app's conventions:
- ratings stay 1-5 stars; solution_source ('self' | 'hint' | 'solution')
  caps the FSRS grade so assisted recalls don't earn long intervals
- next_review stays a DATE (the queue key), floored at tomorrow so a
  reviewed card never reappears in the same day's queue
- interval keeps being written as derived days for event rows and the
  history UI; easiness_factor/repetitions are no longer written
- rows created before migration 009 have stability NULL and are seeded
  from their SM-2 interval on first touch (lazy migration)

Learning/relearning steps are disabled (day granularity) and fuzzing is
off so previews and tests are deterministic.
"""

from datetime import date, datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

VALID_SOLUTION_SOURCES = ("self", "hint", "solution")

_BASE_RATING = {1: Rating.Again, 2: Rating.Again, 3: Rating.Hard, 4: Rating.Good, 5: Rating.Easy}


def map_rating(self_rating: int, solution_source: str = "self") -> Rating:
    """Map a 1-5 star rating + solution source to an FSRS grade.

    Seeing the solution is always a lapse; a hint caps the grade at Hard.
    """
    grade = _BASE_RATING.get(self_rating, Rating.Again)
    if solution_source == "solution":
        return Rating.Again
    if solution_source == "hint" and grade.value > Rating.Hard.value:
        return Rating.Hard
    return grade


def _make_scheduler(desired_retention: float = 0.9, params=None) -> Scheduler:
    kwargs = {
        "desired_retention": desired_retention,
        "learning_steps": (),
        "relearning_steps": (),
        "enable_fuzzing": False,
    }
    if params:
        kwargs["parameters"] = params
    return Scheduler(**kwargs)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _card_from_row(row: dict, now: datetime) -> Card:
    """Reconstruct the FSRS card for a question row.

    Pre-FSRS rows (stability NULL) are seeded as a Review-state card whose
    stability approximates the SM-2 interval it had earned.
    """
    last_review = _parse_dt(row.get("last_reviewed")) or _parse_dt(row.get("solved_at"))
    due = _parse_dt(row.get("next_review")) or now
    if row.get("stability") is None:
        return Card(
            state=State.Review,
            stability=float(max(1, row.get("interval") or 1)),
            difficulty=5.0,
            due=due,
            last_review=last_review,
        )
    return Card(
        state=State(row.get("fsrs_state") or State.Review.value),
        stability=row["stability"],
        difficulty=row.get("fsrs_difficulty") or 5.0,
        due=due,
        last_review=last_review,
    )


def _result_from_card(card: Card, now: datetime) -> dict:
    # Floor the due date at tomorrow: next_review is a date-keyed queue, and
    # a card due "today" would reappear immediately after being reviewed.
    due_date = max(card.due.date(), now.date() + timedelta(days=1))
    return {
        "stability": round(card.stability, 4),
        "fsrs_difficulty": round(card.difficulty, 4),
        "fsrs_state": card.state.value,
        "next_review": due_date.isoformat(),
        "interval": max(1, (due_date - now.date()).days),
    }


def apply_review(
    row: dict,
    self_rating: int,
    solution_source: str = "self",
    *,
    desired_retention: float = 0.9,
    params=None,
    now: datetime | None = None,
) -> dict:
    """Review an existing question and return its updated schedule fields."""
    now = now or datetime.now(timezone.utc)
    scheduler = _make_scheduler(desired_retention, params)
    card = _card_from_row(row, now)
    rating = map_rating(self_rating, solution_source)
    card, _ = scheduler.review_card(card, rating, review_datetime=now)
    return _result_from_card(card, now)


def initial_schedule(
    self_rating: int | None,
    solution_source: str = "self",
    *,
    desired_retention: float = 0.9,
    params=None,
    now: datetime | None = None,
) -> dict:
    """Schedule a brand-new question (first solve).

    A timer-start capture has no rating yet; treat it as Good, mirroring
    the old `sm2(q.self_rating or 3, ...)` neutral default.
    """
    now = now or datetime.now(timezone.utc)
    scheduler = _make_scheduler(desired_retention, params)
    card = Card(due=now)
    rating = map_rating(self_rating, solution_source) if self_rating else Rating.Good
    card, _ = scheduler.review_card(card, rating, review_datetime=now)
    return _result_from_card(card, now)


def preview(
    row: dict,
    *,
    desired_retention: float = 0.9,
    params=None,
    now: datetime | None = None,
) -> dict:
    """All 15 source x rating outcomes for a row, for dashboard tooltips.

    Returns {source: {rating: {"due": iso_date, "days": n}}}.
    """
    now = now or datetime.now(timezone.utc)
    scheduler = _make_scheduler(desired_retention, params)
    out = {}
    for source in VALID_SOLUTION_SOURCES:
        by_rating = {}
        for stars in range(1, 6):
            card = _card_from_row(row, now)
            card, _ = scheduler.review_card(card, map_rating(stars, source), review_datetime=now)
            result = _result_from_card(card, now)
            by_rating[str(stars)] = {"due": result["next_review"], "days": result["interval"]}
        out[source] = by_rating
    return out
