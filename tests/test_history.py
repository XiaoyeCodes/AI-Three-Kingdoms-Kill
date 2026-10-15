import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from werewolf import server
from werewolf.game import ThreeKingdomsWerewolfGame
from werewolf.replay import replay_view


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "history.sqlite3")
    server.SESSIONS.clear()
    with TestClient(server.app, headers={"X-Night-Council": "1"}) as c:
        c.get("/api/state")
        yield c


def create(client, human=0):
    return client.post("/api/games", json={"human": human, "mode": "demo"}).json()[
        "game"
    ]


def test_new_games_are_retained_and_resume_restores_exact_turn(client):
    first = create(client)
    progressed = client.post(
        "/api/step", json={"game_id": first["id"], "version": first["version"]}
    ).json()["game"]
    second = create(client)
    history = client.get("/api/history").json()
    assert history["current_id"] == second["id"]
    assert {g["id"] for g in history["games"]} == {first["id"], second["id"]}
    resumed = client.post(f"/api/history/{first['id']}/resume", json={}).json()["game"]
    assert resumed == progressed
    server.SESSIONS.clear()
    assert client.get("/api/state").json()["game"] == progressed
    assert len(client.get("/api/history").json()["games"]) == 2


def test_history_is_owned_by_session_and_does_not_replace_live_game(client):
    first, second = create(client), create(client)
    assert client.get(f"/api/history/{first['id']}/replay").status_code == 200
    assert client.get("/api/state").json()["game"]["id"] == second["id"]
    with TestClient(server.app, headers={"X-Night-Council": "1"}) as other:
        assert other.get("/api/history").json()["games"] == []
        assert other.get(f"/api/history/{first['id']}/replay").status_code == 404
        assert (
            other.post(f"/api/history/{first['id']}/resume", json={}).status_code == 404
        )


def test_legacy_session_migrates_once_without_changing_archive_timestamp(client):
    sid = hashlib.sha256(client.cookies["night_council"].encode()).hexdigest()
    g = ThreeKingdomsWerewolfGame(human=0, seed=5)
    with sqlite3.connect(server.DB) as db:
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?)", (sid, json.dumps(g.snapshot()), 1234)
        )
    server.SESSIONS.clear()
    history = client.get("/api/history").json()["games"]
    assert len(history) == 1 and history[0]["created"] == 1234
    client.get("/api/history")
    assert client.get("/api/state").json()["game"]["id"] == g.id
    assert client.get("/api/history").json()["games"] == history


def test_busy_and_finished_games_cannot_be_resumed(client):
    g = create(client)
    s = next(iter(server.SESSIONS.values()))
    with s.lock:
        assert client.post(f"/api/history/{g['id']}/resume", json={}).status_code == 409
    s.game.finish("good", "测试终局")
    s.save()
    assert client.post(f"/api/history/{g['id']}/resume", json={}).status_code == 409


def test_unfinished_human_replay_enforces_perspective_server_side(client):
    g = create(client, human=1)
    url = f"/api/history/{g['id']}/replay"
    assert client.get(url + "?perspective=all").status_code == 403
    assert client.get(url + "?perspective=2").status_code == 403
    assert client.get(url + "?perspective=1").status_code == 200
    assert client.get(url + "?perspective=99").status_code == 400
    assert client.get(url + "?cursor=-1").status_code == 400
    assert client.get(url + "?cursor=999999").status_code == 400
    assert client.get(url).json()["traces"] == []


@pytest.fixture
def finished():
    g = ThreeKingdomsWerewolfGame(human=0, seed=8)
    while not g.winner:
        g.advance()
    return g.snapshot()


def test_replay_does_not_leak_future_deaths_roles_or_result(finished):
    opening = replay_view(finished, "public", 1)
    assert all(p["alive"] and p["role"] is None for p in opening["players"])
    assert opening["winner"] is None and opening["traces"] == []
    assert len(opening["events"]) == 1
    assert all(set(e) == {"id", "day", "phase", "kind"} for e in opening["timeline"])
    assert not any("recipients" in e for e in opening["events"])
    for e in finished["events"]:
        if e["kind"] == "dawn" and e["data"]["deaths"]:
            before = replay_view(finished, "public", e["id"] - 1)
            after = replay_view(finished, "public", e["id"])
            for seat in e["data"]["deaths"]:
                assert before["players"][seat - 1]["alive"]
                assert not after["players"][seat - 1]["alive"]
    final = replay_view(finished, "all")
    assert final["winner"] == finished["winner"]
    assert [p["alive"] for p in final["players"]] == [
        p["alive"] for p in finished["players"]
    ]


def test_private_replay_uses_historical_trace_and_recipient_scoped_evidence(finished):
    actor = next(p["id"] for p in finished["players"] if p["role"] == "villager")
    trace = next(t for t in finished["debug_traces"] if t["actor"] == actor)
    other_secret = next(
        e
        for e in finished["events"]
        if not e["public"] and actor not in e["recipients"]
    )
    future = finished["events"][-1]
    trace["evidence_ids"] = [other_secret["id"], future["id"]]
    before = replay_view(finished, str(actor), trace["event_id"] - 1)
    after = replay_view(finished, str(actor), trace["event_id"])
    assert not before["traces"]
    assert len(after["traces"]) == 1 and not after["traces"][0]["evidence"]
    assert after["traces"][0]["notebook"] == trace["notebook"]
    assert sum(p["role"] is not None for p in after["players"]) == 1
    event_map = {e["id"]: e for e in finished["events"]}
    assert all(
        actor in event_map[e["id"]]["recipients"]
        for e in replay_view(finished, str(actor))["events"]
    )
    assert all(t["actor"] == actor for t in replay_view(finished, str(actor))["traces"])


def test_sealed_ballots_and_private_checks_stay_hidden_until_their_event(finished):
    ballots = [e for e in finished["events"] if e["kind"] == "ballot"]
    assert ballots
    for ballot in ballots:
        before = replay_view(finished, "public", ballot["id"] - 1)
        assert not any(e["id"] == ballot["id"] for e in before["events"])
        assert not any(e["kind"] == "action" for e in before["events"])
    assert all(e["public"] for e in replay_view(finished, "public")["events"])


def test_legacy_eliminations_reconstruct_same_roster(finished):
    expected = replay_view(finished, "all")["players"]
    for event in finished["events"]:
        if event["kind"] == "elimination":
            event["data"] = None
    assert replay_view(finished, "all")["players"] == expected


def test_memory_search_obeys_actor_cursor_and_session_permissions(client, finished):
    create(client)
    s = next(iter(server.SESSIONS.values()))
    s.game = ThreeKingdomsWerewolfGame.restore(finished)
    s.save()
    trace = finished["debug_traces"][4]
    cursor = trace["event_id"] - 1
    actor = trace["actor"]
    url = f"/api/history/{s.game.id}/memory/{actor}?cursor={cursor}"
    result = client.get(url).json()
    source = {e["id"]: e for e in finished["events"]}
    assert all(
        r["id"] <= cursor and actor in source[r["id"]]["recipients"]
        for r in result["records"]
    )
    assert client.get(url + "&q=NOT_A_REAL_QUOTE").json()["matched"] == 0
    assert client.get(url + "&day=99").json()["matched"] == 0
    human_game = create(client, human=1)
    assert (
        client.get(f"/api/history/{human_game['id']}/memory/2?cursor=1").status_code
        == 403
    )
    assert (
        client.get(f"/api/history/{human_game['id']}/memory/1?cursor=99999").status_code
        == 400
    )
