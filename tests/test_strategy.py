import json
from types import SimpleNamespace
from dataclasses import replace
import pytest
from werewolf.agents import ModelConfig, ModelFailure, complete
from werewolf.game import ThreeKingdomsWerewolfGame
from werewolf.strategy import (
    clean_assessment,
    speech_similarity,
    expression_safe,
    validate_speech_references,
)
from werewolf.memory import EvidenceMemory


def game():
    return ThreeKingdomsWerewolfGame(size=9, human=0, mode="llm", seed=731)


def response(g):
    ctx = g.context()
    target = ctx["targets"][0]
    action = ctx["actions"][0]
    return {
        "assessment": {
            "position": "PRIVATE_POSITION",
            "revision": "PRIVATE_REVISION",
            "suspicions": [
                {
                    "seat": target,
                    "wolf_probability": 0.6,
                    "basis": "inference",
                    "note": "PRIVATE_SUSPICION",
                    "evidence_ids": [99999],
                }
            ],
            "alternatives": [
                {
                    "hypothesis": "另一个解释",
                    "weakness": "缺少公开发言",
                    "evidence_ids": [],
                }
            ],
            "confidence": 0.5,
            "move": {
                "action": action,
                "target": None if action == "speak" else target,
                "objective": "检验对手",
                "claim": "公开试探",
            },
        },
        "notebook": {"cover_story": "PRIVATE_COVER"},
    }


def test_deep_pipeline_uses_private_assessment_then_public_voice(monkeypatch):
    g = game()
    actor = g.context()["actor"]
    stages = []

    def fake(config, messages):
        stages.append(config.request_stage)
        if config.request_stage == "assessment":
            return json.dumps(response(g)), 100
        assert "PRIVATE_POSITION" in str(messages)
        return (
            json.dumps(
                {
                    "action": "speak",
                    "target": None,
                    "speech": "我倾向先观察四号如何回应，今晚先明确分工。",
                }
            ),
            20,
        )

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test"))
    assert stages == ["assessment", "expression"] and g.usage == {
        "calls": 2,
        "tokens": 120,
    }
    t = g.debug_traces[-1]
    assert t["assessment"]["suspicions"][0]["evidence_ids"] == []
    assert (
        t["decision_mode"] == "deep"
        and g.agents[actor].notebook["assessment"] == t["assessment"]
    )
    for a in g.agents.values():
        assert "PRIVATE_" not in str(a.memory.get_memory())
    restored = ThreeKingdomsWerewolfGame.restore(json.loads(json.dumps(g.snapshot())))
    assert restored.agents[actor].notebook == g.agents[actor].notebook


def test_deep_skill_executes_reviewed_target_without_expression_call(monkeypatch):
    g = game()
    g.queue = [{"kind": "wolf_vote", "actor": g.wolves()[0]}]
    stages = []

    def fake(config, messages):
        stages.append(config.request_stage)
        return json.dumps(response(g)), 20

    monkeypatch.setattr("werewolf.agents.complete", fake)
    expected = g.context()["targets"][0]
    g.advance(ModelConfig(api_key="test"))
    assert stages == ["assessment"] and g.debug_traces[-1]["action"] == {
        "action": "kill",
        "target": expected,
        "speech": "",
    }


def test_failed_expression_does_not_commit_private_or_public_state(monkeypatch):
    g = game()
    before = g.snapshot()
    actor = g.context()["actor"]

    def fake(config, messages):
        return (
            json.dumps(response(g))
            if config.request_stage == "assessment"
            else '{"action":"kill","target":99}'
        ), 20

    monkeypatch.setattr("werewolf.agents.complete", fake)
    with pytest.raises(ModelFailure):
        g.advance(ModelConfig(api_key="test"))
    assert (
        g.events == before["events"]
        and g.queue == before["queue"]
        and g.version == before["version"]
    )
    assert g.agents[actor].notebook == {} and g.debug_traces == []
    assert g.agents[actor].progress is None


def test_highly_repeated_speech_gets_one_rewrite(monkeypatch):
    g = game()
    text = "请四号解释昨天保护三号而今天却要求放逐三号的原因，我认为这两次立场无法同时成立。"
    g.emit("先前发言", text, kind="speech", actor=2)
    calls = []

    def fake(config, messages):
        calls.append(config.request_stage)
        if config.request_stage == "assessment":
            return json.dumps(response(g)), 20
        speech = (
            text
            if len(calls) == 2
            else "四号，你改票的依据只有别人态度强硬？我不会跟这一票，先让三号回答。"
        )
        return json.dumps({"action": "speak", "target": None, "speech": speech}), 20

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test"))
    assert calls == ["assessment", "expression", "expression"]
    assert g.debug_traces[-1]["quality"]["rewritten"]


@pytest.mark.parametrize(
    "mode,stage,thinking,budget",
    [
        ("deep", "assessment", True, 16384),
        ("deep", "expression", False, 2400),
        ("quick", "decision", False, 2400),
    ],
)
def test_provider_thinking_budget_and_metrics(
    monkeypatch, mode, stage, thinking, budget
):
    sent = {}

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok":true}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    total_tokens=100,
                    completion_tokens_details=SimpleNamespace(
                        reasoning_tokens=80 if thinking else 0
                    ),
                ),
            )

    monkeypatch.setattr("werewolf.agents.OpenAI", FakeClient)
    result = complete(
        ModelConfig(api_key="test", decision_mode=mode, request_stage=stage), []
    )
    assert sent["extra_body"]["thinking"]["type"] == (
        "enabled" if thinking else "disabled"
    )
    assert (
        sent["max_tokens"] == budget and result.metrics["native_thinking"] == thinking
    )
    assert ("reasoning_effort" in sent) == thinking


def test_day_has_public_rebuttal_before_sealed_vote():
    g = game()
    g.mode = "demo"
    g.queue = [{"kind": "day_start"}]
    g._settle()
    count = len(g.alive())
    for _ in range(count):
        assert g.context()["kind"] == "talk"
        g.advance()
    for _ in range(count):
        assert g.context()["kind"] == "rebuttal"
        g.advance()
    assert g.context()["kind"] == "vote"
    assert (
        len([e for e in g.events if (e.get("data") or {}).get("kind") == "rebuttal"])
        == count
    )


def test_assessment_rejects_invalid_probabilities_and_keeps_only_owned_evidence():
    g = game()
    archive = EvidenceMemory(g.agents[g.context()["actor"]].memory.get_memory())
    raw = response(g)
    raw["assessment"]["confidence"] = float("nan")
    with pytest.raises(ValueError):
        clean_assessment(raw, archive, g.context(), g.validate)
    raw = response(g)
    a, _ = clean_assessment(raw, archive, g.context(), g.validate)
    assert a["suspicions"][0]["evidence_ids"] == []


def test_discussion_context_distinguishes_not_yet_spoken_and_public_board():
    g = ThreeKingdomsWerewolfGame(size=6, human=0, mode="demo", seed=731)
    g.queue = [{"kind": "day_start"}]
    g._settle()
    ctx = g.context()
    assert ctx["board"]["starting_roles"] == {
        "wolf": 2,
        "villager": 2,
        "seer": 1,
        "witch": 1,
    }
    assert ctx["discussion"]["already_spoken"] == []
    assert ctx["discussion"]["waiting_for_turn"] == g.alive()
    actor = ctx["actor"]
    g.advance()
    assert g.context()["discussion"]["already_spoken"] == [actor]
    assert actor not in g.context()["discussion"]["waiting_for_turn"]
    assert not any("role" in p for p in ctx["players"])


def test_expression_removes_private_log_notation_and_rejects_nonexistent_seats():
    notes = {
        "position": "第12号消息中1号说3号好人，记录14中又改口。",
        "evidence_ids": [12, 14],
    }
    cleaned = expression_safe(notes)
    assert "12" not in str(cleaned) and "14" not in str(cleaned)
    assert "1号" in cleaned["position"] and "3号" in cleaned["position"]
    assert "evidence_ids" not in cleaned
    for speech in ["第9号，1号说3号好人。", "请12号解释。", "记录12证明你说谎。"]:
        with pytest.raises(ValueError):
            validate_speech_references(speech, 6)
    validate_speech_references("1号，你昨天说3号好人，今天却改了口。", 6)


def test_budget_exhaustion_never_silently_disables_thinking(monkeypatch):
    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=""), finish_reason="length"
                    )
                ]
            )

    monkeypatch.setattr("werewolf.agents.OpenAI", FakeClient)
    with pytest.raises(ModelFailure, match="预算"):
        complete(ModelConfig(api_key="test"), [])
    assert (
        len(requests) == 1
        and requests[0]["extra_body"]["thinking"]["type"] == "enabled"
    )
