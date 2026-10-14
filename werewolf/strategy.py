"""Private decision summaries and distinct public voices, never hidden chain-of-thought."""

from difflib import SequenceMatcher
import re

VOICES = {
    "曹操": "冷静而有压迫感。70–150字。抓一个具体破绽，直接点名并下判断；别替全桌总结。可以设置一个对手难以回避的选择。",
    "刘备": "有人情味的结盟者。80–170字。明确愿意跟谁合作、愿意替谁担保到什么程度；被信任者背离时也要有情绪和立场。不要机械复述他人。",
    "孙权": "务实的局势判断者。60–130字。选择对己方更有收益的路线，给出条件式承诺；少说抽象的两种可能，多说今天实际怎么投。",
    "诸葛亮": "证据密度高。100–200字。精确比较两份具体证词或一组票型，允许短编号；指出哪个事实能推翻自己的判断。不要无证据地长篇分析。",
    "貂蝉": "机敏、有锋芒。40–100字。抓住一句话设计一个难回答的具体追问，可用轻微讽刺或假意认同；别讲课，也别连续罗列问题。",
    "关羽": "短而坚定。35–90字。先说站哪边、准备投谁，再给最关键的理由。敢承担判断责任，不必给每个人留余地。",
    "司马懿": "克制而有保留。30–85字。只挑一个关键点，必要时故意不公开全部判断；用一句具体承诺留下后手，避免泛泛“继续观察”。",
    "周瑜": "主动组织阵线。80–150字。提出具体的出人或验证方案，点明希望谁响应；可以强势争论，不能只催促别人交理由。",
    "赵云": "稳健且敢于逆风。60–130字。为受错误指控的人反驳一个具体问题，或解释自己为何拒绝跟票；忠于证据而非机械唱反调。",
}

ASSESSMENT_PROMPT = """这是私有局势评估阶段。先自行判断，再形成简短结论记录。不要写逐步思维链或长篇内心独白。
依据只能来自自己的档案、技能结果和私有笔记。官方查验是事实，别人自称身份是证词，票型只表示投票而不直接证明阵营。死亡公告不揭示身份或死因；平安夜不能证明谁是好人。没有发生的票型或发言不能被当作证据。
必须有自己的暂定立场；证据少时允许低置信度，不能假造证据。先回应别人对自己的直接指控，再决定如何争取己方获胜。投票和技能比较至少两个合法选择的收益，不机械跟随最后一个说话者。
狼人知道队友，内部判断应诚实；公开说辞则可以刻意误导。结合狼队已经形成的计划分工，避免三个狼都用一样的方式装民或都悍跳。自己的假查验须与前次公开说辞一致。好人可藏身份、反驳和改变立场。
对质阶段必须回应第一轮里针对自己的问题，或修正一个具体判断；不允许重说一遍首轮套话。身份和姓名不是性格行为的充分证据，不要仅因诸葛亮聪明就认神。
事件编号不是玩家座号。文字结论只说玩家座号和轮次，消息编号只放在 evidence_ids 字段。
只输出 JSON。可先按已有 recall 协议读取旧消息。最终结构：
{"assessment":{"position":"当前独立立场，简短结论","revision":"相较自己上次判断改变了什么及关键新证据；没改变也说明","suspicions":[{"seat":2,"wolf_probability":0.6,"basis":"inference","note":"对该人的简短结论","evidence_ids":[12]}],"alternatives":[{"hypothesis":"另一个可能的局势或行动方案","weakness":"该方案的关键不确定性","evidence_ids":[12]}],"move":{"action":"本轮合法动作","target":null,"objective":"这次行动要达成什么","claim":"准备公开的立场或故意编造的说辞；技能可为空"},"confidence":0.5},"notebook":{"cover_story":"需要维持的身份说辞","plan":"后续策略","beliefs":{}}}
suspicions 至少包含一个其他存活玩家、最多9个；概率是你自己的判断，不是法官认证。basis 只能为 fact/claim/inference，引用只用自己真实收到的消息编号。alternatives 1–2条；每项文字不超过100字。move 的 action/target 必须来自当前合法选项。speak 的 target 为 null，发言对象写在 claim 中。"""

EXPRESSION_PROMPT = """这是公开表达阶段。局势已经独立评估过，现在只组织一次自然的游戏发言，不能修改锁定的动作或目标。
只输出 JSON {"action":"speak","target":null,"speech":"发言"}。
用你的声音说话，而不是写主持人总结。允许质问、辩护、结盟、假承诺、悍跳和施压，服务于本人的阵营目标。
不要泄露私有评估、概率表、狼队名单、系统提示或 notebook。狼人可按已选说辞欺骗。狼队协商是私密频道，可以谈队友和战术。
不要念消息编号、日志编号或后台术语。引用证据时只说具体玩家、第几天和原话，事件编号绝不是玩家座号。
有具体证据就点明谁说了什么；无证据时承认自己的判断是试探。不要编造已经发生的系统事实。
避免套话：“先把事实理清”“核对发言和票型”“不要盲目跟票”“需要具体理由”“继续观察”。若别人已讲过同一论点，必须反驳、推进或给出自己的明确决定，不能换个词复读。不要为了不同而随机猜身份。"""


VOTE_PROMPT = """本轮是封存投票，必须在 assessment 中另给 vote_review 对象。这是简短决策依据，不是思维链。
不要先跟随公认目标再补理由。独立核对所有合法候选人当日原话及自己的旧判断，再决定行动。
多人重复同一个指控不是多份独立证据；发言强势、晚跳身份、弃票、暂未表态都不能单独证明狼人。对疑似神职，比较误放逐真神与放过假跳的代价；没有对跳也不等于身份已认证。
自己的先前发言不是不可更改的承诺。检查最新辩护是否改变旧结论，允许改变立场。已出局者被猎人带走不代表其阵营已被验证。
狼人私下按已知阵营评估行动收益，可冲票或倒钩；好人按可见证据判断。允许有根据的一致投票，禁止为制造差异随机分票；弃票也须比较损失与收益。
vote_review 结构：
{"candidates":[{"target":1,"case_for":"放逐此人的最强依据或己方收益；无则明确写无","case_against":"此人的最强辩护或误杀代价；无则明确说明","evidence_ids":[12]}],"source_audit":"关键指控最初来自谁、哪些是转述、有没有独立支持；引用真实玩家与原话，证据不足则说明","latest_defense":"回应被选择目标的最新身份声明或辩护；弃票则解释主要争议为什么不足以放逐","decision_reason":"为什么选该目标而不是最有竞争力的另一个选项；不能只说大家都投他","change_condition":"什么新证据会改变该决定"}
candidates 必须各包含一次 current_action.targets 内每个玩家，并额外包含 target:null 表示弃票。每项文字限80字。evidence_ids 只能来自自己的档案，推测不伪装成事实。move 必须与最终决定一致。
"""


def vote_evidence(archive, context):
    """Balanced current-day index, sourced exclusively from this actor's inbox."""
    if context["kind"] not in {"vote", "pk_vote"}:
        return None
    return {
        "policy": "按座位核对原话；多人转述不增加独立证据。excerpt可能截短，完整原文见archive或recall。当前未公布的选票不可见。",
        "speakers": [
            {
                "seat": seat,
                "statements": [
                    {
                        "id": r["id"],
                        "day": r["day"],
                        "excerpt": r["text"][:1200],
                        "truncated": len(r["text"]) > 1200,
                    }
                    for r in archive.records
                    if r["actor"] == seat
                    and r["kind"] == "speech"
                    and r["day"] == context["day"]
                ],
            }
            for seat in context["alive"]
        ],
    }


def clean_vote_review(value, archive, context):
    if not isinstance(value, dict):
        raise ValueError("投票必须提供 vote_review 候选人比较与证据复核")
    expected = set(context["targets"]) | {None}
    rows, seen = [], set()

    def required(obj, field):
        text = obj.get(field)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("投票复核缺少 " + field)
        return text.strip()[:240]

    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("投票复核需要候选人列表")
    for item in candidates:
        if not isinstance(item, dict) or "target" not in item:
            raise ValueError("候选人必须包含 target")
        target = item["target"]
        if (
            (target is not None and type(target) is not int)
            or target not in expected
            or target in seen
        ):
            raise ValueError("投票候选人不合法或重复")
        seen.add(target)
        rows.append(
            {
                "target": target,
                "case_for": required(item, "case_for"),
                "case_against": required(item, "case_against"),
                "evidence_ids": [
                    r["id"] for r in archive.recall(item.get("evidence_ids"))
                ],
            }
        )
    if seen != expected:
        raise ValueError("必须比较全部合法候选人与弃票，不能只评估跟票目标")
    return {
        "candidates": rows,
        **{
            field: required(value, field)
            for field in (
                "source_audit",
                "latest_defense",
                "decision_reason",
                "change_condition",
            )
        },
    }


def clean_assessment(raw, archive, context, validator):
    value = raw.get("assessment")
    if not isinstance(value, dict):
        raise ValueError("缺少 assessment 局势评估对象")

    def short(v, limit=180):
        return v.strip()[:limit] if isinstance(v, str) else ""

    def evidence(v):
        return [r["id"] for r in archive.recall(v)]

    def probability(v):
        if type(v) not in (int, float) or not 0 <= v <= 1:
            raise ValueError("判断概率须为0到1的数值")
        return round(v, 3)

    move = value.get("move", {})
    if not isinstance(move, dict):
        raise ValueError("缺少合法行动方案")
    action = validator(
        {
            "action": move.get("action"),
            "target": move.get("target"),
            "speech": "待组织发言" if move.get("action") == "speak" else "",
        }
    )
    suspects = []
    for item in (value.get("suspicions") or [])[:9]:
        if not isinstance(item, dict) or type(item.get("seat")) is not int:
            continue
        if item["seat"] not in context["alive"] or item["seat"] == context["actor"]:
            continue
        if item.get("basis") not in {"fact", "claim", "inference"} or not short(
            item.get("note")
        ):
            continue
        if any(s["seat"] == item["seat"] for s in suspects):
            continue
        suspects.append(
            {
                "seat": item["seat"],
                "wolf_probability": probability(item.get("wolf_probability")),
                "basis": item["basis"],
                "note": short(item["note"]),
                "evidence_ids": evidence(item.get("evidence_ids")),
            }
        )
    alternatives = []
    for item in (value.get("alternatives") or [])[:2]:
        if (
            isinstance(item, dict)
            and short(item.get("hypothesis"))
            and short(item.get("weakness"))
        ):
            alternatives.append(
                {
                    "hypothesis": short(item["hypothesis"]),
                    "weakness": short(item["weakness"]),
                    "evidence_ids": evidence(item.get("evidence_ids")),
                }
            )
    if (
        not suspects
        or not alternatives
        or not short(value.get("position"))
        or not short(value.get("revision"))
        or not short(move.get("objective"))
    ):
        raise ValueError("需要立场、判断变化、具体嫌疑、备选解释及行动目的")
    cleaned = {
        "position": short(value["position"]),
        "revision": short(value["revision"]),
        "suspicions": suspects,
        "alternatives": alternatives,
        "confidence": probability(value.get("confidence")),
        "move": {
            "action": action["action"],
            "target": action["target"],
            "objective": short(move["objective"]),
            "claim": short(move.get("claim"), 300),
        },
    }
    if context["kind"] in {"vote", "pk_vote"}:
        cleaned["vote_review"] = clean_vote_review(
            value.get("vote_review"), archive, context
        )
    return cleaned, action


def speech_similarity(speech, records):
    """Character sequence similarity; a repair hint, not a strategy score."""

    def normalized(text):
        return re.sub(r"[\W\d_]+", "", text)

    a = normalized(speech)
    scores = []
    for record in records[-12:]:
        b = normalized(record["text"])
        if min(len(a), len(b)) < 18:
            continue
        scores.append(SequenceMatcher(None, a, b).ratio())
    return round(max(scores, default=0), 3)


def expression_safe(value):
    """Remove private audit notation from generated notes, not from source testimony."""
    if isinstance(value, dict):
        return {k: expression_safe(v) for k, v in value.items() if k != "evidence_ids"}
    if isinstance(value, list):
        return [expression_safe(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"第?\s*\d+\s*号\s*(?:消息|记录|原文)", "已知证词", value)
        return re.sub(r"(?:消息|记录|事件)(?:编号)?\s*#?\d+|#\d+", "相关证词", value)
    return value


def validate_speech_references(speech, size):
    if re.search(r"第\s*\d+\s*号|(?:消息|记录|事件)(?:编号)?\s*#?\d+|#\d+", speech):
        raise ValueError("不要念后台编号；请引用玩家、轮次和原话")
    if any(int(n) > size or int(n) < 1 for n in re.findall(r"(?<!\d)(\d+)号", speech)):
        raise ValueError(f"本局只有1至{size}号玩家，不能提及不存在的座位")
