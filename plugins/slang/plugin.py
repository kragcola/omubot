"""SlangPlugin: group slang learning and prompt injection."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from kernel.types import AmadeusPlugin, MessageContext, PluginContext, PromptContext
from services.slang import SlangDailyReviewer, SlangExtractor, SlangStore, normalize_term
from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="system")
TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TICK_JOB_TIMEOUT_S = 50.0


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts).strip()
    return str(content or "").strip()


def _speaker_to_user_id(speaker: str | None) -> str:
    if not speaker:
        return ""
    match = re.search(r"\((\d{4,})\)\s*$", speaker)
    return match.group(1) if match else ""


class SlangLookupTool(Tool):
    """Query approved slang for the current group without expanding every prompt."""

    def __init__(self, store: SlangStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "slang_lookup"

    @property
    def description(self) -> str:
        return "查询当前群或全局已批准黑话的释义、别名和复述策略。只在需要理解群内梗时使用。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查询的黑话、别名、缩写或相关描述。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "最多返回多少条。",
                },
            },
            "required": ["query"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        settings = await self._store.load_settings()
        if not settings.lookup_tool_enabled:
            return "黑话查询工具当前已关闭。"
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "请提供要查询的黑话关键词。"
        try:
            limit = int(kwargs.get("limit") or 6)
        except Exception:
            limit = 6
        terms = await self._store.lookup_terms(
            group_id=ctx.group_id,
            query=query,
            limit=limit,
            min_confidence=settings.min_inject_confidence,
        )
        if not terms:
            return "没有找到当前群可用的已批准黑话。"
        lines = ["可用黑话查询结果："]
        for term in terms:
            aliases = f"；别名：{'、'.join(term.aliases[:5])}" if term.aliases else ""
            scope = "全局" if term.scope == "global" else f"群 {term.group_id}"
            policy = {
                "understand_only": "仅理解，不主动复述",
                "allow_rephrase": "可改写解释",
                "allow_use": "可自然使用",
            }.get(term.repeat_policy, "仅理解")
            lines.append(f"- {term.term}{aliases}：{term.meaning or '含义待补充'}（{scope}；{policy}）")
        return "\n".join(lines)


class SlangPlugin(AmadeusPlugin):
    name = "slang"
    description = "群内黑话：学习候选、审核后注入当前群语境"
    version = "0.1.0"
    priority = 42

    def __init__(self) -> None:
        super().__init__()
        self.store: SlangStore | None = None
        self._message_log: Any = None
        self._llm_client: Any = None
        self._extractor: SlangExtractor | None = None
        self._daily_reviewer: SlangDailyReviewer | None = None
        self._last_extract_monotonic = 0.0
        self._lookup_tool_enabled = True
        self._group_config: Any = None
        self._tick_task: asyncio.Task[None] | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        db_path = Path(getattr(ctx, "storage_dir", Path("storage"))) / "slang.db"
        self.store = SlangStore(db_path)
        await self.store.init()
        self._message_log = getattr(ctx, "msg_log", None)
        self._llm_client = getattr(ctx, "llm_client", None)
        self._extractor = SlangExtractor(self._llm_client)
        self._daily_reviewer = SlangDailyReviewer(self._llm_client)
        self._lookup_tool_enabled = (await self.store.load_settings()).lookup_tool_enabled
        self._group_config = getattr(ctx.config, "group", None)
        ctx.slang_store = self.store
        ctx.slang_plugin = self
        _L.info("slang store initialized | db={}", db_path)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        if self._tick_task is not None and not self._tick_task.done():
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        self._tick_task = None
        if self.store is not None:
            await self.store.close()
            self.store = None

    async def on_message(self, ctx: MessageContext) -> bool:
        if self.store is None or not ctx.group_id:
            return False
        if not self._is_group_enabled(ctx.group_id):
            return False
        settings = await self.store.load_settings()
        if not settings.learning_enabled or not settings.allows_group(ctx.group_id):
            return False
        text = _content_to_text(ctx.content)
        if not text:
            return False
        matches = await self.store.find_matching_terms(
            group_id=ctx.group_id,
            text=text,
            include_candidates=True,
        )
        for term in matches:
            await self.store.record_hit(
                term.term_id,
                group_id=ctx.group_id,
                user_id=ctx.user_id,
                message_id=ctx.message_id,
                raw_text=text,
                context=text,
                reason="message_match",
            )
        return False

    async def on_tick(self, ctx: PluginContext) -> None:
        if self.store is None:
            return
        settings = await self.store.load_settings()
        if not settings.learning_enabled:
            return
        if self._tick_task is not None and not self._tick_task.done():
            _L.debug("slang tick skipped | background job still running")
            return
        self._tick_task = asyncio.create_task(self._run_tick_jobs(ctx, settings))
        self._tick_task.add_done_callback(self._on_tick_job_done)

    async def _run_tick_jobs(self, ctx: PluginContext, settings: Any) -> None:
        try:
            await asyncio.wait_for(
                self._run_tick_jobs_inner(ctx, settings),
                timeout=_TICK_JOB_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _L.warning("slang tick job timeout | timeout={:.0f}s", _TICK_JOB_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.warning("slang tick job failed | error={}", exc)

    async def _run_tick_jobs_inner(self, ctx: PluginContext, settings: Any) -> None:
        await self.run_daily_ai_review_if_due(ctx, settings=settings)
        interval_s = settings.extract_interval_minutes * 60
        now = time.monotonic()
        if now - self._last_extract_monotonic < interval_s:
            return
        self._last_extract_monotonic = now
        await self.run_manual_extract(limit=settings.extraction_batch_limit)

    def _on_tick_job_done(self, task: asyncio.Task[None]) -> None:
        if self._tick_task is task:
            self._tick_task = None
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.result()

    async def run_daily_ai_review_if_due(
        self,
        ctx: PluginContext,
        *,
        settings: Any | None = None,
    ) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        settings = settings or await self.store.load_settings()
        if not settings.daily_ai_review_enabled:
            return {"ok": True, "skipped": "disabled"}
        now = datetime.now(TZ_SHANGHAI)
        if now.strftime("%H:%M") < str(settings.daily_ai_review_time):
            return {"ok": True, "skipped": "not_due"}
        today = now.date().isoformat()
        last_date = await self.store.get_meta("last_daily_ai_review_date", "")
        if last_date == today:
            return {"ok": True, "skipped": "already_ran"}
        await self.store.set_meta("last_daily_ai_review_date", today)
        result = await self.run_daily_ai_review(ctx, settings=settings)
        if result.get("ok"):
            _L.info(
                "daily slang AI review finished | groups={} ai_approved={} candidates={}",
                len(result.get("groups", [])),
                result.get("ai_approved", 0),
                result.get("candidates", 0),
            )
        else:
            _L.warning("daily slang AI review failed | error={}", result.get("error"))
        return result

    async def run_daily_ai_review(
        self,
        ctx: PluginContext,
        *,
        settings: Any | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if self._daily_reviewer is None:
            self._daily_reviewer = SlangDailyReviewer(self._llm_client)
        settings = settings or await self.store.load_settings()
        return await self._daily_reviewer.run(
            store=self.store,
            message_log=self._message_log,
            settings=settings,
            tool_registry=getattr(ctx, "tool_registry", None),
            group_id=group_id,
            group_filter=self._is_group_enabled,
        )

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self.store is None or not ctx.group_id:
            return
        if not self._is_group_enabled(ctx.group_id):
            return
        settings = await self.store.load_settings()
        if not settings.injection_enabled or not settings.allows_group(ctx.group_id):
            return
        block = await self.store.build_prompt_block(
            group_id=ctx.group_id,
            conversation_text=ctx.conversation_text,
            max_terms=settings.max_injected_terms,
            max_chars=settings.max_prompt_chars,
        )
        if block:
            ctx.add_block(text=block, label="群内黑话", position="dynamic")

    def register_tools(self) -> list[Tool]:
        if self.store is None or not self._lookup_tool_enabled:
            return []
        return [SlangLookupTool(self.store)]

    async def run_manual_extract(self, *, group_id: str | None = None, limit: int = 80) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if self._extractor is None:
            self._extractor = SlangExtractor(self._llm_client)

        settings = await self.store.load_settings()
        groups = [str(group_id)] if group_id else await self._message_log.list_group_ids()
        groups = [gid for gid in groups if settings.allows_group(gid) and self._is_group_enabled(gid)]
        run_id = await self.store.start_extraction_run(group_count=len(groups), meta={"manual": group_id is not None})
        promoted = 0
        extracted = 0
        scanned = 0
        try:
            for gid in groups:
                rows = await self._message_log.query_recent(gid, limit=limit)
                user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                if not user_rows:
                    continue
                scanned += len(user_rows)
                extractions = await self._extractor.extract(user_rows, settings=settings)
                extracted += len(extractions)
                for item in extractions:
                    source = self._pick_source_row(item.evidence, user_rows)
                    observed_count = self._estimate_occurrences(item.term, item.aliases, user_rows)
                    term_id = await self.store.upsert_candidate(
                        term=item.term,
                        meaning=item.meaning,
                        aliases=item.aliases,
                        group_id=gid,
                        user_id=_speaker_to_user_id(source.get("speaker")),
                        message_id=source.get("message_id"),
                        raw_text=str(source.get("content_text") or item.evidence),
                        context="\n".join(str(row.get("content_text") or "") for row in user_rows[-8:]),
                        confidence=item.confidence,
                        reason=item.reason,
                        repeat_policy=item.repeat_policy,
                        meta={"evidence": item.evidence},
                        min_count=settings.candidate_min_count,
                        observed_count=observed_count,
                        settings=settings,
                    )
                    if term_id:
                        promoted += 1
            global_scan = None
            if settings.auto_promote_global_enabled:
                global_scan = await self.store.scan_global_candidates(min_groups=settings.global_promote_min_groups)
            await self.store.set_meta("last_extracted_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            await self.store.finish_extraction_run(
                run_id,
                status="success",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=promoted,
                meta={"global_scan": global_scan},
            )
            return {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": promoted,
            }
        except Exception as exc:
            await self.store.finish_extraction_run(
                run_id,
                status="failed",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=promoted,
                error=str(exc),
            )
            _L.warning("slang extraction failed | run={} error={}", run_id, exc)
            return {"ok": False, "run_id": run_id, "error": str(exc)}

    @staticmethod
    def _pick_source_row(evidence: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        evidence = (evidence or "").strip()
        if evidence:
            for row in rows:
                text = str(row.get("content_text") or "")
                if evidence in text or text in evidence:
                    return row
        return rows[-1] if rows else {}

    @staticmethod
    def _estimate_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
        keys = {normalize_term(term), *(normalize_term(alias) for alias in aliases)}
        keys = {key for key in keys if len(key) >= 2}
        if not keys:
            return 1
        count = 0
        for row in rows:
            text_key = normalize_term(str(row.get("content_text") or ""))
            if any(key in text_key for key in keys):
                count += 1
        return max(1, count)

    def _is_group_enabled(self, group_id: str | None) -> bool:
        if not group_id or self._group_config is None:
            return True
        try:
            resolved = self._group_config.resolve(int(group_id))
        except Exception:
            return True
        return bool(getattr(resolved, "slang_enabled", True))
