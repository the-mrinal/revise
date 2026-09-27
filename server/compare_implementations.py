"""Prove the SQL data layer returns what the Supabase-client one did.

Runs every read the dashboard, flex page and admin panel use, for every
user, through both the old code (legacy/supabase_database.py, via the
Supabase REST API) and the new code (database.py, via SQL on the Supabase
Postgres), and diffs the results. Read-only: nothing is written.

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DB_URL:
  docker compose exec server python compare_implementations.py
"""

import json
import sys
from datetime import datetime

import database as new
import db
from legacy import supabase_database as old

PER_USER_READS = [
    "get_all_questions",
    "get_stats",
    "get_flex_stats",
    "get_activity_heatmap",
    "get_questions_activity_summary",
    "get_today_activity",
    "get_revisions_due",
    "count_revisions_done_today",
    "get_user_settings",
    "get_profile",
    "get_user_platforms",
    "get_user_features",
    "is_user_admin",
    "get_user_timezone",
    "get_user_activity",
]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def same_instant(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return parse(a) == parse(b)


def main() -> int:
    db.switch_target("supabase")
    users = old.list_all_users()
    failures = []

    # The admin list now comes from public.users instead of the Auth admin
    # API; timestamps are formatted differently, so compare instants.
    new_users = {u["user_id"]: u for u in new.list_all_users()}
    for u in users:
        n = new_users.pop(u["user_id"], None)
        if n is None:
            failures.append(f"list_all_users: {u['user_id']} missing from users table")
            continue
        if (u["email"], u["is_admin"], u["features"]) != (n["email"], n["is_admin"], n["features"]):
            failures.append(f"list_all_users: {u['user_id']} differs: {u} vs {n}")
        if not same_instant(u["last_sign_in_at"], n["last_sign_in_at"]):
            failures.append(f"list_all_users: {u['user_id']} last_sign_in_at differs")
    for uid in new_users:
        failures.append(f"list_all_users: {uid} only in users table")

    if canonical(old.get_recent_audit()) != canonical(new.get_recent_audit()):
        failures.append("get_recent_audit differs")

    checked = 0
    for u in users:
        uid = u["user_id"]
        for name in PER_USER_READS:
            a, b = getattr(old, name)(uid), getattr(new, name)(uid)
            if canonical(a) != canonical(b):
                failures.append(f"{name}({uid}) differs")
            checked += 1
        for q in old.get_all_questions(uid):
            if canonical(old.get_question_events(uid, q["id"])) != canonical(
                new.get_question_events(uid, q["id"])
            ):
                failures.append(f"get_question_events({uid}, {q['id']}) differs")
            checked += 1

    db.close()
    if failures:
        print("\n".join(failures))
        print(f"\n{len(failures)} difference(s) in {checked} comparisons.")
        return 1
    print(f"Identical: {checked} comparisons across {len(users)} users.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
