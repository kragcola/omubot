"""Learning pipeline aggregate and orchestration endpoints for admin."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import secrets
from datetime import datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiosqlite
from fastapi import APIRouter, Query, Request

from admin.routes.api.style import run_style_manual_extract
from services import learning_settings as _ls
from services.learning_autopilot.base import AggressivenessConfig
from services.learning_autopilot.runner import AutopilotRunner
from services.style import StyleStore

TZ_SHANGHAI = timezone(timedelta(hours=8))
EXTRACT_ALL_TIMEOUT_SECONDS = 120
EXTRACT_ALL_RUN_LIMIT = 20
EXTRACT_ALL_NOUNS: tuple[str, ...] = ("slang", "style", "consolidator")

STAGES: tuple[str, ...] = ("candidate", "review", "approved", "hits", "archived")
NOUNS: tuple[str, ...] = (
    "slang",
    "style",
    "episode",
    "memory",
    "fact",
    "graph_relation",
)
ITEM_SORTS: tuple[str, ...] = ("newest", "confidence", "group")
NOUN_LABELS: dict[str, str] = {
    "slang": "黑话",
    "style": "风格",
    "episode": "经验",
    "memory": "记忆",
    "fact": "事实",
    "graph_relation": "关系",
}

_extract_all_lock = asyncio.Lock()
_extract_all_runs: dict[str, dict[str, Any]] = {}
_extract_all_active_run_id: str | None = None

_autopilot_runner_instance: AutopilotRunner | None = None


def _get_autopilot_runner(ctx: Any) -> AutopilotRunner | None:
    global _autopilot_runner_instance
    if _autopilot_runner_instance is not None:
        return _autopilot_runner_instance

    from services.learning_autopilot.episode_reviewer import EpisodeAIReviewer
    from services.learning_autopilot.knowledge_reviewer import KnowledgeAIReviewer
    from services.learning_autopilot.style_reviewer import StyleAIReviewer

    storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
    llm_client = getattr(ctx, "llm_client", None) if ctx else None

    runner = AutopilotRunner(llm_client=llm_client, storage_dir=storage_dir)

    style_db = storage_dir / "style.db"
    if style_db.exists():
        runner.register(StyleAIReviewer(style_db))

    episode_db = storage_dir / "episodic.db"
    if episode_db.exists():
        runner.register(EpisodeAIReviewer(episode_db))

    kg_db = storage_dir / "knowledge_graph.db"
    if kg_db.exists():
        runner.register(KnowledgeAIReviewer(kg_db, domain="fact"))
        runner.register(KnowledgeAIReviewer(kg_db, domain="graph_relation"))

    # Slang adapter — only if slang_store is available
    slang_store = getattr(ctx, "slang_store", None) if ctx else None
    if slang_store is not None:
        try:
            from services.learning_autopilot.slang_adapter import SlangReviewerAdapter
            slang_plugin = getattr(ctx, "slang_plugin", None)
            backlog_reviewer = getattr(ctx, "slang_backlog_reviewer", None)
            if backlog_reviewer is None and slang_plugin is not None:
                backlog_reviewer = getattr(slang_plugin, "_backlog_reviewer", None)
            message_log = getattr(ctx, "message_log", None)
            tool_registry = getattr(ctx, "tool_registry", None)
            if backlog_reviewer:
                runner.register(SlangReviewerAdapter(
                    backlog_reviewer=backlog_reviewer,
                    store=slang_store,
                    message_log=message_log,
                    settings_loader=slang_store.load_settings,
                    tool_registry=tool_registry,
                ))
        except Exception:
            pass

    _autopilot_runner_instance = runner
    return runner


def _autopilot_config(ap: dict[str, Any]) -> AggressivenessConfig:
    concurrency = int(ap.get("concurrency", 20))
    return AggressivenessConfig.from_level(str(ap.get("aggressiveness", "standard")), concurrency=concurrency)


def create_learning_pipeline_router(*, ctx: Any = None) -> APIRouter:
    router = APIRouter()

    def _storage_dir() -> Path:
        return Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")

    def _db_path(store_attr: str, fallback_name: str) -> Path:
        store = getattr(ctx, store_attr, None) if ctx is not None else None
        if store is not None:
            raw = getattr(store, "db_path", "") or getattr(store, "_db_path", "")
            if raw:
                return Path(str(raw))
        return _storage_dir() / fallback_name

    @router.get("/learning/pipeline")
    async def learning_pipeline(
        group: str = Query(""),
        date: str = Query("all", pattern="^(today|7d|30d|all)$"),
    ) -> dict[str, Any]:
        del date  # Stage counts are inventory snapshots; list endpoints own date filtering.
        stages = _new_stage_payload()
        warnings: list[dict[str, str]] = []
        group_id = str(group or "").strip()

        await _collect_slang_counts(
            _db_path("slang_store", "slang.db"),
            stages=stages,
            group_id=group_id,
            warnings=warnings,
        )
        await _collect_style_counts(
            _db_path("style_store", "style.db"),
            stages=stages,
            group_id=group_id,
            warnings=warnings,
        )
        await _collect_episode_counts(
            _db_path("episode_store", "episodic.db"),
            stages=stages,
            group_id=group_id,
            warnings=warnings,
        )
        await _collect_memory_counts(
            _db_path("card_store", "memory_cards.db"),
            stages=stages,
            group_id=group_id,
            warnings=warnings,
        )
        await _collect_consolidator_counts(
            _db_path("memory_consolidator_store", "consolidator_candidates.db"),
            stages=stages,
            group_id=group_id,
            warnings=warnings,
        )

        _refresh_totals(stages)

        # Sub-stage counts for slang candidate
        candidate_sub: dict[str, int] = {"unscanned": 0, "ai_rejected": 0, "ai_kept": 0, "all": 0}
        slang_db = _db_path("slang_store", "slang.db")
        if slang_db.exists():
            try:
                async with aiosqlite.connect(slang_db) as sdb:
                    g_sql, g_params = _group_filter("group_id", group_id)
                    for key in ("all", "unscanned", "ai_rejected", "ai_kept"):
                        where = _slang_stage_where("candidate", key)
                        cur = await sdb.execute(
                            f"SELECT COUNT(*) FROM slang_terms WHERE {where} {g_sql}",
                            tuple(g_params),
                        )
                        row = await cur.fetchone()
                        candidate_sub[key] = int(row[0]) if row else 0
            except Exception:
                pass

        return {
            "as_of": datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds"),
            "stages": stages,
            "candidate_sub": candidate_sub,
            "warnings": warnings,
        }

    @router.get("/learning/items")
    async def learning_items(
        stage: str = Query("candidate", pattern="^(candidate|review|approved|hits|archived)$"),
        sub_stage: str = Query("all", pattern="^(all|unscanned|ai_rejected|ai_kept)$"),
        noun: str = Query("all"),
        group: str = Query(""),
        date: str = Query("all", pattern="^(today|7d|30d|all)$"),
        sort: str = Query("newest", pattern="^(newest|confidence|group)$"),
        limit: int = Query(30, ge=1, le=100),
        cursor: str = Query(""),
    ) -> dict[str, Any]:
        warnings: list[dict[str, str]] = []
        group_id = str(group or "").strip()
        offset = _decode_cursor(cursor)
        selected_nouns = _selected_nouns(noun)
        fetch_limit = offset + limit + 1
        items: list[dict[str, Any]] = []

        if "slang" in selected_nouns:
            await _collect_slang_items(
                _db_path("slang_store", "slang.db"),
                items=items,
                stage=stage,
                sub_stage=sub_stage,
                group_id=group_id,
                date=date,
                sort=sort,
                limit=fetch_limit,
                warnings=warnings,
            )
        if "style" in selected_nouns:
            await _collect_style_items(
                _db_path("style_store", "style.db"),
                items=items,
                stage=stage,
                group_id=group_id,
                date=date,
                sort=sort,
                limit=fetch_limit,
                warnings=warnings,
            )
        if "episode" in selected_nouns:
            await _collect_episode_items(
                _db_path("episode_store", "episodic.db"),
                items=items,
                stage=stage,
                group_id=group_id,
                date=date,
                sort=sort,
                limit=fetch_limit,
                warnings=warnings,
            )
        if "memory" in selected_nouns:
            await _collect_memory_items(
                _db_path("card_store", "memory_cards.db"),
                items=items,
                stage=stage,
                group_id=group_id,
                date=date,
                sort=sort,
                limit=fetch_limit,
                warnings=warnings,
            )
        consolidator_nouns = tuple(
            noun
            for noun in selected_nouns
            if noun in {"fact", "graph_relation", "slang", "style", "episode"}
        )
        if consolidator_nouns:
            await _collect_consolidator_items(
                _db_path("memory_consolidator_store", "consolidator_candidates.db"),
                items=items,
                stage=stage,
                nouns=consolidator_nouns,
                group_id=group_id,
                date=date,
                sort=sort,
                limit=fetch_limit,
                warnings=warnings,
            )

        _sort_items(items, sort=sort)
        date_counts: dict[str, int] = {}
        await _count_items_by_date(
            date_counts,
            stage=stage,
            sub_stage=sub_stage,
            selected_nouns=selected_nouns,
            group_id=group_id,
            date=date,
            db_path_fn=_db_path,
        )
        page_items = items[offset:offset + limit]
        has_more = len(items) > offset + limit
        return {
            "items": page_items,
            "next_cursor": _encode_cursor(offset + limit) if has_more else "",
            "has_more": has_more,
            "date_counts": date_counts,
            "warnings": warnings,
        }

    @router.post("/learning/extract-all")
    async def learning_extract_all(request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        return await _run_extract_all(
            ctx=ctx,
            group_id=str(body.get("group_id") or "").strip(),
            limit=_int_body(body, "limit", default=80, lo=1, hi=200),
            max_batches=_int_body(body, "max_batches", default=1, lo=1, hi=5),
            batch_size=_int_body(body, "batch_size", default=50, lo=1, hi=200),
            timeout_seconds=EXTRACT_ALL_TIMEOUT_SECONDS,
            wait=_bool_body(body, "wait", default=True),
        )

    @router.get("/learning/extract-all/{run_id}")
    async def learning_extract_all_status(run_id: str) -> dict[str, Any]:
        return _extract_run_status(run_id)

    @router.get("/learning/settings")
    async def learning_settings_get() -> dict[str, Any]:
        slang_settings: dict[str, Any] = {}
        slang_store = getattr(ctx, "slang_store", None) if ctx else None
        if slang_store and hasattr(slang_store, "load_settings"):
            try:
                s = await slang_store.load_settings()
                slang_settings = s.model_dump() if hasattr(s, "model_dump") else {}
            except Exception:
                pass
        pipeline_settings = _load_pipeline_settings(_storage_dir())
        return {
            "autopilot": pipeline_settings.get("autopilot", {"enabled": False, "aggressiveness": "standard"}),
            "slang": slang_settings,
            "style": pipeline_settings.get("style", {"extract_enabled": True, "extract_interval_minutes": 120}),
            "consolidator": pipeline_settings.get("consolidator", {"auto_enabled": False, "interval_minutes": 360}),
            "affection": pipeline_settings.get("affection", {"scoring_enabled": True}),
        }

    @router.post("/learning/settings")
    async def learning_settings_save(request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        slang_payload = body.get("slang")
        if slang_payload and isinstance(slang_payload, dict):
            slang_store = getattr(ctx, "slang_store", None) if ctx else None
            if slang_store and hasattr(slang_store, "load_settings"):
                try:
                    from services.slang.store import SlangSettings
                    current = (await slang_store.load_settings()).model_dump()
                    current.update(slang_payload)
                    await slang_store.save_settings(SlangSettings.model_validate(current))
                except Exception as exc:
                    return {"ok": False, "error": f"slang settings: {exc}"}
        pipeline_patch: dict[str, Any] = {}
        for key in ("autopilot", "style", "consolidator", "affection"):
            if key in body and isinstance(body[key], dict):
                pipeline_patch[key] = body[key]
        if pipeline_patch:
            _save_pipeline_settings(_storage_dir(), pipeline_patch)
        return {"ok": True}

    @router.get("/learning/schedules")
    async def learning_schedules() -> dict[str, Any]:
        """Return last-run / status for each periodic learning task."""
        pipeline_settings = _load_pipeline_settings(_storage_dir())
        style_cfg = pipeline_settings.get("style", {})
        consolidator_cfg = pipeline_settings.get("consolidator", {})
        affection_cfg = pipeline_settings.get("affection", {})

        slang_last_run: str | None = None
        slang_store = getattr(ctx, "slang_store", None) if ctx else None
        if slang_store and hasattr(slang_store, "get_last_extract_run_time"):
            try:
                dt = await slang_store.get_last_extract_run_time()
                slang_last_run = dt.isoformat() if dt else None
            except Exception:
                pass

        return {
            "slang_extract": {
                "enabled": True,
                "last_run": slang_last_run,
                "status": "idle",
            },
            "style_extract": {
                "enabled": style_cfg.get("extract_enabled", True),
                "interval_minutes": style_cfg.get("extract_interval_minutes", 120),
                "status": "idle" if style_cfg.get("extract_enabled", True) else "disabled",
            },
            "consolidator": {
                "enabled": consolidator_cfg.get("auto_enabled", False),
                "interval_minutes": consolidator_cfg.get("interval_minutes", 360),
                "status": "idle" if consolidator_cfg.get("auto_enabled", False) else "disabled",
            },
            "affection_scoring": {
                "enabled": affection_cfg.get("scoring_enabled", True),
                "status": "active" if affection_cfg.get("scoring_enabled", True) else "disabled",
            },
        }

    @router.get("/learning/stats/trend")
    async def learning_stats_trend(
        days: int = Query(7, ge=1, le=30),
    ) -> dict[str, Any]:
        """Return daily new-item counts for the last N days."""
        now = datetime.now(TZ_SHANGHAI)
        day_counts: dict[str, dict[str, int]] = {}
        for i in range(days):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_counts[d] = {"candidate": 0, "approved": 0, "hits": 0}

        slang_db = _db_path("slang_store", "slang.db")
        if slang_db.exists():
            try:
                async with aiosqlite.connect(str(slang_db)) as db:
                    db.row_factory = aiosqlite.Row
                    if await _table_exists(db, "slang_terms"):
                        cutoff = (now - timedelta(days=days)).isoformat()
                        async with db.execute(
                            "SELECT status, created_at FROM slang_terms WHERE created_at >= ?",
                            (cutoff,),
                        ) as cur:
                            async for row in cur:
                                ts = str(row["created_at"] or "")[:10]
                                status = row["status"] or "candidate"
                                if ts in day_counts:
                                    bucket = "hits" if status == "hits" else (
                                        "approved" if status == "approved" else "candidate"
                                    )
                                    day_counts[ts][bucket] += 1
            except Exception:
                pass

        points = [
            {"date": d, **day_counts[d]}
            for d in sorted(day_counts.keys())
        ]
        return {"points": points}

    @router.get("/learning/activity")
    async def learning_activity(
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Return recent pipeline activity events from all noun stores."""
        events: list[dict[str, str]] = []
        status_labels = {
            "candidate": "候选",
            "review": "待审",
            "approved": "已生效",
            "hits": "命中",
            "archived": "归档",
        }

        slang_db = _db_path("slang_store", "slang.db")
        if slang_db.exists():
            try:
                async with aiosqlite.connect(str(slang_db)) as db:
                    db.row_factory = aiosqlite.Row
                    if await _table_exists(db, "slang_terms"):
                        async with db.execute(
                            "SELECT term, status, created_at FROM slang_terms "
                            "ORDER BY created_at DESC LIMIT ?",
                            (limit,),
                        ) as cur:
                            async for row in cur:
                                ts = str(row["created_at"] or "")
                                term = row["term"] or ""
                                status = row["status"] or "candidate"
                                label = status_labels.get(status, status)
                                events.append({
                                    "time": ts,
                                    "message": f'"{term}" → {label}',
                                    "type": "extract",
                                })
            except Exception:
                pass

        style_db = _db_path("style_store", "style.db")
        if style_db.exists():
            try:
                async with aiosqlite.connect(str(style_db)) as db:
                    db.row_factory = aiosqlite.Row
                    if await _table_exists(db, "style_expressions"):
                        async with db.execute(
                            "SELECT expression, status, created_at FROM style_expressions "
                            "ORDER BY created_at DESC LIMIT ?",
                            (limit // 2,),
                        ) as cur:
                            async for row in cur:
                                ts = str(row["created_at"] or "")
                                expr = row["expression"] or ""
                                status = row["status"] or "candidate"
                                label = status_labels.get(status, status)
                                events.append({
                                    "time": ts,
                                    "message": f'表达 "{expr[:20]}" → {label}',
                                    "type": "extract",
                                })
            except Exception:
                pass

        events.sort(key=lambda e: e.get("time", ""), reverse=True)
        return {"events": events[:limit]}

    # --- Autopilot endpoints ---

    @router.get("/learning/autopilot/status")
    async def autopilot_status() -> dict[str, Any]:
        runner = _get_autopilot_runner(ctx)
        if runner is None:
            return {"ok": False, "error": "Autopilot not initialized"}
        pipeline_settings = _load_pipeline_settings(_storage_dir())
        ap = pipeline_settings.get("autopilot", {})
        config = _autopilot_config(ap)
        status = await runner.status_all(config)
        return {
            "ok": True,
            "enabled": bool(ap.get("enabled", False)),
            "aggressiveness": str(ap.get("aggressiveness", "standard")),
            "domains": status,
        }

    @router.post("/learning/autopilot/run/{domain}")
    async def autopilot_run_domain(domain: str) -> dict[str, Any]:
        runner = _get_autopilot_runner(ctx)
        if runner is None:
            return {"ok": False, "error": "Autopilot not initialized"}
        pipeline_settings = _load_pipeline_settings(_storage_dir())
        ap = pipeline_settings.get("autopilot", {})
        config = _autopilot_config(ap)
        result = await runner.run_domain(domain, batch_size=15, config=config)
        return {
            "ok": result.ok,
            "error": result.error,
            "processed": result.processed_in_batch,
            "approved": result.approved_in_batch,
            "rejected": result.rejected_in_batch,
            "kept": result.kept_in_batch,
            "remaining": result.remaining,
            "completed": result.completed,
        }

    @router.post("/learning/autopilot/run-all")
    async def autopilot_run_all() -> dict[str, Any]:
        runner = _get_autopilot_runner(ctx)
        if runner is None:
            return {"ok": False, "error": "Autopilot not initialized"}
        pipeline_settings = _load_pipeline_settings(_storage_dir())
        ap = pipeline_settings.get("autopilot", {})
        config = _autopilot_config(ap)
        results = await runner.run_all(batch_size=50, config=config)
        return {
            "ok": True,
            "results": {
                domain: {
                    "ok": r.ok,
                    "processed": r.processed_in_batch,
                    "approved": r.approved_in_batch,
                    "rejected": r.rejected_in_batch,
                    "remaining": r.remaining,
                    "completed": r.completed,
                }
                for domain, r in results.items()
            },
        }

    @router.post("/learning/autopilot/reset/{domain}")
    async def autopilot_reset_domain(domain: str) -> dict[str, Any]:
        runner = _get_autopilot_runner(ctx)
        if runner is None:
            return {"ok": False, "error": "Autopilot not initialized"}
        reviewer = runner.get_reviewer(domain)
        if reviewer is None:
            return {"ok": False, "error": f"Unknown domain: {domain}"}
        await reviewer.reset_state()
        return {"ok": True}

    return router


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _load_pipeline_settings(storage_dir: Path) -> dict[str, Any]:
    return _ls.load(storage_dir)


def _save_pipeline_settings(storage_dir: Path, patch: dict[str, Any]) -> None:
    path = storage_dir / _ls._FILENAME
    current = _load_pipeline_settings(storage_dir)
    for key, value in patch.items():
        if isinstance(value, dict):
            existing = current.get(key, {})
            if isinstance(existing, dict):
                existing.update(value)
                current[key] = existing
            else:
                current[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _int_body(body: dict[str, Any], key: str, *, default: int, lo: int, hi: int) -> int:
    try:
        value = int(body.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(value, hi))


def _bool_body(body: dict[str, Any], key: str, *, default: bool) -> bool:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


async def _run_extract_all(
    *,
    ctx: Any,
    group_id: str = "",
    limit: int = 80,
    max_batches: int = 1,
    batch_size: int = 50,
    timeout_seconds: float = EXTRACT_ALL_TIMEOUT_SECONDS,
    wait: bool = True,
) -> dict[str, Any]:
    global _extract_all_active_run_id
    if _extract_all_active_run_id or _extract_all_lock.locked():
        payload = _extract_run_status(_extract_all_active_run_id or "")
        payload.update({"ok": False, "error": "already_running"})
        return payload

    _prune_extract_runs()
    run = _create_extract_run(
        group_id=group_id,
        limit=limit,
        max_batches=max_batches,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    )
    _extract_all_active_run_id = str(run["run_id"])

    if wait:
        return await _execute_extract_all_run(
            str(run["run_id"]),
            ctx=ctx,
            group_id=group_id,
            limit=limit,
            max_batches=max_batches,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
        )

    task = asyncio.create_task(_execute_extract_all_run(
        str(run["run_id"]),
        ctx=ctx,
        group_id=group_id,
        limit=limit,
        max_batches=max_batches,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    ))
    task.add_done_callback(
        lambda done: done.result()
        if not done.cancelled() and done.exception() is None
        else None
    )
    return _extract_run_snapshot(run)


async def _execute_extract_all_run(
    run_id: str,
    *,
    ctx: Any,
    group_id: str,
    limit: int,
    max_batches: int,
    batch_size: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    global _extract_all_active_run_id
    run = _extract_all_runs.get(run_id)
    if run is None:
        return _extract_run_not_found(run_id)

    try:
        async with _extract_all_lock:
            _update_extract_run(run, status="running")
            nouns = {
                "slang": _run_slang_extract(ctx, group_id=group_id, limit=limit),
                "style": _run_style_extract(
                    ctx,
                    group_id=group_id,
                    limit=limit,
                    max_batches=max_batches,
                ),
                "consolidator": _run_consolidator_extract(
                    ctx,
                    group_id=group_id,
                    max_batches=max_batches,
                    batch_size=batch_size,
                ),
            }
            results = await asyncio.gather(
                *(
                    _run_extract_noun(
                        run,
                        noun,
                        coro,
                        timeout_seconds=timeout_seconds,
                    )
                    for noun, coro in nouns.items()
                ),
                return_exceptions=False,
            )
            run["results"] = dict(results)
            _update_extract_run(
                run,
                status=_final_extract_status(run["results"]),
                finished=True,
            )
    except Exception as exc:
        _update_extract_run(run, status="failed", error=str(exc), finished=True)
    finally:
        if _extract_all_active_run_id == run_id:
            _extract_all_active_run_id = None

    return _extract_run_snapshot(run)


async def _run_extract_noun(
    run: dict[str, Any],
    noun: str,
    coro: Any,
    *,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]]:
    _update_extract_noun(run, noun, status="running")
    result = await _run_with_timeout(noun, coro, timeout_seconds=timeout_seconds)
    _update_extract_noun(
        run,
        noun,
        status=_extract_noun_status(result),
        result=result,
    )
    return noun, result


def _create_extract_run(
    *,
    group_id: str,
    limit: int,
    max_batches: int,
    batch_size: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    run_id = "learn_ext_" + secrets.token_hex(6)
    now = _now_iso()
    run: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "status": "queued",
        "error": "",
        "started_at": now,
        "updated_at": now,
        "finished_at": "",
        "group_id": group_id,
        "params": {
            "limit": limit,
            "max_batches": max_batches,
            "batch_size": batch_size,
            "timeout_seconds": timeout_seconds,
        },
        "nouns": {
            noun: {
                "status": "pending",
                "result": None,
                "error": "",
                "updated_at": now,
            }
            for noun in EXTRACT_ALL_NOUNS
        },
        "results": {},
    }
    _extract_all_runs[run_id] = run
    _prune_extract_runs()
    return run


def _update_extract_run(
    run: dict[str, Any],
    *,
    status: str,
    error: str = "",
    finished: bool = False,
) -> None:
    now = _now_iso()
    run["status"] = status
    run["updated_at"] = now
    run["error"] = error
    if finished:
        run["finished_at"] = now


def _update_extract_noun(
    run: dict[str, Any],
    noun: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    now = _now_iso()
    noun_state = run["nouns"].setdefault(noun, {})
    noun_state["status"] = status
    noun_state["updated_at"] = now
    if result is not None:
        noun_state["result"] = result
        noun_state["error"] = str(result.get("error") or "")
    run["updated_at"] = now


def _extract_run_status(run_id: str) -> dict[str, Any]:
    run = _extract_all_runs.get(str(run_id or ""))
    if run is None:
        return _extract_run_not_found(run_id)
    return _extract_run_snapshot(run)


def _extract_run_not_found(run_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "not_found",
        "run_id": run_id,
        "status": "not_found",
        "started_at": "",
        "updated_at": "",
        "finished_at": "",
        "group_id": "",
        "params": {},
        "nouns": {},
        "results": {},
    }


def _extract_run_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(run.get("ok", True)),
        "run_id": str(run.get("run_id") or ""),
        "status": str(run.get("status") or ""),
        "error": str(run.get("error") or ""),
        "started_at": str(run.get("started_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "finished_at": str(run.get("finished_at") or ""),
        "group_id": str(run.get("group_id") or ""),
        "params": dict(run.get("params") or {}),
        "nouns": {
            str(noun): {
                "status": str(state.get("status") or ""),
                "result": _copy_extract_result(state.get("result")),
                "error": str(state.get("error") or ""),
                "updated_at": str(state.get("updated_at") or ""),
            }
            for noun, state in dict(run.get("nouns") or {}).items()
            if isinstance(state, dict)
        },
        "results": {
            str(noun): _copy_extract_result(result)
            for noun, result in dict(run.get("results") or {}).items()
        },
    }


def _copy_extract_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return dict(result)
    return {"ok": True, "result": result}


def _extract_noun_status(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "skipped"
    if result.get("error") == "timeout":
        return "timeout"
    if result.get("error") == "cancelled":
        return "cancelled"
    if result.get("ok") is False:
        return "failed"
    return "completed"


def _final_extract_status(results: dict[str, dict[str, Any]]) -> str:
    statuses = [_extract_noun_status(result) for result in results.values()]
    failed = {"failed", "timeout", "cancelled"}
    if statuses and all(status in failed for status in statuses):
        return "failed"
    if any(status in failed for status in statuses):
        return "partial_failed"
    return "completed"


def _prune_extract_runs() -> None:
    removable = [
        run_id
        for run_id in _extract_all_runs
        if run_id != _extract_all_active_run_id
    ]
    while len(_extract_all_runs) > EXTRACT_ALL_RUN_LIMIT and removable:
        _extract_all_runs.pop(removable.pop(0), None)


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


async def _run_with_timeout(
    noun: str,
    coro: Any,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return _normalize_extract_result(noun, result)
    except TimeoutError:
        return {"ok": False, "error": "timeout", "noun": noun}
    except asyncio.CancelledError:
        return {"ok": False, "error": "cancelled", "noun": noun}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "noun": noun}


async def _run_slang_extract(ctx: Any, *, group_id: str, limit: int) -> dict[str, Any]:
    runner = _extract_runner(ctx, "slang")
    if runner is not None:
        return await _call_extract_runner(runner, group_id=group_id, limit=limit)
    plugin = getattr(ctx, "slang_plugin", None) if ctx is not None else None
    if plugin is not None and hasattr(plugin, "run_manual_extract"):
        return await plugin.run_manual_extract(group_id=group_id or None, limit=limit)
    return {"ok": False, "error": "Slang extractor not available", "skipped": True}


async def _run_style_extract(
    ctx: Any,
    *,
    group_id: str,
    limit: int,
    max_batches: int,
) -> dict[str, Any]:
    runner = _extract_runner(ctx, "style")
    if runner is not None:
        return await _call_extract_runner(
            runner,
            group_id=group_id,
            limit=limit,
            max_batches=max_batches,
        )
    plugin = getattr(ctx, "style_plugin", None) if ctx is not None else None
    if plugin is not None and hasattr(plugin, "run_manual_extract"):
        return await plugin.run_manual_extract(
            group_id=group_id or None,
            limit=limit,
            max_batches=max_batches,
        )
    return await run_style_manual_extract(
        style_store=await _style_store_for_ctx(ctx),
        message_log=getattr(ctx, "msg_log", None) if ctx is not None else None,
        llm_client=getattr(ctx, "llm_client", None) if ctx is not None else None,
        slang_store=getattr(ctx, "slang_store", None) if ctx is not None else None,
        group_id=group_id,
        scope="group",
        limit=limit,
        max_batches=max_batches,
    )


async def _run_consolidator_extract(
    ctx: Any,
    *,
    group_id: str,
    max_batches: int,
    batch_size: int,
) -> dict[str, Any]:
    runner = _extract_runner(ctx, "consolidator")
    if runner is not None:
        return await _call_extract_runner(
            runner,
            group_id=group_id,
            max_batches=max_batches,
            batch_size=batch_size,
        )
    consolidator = getattr(ctx, "memory_consolidator", None) if ctx is not None else None
    if consolidator is None or not hasattr(consolidator, "run_once"):
        return {"ok": False, "error": "Memory consolidator not available", "skipped": True}
    if not group_id:
        return {"ok": False, "error": "group_id required", "skipped": True}
    report = await consolidator.run_once(
        group_id=group_id,
        triggered_by="admin",
        scope="group",
        max_batches=max_batches,
        batch_size=batch_size,
    )
    return {
        "ok": True,
        "run_id": getattr(report, "run_id", ""),
        "scanned": getattr(report, "scanned", 0),
        "candidates": getattr(report, "candidates", 0),
        "status": getattr(report, "status", ""),
        "error_text": getattr(report, "error_text", ""),
    }


def _extract_runner(ctx: Any, noun: str) -> Any:
    runners = getattr(ctx, "learning_extract_runners", None) if ctx is not None else None
    if isinstance(runners, dict):
        return runners.get(noun)
    return None


async def _style_store_for_ctx(ctx: Any) -> StyleStore:
    store = getattr(ctx, "style_store", None) if ctx is not None else None
    if store is None:
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
        store = StyleStore(storage_dir / "style.db")
        await store.init()
        if ctx is not None:
            ctx.style_store = store
    elif not getattr(store, "initialized", False):
        await store.init()
    return store


async def _call_extract_runner(runner: Any, **kwargs: Any) -> dict[str, Any]:
    result = runner(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else {"ok": True, "result": result}


def _normalize_extract_result(noun: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": True, "noun": noun, "result": result}
    payload = dict(result)
    payload.setdefault("ok", True)
    payload.setdefault("noun", noun)
    return payload


def _new_stage_payload() -> dict[str, dict[str, Any]]:
    return {
        stage: {
            "total": 0,
            "by_noun": {noun: None for noun in NOUNS},
        }
        for stage in STAGES
    }


def _set_count(stages: dict[str, dict[str, Any]], stage: str, noun: str, value: int | None) -> None:
    stages[stage]["by_noun"][noun] = value


def _add_count(stages: dict[str, dict[str, Any]], stage: str, noun: str, value: int) -> None:
    current = stages[stage]["by_noun"].get(noun)
    stages[stage]["by_noun"][noun] = int(value) + (int(current) if current is not None else 0)


def _refresh_totals(stages: dict[str, dict[str, Any]]) -> None:
    for payload in stages.values():
        payload["total"] = sum(
            int(value)
            for value in payload["by_noun"].values()
            if value is not None
        )


def _today_prefix() -> str:
    return datetime.now(TZ_SHANGHAI).date().isoformat()


async def _count_one(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    return int((row["cnt"] if row is not None else 0) or 0)


def _warn(warnings: list[dict[str, str]], noun: str, exc: Exception) -> None:
    warnings.append({"noun": noun, "error": str(exc)})


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (name,),
    )
    return await cursor.fetchone() is not None


def _selected_nouns(noun: str) -> tuple[str, ...]:
    if noun == "all":
        return NOUNS
    parts = tuple(n.strip() for n in noun.split(",") if n.strip() in NOUNS)
    return parts or NOUNS


def _encode_cursor(offset: int) -> str:
    raw = str(max(0, int(offset))).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode(padded.encode()).decode()))
    except Exception:
        try:
            return max(0, int(cursor))
        except Exception:
            return 0


def _today_start() -> datetime:
    today = datetime.now(TZ_SHANGHAI).date()
    return datetime.combine(today, datetime_time.min, tzinfo=TZ_SHANGHAI)


def _date_filter(column: str, date: str, *, value_type: str = "iso") -> tuple[str, tuple[Any, ...]]:
    if date == "all":
        return "", ()
    if date == "today":
        if value_type == "epoch":
            return f" AND {column} >= ?", (_today_start().timestamp(),)
        return f" AND {column} LIKE ?", (f"{_today_prefix()}%",)
    days = 7 if date == "7d" else 30
    cutoff = datetime.now(TZ_SHANGHAI) - timedelta(days=days)
    if value_type == "epoch":
        return f" AND {column} >= ?", (cutoff.timestamp(),)
    return f" AND {column} >= ?", (cutoff.isoformat(timespec="seconds"),)


def _order_sql(sort: str, *, timestamp: str, confidence: str = "confidence", group: str = "group_id") -> str:
    if sort == "confidence":
        return f"{confidence} DESC, {timestamp} DESC"
    if sort == "group":
        return f"{group} ASC, {timestamp} DESC"
    return f"{timestamp} DESC"


def _sort_timestamp(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = str(value or "")
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _sort_items(items: list[dict[str, Any]], *, sort: str) -> None:
    if sort == "confidence":
        items.sort(
            key=lambda item: (float(item.get("confidence") or 0), _sort_timestamp(item.get("created_at"))),
            reverse=True,
        )
        return
    if sort == "group":
        items.sort(key=lambda item: (str(item.get("group_id") or ""), -_sort_timestamp(item.get("created_at"))))
        return
    items.sort(key=lambda item: _sort_timestamp(item.get("created_at")), reverse=True)


def _iso_from_epoch(value: Any) -> str:
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, TZ_SHANGHAI).isoformat(timespec="seconds")


def _item(
    *,
    item_id: str,
    noun: str,
    content: str,
    content_full: str,
    group_id: str,
    created_at: str,
    status: str,
    status_label: str,
    confidence: float | None,
    deep_link: str,
    review_drawer: str | None,
    source: str = "",
    tags: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "noun": noun,
        "kind_label": NOUN_LABELS.get(noun, noun),
        "content": content,
        "content_full": content_full,
        "group_id": group_id,
        "created_at": created_at,
        "status": status,
        "status_label": status_label,
        "confidence": confidence,
        "deep_link": deep_link,
        "review_drawer": review_drawer,
        "source": source,
        "tags": tags or [],
    }


async def _collect_slang_counts(
    db_path: Path,
    *,
    stages: dict[str, dict[str, Any]],
    group_id: str,
    warnings: list[dict[str, str]],
) -> None:
    for stage in STAGES:
        _set_count(stages, stage, "slang", 0)
    if not db_path.exists():
        return

    try:
        today_prefix = _today_prefix()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            group_sql, group_params = _group_filter("group_id", group_id)
            ai_reviewed = (
                "(json_extract(meta_json, '$.ai_review.status') IS NOT NULL"
                " OR json_extract(meta_json, '$.ai_reviewed_at') IS NOT NULL)"
            )
            human_reviewed = "json_extract(meta_json, '$.human_review.status') IS NOT NULL"

            _set_count(
                stages,
                "candidate",
                "slang",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM slang_terms
                        WHERE status = 'candidate'
                        {group_sql}""",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "review",
                "slang",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM slang_terms
                        WHERE status = 'approved' AND {ai_reviewed} AND NOT ({human_reviewed})
                        {group_sql}""",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "approved",
                "slang",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM slang_terms
                        WHERE status = 'approved' AND NOT ({ai_reviewed} AND NOT ({human_reviewed}))
                        {group_sql}""",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "hits",
                "slang",
                await _count_one(
                    db,
                    "SELECT COUNT(*) AS cnt FROM slang_observations WHERE observed_at LIKE ?",
                    (f"{today_prefix}%",),
                ),
            )
            _set_count(
                stages,
                "archived",
                "slang",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM slang_terms
                        WHERE status IN ('muted', 'expired') {group_sql}""",
                    group_params,
                ),
            )
    except Exception as exc:
        _warn(warnings, "slang", exc)


async def _collect_style_counts(
    db_path: Path,
    *,
    stages: dict[str, dict[str, Any]],
    group_id: str,
    warnings: list[dict[str, str]],
) -> None:
    _set_count(stages, "candidate", "style", 0)
    _set_count(stages, "review", "style", 0)
    _set_count(stages, "approved", "style", 0)
    _set_count(stages, "hits", "style", 0)
    _set_count(stages, "archived", "style", 0)
    if not db_path.exists():
        return

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            group_sql, group_params = _group_filter("group_id", group_id)
            obs_group_sql, obs_group_params = _group_filter("group_id", group_id)
            pending = await _count_one(
                db,
                f"SELECT COUNT(*) AS cnt FROM style_expressions WHERE status = 'pending' {group_sql}",
                group_params,
            )
            _set_count(stages, "candidate", "style", pending)
            _set_count(stages, "review", "style", pending)
            _set_count(
                stages,
                "approved",
                "style",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM style_expressions WHERE status = 'approved' {group_sql}",
                    group_params,
                ),
            )
            if await _table_exists(db, "style_observations"):
                _set_count(
                    stages,
                    "hits",
                    "style",
                    await _count_one(
                        db,
                        f"""SELECT COUNT(DISTINCT expression_id) AS cnt
                            FROM style_observations
                            WHERE observed_at LIKE ? {obs_group_sql}""",
                        (f"{_today_prefix()}%", *obs_group_params),
                    ),
                )
            _set_count(
                stages,
                "archived",
                "style",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM style_expressions
                        WHERE status IN ('rejected', 'muted') {group_sql}""",
                    group_params,
                ),
            )
    except Exception as exc:
        _warn(warnings, "style", exc)


async def _collect_episode_counts(
    db_path: Path,
    *,
    stages: dict[str, dict[str, Any]],
    group_id: str,
    warnings: list[dict[str, str]],
) -> None:
    _set_count(stages, "candidate", "episode", 0)
    _set_count(stages, "review", "episode", 0)
    _set_count(stages, "approved", "episode", 0)
    _set_count(stages, "hits", "episode", 0)
    _set_count(stages, "archived", "episode", 0)
    if not db_path.exists():
        return

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            group_sql, group_params = _group_filter("group_id", group_id)
            obs_group_sql, obs_group_params = _group_filter("group_id", group_id)
            _set_count(
                stages,
                "candidate",
                "episode",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM episodes WHERE episode_state = 'candidate' {group_sql}",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "review",
                "episode",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM episodes WHERE episode_state = 'approved' {group_sql}",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "approved",
                "episode",
                await _count_one(
                    db,
                    f"""SELECT COUNT(*) AS cnt FROM episodes
                        WHERE episode_state = 'enabled_for_prompt' {group_sql}""",
                    group_params,
                ),
            )
            if await _table_exists(db, "episode_observations"):
                _set_count(
                    stages,
                    "hits",
                    "episode",
                    await _count_one(
                        db,
                        f"""SELECT COUNT(DISTINCT episode_id) AS cnt
                            FROM episode_observations
                            WHERE observed_at LIKE ? {obs_group_sql}""",
                        (f"{_today_prefix()}%", *obs_group_params),
                    ),
                )
            _set_count(
                stages,
                "archived",
                "episode",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM episodes WHERE episode_state = 'disabled' {group_sql}",
                    group_params,
                ),
            )
    except Exception as exc:
        _warn(warnings, "episode", exc)


async def _collect_memory_counts(
    db_path: Path,
    *,
    stages: dict[str, dict[str, Any]],
    group_id: str,
    warnings: list[dict[str, str]],
) -> None:
    _set_count(stages, "candidate", "memory", None)
    _set_count(stages, "review", "memory", None)
    _set_count(stages, "approved", "memory", 0)
    _set_count(stages, "hits", "memory", None)
    _set_count(stages, "archived", "memory", 0)
    if not db_path.exists():
        return

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            group_sql = ""
            group_params: tuple[Any, ...] = ()
            if group_id:
                group_sql = " AND scope = ? AND scope_id = ?"
                group_params = ("group", group_id)
            _set_count(
                stages,
                "approved",
                "memory",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM memory_cards WHERE status = 'active' {group_sql}",
                    group_params,
                ),
            )
            _set_count(
                stages,
                "archived",
                "memory",
                await _count_one(
                    db,
                    f"SELECT COUNT(*) AS cnt FROM memory_cards WHERE status = 'expired' {group_sql}",
                    group_params,
                ),
            )
    except Exception as exc:
        _warn(warnings, "memory", exc)


async def _collect_consolidator_counts(
    db_path: Path,
    *,
    stages: dict[str, dict[str, Any]],
    group_id: str,
    warnings: list[dict[str, str]],
) -> None:
    for noun in ("fact", "graph_relation"):
        _set_count(stages, "candidate", noun, 0)
        _set_count(stages, "approved", noun, 0)
        _set_count(stages, "archived", noun, 0)
    if not db_path.exists():
        return

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            group_sql, group_params = _group_filter("group_id", group_id)
            # Only fact / graph_relation are surfaced from the consolidator
            # here — slang / style / episode candidates are promoted into
            # their dedicated stores and counted by their own collectors,
            # so folding the consolidator's pre-promotion rows on top would
            # double-count them.
            cursor = await db.execute(
                f"""SELECT domain, state, COUNT(*) AS cnt
                    FROM consolidator_candidates
                    WHERE domain IN ('fact', 'graph_relation')
                    {group_sql}
                    GROUP BY domain, state""",
                group_params,
            )
            rows = await cursor.fetchall()
            for row in rows:
                noun = str(row["domain"])
                state = str(row["state"])
                cnt = int(row["cnt"] or 0)
                if state in {"dry_run", "queued"}:
                    _add_count(stages, "candidate", noun, cnt)
                elif state == "approved":
                    _add_count(stages, "approved", noun, cnt)
                elif state == "rejected":
                    _add_count(stages, "archived", noun, cnt)
    except Exception as exc:
        _warn(warnings, "consolidator", exc)


async def _count_items_by_date(
    counts: dict[str, int],
    *,
    stage: str,
    sub_stage: str = "all",
    selected_nouns: tuple[str, ...],
    group_id: str,
    date: str,
    db_path_fn: Any,
) -> None:
    if "slang" in selected_nouns:
        db_path = db_path_fn("slang_store", "slang.db")
        if db_path.exists():
            try:
                async with aiosqlite.connect(db_path) as db:
                    if stage == "hits":
                        group_sql = " AND o.group_id = ?" if group_id else ""
                        group_params: tuple[Any, ...] = (group_id,) if group_id else ()
                        cur = await db.execute(
                            f"""SELECT substr(MAX(o.observed_at), 1, 10) AS d, COUNT(DISTINCT t.term_id) AS cnt
                                FROM slang_observations o
                                JOIN slang_terms t ON t.term_id = o.term_id
                                WHERE o.observed_at LIKE ? AND t.status = 'approved'
                                {group_sql}
                                GROUP BY substr(o.observed_at, 1, 10)""",
                            (f"{_today_prefix()}%", *group_params),
                        )
                        for row in await cur.fetchall():
                            d = str(row[0] or "")
                            if d:
                                counts[d] = counts.get(d, 0) + int(row[1])
                    else:
                        where = _slang_stage_where(stage, sub_stage)
                        if where:
                            group_sql, group_params = _group_filter("group_id", group_id)
                            date_sql, date_params = _date_filter("created_at", date)
                            cur = await db.execute(
                                f"""SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS cnt
                                    FROM slang_terms
                                    WHERE {where} {group_sql} {date_sql}
                                    GROUP BY d""",
                                (*group_params, *date_params),
                            )
                            for row in await cur.fetchall():
                                d = str(row[0] or "")
                                if d:
                                    counts[d] = counts.get(d, 0) + int(row[1])
            except Exception:
                pass
    if "style" in selected_nouns:
        db_path = db_path_fn("style_store", "style.db")
        if db_path.exists():
            try:
                async with aiosqlite.connect(db_path) as db:
                    if stage == "hits":
                        group_sql = " AND o.group_id = ?" if group_id else ""
                        group_params = (group_id,) if group_id else ()
                        cur = await db.execute(
                            f"""SELECT substr(MAX(o.observed_at), 1, 10) AS d, COUNT(DISTINCT e.expression_id) AS cnt
                                FROM style_observations o
                                JOIN style_expressions e ON e.expression_id = o.expression_id
                                WHERE o.observed_at LIKE ? AND e.status = 'approved'
                                {group_sql}
                                GROUP BY substr(o.observed_at, 1, 10)""",
                            (f"{_today_prefix()}%", *group_params),
                        )
                        for row in await cur.fetchall():
                            d = str(row[0] or "")
                            if d:
                                counts[d] = counts.get(d, 0) + int(row[1])
                    else:
                        statuses = _style_statuses_for_stage(stage)
                        if statuses:
                            ph = ",".join("?" * len(statuses))
                            group_sql, group_params = _group_filter("group_id", group_id)
                            date_sql, date_params = _date_filter("created_at", date)
                            cur = await db.execute(
                                f"""SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS cnt
                                    FROM style_expressions
                                    WHERE status IN ({ph}) {group_sql} {date_sql}
                                    GROUP BY d""",
                                (*statuses, *group_params, *date_params),
                            )
                            for row in await cur.fetchall():
                                d = str(row[0] or "")
                                if d:
                                    counts[d] = counts.get(d, 0) + int(row[1])
            except Exception:
                pass
    if "episode" in selected_nouns:
        db_path = db_path_fn("episode_store", "episodic.db")
        if db_path.exists():
            try:
                async with aiosqlite.connect(db_path) as db:
                    if stage == "hits":
                        group_sql = " AND o.group_id = ?" if group_id else ""
                        group_params = (group_id,) if group_id else ()
                        cur = await db.execute(
                            f"""SELECT substr(MAX(o.observed_at), 1, 10) AS d, COUNT(DISTINCT e.episode_id) AS cnt
                                FROM episode_observations o
                                JOIN episodes e ON e.episode_id = o.episode_id
                                WHERE o.observed_at LIKE ? AND e.episode_state = 'enabled_for_prompt'
                                {group_sql}
                                GROUP BY substr(o.observed_at, 1, 10)""",
                            (f"{_today_prefix()}%", *group_params),
                        )
                        for row in await cur.fetchall():
                            d = str(row[0] or "")
                            if d:
                                counts[d] = counts.get(d, 0) + int(row[1])
                    else:
                        states = _episode_states_for_stage(stage)
                        if states:
                            ph = ",".join("?" * len(states))
                            group_sql, group_params = _group_filter("group_id", group_id)
                            date_sql, date_params = _date_filter("created_at", date)
                            cur = await db.execute(
                                f"""SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS cnt
                                    FROM episodes
                                    WHERE state IN ({ph}) {group_sql} {date_sql}
                                    GROUP BY d""",
                                (*states, *group_params, *date_params),
                            )
                            for row in await cur.fetchall():
                                d = str(row[0] or "")
                                if d:
                                    counts[d] = counts.get(d, 0) + int(row[1])
            except Exception:
                pass


async def _collect_slang_items(
    db_path: Path,
    *,
    items: list[dict[str, Any]],
    stage: str,
    sub_stage: str = "all",
    group_id: str,
    date: str,
    sort: str,
    limit: int,
    warnings: list[dict[str, str]],
) -> None:
    if not db_path.exists():
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if stage == "hits":
                await _collect_slang_hit_items(db, items=items, group_id=group_id, sort=sort, limit=limit)
                return
            where = _slang_stage_where(stage, sub_stage)
            if not where:
                return
            group_sql, group_params = _group_filter("group_id", group_id)
            date_sql, date_params = _date_filter("created_at", date)
            cursor = await db.execute(
                f"""SELECT term_id, term, meaning, group_id, confidence, status,
                           created_at, updated_at, scope, repeat_policy, meta_json
                    FROM slang_terms
                    WHERE {where}
                    {group_sql}
                    {date_sql}
                    ORDER BY {_order_sql(sort, timestamp="created_at")}
                    LIMIT ?""",
                (*group_params, *date_params, int(limit)),
            )
            for row in await cursor.fetchall():
                d = dict(row)
                term = str(d.get("term") or d.get("term_id") or "")
                meaning = str(d.get("meaning") or "")
                content_full = f"{term} = {meaning}" if meaning else term
                ai_status = _derive_slang_ai_status(d.get("meta_json"), str(d.get("status") or ""))
                tags = _slang_tags(d)
                if ai_status:
                    tags = [*tags, {"key": "ai_status", "value": ai_status, "label": _AI_STATUS_LABELS[ai_status]}]
                items.append(_item(
                    item_id=f"slang-{d.get('term_id')}",
                    noun="slang",
                    content=term,
                    content_full=content_full,
                    group_id=str(d.get("group_id") or ""),
                    created_at=str(d.get("created_at") or d.get("updated_at") or ""),
                    status=str(d.get("status") or stage),
                    status_label=_slang_status_label(stage, str(d.get("status") or "")),
                    confidence=_float_or_none(d.get("confidence")),
                    deep_link=f"/slang?id={d.get('term_id')}",
                    review_drawer="slang",
                    source="slang",
                    tags=tags,
                ))
    except Exception as exc:
        _warn(warnings, "slang", exc)


async def _collect_slang_hit_items(
    db: aiosqlite.Connection,
    *,
    items: list[dict[str, Any]],
    group_id: str,
    sort: str,
    limit: int,
) -> None:
    group_sql = " AND o.group_id = ?" if group_id else ""
    group_params: tuple[Any, ...] = (group_id,) if group_id else ()
    cursor = await db.execute(
        f"""SELECT t.term_id, t.term, t.meaning, t.group_id, t.confidence, t.status,
                   MAX(o.observed_at) AS hit_at, COUNT(*) AS hit_count
            FROM slang_observations o
            JOIN slang_terms t ON t.term_id = o.term_id
            WHERE o.observed_at LIKE ? AND t.status = 'approved'
            {group_sql}
            GROUP BY t.term_id
            ORDER BY {_order_sql(sort, timestamp="hit_at", group="t.group_id")}
            LIMIT ?""",
        (f"{_today_prefix()}%", *group_params, int(limit)),
    )
    for row in await cursor.fetchall():
        d = dict(row)
        term = str(d.get("term") or d.get("term_id") or "")
        meaning = str(d.get("meaning") or "")
        hit_count = int(d.get("hit_count") or 0)
        content_full = f"{term} = {meaning}" if meaning else term
        if hit_count:
            content_full = f"{content_full} · 今日 {hit_count} 次"
        items.append(_item(
            item_id=f"slang-hit-{d.get('term_id')}",
            noun="slang",
            content=term,
            content_full=content_full,
            group_id=str(d.get("group_id") or ""),
            created_at=str(d.get("hit_at") or ""),
            status="hit",
            status_label="今日命中",
            confidence=_float_or_none(d.get("confidence")),
            deep_link=f"/slang?id={d.get('term_id')}",
            review_drawer="slang",
            source="slang_observation",
        ))


async def _collect_style_items(
    db_path: Path,
    *,
    items: list[dict[str, Any]],
    stage: str,
    group_id: str,
    date: str,
    sort: str,
    limit: int,
    warnings: list[dict[str, str]],
) -> None:
    if not db_path.exists():
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if stage == "hits":
                if await _table_exists(db, "style_observations"):
                    await _collect_style_hit_items(db, items=items, group_id=group_id, sort=sort, limit=limit)
                return
            statuses = _style_statuses_for_stage(stage)
            if not statuses:
                return
            placeholders = ",".join("?" for _ in statuses)
            group_sql, group_params = _group_filter("group_id", group_id)
            date_sql, date_params = _date_filter("created_at", date)
            cursor = await db.execute(
                f"""SELECT expression_id, situation, style, group_id, confidence,
                           status, created_at, updated_at, scope, output_policy,
                           risk_tags_json, meta_json
                    FROM style_expressions
                    WHERE status IN ({placeholders})
                    {group_sql}
                    {date_sql}
                    ORDER BY {_order_sql(sort, timestamp="created_at")}
                    LIMIT ?""",
                (*statuses, *group_params, *date_params, int(limit)),
            )
            for row in await cursor.fetchall():
                items.append(_style_item(dict(row), stage=stage))
    except Exception as exc:
        _warn(warnings, "style", exc)


async def _collect_style_hit_items(
    db: aiosqlite.Connection,
    *,
    items: list[dict[str, Any]],
    group_id: str,
    sort: str,
    limit: int,
) -> None:
    group_sql = " AND o.group_id = ?" if group_id else ""
    group_params: tuple[Any, ...] = (group_id,) if group_id else ()
    cursor = await db.execute(
        f"""SELECT e.expression_id, e.situation, e.style, e.group_id, e.confidence,
                   e.status, MAX(o.observed_at) AS hit_at, COUNT(*) AS hit_count
            FROM style_observations o
            JOIN style_expressions e ON e.expression_id = o.expression_id
            WHERE o.observed_at LIKE ? AND e.status = 'approved'
            {group_sql}
            GROUP BY e.expression_id
            ORDER BY {_order_sql(sort, timestamp="hit_at", group="e.group_id")}
            LIMIT ?""",
        (f"{_today_prefix()}%", *group_params, int(limit)),
    )
    for row in await cursor.fetchall():
        items.append(_style_item(dict(row), stage="hits"))


def _style_item(d: dict[str, Any], *, stage: str) -> dict[str, Any]:
    style = str(d.get("style") or d.get("expression_id") or "")
    situation = str(d.get("situation") or "")
    full = f"{situation} / {style}" if situation else style
    hit_count = int(d.get("hit_count") or 0)
    if hit_count:
        full = f"{full} · 今日 {hit_count} 次"
    tags = _style_tags(d)
    ai_status = _derive_ai_status_from_meta(d.get("meta_json"))
    if ai_status:
        tags = [*tags, {"key": "ai_status", "value": ai_status, "label": _AI_STATUS_LABELS[ai_status]}]
    return _item(
        item_id=f"style-{d.get('expression_id')}",
        noun="style",
        content=style,
        content_full=full,
        group_id=str(d.get("group_id") or ""),
        created_at=str(d.get("hit_at") or d.get("created_at") or d.get("updated_at") or ""),
        status="hit" if stage == "hits" else str(d.get("status") or stage),
        status_label="今日命中" if stage == "hits" else _style_status_label(stage),
        confidence=_float_or_none(d.get("confidence")),
        deep_link=f"/style?id={d.get('expression_id')}",
        review_drawer="style",
        source="style_observation" if stage == "hits" else "style",
        tags=tags,
    )


async def _collect_episode_items(
    db_path: Path,
    *,
    items: list[dict[str, Any]],
    stage: str,
    group_id: str,
    date: str,
    sort: str,
    limit: int,
    warnings: list[dict[str, str]],
) -> None:
    if not db_path.exists():
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if stage == "hits":
                if await _table_exists(db, "episode_observations"):
                    await _collect_episode_hit_items(db, items=items, group_id=group_id, sort=sort, limit=limit)
                return
            states = _episode_states_for_stage(stage)
            if not states:
                return
            placeholders = ",".join("?" for _ in states)
            group_sql, group_params = _group_filter("group_id", group_id)
            date_sql, date_params = _date_filter("created_at", date)
            cursor = await db.execute(
                f"""SELECT episode_id, group_id, situation, reflection, confidence,
                           episode_state, created_at, updated_at, scope, meta_json
                    FROM episodes
                    WHERE episode_state IN ({placeholders})
                    {group_sql}
                    {date_sql}
                    ORDER BY {_order_sql(sort, timestamp="created_at")}
                    LIMIT ?""",
                (*states, *group_params, *date_params, int(limit)),
            )
            for row in await cursor.fetchall():
                items.append(_episode_item(dict(row), stage=stage))
    except Exception as exc:
        _warn(warnings, "episode", exc)


async def _collect_episode_hit_items(
    db: aiosqlite.Connection,
    *,
    items: list[dict[str, Any]],
    group_id: str,
    sort: str,
    limit: int,
) -> None:
    group_sql = " AND o.group_id = ?" if group_id else ""
    group_params: tuple[Any, ...] = (group_id,) if group_id else ()
    cursor = await db.execute(
        f"""SELECT e.episode_id, e.group_id, e.situation, e.reflection,
                   e.confidence, e.episode_state,
                   MAX(o.observed_at) AS hit_at, COUNT(*) AS hit_count
            FROM episode_observations o
            JOIN episodes e ON e.episode_id = o.episode_id
            WHERE o.observed_at LIKE ? AND e.episode_state = 'enabled_for_prompt'
            {group_sql}
            GROUP BY e.episode_id
            ORDER BY {_order_sql(sort, timestamp="hit_at", group="e.group_id")}
            LIMIT ?""",
        (f"{_today_prefix()}%", *group_params, int(limit)),
    )
    for row in await cursor.fetchall():
        items.append(_episode_item(dict(row), stage="hits"))


def _episode_item(d: dict[str, Any], *, stage: str) -> dict[str, Any]:
    situation = str(d.get("situation") or d.get("episode_id") or "")
    reflection = str(d.get("reflection") or "")
    full = f"{situation} / {reflection}" if reflection else situation
    hit_count = int(d.get("hit_count") or 0)
    if hit_count:
        full = f"{full} · 今日 {hit_count} 次"
    tags = _episode_tags(d)
    ai_status = _derive_ai_status_from_meta(d.get("meta_json"))
    if ai_status:
        tags = [*tags, {"key": "ai_status", "value": ai_status, "label": _AI_STATUS_LABELS[ai_status]}]
    return _item(
        item_id=f"episode-{d.get('episode_id')}",
        noun="episode",
        content=situation,
        content_full=full,
        group_id=str(d.get("group_id") or ""),
        created_at=str(d.get("hit_at") or d.get("created_at") or d.get("updated_at") or ""),
        status="hit" if stage == "hits" else str(d.get("episode_state") or stage),
        status_label="今日命中" if stage == "hits" else _episode_status_label(stage),
        confidence=_float_or_none(d.get("confidence")),
        deep_link=f"/episodes?id={d.get('episode_id')}",
        review_drawer="episode",
        source="episode_observation" if stage == "hits" else "episode",
        tags=tags,
    )


async def _collect_memory_items(
    db_path: Path,
    *,
    items: list[dict[str, Any]],
    stage: str,
    group_id: str,
    date: str,
    sort: str,
    limit: int,
    warnings: list[dict[str, str]],
) -> None:
    if stage not in {"approved", "archived"} or not db_path.exists():
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            status = "active" if stage == "approved" else "expired"
            group_sql = " AND scope = ? AND scope_id = ?" if group_id else ""
            group_params: tuple[Any, ...] = ("group", group_id) if group_id else ()
            date_sql, date_params = _date_filter("created_at", date)
            cursor = await db.execute(
                f"""SELECT card_id, category, scope, scope_id, content, confidence,
                           status, priority, source, created_at, updated_at
                    FROM memory_cards
                    WHERE status = ?
                    {group_sql}
                    {date_sql}
                    ORDER BY {_order_sql(sort, timestamp="created_at", group="scope_id")}
                    LIMIT ?""",
                (status, *group_params, *date_params, int(limit)),
            )
            for row in await cursor.fetchall():
                d = dict(row)
                card_id = str(d.get("card_id") or "")
                content = str(d.get("content") or d.get("card_id") or "")
                scope = str(d.get("scope") or "")
                scope_id = str(d.get("scope_id") or "")
                items.append(_item(
                    item_id=f"memory-{card_id}",
                    noun="memory",
                    content=content,
                    content_full=content,
                    group_id=scope_id if scope == "group" else "",
                    created_at=str(d.get("created_at") or d.get("updated_at") or ""),
                    status=str(d.get("status") or status),
                    status_label="活跃" if status == "active" else "过期",
                    confidence=_float_or_none(d.get("confidence")),
                    deep_link=f"/memory?view=manage&card_id={quote(card_id, safe='')}",
                    review_drawer=None,
                    source="memory",
                    tags=_memory_tags(d),
                ))
    except Exception as exc:
        _warn(warnings, "memory", exc)


async def _collect_consolidator_items(
    db_path: Path,
    *,
    items: list[dict[str, Any]],
    stage: str,
    nouns: tuple[str, ...],
    group_id: str,
    date: str,
    sort: str,
    limit: int,
    warnings: list[dict[str, str]],
) -> None:
    if stage not in {"candidate", "approved", "archived"} or not db_path.exists():
        return
    states = {
        "candidate": ("dry_run", "queued"),
        "approved": ("approved",),
        "archived": ("rejected",),
    }[stage]
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            state_placeholders = ",".join("?" for _ in states)
            noun_placeholders = ",".join("?" for _ in nouns)
            group_sql, group_params = _group_filter("group_id", group_id)
            date_sql, date_params = _date_filter("created_at", date, value_type="epoch")
            cursor = await db.execute(
                f"""SELECT candidate_id, domain, scope, group_id, payload_json,
                           confidence, state, created_at
                    FROM consolidator_candidates
                    WHERE state IN ({state_placeholders})
                      AND domain IN ({noun_placeholders})
                    {group_sql}
                    {date_sql}
                    ORDER BY {_order_sql(sort, timestamp="created_at")}
                    LIMIT ?""",
                (*states, *nouns, *group_params, *date_params, int(limit)),
            )
            for row in await cursor.fetchall():
                d = dict(row)
                domain = str(d.get("domain") or "")
                payload = _parse_json_object(d.get("payload_json"))
                content = _summarize_consolidator_payload(domain, payload)
                items.append(_item(
                    item_id=f"consolidator-{d.get('candidate_id')}",
                    noun=domain,
                    content=content,
                    content_full=_full_consolidator_payload(domain, payload),
                    group_id=str(d.get("group_id") or ""),
                    created_at=_iso_from_epoch(d.get("created_at")),
                    status=str(d.get("state") or stage),
                    status_label=_consolidator_status_label(str(d.get("state") or "")),
                    confidence=_float_or_none(d.get("confidence")),
                    deep_link="/memory-consolidator",
                    review_drawer="consolidator",
                    source="consolidator",
                ))
    except Exception as exc:
        _warn(warnings, "consolidator", exc)


_AI_STATUS_LABELS = {
    "unscanned": "未扫描",
    "ai_kept": "观察中",
    "ai_approved": "AI 通过",
    "ai_rejected": "AI 否决",
}


def _derive_slang_ai_status(meta_json_raw: Any, status: str) -> str:
    if not meta_json_raw:
        return "unscanned"
    try:
        meta = json.loads(meta_json_raw) if isinstance(meta_json_raw, str) else dict(meta_json_raw)
    except Exception:
        return "unscanned"
    ai_review = meta.get("ai_review") or {}
    decision = str(ai_review.get("decision") or meta.get("ai_review_decision") or "").lower()
    reviewed_at = ai_review.get("status") or ai_review.get("reviewed_at") or meta.get("ai_reviewed_at")
    if not reviewed_at and not decision:
        if meta.get("ai_approved") is True or meta.get("ai_review_decision") == "approved":
            return "ai_approved"
        return "unscanned"
    if decision == "rejected":
        return "ai_rejected"
    if decision in ("kept", "keep"):
        return "ai_kept"
    if decision == "approved":
        return "ai_approved"
    return "unscanned"


def _derive_ai_status_from_meta(meta_json_raw: Any) -> str:
    """Generic ai_status derivation for non-slang nouns."""
    if not meta_json_raw:
        return "unscanned"
    try:
        meta = json.loads(meta_json_raw) if isinstance(meta_json_raw, str) else dict(meta_json_raw)
    except Exception:
        return "unscanned"
    decision = str(meta.get("ai_review_decision") or "").lower()
    if not decision:
        ai_review = meta.get("ai_review") or {}
        decision = str(ai_review.get("decision") or "").lower()
    if decision == "approved":
        return "ai_approved"
    if decision == "rejected":
        return "ai_rejected"
    if decision in ("kept", "keep"):
        return "ai_kept"
    if meta.get("ai_reviewed_at"):
        return "ai_kept"
    return "unscanned"


def _slang_stage_where(stage: str, sub_stage: str = "all") -> str:
    ai_reviewed = (
        "(json_extract(meta_json, '$.ai_review.status') IS NOT NULL"
        " OR json_extract(meta_json, '$.ai_reviewed_at') IS NOT NULL)"
    )
    human_reviewed = "json_extract(meta_json, '$.human_review.status') IS NOT NULL"
    ai_kept = (
        "(json_extract(meta_json, '$.ai_review.decision') = 'keep'"
        " OR json_extract(meta_json, '$.ai_review_decision') = 'kept')"
    )
    if stage == "candidate":
        if sub_stage == "unscanned":
            return f"status = 'candidate' AND NOT ({ai_reviewed})"
        if sub_stage == "ai_rejected":
            return f"status = 'candidate' AND {ai_reviewed} AND NOT {ai_kept}"
        if sub_stage == "ai_kept":
            return f"status = 'candidate' AND {ai_reviewed} AND {ai_kept}"
        return "status = 'candidate'"
    if stage == "review":
        return f"status = 'approved' AND {ai_reviewed} AND NOT ({human_reviewed})"
    if stage == "approved":
        return f"status = 'approved' AND NOT ({ai_reviewed} AND NOT ({human_reviewed}))"
    if stage == "archived":
        return "status IN ('muted', 'expired')"
    return ""


def _style_statuses_for_stage(stage: str) -> tuple[str, ...]:
    return {
        "candidate": ("pending",),
        "review": ("pending",),
        "approved": ("approved",),
        "archived": ("rejected", "muted"),
    }.get(stage, ())


def _episode_states_for_stage(stage: str) -> tuple[str, ...]:
    return {
        "candidate": ("candidate",),
        "review": ("approved",),
        "approved": ("enabled_for_prompt",),
        "archived": ("disabled",),
    }.get(stage, ())


def _slang_status_label(stage: str, status: str) -> str:
    if stage == "review":
        return "AI 待复核"
    return {
        "candidate": "候选",
        "approved": "入库",
        "muted": "静音",
        "expired": "过期",
    }.get(status, status or stage)


def _style_status_label(stage: str) -> str:
    return {
        "candidate": "候选",
        "review": "待审",
        "approved": "入库",
        "archived": "归档",
    }.get(stage, stage)


def _episode_status_label(stage: str) -> str:
    return {
        "candidate": "候选",
        "review": "待启用",
        "approved": "已启用",
        "archived": "已禁用",
    }.get(stage, stage)


def _consolidator_status_label(state: str) -> str:
    return {
        "dry_run": "预演",
        "queued": "排队",
        "approved": "已通过",
        "rejected": "已拒绝",
    }.get(state, state)


def _float_or_none(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _parse_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _summarize_consolidator_payload(domain: str, payload: dict[str, Any]) -> str:
    if domain == "fact":
        return " ".join(str(payload.get(key) or "").strip() for key in ("subject", "predicate", "object")).strip()
    if domain == "slang":
        return str(payload.get("term") or "").strip()
    if domain == "style":
        return str(payload.get("expression") or payload.get("style") or "").strip()
    if domain == "episode":
        return str(payload.get("situation") or payload.get("reflection") or "").strip()
    if domain == "graph_relation":
        return " ".join(
            str(payload.get(key) or "").strip()
            for key in ("subject_node", "predicate", "object_node")
        ).strip()
    return str(payload)[:80]


def _full_consolidator_payload(domain: str, payload: dict[str, Any]) -> str:
    summary = _summarize_consolidator_payload(domain, payload)
    if summary:
        return summary
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _group_filter(column: str, group_id: str) -> tuple[str, tuple[Any, ...]]:
    if not group_id:
        return "", ()
    return f" AND {column} = ?", (group_id,)


_SCOPE_LABELS: dict[str, str] = {"group": "群级", "global": "全局", "user": "用户级"}
_REPEAT_POLICY_LABELS: dict[str, str] = {
    "understand_only": "仅理解",
    "rewrite": "可改写",
    "use": "可使用",
}
_OUTPUT_POLICY_LABELS: dict[str, str] = {
    "use": "可使用",
    "transform": "需转化",
    "observe": "仅观察",
}
_CATEGORY_LABELS: dict[str, str] = {
    "preference": "偏好",
    "boundary": "边界",
    "event": "事件",
    "commitment": "承诺",
    "fact": "事实",
    "habit": "习惯",
    "relationship": "关系",
}


def _slang_tags(d: dict[str, Any]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    scope = str(d.get("scope") or "group")
    if scope in _SCOPE_LABELS:
        tags.append({"key": "scope", "value": scope, "label": _SCOPE_LABELS[scope]})
    policy = str(d.get("repeat_policy") or "")
    if policy in _REPEAT_POLICY_LABELS:
        tags.append({"key": "repeat_policy", "value": policy, "label": _REPEAT_POLICY_LABELS[policy]})
    return tags


def _style_tags(d: dict[str, Any]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    scope = str(d.get("scope") or "group")
    if scope in _SCOPE_LABELS:
        tags.append({"key": "scope", "value": scope, "label": _SCOPE_LABELS[scope]})
    policy = str(d.get("output_policy") or "")
    if policy in _OUTPUT_POLICY_LABELS:
        tags.append({"key": "output_policy", "value": policy, "label": _OUTPUT_POLICY_LABELS[policy]})
    risk_raw = d.get("risk_tags_json") or "[]"
    try:
        risk_tags = json.loads(risk_raw) if isinstance(risk_raw, str) else risk_raw
    except (json.JSONDecodeError, TypeError):
        risk_tags = []
    if isinstance(risk_tags, list):
        for tag in risk_tags[:3]:
            if tag:
                tags.append({"key": "risk", "value": str(tag), "label": str(tag)})
    return tags


def _episode_tags(d: dict[str, Any]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    scope = str(d.get("scope") or "group")
    if scope in _SCOPE_LABELS:
        tags.append({"key": "scope", "value": scope, "label": _SCOPE_LABELS[scope]})
    return tags


def _memory_tags(d: dict[str, Any]) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    scope = str(d.get("scope") or "")
    if scope in _SCOPE_LABELS:
        tags.append({"key": "scope", "value": scope, "label": _SCOPE_LABELS[scope]})
    category = str(d.get("category") or "")
    if category:
        label = _CATEGORY_LABELS.get(category, category)
        tags.append({"key": "category", "value": category, "label": label})
    return tags
