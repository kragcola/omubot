"""Lightweight graph fact extractor.

The first production path is deliberately deterministic and conservative. It
extracts only simple owner-observable facts from memory cards and document
chunks, then lets KnowledgeGraphService governance thresholds decide whether
they become active facts or review candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.context.types import ContextHit


@dataclass(slots=True)
class GraphExtractionResult:
    subject: str
    predicate: str
    object: str
    confidence: float
    evidence: dict[str, Any]
    source: str = "context_extractor"


class KnowledgeGraphExtractor:
    """Deterministic first-version extractor for context hits."""

    async def extract_from_hits(self, hits: list[ContextHit]) -> list[GraphExtractionResult]:
        results: list[GraphExtractionResult] = []
        for hit in hits:
            if hit.type not in {"memory_card", "doc_chunk"}:
                continue
            evidence = _evidence_from_hit(hit)
            subject_hint = _subject_from_hit(hit)
            source = f"context:{hit.type}"
            results.extend(self._extract_from_text(
                hit.content,
                evidence=evidence,
                subject_hint=subject_hint,
                source=source,
                base_confidence=0.86 if hit.type == "memory_card" else 0.72,
            ))
        return _dedupe_results(results)

    async def extract(self, text: str, *, evidence: dict[str, Any]) -> list[GraphExtractionResult]:
        return self._extract_from_text(
            text,
            evidence=evidence,
            subject_hint=str(evidence.get("subject") or "未知实体"),
            source=str(evidence.get("source") or "manual_extract"),
            base_confidence=float(evidence.get("confidence") or 0.7),
        )

    def _extract_from_text(
        self,
        text: str,
        *,
        evidence: dict[str, Any],
        subject_hint: str,
        source: str,
        base_confidence: float,
    ) -> list[GraphExtractionResult]:
        text = _clean_text(text)
        if not text:
            return []

        results: list[GraphExtractionResult] = []
        for pattern, predicate, confidence_boost in _PATTERNS:
            for match in pattern.finditer(text):
                subject = _clean_entity(match.groupdict().get("subject") or subject_hint)
                if subject in _GENERIC_SUBJECTS:
                    subject = _clean_entity(subject_hint)
                obj = _clean_entity(match.groupdict().get("object") or "")
                if not _valid_entity(subject) or not _valid_entity(obj):
                    continue
                results.append(GraphExtractionResult(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    confidence=min(0.95, max(0.0, base_confidence + confidence_boost)),
                    source=source,
                    evidence={**evidence, "quote": _quote_for(text, match.group(0))},
                ))
        return results


_PATTERNS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (
        re.compile(r"(?P<subject>[\w\u4e00-\u9fff @#-]{1,28})不喜欢(?P<object>[^，。；;!?！？\n]{1,36})"),
        "不喜欢",
        0.05,
    ),
    (
        re.compile(r"(?P<subject>[\w\u4e00-\u9fff @#-]{1,28})喜欢(?P<object>[^，。；;!?！？\n]{1,36})"),
        "喜欢",
        0.04,
    ),
    (
        re.compile(r"(?P<subject>[\w\u4e00-\u9fff @#-]{1,28})采用(?P<object>[^，。；;!?！？\n]{1,36})"),
        "采用",
        0.08,
    ),
    (
        re.compile(r"(?P<subject>[\w\u4e00-\u9fff @#-]{1,28})正在(?P<object>[^，。；;!?！？\n]{2,36})"),
        "正在",
        0.02,
    ),
    (
        re.compile(r"(?P<subject>[\w\u4e00-\u9fff @#-]{1,28})是(?P<object>[^，。；;!?！？\n]{2,36})"),
        "是",
        0.0,
    ),
)

_GENERIC_SUBJECTS = {"主人", "用户", "群里", "本群", "当前群", "文档", "资料"}


def _evidence_from_hit(hit: ContextHit) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "type": hit.type,
        "id": hit.id,
        "quote": hit.content[:180],
        "source": hit.source,
        "scope": hit.scope,
        "scope_id": hit.scope_id,
    }
    if hit.type == "memory_card":
        evidence["card_id"] = hit.id
    elif hit.type == "doc_chunk":
        evidence["chunk_id"] = hit.id
    return evidence


def _subject_from_hit(hit: ContextHit) -> str:
    if hit.scope == "user":
        return f"用户{hit.scope_id}"
    if hit.scope == "group":
        return f"群{hit.scope_id}"
    if hit.type == "doc_chunk":
        return hit.title or hit.source or "文档知识"
    return hit.title or hit.source or "全局知识"


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_entity(value: str) -> str:
    value = re.sub(r"^[\s:：,，。；;、\-—]+", "", value or "")
    value = re.sub(r"[\s:：,，。；;、\-—]+$", "", value)
    return value.strip()[:48]


def _valid_entity(value: str) -> bool:
    if len(value) < 2:
        return False
    return value not in {"这是", "这个", "一种", "一个", "当前", "文档"}


def _quote_for(text: str, matched: str) -> str:
    index = text.find(matched)
    if index < 0:
        return text[:180]
    start = max(0, index - 24)
    end = min(len(text), index + len(matched) + 48)
    return text[start:end]


def _dedupe_results(results: list[GraphExtractionResult]) -> list[GraphExtractionResult]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[GraphExtractionResult] = []
    for result in results:
        key = (result.subject, result.predicate, result.object, str(result.evidence.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped
