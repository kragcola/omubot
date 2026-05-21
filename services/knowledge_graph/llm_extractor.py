"""LLM-assisted knowledge graph fact extractor.

Replaces the regex MVP for ContextHit -> graph fact extraction. The regex
extractor (KnowledgeGraphExtractor) is retained as an evaluation baseline
only; production routes through this extractor when an LLM client is
configured. No silent fallback to regex — when the LLM is unavailable we
return empty rather than re-introducing the conjunction-leak failure mode
that triggered the 2026-05-21 candidate purge.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from services.context.types import ContextHit
from services.knowledge_graph.extractor import GraphExtractionResult
from services.llm.llm_request import LLMRequest

_L = logger.bind(channel="knowledge_graph")

# Subjects/objects that LLMs commonly hallucinate from Chinese discourse
# markers, conjunctions, adverbs and pronouns. Any triple where the subject
# OR object normalises to one of these is rejected post-LLM.
_BANNED_ENTITIES: frozenset[str] = frozenset({
    # 连词
    "而不", "而且", "并且", "以及", "或者", "不过", "然而", "但是", "可是",
    "因此", "所以", "因为", "由于", "如果", "假如", "虽然", "尽管", "即便",
    "即使", "除非", "只要", "只有", "不仅", "不但", "况且", "何况", "于是",
    # 副词/语气词
    "也就", "通常", "其实", "其中", "也是", "就是", "还是", "总是", "已经",
    "正在", "曾经", "将要", "可能", "或许", "大概", "似乎", "几乎", "非常",
    "特别", "尤其", "确实", "当然", "果然", "竟然", "居然", "终于", "本来",
    "原来", "如此", "这样", "那样", "怎样", "怎么", "为何", "为什么",
    # 核心/主旨类抽象短语（容易被模型当 subject）
    "核心", "重点", "主要", "关键", "总之", "综上",
    "核心仍然", "重点是", "主要是", "关键在于",
    # 代词/泛指
    "他", "她", "它", "他们", "她们", "它们", "我", "我们", "你", "你们",
    "这", "那", "这是", "那是", "这个", "那个", "一个", "一种", "一些",
    "当前", "目前", "现在",
    # 模糊指代
    "文档", "资料", "信息", "内容", "情况", "状态", "结果", "原因",
    # 旧 regex 黑名单兼容
    "主人", "用户", "群里", "本群", "当前群",
})

# Predicate patterns that are too generic to be useful unless both ends are
# named entities. We treat bare 是 / 不是 specially: allow only when subject
# AND object are non-banned and at least one looks like a proper noun
# (contains digits, English letters, or distinctive uppercase Chinese
# characters) — see _looks_like_named_entity.
_GENERIC_BARE_PREDICATES: frozenset[str] = frozenset({"是", "不是", "为", "成为"})

_NAMED_ENTITY_HINT_RE = re.compile(
    r"[A-Za-z0-9_]|"  # English/digits → product or QQ id
    r"[一-鿿]+\d+|"  # Chinese + digits (e.g. 用户123)
    r"@",  # mention prefix
)

# Sentence splitter: split on Chinese/English terminal punctuation while
# keeping things tractable. Result is filtered by length to avoid feeding
# 2-char fragments to the LLM.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?\n\r])|(?<=[；;])\s*")
_MAX_SENTENCES_PER_HIT = 12
_MIN_SENTENCE_CHARS = 8
_MAX_SENTENCE_CHARS = 240
_MAX_FACTS_PER_SENTENCE = 2

_SYSTEM_PROMPT = (
    "你是 Omubot 的知识图谱事实抽取器。\n\n"
    "【任务】\n"
    "从一句话中找出**显式陈述**的事实三元组：subject 主语 / predicate 谓语 / object 宾语。\n\n"
    "【输出格式】\n"
    "严格 JSON，不要 Markdown 代码块。结构：\n"
    "{\"facts\": [\n"
    "  {\"subject\": \"...\", \"predicate\": \"...\", \"object\": \"...\","
    " \"confidence\": 0.0, \"evidence\": \"原句的关键片段\"}\n"
    "]}\n\n"
    "【硬约束 — 违反任意一条则返回空】\n"
    "1. subject 与 object **必须是命名实体或具体概念**："
    "人名/QQ 号/群号/角色名/产品/工具名/组织/文档名/技术术语/位置/时间。\n"
    "   禁止使用以下作 subject 或 object：\n"
    "   - 连词副词：而不、也就、通常、其实、然而、因此、不过、而且、所以、那么、就是、还是、"
    "虽然、尽管、即便、即使、于是、况且、何况、由于、因为、如果、假如、除非、只要\n"
    "   - 抽象主旨词：核心、重点、关键、主要、综上、总之、核心仍然\n"
    "   - 代词泛指：他、她、它、这、那、这是、那是、这个、那个、一个、一种、当前、目前、现在\n"
    "   - 模糊指代：文档、资料、信息、内容、情况、状态、结果、原因\n"
    "2. predicate 是动词或动宾短语：喜欢/不喜欢/讨厌/采用/位于/包含/支持/拒绝/使用/属于/创建/管理 等。\n"
    "   仅当 subject 与 object **都是明确命名实体**时才允许 \"是/不是\" 作为 predicate"
    "（如「Omubot 是 QQ 机器人」OK；「核心 是 学习辅助」NOT OK）。\n"
    "3. **忠于原文**：subject、predicate、object 都要能在原句字面或近义找到。**禁止推测、引申、改写**。\n"
    "4. **保留否定**：原文若是否定句（\"不是、不喜欢、没有\"），predicate 必须含否定词。漏否定 = 反向事实 = 禁止。\n"
    "5. confidence 保守估计，区间 [0.0, 0.85]。不要给出 0.85 以上的分数。\n"
    "6. 一句话最多 2 个三元组。**没有清晰事实时返回 `{\"facts\": []}`**——宁可漏抽不可错抽。\n\n"
    "【示范】\n\n"
    "Input: 用户123 喜欢音游\n"
    "Output: {\"facts\":[{\"subject\":\"用户123\",\"predicate\":\"喜欢\","
    "\"object\":\"音游\",\"confidence\":0.82,\"evidence\":\"用户123 喜欢音游\"}]}\n\n"
    "Input: 而不是核心仍然学习辅助\n"
    "Output: {\"facts\":[]}\n\n"
    "Input: Omubot 管理端采用雾青控制台风格\n"
    "Output: {\"facts\":[{\"subject\":\"Omubot 管理端\",\"predicate\":\"采用\","
    "\"object\":\"雾青控制台风格\",\"confidence\":0.78,"
    "\"evidence\":\"Omubot 管理端采用雾青控制台风格\"}]}\n\n"
    "Input: 通常我们会避免直接修改主分支\n"
    "Output: {\"facts\":[]}\n\n"
    "Input: 用户1416930401 不喜欢被 at\n"
    "Output: {\"facts\":[{\"subject\":\"用户1416930401\",\"predicate\":\"不喜欢\","
    "\"object\":\"被 at\",\"confidence\":0.8,"
    "\"evidence\":\"用户1416930401 不喜欢被 at\"}]}\n"
)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {"facts": loaded}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {"facts": []}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {"facts": []}
    except Exception:
        return {"facts": []}


def _looks_like_named_entity(value: str) -> bool:
    """Heuristic: at least one digit, English letter, @, or a 3+ char run.

    The point is to gate bare "是/不是" — we want to allow named-entity
    pairs through and reject generic noun pairs. A 4-char Chinese run is
    treated as named-entity-ish to keep recall reasonable.
    """
    if not value:
        return False
    if _NAMED_ENTITY_HINT_RE.search(value):
        return True
    return len(value) >= 4


def _validate_fact(
    subject: str,
    predicate: str,
    obj: str,
    *,
    confidence: float,
    sentence: str,
) -> tuple[bool, str]:
    if not subject or not predicate or not obj:
        return False, "empty_field"
    if subject in _BANNED_ENTITIES or obj in _BANNED_ENTITIES:
        return False, "banned_entity"
    if len(subject) < 2 or len(obj) < 2:
        return False, "too_short"
    if len(subject) > 48 or len(obj) > 48:
        return False, "too_long"
    # Reject when subject or object is essentially the whole sentence
    # (LLM cop-out where it puts the full clause into one slot). Tight
    # margin (-1) so legitimate triples like ("用户123","喜欢","音游") in
    # the 7-char sentence "用户123喜欢音游" survive — only catches the
    # truly-equal case where one slot ate the entire input.
    if len(subject) >= len(sentence) - 1 or len(obj) >= len(sentence) - 1:
        return False, "ate_sentence"
    if predicate in _GENERIC_BARE_PREDICATES and not (
        _looks_like_named_entity(subject) and _looks_like_named_entity(obj)
    ):
        return False, "bare_copula_without_named_entity"
    if confidence < 0.0 or confidence > 0.85:
        return False, "confidence_out_of_range"
    return True, ""


_TERMINAL_PUNCT_RE = re.compile(r"[。！？!?；;\s]+$")


def _split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text or "").strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned) if part and part.strip()]
    sentences: list[str] = []
    for part in parts:
        # Drop trailing terminal punctuation so the sentence the LLM sees
        # is "用户123喜欢音游" rather than "用户123喜欢音游。" — keeps prompt
        # and evidence clean and matches sentence-keyed test fixtures.
        trimmed = _TERMINAL_PUNCT_RE.sub("", part).strip()
        if len(trimmed) < _MIN_SENTENCE_CHARS:
            continue
        if len(trimmed) > _MAX_SENTENCE_CHARS:
            trimmed = trimmed[:_MAX_SENTENCE_CHARS]
        sentences.append(trimmed)
        if len(sentences) >= _MAX_SENTENCES_PER_HIT:
            break
    return sentences


def _evidence_from_hit(hit: ContextHit, sentence: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "type": hit.type,
        "id": hit.id,
        "quote": sentence[:240],
        "source": hit.source,
        "scope": hit.scope,
        "scope_id": hit.scope_id,
    }
    if hit.type == "memory_card":
        evidence["card_id"] = hit.id
    elif hit.type == "doc_chunk":
        evidence["chunk_id"] = hit.id
    return evidence


class LLMGraphExtractor:
    """Single-sentence LLM-driven graph triple extractor with hard validation."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client

    async def extract_from_hits(self, hits: list[ContextHit]) -> list[GraphExtractionResult]:
        if self._llm_client is None or not hasattr(self._llm_client, "_call"):
            _L.debug("llm graph extractor skipped | reason=no_llm_client")
            return []
        results: list[GraphExtractionResult] = []
        for hit in hits:
            if hit.type not in {"memory_card", "doc_chunk"}:
                continue
            sentences = _split_sentences(hit.content)
            if not sentences:
                continue
            for sentence in sentences:
                facts = await self._extract_one(sentence, hit)
                results.extend(facts)
        return _dedupe_results(results)

    async def _extract_one(
        self, sentence: str, hit: ContextHit,
    ) -> list[GraphExtractionResult]:
        try:
            request = LLMRequest(
                task="graph_review",
                static_blocks=[],
                stable_blocks=[_SYSTEM_PROMPT],
                user_messages=[{"role": "user", "content": f"Input: {sentence}"}],
                max_tokens=400,
                requires_capabilities=("chat",),
            )
            response = await self._llm_client._call(request)
        except Exception as exc:
            _L.warning(
                "llm graph extract call failed | hit_id={} type={} err={}",
                hit.id, hit.type, type(exc).__name__,
            )
            return []
        raw = str(response.get("text", "")).strip()
        data = _extract_json_object(raw)
        facts = data.get("facts", [])
        if not isinstance(facts, list):
            return []

        accepted: list[GraphExtractionResult] = []
        for item in facts[:_MAX_FACTS_PER_SENTENCE]:
            if not isinstance(item, dict):
                continue
            subject = _clean_entity(str(item.get("subject", "")))
            predicate = _clean_entity(str(item.get("predicate", "")))
            obj = _clean_entity(str(item.get("object", "")))
            try:
                confidence = float(item.get("confidence", 0.0))
            except Exception:
                confidence = 0.0
            ok, reason = _validate_fact(
                subject, predicate, obj,
                confidence=confidence,
                sentence=sentence,
            )
            if not ok:
                _L.debug(
                    "llm graph fact rejected | reason={} subject={!r} predicate={!r} object={!r}",
                    reason, subject, predicate, obj,
                )
                continue
            accepted.append(GraphExtractionResult(
                subject=subject,
                predicate=predicate,
                object=obj,
                confidence=max(0.0, min(0.85, confidence)),
                source=f"context:{hit.type}:llm",
                evidence=_evidence_from_hit(hit, sentence),
            ))
        return accepted


def _clean_entity(value: str) -> str:
    value = re.sub(r"^[\s:：,，。；;、\-—\"'“”‘’]+", "", value or "")
    value = re.sub(r"[\s:：,，。；;、\-—\"'“”‘’]+$", "", value)
    return value.strip()[:48]


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
