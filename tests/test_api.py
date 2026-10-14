import json
import threading

import pytest
from fastapi.testclient import TestClient

from werewolf import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "test.sqlite3")
    monkeypatch.setattr(
        server,
        "DEFAULT",
        server.ModelConfig(
            "https://api.example.com", "test-model", "server-test-secret"
        ),
    )
    server.SESSIONS.clear()
    with TestClient(server.app, headers={"X-Night-Council": "1"}) as c:
        c.get("/api/state")
        yield c


def create(client, **kwargs):
    response = client.post("/api/games", json={"human": 0, "mode": "demo", **kwargs})
    assert response.status_code == 200
    return response.json()["game"]


def test_decision_mode_inherits_with_role_and_seat_configs(client):
    g = create(client)
    s = next(iter(server.SESSIONS.values()))
    seat = s.game.wolves()[0]
    payload = {
        "base_url": "https://api.example.com",
        "model": "test-model",
        "api_key": "",
        "decision_mode": "quick",
    }
    r = client.post("/api/settings", json={**payload, "scope": "role:wolf"})
    assert r.status_code == 200 and s.effective(seat).decision_mode == "quick"
    assert s.effective(seat).api_key == "server-test-secret"
    r = client.post(
        "/api/settings",
        json={**payload, "scope": f"seat:{seat}", "decision_mode": "deep"},
    )
    assert r.status_code == 200 and s.effective(seat).decision_mode == "deep"
    assert "server-test-secret" not in r.text
    assert (
        client.post(
            "/api/settings", json={**payload, "decision_mode": "invalid"}
        ).status_code
        == 422
    )


def test_busy_progress_discloses_stage_without_actor_or_private_conclusions(client):
    create(client, human=1)
    s = next(iter(server.SESSIONS.values()))
    a = s.game.agents[s.game.context()["actor"]]
    a.progress = {"stage": "assessment", "label": "独立评估证据与行动"}
    a.notebook = {"plan": "SECRET_PLAN"}
    with s.lock:
        result = client.get("/api/state")
    assert result.json() == {"busy": True, "game": None, "progress": a.progress}
    assert "SECRET_PLAN" not in result.text


def test_roundtrip_and_stale_turn(client):
    g = create(client)
    body = {"game_id": g["id"], "version": g["version"]}
    assert client.post("/api/step", json=body).status_code == 200
    assert client.post("/api/step", json=body).status_code == 409
    before = client.get("/api/state").json()["game"]
    server.SESSIONS.clear()
    after = client.get("/api/state").json()["game"]
    assert before == after


def test_session_cookie_is_http_only_and_fog_preserved(client):
    response = client.get("/api/health")
    g = create(client, human=1, human_role="villager")
    assert sum(x["role"] is not None for x in g["players"]) == 1
    assert "victim" not in g["pending"]
    with TestClient(server.app) as other:
        r = other.get("/api/state")
        assert r.json()["game"] is None
        assert (
            "HttpOnly" in r.headers["set-cookie"]
            and "SameSite=strict" in r.headers["set-cookie"]
        )


def test_keys_never_returned_or_persisted(client):
    create(client)
    r = client.post(
        "/api/settings",
        json={
            "scope": "global",
            "base_url": "https://api.example.com",
            "model": "a",
            "api_key": "VERY_SECRET_TEST_KEY",
        },
    )
    assert r.status_code == 200 and "VERY_SECRET_TEST_KEY" not in r.text
    assert "VERY_SECRET_TEST_KEY" not in client.get("/api/settings").text
    assert "VERY_SECRET_TEST_KEY" not in client.get("/api/replay").text
    with server.db_connect() as db:
        assert "VERY_SECRET_TEST_KEY" not in str(
            db.execute("SELECT * FROM sessions").fetchall()
        )


def test_no_secret_in_validation_errors(client):
    r = client.post(
        "/api/settings", json={"api_key": {"value": "VERY_SECRET_TEST_KEY"}}
    )
    assert r.status_code == 422 and "VERY_SECRET_TEST_KEY" not in r.text


def test_no_credential_inheritance_to_different_host(client):
    create(client)
    client.post(
        "/api/settings",
        json={
            "scope": "role:wolf",
            "base_url": "https://other.example.com",
            "model": "other",
        },
    )
    s = next(iter(server.SESSIONS.values()))
    assert s.effective(scope="role:wolf").api_key == ""
    client.post(
        "/api/settings",
        json={
            "scope": "role:seer",
            "base_url": "https://api.example.com",
            "model": "other",
        },
    )
    assert s.effective(scope="role:seer").api_key == "server-test-secret"


def test_role_and_seat_override_priority(client):
    g = create(client)
    s = next(iter(server.SESSIONS.values()))
    wolf = s.game.wolves()[0]
    for scope, model in [("role:wolf", "role-model"), (f"seat:{wolf}", "seat-model")]:
        client.post(
            "/api/settings",
            json={
                "scope": scope,
                "base_url": "https://api.example.com",
                "model": model,
            },
        )
    assert s.effective(wolf).model == "seat-model"


def test_cross_origin_blocked(client):
    assert (
        client.post(
            "/api/games", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/games", json={}, headers={"X-Night-Council": ""}).status_code
        == 403
    )
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_parallel_action_lock(client):
    g = create(client)
    s = next(iter(server.SESSIONS.values()))
    with s.lock:
        assert (
            client.post(
                "/api/step", json={"game_id": g["id"], "version": g["version"]}
            ).status_code
            == 409
        )
        assert client.get("/api/state").json() == {
            "game": None,
            "busy": True,
            "progress": None,
        }


def test_export_respects_current_visibility(client):
    g = create(client, human=1, human_role="villager")
    r = client.get("/api/replay")
    assert r.status_code == 200
    data = r.json()
    assert sum(x["role"] is not None for x in data["players"]) == 1
    assert "当前视角" in data["export_note"]


def test_ended_and_new_game_ids_cannot_accept_old_actions(client):
    old = create(client)
    new = create(client)
    assert (
        client.post(
            "/api/step", json={"game_id": old["id"], "version": new["version"]}
        ).status_code
        == 409
    )


def test_static_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "三国" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_no_silent_fallback_on_llm_failure(client, monkeypatch):
    g = create(client, mode="llm")

    def fail(*a, **kw):
        raise server.ModelFailure("模型不可达")

    monkeypatch.setattr("werewolf.agents.complete", fail)
    r = client.post("/api/step", json={"game_id": g["id"], "version": g["version"]})
    assert r.status_code == 502
    state = client.get("/api/state").json()["game"]
    assert state["mode"] == "llm" and state["version"] == g["version"]
    assert (
        client.post(
            "/api/practice", json={"game_id": g["id"], "version": g["version"]}
        ).json()["game"]["mode"]
        == "demo"
    )


def test_debug_gate_and_memory_scope(client):
    g = create(client, human=1, human_role="villager")
    assert client.get("/api/memory/2").status_code == 403
    s = next(iter(server.SESSIONS.values()))
    s.game.emit("private", "SEAT_TWO_PRIVATE", [2], "private")
    s.game.emit("private", "SEAT_THREE_PRIVATE", [3], "private")
    before = [str(a.memory.get_memory()) for a in s.game.agents.values()]
    response = client.post(
        "/api/debug", json={"game_id": g["id"], "version": g["version"]}
    )
    assert response.status_code == 200
    assert (
        response.json()["game"]["debug_mode"]
        and response.json()["game"]["debug_available"]
    )
    assert before == [str(a.memory.get_memory()) for a in s.game.agents.values()]
    result = client.get("/api/memory/2?q=PRIVATE").json()
    assert result["matched"] == 1 and result["records"][0]["text"] == "SEAT_TWO_PRIVATE"
    server.SESSIONS.clear()
    assert client.get("/api/state").json()["game"]["debug_mode"]
