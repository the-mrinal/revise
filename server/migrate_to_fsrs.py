"""One-time migration: seed FSRS memory state for pre-FSRS questions.

Replays each question's event history (created/reviewed, oldest first)
through the FSRS scheduler so stability/difficulty start from an informed
place instead of zero. Events predate solution_source, so missing values
count as 'self'. Questions with no rated events are seeded directly from
their SM-2 interval (same rule as scheduler._card_from_row's lazy path)
and keep their existing next_review so due dates don't jump.

Usage: python migrate_to_fsrs.py            (needs SUPABASE_* env vars)
Safe to re-run: questions that already have stability are skipped.
"""

from datetime import datetime, timezone

from fsrs import Card, Rating, State

import scheduler
from database import get_client


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def replay_events(events: list[dict]) -> Card | None:
    """Run rated created/reviewed events through a fresh FSRS card."""
    fsrs_scheduler = scheduler._make_scheduler()
    card = None
    for ev in events:
        if ev["event_type"] not in ("created", "reviewed"):
            continue
        when = _parse_dt(ev.get("created_at"))
        if when is None:
            continue
        rating = (
            scheduler.map_rating(ev["self_rating"], ev.get("solution_source") or "self")
            if ev.get("self_rating")
            else Rating.Good
        )
        if card is None:
            card = Card(due=when)
        elif card.last_review and when <= card.last_review:
            continue  # out-of-order/duplicate timestamps
        card, _ = fsrs_scheduler.review_card(card, rating, review_datetime=when)
    return card


def main():
    client = get_client()

    questions = client.table("questions").select(
        "id, user_id, solved_at, last_reviewed, interval, next_review, stability"
    ).execute().data
    print(f"Found {len(questions)} questions")

    migrated = 0
    seeded = 0
    skipped = 0

    for q in questions:
        if q.get("stability") is not None:
            skipped += 1
            continue

        events = (
            client.table("question_events")
            .select("event_type, self_rating, solution_source, created_at")
            .eq("question_id", q["id"])
            .order("created_at", desc=False)
            .execute()
            .data
        )
        card = replay_events(events)

        replayed = card is not None and card.stability is not None
        if replayed:
            due_date = card.due.date()
            update = {
                "stability": round(card.stability, 4),
                "fsrs_difficulty": round(card.difficulty, 4),
                "fsrs_state": card.state.value,
                "next_review": due_date.isoformat(),
                "interval": max(1, (due_date - card.last_review.date()).days),
            }
        else:
            # No usable history: approximate memory state from the SM-2
            # interval and keep the scheduled date the user already sees.
            update = {
                "stability": float(max(1, q.get("interval") or 1)),
                "fsrs_difficulty": 5.0,
                "fsrs_state": State.Review.value,
            }

        try:
            client.table("questions").update(update).eq("id", q["id"]).execute()
        except Exception as e:
            print(f"  FAILED q{q['id']}: {e}")
            continue
        if replayed:
            migrated += 1
        else:
            seeded += 1

    print(f"\nDone. replayed={migrated} seeded={seeded} skipped(already-fsrs)={skipped}")


if __name__ == "__main__":
    main()
