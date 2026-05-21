"""Unit tests for LLMGraphExtractor (PR2, 2026-05-21).

Replaces the regex MVP for ContextHit -> graph fact extraction. The
core failure mode regex couldn't reject was Chinese conjunctions and
adverbs leaking into ``subject``/``object`` positions (e.g. ``而不``,
``也就``, ``通常``, ``核心仍然``). These tests lock in the new
multi-layer defence: prompt-level constraints, post-LLM banlist,
named-entity gating for bare 是/不是 predicates, and confidence
clamping at 0.85.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.context.types import ContextHit
from services.knowledge_graph.llm_extractor import (
    LLMGraphExtractor,
    _split_sentences,
    _validate_fact,
)


class _ScriptedClient:
    def __init__(self, sentence_to_response: dict[str, str]) -> None:
        self._responses = sentence_to_response
        self.calls: list[str] = []

    async def _call(self, request: Any) -> dict[str, str]:
        sentence = ""
        for msg in getattr(request, "user_messages", []) or []:
            content = msg.get("content") if isinstance(msg, dict) else ""
            if isinstance(content, str) and content.startswith("Input:"):
                sentence = content[len("Input:"):].strip()
        self.calls.append(sentence)
        return {"text": self._responses.get(sentence, '{"facts": []}')}


@pytest.mark.asyncio
async def test_returns_empty_when_llm_client_missing() -> None:
    extractor = LLMGraphExtractor(llm_client=None)
    hits = [
        ContextHit(
            id="card_1",
            type="memory_card",
            content="用户123喜欢音游",
            score=1.0,
            source="test",
        ),
    ]
    assert await extractor.extract_from_hits(hits) == []


@pytest.mark.asyncio
async def test_extracts_clean_named_entity_triple() -> None:
    client = _ScriptedClient({
        "用户123喜欢音游": (
            '{"facts":[{"subject":"用户123","predicate":"喜欢",'
            '"object":"音游","confidence":0.82,"evidence":"喜欢音游"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="card_1",
            type="memory_card",
            content="用户123喜欢音游。",
            score=1.0,
            source="test",
        ),
    ])
    assert len(results) == 1
    assert results[0].subject == "用户123"
    assert results[0].predicate == "喜欢"
    assert results[0].object == "音游"
    assert results[0].confidence == pytest.approx(0.82, abs=0.001)
    assert results[0].source == "context:memory_card:llm"


@pytest.mark.asyncio
async def test_rejects_banned_chinese_conjunction_subject() -> None:
    """The exact failure mode that triggered the 2026-05-21 candidate purge."""
    client = _ScriptedClient({
        "而不是核心仍然学习辅助功能": (
            '{"facts":[{"subject":"而不","predicate":"是",'
            '"object":"核心仍然学习辅助","confidence":0.9,'
            '"evidence":"而不是"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_1",
            type="doc_chunk",
            content="而不是核心仍然学习辅助功能。",
            score=0.5,
            source="docs/x.md",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_rejects_banned_adverb_subject() -> None:
    client = _ScriptedClient({
        "通常我们会避免直接修改主分支": (
            '{"facts":[{"subject":"通常","predicate":"是",'
            '"object":"避免直接修改主分支","confidence":0.7,'
            '"evidence":"通常"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_2",
            type="doc_chunk",
            content="通常我们会避免直接修改主分支。",
            score=0.5,
            source="docs/y.md",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_rejects_bare_copula_without_named_entity() -> None:
    """Generic 是 between two non-named-entity nouns must be rejected."""
    client = _ScriptedClient({
        "核心是学习辅助": (
            '{"facts":[{"subject":"核心","predicate":"是",'
            '"object":"学习辅助","confidence":0.7,"evidence":"核心是学习辅助"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_3",
            type="doc_chunk",
            content="核心是学习辅助。",
            score=0.5,
            source="docs/z.md",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_accepts_bare_copula_with_named_entity() -> None:
    """``Omubot 是 QQ 机器人`` is fine — both ends are named entities."""
    client = _ScriptedClient({
        "Omubot 是 QQ 机器人": (
            '{"facts":[{"subject":"Omubot","predicate":"是",'
            '"object":"QQ 机器人","confidence":0.8,'
            '"evidence":"Omubot 是 QQ 机器人"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_4",
            type="doc_chunk",
            content="Omubot 是 QQ 机器人。",
            score=0.6,
            source="docs/intro.md",
        ),
    ])
    assert len(results) == 1
    assert results[0].subject == "Omubot"
    assert results[0].object == "QQ 机器人"


@pytest.mark.asyncio
async def test_rejects_confidence_above_clamp() -> None:
    """Prompt asks ≤0.85; if the model overshoots we drop the triple.

    This is intentional: confidence clamping protects the candidate
    queue from an LLM that ignores the upper bound.
    """
    client = _ScriptedClient({
        "用户123喜欢音游": (
            '{"facts":[{"subject":"用户123","predicate":"喜欢",'
            '"object":"音游","confidence":0.99,"evidence":"喜欢音游"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="card_5",
            type="memory_card",
            content="用户123喜欢音游。",
            score=1.0,
            source="test",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_handles_malformed_json_gracefully() -> None:
    client = _ScriptedClient({
        "随便一句": "this is not json",
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_bad",
            type="doc_chunk",
            content="随便一句。",
            score=0.5,
            source="docs/bad.md",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_strips_markdown_code_fence_around_json() -> None:
    client = _ScriptedClient({
        "用户123喜欢音游": (
            '```json\n{"facts":[{"subject":"用户123","predicate":"喜欢",'
            '"object":"音游","confidence":0.78,"evidence":"喜欢音游"}]}\n```'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="card_md",
            type="memory_card",
            content="用户123喜欢音游。",
            score=1.0,
            source="test",
        ),
    ])
    assert len(results) == 1
    assert results[0].subject == "用户123"


@pytest.mark.asyncio
async def test_call_failure_returns_empty() -> None:
    class _FailingClient:
        async def _call(self, _request: Any) -> dict[str, str]:
            raise RuntimeError("model timeout")

    extractor = LLMGraphExtractor(llm_client=_FailingClient())
    results = await extractor.extract_from_hits([
        ContextHit(
            id="card_fail",
            type="memory_card",
            content="用户123喜欢音游。",
            score=1.0,
            source="test",
        ),
    ])
    assert results == []


@pytest.mark.asyncio
async def test_splits_long_chunk_into_sentences() -> None:
    """A long doc chunk fans out into multiple LLM calls, one per sentence."""
    client = _ScriptedClient({
        "Omubot 采用 Docker 部署": (
            '{"facts":[{"subject":"Omubot","predicate":"采用",'
            '"object":"Docker 部署","confidence":0.78,'
            '"evidence":"采用 Docker 部署"}]}'
        ),
        "管理端使用 Vue 3": (
            '{"facts":[{"subject":"管理端","predicate":"使用",'
            '"object":"Vue 3","confidence":0.74,'
            '"evidence":"管理端使用 Vue 3"}]}'
        ),
    })
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_long",
            type="doc_chunk",
            content="Omubot 采用 Docker 部署。管理端使用 Vue 3。",
            score=0.5,
            source="docs/arch.md",
        ),
    ])
    assert len(results) == 2
    objs = {r.object for r in results}
    assert objs == {"Docker 部署", "Vue 3"}
    assert sorted(client.calls) == sorted(["Omubot 采用 Docker 部署", "管理端使用 Vue 3"])


@pytest.mark.asyncio
async def test_skips_short_sentence_fragments() -> None:
    """Fragments shorter than _MIN_SENTENCE_CHARS are dropped before LLM."""
    client = _ScriptedClient({})
    extractor = LLMGraphExtractor(llm_client=client)
    results = await extractor.extract_from_hits([
        ContextHit(
            id="chunk_tiny",
            type="doc_chunk",
            content="嗯。是吗？",  # both fragments under 8 chars
            score=0.5,
            source="docs/tiny.md",
        ),
    ])
    assert results == []
    assert client.calls == []


def test_split_sentences_respects_terminal_punctuation() -> None:
    sentences = _split_sentences("Omubot 是 QQ 机器人。它 采用 Docker 部署！还支持 Vue 管理端？")
    assert len(sentences) == 3
    assert sentences[0].startswith("Omubot")


def test_validate_fact_rejects_oversized_object_eating_sentence() -> None:
    sentence = "Omubot喜欢音游"  # 10 chars, object spans the whole thing
    ok, reason = _validate_fact(
        "Omubot", "喜欢", "Omubot喜欢音游",
        confidence=0.8, sentence=sentence,
    )
    # object length == sentence length → ate_sentence guard fires
    assert ok is False
    assert reason == "ate_sentence"


def test_validate_fact_rejects_negative_confidence() -> None:
    ok, reason = _validate_fact(
        "Omubot", "采用", "Docker",
        confidence=-0.1, sentence="Omubot 采用 Docker 部署",
    )
    assert ok is False
    assert reason == "confidence_out_of_range"
