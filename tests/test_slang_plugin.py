from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from kernel.config import BotConfig
from kernel.types import Identity, MessageContext, PluginContext, PromptContext
from plugins.slang.plugin import SlangPlugin
from services.conversation_archive import ConversationArchive
from services.slang import SlangDriftAssessment
from services.tools.context import ToolContext


class _DummyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return [
            {
                "role": "user",
                "speaker": "Alice(10001)",
                "content_text": "猫饼就是群里说离谱但可爱的操作",
                "message_id": 10,
                "created_at": 1.0,
            },
            {
                "role": "user",
                "speaker": "Bob(10002)",
                "content_text": "对，这个猫饼太典了",
                "message_id": 11,
                "created_at": 2.0,
            },
        ]


class _EmptyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return []


class _FakeDriftReviewer:
    def __init__(self, verdict: str, confidence: float = 0.9, reason: str = "test") -> None:
        self.verdict = verdict
        self.confidence = confidence
        self.reason = reason

    async def review_drift(self, **_kwargs):
        return SlangDriftAssessment(
            verdict=self.verdict,  # type: ignore[arg-type]
            confidence=self.confidence,
            reason=self.reason,
            reviewed=True,
        )


class _DummyLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"群里说离谱但可爱的操作",'
                '"aliases":["猫猫饼"],"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.7,"reason":"有群内解释","repeat_policy":"understand_only"}]}'
            )
        }


class _AliasStoplistLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        del system_blocks, messages, tools, max_tokens, thinking
        return {
            "text": (
                '{"terms":[{"term":"project sekai","meaning":"群内用来指音游",'
                '"aliases":["P J S K"],"evidence":"project sekai 也叫 P J S K",'
                '"confidence":0.7,"reason":"有群内解释","repeat_policy":"understand_only"}]}'
            )
        }


class _DailyReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            payload = json.loads(str(messages[0].get("content", "{}")))
            results = []
            for candidate in payload.get("candidates", []):
                results.append({
                    "index": candidate.get("index", 0),
                    "approved": True,
                    "term": candidate.get("term") or "猫饼",
                    "meaning": "网络梗，用来形容离谱但可爱的操作",
                    "aliases": ["猫猫饼"],
                    "confidence": 0.91,
                    "reason": "群内证据可靠",
                    "repeat_policy": "understand_only",
                    "is_public_meme": True,
                })
            return {"text": json.dumps({"results": results}, ensure_ascii=False)}
        if "复核器" in system_text:
            return {
                "text": (
                    '{"approved":true,"term":"猫饼","meaning":"网络梗，用来形容离谱但可爱的操作",'
                    '"aliases":["猫猫饼"],"confidence":0.91,"reason":"群内证据与搜索结果一致",'
                    '"repeat_policy":"understand_only","is_public_meme":true}'
                )
            }
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"群里说离谱但可爱的操作",'
                '"aliases":["猫猫饼"],"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.78,"reason":"有群内解释","repeat_policy":"understand_only"}]}'
            )
        }


class _RejectCandidateReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            payload = json.loads(str(messages[0].get("content", "{}")))
            results = []
            for candidate in payload.get("candidates", []):
                results.append({
                    "index": candidate.get("index", 0),
                    "approved": False,
                    "term": candidate.get("term") or "普通词",
                    "meaning": candidate.get("meaning") or "普通词，不是群内黑话",
                    "aliases": [],
                    "confidence": 0.88,
                    "reason": "普通词，不是群内黑话",
                    "repeat_policy": "understand_only",
                    "is_public_meme": False,
                })
            return {"text": json.dumps({"results": results}, ensure_ascii=False)}
        return {"text": '{"terms":[]}'}


class _RejectCandidateWithLowApprovalConfidenceLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            payload = json.loads(str(messages[0].get("content", "{}")))
            return {
                "text": json.dumps({
                    "results": [
                        {
                            "index": candidate.get("index", 0),
                            "decision": "reject",
                            "approved": False,
                            "term": candidate.get("term") or "普通问句",
                            "meaning": candidate.get("meaning") or "普通问句，不是黑话",
                            "aliases": [],
                            "decision_confidence": 0.9,
                            "confidence": 0.1,
                            "reason": "明确只是普通问句，不应进入黑话库",
                            "repeat_policy": "understand_only",
                            "is_public_meme": False,
                        }
                        for candidate in payload.get("candidates", [])
                    ]
                }, ensure_ascii=False)
            }
        return {"text": '{"terms":[]}'}


class _ObserveCandidateReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            payload = json.loads(str(messages[0].get("content", "{}")))
            return {
                "text": json.dumps({
                    "results": [
                        {
                            "index": candidate.get("index", 0),
                            "decision": "observe",
                            "approved": False,
                            "term": candidate.get("term") or "可能黑话",
                            "meaning": candidate.get("meaning") or "含义不清",
                            "aliases": [],
                            "decision_confidence": 0.8,
                            "confidence": 0.2,
                            "reason": "可能有群内含义，但证据不足",
                            "repeat_policy": "understand_only",
                            "is_public_meme": False,
                        }
                        for candidate in payload.get("candidates", [])
                    ]
                }, ensure_ascii=False)
            }
        return {"text": '{"terms":[]}'}


class _DecisionOnlyCandidateReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            payload = json.loads(str(messages[0].get("content", "{}")))
            return {
                "text": json.dumps({
                    "results": [
                        {
                            "index": candidate.get("index", 0),
                            "decision": "reject",
                            "term": candidate.get("term") or "普通问句",
                            "meaning": candidate.get("meaning") or "普通问句，不是黑话",
                            "decision_confidence": 0.88,
                            "confidence": 0.1,
                            "reason": "明确不是黑话",
                            "repeat_policy": "understand_only",
                        }
                        for candidate in payload.get("candidates", [])
                    ]
                }, ensure_ascii=False)
            }
        return {"text": '{"terms":[]}'}


class _MissingBatchItemFallsBackLLM:
    def __init__(self) -> None:
        self.single_calls = 0

    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            return {"text": '{"results":[]}'}
        if "复核器" in system_text:
            self.single_calls += 1
            return {
                "text": json.dumps({
                    "decision": "reject",
                    "approved": False,
                    "term": "普通问句",
                    "meaning": "普通问句，不是黑话",
                    "decision_confidence": 0.9,
                    "confidence": 0.1,
                    "reason": "单条 fallback 判定非黑话",
                    "repeat_policy": "understand_only",
                    "is_public_meme": False,
                }, ensure_ascii=False)
            }
        return {"text": '{"terms":[]}'}


class _FailedCandidateReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "批量复核器" in system_text:
            return {"text": '{"results":[]}'}
        if "复核器" in system_text:
            return {"text": '{"missing_decision":true}'}
        return {"text": '{"terms":[]}'}


class _LowSignalLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"猫饼","aliases":[],"evidence":"猫饼",'
                '"confidence":0.66,"reason":"只是重复原词","repeat_policy":"understand_only"}]}'
            )
        }


class _GenericMeaningLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"一个梗","aliases":["猫饼","哈哈"],'
                '"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.66,"reason":"解释过于泛化","repeat_policy":"understand_only"}]}'
            )
        }


class _DailyReviewFallbackLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "复核器" in system_text:
            return {
                "text": (
                    '{"approved":true,"term":"猫饼","meaning":"一个梗","aliases":["猫猫饼","哈哈"],'
                    '"confidence":0.91,"reason":"AI 解释退化","repeat_policy":"understand_only","is_public_meme":true}'
                )
            }
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"群里说离谱但可爱的操作",'
                '"aliases":["猫猫饼"],"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.78,"reason":"有群内解释","repeat_policy":"understand_only"}]}'
            )
        }


class _BatchSensitiveDailyReviewLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        user_text = str(messages[0].get("content", "")) if messages else ""
        if "候选提取器" in system_text:
            lines = [line for line in user_text.splitlines() if line.strip()]
            if len(lines) > 10:
                return {"text": '{"terms":[]}'}
            return {
                "text": (
                    '{"terms":[{"term":"猫饼","meaning":"群里说离谱但可爱的操作",'
                    '"aliases":["猫猫饼"],"evidence":"猫饼就是群里说离谱但可爱的操作",'
                    '"confidence":0.78,"reason":"分批后能稳定抓到","repeat_policy":"understand_only"}]}'
                )
            }
        if "复核器" in system_text:
            return {
                "text": (
                    '{"approved":true,"term":"猫饼","meaning":"网络梗，用来形容离谱但可爱的操作",'
                    '"aliases":["猫猫饼"],"confidence":0.91,"reason":"群内证据与搜索结果一致",'
                    '"repeat_policy":"understand_only","is_public_meme":true}'
                )
            }
        return {"text": '{"terms":[]}'}


class _SemanticReviewLLM:
    def __init__(
        self,
        *,
        context_response: object,
        literal_response: object,
        compare_response: object,
        delay_s: float = 0.0,
    ) -> None:
        self.context_response = context_response
        self.literal_response = literal_response
        self.compare_response = compare_response
        self.delay_s = delay_s
        self.calls: list[dict[str, object]] = []

    async def _call_slang_review(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        del tools, max_tokens, thinking
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        payload = json.loads(str(messages[0].get("content", "{}")))
        stage = "compare"
        if "上下文推断" in system_text:
            stage = "context"
        elif "裸推断" in system_text:
            stage = "literal"
        self.calls.append({"stage": stage, "payload": payload})
        if self.delay_s:
            import asyncio

            await asyncio.sleep(self.delay_s)
        response = {
            "context": self.context_response,
            "literal": self.literal_response,
            "compare": self.compare_response,
        }[stage]
        if isinstance(response, str):
            return {"text": response}
        return {"text": json.dumps(response, ensure_ascii=False)}


class _FailAfterExtractLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        raise RuntimeError("review failed")


class _PendingReviewRejectLLM:
    async def _call(self, system_blocks, messages, tools=None, max_tokens=1024, thinking=None):
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        if "复核器" in system_text:
            return {
                "text": (
                    '{"approved":false,"term":"你觉得不队","meaning":"普通问句，不是黑话",'
                    '"aliases":[],"confidence":0.93,"reason":"普通短句，不应入库",'
                    '"repeat_policy":"understand_only","is_public_meme":false}'
                )
            }
        return {"text": '{"terms":[]}'}


class _SearchTool:
    name = "web_search"

    async def execute(self, ctx, **kwargs):
        return "1. 猫饼是什么梗\n   https://example.com\n   指离谱但可爱的操作。"


class _FailingSearchTool:
    name = "web_search"

    async def execute(self, ctx, **kwargs):
        return "搜索失败: network"


class _NoisyThenUsefulSearchTool:
    name = "web_search"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def execute(self, ctx, **kwargs):
        query = str(kwargs.get("query") or "")
        self.queries.append(query)
        if len(self.queries) == 1:
            return "搜索失败: network"
        if len(self.queries) == 2:
            return "未找到相关结果"
        return "1. 猫饼是什么梗\n   https://example.com\n   指离谱但可爱的操作。"


class _ToolRegistry:
    def __init__(self, tool):
        self._tool = tool

    def get(self, name: str):
        return self._tool if name == "web_search" else None


class _SingleMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return [
            {
                "role": "user",
                "speaker": "Alice(10001)",
                "content_text": "猫饼就是群里说离谱但可爱的操作",
                "message_id": 10,
                "created_at": 1.0,
            },
        ]


class _ExplodingMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        raise RuntimeError("message log failed")


class _EmptyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return []


class _SlowMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        import asyncio

        await asyncio.sleep(0.05)
        return [
            {
                "role": "user",
                "speaker": "Alice(10001)",
                "content_text": "猫饼就是群里说离谱但可爱的操作",
                "message_id": 10,
                "created_at": 1.0,
            },
        ]


class _ManyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        rows: list[dict] = []
        for idx in range(limit):
            rows.append({
                "role": "user",
                "speaker": f"User{idx}(100{idx:02d})",
                "content_text": "日常闲聊内容",
                "message_id": idx + 1,
                "created_at": float(idx + 1),
            })
        rows[-1] = {
            "role": "user",
            "speaker": "Alice(10001)",
            "content_text": "猫饼就是群里说离谱但可爱的操作",
            "message_id": limit,
            "created_at": float(limit),
        }
        return rows


@pytest.mark.asyncio
async def test_slang_plugin_extracts_candidates_and_injects_only_approved(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 1

        assert plugin.store is not None
        terms, _total = await plugin.store.list_terms(group_id="100")
        assert len(terms) == 1
        assert terms[0].status == "candidate"

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="今天猫饼了",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert prompt_ctx.blocks == []

        await plugin.store.set_status(terms[0].term_id, "approved")
        await plugin.on_pre_prompt(prompt_ctx)
        assert len(prompt_ctx.blocks) == 1
        assert prompt_ctx.blocks[0].label == "群内黑话"
        assert "猫饼" in prompt_ctx.blocks[0].text
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_manual_extract_uses_archive_cursor_when_available(tmp_path):
    archive = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    await archive.init()
    await archive.record(
        group_id="100",
        role="user",
        speaker="Alice(10001)",
        content_text="猫饼就是群里说离谱但可爱的操作",
        content_json=None,
        message_id=10,
        created_at=1.0,
    )
    await archive.record(
        group_id="100",
        role="user",
        speaker="Bob(10002)",
        content_text="对，这个猫饼太典了",
        content_json=None,
        message_id=11,
        created_at=2.0,
    )
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=archive, llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        first = await plugin.run_manual_extract(group_id="100", limit=20)
        assert first["ok"] is True
        assert first["candidates"] == 1

        second = await plugin.run_manual_extract(group_id="100", limit=20)
        assert second["ok"] is True
        assert second["scanned"] == 0
        assert second["candidates"] == 0
    finally:
        await plugin.on_shutdown(ctx)
        await archive.close()


@pytest.mark.asyncio
async def test_slang_daily_review_uses_archive_cursor_when_available(tmp_path):
    archive = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    await archive.init()
    await archive.record(
        group_id="100",
        role="user",
        speaker="Alice(10001)",
        content_text="猫饼就是群里说离谱但可爱的操作",
        content_json=None,
        message_id=10,
        created_at=1.0,
    )
    await archive.record(
        group_id="100",
        role="user",
        speaker="Bob(10002)",
        content_text="对，这个猫饼太典了",
        content_json=None,
        message_id=11,
        created_at=2.0,
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=archive,
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        first = await plugin.run_daily_ai_review(ctx, settings=settings, group_id="100")
        assert first["ok"] is True
        assert first["scanned"] == 2
        assert first["ai_approved"] == 1

        second = await plugin.run_daily_ai_review(ctx, settings=settings, group_id="100")
        assert second["ok"] is True
        assert second["scanned"] == 0
        assert second["ai_approved"] == 0
    finally:
        await plugin.on_shutdown(ctx)
        await archive.close()


@pytest.mark.asyncio
async def test_slang_plugin_prefers_exact_source_row_and_conservative_occurrences():
    rows = [
        {"content_text": "zabcx", "message_id": 1},
        {"content_text": "abc!", "message_id": 2},
        {"content_text": "abc", "message_id": 3},
    ]

    source = SlangPlugin._pick_source_row("abc", rows)
    assert source["message_id"] == 3
    assert SlangPlugin._estimate_occurrences("abc", [], rows) == 2


@pytest.mark.asyncio
async def test_slang_plugin_records_run_and_buffers_below_min_count(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_SingleMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["run_id"]
        assert result["candidates"] == 0

        terms, total = await plugin.store.list_terms(group_id="100")
        assert terms == []
        assert total == 0
        pending, pending_total = await plugin.store.list_pending(group_id="100")
        assert pending_total == 1
        assert pending[0].count == 1
        runs = await plugin.store.list_extraction_runs()
        assert runs[0].run_id == result["run_id"]
        assert runs[0].promoted_candidates == 0
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_buffers_message_hits_and_flushes(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        term = await plugin.store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            scope="group",
            group_id="100",
            status="approved",
        )
        message_ctx = MessageContext(
            session_id="group_100",
            group_id="100",
            user_id="u2",
            content="今天又猫饼了",
            raw_message={},
            message_id=200,
        )

        await plugin.on_message(message_ctx)
        await plugin.on_message(message_ctx)
        before_flush = await plugin.store.get_term(term.term_id)
        assert before_flush is not None
        assert before_flush.usage_count == 0

        changed = await plugin._flush_hit_buffer()
        assert changed == 1
        after_flush = await plugin.store.get_term(term.term_id)
        assert after_flush is not None
        assert after_flush.usage_count == 1
        assert "u2" in after_flush.unique_users

        await plugin.on_shutdown(ctx)
        plugin.store = None
    finally:
        if plugin.store is not None:
            await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_buffers_hits_without_message_id_as_distinct_events(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        term = await plugin.store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            scope="group",
            group_id="100",
            status="approved",
        )

        await plugin.on_message(MessageContext(
            session_id="group_100",
            group_id="100",
            user_id="u2",
            content="第一条猫饼",
            raw_message={},
            message_id=None,
        ))
        await plugin.on_message(MessageContext(
            session_id="group_100",
            group_id="100",
            user_id="u3",
            content="第二条猫饼",
            raw_message={},
            message_id=None,
        ))

        changed = await plugin._flush_hit_buffer()
        assert changed == 2
        after_flush = await plugin.store.get_term(term.term_id)
        assert after_flush is not None
        assert after_flush.usage_count == 2
        assert after_flush.unique_users == ["u2", "u3"]
        observations = await plugin.store.list_observations(term.term_id)
        assert len(observations) == 2
        assert {item.raw_text for item in observations} == {"第一条猫饼", "第二条猫饼"}
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_extractor_drops_candidate_when_alias_is_stoplisted(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_SingleMessageLog(), llm_client=_AliasStoplistLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.stoplist = ["pjsk"]
        await plugin.store.save_settings(settings)

        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["extracted"] == 0
        terms, total = await plugin.store.list_terms(group_id="100")
        assert terms == []
        assert total == 0
        pending, pending_total = await plugin.store.list_pending(group_id="100")
        assert pending == []
        assert pending_total == 0
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_approves_once_per_day(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["ai_approved"] == 1

        terms, total = await plugin.store.list_terms(review_filter="needs_human_review")
        assert total == 1
        assert terms[0].status == "approved"
        assert terms[0].source == "ai_auto_review"
        assert terms[0].meta["ai_approved"] is True
        assert terms[0].meta["human_reviewed"] is False

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="猫饼",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert len(prompt_ctx.blocks) == 1

        skipped = await plugin.run_daily_ai_review_if_due(ctx)
        assert skipped["skipped"] == "already_ran"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_if_due_reviews_unreviewed_candidates(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = False
        await plugin.store.save_settings(settings)
        await plugin.store.create_term(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            scope="group",
            group_id="100",
            status="candidate",
            confidence=0.7,
            meta={"evidence": "猫饼就是群里说离谱但可爱的操作"},
            source="extractor",
        )

        result = await plugin.run_daily_ai_review_if_due(ctx)

        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        terms, total = await plugin.store.list_terms(review_filter="candidate_ai_approved")
        assert total == 1
        assert terms[0].meta["candidate_review_state"] == "suggested"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_if_due_replays_open_drift_reviews(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        await plugin.store.save_settings(settings)
        term = await plugin.store.create_term(
            term="没米",
            meaning="没有钱或没有资源",
            scope="group",
            group_id="100",
            status="approved",
            confidence=0.9,
        )
        plugin.store.set_drift_reviewer(_FakeDriftReviewer("real_drift", reason="先制造 open drift"))
        await plugin.store.upsert_candidate(
            term="没米",
            meaning="固定成员的新称呼",
            group_id="100",
            raw_text="没米来了",
            confidence=0.9,
            settings=settings,
        )
        open_reviews, open_total = await plugin.store.list_drift_reviews(status="open")
        assert open_total == 1
        assert open_reviews[0].term_id == term.term_id

        plugin.store.set_drift_reviewer(_FakeDriftReviewer("same_meaning", reason="回放判定同义"))
        result = await plugin.run_daily_ai_review_if_due(ctx)

        assert result["ok"] is True
        assert result["drift_replay_reviewed"] == 1
        assert result["drift_replay_closed_same_meaning"] == 1
        _open_reviews, open_after = await plugin.store.list_drift_reviews(status="open")
        assert open_after == 0
        rejected, rejected_total = await plugin.store.list_drift_reviews(status="rejected")
        assert rejected_total == 1
        assert rejected[0].meta["drift_semantic_verdict"] == "same_meaning"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_can_auto_approve_without_search_when_group_evidence_is_sufficient(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_FailingSearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review(ctx, settings=settings)
        assert result["ok"] is True
        assert result["ai_approved"] == 1

        terms, total = await plugin.store.list_terms(review_filter="needs_human_review")
        assert total == 1
        assert terms[0].status == "approved"
        assert terms[0].source == "ai_auto_review"
        assert terms[0].meta["daily_ai_review"] is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_keeps_singleton_without_search_evidence(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_SingleMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_FailingSearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_search_enabled = True
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review(ctx, settings=settings)
        assert result["ok"] is True
        assert result["ai_approved"] == 0
        assert result["pending_reviewed"] == 0
        assert result["pending_kept"] == 1
        assert result["semantic_reviewed"] == 0

        _terms, total = await plugin.store.list_terms(group_id="100")
        assert total == 0
        pending, pending_total = await plugin.store.list_pending(group_id="100")
        assert pending_total == 1
        assert pending[0].term == "猫饼"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_search_tries_later_queries_after_failures(tmp_path):
    tool = _NoisyThenUsefulSearchTool()
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(tool),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        settings.daily_ai_review_search_enabled = True
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review(ctx, settings=settings)
        assert result["ok"] is True
        assert result["ai_approved"] == 1
        assert len(tool.queries) >= 3
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_force_review_all_pending_bypasses_threshold_gate(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "", "no_info": True, "confidence": 0.12, "reason": "上下文不足"},
        literal_response={"meaning": "常见字面义", "confidence": 0.9, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = False
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        await plugin.store.upsert_candidate(
            term="继续说呢",
            meaning="普通问句",
            group_id="100",
            raw_text="继续说呢",
            confidence=0.4,
            min_count=999,
            observed_count=1,
        )
        await plugin.store.upsert_candidate(
            term="然后呢",
            meaning="普通问句",
            group_id="100",
            raw_text="然后呢",
            confidence=0.4,
            min_count=999,
            observed_count=2,
        )

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
            review_all_pending=True,
        )
        assert result["ok"] is True
        assert result["pending_reviewed"] == 2
        assert result["semantic_reviewed"] == 2
        assert result["semantic_no_info"] == 2
        assert len(llm.calls) == 2

        pending_after, pending_after_total = await plugin.store.list_pending(group_id="100")
        assert pending_after_total == 2
        assert all(item.meta["semantic_force_review"] is True for item in pending_after)
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_review_does_not_force_all_pending_by_default(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "", "no_info": True, "confidence": 0.12, "reason": "上下文不足"},
        literal_response={"meaning": "常见字面义", "confidence": 0.9, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        await plugin.store.upsert_candidate(
            term="继续说呢",
            meaning="普通问句",
            group_id="100",
            raw_text="继续说呢",
            confidence=0.4,
            min_count=999,
            observed_count=1,
        )

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["pending_kept"] == 1
        assert result["semantic_reviewed"] == 0
        assert llm.calls == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_mutes_semantically_similar_pending_items(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "普通问句", "no_info": False, "confidence": 0.93, "reason": "上下文一致"},
        literal_response={"meaning": "普通问句", "confidence": 0.92, "reason": "字面一致"},
        compare_response={"is_similar": True, "confidence": 0.95, "reason": "语义接近"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        pending_id = await plugin.store.upsert_candidate(
            term="你觉得不队",
            meaning="普通问句，不是黑话",
            group_id="100",
            raw_text="你觉得不队",
            confidence=0.5,
            min_count=999,
            observed_count=2,
        )
        assert pending_id is None
        _pending, pending_total = await plugin.store.list_pending(group_id="100")
        assert pending_total == 1

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["pending_reviewed"] == 1
        assert result["pending_rejected"] == 1
        assert result["semantic_rejected"] == 1

        _pending_after, pending_after_total = await plugin.store.list_pending(group_id="100")
        assert pending_after_total == 0
        muted_terms, muted_total = await plugin.store.list_terms(group_id="100", status="muted")
        assert muted_total == 1
        assert muted_terms[0].term == "你觉得不队"
        assert muted_terms[0].meta["semantic_is_similar"] is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_promotes_candidate_when_semantic_confirms(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "群内黑话义", "no_info": False, "confidence": 0.92, "reason": "上下文明确"},
        literal_response={"meaning": "普通字面义", "confidence": 0.9, "reason": "字面可知"},
        compare_response={"is_similar": False, "confidence": 0.94, "reason": "上下文与字面不同"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = False
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        pending_id = await plugin.store.upsert_candidate(
            term="pjsk",
            meaning="旧释义",
            group_id="100",
            raw_text="pjsk 这词又出现了",
            confidence=0.4,
            min_count=999,
            observed_count=2,
        )
        assert pending_id is None

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["pending_reviewed"] == 1
        assert result["semantic_kept"] == 1

        _pending_after, pending_after_total = await plugin.store.list_pending(group_id="100")
        assert pending_after_total == 0
        terms, total = await plugin.store.list_terms(group_id="100", status="candidate")
        assert total == 1
        assert terms[0].term == "pjsk"
        assert terms[0].meta["semantic_candidate_confirmed"] is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_auto_approves_semantic_candidate_when_enabled(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "群内黑话义", "no_info": False, "confidence": 0.95, "reason": "上下文明确"},
        literal_response={"meaning": "普通字面义", "confidence": 0.91, "reason": "字面可知"},
        compare_response={"is_similar": False, "confidence": 0.96, "reason": "上下文与字面不同"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        pending_id = await plugin.store.upsert_candidate(
            term="pjsk",
            meaning="旧释义",
            group_id="100",
            raw_text="pjsk 这词又出现了",
            confidence=0.4,
            min_count=999,
            observed_count=24,
        )
        assert pending_id is None

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["pending_reviewed"] == 1
        assert result["pending_approved"] == 1
        assert result["semantic_approved"] == 1

        terms, total = await plugin.store.list_terms(review_filter="needs_human_review")
        assert total == 1
        assert terms[0].status == "approved"
        assert terms[0].source == "ai_auto_review"
        assert terms[0].meta["semantic_auto_approved"] is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_force_review_existing_candidates_can_auto_approve(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="猫饼",
            meaning="群里说离谱但可爱的操作",
            group_id="100",
            raw_text="猫饼就是群里说离谱但可爱的操作",
            confidence=0.9,
            min_count=1,
            observed_count=5,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_approved"] == 1
        assert result["ai_approved"] == 1

        approved_terms, approved_total = await plugin.store.list_terms(group_id="100", status="approved")
        assert approved_total == 1
        assert approved_terms[0].term == "猫饼"
        assert approved_terms[0].meta["candidate_ai_auto_approved"] is True
        candidates, candidate_total = await plugin.store.list_terms(group_id="100", status="candidate")
        assert candidate_total == 0
        assert candidates == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_force_review_skips_previously_reviewed_candidates_by_default(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        reviewed_id = await plugin.store.upsert_candidate(
            term="已审猫饼",
            meaning="已审旧结果",
            group_id="100",
            raw_text="已审猫饼",
            confidence=0.9,
            min_count=1,
            observed_count=5,
        )
        fresh_id = await plugin.store.upsert_candidate(
            term="新猫饼",
            meaning="新候选",
            group_id="100",
            raw_text="新猫饼就是群里说离谱但可爱的操作",
            confidence=0.9,
            min_count=1,
            observed_count=5,
        )
        assert reviewed_id is not None
        assert fresh_id is not None
        await plugin.store.update_term(
            reviewed_id,
            meta={
                "candidate_reviewed": True,
                "candidate_review_approved": False,
                "candidate_review_state": "kept",
                "candidate_review_reason": "旧审核结果",
            },
        )

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_skipped_reviewed"] == 1

        still_reviewed = await plugin.store.get_term(reviewed_id)
        assert still_reviewed is not None
        assert still_reviewed.meta["candidate_review_reason"] == "旧审核结果"

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
            rerun_reviewed_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_skipped_reviewed"] == 0
        assert result["candidate_reviewed"] >= 1
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_ai_reject_archives_to_muted_queue(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_RejectCandidateReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="普通词",
            meaning="普通词，不是群内黑话",
            group_id="100",
            raw_text="普通词",
            confidence=0.6,
            min_count=1,
            observed_count=3,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_rejected"] == 1
        assert result["candidate_kept"] == 0

        term = await plugin.store.get_term(term_id)
        assert term is not None
        assert term.status == "muted"
        assert term.meta["ai_rejected"] is True
        assert term.meta["candidate_review_state"] == "rejected"
        assert term.meta["review_decision"] == "denied"

        rejected_terms, rejected_total = await plugin.store.list_terms(review_filter="ai_rejected")
        assert rejected_total == 1
        assert rejected_terms[0].term_id == term_id
        unreviewed_terms, unreviewed_total = await plugin.store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 0
        assert unreviewed_terms == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_reject_uses_decision_confidence_not_approval_probability(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_RejectCandidateWithLowApprovalConfidenceLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="什么游戏",
            meaning="普通问句，不是群内黑话",
            group_id="100",
            raw_text="这是什么游戏",
            confidence=0.6,
            min_count=1,
            observed_count=1,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_rejected"] == 1
        assert result["candidate_kept"] == 0

        term = await plugin.store.get_term(term_id)
        assert term is not None
        assert term.status == "muted"
        assert term.meta["candidate_review_decision"] == "reject"
        assert term.meta["candidate_review_decision_confidence"] == 0.9
        assert term.meta["candidate_review_confidence"] == 0.1
        assert term.meta["candidate_review_state"] == "rejected"
        assert term.meta["review_decision"] == "denied"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_observe_stays_out_of_unreviewed_queue(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_ObserveCandidateReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="可能黑话",
            meaning="含义不清",
            group_id="100",
            raw_text="可能黑话",
            confidence=0.6,
            min_count=1,
            observed_count=1,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_rejected"] == 0
        assert result["candidate_kept"] == 1

        term = await plugin.store.get_term(term_id)
        assert term is not None
        assert term.status == "candidate"
        assert term.meta["candidate_review_decision"] == "observe"
        assert term.meta["candidate_review_state"] == "observing"
        assert term.meta["review_decision"] == "observe_more"

        unreviewed_terms, unreviewed_total = await plugin.store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 0
        assert unreviewed_terms == []
        observing_terms, observing_total = await plugin.store.list_terms(review_filter="observe_more")
        assert observing_total == 1
        assert observing_terms[0].term_id == term_id
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_review_accepts_decision_without_legacy_approved_field(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_DecisionOnlyCandidateReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="普通问句",
            meaning="普通问句，不是黑话",
            group_id="100",
            raw_text="你是谁。",
            confidence=0.6,
            min_count=1,
            observed_count=1,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_rejected"] == 1
        assert result["candidate_failed"] == 0

        term = await plugin.store.get_term(term_id)
        assert term is not None
        assert term.status == "muted"
        assert term.meta["candidate_review_decision"] == "reject"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_review_falls_back_to_single_item_when_batch_omits_result(tmp_path):
    llm = _MissingBatchItemFallsBackLLM()
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        await plugin.store.save_settings(settings)

        term_id = await plugin.store.upsert_candidate(
            term="普通问句",
            meaning="普通问句，不是黑话",
            group_id="100",
            raw_text="你是谁。",
            confidence=0.6,
            min_count=1,
            observed_count=1,
        )
        assert term_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_rejected"] == 1
        assert result["candidate_failed"] == 0
        assert llm.single_calls == 1

        term = await plugin.store.get_term(term_id)
        assert term is not None
        assert term.status == "muted"
        assert term.meta["candidate_review_reason"] == "单条 fallback 判定非黑话"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_candidate_review_failure_returns_to_unreviewed_queue(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=_FailedCandidateReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        await plugin.store.save_settings(settings)

        failed_id = await plugin.store.upsert_candidate(
            term="失败项",
            meaning="普通句子",
            group_id="100",
            raw_text="失败项",
            confidence=0.6,
            min_count=1,
            observed_count=1,
        )
        assert failed_id is not None

        result = await plugin.run_daily_ai_review(
            ctx,
            settings=settings,
            group_id="100",
            review_candidates=True,
        )
        assert result["ok"] is True
        assert result["candidate_reviewed"] == 1
        assert result["candidate_failed"] == 1

        failed_term = await plugin.store.get_term(failed_id)
        assert failed_term is not None
        assert failed_term.status == "candidate"
        assert failed_term.meta["candidate_review_failed"] is True
        assert failed_term.meta["candidate_reviewed"] is False
        unreviewed_terms, unreviewed_total = await plugin.store.list_terms(review_filter="candidate_ai_unreviewed")
        assert unreviewed_total == 1
        assert unreviewed_terms[0].term_id == failed_id
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_keeps_only_once_per_semantic_threshold(tmp_path):
    llm = _SemanticReviewLLM(
        context_response={"meaning": "", "no_info": True, "confidence": 0.12, "reason": "上下文不足"},
        literal_response={"meaning": "普通字面义", "confidence": 0.9, "reason": "字面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_EmptyMessageLog(),
        llm_client=llm,
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        pending_id = await plugin.store.upsert_candidate(
            term="继续说呢",
            meaning="普通问句",
            group_id="100",
            raw_text="继续说呢",
            confidence=0.4,
            min_count=999,
            observed_count=2,
        )
        assert pending_id is None

        first = await plugin.run_daily_ai_review_if_due(ctx)
        assert first["ok"] is True
        assert first["pending_reviewed"] == 1
        assert first["pending_kept"] == 1
        assert first["semantic_no_info"] == 1
        assert len(llm.calls) == 1

        pending_after, pending_after_total = await plugin.store.list_pending(group_id="100")
        assert pending_after_total == 1
        assert pending_after[0].meta["last_semantic_inference_count"] == 2

        second = await plugin.run_daily_ai_review(ctx, settings=settings)
        assert second["ok"] is True
        assert second["pending_reviewed"] == 0
        assert second["pending_kept"] == 1
        assert second["semantic_reviewed"] == 0
        assert len(llm.calls) == 1
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_failure_does_not_consume_day(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_ExplodingMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        await plugin.store.save_settings(settings)

        failed = await plugin.run_daily_ai_review_if_due(ctx)
        assert failed["ok"] is False
        assert failed["error"] == "message log failed"
        assert await plugin.store.get_meta("last_daily_ai_review_date", "") == ""
        assert await plugin.store.get_meta("last_daily_ai_review_status", "") == "failed"

        retry = await plugin.run_daily_ai_review_if_due(ctx)
        assert retry["ok"] is False
        assert retry["error"] == "message log failed"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_recovers_stale_run_before_retry(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        stale_run_id = await plugin.store.start_extraction_run(
            group_count=1,
            meta={"kind": "daily_ai_review"},
        )
        db = plugin.store._require_db()
        stale_started_at = (
            datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(minutes=20)
        ).isoformat(timespec="seconds")
        await db.execute(
            "UPDATE slang_extraction_runs SET started_at = ? WHERE run_id = ?",
            (stale_started_at, stale_run_id),
        )
        await db.commit()

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        runs = await plugin.store.list_extraction_runs(limit=5)
        stale_run = next(run for run in runs if run.run_id == stale_run_id)
        assert stale_run.status == "abandoned"
        assert stale_run.meta["abandoned"] is True
        assert stale_run.meta["abandon_reason"] == "stale_daily_ai_review_recovered"
        assert await plugin.store.get_meta("last_daily_ai_review_date", "") == datetime.now(
            ZoneInfo("Asia/Shanghai")
        ).date().isoformat()
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_retries_legacy_daily_review_date_without_success_run(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        await plugin.store.set_meta("last_daily_ai_review_date", today)
        stale_run_id = await plugin.store.start_extraction_run(
            group_count=1,
            meta={"kind": "daily_ai_review"},
        )
        db = plugin.store._require_db()
        stale_started_at = (
            datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(minutes=20)
        ).isoformat(timespec="seconds")
        await db.execute(
            "UPDATE slang_extraction_runs SET started_at = ? WHERE run_id = ?",
            (stale_started_at, stale_run_id),
        )
        await db.commit()

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["ai_approved"] == 1
        assert await plugin.store.has_successful_extraction_run(
            kind="daily_ai_review",
            started_date=today,
        )

        runs = await plugin.store.list_extraction_runs(limit=5)
        stale_run = next(run for run in runs if run.run_id == stale_run_id)
        assert stale_run.status == "abandoned"
        assert stale_run.meta["abandon_reason"] == "stale_daily_ai_review_recovered"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_daily_ai_review_chunks_long_history_before_extract(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_ManyMessageLog(),
        llm_client=_BatchSensitiveDailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.daily_ai_recent_message_limit = 25
        settings.extraction_batch_limit = 10
        settings.daily_ai_review_search_enabled = True
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review_if_due(ctx)
        assert result["ok"] is True
        assert result["ai_approved"] == 1
        terms, total = await plugin.store.list_terms(group_id="100", status="approved")
        assert total == 1
        assert terms[0].source == "ai_auto_review"
        assert terms[0].meta["daily_ai_review"] is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_tick_waits_for_daily_review_without_hard_timeout(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_SlowMessageLog(),
        llm_client=_DailyReviewLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_review_time = "00:00"
        settings.extract_interval_minutes = 999
        await plugin.store.save_settings(settings)

        await plugin._run_tick_jobs_inner(ctx, settings)

        runs = await plugin.store.list_extraction_runs(limit=5)
        assert runs
        daily_review_run = next(run for run in runs if run.meta.get("kind") == "daily_ai_review")
        assert daily_review_run.status == "success"
        assert daily_review_run.error == ""
        assert await plugin.store.get_meta("last_daily_ai_review_status", "") == "success"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_lookup_tool_uses_current_group_and_global_terms(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        await plugin.store.create_term(
            term="本群猫饼",
            meaning="当前群的离谱可爱操作",
            scope="group",
            group_id="100",
            status="approved",
        )
        await plugin.store.create_term(
            term="全局猫饼",
            meaning="所有群都能理解的猫饼",
            scope="global",
            status="approved",
        )
        await plugin.store.create_term(
            term="隔壁猫饼",
            meaning="隔壁群专用解释",
            scope="group",
            group_id="200",
            status="approved",
        )

        tools = plugin.register_tools()
        assert len(tools) == 1
        result = await tools[0].execute(ToolContext(group_id="100", session_id="group_100"), query="猫饼")
        assert "当前群的离谱可爱操作" in result
        assert "所有群都能理解" in result
        assert "隔壁群专用解释" not in result

        settings = await plugin.store.load_settings()
        settings.global_excluded_group_ids = ["100"]
        await plugin.store.save_settings(settings)
        closed = await tools[0].execute(ToolContext(group_id="100", session_id="group_100"), query="全局猫饼")
        assert "没有找到当前群可用" in closed

        settings = await plugin.store.load_settings()
        settings.lookup_tool_enabled = False
        await plugin.store.save_settings(settings)
        disabled = await tools[0].execute(ToolContext(group_id="100"), query="猫饼")
        assert "已关闭" in disabled
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_extractor_filters_low_signal_meaning(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_LowSignalLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0
        assert plugin.store is not None
        terms, total = await plugin.store.list_terms(group_id="100")
        assert total == 0
        assert terms == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_extractor_filters_generic_placeholder_meaning(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_GenericMeaningLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0
        assert plugin.store is not None
        terms, total = await plugin.store.list_terms(group_id="100")
        assert total == 0
        assert terms == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_daily_review_falls_back_to_extracted_meaning_when_ai_meaning_is_generic(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DailyReviewFallbackLLM(),
        tool_registry=_ToolRegistry(_SearchTool()),
    )
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.daily_ai_auto_approve_enabled = True
        settings.daily_ai_auto_approve_min_confidence = 0.82
        await plugin.store.save_settings(settings)

        result = await plugin.run_daily_ai_review(ctx, settings=settings)
        assert result["ok"] is True
        assert result["ai_approved"] == 1

        terms, total = await plugin.store.list_terms(review_filter="needs_human_review")
        assert total == 1
        assert terms[0].meaning == "群里说离谱但可爱的操作"
        assert terms[0].aliases == ["猫猫饼"]
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_respects_group_profile_disable(tmp_path):
    plugin = SlangPlugin()
    config = BotConfig.model_validate({
        "group": {
            "allowed_groups": [100],
            "overrides": {
                "100": {
                    "slang_enabled": False,
                },
            },
        },
    })
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DummyLLM(),
        config=config,
    )
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="今天猫饼了",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert prompt_ctx.blocks == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_silent_learn_group_can_learn_but_not_inject(tmp_path):
    plugin = SlangPlugin()
    config = BotConfig.model_validate({
        "group": {
            "access": {
                "mode": "whitelist",
                "whitelist": [200],
                "blacklist": [],
            },
            "presence": {
                "default_mode": "active",
            },
            "overrides": {
                "100": {
                    "presence_mode": "silent_learn",
                    "slang_enabled": True,
                },
            },
        },
    })
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DummyLLM(),
        config=config,
    )
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 1

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="今天猫饼了",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert prompt_ctx.blocks == []
    finally:
        await plugin.on_shutdown(ctx)
