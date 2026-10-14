import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from agentscope.agents import DialogAgent

from werewolf.agents import ModelConfig, ModelFailure, parse_json
from werewolf.game import ThreeKingdomsWerewolfGame, IllegalAction


def make_game(size=9, human=0, seed=8):
    return ThreeKingdomsWerewolfGame(size=size, human=human, seed=seed)


def seat(g, role):
    return next(p["id"] for p in g.players if p["role"] == role)


def pending(g, kind, actor):
    g.queue = [{"kind": kind, "actor": actor}]


def submit(g, action, target=None, speech=""):
    g.human = g.queue[0]["actor"]
    g.advance(submitted={"action": action, "target": target, "speech": speech})


def test_real_dialog_agents_and_msghub_secrecy():
    g = make_game()
    assert all(isinstance(a, DialogAgent) for a in g.agents.values())
    wolves = g.wolves()
    g.router.broadcast(wolves, "秘密", "NIGHT_SECRET_ABC")
    for i, a in g.agents.items():
        assert any(
            "NIGHT_SECRET_ABC" in str(m.content) for m in a.memory.get_memory()
        ) == (i in wolves)
    # Exiting a wolf hub must remove its audience before entering a public scope.
    actor = wolves[0]
    pending(g, "wolf_chat", actor)
    submit(g, "speak", speech="ONLY_WOLVES_SEE_THIS")
    for i, a in g.agents.items():
        assert any(
            "ONLY_WOLVES_SEE_THIS" in str(m.content) for m in a.memory.get_memory()
        ) == (i in wolves)
    assert all(not a._audience for a in g.agents.values())


def test_private_view_never_sends_hidden_roles_or_actor():
    g = make_game(human=1)
    g.human = seat(g, "villager")
    view = g.view()
    assert all(p["role"] is None for p in view["players"] if p["id"] != g.human)
    assert view["pending"] == {"label": "夜间行动中", "kind": "hidden", "actor": None}
    assert all(e["public"] or e["title"] == "身份" for e in view["events"])
    assert "recipients" not in json.dumps(view)
    assert "wolf_votes" not in view and "checks" not in view and "victim" not in view


def test_wolf_only_knows_wolf_teammates():
    g = make_game()
    g.human = g.wolves()[0]
    for p in g.view()["players"]:
        assert p["role"] == ("wolf" if g.player(p["id"])["role"] == "wolf" else None)


def test_votes_are_sealed_until_tally():
    g = make_game()
    g.phase = "vote"
    g.queue = [{"kind": "vote", "actor": x} for x in g.alive()]
    voter = g.queue[0]["actor"]
    submit(g, "vote", 2)
    g.human = 3
    view = g.view()
    assert not any(
        e["kind"] == "action" and e["actor"] == voter for e in view["events"]
    )
    memory = "\n".join(str(m.content) for m in g.agents[3].memory.get_memory())
    assert '"action": "vote"' not in memory


@pytest.mark.parametrize(
    "action,target",
    [
        ("shoot", 2),
        ("vote", 1),
        ("vote", 999),
        ("vote", True),
        ("vote", "2"),
        ("abstain", 2),
    ],
)
def test_invalid_actions_do_not_mutate(action, target):
    g = make_game(human=1)
    pending(g, "vote", 1)
    before = g.snapshot()
    with pytest.raises(IllegalAction):
        submit(g, action, target)
    assert g.snapshot() == before


def test_witch_single_use_and_self_save_first_night_only():
    g = make_game()
    witch = seat(g, "witch")
    g.victim = witch
    pending(g, "witch", witch)
    assert "save" in g.context()["actions"]
    submit(g, "save", witch)
    assert g.saved and not g.potions["save"]
    pending(g, "witch", witch)
    assert "save" not in g.context()["actions"] and g.context()["victim"] is None
    g.potions["save"] = True
    g.day = 2
    assert "save" not in g.context()["actions"]
    with pytest.raises(IllegalAction):
        submit(g, "save", witch)


def test_poison_and_knife_hunter_has_no_shot_even_if_saved():
    g = make_game()
    hunter = seat(g, "hunter")
    g.victim = g.poisoned = hunter
    g.saved = True
    g.queue = [{"kind": "dawn"}]
    g._settle()
    assert not g.player(hunter)["alive"]
    assert g.player(hunter)["death"]["cause"] == "poison"
    assert not any(t["kind"] == "hunter" for t in g.queue)


def test_night_actions_finish_before_death():
    g = make_game()
    seer = seat(g, "seer")
    g.victim = seer
    g.queue = [{"kind": "seer", "actor": seer}, {"kind": "dawn"}]
    target = g.wolves()[0]
    submit(g, "check", target)
    assert g.checks[str(target)] == "wolf"
    assert not g.player(seer)["alive"]


def test_hunter_gets_shot_before_victory_check():
    g = make_game()
    hunter = seat(g, "hunter")
    for p in g.players:
        if p["role"] in {"seer", "witch"}:
            g._kill(p["id"], "vote")
    g.victim = hunter
    g.day = 2
    g.queue = [{"kind": "dawn"}]
    g._settle()
    assert g.winner is None and g.context()["kind"] == "hunter"
    target = g.wolves()[0]
    submit(g, "shoot", target)
    assert not g.player(target)["alive"] and g.shot_used
    assert g.winner == "wolf"


def test_hunter_poison_loses_last_god_without_shot():
    g = make_game()
    hunter = seat(g, "hunter")
    for p in g.players:
        if p["role"] in {"seer", "witch"}:
            g._kill(p["id"], "vote")
    g.poisoned = hunter
    g.day = 2
    g.queue = [{"kind": "dawn"}]
    g._settle()
    assert g.winner == "wolf" and not g.shot_used


def test_seer_receives_alignment_not_role():
    g = make_game()
    seer = seat(g, "seer")
    target = seat(g, "witch")
    pending(g, "seer", seer)
    submit(g, "check", target)
    assert g.checks[str(target)] == "good"
    assert "女巫" not in g.events[-1]["text"]
    assert g.events[-1]["recipients"] == [seer]


def test_pk_tie_then_second_tie_no_elimination():
    g = make_game()
    g.phase = "vote"
    g.ballots = {1: 2, 2: 1, 3: 1, 4: 2, 5: None, 6: None, 7: None, 8: None, 9: None}
    g.queue = [{"kind": "tally"}]
    g._settle()
    assert g.pk == [1, 2] and g.context()["kind"] == "pk_talk"
    submit(g, "speak", speech="我的辩论")
    submit(g, "speak", speech="我的辩论")
    assert all(t.get("actor") not in [1, 2] for t in g.queue)
    assert g.context()["targets"] == [1, 2]
    g.ballots = {3: 1, 4: 2}
    g.queue = [{"kind": "tally"}]
    g._settle()
    assert g.day == 2 and len(g.alive()) == 9


def test_all_abstain_skips_pk():
    g = make_game()
    g.ballots = {1: None, 2: None}
    g.queue = [{"kind": "tally"}]
    g._settle()
    assert g.day == 2 and g.context()["kind"] == "wolf_chat"


def test_win_is_side_elimination_not_parity():
    g = make_game()
    for p in g.players:
        if p["role"] in {"witch", "hunter"} or (
            p["role"] == "villager" and p["id"] != seat(g, "villager")
        ):
            g._kill(p["id"], "vote")
    assert len(g.wolves()) >= len(g.alive()) - len(g.wolves())
    assert g.check_winner() is None
    g._kill(seat(g, "villager"), "vote")
    assert g.check_winner() == "wolf"


def test_good_wins_when_no_wolves_remain():
    g = make_game()
    for i in g.wolves():
        g._kill(i, "vote")
    assert g.check_winner() == "good"


def test_all_dead_draw():
    g = make_game()
    for i in g.alive():
        g._kill(i, "poison")
    assert g.check_winner() == "draw"


def test_dead_human_retains_fog_until_end():
    g = make_game()
    g.human = seat(g, "villager")
    g._kill(g.human, "wolf")
    assert not g.view()["omniscient"]
    g.finish("wolf", "测试终局")
    assert all(p["role"] for p in g.view()["players"])


def test_round_limit_draw():
    g = make_game()
    g.day = 20
    g.queue = [{"kind": "night_start"}]
    g._settle()
    assert g.winner == "draw"


def test_snapshot_restore_preserves_next_action_and_scoped_memories():
    g = make_game()
    for _ in range(15):
        g.advance()
    other = ThreeKingdomsWerewolfGame.restore(json.loads(json.dumps(g.snapshot())))
    assert other.view() == g.view()
    for i in g.agents:
        assert [m.content for m in other.agents[i].memory.get_memory()] == [
            m.content for m in g.agents[i].memory.get_memory()
        ]
    g.advance()
    other.advance()
    assert g.snapshot() == other.snapshot()


@pytest.mark.parametrize("size", [6, 9])
def test_twenty_complete_practice_games(size):
    for seed in range(20):
        g = make_game(size=size, seed=seed)
        for _ in range(800):
            if g.winner:
                break
            g.advance()
        assert g.winner in {"wolf", "good", "draw"}
        assert not g.queue


def test_model_failure_keeps_turn_and_does_not_broadcast(monkeypatch):
    g = make_game()
    g.mode = "llm"
    before = g.snapshot()
    memories = {i: len(a.memory.get_memory()) for i, a in g.agents.items()}

    def fail(*args):
        raise ModelFailure("测试超时")

    monkeypatch.setattr("werewolf.agents.complete", fail)
    with pytest.raises(ModelFailure):
        g.advance(ModelConfig(api_key="test-only", decision_mode="quick"))
    assert (
        g.queue == before["queue"]
        and g.version == before["version"]
        and g.events == before["events"]
    )
    assert memories == {i: len(a.memory.get_memory()) for i, a in g.agents.items()}


def test_bad_model_json_retried_once_then_valid(monkeypatch):
    g = make_game()
    g.mode = "llm"
    calls = []

    def fake(config, messages):
        calls.append(messages)
        return (
            (
                "not JSON"
                if len(calls) == 1
                else '{"action":"speak","target":null,"speech":"我们先商量刀口。"}'
            ),
            12,
        )

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test-only", decision_mode="quick"))
    assert g.version == 1 and g.usage == {"calls": 2, "tokens": 24}


def test_json_fences_and_non_objects():
    assert parse_json('```json\n{"ok":true}\n```') == {"ok": True}
    with pytest.raises(ValueError):
        parse_json("[]")


def test_two_parallel_rooms_do_not_share_private_messages():
    def play(seed):
        g = make_game(seed=seed)
        g.router.broadcast([1], "private", f"ROOM_SECRET_{seed}")
        for _ in range(3):
            g.advance()
        return g

    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(play, [41, 42]))
    assert "ROOM_SECRET_42" not in str(a.agents[1].memory.get_memory())
    assert "ROOM_SECRET_41" not in str(b.agents[1].memory.get_memory())


def test_restore_after_dead_players_last_words():
    g = make_game()
    while not g.winner:
        g.advance()
    restored = ThreeKingdomsWerewolfGame.restore(json.loads(json.dumps(g.snapshot())))
    for i in g.agents:
        assert [m.content for m in restored.agents[i].memory.get_memory()] == [
            m.content for m in g.agents[i].memory.get_memory()
        ]
