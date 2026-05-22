"""Service-level health aggregation for Admin runtime diagnostics."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


def _service(
    service_id: str,
    label: str,
    status: str,
    detail: str,
    *,
    metric: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": service_id,
        "label": label,
        "status": status,
        "detail": detail,
        "metric": metric,
        "meta": meta or {},
    }


def _status_priority(status: str) -> int:
    return {
        "ok": 0,
        "unknown": 1,
        "warning": 2,
        "error": 3,
    }.get(status, 1)


def _overall_status(services: list[dict[str, Any]]) -> str:
    if not services:
        return "unknown"
    worst = max((str(item.get("status", "unknown")) for item in services), key=_status_priority)
    if worst == "error":
        return "degraded"
    return worst


def _alert_priority(severity: str) -> int:
    return {
        "error": 0,
        "warning": 1,
        "info": 2,
    }.get(severity, 3)


def _runtime_bots(ctx: Any = None, bot: Any = None) -> list[Any]:
    bots: list[Any] = []
    try:
        import nonebot

        runtime_bots = nonebot.get_bots()
        if isinstance(runtime_bots, dict):
            bots.extend(runtime_bots.values())
    except Exception:
        pass

    for candidate in (bot, getattr(ctx, "bot", None) if ctx is not None else None):
        if candidate is not None and candidate not in bots:
            bots.append(candidate)
    return bots


def _resolve_path(raw_path: Any, fallback: Path) -> Path:
    if raw_path:
        return Path(str(raw_path))
    return fallback


def _sqlite_probe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": path.name,
            "path": str(path),
            "exists": False,
            "status": "warning",
            "detail": "数据库文件尚不存在",
            "size_bytes": 0,
        }

    try:
        with sqlite3.connect(str(path), timeout=1.0) as conn:
            quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        status = "ok" if quick_check.lower() == "ok" else "error"
        detail = f"quick_check={quick_check}, journal={journal_mode}"
    except Exception as exc:
        status = "error"
        detail = str(exc)[:180]

    return {
        "name": path.name,
        "path": str(path),
        "exists": True,
        "status": status,
        "detail": detail,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


async def _count_from_store(store: Any, sql: str) -> int | None:
    db = getattr(store, "_db", None)
    if db is None:
        return None
    try:
        cursor = await db.execute(sql)
        row = await cursor.fetchone()
        if row is None:
            return None
        value = next(iter(row.values())) if isinstance(row, dict) else row[0]
        return int(value or 0)
    except Exception:
        return None


async def collect_service_health(
    *,
    ctx: Any = None,
    config: Any = None,
    bot: Any = None,
) -> dict[str, Any]:
    """Collect a lightweight service health snapshot for Admin System page."""
    storage_dir = Path(getattr(ctx, "storage_dir", Path("storage")) if ctx is not None else Path("storage"))
    services = [
        _check_llm(ctx=ctx, config=config),
        _check_plugin_bus(ctx=ctx),
        _check_runtime_errors(ctx=ctx),
        _check_napcat(ctx=ctx, config=config, bot=bot),
        _check_protocol_trace(ctx=ctx),
        _check_sqlite(ctx=ctx, storage_dir=storage_dir),
        _check_backup_freshness(storage_dir=storage_dir),
        _check_backup_disk_usage(storage_dir=storage_dir),
        await _check_memory(ctx=ctx),
        await _check_slang(ctx=ctx, storage_dir=storage_dir),
    ]
    summary = {
        "ok": sum(1 for item in services if item["status"] == "ok"),
        "warning": sum(1 for item in services if item["status"] == "warning"),
        "error": sum(1 for item in services if item["status"] == "error"),
        "unknown": sum(1 for item in services if item["status"] == "unknown"),
    }
    alerts, alert_policy = _build_health_alerts(services)
    maintenance_window = _build_maintenance_window(services, summary, alerts)
    return {
        "checked_at": time.time(),
        "overall_status": _overall_status(services),
        "summary": summary,
        "alerts": alerts,
        "policy": alert_policy,
        "maintenance_window": maintenance_window,
        "services": services,
    }


def _service_action(service_id: str) -> str:
    actions = {
        "llm": "检查默认 profile、API Key 与 Provider 连通性，必要时在维护窗口内切换或重启。",
        "plugin_bus": "进入插件页查看慢调用或异常插件，批量改动后建议做一次硬重启。",
        "runtime_errors": "先看关键错误摘要，再进入日志页定位高频问题。",
        "napcat": "确认 NapCat 登录态、连接状态与最近错误，再决定是否重启协议层。",
        "protocol_trace": "检查 OneBot 请求失败和 pending 堆积，确认协议端能力是否正常。",
        "sqlite": "先创建备份，再检查数据库文件、磁盘空间与 quick_check 结果。",
        "memory": "确认记忆库、短期会话和语义回退是否符合预期。",
        "slang": "检查黑话 store 初始化与抽取链路，必要时在低峰期恢复。",
    }
    return actions.get(service_id, "建议在维护窗口内核对该服务的配置与运行日志。")


def _alert_thresholds() -> dict[str, str]:
    return {
        "llm": "仅当默认 profile 缺失关键字段或无法解析时升级为顶部告警",
        "plugin_bus": "errors >= 1，或 throttled_plugins >= 1，或 slow_calls >= 3，或 permission_denials >= 5",
        "runtime_errors": "critical >= 1，或 errors >= 1，或 warnings >= 3",
        "napcat": "未连接即升级为顶部告警",
        "protocol_trace": "failed >= 3，或 pending >= 5",
        "sqlite": "任意数据库 quick_check 失败，或缺失库 >= 2",
        "memory": "semantic errors >= 2 且 queries >= 5，或记忆主链路本身异常",
        "slang": "仅当查询失败或显式 error 时升级为顶部告警",
    }


def _build_alert_entry(
    service: dict[str, Any],
    *,
    severity: str,
    maintenance_window: bool | None = None,
) -> dict[str, Any]:
    service_id = str(service.get("id", "service"))
    label = str(service.get("label", service_id) or service_id)
    return {
        "id": service_id,
        "source": service_id,
        "severity": severity,
        "title": f"{label}{'异常' if severity == 'error' else '需留意'}",
        "detail": str(service.get("detail", "") or ""),
        "metric": str(service.get("metric", "") or ""),
        "action": _service_action(service_id),
        "maintenance_window": (
            maintenance_window
            if maintenance_window is not None
            else severity == "error" or service_id in {"napcat", "sqlite", "plugin_bus", "runtime_errors", "llm"}
        ),
    }


def _decide_alert(service: dict[str, Any]) -> dict[str, Any] | None:
    status = str(service.get("status", "unknown"))
    service_id = str(service.get("id", "service"))
    meta = service.get("meta") if isinstance(service.get("meta"), dict) else {}

    if status == "error":
        return _build_alert_entry(service, severity="error")

    if status != "warning":
        return None

    if service_id == "llm":
        return None

    if service_id == "plugin_bus":
        errors = int(meta.get("errors", 0) or 0)
        throttled_plugins = int(meta.get("throttled_plugins", 0) or 0)
        slow_calls = int(meta.get("slow_calls", 0) or 0)
        permission_denials = int(meta.get("permission_denials", 0) or 0)
        if errors >= 1 or throttled_plugins >= 1 or slow_calls >= 3 or permission_denials >= 5:
            return _build_alert_entry(service, severity="warning")
        return None

    if service_id == "runtime_errors":
        warnings = int(meta.get("warnings", 0) or 0)
        errors = int(meta.get("errors", 0) or 0)
        critical = int(meta.get("critical", 0) or 0)
        if critical >= 1:
            return _build_alert_entry(service, severity="error", maintenance_window=True)
        if errors >= 1 or warnings >= 3:
            return _build_alert_entry(service, severity="warning", maintenance_window=True)
        return None

    if service_id == "napcat":
        return _build_alert_entry(service, severity="error", maintenance_window=True)

    if service_id == "protocol_trace":
        failed = int(meta.get("failed", 0) or 0)
        pending = int(meta.get("pending", 0) or 0)
        if failed >= 3 or pending >= 5:
            return _build_alert_entry(service, severity="warning")
        return None

    if service_id == "sqlite":
        missing_count = int(meta.get("missing_count", 0) or 0)
        error_count = int(meta.get("error_count", 0) or 0)
        if error_count >= 1:
            return _build_alert_entry(service, severity="error", maintenance_window=True)
        if missing_count >= 2:
            return _build_alert_entry(service, severity="warning", maintenance_window=True)
        return None

    if service_id == "memory":
        semantic = meta.get("semantic") if isinstance(meta.get("semantic"), dict) else {}
        semantic_errors = int(semantic.get("errors", 0) or 0)
        semantic_queries = int(semantic.get("queries", 0) or 0)
        if semantic_errors >= 2 and semantic_queries >= 5:
            return _build_alert_entry(service, severity="warning")
        return None

    if service_id == "slang":
        return None

    return _build_alert_entry(service, severity="warning")


def _build_health_alerts(services: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    suppressed_count = 0
    for service in services:
        status = str(service.get("status", "unknown"))
        if status not in {"warning", "error"}:
            continue
        alert = _decide_alert(service)
        if alert is not None:
            alerts.append(alert)
        else:
            suppressed_count += 1
    alerts.sort(key=lambda item: (_alert_priority(str(item.get("severity", "info"))), str(item.get("title", ""))))
    visible_alerts = alerts[:8]
    overflow_count = max(0, len(alerts) - len(visible_alerts))
    return visible_alerts, {
        "mode": "thresholded",
        "summary": "顶部告警只展示达到阈值的异常；轻量 warning 仍保留在服务级健康卡中。",
        "suppressed_count": suppressed_count,
        "overflow_count": overflow_count,
        "alert_count": len(visible_alerts),
        "thresholds": _alert_thresholds(),
    }


def _build_maintenance_window(
    services: list[dict[str, Any]],
    summary: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    service_warning_count = int(summary.get("warning", 0) or 0)
    service_error_count = int(summary.get("error", 0) or 0)
    alert_error_count = sum(1 for item in alerts if str(item.get("severity")) == "error")
    alert_warning_count = sum(1 for item in alerts if str(item.get("severity")) == "warning")
    recommended = alert_error_count > 0 or alert_warning_count >= 2
    severity = "error" if alert_error_count > 0 else "warning" if alert_warning_count > 0 else "info"

    if alert_error_count > 0:
        title = "建议立即安排维护窗口"
        summary_text = f"当前存在 {alert_error_count} 项高优先级异常，建议在群聊低峰期集中处理，并在完成后复查系统页。"
    elif alert_warning_count > 0:
        title = "建议安排低峰维护"
        summary_text = f"当前存在 {alert_warning_count} 项达到阈值的留意项，虽然不一定阻断运行，但更适合放到维护窗口内统一处理。"
    else:
        title = "当前运行面整体稳定"
        if service_warning_count > 0 or service_error_count > 0:
            summary_text = f"当前没有达到阈值的顶部告警，但仍有 {service_warning_count + service_error_count} 项轻量提醒保留在服务级健康卡中。"
        else:
            summary_text = "当前没有明显服务告警。常规配置调整仍建议先创建备份，再按需重启验证。"

    reasons = [str(item.get("title", "")) for item in alerts[:3] if item.get("title")]
    if not reasons and not recommended:
        reasons = [
            "系统资源、协议连接和关键服务当前没有达到阈值的阻塞信号。",
        ]

    service_ids = {str(item.get("id", "")) for item in services if item.get("status") in {"warning", "error"}}
    alert_ids = {str(item.get("source", "")) for item in alerts if item.get("source")}
    checklist = ["先创建备份，或确认最近的配置快照可以回滚。"]
    if {"napcat", "protocol_trace"} & service_ids:
        checklist.append("确认 NapCat / OneBot 连接与最近协议错误，避免在连接异常时继续叠加操作。")
    if {"llm", "plugin_bus", "memory", "slang"} & service_ids:
        checklist.append("处理 Provider、插件、记忆或黑话链路后，建议执行一次重启并回看系统页。")
    if {"runtime_errors", "sqlite"} & service_ids:
        checklist.append("先看关键错误与数据库状态，再决定是否要做更重的运维动作。")
    if len(checklist) == 1:
        checklist.append("若涉及配置、插件或 Provider 映射变更，优先选择群聊低峰期执行重启验证。")

    return {
        "recommended": recommended,
        "severity": severity,
        "title": title,
        "summary": summary_text,
        "window_hint": "优先选择群聊低峰、主动任务较少、且管理员能及时回看系统页的时段。",
        "reasons": checklist[:1] if not recommended and not alerts else reasons,
        "checklist": checklist[:4],
        "restart_recommended": recommended or bool({"llm", "plugin_bus", "memory", "slang"} & alert_ids),
    }


def _check_llm(*, ctx: Any = None, config: Any = None) -> dict[str, Any]:
    llm = getattr(config, "llm", None)
    if llm is None:
        return _service("llm", "LLM", "error", "LLM 配置不可用")

    default_profile = str(getattr(llm, "default_profile", "main") or "main")
    profile = llm.resolve_profile(default_profile) if hasattr(llm, "resolve_profile") else None
    if profile is None:
        return _service("llm", "LLM", "error", f"profile {default_profile} 无法解析")

    model = str(getattr(profile, "model", "") or "")
    base_url = str(getattr(profile, "base_url", "") or "")
    api_key = str(getattr(profile, "api_key", "") or "")
    client_ready = getattr(ctx, "llm_client", None) is not None if ctx is not None else False
    if not model or not base_url:
        status = "error"
        detail = "默认 LLM profile 缺少 model 或 base_url"
    elif not api_key:
        status = "warning"
        detail = "默认 LLM profile 未配置 api_key，若使用本地免密服务可忽略"
    else:
        status = "ok"
        detail = "默认 profile 配置完整"

    return _service(
        "llm",
        "LLM",
        status,
        detail,
        metric=model or default_profile,
        meta={
            "default_profile": default_profile,
            "api_format": getattr(profile, "api_format", "anthropic"),
            "client_ready": client_ready,
        },
    )


def _check_plugin_bus(*, ctx: Any = None) -> dict[str, Any]:
    bus = getattr(ctx, "bus", None) if ctx is not None else None
    if bus is None:
        return _service("plugin_bus", "PluginBus", "error", "PluginBus 不可用")

    plugins = list(getattr(bus, "plugins", []) or [])
    health = bus.plugin_health() if hasattr(bus, "plugin_health") else []
    enabled = sum(1 for plugin in plugins if getattr(plugin, "enabled", True))
    errors = sum(int(item.get("errors", 0) or 0) for item in health)
    slow_calls = sum(int(item.get("slow_calls", 0) or 0) for item in health)
    permission_denials = sum(int(item.get("permission_denials", 0) or 0) for item in health)
    throttled_plugins = sum(1 for item in health if str(item.get("state", "")) == "throttled")
    suppressed_calls = sum(int(item.get("suppressed_calls", 0) or 0) for item in health)

    if not plugins:
        status = "warning"
        detail = "当前没有注册插件"
    elif throttled_plugins:
        status = "warning"
        detail = f"{throttled_plugins} 个插件进入软隔离，已临时跳过高频 Hook"
    elif errors:
        status = "warning"
        detail = f"{errors} 次插件异常，建议查看插件页详情"
    elif slow_calls:
        status = "warning"
        detail = f"{slow_calls} 次 Hook 超出预算"
    else:
        status = "ok"
        detail = "插件总线运行正常"

    return _service(
        "plugin_bus",
        "PluginBus",
        status,
        detail,
        metric=f"{enabled}/{len(plugins)} 启用",
        meta={
            "plugin_count": len(plugins),
            "enabled_count": enabled,
            "errors": errors,
            "throttled_plugins": throttled_plugins,
            "slow_calls": slow_calls,
            "permission_denials": permission_denials,
            "suppressed_calls": suppressed_calls,
        },
    )


def _check_runtime_errors(*, ctx: Any = None) -> dict[str, Any]:
    store = getattr(ctx, "runtime_errors", None) if ctx is not None else None
    if store is None or not hasattr(store, "summary"):
        return _service("runtime_errors", "Runtime Errors", "warning", "关键错误聚合尚未启用")

    summary = store.summary()
    warnings = int(summary.get("warnings", 0) or 0)
    errors = int(summary.get("errors", 0) or 0)
    critical = int(summary.get("critical", 0) or 0)
    unique = int(summary.get("unique", 0) or 0)
    top_issue = summary.get("top_issue") if isinstance(summary.get("top_issue"), dict) else {}
    if critical:
        status = "error"
        detail = f"最近出现 {critical} 条 CRITICAL：{top_issue.get('message') or '--'}"
    elif errors:
        status = "warning"
        detail = f"最近记录 {errors} 条 ERROR/CRITICAL，建议查看关键错误面板"
    elif warnings:
        status = "warning"
        detail = f"最近记录 {warnings} 条 WARNING，建议留意运行日志"
    else:
        status = "ok"
        detail = "最近没有关键 warning/error"

    return _service(
        "runtime_errors",
        "Runtime Errors",
        status,
        detail,
        metric=f"{errors} error / {warnings} warning",
        meta={
            "warnings": warnings,
            "errors": errors,
            "critical": critical,
            "unique": unique,
            "top_issue": top_issue,
        },
    )


def _check_napcat(*, ctx: Any = None, config: Any = None, bot: Any = None) -> dict[str, Any]:
    bots = _runtime_bots(ctx=ctx, bot=bot)
    napcat = getattr(config, "napcat", None)
    api_url = str(getattr(napcat, "api_url", "") or "")
    self_ids = [str(getattr(item, "self_id", "") or "") for item in bots]
    connection = None
    history = getattr(ctx, "protocol_connections", None) if ctx is not None else None
    if history is not None and hasattr(history, "record_snapshot"):
        connection = history.record_snapshot(
            connected_bots=len(bots),
            self_ids=self_ids,
            source="services_health",
        )
    status = "ok" if bots else "error"
    detail = f"{len(bots)} 个 Bot 已连接" if bots else "NoneBot 当前没有已连接 Bot"
    if connection and connection.get("last_error") and not bots:
        detail = f"{detail}；最近错误：{connection['last_error']}"
    return _service(
        "napcat",
        "NapCat",
        status,
        detail,
        metric=f"{len(bots)} connected",
        meta={"api_url": api_url, "connection": connection or {}},
    )


def _check_protocol_trace(*, ctx: Any = None) -> dict[str, Any]:
    trace = getattr(ctx, "protocol_trace", None) if ctx is not None else None
    if trace is None or not hasattr(trace, "summary"):
        return _service("protocol_trace", "Protocol Trace", "warning", "协议请求追踪尚未启用")
    summary = trace.summary()
    failed = int(summary.get("failed", 0) or 0)
    pending = int(summary.get("pending", 0) or 0)
    wrapped = int(summary.get("wrapped_bots", 0) or 0)
    if failed:
        status = "warning"
        detail = f"{failed} 个 OneBot 请求失败，最近错误：{summary.get('last_error') or '--'}"
    elif wrapped <= 0:
        status = "warning"
        detail = "等待 Bot 连接后安装请求追踪包装器"
    else:
        status = "ok"
        detail = "OneBot 请求追踪已启用"
    return _service(
        "protocol_trace",
        "Protocol Trace",
        status,
        detail,
        metric=f"{summary.get('ok', 0)} ok / {pending} pending",
        meta=summary,
    )


def _check_sqlite(*, ctx: Any = None, storage_dir: Path) -> dict[str, Any]:
    from services.storage.backup import BACKUP_REGISTRY

    repo_root = storage_dir.parent
    paths = [
        repo_root / item.path
        for item in BACKUP_REGISTRY
        if item.item_type == "sqlite"
    ]
    probes = [_sqlite_probe(path) for path in paths]
    error_count = sum(1 for item in probes if item["status"] == "error")
    missing_count = sum(1 for item in probes if not item["exists"])
    ok_count = sum(1 for item in probes if item["status"] == "ok")
    if error_count:
        status = "error"
        detail = f"{error_count} 个 SQLite 数据库检查失败"
    elif missing_count:
        status = "warning"
        detail = f"{missing_count} 个数据库文件尚未创建"
    else:
        status = "ok"
        detail = f"全部 {ok_count} 个 SQLite 数据库 quick_check 通过"
    return _service(
        "sqlite",
        "SQLite",
        status,
        detail,
        metric=f"{ok_count}/{len(probes)} ok",
        meta={
            "databases": probes,
            "missing_count": missing_count,
            "error_count": error_count,
            "ok_count": ok_count,
        },
    )


def _check_backup_freshness(*, storage_dir: Path) -> dict[str, Any]:
    from datetime import UTC, datetime

    from services.storage.backup import BackupService

    svc = BackupService(storage_dir=storage_dir, repo_root=storage_dir.parent)
    latest = svc.latest_status()
    if latest is None:
        return _service("backup", "Backup", "warning", "尚无备份记录")

    trusted = latest.get("summary", {}).get("trusted", False)
    if not trusted:
        return _service("backup", "Backup", "error", "最近备份不可信（trusted=false）",
                        meta={"backup_id": latest.get("backup_id")})

    try:
        created = datetime.fromisoformat(latest["created_at"])
        age_hours = (datetime.now(UTC) - created.astimezone(UTC)).total_seconds() / 3600
    except Exception:
        age_hours = 999

    if age_hours > 48:
        return _service("backup", "Backup", "warning",
                        f"最近备份已超 {age_hours:.0f} 小时",
                        meta={"backup_id": latest.get("backup_id"), "age_hours": round(age_hours, 1)})

    return _service("backup", "Backup", "ok",
                    f"最近备份 {latest.get('backup_id')} 可信",
                    meta={"backup_id": latest.get("backup_id"), "age_hours": round(age_hours, 1)})


def _check_backup_disk_usage(*, storage_dir: Path) -> dict[str, Any]:
    import os

    backup_root = storage_dir / "backups"
    if not backup_root.exists():
        return _service("backup_disk", "Backup Disk", "ok", "尚未创建备份目录")

    st = os.statvfs(backup_root)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used_pct = (total - free) / total * 100 if total > 0 else 0

    used_by_backup = sum(
        f.stat().st_size for f in backup_root.rglob("*") if f.is_file()
    )

    if used_pct >= 90:
        status = "error"
        detail = f"磁盘占用 {used_pct:.0f}%，备份将很快失败"
    elif used_pct >= 80:
        status = "warning"
        detail = f"磁盘占用 {used_pct:.0f}%，建议清理"
    else:
        status = "ok"
        detail = f"磁盘占用 {used_pct:.0f}%，备份目录 {used_by_backup // 1024 // 1024} MB"

    return _service("backup_disk", "Backup Disk", status, detail, meta={
        "used_pct": round(used_pct, 1),
        "free_bytes": free,
        "backup_bytes": used_by_backup,
    })


async def _check_memory(*, ctx: Any = None) -> dict[str, Any]:
    if ctx is None:
        return _service("memory", "Memory", "unknown", "运行上下文不可用")

    card_store = getattr(ctx, "card_store", None)
    msg_log = getattr(ctx, "msg_log", None)
    if card_store is None and msg_log is None:
        return _service("memory", "Memory", "warning", "记忆卡片与消息日志服务均不可用")

    card_count = await _count_from_store(card_store, "SELECT COUNT(*) FROM memory_cards") if card_store else None
    message_count = await _count_from_store(msg_log, "SELECT COUNT(*) FROM group_messages") if msg_log else None
    active_sessions = None
    short_term = getattr(ctx, "short_term", None)
    if short_term is not None:
        store = getattr(short_term, "_store", None)
        if isinstance(store, dict):
            active_sessions = len(store)
    retrieval = getattr(ctx, "retrieval", None)
    semantic = retrieval.semantic_status() if retrieval is not None and hasattr(retrieval, "semantic_status") else {}
    semantic_enabled = bool(semantic.get("enabled"))
    semantic_backend = str(semantic.get("active_backend") or semantic.get("requested_backend") or "ngram")
    semantic_queries = int(semantic.get("queries", 0) or 0)
    semantic_hits = int(semantic.get("hits", 0) or 0)
    semantic_fallbacks = int(semantic.get("fallbacks", 0) or 0)
    semantic_errors = int(semantic.get("errors", 0) or 0)
    semantic_hit_rate = round((semantic_hits / semantic_queries), 3) if semantic_queries > 0 else 0.0

    if card_store is None or msg_log is None:
        status = "warning"
        detail = "部分记忆服务未初始化"
    elif card_count is None or message_count is None:
        status = "warning"
        detail = "记忆服务已注册，但统计查询不可用"
    elif semantic_enabled and semantic_errors > 0:
        status = "warning"
        detail = f"记忆服务可查询；语义检索已回退到 {semantic_backend}"
    else:
        status = "ok"
        detail = "记忆卡片与消息日志可查询"
        if semantic_enabled:
            if semantic_queries > 0:
                detail = f"{detail}；轻量语义检索已启用，最近 {semantic_hits}/{semantic_queries} 次命中"
            else:
                detail = f"{detail}；轻量语义检索已启用"

    return _service(
        "memory",
        "Memory",
        status,
        detail,
        metric=(
            f"{card_count if card_count is not None else '--'} cards"
            + (f" / semantic {semantic_backend}" if semantic_enabled else "")
        ),
        meta={
            "card_count": card_count,
            "message_count": message_count,
            "active_sessions": active_sessions,
            "semantic": {
                **semantic,
                "queries": semantic_queries,
                "hits": semantic_hits,
                "fallbacks": semantic_fallbacks,
                "errors": semantic_errors,
                "hit_rate": semantic_hit_rate,
                "active_backend": semantic_backend,
            },
        },
    )


async def _check_slang(*, ctx: Any = None, storage_dir: Path) -> dict[str, Any]:
    store = getattr(ctx, "slang_store", None) if ctx is not None else None
    if store is None:
        db_path = storage_dir / "slang.db"
        if db_path.exists():
            return _service("slang", "Slang", "warning", "黑话数据库存在，但运行时 store 未挂载")
        return _service("slang", "Slang", "warning", "黑话服务尚未初始化")

    if not getattr(store, "initialized", False):
        return _service("slang", "Slang", "warning", "黑话 store 尚未初始化")

    try:
        summary = await store.summary()
        candidate = int(summary.get("candidate_count", 0) or 0)
        approved = int(summary.get("approved_count", 0) or 0)
        observing = int(summary.get("observing_count", 0) or 0)
        return _service(
            "slang",
            "Slang",
            "ok",
            "黑话 store 可查询",
            metric=f"{approved} approved",
            meta={
                "candidate_count": candidate,
                "approved_count": approved,
                "observing_count": observing,
            },
        )
    except Exception as exc:
        return _service("slang", "Slang", "error", str(exc)[:180])
