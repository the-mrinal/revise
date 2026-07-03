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
    assert r.status_code == 403  # HTTPBearer rejects the missing header


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
