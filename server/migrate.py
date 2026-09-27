"""Apply server/migrations/pg/*.sql in order, once each.

Runs at container start (see Dockerfile). Applied versions are recorded in
schema_migrations; each file runs in its own transaction under an advisory
lock, so two starting containers can't apply the same file twice.

Our own Postgres (DATABASE_URL) is always migrated. The Supabase database
(SUPABASE_DB_URL) is migrated too while it is still the one serving
requests; after the cutover it is left frozen.

Usage: python migrate.py
"""

import glob
import os
import sys

import psycopg

import cutover
import db

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations", "pg")
LOCK_ID = 7_300_001  # arbitrary, stable advisory-lock key


def pending(conn, files: list[str]) -> list[str]:
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    return [f for f in files if os.path.basename(f) not in applied]


def migrate(url: str, label: str) -> list[str]:
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    done = []
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Supabase exposes public tables over its REST API; keep this closed.
        conn.execute("ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY")
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            for path in pending(conn, files):
                version = os.path.basename(path)
                with open(path) as f:
                    body = f.read()
                with conn.transaction():
                    conn.execute(body)
                    conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                    )
                print(f"[migrate] {label}: applied {version}")
                done.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    if not done:
        print(f"[migrate] {label}: up to date")
    return done


def refuse_empty_local() -> None:
    """Never serve users from an empty local database while the Supabase one
    (which has their data) is still configured: that means the cutover
    hasn't run, and DB_TARGET was set to local too early."""
    if cutover.current_target() != "local" or not os.environ.get("SUPABASE_DB_URL"):
        return
    with psycopg.connect(db.database_url("local"), prepare_threshold=None) as conn:
        has_users = conn.execute("SELECT EXISTS (SELECT 1 FROM users)").fetchone()[0]
    if not has_users:
        raise SystemExit(
            "Refusing to start: DB_TARGET is local but the local database is empty, "
            "while SUPABASE_DB_URL is still set. Set DB_TARGET=supabase until "
            "copy_to_local.py run has completed."
        )


def main() -> int:
    migrate(db.database_url("local"), "local")
    if os.environ.get("SUPABASE_DB_URL") and cutover.current_target() == "supabase":
        migrate(db.database_url("supabase"), "supabase")
    refuse_empty_local()
    return 0


if __name__ == "__main__":
    sys.exit(main())
