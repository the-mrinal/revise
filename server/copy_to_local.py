"""Copy every Revise table from Supabase into our own Postgres, and prove it.

  python copy_to_local.py rehearse   copy + verify; the live server is untouched
  python copy_to_local.py run        the cutover: hold requests, copy, verify,
                                     switch the server to local, release

Both need SUPABASE_DB_URL and DATABASE_URL. Run inside the server container:
  docker compose exec server python copy_to_local.py rehearse

The copy reads Supabase inside one REPEATABLE READ snapshot, so every table
reflects the same moment, and writes our Postgres inside one transaction
(all or nothing). Verification compares, per table, the row count and an
md5 over every row's text, plus each ID counter. The rehearsal also runs
the read functions the dashboard uses for every user against both
databases and diffs their JSON (too slow to do while requests are held).

If anything fails during 'run', the server is released still serving from
Supabase and nothing was lost; the local copy is simply redone next time.
"""

import json
import sys
import time

import psycopg
from psycopg import sql

import cutover
import database
import db

# Parents before children, so foreign keys hold at every step.
TABLES = [
    "users",
    "questions",
    "question_events",
    "user_settings",
    "user_platforms",
    "user_profiles",
    "feature_access",
    "access_audit",
]
PRIMARY_KEYS = {"user_settings": "user_id", "user_profiles": "user_id", "users": "id"}

SESSION = "-c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=1"


def connect(target: str) -> psycopg.Connection:
    return psycopg.connect(db.database_url(target), options=SESSION, prepare_threshold=None)


def columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def refresh_users_on_source() -> None:
    """Bring Supabase's public.users up to date with auth.users, so every
    account (and every foreign key) comes across."""
    with psycopg.connect(db.database_url("supabase"), autocommit=True, prepare_threshold=None) as c:
        c.execute(
            "INSERT INTO public.users (id, email, created_at, last_sign_in_at, source) "
            "SELECT id, email, created_at, last_sign_in_at, 'supabase' FROM auth.users "
            "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, "
            "last_sign_in_at = GREATEST(public.users.last_sign_in_at, EXCLUDED.last_sign_in_at)"
        )


def fingerprint(conn, table: str) -> tuple[int, str | None]:
    pk = PRIMARY_KEYS.get(table, "id")
    query = sql.SQL(
        "SELECT count(*), md5(string_agg(t::text, E'\\n' ORDER BY t.{pk})) FROM public.{t} t"
    ).format(pk=sql.Identifier(pk), t=sql.Identifier(table))
    return tuple(conn.execute(query).fetchone())


def sequence_value(conn, table: str) -> tuple | None:
    if PRIMARY_KEYS.get(table, "id") != "id":
        return None  # keyed by user_id: no id counter
    seq = conn.execute(
        "SELECT pg_get_serial_sequence(%s, 'id')", (f"public.{table}",)
    ).fetchone()[0]
    if not seq:
        return None
    last_value, is_called = conn.execute(
        sql.SQL("SELECT last_value, is_called FROM {}").format(sql.SQL(seq))
    ).fetchone()
    return seq, last_value, is_called


def copy_and_verify(held: bool = False) -> dict:
    """Copy all tables, then compare. Returns a report; raises on mismatch.

    ID counters aren't part of a snapshot: with live traffic (a rehearsal)
    Supabase's keep moving after the copy. So each local counter is checked
    against the value copied and against the copied rows; and when requests
    are held (the real cutover), Supabase's must not have moved at all."""
    # The copy empties the local tables first. Once local is live, that
    # would destroy data, so refuse.
    if cutover.current_target() == "local":
        raise SystemExit("Local Postgres is live; refusing to overwrite it.")
    report = {"tables": {}, "sequences": {}}
    copied_counters: dict[str, tuple] = {}
    with connect("supabase") as src, connect("local") as dst:
        # One snapshot for the whole copy and its verification.
        src.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        src.read_only = True
        with dst.transaction():
            dst.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
                )
            )
            for table in TABLES:
                cols = columns(dst, table)
                if cols != columns(src, table):
                    raise RuntimeError(f"{table}: columns differ between Supabase and local")
                col_list = sql.SQL(", ").join(map(sql.Identifier, cols))
                out_q = sql.SQL("COPY (SELECT {} FROM public.{}) TO STDOUT (FORMAT BINARY)").format(
                    col_list, sql.Identifier(table)
                )
                in_q = sql.SQL("COPY public.{} ({}) FROM STDIN (FORMAT BINARY)").format(
                    sql.Identifier(table), col_list
                )
                with src.cursor().copy(out_q) as reader, dst.cursor().copy(in_q) as writer:
                    for chunk in reader:
                        writer.write(chunk)
                seq = sequence_value(src, table)
                if seq:
                    local_seq = sequence_value(dst, table)[0]
                    dst.execute("SELECT setval(%s, %s, %s)", (local_seq, seq[1], seq[2]))
                    copied_counters[table] = (seq[1], seq[2])

        # Verify against the same source snapshot the copy read from.
        problems = []
        for table in TABLES:
            a, b = fingerprint(src, table), fingerprint(dst, table)
            report["tables"][table] = {"rows": a[0], "match": a == b}
            if a != b:
                problems.append(f"{table}: supabase {a} vs local {b}")
            if table in copied_counters:
                sb = sequence_value(dst, table)
                max_id = dst.execute(
                    sql.SQL("SELECT coalesce(max(id), 0) FROM public.{}").format(sql.Identifier(table))
                ).fetchone()[0]
                report["sequences"][table] = sb[1]
                if (sb[1], sb[2]) != copied_counters[table] or sb[1] < max_id:
                    problems.append(f"{table} id counter: local {sb[1:]}, copied "
                                    f"{copied_counters[table]}, highest id {max_id}")
                if held and sequence_value(src, table)[1:] != copied_counters[table]:
                    problems.append(f"{table}: Supabase's id counter moved while requests were held")
        src.rollback()
    if problems:
        raise RuntimeError("Verification failed:\n  " + "\n  ".join(problems))
    return report


# Read paths the dashboard, flex page and admin panel use.
PER_USER_READS = [
    "get_all_questions",
    "get_stats",
    "get_flex_stats",
    "get_activity_heatmap",
    "get_questions_activity_summary",
    "get_user_settings",
    "get_profile",
    "get_user_platforms",
    "get_user_features",
]


USER_TABLES = {
    "questions": "user_id",
    "question_events": "user_id",
    "user_settings": "user_id",
    "user_platforms": "user_id",
    "user_profiles": "user_id",
    "feature_access": "user_id",
}


def _user_digest(uid: str) -> str:
    """One hash over everything a user owns, to tell 'the reads differ' from
    'the user saved something since the copy'."""
    parts = []
    for table, col in USER_TABLES.items():
        pk = PRIMARY_KEYS.get(table, "id")
        row = db.fetch_one(
            sql.SQL("SELECT md5(string_agg(t::text, E'\\n' ORDER BY t.{pk})) AS h "
                    "FROM public.{t} t WHERE t.{c} = %s").format(
                pk=sql.Identifier(pk), t=sql.Identifier(table), c=sql.Identifier(col)),
            (uid,),
        )
        parts.append(row["h"] or "")
    return "|".join(parts)


def _reads(uid: str) -> str:
    return json.dumps(
        {name: getattr(database, name)(uid) for name in PER_USER_READS},
        sort_keys=True, default=str,
    )


def compare_reads() -> dict:
    """Run every read for every user against both databases and diff them.

    A user whose data changed on Supabase after the copy (someone saved a
    question mid-rehearsal) is reported as skipped, not as a failure; a
    difference with identical data is a real problem and raises."""
    db.switch_target("local")
    user_ids = [r["id"] for r in db.fetch_all("SELECT id FROM users ORDER BY id")]
    local_admin = (database.list_all_users(), database.get_recent_audit())
    db.switch_target("supabase")
    supa_admin = (database.list_all_users(), database.get_recent_audit())
    result = {"identical": 0, "skipped": []}
    for uid in user_ids:
        db.switch_target("supabase")
        supa, supa_digest = _reads(uid), _user_digest(uid)
        db.switch_target("local")
        local, local_digest = _reads(uid), _user_digest(uid)
        if supa == local:
            result["identical"] += 1
        elif supa_digest != local_digest:
            result["skipped"].append(uid)
        else:
            db.close()
            raise RuntimeError(f"Reads differ for user {uid} although their data is identical")
    db.close()
    if json.dumps(local_admin, sort_keys=True, default=str) != json.dumps(supa_admin, sort_keys=True, default=str):
        print("Note: admin user list or audit log changed during the check (sign-ins or grants).")
    return result


def rehearse() -> None:
    started = time.time()
    refresh_users_on_source()
    report = copy_and_verify()
    reads = compare_reads()
    print(json.dumps(report, indent=2))
    if reads["skipped"]:
        print(f"Skipped {len(reads['skipped'])} user(s) who saved something during the "
              f"rehearsal: {', '.join(reads['skipped'])}")
    print(f"Rehearsal passed: data identical, reads identical for {reads['identical']} users "
          f"({time.time() - started:.1f}s). The live server was not touched.")


def run() -> None:
    if cutover.current_target() == "local":
        raise SystemExit("The server already serves from local Postgres. Nothing to do.")
    refresh_users_on_source()
    started = time.time()
    cutover.hold()
    print("Requests held; nothing in flight.")
    switched = False
    try:
        refresh_users_on_source()  # anyone who signed up in the last seconds
        report = copy_and_verify(held=True)
        cutover.switch("local")
        switched = True
    finally:
        cutover.release()
        held_for = time.time() - started
        where = "local Postgres" if switched else "Supabase (unchanged)"
        print(f"Released after {held_for:.1f}s. Serving from {where}.")
    print(json.dumps(report, indent=2))
    print("Cutover complete: every table and ID counter identical.")
    print("Next: set DB_TARGET=local in .env so the setting matches control.json.")


if __name__ == "__main__":
    commands = {"rehearse": rehearse, "run": run}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]]()
