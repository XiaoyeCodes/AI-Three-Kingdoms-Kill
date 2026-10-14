"""Paid regression against the local historical day-2 ballot; never edits its save.

This fixture requires the original nine-seat game with event 64's seer claim.
Only villager voters are replayed; no post-ballot records or notebooks are used.
One sampled vote per actor cannot establish a win-rate or causal effect.
"""

import json
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werewolf.game import ThreeKingdomsWerewolfGame
from werewolf.server import DEFAULT, DB, DATA
from werewolf.agents import ModelFailure


def historical_fixture():
    with sqlite3.connect(DB) as db:
        states = [
            json.loads(row[0])
            for row in db.execute("SELECT state FROM sessions ORDER BY updated DESC")
        ]
    for saved in states:
        events = saved["events"]
        if (
            len(saved["players"]) == 9
            and len(events) >= 65
            and events[63]["actor"] == 1
            and "4号" in events[63]["text"]
            and "7号" in events[63]["text"]
            and "预言家" in events[63]["text"]
        ):
            break
    else:
        raise RuntimeError("Historical fixture not found; no model call made")
    state = deepcopy(saved)
    state.update(
        day=2,
        phase="vote",
        human=0,
        winner=None,
        mode="llm",
        debug_mode=True,
        ballots={},
        pk=[],
        vote_round=0,
        error=None,
        usage={"calls": 0, "tokens": 0},
    )
    state["events"] = [e for e in events if e["id"] <= 65]
    state["debug_traces"] = [t for t in saved["debug_traces"] if t["event_id"] <= 65]
    state["agent_notebooks"] = {}
    for trace in state["debug_traces"]:
        state["agent_notebooks"][str(trace["actor"])] = deepcopy(trace["notebook"])
    for player in state["players"]:
        player["alive"] = player["id"] in {1, 5, 6, 7, 8, 9}
        player["revealed"] = player["id"] == 4
    # These private fields are not exposed to villager voters. Still reset future state.
    state.update(
        potions={"save": False, "poison": True},
        victim=4,
        poisoned=None,
        saved=False,
        shot_used=True,
        checks={"4": "good", "7": "good"},
        wolf_votes={},
    )
    return state


if __name__ == "__main__":
    base = historical_fixture()
    report = {
        "fixture": "original day 2, through event 65 only",
        "cases": [],
        "completed": False,
        "limitations": "One stochastic sample per villager; no enforced vote diversity or win-rate claim.",
    }
    (DATA / "vote-replay-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for seat in (5, 7, 8):
        assert base["players"][seat - 1]["role"] == "villager"
        state = deepcopy(base)
        state["queue"] = [{"kind": "vote", "actor": seat}]
        game = ThreeKingdomsWerewolfGame.restore(state)
        try:
            game.advance(DEFAULT)
        except ModelFailure as exc:
            report["error"] = str(exc)
            (DATA / "vote-replay-acceptance.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise SystemExit(str(exc)) from None
        trace = game.debug_traces[-1]
        report["cases"].append(
            {
                "seat": seat,
                "action": trace["action"],
                "summary": trace["summary"],
                "assessment": trace["assessment"],
                "requests": trace["requests"],
                "seconds": trace["duration_seconds"],
                "tokens": game.usage["tokens"],
            }
        )
        (DATA / "vote-replay-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (DATA / "vote-review-preview-state.json").write_text(
            json.dumps(game.snapshot(), ensure_ascii=False), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "seat": seat,
                    "target": trace["action"]["target"],
                    "seconds": trace["duration_seconds"],
                    "review": trace["assessment"]["vote_review"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    report["completed"] = True
    (DATA / "vote-replay-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
