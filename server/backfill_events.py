"""One-time backfill: reconstruct question_events for existing questions.

Existing rows only carry current state, so history is approximate:
  - a 'created' event from solved_at (with the row's current SM-2 snapshot)
  - a 'reviewed' event from last_reviewed, only if its date differs from solved_at

Intermediate reviews were never recorded and cannot be recovered. Reconstructed
rows are flagged (reconstructed=true) so the UI can mark them.

Usage: docker compose exec server python backfill_events.py
Safe to re-run: questions that already have events are skipped.
"""

from database import get_client


def main():
    client = get_client()

    questions = client.table("questions").select(
        "id, user_id, solved_at, last_reviewed, self_rating, time_taken, "
        "interval, repetitions, easiness_factor, next_review"
    ).execute().data
    print(f"Found {len(questions)} questions")

    existing = client.table("question_events").select("question_id").execute().data
    have_events = {e["question_id"] for e in existing}

    created_count = 0
    reviewed_count = 0
    skipped = 0

    for q in questions:
        if q["id"] in have_events:
            skipped += 1
            continue

        rows = []
        solved_at = q.get("solved_at")
        rows.append({
            "user_id": q["user_id"],
            "question_id": q["id"],
            "event_type": "created",
            "self_rating": q.get("self_rating"),
            "time_taken": q.get("time_taken"),
            "interval": q.get("interval"),
            "repetitions": q.get("repetitions"),
            "easiness_factor": q.get("easiness_factor"),
            "next_review": q.get("next_review"),
            "reconstructed": True,
            "created_at": solved_at,
        })

        last_reviewed = q.get("last_reviewed")
        if last_reviewed and (solved_at or "")[:10] != last_reviewed[:10]:
            rows.append({
                "user_id": q["user_id"],
                "question_id": q["id"],
                "event_type": "reviewed",
                "self_rating": q.get("self_rating"),
                "interval": q.get("interval"),
                "repetitions": q.get("repetitions"),
                "easiness_factor": q.get("easiness_factor"),
                "next_review": q.get("next_review"),
                "reconstructed": True,
                "created_at": last_reviewed,
            })
            reviewed_count += 1

        try:
            client.table("question_events").insert(rows).execute()
            created_count += 1
        except Exception as e:
            print(f"  FAILED q{q['id']}: {e}")

    print(
        f"\nDone. created={created_count} reviewed={reviewed_count} "
        f"skipped(existing)={skipped}"
    )


if __name__ == "__main__":
    main()
