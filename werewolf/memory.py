"""Lossless per-agent archive with a bounded evidence index and scoped recall.

The source of truth is exclusively that agent's MsgHub-delivered memory.
No global game event list or another player's private notes are accepted here.
"""

from __future__ import annotations

import json
import re

INDEX_KINDS = {"speech", "wolf", "private", "ballot", "dawn", "elimination", "result"}
IMPORTANT = re.compile(
    r"预言家|女巫|猎人|平民|狼人|查验|金水|银水|查杀|投|怀疑|信任|对跳|救|毒|开枪|身份"
)


def evidence_excerpt(text: str, limit=100) -> str:
    if len(text) <= limit:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；\n])", text) if s.strip()]
    important = [s for s in sentences if IMPORTANT.search(s)]
    # Extractive only: no generated summary is promoted to an established fact.
    selected = "…".join(important or sentences)
    return selected[:limit] + "…"


class EvidenceMemory:
    INDEX_BUDGET = 12000
    DETAIL_BUDGET = 18000
    RECALL_LIMIT = 8

    def __init__(self, messages):
        self.records = []
        for n, msg in enumerate(messages, 1):
            meta = msg.metadata if isinstance(msg.metadata, dict) else {}
            self.records.append(
                {
                    "id": meta.get("event_id", f"local-{n}"),
                    "day": meta.get("day", 0),
                    "phase": meta.get("phase", "unknown"),
                    "speaker": msg.name,
                    "actor": meta.get("actor"),
                    "kind": meta.get("kind", "speech"),
                    "text": str(msg.content),
                }
            )
        self.by_id = {str(r["id"]): r for r in self.records}

    def recall(self, ids):
        if not isinstance(ids, list):
            return []
        # Unknown IDs include both missing records and other players' secrets.
        # They are indistinguishable; no lookup ever consults the global event log.
        result = []
        seen = set()
        for value in ids[: self.RECALL_LIMIT]:
            key = str(value)
            if key in self.by_id and key not in seen:
                seen.add(key)
                result.append(self.by_id[key].copy())
        return result

    def pack(self, context: dict, owner: int) -> tuple[dict, dict]:
        indexed = [r for r in self.records if r["kind"] in INDEX_KINDS]
        # Every evidence record retains an index entry, including the first round.
        # Excerpts shrink for long games; complete text stays available by ID.
        excerpt_size = max(18, min(100, self.INDEX_BUDGET // max(1, len(indexed)) - 42))
        dossiers = {}
        for record in indexed:
            key = record["speaker"]
            dossiers.setdefault(key, []).append(
                {
                    "id": record["id"],
                    "day": record["day"],
                    "kind": record["kind"],
                    "excerpt": evidence_excerpt(record["text"], excerpt_size),
                }
            )
        details, used = {}, 0

        def add(record):
            nonlocal used
            key = str(record["id"])
            cost = len(record["text"]) + 120
            if key not in details and used + cost <= self.DETAIL_BUDGET:
                details[key] = record
                used += cost

        # Guaranteed identity, official private results and vote history first.
        for record in self.records:
            if record["kind"] in {"private", "ballot", "dawn", "elimination"}:
                add(record)
        for record in self.records[-18:]:
            add(record)
        # Revisit earlier claims by *every* speaker, not only the most recent voice.
        speakers = set(r["speaker"] for r in indexed)
        for speaker in sorted(speakers):
            speeches = [
                r
                for r in indexed
                if r["speaker"] == speaker and r["kind"] in {"speech", "wolf"}
            ]
            for record in speeches[:1] + speeches[-2:]:
                add(record)
            for record in speeches:
                if IMPORTANT.search(record["text"]):
                    add(record)
        # Include remaining exact text when the budget permits.
        for record in reversed(self.records):
            add(record)
        detail_ids = set(details)
        packet = {
            "archive_policy": "以下全部来自你自己接收的消息。原文永久保留。本索引涵盖本局已知的历次证词；excerpt只是摘录，不是全文或已验证事实。别人声称的身份/查验可能是谎言。",
            "by_speaker_all_rounds": dossiers,
            "exact_records": [r for r in self.records if str(r["id"]) in detail_ids],
            "recall_protocol": '若摘录不足以判断，先输出 {"recall":[事件id,...]}，每次最多8条、每行动最多2次，读取自己的完整旧记录，再提交动作。禁止猜测摘录省略的内容。',
        }
        stats = {
            "archive_records": len(self.records),
            "indexed_records": len(indexed),
            "exact_records": len(details),
            "earliest_day": min((r["day"] for r in indexed), default=0),
            "latest_day": max((r["day"] for r in indexed), default=0),
            "included_ids": [r["id"] for r in packet["exact_records"]],
            "retrieved_ids": [],
            "packet_chars": len(json.dumps(packet, ensure_ascii=False)),
        }
        return packet, stats


def clean_private_report(raw, archive: EvidenceMemory, previous: dict):
    """Private, concise decision annotations. Never a raw reasoning transcript."""
    report = raw.get("decision_summary", {})
    if not isinstance(report, dict):
        report = {}

    def short(value, limit):
        return value.strip()[:limit] if isinstance(value, str) else ""

    summary = {
        "summary": short(report.get("summary"), 220) or "模型未提供决策摘要。",
        "intent": short(report.get("intent"), 160),
        "evidence_ids": [
            r["id"] for r in archive.recall(report.get("evidence_ids", []))
        ],
    }
    notes = dict(previous)
    update = raw.get("notebook", {})
    if isinstance(update, dict):
        for field, limit in (("cover_story", 300), ("plan", 300)):
            if isinstance(update.get(field), str) and update[field].strip():
                notes[field] = short(update[field], limit)
        beliefs = dict(notes.get("beliefs", {}))
        if isinstance(update.get("beliefs"), dict):
            for key, value in update["beliefs"].items():
                if str(key) in {str(i) for i in range(1, 10)} and isinstance(
                    value, str
                ):
                    beliefs[str(key)] = short(value, 120)
        notes["beliefs"] = beliefs
    return summary, notes
