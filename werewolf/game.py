from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import random
import secrets

from .agents import HubRouter, ModelConfig, PERSONAS, ROLES, WerewolfDialogAgent

SPEECH_KINDS = {"talk", "rebuttal", "pk_talk", "last_words", "wolf_chat"}
ACTION_NAMES = {
    "speak": "发言",
    "kill": "选择刀口",
    "check": "查验",
    "save": "使用解药",
    "poison": "使用毒药",
    "pass": "不使用技能",
    "vote": "投票",
    "shoot": "开枪",
    "abstain": "弃票",
}
TASK_NAMES = {
    "wolf_chat": "狼人协商",
    "wolf_vote": "狼人决策",
    "seer": "预言家查验",
    "witch": "女巫用药",
    "talk": "白天发言",
    "rebuttal": "回应与对质",
    "vote": "放逐投票",
    "pk_talk": "平票辩论",
    "pk_vote": "平票复投",
    "last_words": "遗言",
    "hunter": "猎人开枪",
}


class IllegalAction(ValueError):
    pass


class ThreeKingdomsWerewolfGame:
    """Server-authoritative, serial, persistable state machine. LLMs never arbitrate rules."""

    def __init__(self, size=9, human=1, mode="demo", seed=None, human_role=None):
        if (
            size not in (6, 9)
            or mode not in ("demo", "llm")
            or human not in range(size + 1)
        ):
            raise ValueError("无效的房间设置")
        deck = (
            ["wolf"] * (3 if size == 9 else 2)
            + ["villager"] * (3 if size == 9 else 2)
            + ["seer", "witch"]
        )
        if size == 9:
            deck.append("hunter")
        rng = random.Random(seed) if seed is not None else random.SystemRandom()
        rng.shuffle(deck)
        if human and human_role:
            if human_role not in deck:
                raise ValueError("该板子不包含所选身份")
            idx = deck.index(human_role)
            deck[human - 1], deck[idx] = deck[idx], deck[human - 1]
        self.id = secrets.token_hex(8)
        self.mode, self.human = mode, human
        self.players = [
            {
                "id": i + 1,
                "name": PERSONAS[i][0],
                "trait": PERSONAS[i][1],
                "persona": PERSONAS[i][2],
                "role": role,
                "alive": True,
                "revealed": False,
                "death": None,
            }
            for i, role in enumerate(deck)
        ]
        self.day, self.phase, self.version = 0, "night", 0
        self.queue, self.events = [], []
        self.winner = None
        self.potions = {"save": True, "poison": True}
        self.checks = {}
        self.wolf_votes, self.ballots = {}, {}
        self.victim, self.poisoned, self.saved = None, None, False
        self.pk, self.vote_round = [], 0
        self.shot_used = False
        self.error = None
        self.usage = {"calls": 0, "tokens": 0}
        self.debug_mode = False
        self.debug_traces = []
        self._build_agents()
        self.emit(
            "序章",
            f"{size}位来客已入席。身份已秘密分配。{'练习模式由规则机器人行动。' if mode == 'demo' else '真实模型对局已准备就绪。'}",
            kind="system",
        )
        for p in self.players:
            content = f"你的身份是{ROLES[p['role']]}。人格：{p['trait']}。"
            if p["role"] == "wolf":
                content += (
                    "狼队成员是"
                    + "、".join(
                        f"{w['id']}号{w['name']}"
                        for w in self.players
                        if w["role"] == "wolf"
                    )
                    + "。"
                )
            self.emit("身份", content, [p["id"]], "private")
        self.queue = [{"kind": "night_start"}]
        self._settle()

    def _build_agents(self):
        self.agents = {p["id"]: WerewolfDialogAgent(p) for p in self.players}
        self.router = HubRouter(self.agents)

    def player(self, seat):
        return self.players[seat - 1]

    def alive(self):
        return [p["id"] for p in self.players if p["alive"]]

    def wolves(self):
        return [p["id"] for p in self.players if p["alive"] and p["role"] == "wolf"]

    def emit(
        self,
        title,
        text,
        recipients=None,
        kind="system",
        actor=None,
        data=None,
        routed=False,
    ):
        public = recipients is None
        recipients = self.alive() if public else recipients
        if routed and actor is not None and actor not in recipients:
            recipients = recipients + [actor]
        event = {
            "id": len(self.events) + 1,
            "day": self.day,
            "phase": self.phase,
            "title": title,
            "text": text,
            "kind": kind,
            "actor": actor,
            "public": public,
            "recipients": list(recipients),
            "data": data,
        }
        self.events.append(event)
        if not routed:
            self.router.broadcast(recipients, title, text, self.message_metadata(event))

    @staticmethod
    def message_metadata(event):
        return {
            "event_id": event["id"],
            "day": event["day"],
            "phase": event["phase"],
            "actor": event["actor"],
            "kind": event["kind"],
        }

    def _prepend(self, tasks):
        self.queue = tasks + self.queue

    def _settle(self):
        while self.queue and "actor" not in self.queue[0] and not self.winner:
            task = self.queue.pop(0)
            kind = task["kind"]
            if kind == "night_start":
                self.day += 1
                if self.day > 20:
                    self.finish("draw", "达到20轮上限，双方未分胜负。")
                    return
                self.phase = "night"
                self.wolf_votes = {}
                self.victim, self.poisoned, self.saved = None, None, False
                self.emit(
                    "天黑请闭眼",
                    f"第{self.day}夜降临。所有夜间行动将在天亮时一并结算。",
                )
                tasks = [{"kind": "wolf_chat", "actor": x} for x in self.wolves()]
                tasks += [{"kind": "wolf_vote", "actor": x} for x in self.wolves()]
                tasks += [{"kind": "wolves_resolve"}]
                for role in ("seer", "witch"):
                    tasks += [
                        {"kind": role, "actor": p["id"]}
                        for p in self.players
                        if p["alive"] and p["role"] == role
                    ]
                tasks += [{"kind": "dawn"}]
                self._prepend(tasks)
            elif kind == "wolves_resolve":
                counts = Counter(self.wolf_votes.values())
                self.victim = (
                    min(counts, key=lambda x: (-counts[x], x)) if counts else None
                )
                self.emit(
                    "狼队决定",
                    f"今夜选择{self.victim}号。平票时按较小座号决断。",
                    self.wolves(),
                    "wolf",
                )
            elif kind == "dawn":
                self.phase = "day"
                deaths = {}
                if self.victim and not self.saved:
                    deaths[self.victim] = "wolf"
                if self.poisoned:
                    deaths[self.poisoned] = (
                        "poison"  # poison dominates: hunter cannot shoot
                    )
                for seat, cause in deaths.items():
                    self._kill(seat, cause)
                self.emit(
                    "天亮了",
                    (
                        (
                            "昨夜出局："
                            + "、".join(
                                f"{i}号{self.player(i)['name']}" for i in sorted(deaths)
                            )
                            + "。"
                        )
                        if deaths
                        else "昨夜是平安夜。所有人都平安归来。"
                    ),
                    kind="dawn",
                    data={"deaths": sorted(deaths)},
                )
                aftermath = []
                for seat, cause in sorted(deaths.items()):
                    if self.day == 1:
                        aftermath.append({"kind": "last_words", "actor": seat})
                    if self.player(seat)["role"] == "hunter" and cause != "poison":
                        aftermath.append({"kind": "hunter", "actor": seat})
                self._prepend(
                    aftermath + [{"kind": "check_win"}, {"kind": "day_start"}]
                )
            elif kind == "day_start":
                self.phase = "day"
                ids = self.alive()
                offset = (self.day - 1) % len(ids)
                ids = ids[offset:] + ids[:offset]
                self.emit(
                    "议事开始",
                    "请依次发言。对方的身份声明不等于法官认证，留意发言、查验和票型之间的矛盾。",
                )
                self._prepend(
                    [{"kind": "talk", "actor": x} for x in ids]
                    + [{"kind": "rebuttal_start"}]
                )
            elif kind == "rebuttal_start":
                ids = self.alive()
                offset = (self.day - 1) % len(ids)
                ids = ids[offset:] + ids[:offset]
                self.emit(
                    "回应与对质",
                    "每人再回应一次：回答针对自己的质疑，指出具体分歧，或说明判断为何改变。此轮结束后封存投票。",
                )
                self._prepend(
                    [{"kind": "rebuttal", "actor": x} for x in ids]
                    + [{"kind": "vote_start"}]
                )
            elif kind == "vote_start":
                self.phase = "vote"
                self.ballots, self.pk, self.vote_round = {}, [], 0
                self.emit(
                    "开始投票", "选票同时封存，全部提交后统一公布。可弃票，不可投自己。"
                )
                self._prepend(
                    [{"kind": "vote", "actor": x} for x in self.alive()]
                    + [{"kind": "tally"}]
                )
            elif kind == "pk_start":
                self.phase = "vote"
                self.ballots, self.vote_round = {}, 1
                self._prepend(
                    [
                        {"kind": "pk_vote", "actor": x}
                        for x in self.alive()
                        if x not in self.pk
                    ]
                    + [{"kind": "tally"}]
                )
            elif kind == "tally":
                counts = Counter(x for x in self.ballots.values() if x is not None)
                top = (
                    sorted(
                        [
                            x
                            for x, count in counts.items()
                            if count == max(counts.values())
                        ]
                    )
                    if counts
                    else []
                )
                summary = "；".join(
                    f"{k}号 → {str(v) + '号' if v else '弃票'}"
                    for k, v in self.ballots.items()
                )
                self.emit(
                    "复投票型" if self.vote_round else "放逐票型",
                    summary or "无人可投票。",
                    kind="ballot",
                    data={"ballots": deepcopy(self.ballots), "round": self.vote_round},
                )
                if len(top) == 1:
                    seat = top[0]
                    self._kill(seat, "vote")
                    self.emit(
                        "放逐结果",
                        f"{seat}号{self.player(seat)['name']}被放逐出局，获得{counts[seat]}票。",
                        kind="elimination",
                        data={"deaths": [seat]},
                    )
                    tasks = [{"kind": "last_words", "actor": seat}]
                    if self.player(seat)["role"] == "hunter":
                        tasks.append({"kind": "hunter", "actor": seat})
                    self._prepend(
                        tasks + [{"kind": "check_win"}, {"kind": "night_start"}]
                    )
                elif len(top) > 1 and not self.vote_round:
                    self.pk = top
                    self.phase = "day"
                    self.emit(
                        "平票 · 追加辩论",
                        "、".join(f"{x}号" for x in top)
                        + "并列最高票。各辩论一次后，由其余存活玩家复投。",
                    )
                    self._prepend(
                        [{"kind": "pk_talk", "actor": x} for x in top]
                        + [{"kind": "pk_start"}]
                    )
                else:
                    self.emit("无人出局", "再次平票或全部弃票，今日不放逐任何人。")
                    self._prepend([{"kind": "night_start"}])
            elif kind == "check_win":
                self.check_winner()

    def _kill(self, seat, cause):
        p = self.player(seat)
        p["alive"], p["death"] = False, {"day": self.day, "cause": cause}

    def check_winner(self):
        living = [p for p in self.players if p["alive"]]
        if not living:
            self.finish("draw", "所有玩家在同一轮结算中出局。")
        elif not any(p["role"] == "wolf" for p in living):
            self.finish("good", "所有狼人均已出局，好人阵营获胜。")
        elif not any(p["role"] == "villager" for p in living):
            self.finish("wolf", "所有平民均已出局，狼人达成屠边。")
        elif not any(p["role"] in {"seer", "witch", "hunter"} for p in living):
            self.finish("wolf", "所有神职均已出局，狼人达成屠边。")
        return self.winner

    def finish(self, winner, text):
        self.winner, self.phase, self.queue = winner, "finished", []
        self.emit("终局", text, kind="result")

    def context(self):
        if not self.queue or self.winner:
            return None
        task = self.queue[0]
        seat, kind = task["actor"], task["kind"]
        p = self.player(seat)
        targets = [x for x in self.alive() if x != seat]
        actions = ["speak"]
        if kind in {"wolf_vote", "wolf_chat"}:
            targets = [x for x in self.alive() if x not in self.wolves()]
            actions = ["kill"] if kind == "wolf_vote" else ["speak"]
        elif kind in {"vote", "pk_vote"}:
            targets = self.pk if kind == "pk_vote" else targets
            actions = ["vote", "abstain"]
        elif kind == "seer":
            actions = ["check"]
        elif kind == "witch":
            targets = self.alive()
            actions = ["pass"]
            if (
                self.potions["save"]
                and self.victim
                and (self.victim != seat or self.day == 1)
            ):
                actions.append("save")
            if self.potions["poison"]:
                actions.append("poison")
        elif kind == "hunter":
            actions = ["shoot", "pass"] if targets and not self.shot_used else ["pass"]
        ctx = {
            "kind": kind,
            "label": TASK_NAMES[kind],
            "actor": seat,
            "day": self.day,
            "round": self.vote_round,
            "alive": self.alive(),
            "targets": list(targets),
            "actions": actions,
            "board": {
                "size": len(self.players),
                "starting_roles": dict(Counter(x["role"] for x in self.players)),
                "wolf_can_skip_kill": False,
                "death_reveals_role": False,
            },
            "players": [
                {"id": x["id"], "name": x["name"], "alive": x["alive"]}
                for x in self.players
            ],
        }
        if kind in {"talk", "rebuttal", "pk_talk"}:
            ctx["discussion"] = {
                "stage": kind,
                "already_spoken": [
                    e["actor"]
                    for e in self.events
                    if e["day"] == self.day
                    and e["kind"] == "speech"
                    and (e.get("data") or {}).get("kind") == kind
                ],
                "waiting_for_turn": [
                    task["actor"] for task in self.queue if task["kind"] == kind
                ],
                "rule": "未轮到的人尚未说话，不是拒绝回应；回应轮所有人已听过首轮发言。",
            }
        if p["role"] == "wolf":
            ctx["teammates"] = [x["id"] for x in self.players if x["role"] == "wolf"]
        if p["role"] == "seer":
            ctx["checks"] = deepcopy(self.checks)
        if p["role"] == "witch":
            ctx["potions"] = deepcopy(self.potions)
            if kind == "witch":
                ctx["victim"] = self.victim if self.potions["save"] else None
        return ctx

    def validate(self, proposed):
        ctx = self.context()
        if not isinstance(proposed, dict) or not ctx:
            raise IllegalAction("当前没有可执行的行动")
        action, target = proposed.get("action"), proposed.get("target")
        speech = proposed.get("speech", "")
        if action not in ctx["actions"]:
            raise IllegalAction("该行动在当前阶段不可用")
        if action in {"kill", "check", "poison", "shoot", "vote", "save"}:
            if type(target) is not int or target not in ctx["targets"]:
                raise IllegalAction("请选择合法的存活目标")
            if action == "save" and target != self.victim:
                raise IllegalAction("解药只能救今夜刀口")
        elif target is not None:
            raise IllegalAction("该行动不需要目标")
        if not isinstance(speech, str) or len(speech) > 600:
            raise IllegalAction("发言需为600字以内的文字")
        if action == "speak" and not speech.strip():
            raise IllegalAction("请填写发言内容")
        return {"action": action, "target": target, "speech": speech.strip()}

    def advance(
        self, config: ModelConfig | None = None, submitted=None, allow_human_bot=False
    ):
        if self.winner:
            return
        ctx = self.context()
        seat, kind = ctx["actor"], ctx["kind"]
        if seat == self.human and submitted is None and not allow_human_bot:
            raise IllegalAction("轮到你了，请提交行动")
        if submitted is not None and seat != self.human:
            raise IllegalAction("当前不是你的回合")
        recipients = (
            self.alive()
            if kind in {"talk", "rebuttal", "pk_talk", "last_words"}
            else self.wolves() if kind == "wolf_chat" else [seat]
        )
        if seat not in recipients:
            recipients = recipients + [seat]
        agent = self.agents[seat]
        before_calls, before_tokens = agent.calls, agent.tokens
        try:
            message = self.router.speak(
                seat,
                recipients,
                context=ctx,
                mode=self.mode,
                config=config or ModelConfig(),
                validator=self.validate,
                submitted=submitted,
                message_meta={
                    "event_id": len(self.events) + 1,
                    "day": self.day,
                    "phase": self.phase,
                    "actor": seat,
                    "kind": (
                        "speech"
                        if kind in SPEECH_KINDS and kind != "wolf_chat"
                        else "wolf" if kind == "wolf_chat" else "action"
                    ),
                },
            )
        finally:
            agent.progress = None
            self.usage["calls"] += agent.calls - before_calls
            self.usage["tokens"] += agent.tokens - before_tokens
        action = agent.last_action
        if agent.last_debug:
            self.debug_traces.append(
                {
                    "id": len(self.debug_traces) + 1,
                    "actor": seat,
                    "day": self.day,
                    "phase": self.phase,
                    "kind": kind,
                    "event_id": len(self.events) + 1,
                    "model": (
                        config.model if config and self.mode == "llm" else "练习策略"
                    ),
                    "action": deepcopy(action),
                    **deepcopy(agent.last_debug),
                }
            )
        self.emit(
            agent.name,
            message.content,
            (
                None
                if kind in {"talk", "rebuttal", "pk_talk", "last_words"}
                else recipients
            ),
            (
                "speech"
                if kind in SPEECH_KINDS and kind != "wolf_chat"
                else "wolf" if kind == "wolf_chat" else "action"
            ),
            seat,
            {"kind": kind, **action},
            routed=True,
        )
        self.queue.pop(0)
        target = action["target"]
        if kind == "wolf_vote":
            self.wolf_votes[seat] = target
        elif kind == "seer":
            result = "wolf" if self.player(target)["role"] == "wolf" else "good"
            self.checks[str(target)] = result
            self.emit(
                "查验结果",
                f"{target}号{self.player(target)['name']}的阵营是{'狼人' if result == 'wolf' else '好人'}。",
                [seat],
                "private",
            )
        elif kind == "witch":
            if action["action"] == "save":
                self.saved, self.potions["save"] = True, False
            elif action["action"] == "poison":
                self.poisoned, self.potions["poison"] = target, False
        elif kind in {"vote", "pk_vote"}:
            self.ballots[seat] = target
        elif kind == "hunter":
            self.shot_used = True
            self.player(seat)["revealed"] = True
            if action["action"] == "shoot":
                self._kill(target, "shot")
                self.emit(
                    "猎人开枪",
                    f"{seat}号亮出猎人身份，带走了{target}号{self.player(target)['name']}。",
                    kind="elimination",
                    data={"deaths": [target]},
                )
            else:
                self.emit("猎人收枪", f"{seat}号亮出猎人身份，选择不开枪。")
        self.error = None
        self.version += 1
        self._settle()

    def snapshot(self):
        result = deepcopy(
            {k: v for k, v in self.__dict__.items() if k not in {"agents", "router"}}
        )
        result["agent_notebooks"] = {
            str(i): deepcopy(a.notebook) for i, a in self.agents.items()
        }
        return result

    @classmethod
    def restore(cls, data):
        game = cls.__new__(cls)
        saved = deepcopy(data)
        notebooks = saved.pop("agent_notebooks", {})
        game.__dict__.update(saved)
        game.debug_mode = saved.get("debug_mode", False)
        game.debug_traces = saved.get("debug_traces", [])
        game.wolf_votes = {int(k): v for k, v in game.wolf_votes.items()}
        game.ballots = {int(k): v for k, v in game.ballots.items()}
        game._build_agents()
        for e in game.events:
            game.router.broadcast(
                e["recipients"], e["title"], e["text"], game.message_metadata(e)
            )
        for i, agent in game.agents.items():
            agent.notebook = deepcopy(notebooks.get(str(i), {}))
        return game

    def view(self):
        omniscient = not self.human or bool(self.winner) or self.debug_mode
        me = self.player(self.human) if self.human else None
        wolf_view = bool(me and me["role"] == "wolf")
        players = []
        for p in self.players:
            known = (
                omniscient
                or p["id"] == self.human
                or p["revealed"]
                or (wolf_view and p["role"] == "wolf")
            )
            players.append(
                {
                    "id": p["id"],
                    "name": p["name"],
                    "trait": p["trait"],
                    "alive": p["alive"],
                    "role": p["role"] if known else None,
                    "death_day": p["death"]["day"] if p["death"] else None,
                }
            )
        events = []
        for e in self.events:
            if omniscient or e["public"] or self.human in e["recipients"]:
                events.append(
                    {k: deepcopy(v) for k, v in e.items() if k != "recipients"}
                )
        ctx = self.context()
        pending = None
        if ctx:
            if omniscient or ctx["actor"] == self.human:
                pending = ctx
            elif self.phase == "night":
                pending = {"label": "夜间行动中", "kind": "hidden", "actor": None}
            elif ctx["kind"] in {"vote", "pk_vote"}:
                pending = {"label": "等待选票封存", "kind": "hidden", "actor": None}
            else:
                pending = {
                    "label": ctx["label"],
                    "kind": ctx["kind"],
                    "actor": ctx["actor"],
                }
        personal = None
        if me:
            personal = {
                "role": me["role"],
                "alive": me["alive"],
                "name": me["name"],
                "trait": me["trait"],
            }
            if me["role"] == "seer":
                personal["checks"] = deepcopy(self.checks)
            if me["role"] == "witch":
                personal["potions"] = deepcopy(self.potions)
        return {
            "id": self.id,
            "mode": self.mode,
            "human": self.human,
            "day": self.day,
            "phase": self.phase,
            "version": self.version,
            "players": players,
            "events": events,
            "pending": pending,
            "needs_human": bool(ctx and ctx["actor"] == self.human),
            "winner": self.winner,
            "personal": personal,
            "usage": deepcopy(self.usage),
            "error": self.error,
            "omniscient": omniscient,
            "alive_count": len(self.alive()),
            "debug_mode": self.debug_mode,
            "debug_available": omniscient,
            "debug_traces": deepcopy(self.debug_traces) if omniscient else [],
        }

    def export(self):
        result = self.view()
        result["export_note"] = (
            "终局完整复盘" if self.winner else "进行中：仅包含当前视角可见内容"
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
