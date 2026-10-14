"""Explicit opt-in paid-provider acceptance: python scripts/live_acceptance.py --full.

Never prints keys, provider error bodies, prompts or hidden chain-of-thought.
"""

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from werewolf.server import DEFAULT, DATA
from werewolf.agents import complete, parse_json, ModelFailure
from werewolf.game import ThreeKingdomsWerewolfGame

start = time.monotonic()
raw, tokens = complete(
    DEFAULT,
    [
        {
            "role": "user",
            "content": "Return only a JSON object with field ok set to true.",
        }
    ],
)
assert parse_json(raw).get("ok") is True
print(
    json.dumps(
        {
            "connection": "passed",
            "model": DEFAULT.model,
            "tokens": tokens,
            "seconds": round(time.monotonic() - start, 2),
        }
    ),
    flush=True,
)
if "--full" not in sys.argv:
    sys.exit(0)
g = (
    ThreeKingdomsWerewolfGame.restore(
        json.loads((DATA / "live-game-state.json").read_text(encoding="utf-8"))
    )
    if "--resume" in sys.argv
    else ThreeKingdomsWerewolfGame(size=9, human=0, mode="llm", seed=731)
)
steps = g.version
try:
    while not g.winner and steps < 250:
        ctx = g.context()
        g.advance(DEFAULT)
        steps += 1
        (DATA / "live-game-state.json").write_text(
            json.dumps(g.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "step": steps,
                    "day": g.day,
                    "action": ctx["kind"],
                    "actor": ctx["actor"],
                    "alive": len(g.alive()),
                    "winner": g.winner,
                    "tokens": g.usage["tokens"],
                }
            ),
            flush=True,
        )
    assert g.winner, "Game did not finish within acceptance cap"
except ModelFailure as exc:
    print(json.dumps({"error": str(exc), "step": steps}, ensure_ascii=True), flush=True)
    sys.exit(1)
report = {
    "completed": True,
    "model": DEFAULT.model,
    "size": 9,
    "steps": steps,
    "winner": g.winner,
    "days": g.day,
    "seconds": round(time.monotonic() - start, 1),
    "usage": g.usage,
    "debug_traces": len(g.debug_traces),
    "summaries": sum(
        t["source"] == "llm" and t["summary"] != "模型未提供决策摘要。"
        for t in g.debug_traces
    ),
    "notebooks": sum(
        bool(a.notebook.get("plan") or a.notebook.get("cover_story"))
        for a in g.agents.values()
    ),
    "archive_records": {
        str(i): len(a.memory.get_memory()) for i, a in g.agents.items()
    },
}
(DATA / "live-acceptance.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
(DATA / "live-replay.json").write_text(g.export(), encoding="utf-8")
print(json.dumps(report), flush=True)
