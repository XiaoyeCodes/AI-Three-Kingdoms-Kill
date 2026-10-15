"""Read-only, time-bounded replay projections. Never restore agents to browse a game."""

from copy import deepcopy
import re


def history_summary(saved, created, updated):
    human = saved.get("human", 0)
    player = next((p for p in saved["players"] if p["id"] == human), None)
    return {
        "id": saved["id"],
        "size": len(saved["players"]),
        "mode": saved["mode"],
        "human": human,
        "day": saved["day"],
        "phase": saved["phase"],
        "winner": saved.get("winner"),
        "created": created,
        "updated": updated,
        "events": sum(e["public"] for e in saved["events"]),
        "role": player["role"] if player else None,
        "debug_mode": saved.get("debug_mode", False),
    }


def replay_view(saved, perspective="public", cursor=None):
    players = saved["players"]
    full_access = (
        not saved.get("human") or saved.get("winner") or saved.get("debug_mode")
    )
    seat = None
    if perspective not in ("public", "all"):
        if not perspective.isdigit() or int(perspective) not in [
            p["id"] for p in players
        ]:
            raise ValueError("perspective")
        seat = int(perspective)
    if not full_access and (perspective == "all" or (seat and seat != saved["human"])):
        raise PermissionError("perspective")
    events = saved["events"]
    last_id = events[-1]["id"] if events else 0
    cursor = last_id if cursor is None else cursor
    if not 0 <= cursor <= last_id:
        raise ValueError("cursor")

    def visible(e):
        if perspective == "all":
            return True
        if seat:
            return seat in e.get("recipients", [])
        return e["public"]

    permitted = [e for e in events if visible(e)]
    past = [e for e in events if e["id"] <= cursor]
    viewed = [deepcopy(e) for e in permitted if e["id"] <= cursor]
    for e in viewed:
        e.pop("recipients", None)
    deaths, revealed = set(), set()
    for e in past:
        # Status is reconstructed from public announcements, never final player state.
        if not e["public"]:
            continue
        data = e.get("data") or {}
        if e["kind"] == "dawn":
            deaths.update(data.get("deaths", []))
        if e["kind"] == "elimination":
            if "deaths" in data:
                deaths.update(data["deaths"])
            else:  # Compatibility for previously recorded games.
                pattern = r"带走了(\d+)号" if e["title"] == "猎人开枪" else r"^(\d+)号"
                match = re.search(pattern, e["text"])
                if match:
                    deaths.add(int(match[1]))
        if e["title"] in ("猎人开枪", "猎人收枪"):
            match = re.match(r"(\d+)号", e["text"])
            if match:
                revealed.add(int(match[1]))
    own = next((p for p in players if p["id"] == seat), None)
    known_reveals = {
        p
        for p in revealed
        if any(
            e["title"] in ("猎人开枪", "猎人收枪") and e["text"].startswith(f"{p}号")
            for e in viewed
        )
    }
    roster = [
        {
            "id": p["id"],
            "name": p["name"],
            "trait": p["trait"],
            "alive": p["id"] not in deaths,
            "role": (
                p["role"]
                if perspective == "all"
                or p["id"] == seat
                or p["id"] in known_reveals
                or (own and own["role"] == "wolf" and p["role"] == "wolf")
                else None
            ),
        }
        for p in players
    ]
    traces = []
    for original in saved.get("debug_traces", []):
        if original["event_id"] > cursor or perspective == "public":
            continue
        if seat and original["actor"] != seat:
            continue
        trace = deepcopy(original)
        # Evidence was available to this actor before their action, not merely to the viewer.
        trace["evidence"] = [
            {k: deepcopy(v) for k, v in e.items() if k != "recipients"}
            for e in events
            if e["id"] in trace.get("evidence_ids", [])
            and e["id"] < trace["event_id"]
            and trace["actor"] in e.get("recipients", [])
        ]
        traces.append(trace)
    perspectives = [{"value": "public", "label": "公开视角"}]
    if full_access:
        perspectives.append({"value": "all", "label": "全知视角"})
    for p in players:
        if full_access or p["id"] == saved.get("human"):
            perspectives.append(
                {"value": str(p["id"]), "label": f"{p['id']}号 · {p['name']}"}
            )
    current = past[-1] if past else None
    return {
        "id": saved["id"],
        "mode": saved["mode"],
        "human": saved.get("human", 0),
        "perspective": perspective,
        "perspectives": perspectives,
        "cursor": cursor,
        "max_cursor": last_id,
        "day": current["day"] if current else 0,
        "phase": current["phase"] if current and current["day"] else "opening",
        "players": roster,
        "events": viewed,
        "traces": traces,
        # Navigation metadata deliberately excludes future text, actors and targets.
        "timeline": [
            {"id": e["id"], "day": e["day"], "phase": e["phase"], "kind": e["kind"]}
            for e in permitted
        ],
        "finished": bool(saved.get("winner")),
        "winner": (
            saved.get("winner") if any(e["kind"] == "result" for e in viewed) else None
        ),
    }
