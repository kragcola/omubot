"""SlangPlugin: group slang learning and prompt injection."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from kernel.types import AmadeusPlugin, MessageContext, PluginContext, PromptContext
from services.conversation_archive import add_evidence_message_ref, finish_scan_batch, read_scan_batch
from services.slang import (
    SlangDailyReviewer,
    SlangDatabaseCorruptError,
    SlangDriftReviewer,
    SlangExtractor,
    SlangStore,
)
from services.slang.quality import estimate_slang_occurrences, select_slang_source_row, speaker_to_user_id
from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="system")
TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MANUAL_EXTRACT_TIMEOUT_S = 20.0
_DAILY_REVIEW_STALE_AFTER_S = 10 * 60
_HIT_FLUSH_INTERVAL_S = 2.0
_HIT_FLUSH_MAX_EVENTS = 30


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


def _time_text_to_minutes(value: str) -> int:
    hour_text, minute_text = str(value or "00:00").split(":", 1)
    return int(hour_text) * 60 + int(minute_text)


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
        self._db_path: Path | None = None
        self._message_log: Any = None
        self._llm_client: Any = None
        self._extractor: SlangExtractor | None = None
        self._daily_reviewer: SlangDailyReviewer | None = None
        self._last_extract_monotonic = 0.0
        self._lookup_tool_enabled = True
        self._group_config: Any = None
        self._tick_task: asyncio.Task[None] | None = None
        self._hit_flush_task: asyncio.Task[None] | None = None
        self._hit_buffer: dict[tuple[str, str, int | str], dict[str, Any]] = {}
        self._hit_event_seq = 0
        self._hit_buffer_lock = asyncio.Lock()
        self._job_lock = asyncio.Lock()
        self._slang_disabled_reason = ""

    def _disable_slang(self, reason: str) -> None:
        detail = str(reason or "").strip() or "slang database unavailable"
        if self._slang_disabled_reason == detail:
            return
        self._slang_disabled_reason = detail
        db_path = self._db_path or Path("storage/slang.db")
        _L.error(
            "slang database corrupt | db={} error={} repair=scripts/dev/slang_db_repair.py",
            db_path,
            detail,
        )

    def _disable_slang_from_exc(self, exc: Exception) -> None:
        self._disable_slang(str(exc))

    async def on_startup(self, ctx: PluginContext) -> None:
        db_path = Path(getattr(ctx, "storage_dir", Path("storage"))) / "slang.db"
        self._db_path = db_path
        self._slang_disabled_reason = ""
        self.store = SlangStore(db_path)
        try:
            await self.store.init()
        except SlangDatabaseCorruptError as exc:
            self.store = None
            self._disable_slang(exc.detail)
            ctx.slang_plugin = self
            return
        except sqlite3.DatabaseError as exc:
            self.store = None
            self._disable_slang(str(exc))
            ctx.slang_plugin = self
            return
        self._message_log = getattr(ctx, "msg_log", None)
        self._llm_client = getattr(ctx, "llm_client", None)
        self.store.set_drift_reviewer(SlangDriftReviewer(self._llm_client))
        self._extractor = SlangExtractor(self._llm_client)
        self._daily_reviewer = SlangDailyReviewer(self._llm_client)
        try:
            self._lookup_tool_enabled = (await self.store.load_settings()).lookup_tool_enabled
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            await self.store.close()
            self.store = None
            self._disable_slang(str(exc))
            ctx.slang_plugin = self
            return
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
        if self._hit_flush_task is not None and not self._hit_flush_task.done():
            self._hit_flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._hit_flush_task
        self._hit_flush_task = None
        await self._flush_hit_buffer()
        if self.store is not None:
            await self.store.close()
            self.store = None

    async def on_message(self, ctx: MessageContext) -> bool:
        if self.store is None or self._slang_disabled_reason or not ctx.group_id:
            return False
        if not self._is_group_learning_enabled(ctx.group_id):
            return False
        try:
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
            if matches:
                await self._buffer_hits(
                    [term.term_id for term in matches],
                    group_id=ctx.group_id,
                    user_id=ctx.user_id,
                    message_id=ctx.message_id,
                    raw_text=text,
                    context=text,
                    reason="message_match",
                )
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
        return False

    async def on_tick(self, ctx: PluginContext) -> None:
        if self.store is None or self._slang_disabled_reason:
            return
        try:
            settings = await self.store.load_settings()
            if not settings.learning_enabled:
                return
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
            return
        if self._tick_task is not None and not self._tick_task.done():
            _L.debug("slang tick skipped | background job still running")
            return
        self._tick_task = asyncio.create_task(self._run_tick_jobs(ctx, settings))
        self._tick_task.add_done_callback(self._on_tick_job_done)

    async def _run_tick_jobs(self, ctx: PluginContext, settings: Any) -> None:
        try:
            await self._run_tick_jobs_inner(ctx, settings)
        except asyncio.CancelledError:
            raise
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
        except Exception as exc:
            _L.warning("slang tick job failed | error={}", exc)

    async def _abandon_running_runs(self, *, kind: str, reason: str, meta: dict[str, Any] | None = None) -> int:
        if self.store is None:
            return 0
        runs = await self.store.list_running_extraction_runs(kind=kind, limit=20)
        for run in runs:
            await self.store.abandon_extraction_run(
                run.run_id,
                reason=reason,
                meta=meta,
            )
        return len(runs)

    async def _run_tick_jobs_inner(self, ctx: PluginContext, settings: Any) -> None:
        interval_s = settings.extract_interval_minutes * 60
        now = time.monotonic()
        if now - self._last_extract_monotonic >= interval_s:
            self._last_extract_monotonic = now
            try:
                await asyncio.wait_for(
                    self.run_manual_extract(limit=settings.extraction_batch_limit),
                    timeout=_MANUAL_EXTRACT_TIMEOUT_S,
                )
            except TimeoutError:
                await self._abandon_running_runs(
                    kind="manual_extract",
                    reason="manual_extract_timeout",
                    meta={"timeout_s": _MANUAL_EXTRACT_TIMEOUT_S},
                )
                _L.warning("slang manual extract timeout | timeout={:.0f}s", _MANUAL_EXTRACT_TIMEOUT_S)
        await self.run_daily_ai_review_if_due(ctx, settings=settings)
    def _on_tick_job_done(self, task: asyncio.Task[None]) -> None:
        if self._tick_task is task:
            self._tick_task = None
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.result()

    async def _buffer_hits(
        self,
        term_ids: list[str],
        *,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        reason: str = "message_match",
    ) -> None:
        if self.store is None or not term_ids:
            return
        should_flush = False
        async with self._hit_buffer_lock:
            self._hit_event_seq += 1
            event_id = self._hit_event_seq
            for term_id in term_ids:
                key = (
                    str(group_id),
                    str(term_id),
                    message_id if message_id is not None else f"event:{event_id}",
                )
                self._hit_buffer[key] = {
                    "buffer_event_key": key[2],
                    "group_id": str(group_id),
                    "term_id": str(term_id),
                    "user_id": str(user_id or ""),
                    "message_id": message_id,
                    "raw_text": raw_text,
                    "context": context,
                    "reason": reason,
                }
            should_flush = len(self._hit_buffer) >= _HIT_FLUSH_MAX_EVENTS
            if self._hit_flush_task is None or self._hit_flush_task.done():
                self._hit_flush_task = asyncio.create_task(self._delayed_flush_hit_buffer())
        if should_flush:
            await self._flush_hit_buffer()

    async def _delayed_flush_hit_buffer(self) -> None:
        try:
            await asyncio.sleep(_HIT_FLUSH_INTERVAL_S)
            await self._flush_hit_buffer()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.warning("slang hit buffer flush failed | error={}", exc)

    async def _flush_hit_buffer(self) -> int:
        if self.store is None:
            return 0
        async with self._hit_buffer_lock:
            events = list(self._hit_buffer.values())
            self._hit_buffer.clear()
        if not events:
            return 0
        changed = 0
        grouped: dict[tuple[str, str, int | None, int | str | None, str, str, str], list[str]] = {}
        for event in events:
            message_id = event["message_id"]
            key = (
                str(event["group_id"]),
                str(event["user_id"]),
                message_id,
                message_id if message_id is not None else event.get("buffer_event_key"),
                str(event["raw_text"]),
                str(event["context"]),
                str(event["reason"]),
            )
            grouped.setdefault(key, []).append(str(event["term_id"]))
        try:
            for group_key, term_ids in grouped.items():
                group_id, user_id, message_id, _buffer_event_key, raw_text, context, reason = group_key
                changed += await self.store.record_hits(
                    term_ids,
                    group_id=group_id,
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    reason=reason,
                )
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
        except Exception as exc:
            _L.warning("slang hit buffer flush failed | events={} error={}", len(events), exc)
        return changed

    async def run_daily_ai_review_if_due(
        self,
        ctx: PluginContext,
        *,
        settings: Any | None = None,
    ) -> dict[str, Any]:
        if self.store is None or self._slang_disabled_reason:
            return {"ok": False, "error": "SlangStore not available"}
        try:
            await self.store.quick_check()
            settings = settings or await self.store.load_settings()
            if not settings.daily_ai_review_enabled:
                _L.debug("daily slang AI review skipped | task=slang_review skip_reason=disabled")
                return {"ok": True, "skipped": "disabled"}
            now = datetime.now(TZ_SHANGHAI)
            now_minutes = now.hour * 60 + now.minute
            review_minutes = _time_text_to_minutes(str(settings.daily_ai_review_time))
            if now_minutes < review_minutes:
                _L.debug("daily slang AI review skipped | task=slang_review skip_reason=not_due")
                return {"ok": True, "skipped": "not_due"}
            stale_before = (now - timedelta(seconds=_DAILY_REVIEW_STALE_AFTER_S)).isoformat(timespec="seconds")
            stale_runs = await self.store.list_stale_extraction_runs(
                kind="daily_ai_review",
                stale_before_iso=stale_before,
            )
            for stale_run in stale_runs:
                await self.store.abandon_extraction_run(
                    stale_run.run_id,
                    reason="stale_daily_ai_review_recovered",
                    meta={"recovered_at": now.isoformat(timespec="seconds")},
                )
                _L.warning(
                    (
                        "daily slang AI review stale run recovered | run={} started_at={} status={} "
                        "reason=stale_before_retry"
                    ),
                    stale_run.run_id,
                    stale_run.started_at,
                    stale_run.status,
                )
            today = now.date().isoformat()
            last_date = await self.store.get_meta("last_daily_ai_review_date", "")
            if last_date == today:
                has_success = await self.store.has_successful_extraction_run(
                    kind="daily_ai_review",
                    started_date=today,
                )
                if has_success:
                    _L.debug("daily slang AI review skipped | task=slang_review skip_reason=already_ran")
                    return {"ok": True, "skipped": "already_ran"}
                _L.warning(
                    (
                        "daily slang AI review stale date ignored | task=slang_review "
                        "date={} stale_recovered={} reason=no_success_run"
                    ),
                    today,
                    len(stale_runs),
                )
            await self.store.set_meta("last_daily_ai_review_started_at", now.isoformat(timespec="seconds"))
            await self.store.set_meta("last_daily_ai_review_status", "running")
            _L.info("daily slang AI review start | date={} stale_recovered={}", today, len(stale_runs))
            result = await self.run_daily_ai_review(
                ctx,
                settings=settings,
                review_candidates=True,
                review_all_pending=False,
            )
            if result.get("ok"):
                await self.store.set_meta("last_daily_ai_review_date", today)
                await self.store.set_meta("last_daily_ai_review_status", "success")
                await self.store.set_meta("last_daily_ai_review_run_id", result.get("run_id", ""))
                _L.info(
                    (
                        "daily slang AI review finished | run={} groups={} ai_approved={} candidates={} "
                        "candidate_reviewed={} candidate_approved={} candidate_rejected={} "
                        "candidate_kept={} candidate_failed={} "
                        "pending_reviewed={} pending_approved={} pending_rejected={} pending_kept={} "
                        "semantic_reviewed={} semantic_approved={} semantic_rejected={} semantic_kept={} "
                        "semantic_no_info={} semantic_failed={} "
                        "drift_replay_reviewed={} drift_replay_closed={} drift_replay_aliased={}"
                    ),
                    result.get("run_id", ""),
                    len(result.get("groups", [])),
                    result.get("ai_approved", 0),
                    result.get("candidates", 0),
                    result.get("candidate_reviewed", 0),
                    result.get("candidate_approved", 0),
                    result.get("candidate_rejected", 0),
                    result.get("candidate_kept", 0),
                    result.get("candidate_failed", 0),
                    result.get("pending_reviewed", 0),
                    result.get("pending_approved", 0),
                    result.get("pending_rejected", 0),
                    result.get("pending_kept", 0),
                    result.get("semantic_reviewed", 0),
                    result.get("semantic_approved", 0),
                    result.get("semantic_rejected", 0),
                    result.get("semantic_kept", 0),
                    result.get("semantic_no_info", 0),
                    result.get("semantic_failed", 0),
                    result.get("drift_replay_reviewed", 0),
                    result.get("drift_replay_closed_same_meaning", 0),
                    result.get("drift_replay_aliased", 0),
                )
            else:
                await self.store.set_meta("last_daily_ai_review_status", "failed")
                _L.warning(
                    "daily slang AI review failed | run={} error={}",
                    result.get("run_id", ""),
                    result.get("error"),
                )
            return result
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
            return {"ok": False, "error": self._slang_disabled_reason}

    async def run_daily_ai_review(
        self,
        ctx: PluginContext,
        *,
        settings: Any | None = None,
        group_id: str | None = None,
        review_candidates: bool = False,
        review_all_pending: bool = False,
        rerun_reviewed_candidates: bool = False,
        candidate_review_filter: str = "",
    ) -> dict[str, Any]:
        async with self._job_lock:
            return await self._run_daily_ai_review_locked(
                ctx,
                settings=settings,
                group_id=group_id,
                review_candidates=review_candidates,
                review_all_pending=review_all_pending,
                rerun_reviewed_candidates=rerun_reviewed_candidates,
                candidate_review_filter=candidate_review_filter,
            )

    async def _run_daily_ai_review_locked(
        self,
        ctx: PluginContext,
        *,
        settings: Any | None = None,
        group_id: str | None = None,
        review_candidates: bool = False,
        review_all_pending: bool = False,
        rerun_reviewed_candidates: bool = False,
        candidate_review_filter: str = "",
    ) -> dict[str, Any]:
        if self.store is None or self._slang_disabled_reason:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if self._daily_reviewer is None:
            self._daily_reviewer = SlangDailyReviewer(self._llm_client)
        try:
            await self.store.quick_check()
            settings = settings or await self.store.load_settings()
            return await self._daily_reviewer.run(
                store=self.store,
                message_log=self._message_log,
                settings=settings,
                tool_registry=getattr(ctx, "tool_registry", None),
                group_id=group_id,
                group_filter=self._is_group_learning_enabled,
                review_candidates=review_candidates,
                review_all_pending=review_all_pending,
                rerun_reviewed_candidates=rerun_reviewed_candidates,
                candidate_review_filter=candidate_review_filter,
            )
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
            return {"ok": False, "error": self._slang_disabled_reason}

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self.store is None or self._slang_disabled_reason or not ctx.group_id:
            return
        if not self._is_group_speaking_enabled(ctx.group_id):
            return
        try:
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
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))

    def register_tools(self) -> list[Tool]:
        if self.store is None or self._slang_disabled_reason or not self._lookup_tool_enabled:
            return []
        return [SlangLookupTool(self.store)]

    async def run_manual_extract(self, *, group_id: str | None = None, limit: int = 80) -> dict[str, Any]:
        async with self._job_lock:
            return await self._run_manual_extract_locked(group_id=group_id, limit=limit)

    async def _run_manual_extract_locked(self, *, group_id: str | None = None, limit: int = 80) -> dict[str, Any]:
        if self.store is None or self._slang_disabled_reason:
            return {"ok": False, "error": "SlangStore not available"}
        if self._message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}
        if self._extractor is None:
            self._extractor = SlangExtractor(self._llm_client)

        run_id = ""
        groups: list[str] = []
        promoted = 0
        extracted = 0
        scanned = 0
        active_scan_batch: dict[str, Any] | None = None
        active_group_scanned = 0
        active_group_extracted = 0
        active_group_promoted = 0
        run_started = time.monotonic()
        try:
            await self.store.quick_check()
            settings = await self.store.load_settings()
            groups = [str(group_id)] if group_id else await self._message_log.list_group_ids()
            groups = [gid for gid in groups if settings.allows_group(gid) and self._is_group_learning_enabled(gid)]
            run_id = await self.store.start_extraction_run(
                group_count=len(groups),
                meta={"manual": group_id is not None, "kind": "manual_extract"},
            )
            _L.info(
                "slang manual extract start | task=slang_extract run={} groups={} limit={} scoped={}",
                run_id,
                len(groups),
                limit,
                bool(group_id),
            )
            for gid in groups:
                batch = await read_scan_batch(
                    self._message_log,
                    scanner_name="slang_manual_extract",
                    group_id=gid,
                    limit=limit,
                    scanner_version="v1",
                    meta={"manual": group_id is not None, "run_id": run_id},
                )
                active_scan_batch = batch
                group_scanned = 0
                group_extracted = 0
                group_promoted = 0
                try:
                    rows = list(batch.get("rows") or [])
                    user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                    group_scanned = len(user_rows)
                    active_group_scanned = group_scanned
                    scanned += group_scanned
                    if user_rows:
                        extractions = await self._extractor.extract(user_rows, settings=settings)
                        group_extracted = len(extractions)
                        active_group_extracted = group_extracted
                        extracted += group_extracted
                        for item in extractions:
                            source = self._pick_source_row(item.evidence, user_rows)
                            observed_count = self._estimate_occurrences(item.term, item.aliases, user_rows)
                            term_id = await self.store.upsert_candidate(
                                term=item.term,
                                meaning=item.meaning,
                                aliases=item.aliases,
                                group_id=gid,
                                user_id=speaker_to_user_id(source.get("speaker")),
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
                                await add_evidence_message_ref(
                                    self._message_log,
                                    group_id=gid,
                                    source_row=source,
                                    ref_owner="slang",
                                    external_table="slang_terms",
                                    external_id=term_id,
                                    snapshot_text=str(source.get("content_text") or item.evidence),
                                    meta={"source": "slang_manual_extract", "slang_run_id": run_id},
                                )
                                promoted += 1
                                group_promoted += 1
                                active_group_promoted = group_promoted
                    await finish_scan_batch(
                        self._message_log,
                        batch,
                        status="success",
                        scanned_count=group_scanned,
                        extracted_count=group_extracted,
                        saved_count=group_promoted,
                        meta={"slang_run_id": run_id},
                    )
                    active_scan_batch = None
                except Exception as exc:
                    await finish_scan_batch(
                        self._message_log,
                        batch,
                        status="failed",
                        scanned_count=group_scanned,
                        extracted_count=group_extracted,
                        saved_count=group_promoted,
                        error=str(exc),
                        advance_cursor=False,
                    )
                    raise
                active_group_scanned = group_scanned
                active_group_extracted = group_extracted
                active_group_promoted = group_promoted
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
            latency_ms = int((time.monotonic() - run_started) * 1000)
            _L.info(
                (
                    "slang manual extract finished | task=slang_extract run={} "
                    "latency_ms={} groups={} scanned={} extracted={} promoted={}"
                ),
                run_id,
                latency_ms,
                len(groups),
                scanned,
                extracted,
                promoted,
            )
            return {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": promoted,
            }
        except (SlangDatabaseCorruptError, sqlite3.DatabaseError) as exc:
            self._disable_slang(str(exc))
            return {"ok": False, "error": self._slang_disabled_reason}
        except asyncio.CancelledError as exc:
            if active_scan_batch is not None:
                await finish_scan_batch(
                    self._message_log,
                    active_scan_batch,
                    status="abandoned",
                    scanned_count=active_group_scanned,
                    extracted_count=active_group_extracted,
                    saved_count=active_group_promoted,
                    error=str(exc) or "cancelled",
                    advance_cursor=False,
                    meta={"slang_run_id": run_id},
                )
            raise
        except Exception as exc:
            if run_id:
                await self.store.finish_extraction_run(
                    run_id,
                    status="failed",
                    group_count=len(groups),
                    scanned_messages=scanned,
                    extracted_terms=extracted,
                    promoted_candidates=promoted,
                    error=str(exc),
                )
            latency_ms = int((time.monotonic() - run_started) * 1000)
            _L.warning(
                "slang extraction failed | task=slang_extract run={} latency_ms={} error={}",
                run_id,
                latency_ms,
                exc,
            )
            return {"ok": False, "run_id": run_id, "error": str(exc)}

    @staticmethod
    def _pick_source_row(evidence: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return select_slang_source_row(evidence, rows)

    @staticmethod
    def _estimate_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
        return estimate_slang_occurrences(term, aliases, rows)

    def _is_group_learning_enabled(self, group_id: str | None) -> bool:
        if not group_id or self._group_config is None:
            return True
        try:
            resolved = self._group_config.resolve(int(group_id))
        except Exception:
            return True
        presence_mode = str(getattr(resolved, "presence_mode", "active") or "active")
        return bool(getattr(resolved, "slang_enabled", True)) and presence_mode != "off"

    def _is_group_speaking_enabled(self, group_id: str | None) -> bool:
        if not group_id or self._group_config is None:
            return True
        try:
            if hasattr(self._group_config, "allows_active_group"):
                return bool(self._group_config.allows_active_group(group_id))
            resolved = self._group_config.resolve(int(group_id))
        except Exception:
            return True
        presence_mode = str(getattr(resolved, "presence_mode", "active") or "active")
        access_allowed = bool(getattr(resolved, "access_allowed", True))
        return bool(getattr(resolved, "slang_enabled", True)) and access_allowed and presence_mode == "active"
