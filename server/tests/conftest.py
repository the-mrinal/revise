"""Test bootstrap.

database.py reads Supabase env vars at import time, so they must be stubbed
before any test module imports `main` or `database`. get_client() is lazy —
no network call happens unless a query actually runs, and tests must never
let one run (patch the db functions on `main`, which imports them by name).
"""

import os
import sys

os.environ.setdefault("SUPABASE_URL", "http://supabase.test.invalid")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

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
