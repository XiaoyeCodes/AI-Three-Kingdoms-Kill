"""Explicit paid six-player integration run for the deep-decision release."""

import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werewolf.server import DEFAULT, DATA
from werewolf.game import ThreeKingdomsWerewolfGame

path = DATA / "deep-game-state.json"
g = (
    ThreeKingdomsWerewolfGame.restore(json.loads(path.read_text(encoding="utf-8")))
    if "--resume" in sys.argv
    else ThreeKingdomsWerewolfGame(size=6, human=0, mode="llm", seed=731)
)
started = time.monotonic()
while not g.winner and g.version < 300:
    ctx = g.context()
    g.advance(DEFAULT)
    path.write_text(
        json.dumps(g.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "step": g.version,
                "day": g.day,
                "kind": ctx["kind"],
                "seconds": g.debug_traces[-1]["duration_seconds"],
                "winner": g.winner,
            }
        ),
        flush=True,
    )
assert g.winner
report = {
    "complete": True,
    "size": 6,
    "mode": "deep",
    "winner": g.winner,
    "days": g.day,
    "steps": g.version,
    "usage": g.usage,
    "run_seconds": round(time.monotonic() - started, 2),
    "rebuttal_actions": sum(t["kind"] == "rebuttal" for t in g.debug_traces),
    "native_thinking_calls": sum(
        r["native_thinking"] for t in g.debug_traces for r in t["requests"]
    ),
}
(DATA / "deep-game-acceptance.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
(DATA / "deep-game-replay.json").write_text(g.export(), encoding="utf-8")
print(json.dumps(report), flush=True)
