"""JSON API: system — health, version, resources, humanizer."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter


def create_system_router(
    *,
    config: Any = None,
    short_term_memory: Any = None,
    humanizer: Any = None,
    bot: Any = None,
    ctx: Any = None,
    restart_executor: Callable[[int], None] | None = None,
) -> APIRouter:
    router = APIRouter()
    restart_tasks: list[asyncio.Task[None]] = []

    def _schedule_restart(delay_seconds: float = 0.6) -> bool:
        exit_fn = restart_executor or os._exit

        async def _delayed_exit() -> None:
            await asyncio.sleep(delay_seconds)
            exit_fn(0)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_delayed_exit())
            restart_tasks.append(task)
            return True
        except Exception:
            # 没有可用 event loop 时，不中断当前请求，只返回失败提示。
            return False

    def _connected_bot_count() -> int:
        # Prefer runtime bot registry (dynamic), then fallback to injected refs.
        try:
            import nonebot

            bots = nonebot.get_bots()
            if isinstance(bots, dict):
                return len(bots)
        except Exception:
            pass

        if ctx is not None and getattr(ctx, "bot", None) is not None:
            return 1
        if bot is not None:
            return 1
        return 0

    def _read_proc_text(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    def _extract_proc_kib(content: str, key: str) -> int | None:
        match = re.search(rf"^{re.escape(key)}:\s+(\d+)\s+kB$", content, re.MULTILINE)
        if not match:
            return None
        return int(match.group(1))

    def _collect_fallback_system_info() -> dict[str, Any]:
        info: dict[str, Any] = {
            "cpu_percent": None,
            "memory": None,
            "disk": None,
            "process": None,
        }

        # CPU fallback: normalized load avg as percentage of logical cores.
        try:
            load1, _load5, _load15 = os.getloadavg()
            cpu_count = max(1, os.cpu_count() or 1)
            info["cpu_percent"] = max(0.0, min(100.0, round((load1 / cpu_count) * 100, 1)))
        except Exception:
            info["cpu_percent"] = None

        # Memory fallback via /proc/meminfo (Linux containers).
        meminfo = _read_proc_text("/proc/meminfo")
        total_kib = _extract_proc_kib(meminfo, "MemTotal")
        available_kib = _extract_proc_kib(meminfo, "MemAvailable")
        if total_kib is not None and available_kib is not None and total_kib > 0:
            used_kib = max(0, total_kib - available_kib)
            info["memory"] = {
                "total_gb": round(total_kib / (1024 * 1024), 1),
                "used_gb": round(used_kib / (1024 * 1024), 1),
                "percent": round((used_kib / total_kib) * 100, 1),
            }

        # Disk fallback via stdlib.
        try:
            disk = shutil.disk_usage("/")
            info["disk"] = {
                "total_gb": round(disk.total / (1024 ** 3), 1),
                "used_gb": round(disk.used / (1024 ** 3), 1),
                "percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0.0,
            }
        except Exception:
            info["disk"] = None

        # Process fallback from /proc.
        pid = os.getpid()
        process_info: dict[str, Any] = {"pid": pid, "memory_mb": None, "threads": None}
        try:
            statm = _read_proc_text("/proc/self/statm").split()
            if len(statm) >= 2:
                rss_pages = int(statm[1])
                page_size = os.sysconf("SC_PAGE_SIZE")
                process_info["memory_mb"] = round((rss_pages * page_size) / (1024 ** 2), 1)
        except Exception:
            pass

        status = _read_proc_text("/proc/self/status")
        thread_match = re.search(r"^Threads:\s+(\d+)$", status, re.MULTILINE)
        if thread_match:
            process_info["threads"] = int(thread_match.group(1))
        info["process"] = process_info
        return info

    def _active_session_count() -> int | None:
        stm = short_term_memory
        if stm is None and ctx is not None:
            stm = getattr(ctx, "short_term", None)
        if stm is None:
            return None

        try:
            if hasattr(stm, "_store") and isinstance(stm._store, dict):  # type: ignore[attr-defined]
                return len(stm._store)  # type: ignore[attr-defined]
            if hasattr(stm, "_messages") and isinstance(stm._messages, dict):  # type: ignore[attr-defined]
                return len(stm._messages)  # type: ignore[attr-defined]
        except Exception:
            return None
        return None

    def _restart_notice_payload() -> dict[str, Any]:
        return {
            "supported": True,
            "title": "在线重启说明",
            "summary": (
                "这个按钮只会重启当前 Bot 进程，让配置与运行态重新收敛；"
                "它不会重建 Docker 镜像，也不会把新的 Python 代码装进容器。"
            ),
            "window_hint": "改配置时可直接在线重启；改代码、依赖或 Dockerfile 时，请先重建 bot 镜像。",
            "impact": [
                "QQ 连接会短暂中断，这段时间内群消息与事件不会被当前进程处理。",
                "主动插话、定时任务、黑话抽取和后台扫描会在进程恢复后继续运行。",
            ],
            "works_for": [
                "修改 config/config.toml、config/config.json 或 config/.env 这类运行参数后，需要让新配置重新加载。",
                "切换 Provider、任务 profile、插件启停、协议连接等运行态改动后，需要让进程重新收敛。",
            ],
            "needs_rebuild": [
                "修改 Python 代码、插件实现、Provider 逻辑、依赖版本或 Dockerfile 后，在线重启不会更新容器内代码。",
                "这类改动请执行 docker compose build bot，然后再执行 docker compose up -d bot。",
            ],
            "checklist": [
                "重启前先确认最近备份或配置快照可用。",
                "若不是 Docker with restart 策略环境，请先确认手工启动方式。",
                "重启后回到系统页确认 Bot、NapCat 与服务健康是否恢复正常。",
            ],
        }

    def _empty_runtime_error_payload() -> dict[str, Any]:
        return {
            "summary": {
                "total": 0,
                "warnings": 0,
                "errors": 0,
                "critical": 0,
                "unique": 0,
                "last_error": {},
                "last_warning": {},
                "top_issue": {},
            },
            "groups": [],
            "events": [],
            "max_events": 0,
            "max_groups": 0,
        }

    @router.get("/health")
    async def health():
        connected_bots = _connected_bot_count()
        napcat_connected = connected_bots > 0
        return {
            "bot": "running",
            "napcat": "connected" if napcat_connected else "disconnected",
            "uptime_seconds": time.time() - _process_start_time,
            "connected_bots": connected_bots,
        }

    @router.get("/services/health")
    async def services_health():
        from services.health import collect_service_health

        return await collect_service_health(ctx=ctx, config=config, bot=bot)

    @router.get("/system/errors")
    async def system_errors(event_limit: int = 20, group_limit: int = 12):
        store = getattr(ctx, "runtime_errors", None) if ctx is not None else None
        if store is None or not hasattr(store, "as_payload"):
            return _empty_runtime_error_payload()
        return store.as_payload(event_limit=event_limit, group_limit=group_limit)

    @router.get("/version")
    async def version():
        try:
            from services.version import (
                VERSION,
                fetch_latest_release,
                parse_semver,
                version_summary,
            )
            latest = await fetch_latest_release()
            latest_tag = ""
            latest_name = ""
            latest_url = ""
            has_update = False
            if latest:
                latest_tag = str(latest.get("tag_name") or latest.get("name") or "")
                latest_name = str(latest.get("name") or latest_tag)
                latest_url = str(latest.get("html_url") or "")
                if latest_tag:
                    try:
                        has_update = parse_semver(latest_tag) > parse_semver(VERSION)
                    except Exception:
                        has_update = latest_tag.lstrip("v") != VERSION.lstrip("v")
            return {
                "version": VERSION,
                "summary": version_summary(),
                "latest_release": latest,
                "latest_tag": latest_tag,
                "latest_name": latest_name,
                "latest_url": latest_url,
                "has_update": has_update,
            }
        except Exception:
            return {"version": "unknown"}

    @router.get("/system")
    async def system():
        info: dict[str, Any] = {
            "cpu_percent": None,
            "memory": None,
            "disk": None,
            "process": None,
        }

        # System resources via psutil (preferred)
        used_psutil = False
        try:
            import psutil

            proc = psutil.Process(os.getpid())
            info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            info["memory"] = {
                "total_gb": round(mem.total / (1024 ** 3), 1),
                "used_gb": round(mem.used / (1024 ** 3), 1),
                "percent": mem.percent,
            }
            disk = psutil.disk_usage("/")
            info["disk"] = {
                "total_gb": round(disk.total / (1024 ** 3), 1),
                "used_gb": round(disk.used / (1024 ** 3), 1),
                "percent": disk.percent,
            }
            info["process"] = {
                "pid": proc.pid,
                "memory_mb": round(proc.memory_info().rss / (1024 ** 2), 1),
                "threads": proc.num_threads(),
            }
            used_psutil = True
        except Exception:
            used_psutil = False

        if not used_psutil:
            info.update(_collect_fallback_system_info())

        info["active_sessions"] = _active_session_count()
        info["restart_notice"] = _restart_notice_payload()

        return info

    @router.get("/humanizer")
    async def humanizer_info():
        if humanizer is None:
            return {"enabled": False}

        from services.humanizer import get_humanizer
        h = get_humanizer()
        if h is None:
            return {"enabled": False}

        return {
            "enabled": getattr(h, "enabled", True),
            "min_delay": getattr(h, "min_delay", 0.5),
            "max_delay": getattr(h, "max_delay", 3.0),
            "char_delay": getattr(h, "char_delay", 0.02),
        }

    @router.get("/talk-schedule")
    async def talk_schedule():
        try:
            from services.talk_schedule import get_time_multiplier
            return {"time_multiplier": get_time_multiplier()}
        except Exception:
            return {"time_multiplier": 1.0}

    @router.post("/backup")
    async def backup(profile: str = "daily"):
        from pathlib import Path

        from services.storage.backup import BackupLockedError, BackupService

        svc = BackupService(storage_dir=Path("storage"), repo_root=Path.cwd())
        try:
            manifest = svc.create(profile=profile, host_mode=False)
        except BackupLockedError:
            return {"ok": False, "error": "另一个备份正在进行中"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if manifest.get("status") == "no_space":
            return {"ok": False, "error": "磁盘空间不足"}

        summary = manifest.get("summary", {})
        return {
            "ok": summary.get("trusted", False),
            "backup_id": manifest.get("backup_id"),
            "summary": summary,
            "complete": manifest.get("complete", True),
            "skipped_host_only": manifest.get("skipped_host_only", []),
            "message": f"备份已创建: {manifest.get('backup_id')}",
        }

    @router.get("/backup/list")
    async def backup_list(profile: str = "daily", all_profiles: bool = False):
        from pathlib import Path

        from services.storage.backup import BackupService

        svc = BackupService(storage_dir=Path("storage"), repo_root=Path.cwd())
        if all_profiles:
            all_backups = []
            for p in ("daily", "pre-change", "migration"):
                all_backups.extend(svc.list_backups(profile=p))
            all_backups.sort(
                key=lambda b: b.get("created_at", ""), reverse=True
            )
            return {"backups": all_backups}
        return {"backups": svc.list_backups(profile=profile)}

    @router.get("/backup/settings")
    async def get_backup_settings():
        return {
            "enabled": config.backup.enabled,
            "daily_time": config.backup.daily_time,
            "keep_days": config.backup.keep_days,
            "default_profile": config.backup.default_profile,
            "pre_change_enabled": config.backup.pre_change_enabled,
            "pre_change_keep_count": config.backup.pre_change_keep_count,
        }

    @router.post("/backup/settings")
    async def update_backup_settings(payload: dict):
        import json
        from pathlib import Path

        from kernel.config import BackupConfig

        try:
            new_config = BackupConfig.model_validate(payload)
        except Exception as e:
            return {"ok": False, "error": f"校验失败: {e}"}

        config_path = Path("config/config.json")
        if not config_path.exists():
            return {"ok": False, "error": "config.json 不存在"}

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        new_values = new_config.model_dump()
        raw["backup"] = new_values

        serialized = json.dumps(raw, ensure_ascii=False, indent=2)
        tmp = config_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, config_path)

        reload_ok = True
        reload_error = None
        try:
            from bot import get_backup_scheduler
            sched = get_backup_scheduler()
            if sched is not None:
                sched.reload(
                    daily_time=new_config.daily_time,
                    keep_days=new_config.keep_days,
                    default_profile=new_config.default_profile,
                    enabled=new_config.enabled,
                )
        except Exception as e:
            reload_ok = False
            reload_error = str(e)

        return {
            "ok": True,
            "settings": new_values,
            "reload_ok": reload_ok,
            "reload_error": reload_error,
        }

    @router.post("/system/restart")
    async def restart():
        if not _schedule_restart():
            return {
                "ok": False,
                "error": "当前运行环境不支持在线重启，请手动重启进程。",
            }
        return {
            "ok": True,
            "message": "在线重启请求已发送。若刚改了代码、依赖或 Dockerfile，请改用重建 bot 镜像。",
        }

    return router


_process_start_time = time.time()
