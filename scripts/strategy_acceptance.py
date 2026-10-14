"""Explicit paid, paired evaluation. Stores final outputs only, never raw reasoning or keys."""

import json
import sys
from pathlib import Path
from dataclasses import replace
from itertools import combinations
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werewolf.server import DEFAULT, DATA
from werewolf.game import ThreeKingdomsWerewolfGame


def scenario(seat, role="villager", kind="rebuttal"):
    g = ThreeKingdomsWerewolfGame(
        size=9, human=seat, human_role=role, mode="llm", seed=731
    )
    g.human = 0
    g.day = 1
    g.phase = "day"
    g.emit(
        "1号·曹操",
        "我跳预言家，昨晚查验3号是好人。我会优先保护3号。",
        kind="speech",
        actor=1,
    )
    first = g.events[-1]["id"]
    g.emit(
        "8号·周瑜",
        "我暂时信1号。3号今天还没说话，我没有其他证据。",
        kind="speech",
        actor=8,
    )
    g.day = 2
    g.emit(
        "1号·曹操",
        "纠正一下，我第一夜验的是3号狼人，昨天也是这样报的。今天一起投3号。",
        kind="speech",
        actor=1,
    )
    second = g.events[-1]["id"]
    g.emit(
        "3号·孙权",
        "1号昨天明明说我是好人，今天却说第一夜是查杀。请其他人对照原话，不要把没发生的票型编出来。",
        kind="speech",
        actor=3,
    )
    g.emit(
        "8号·周瑜", "我还是跟1号投3号，强势的预言家比较可信。", kind="speech", actor=8
    )
    if role == "seer":
        g.checks = {"1": "wolf"}
        g.emit("查验结果", "1号曹操的阵营是狼人。", [seat], "private")
    if role == "wolf":
        g.agents[seat].notebook = {
            "cover_story": "此前公开自称普通平民，未报过查验。",
            "plan": "保持装民，利用对跳引导好人争论。",
        }
    g.queue = [{"kind": kind, "actor": seat}]
    return g, [first, second]


report = {
    "cases": [],
    "note": "Paired fixtures, one sample per persona; no win-rate claim.",
}
for mode in ["quick", "deep"]:
    for seat in [2, 4, 6, 7]:
        g, evidence = scenario(seat)
        g.advance(replace(DEFAULT, decision_mode=mode))
        t = g.debug_traces[-1]
        row = {
            "mode": mode,
            "seat": seat,
            "name": g.player(seat)["name"],
            "speech": t["action"]["speech"],
            "summary": t["summary"],
            "cited_ids": t["evidence_ids"],
            "contradiction_ids": evidence,
            "seconds": t["duration_seconds"],
            "requests": t["requests"],
            "tokens": g.usage["tokens"],
            "assessment": t.get("assessment"),
        }
        report["cases"].append(row)
        (DATA / "strategy-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "mode": mode,
                    "seat": seat,
                    "seconds": row["seconds"],
                    "tokens": row["tokens"],
                    "cites": row["cited_ids"],
                }
            ),
            flush=True,
        )
for role, seat, kind in [("seer", 4, "vote"), ("wolf", 7, "rebuttal")]:
    g, evidence = scenario(seat, role, kind)
    g.advance(replace(DEFAULT, decision_mode="deep"))
    t = g.debug_traces[-1]
    report[role] = {
        "action": t["action"],
        "summary": t["summary"],
        "assessment": t["assessment"],
        "seconds": t["duration_seconds"],
        "requests": t["requests"],
        "tokens": g.usage["tokens"],
    }
    if role == "seer":
        assert (
            t["action"]["action"] == "vote" and t["action"]["target"] == 1
        ), "Seer ignored verified wolf"
    (DATA / "strategy-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "role": role,
                "seconds": t["duration_seconds"],
                "tokens": g.usage["tokens"],
            }
        ),
        flush=True,
    )
for mode in ["quick", "deep"]:
    rows = [r for r in report["cases"] if r["mode"] == mode]
    report[mode + "_metrics"] = {
        "max_pair_similarity": round(
            max(
                SequenceMatcher(None, a["speech"], b["speech"]).ratio()
                for a, b in combinations(rows, 2)
            ),
            3,
        ),
        "mean_seconds": round(sum(r["seconds"] for r in rows) / len(rows), 2),
        "both_contradiction_citations": sum(
            set(r["contradiction_ids"]) <= set(r["cited_ids"]) for r in rows
        ),
        "tokens": sum(r["tokens"] for r in rows),
    }
report["completed"] = True
(DATA / "strategy-acceptance.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "completed": True,
            "quick": report["quick_metrics"],
            "deep": report["deep_metrics"],
        }
    ),
    flush=True,
)
