"""Test bootstrap.

auth.py reads Supabase env vars at import time, so they must be stubbed
before any test module imports `main`. Route tests never touch a database:
they patch the db functions on `main`, which imports them by name.

Database tests (tests/test_database.py, tests/test_copy_to_local.py) need a
Postgres server: set TEST_DATABASE_URL to a superuser connection string
(CI provides one). Each test gets freshly created databases, built by the
real migrations. Without TEST_DATABASE_URL those tests are skipped.
"""

import os
import sys
import tempfile
import uuid

os.environ.setdefault("SUPABASE_URL", "http://supabase.test.invalid")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("AVATAR_DIR", tempfile.mkdtemp(prefix="revise-avatars-"))
os.environ.setdefault("CUTOVER_DIR", tempfile.mkdtemp(prefix="revise-cutover-"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import database
import db
import main as main_module
from auth import get_current_user_id

TEST_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def client():
    """TestClient authenticated as TEST_USER_ID (auth dependency overridden)."""
    main_module.app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.pop(get_current_user_id, None)


# --- Postgres fixtures ------------------------------------------------------------

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


def _require_postgres():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")


@pytest.fixture
def make_database():
    """Factory: create an empty database, return its URL; all dropped after."""
    _require_postgres()
    created = []

    def make() -> str:
        name = f"revise_t_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return make_conninfo(**{**conninfo_to_dict(ADMIN_URL), "dbname": name})

    yield make
    db.close()
    database._known_users.clear()
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        for name in created:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def local_db(make_database, monkeypatch):
    """A migrated, empty Revise database serving as the 'local' target."""
    import migrate

    url = make_database()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DB_TARGET", "local")
    migrate.migrate(url, "local")
    db.close()
    database._known_users.clear()
    return url


def make_supabase_shaped(url: str) -> None:
    """Recreate Supabase's layout: an auth.users table, and the Revise tables
    with foreign keys to it (as they are in production before the move)."""
    import migrate

    with open(os.path.join(migrate.MIGRATIONS_DIR, "000_baseline.sql")) as f:
        baseline = f.read()
    users_ddl_end = baseline.index("CREATE TABLE IF NOT EXISTS questions")
    tables = baseline[users_ddl_end:].replace("REFERENCES users(id)", "REFERENCES auth.users(id)")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CREATE SCHEMA auth; CREATE TABLE auth.users ("
            " id uuid PRIMARY KEY, email text, created_at timestamptz DEFAULT now(),"
            " last_sign_in_at timestamptz)"
        )
        conn.execute(tables)
