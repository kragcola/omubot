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
from services.slang import (
    SlangBacklogReviewer,
    SlangDatabaseCorruptError,
    SlangDriftReviewer,
    SlangExtractor,
    SlangStore,
    normalize_term,
)
from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="system")
TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TICK_JOB_TIMEOUT_S = 600.0


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
    version = "0.1.17"
    priority = 42
    silent_safe = True  # on_message 仅写黑话候选库，不发消息、不改 trigger

    def __init__(self) -> None:
        super().__init__()
        self.store: SlangStore | None = None
        self._message_log: Any = None
        self._llm_client: Any = None
        self._extractor: SlangExtractor | None = None
        self._backlog_reviewer: SlangBacklogReviewer | None = None
        self._drift_reviewer: SlangDriftReviewer | None = None
        self._last_extract_monotonic = 0.0
        self._last_drift_age_out_date: str = ""
        self._lookup_tool_enabled = True
        self._group_config: Any = None
        self._tick_task: asyncio.Task[None] | None = None
        self._slang_disabled_reason: str = ""
        self._backlog_review_in_flight: bool = False
        self._provider_superseded: bool = False
        self._tool_registry: Any = None

    async def on_startup(self, ctx: PluginContext) -> None:
        db_path = Path(getattr(ctx, "storage_dir", Path("storage"))) / "slang.db"
        store = SlangStore(db_path)
        try:
            await store.init()
        except SlangDatabaseCorruptError as exc:
            self.store = None
            self._slang_disabled_reason = (
                f"slang database corrupt at {exc.db_path}; "
                "run scripts/dev/slang_db_repair.py to recover"
            )
            ctx.slang_store = None
            ctx.slang_plugin = self
            self._group_config = getattr(ctx.config, "group", None) if getattr(ctx, "config", None) else None
            _L.error(
                "slang plugin disabled | reason={} db={}",
                self._slang_disabled_reason,
                db_path,
            )
            return
        self.store = store
        self._message_log = getattr(ctx, "msg_log", None)
        self._llm_client = getattr(ctx, "llm_client", None)
        self._tool_registry = getattr(ctx, "tool_registry", None)
        self._extractor = SlangExtractor(self._llm_client)
        self._backlog_reviewer = SlangBacklogReviewer(self._llm_client)
        self._drift_reviewer = SlangDriftReviewer(self._llm_client)
        self.store.set_drift_reviewer(self._drift_reviewer)
        self._lookup_tool_enabled = (await self.store.load_settings()).lookup_tool_enabled
        self._group_config = getattr(ctx.config, "group", None)
        ctx.slang_store = self.store
        ctx.slang_plugin = self
        # Phase E.1 graph edge double-write — mirror term-group hits into
        # knowledge_graph.db as `term_used_in_group` edges. Best-effort: a
        # graph write failure must never block `record_hit` (audit § E.1).
        try:
            from services.knowledge_graph.graph_writer import GraphWriter
            from services.slang.graph_bridge import SlangGraphBridge

            kg_service = getattr(ctx, "knowledge_graph", None)
            kg_store = getattr(kg_service, "_store", None) if kg_service else None
            if kg_store is not None and getattr(kg_store, "_db", None) is not None:
                ctx.slang_graph_bridge = SlangGraphBridge(GraphWriter(kg_store))
                ctx.slang_graph_bridge.attach(self.store)
        except Exception as exc:
            _L.warning("slang graph bridge attach failed | err={}", exc)
        # Mark superseded if SlangProvider is already registered on the
        # provider bus — provider becomes the sole prompt-injection path.
        provider_bus = getattr(ctx, "provider_bus", None)
        if provider_bus is not None and provider_bus.has_provider("slang"):
            self._provider_superseded = True
            _L.info("slang prompt injection delegated to provider bus")
        # Clear stale "running" runs left from previous crash/timeout — keeps the
        # admin dashboard truthful instead of permanently parking on running.
        try:
            stale = await self.store.mark_stale_running_runs()
        except Exception as exc:
            _L.warning("slang stale run cleanup failed | error={}", exc)
        else:
            if stale:
                _L.warning("slang stale runs marked abandoned | count={}", stale)
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
        except TimeoutError:
            _L.warning("slang tick job timeout | timeout={:.0f}s", _TICK_JOB_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.warning("slang tick job failed | error={}", exc)

    async def _run_tick_jobs_inner(self, ctx: PluginContext, settings: Any) -> None:
        await self._maybe_age_out_drifts(settings)
        await self.run_backlog_review_one_batch_if_due(ctx, settings=settings)
        interval_s = settings.extract_interval_minutes * 60
        now = time.monotonic()
        if now - self._last_extract_monotonic < interval_s:
            # Fast path: in-memory timer says not yet.
            return
        # Cold start guard: if monotonic timer was never set (container just
        # restarted), check DB for the last successful extract run time to avoid
        # duplicate extraction within the configured interval.
        if self._last_extract_monotonic == 0.0 and self.store is not None:
            last_run_at = await self.store.get_last_extract_run_time()
            if last_run_at:
                from datetime import UTC, datetime

                elapsed = datetime.now(UTC) - last_run_at
                if elapsed.total_seconds() < interval_s:
                    # Not enough time since last run — skip and align monotonic.
                    self._last_extract_monotonic = now - elapsed.total_seconds()
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

    async def _maybe_age_out_drifts(self, settings: Any) -> None:
        """Run the drift age-out gate at most once per local day."""
        if self.store is None:
            return
        days = int(getattr(settings, "drift_age_out_days", 0) or 0)
        if days <= 0:
            return
        today = datetime.now(TZ_SHANGHAI).date().isoformat()
        if self._last_drift_age_out_date == today:
            return
        last_run = await self.store.get_meta("last_drift_age_out_date", "")
        if last_run == today:
            self._last_drift_age_out_date = today
            return
        try:
            aged = await self.store.age_out_open_drifts(days=days)
        except Exception as exc:
            _L.warning("drift age-out failed | error={}", exc)
            return
        self._last_drift_age_out_date = today
        await self.store.set_meta("last_drift_age_out_date", today)
        if aged > 0:
            _L.info("drift age-out completed | aged={} days={}", aged, days)

    async def run_backlog_review_one_batch_if_due(
        self,
        ctx: PluginContext | None = None,
        *,
        settings: Any | None = None,
    ) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        settings = settings or await self.store.load_settings()
        if not settings.backlog_review_enabled:
            return {"ok": True, "skipped": "disabled"}

        times: list[str] = list(settings.daily_ai_review_times or [])
        if not times:
            return {"ok": True, "skipped": "no_slots"}
        now = datetime.now(TZ_SHANGHAI)
        current_hm = now.strftime("%H:%M")
        due_slots = [slot for slot in times if slot <= current_hm]
        if not due_slots:
            return {"ok": True, "skipped": "not_due"}
        current_slot = max(due_slots)
        today = now.date().isoformat()
        slot_key = f"{today}:{current_slot}"
        last_slot_key = await self.store.get_meta("last_backlog_review_slot", "")
        if last_slot_key == slot_key:
            return {"ok": True, "skipped": "already_ran"}

        if self._backlog_review_in_flight:
            return {"ok": True, "skipped": "in_flight"}
        self._backlog_review_in_flight = True
        try:
            total_approved = 0
            total_muted = 0
            total_kept = 0
            total_processed = 0
            batches = 0
            completed_in_session = False
            deadline = time.monotonic() + _TICK_JOB_TIMEOUT_S * 0.85
            while time.monotonic() < deadline:
                result = await self.run_backlog_review_now(ctx, settings=settings, _caller_holds_lock=True)
                if not result.get("ok"):
                    if batches == 0:
                        _L.warning("backlog AI review failed | slot={} error={}", slot_key, result.get("error"))
                    return result
                if result.get("skipped"):
                    break
                batches += 1
                total_approved += result.get("approved_in_batch", 0)
                total_muted += result.get("muted_in_batch", 0)
                total_kept += result.get("kept_in_batch", 0)
                total_processed += result.get("batch_size", 0)
                if result.get("completed") or result.get("remaining", 1) == 0:
                    completed_in_session = True
                    break
            if completed_in_session:
                # Only lock the slot out once the backlog is fully drained. If we
                # ran out of tick budget mid-pool, leave the slot unlocked so the
                # next tick can resume — state.active stays True and the next
                # call will pick up the cursor without re-counting from zero.
                await self.store.set_meta("last_backlog_review_slot", slot_key)
            if batches > 0:
                _L.info(
                    "backlog AI review session done | slot={} batches={} processed={} "
                    "approved={} muted={} kept={} completed={}",
                    slot_key,
                    batches,
                    total_processed,
                    total_approved,
                    total_muted,
                    total_kept,
                    completed_in_session,
                )
        finally:
            self._backlog_review_in_flight = False
        return {
            "ok": True,
            "slot": slot_key,
            "batches": batches,
            "processed": total_processed,
            "completed": completed_in_session,
        }

    async def run_backlog_review_now(
        self,
        ctx: PluginContext | None = None,
        *,
        settings: Any | None = None,
        batch_size: int | None = None,
        min_confidence: float | None = None,
        _caller_holds_lock: bool = False,
    ) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if not _caller_holds_lock and self._backlog_review_in_flight:
            return {"ok": False, "error": "AI 清池正在进行中，请等待完成"}
        if not _caller_holds_lock:
            self._backlog_review_in_flight = True
        try:
            if self._backlog_reviewer is None:
                self._backlog_reviewer = SlangBacklogReviewer(self._llm_client)
            settings = settings or await self.store.load_settings()
            tool_registry = (
                getattr(ctx, "tool_registry", None) if ctx is not None else None
            ) or self._tool_registry
            return await self._backlog_reviewer.run_one_batch(
                store=self.store,
                message_log=self._message_log,
                settings=settings,
                tool_registry=tool_registry,
                group_filter=self._is_group_enabled,
                batch_size_override=batch_size,
                min_confidence_override=min_confidence,
            )
        finally:
            if not _caller_holds_lock:
                self._backlog_review_in_flight = False

    async def run_backlog_review_continuous(
        self,
        ctx: PluginContext | None = None,
        *,
        per_batch_timeout_s: float = 600.0,
        max_batches: int = 200,
    ) -> dict[str, Any]:
        """Run backlog review batches in a loop until the pool is drained.

        Unlike the tick scheduler (which has to leave headroom for the next
        tick), this is a manual trigger and runs to completion. Each batch
        is wrapped with ``per_batch_timeout_s`` so a stuck LLM call can't
        hang the loop forever; ``max_batches`` is a hard safety cap.
        """
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if self._backlog_review_in_flight:
            return {"ok": False, "error": "AI 清池正在进行中，请等待完成"}
        self._backlog_review_in_flight = True
        try:
            settings = await self.store.load_settings()
            total_approved = 0
            total_muted = 0
            total_kept = 0
            total_processed = 0
            batches = 0
            completed = False
            timed_out = False
            for _ in range(max_batches):
                try:
                    result = await asyncio.wait_for(
                        self.run_backlog_review_now(
                            ctx, settings=settings, _caller_holds_lock=True,
                        ),
                        timeout=per_batch_timeout_s,
                    )
                except TimeoutError:
                    _L.error(
                        "manual backlog AI review batch timed out after {:.0f}s | batches_done={}",
                        per_batch_timeout_s, batches,
                    )
                    timed_out = True
                    break
                if not result.get("ok"):
                    if batches == 0:
                        _L.warning("manual backlog AI review failed | error={}", result.get("error"))
                    return {
                        "ok": False,
                        "error": result.get("error", "batch failed"),
                        "batches": batches,
                        "processed": total_processed,
                        "approved": total_approved,
                        "muted": total_muted,
                        "kept": total_kept,
                    }
                if result.get("skipped"):
                    break
                batches += 1
                approved_in = result.get("approved_in_batch", 0)
                muted_in = result.get("muted_in_batch", 0)
                kept_in = result.get("kept_in_batch", 0)
                total_approved += approved_in
                total_muted += muted_in
                total_kept += kept_in
                total_processed += (approved_in + muted_in + kept_in)
                if result.get("completed") or result.get("remaining", 1) == 0:
                    completed = True
                    break
            if batches > 0:
                _L.info(
                    "manual backlog AI review done | batches={} processed={} "
                    "approved={} muted={} kept={} completed={} timed_out={}",
                    batches, total_processed, total_approved, total_muted,
                    total_kept, completed, timed_out,
                )
            return {
                "ok": True,
                "batches": batches,
                "processed": total_processed,
                "approved": total_approved,
                "muted": total_muted,
                "kept": total_kept,
                "completed": completed,
                "timed_out": timed_out,
            }
        finally:
            self._backlog_review_in_flight = False

    async def get_backlog_review_status(self) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._backlog_reviewer is None:
            self._backlog_reviewer = SlangBacklogReviewer(self._llm_client)
        settings = await self.store.load_settings()
        status = await self._backlog_reviewer.status(self.store, settings=settings)
        return {"ok": True, **status}

    async def reset_backlog_review(self) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "SlangStore not available"}
        if self._backlog_reviewer is None:
            self._backlog_reviewer = SlangBacklogReviewer(self._llm_client)
        cleared = await self._backlog_reviewer.reset(self.store)
        await self.store.set_meta("last_backlog_review_slot", "")
        return {"ok": True, "state": cleared}

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._provider_superseded:
            return
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
            ctx.add_block(text=block, label="群内黑话", position="dynamic", priority=40, source="slang")

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
        status: str = "success"
        error_text: str = ""
        result_meta: dict[str, Any] | None = None
        result_payload: dict[str, Any] = {}
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
            result_meta = {"global_scan": global_scan}
            result_payload = {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": promoted,
            }
            return result_payload
        except asyncio.CancelledError:
            status = "cancelled"
            error_text = "extraction cancelled (timeout or shutdown)"
            result_meta = {"cancelled": True}
            result_payload = {"ok": False, "run_id": run_id, "error": error_text}
            raise
        except Exception as exc:
            status = "failed"
            error_text = str(exc)
            result_payload = {"ok": False, "run_id": run_id, "error": error_text}
            _L.warning("slang extraction failed | run={} error={}", run_id, exc)
            return result_payload
        finally:
            # Always close the run row — otherwise the dashboard sees it stuck on
            # 'running' forever (the original bug). Use shielded await so a
            # CancelledError doesn't abort the cleanup itself.
            with contextlib.suppress(Exception):
                await asyncio.shield(
                    self.store.finish_extraction_run(
                        run_id,
                        status=status,
                        group_count=len(groups),
                        scanned_messages=scanned,
                        extracted_terms=extracted,
                        promoted_candidates=promoted,
                        error=error_text,
                        meta=result_meta,
                    )
                )

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
