import json

from werewolf.agents import ModelConfig, ROLE_STRATEGIES, PERSONALITY_STYLES
from werewolf.game import ThreeKingdomsWerewolfGame
from werewolf.memory import EvidenceMemory


def game():
    return ThreeKingdomsWerewolfGame(human=0, seed=42)


def test_every_speaker_every_round_keeps_index_and_full_original():
    g = game()
    expected = []
    for day in range(1, 21):
        g.day = day
        for seat in range(1, 10):
            text = (
                f"第{day}轮{seat}号的发言："
                + "这是需要核对的历史证词。" * 40
                + f"我最后的承诺是投给{seat % 9 + 1}号。"
            )
            g.emit(f"{seat}号", text, kind="speech", actor=seat)
            expected.append(g.events[-1])
    archive = EvidenceMemory(g.agents[1].memory.get_memory())
    packet, stats = archive.pack(g.context(), 1)
    all_indexed = [r for rows in packet["by_speaker_all_rounds"].values() for r in rows]
    assert all(e["id"] in {r["id"] for r in all_indexed} for e in expected)
    for event in expected:
        assert archive.recall([event["id"]])[0]["text"] == event["text"]
    assert stats["earliest_day"] == 0 and stats["latest_day"] == 20
    assert stats["exact_records"] < stats["archive_records"]
    assert stats["packet_chars"] < 60000


def test_private_record_cannot_be_recalled_from_another_agent():
    g = game()
    g.emit("秘密", "ONLY_PLAYER_2_KNOWS", [2], "private")
    secret_id = g.events[-1]["id"]
    assert EvidenceMemory(g.agents[1].memory.get_memory()).recall([secret_id]) == []
    assert (
        EvidenceMemory(g.agents[2].memory.get_memory()).recall([secret_id])[0]["text"]
        == "ONLY_PLAYER_2_KNOWS"
    )


def test_llm_can_retrieve_early_exact_text_then_decide_without_leaking_debug(
    monkeypatch,
):
    g = game()
    g.mode = "llm"
    actor = g.context()["actor"]
    other = next(i for i in g.alive() if i != actor)
    early_text = "我第一天的完整证词。" * 80 + "末尾的精确承诺：我不会投给三号。"
    g.emit("早期证人", early_text, kind="speech", actor=other)
    early_id = g.events[-1]["id"]
    g.emit("私有秘密", "FOREIGN_SECRET_NEVER_READ", [other], "private")
    foreign_id = g.events[-1]["id"]
    for n in range(100):
        g.emit(
            "后续证人",
            f"后续记录{n}。" + "观察到的发言。" * 70,
            kind="speech",
            actor=other,
        )
    calls = []

    def fake(config, messages):
        prompt = json.dumps(messages, ensure_ascii=False)
        assert "FOREIGN_SECRET_NEVER_READ" not in prompt
        calls.append(prompt)
        if len(calls) == 1:
            assert str(early_id) in prompt
            return json.dumps({"recall": [early_id, foreign_id]}), 10
        assert early_text in messages[-1]["content"]
        return (
            json.dumps(
                {
                    "action": "speak",
                    "target": None,
                    "speech": "我们按之前的计划行动。",
                    "decision_summary": {
                        "summary": "PRIVATE_DEBUG_SENTINEL",
                        "intent": "延续身份伪装",
                        "evidence_ids": [early_id, foreign_id],
                    },
                    "notebook": {
                        "cover_story": "PRIVATE_COVER_SENTINEL",
                        "plan": "保存早期承诺",
                        "beliefs": {"3": "推测：证词需要核验"},
                    },
                }
            ),
            20,
        )

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test-only", decision_mode="quick"))
    assert len(calls) == 2
    trace = g.debug_traces[-1]
    assert trace["evidence_ids"] == [early_id]
    assert trace["memory"]["retrieved_ids"] == [early_id]
    assert g.agents[actor].notebook["cover_story"] == "PRIVATE_COVER_SENTINEL"
    for agent in g.agents.values():
        routed = json.dumps(
            [
                {"content": m.content, "metadata": m.metadata}
                for m in agent.memory.get_memory()
            ],
            ensure_ascii=False,
        )
        assert (
            "PRIVATE_DEBUG_SENTINEL" not in routed
            and "PRIVATE_COVER_SENTINEL" not in routed
        )
    assert "PRIVATE_DEBUG_SENTINEL" not in json.dumps(g.events, ensure_ascii=False)
    assert "PRIVATE_COVER_SENTINEL" not in json.dumps(g.context(), ensure_ascii=False)


def test_notebook_and_debug_survive_restart_without_becoming_messages(monkeypatch):
    g = game()
    g.mode = "llm"
    actor = g.context()["actor"]

    def fake(*args):
        return (
            json.dumps(
                {
                    "action": "speak",
                    "target": None,
                    "speech": "保持谨慎。",
                    "decision_summary": {"summary": "PRIVATE_SUMMARY"},
                    "notebook": {
                        "cover_story": "PRIVATE_NOTE",
                        "beliefs": {"2": "推测：可疑"},
                    },
                }
            ),
            20,
        )

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test", decision_mode="quick"))
    restored = ThreeKingdomsWerewolfGame.restore(json.loads(json.dumps(g.snapshot())))
    assert restored.snapshot() == g.snapshot()
    assert restored.agents[actor].notebook == g.agents[actor].notebook
    assert all(
        "PRIVATE_NOTE" not in str(a.memory.get_memory())
        for a in restored.agents.values()
    )


def test_plain_human_view_hides_debug_and_unlock_does_not_change_agent_context():
    g = game()
    g.advance()
    g.human = 1
    assert g.view()["debug_traces"] == [] and not g.view()["debug_available"]
    before = g.context()
    memories = [str(a.memory.get_memory()) for a in g.agents.values()]
    g.debug_mode = True
    assert g.view()["debug_available"] and g.view()["debug_traces"]
    assert g.context() == before and memories == [
        str(a.memory.get_memory()) for a in g.agents.values()
    ]


def test_old_save_migrates_without_notebooks():
    g = game()
    data = g.snapshot()
    for key in ["debug_mode", "debug_traces", "agent_notebooks"]:
        data.pop(key)
    restored = ThreeKingdomsWerewolfGame.restore(data)
    assert not restored.debug_mode and restored.debug_traces == []
    assert all(a.notebook == {} for a in restored.agents.values())


def test_each_personality_and_role_strategy_is_injected():
    g = game()
    for p in g.players:
        prompt = g.agents[p["id"]].sys_prompt
        assert PERSONALITY_STYLES[p["name"]] in prompt
        assert ROLE_STRATEGIES[p["role"]] in prompt
    assert len(set(PERSONALITY_STYLES.values())) == 9
    assert "主动欺骗" in ROLE_STRATEGIES["wolf"] and "倒钩" in ROLE_STRATEGIES["wolf"]


def test_model_annotations_are_not_accepted_from_human_action():
    g = game()
    g.human = g.context()["actor"]
    g.advance(
        submitted={
            "action": "speak",
            "target": None,
            "speech": "正常发言",
            "notebook": {"cover_story": "INJECTED"},
            "decision_summary": {"summary": "INJECTED"},
        }
    )
    assert not g.debug_traces and g.agents[g.human].notebook == {}


def test_next_model_prompt_keeps_own_notebook_but_never_another_players(monkeypatch):
    g = game()
    g.mode = "llm"
    first_actor = g.context()["actor"]
    seen = set()
    revisited = []

    def fake(config, messages):
        ctx = g.context()
        actor = ctx["actor"]
        prompt = json.dumps(messages, ensure_ascii=False)
        for owner in seen:
            marker = f"UNIQUE_PRIVATE_PLAN_{owner}_END"
            assert (marker in prompt) == (owner == actor)
        if actor in seen:
            revisited.append(actor)
        seen.add(actor)
        action = ctx["actions"][0]
        target = None if action in {"speak", "pass", "abstain"} else ctx["targets"][0]
        return (
            json.dumps(
                {
                    "action": action,
                    "target": target,
                    "speech": "先核验公开信息再决定。",
                    "notebook": {"plan": f"UNIQUE_PRIVATE_PLAN_{actor}_END"},
                    "decision_summary": {"summary": "保持此前立场，等待证据。"},
                }
            ),
            10,
        )

    monkeypatch.setattr("werewolf.agents.complete", fake)
    for _ in range(10):
        g.advance(ModelConfig(api_key="test-only", decision_mode="quick"))
        if first_actor in revisited:
            break
    assert first_actor in revisited
