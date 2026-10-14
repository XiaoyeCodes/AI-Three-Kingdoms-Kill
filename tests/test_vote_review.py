import json
from copy import deepcopy

import pytest

from werewolf.agents import ModelConfig, ModelFailure
from werewolf.game import ThreeKingdomsWerewolfGame
from werewolf.memory import EvidenceMemory
from werewolf.strategy import clean_vote_review, vote_evidence


def setup_vote():
    g = ThreeKingdomsWerewolfGame(
        size=6, human=1, human_role="villager", mode="llm", seed=731
    )
    g.human = 0
    g.day = 2
    g.phase = "vote"
    g.emit("2号", "我认为3号可疑，因为他昨天弃票。", kind="speech", actor=2)
    g.emit(
        "3号", "弃票不代表狼人。我是预言家，第一夜查验1号好人。", kind="speech", actor=3
    )
    g.queue = [{"kind": "vote", "actor": 1}, {"kind": "vote", "actor": 4}]
    return g


def review(ctx):
    return {
        "candidates": [
            {
                "target": s,
                "case_for": "证据不足",
                "case_against": "误杀有风险",
                "evidence_ids": [],
            }
            for s in ctx["targets"] + [None]
        ],
        "source_audit": "PRIVATE_SOURCE_AUDIT：2号对弃票的解释是推断",
        "latest_defense": "3号的新身份声明需要核对，不能忽略",
        "decision_reason": "没有充分证据，暂时弃票",
        "change_condition": "出现矛盾证词或可靠查验",
    }


def answer(g):
    return {
        "assessment": {
            "position": "证据不足",
            "revision": "新身份声明增加了误杀代价",
            "suspicions": [
                {
                    "seat": 2,
                    "wolf_probability": 0.4,
                    "basis": "inference",
                    "note": "带票没有独立证据",
                    "evidence_ids": [],
                }
            ],
            "alternatives": [
                {
                    "hypothesis": "2号也可能是误判的好人",
                    "weakness": "未知阵营",
                    "evidence_ids": [],
                }
            ],
            "move": {
                "action": "abstain",
                "target": None,
                "objective": "避免缺乏证据的放逐",
            },
            "confidence": 0.3,
            "vote_review": review(g.context()),
        }
    }


@pytest.mark.parametrize(
    "defect", ["missing_candidate", "missing_defense", "duplicate", "boolean_target"]
)
def test_rejects_incomplete_or_invalid_candidate_review(defect):
    g = setup_vote()
    value = review(g.context())
    if defect == "missing_candidate":
        value["candidates"].pop(0)
    if defect == "missing_defense":
        value["candidates"][0]["case_against"] = ""
    if defect == "duplicate":
        value["candidates"].append(deepcopy(value["candidates"][0]))
    if defect == "boolean_target":
        value["candidates"][0]["target"] = True
    with pytest.raises(ValueError):
        clean_vote_review(value, EvidenceMemory([]), g.context())


def test_pk_only_compares_eligible_candidates_and_abstention():
    g = setup_vote()
    g.pk = [2, 3]
    g.queue = [{"kind": "pk_vote", "actor": 1}]
    result = clean_vote_review(review(g.context()), EvidenceMemory([]), g.context())
    assert [r["target"] for r in result["candidates"]] == [2, 3, None]


def test_vote_evidence_retains_early_and_late_speech_without_private_leaks():
    g = setup_vote()
    for _ in range(24):
        g.emit("4号", "重复追问", kind="speech", actor=4)
    g.emit("秘密", "OTHER_PLAYER_SECRET", [5], "private")
    archive = EvidenceMemory(g.agents[1].memory.get_memory())
    packet = vote_evidence(archive, g.context())
    assert "第一夜查验1号好人" in str(packet)
    assert "OTHER_PLAYER_SECRET" not in str(packet)
    assert [x["seat"] for x in packet["speakers"]] == g.alive()


def test_sealed_votes_and_reviews_never_reach_next_voter(monkeypatch):
    g = setup_vote()
    before = str(g.agents[4].memory.get_memory())

    def fake(config, messages):
        packet = json.loads(messages[1]["content"])
        assert "第一夜查验1号好人" in str(packet["vote_evidence"])
        assert "多人重复同一个指控不是多份独立证据" in messages[0]["content"]
        return json.dumps(answer(g)), 40

    monkeypatch.setattr("werewolf.agents.complete", fake)
    g.advance(ModelConfig(api_key="test"))
    assert g.ballots == {1: None}
    assert str(g.agents[4].memory.get_memory()) == before
    assert "ballots" not in g.context()
    assert "PRIVATE_SOURCE_AUDIT" not in str(g.agents[4].notebook)
    assert g.debug_traces[-1]["assessment"]["vote_review"]["source_audit"].startswith(
        "PRIVATE_"
    )
    restored = ThreeKingdomsWerewolfGame.restore(g.snapshot())
    assert restored.agents[1].notebook == g.agents[1].notebook


def test_invalid_vote_review_does_not_cast_ballot(monkeypatch):
    g = setup_vote()
    raw = answer(g)
    del raw["assessment"]["vote_review"]
    monkeypatch.setattr("werewolf.agents.complete", lambda *a: (json.dumps(raw), 20))
    before = g.snapshot()
    with pytest.raises(ModelFailure):
        g.advance(ModelConfig(api_key="test"))
    assert g.ballots == {} and g.events == before["events"]
    assert g.agents[1].notebook == before["agent_notebooks"]["1"]


def test_payment_error_is_clear_without_disclosing_provider_body(monkeypatch):
    import httpx
    from openai import APIStatusError
    from werewolf.agents import complete

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            raise APIStatusError(
                "provider-secret",
                response=httpx.Response(
                    402, request=httpx.Request("POST", "https://example.test")
                ),
                body={"detail": "provider-secret"},
            )

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("werewolf.agents.OpenAI", Client)
    with pytest.raises(ModelFailure, match="余额") as exc:
        complete(ModelConfig(api_key="test"), [])
    assert "provider-secret" not in str(exc.value)
