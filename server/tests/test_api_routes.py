import main as main_module

from conftest import TEST_USER_ID


# --- GET /api/activity/heatmap ---

def test_heatmap_route_returns_counts(client, monkeypatch):
    calls = {}

    def fake_heatmap(user_id):
        calls["user_id"] = user_id
        return {"2026-07-01": 3, "2026-07-02": 1}

    monkeypatch.setattr(main_module, "get_activity_heatmap", fake_heatmap)
    r = client.get("/api/activity/heatmap")
    assert r.status_code == 200
    assert r.json() == {"2026-07-01": 3, "2026-07-02": 1}
    assert calls["user_id"] == TEST_USER_ID


def test_heatmap_requires_auth():
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as anon:
        r = anon.get("/api/activity/heatmap")
    assert r.status_code == 401  # HTTPBearer rejects the missing header (401 since FastAPI 0.116)


# --- POST /api/questions/{qid}/cancel-attempt ---

def test_cancel_attempt_rolls_back(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "get_question", lambda uid, qid: {"id": qid})
    monkeypatch.setattr(main_module, "decrement_attempts", lambda uid, qid: calls.append(("dec", qid)))
    monkeypatch.setattr(main_module, "delete_latest_attempt_event", lambda uid, qid: calls.append(("del_event", qid)))

    r = client.post("/api/questions/42/cancel-attempt")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls == [("dec", 42), ("del_event", 42)]


def test_cancel_attempt_unknown_question_404(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_question", lambda uid, qid: None)
    r = client.post("/api/questions/999/cancel-attempt")
    assert r.status_code == 404


# --- DELETE /api/questions/{qid} ---

def test_delete_question_ok(client, monkeypatch):
    monkeypatch.setattr(main_module, "delete_question", lambda uid, qid: True)
    r = client.delete("/api/questions/42")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_question_unknown_404(client, monkeypatch):
    monkeypatch.setattr(main_module, "delete_question", lambda uid, qid: False)
    r = client.delete("/api/questions/999")
    assert r.status_code == 404


# --- PUT /api/profile (timezone) ---

def test_profile_accepts_valid_timezone(client, monkeypatch):
    saved = {}

    def fake_update(user_id, updates):
        saved.update(updates)
        return {"user_id": user_id, **updates}

    monkeypatch.setattr(main_module, "update_profile", fake_update)
    r = client.put("/api/profile", json={"timezone": "America/New_York"})
    assert r.status_code == 200
    assert saved["timezone"] == "America/New_York"


def test_profile_rejects_unknown_timezone(client, monkeypatch):
    def fail(uid, updates):
        raise AssertionError("update_profile must not be called")

    monkeypatch.setattr(main_module, "update_profile", fail)
    r = client.put("/api/profile", json={"timezone": "Mars/Olympus_Mons"})
    assert r.status_code == 400
    assert "timezone" in r.json()["detail"].lower()


# --- POST /api/questions/{qid}/review (FSRS + solution_source) ---

DEFAULT_SETTINGS = {"revision_queue_size": 20, "desired_retention": 0.9, "fsrs_params": None}

QUESTION_ROW = {
    "id": 42,
    "stability": 20.0,
    "fsrs_difficulty": 5.0,
    "fsrs_state": 2,
    "next_review": "2026-07-09",
    "last_reviewed": "2026-06-19T10:00:00Z",
    "solved_at": "2026-05-01T10:00:00Z",
    "interval": 20,
}


def _patch_review_deps(monkeypatch, captured):
    monkeypatch.setattr(main_module, "merge_duplicates_for_question", lambda uid, qid: qid)
    monkeypatch.setattr(main_module, "get_question", lambda uid, qid: dict(QUESTION_ROW))
    monkeypatch.setattr(main_module, "get_user_settings", lambda uid: dict(DEFAULT_SETTINGS))
    monkeypatch.setattr(
        main_module, "update_question_schedule",
        lambda uid, qid, data, set_reviewed=False, solution_source=None: captured.update(
            {"schedule": data, "set_reviewed": set_reviewed, "solution_source": solution_source}
        ),
    )
    monkeypatch.setattr(
        main_module, "insert_event",
        lambda uid, qid, event_type, **fields: captured.update({"event_type": event_type, "event": fields}),
    )


def test_review_records_solution_source(client, monkeypatch):
    captured = {}
    _patch_review_deps(monkeypatch, captured)

    r = client.post("/api/questions/42/review", json={"self_rating": 5, "solution_source": "solution"})
    assert r.status_code == 200
    assert captured["solution_source"] == "solution"
    assert captured["set_reviewed"] is True
    assert captured["event_type"] == "reviewed"
    assert captured["event"]["solution_source"] == "solution"
    # Saw-the-solution is a lapse: due again within two days despite 5 stars.
    assert captured["schedule"]["interval"] <= 2
    assert set(captured["schedule"]) >= {"stability", "fsrs_difficulty", "fsrs_state", "next_review", "interval"}


def test_review_defaults_solution_source_to_self(client, monkeypatch):
    """Old extension clients that don't send the field must keep working."""
    captured = {}
    _patch_review_deps(monkeypatch, captured)

    r = client.post("/api/questions/42/review", json={"self_rating": 4})
    assert r.status_code == 200
    assert captured["solution_source"] == "self"
    assert captured["schedule"]["interval"] > 7  # full credit for a Good review


def test_review_rejects_invalid_solution_source(client, monkeypatch):
    captured = {}
    _patch_review_deps(monkeypatch, captured)

    r = client.post("/api/questions/42/review", json={"self_rating": 4, "solution_source": "cheated"})
    assert r.status_code == 422
    assert captured == {}


# --- GET /api/revisions/today carries schedule_preview ---

def test_revisions_today_attaches_schedule_preview(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_user_settings", lambda uid: dict(DEFAULT_SETTINGS))
    monkeypatch.setattr(main_module, "count_revisions_done_today", lambda uid: 0)
    monkeypatch.setattr(main_module, "get_revisions_due", lambda uid, today, limit=None: [dict(QUESTION_ROW)])

    r = client.get("/api/revisions/today")
    assert r.status_code == 200
    items = r.json()
    preview = items[0]["schedule_preview"]
    assert set(preview) == {"self", "hint", "solution"}
    assert set(preview["self"]) == {"1", "2", "3", "4", "5"}
    # The lapse row always lands sooner than the honest 5-star row.
    assert preview["solution"]["5"]["days"] < preview["self"]["5"]["days"]


# --- PUT /api/settings (desired_retention) ---

def test_settings_roundtrip_desired_retention(client, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        main_module, "upsert_user_settings",
        lambda uid, data: (saved.update(data), {**DEFAULT_SETTINGS, **data})[1],
    )

    r = client.put("/api/settings", json={"desired_retention": 0.85})
    assert r.status_code == 200
    assert saved == {"desired_retention": 0.85}  # queue size untouched
    assert r.json()["desired_retention"] == 0.85


def test_settings_rejects_out_of_range_retention(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "upsert_user_settings",
        lambda uid, data: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    r = client.put("/api/settings", json={"desired_retention": 0.5})
    assert r.status_code == 422
