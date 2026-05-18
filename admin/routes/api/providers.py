"""JSON API: provider profiles — LLM connection overview."""

from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from typing import Any, Literal, get_args

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from kernel.config import BotConfig, LLMCapability
from services.llm.llm_request import all_llm_tasks

# Single source of truth: services/llm/llm_request.py LLMTask Literal.
# Tests assert these stay in sync.
_LLM_TASKS = all_llm_tasks()
_API_FORMATS = ("anthropic", "openai", "deepseek")
_PROFILE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_CAPABILITY_LABELS = {
    "chat": "聊天",
    "tools": "工具调用",
    "thinking": "深度思考",
    "vision": "视觉理解",
    "json": "结构化 JSON",
    "compact": "压缩任务",
}


class ProviderDefinitionPayload(BaseModel):
    name: str
    api_format: Literal["anthropic", "openai", "deepseek"] = "anthropic"
    base_url: str = ""
    api_key: str = ""
    api_key_mode: Literal["keep", "replace", "clear"] = "keep"
    model: str = ""
    max_tokens: int | None = None
    capabilities: list[LLMCapability] = Field(default_factory=lambda: ["chat"])


class ProviderDefinitionsSavePayload(BaseModel):
    profiles: list[ProviderDefinitionPayload]


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return f"{value[:1]}***{value[-1:]}"
    return f"{value[:3]}***{value[-2:]}"


def create_providers_router(
    *,
    config: Any = None,
    config_path: str = "config/config.json",
    llm_client: Any = None,
    provider_tester: Any = None,
) -> APIRouter:
    router = APIRouter()
    target_json_path = _normalize_json_path(config_path)

    def _llm_config() -> Any:
        return getattr(config, "llm", None)

    @router.get("/providers")
    async def providers():
        llm = _llm_config()
        if llm is None:
            return {
                "default_profile": "main",
                "profiles": [],
                "error": "LLM config not available",
            }

        return _providers_payload(llm, llm_client=llm_client)

    @router.post("/providers/selection")
    async def save_provider_selection(request: Request):
        llm = _llm_config()
        if llm is None:
            return {"ok": False, "error": "LLM config not available"}

        body = await request.json()
        profile_names = _selectable_profile_names(llm)
        default_profile = str(body.get("default_profile") or getattr(llm, "default_profile", "main") or "main")
        incoming_task_profiles = body.get("task_profiles", {})
        if not isinstance(incoming_task_profiles, dict):
            return {"ok": False, "error": "task_profiles 必须是对象"}

        selection = _normalized_task_selection(
            llm=llm,
            default_profile=default_profile,
            incoming_task_profiles=incoming_task_profiles,
        )
        invalid_profiles = sorted(
            {default_profile, *selection.values()} - profile_names
        )
        if invalid_profiles:
            return {
                "ok": False,
                "error": f"profile 不存在: {', '.join(invalid_profiles)}",
                "available_profiles": sorted(profile_names),
            }

        try:
            persisted_model = _persist_provider_selection(
                target_json_path=target_json_path,
                fallback_config=config,
                default_profile=default_profile,
                task_profiles=selection,
            )
        except Exception as exc:
            return {"ok": False, "error": f"保存 profile 选择失败: {exc}"}

        active_llm = _apply_persisted_llm(config, persisted_model)
        runtime_applied = _apply_runtime_profiles(active_llm, llm_client=llm_client)
        return {
            "ok": True,
            "path": str(target_json_path),
            "default_profile": active_llm.default_profile,
            "task_profiles": [
                _task_profile_payload(active_llm, task)
                for task in _LLM_TASKS
            ],
            "runtime_applied": runtime_applied,
            "persisted": True,
            "message": "Provider profile 已热切换并写入配置。",
            "config_default_profile": persisted_model.llm.default_profile,
        }

    @router.post("/providers/definitions")
    async def save_provider_definitions(payload: ProviderDefinitionsSavePayload):
        llm = _llm_config()
        if llm is None:
            return {"ok": False, "error": "LLM config not available"}

        try:
            persisted_model = _persist_provider_definitions(
                target_json_path=target_json_path,
                fallback_config=config,
                profiles=payload.profiles,
            )
        except Exception as exc:
            return {"ok": False, "error": f"保存 profile 定义失败: {exc}"}

        active_llm = _apply_persisted_llm(config, persisted_model)
        runtime_applied = _apply_runtime_profiles(active_llm, llm_client=llm_client)
        response = _providers_payload(active_llm, llm_client=llm_client)
        response.update({
            "ok": True,
            "path": str(target_json_path),
            "runtime_applied": runtime_applied,
            "persisted": True,
            "message": "Provider 定义已保存，任务映射与运行时已同步刷新。",
        })
        return response

    @router.get("/providers/cache-diagnostic/{task}")
    async def cache_diagnostic_history(task: str, limit: int = 20):
        """Return recent per-axis cache diagnostic snapshots for a single task.

        Each entry is `{snapshot, diff}` where ``snapshot`` is the per-block /
        per-tool / per-message hash bundle from `cache_diagnostic.py` and
        ``diff`` is the diff against the previous snapshot for the same task
        (None for the very first snapshot). Drives the system-page diagnostic
        panel that answers "最近 break 是哪段变了" — when cache hit % drops we
        can see which axis (system / tools / messages) and which block
        invalidated.
        """
        if llm_client is None or not hasattr(llm_client, "cache_diagnostic_history"):
            return {"task": task, "entries": []}
        try:
            history = llm_client.cache_diagnostic_history(task, limit=max(1, min(int(limit or 20), 100)))
        except Exception as exc:
            return {"task": task, "entries": [], "error": str(exc)[:200]}
        entries = []
        for snapshot, diff in history:
            entries.append({
                "snapshot": snapshot.to_dict(),
                "diff": diff.to_dict() if diff is not None else None,
            })
        return {"task": task, "entries": entries}

    @router.post("/providers/{name}/test")
    async def test_provider(name: str):
        llm = _llm_config()
        if llm is None:
            return {"ok": False, "profile": name, "error": "LLM config not available"}

        profile = llm.resolve_profile(name) if hasattr(llm, "resolve_profile") else None
        if profile is None:
            return {"ok": False, "profile": name, "error": f"profile {name} not found"}

        base_url = str(getattr(profile, "base_url", "") or "")
        model = str(getattr(profile, "model", "") or "")
        api_key = str(getattr(profile, "api_key", "") or "")
        api_format = str(getattr(profile, "api_format", "anthropic") or "anthropic")
        if not base_url or not model:
            return {"ok": False, "profile": name, "error": "profile missing base_url or model"}

        started = time.perf_counter()
        try:
            if provider_tester is not None:
                result = await provider_tester(profile)
            else:
                result = await _call_provider_probe(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    api_format=api_format,
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            text = str(result.get("text", "") if isinstance(result, dict) else result)
            return {
                "ok": True,
                "profile": name,
                "api_format": api_format,
                "model": model,
                "elapsed_ms": elapsed_ms,
                "text_preview": text[:120],
                "provider_kind": result.get("provider_kind", "") if isinstance(result, dict) else "",
                "provider_mode": result.get("provider_mode", "") if isinstance(result, dict) else "",
                "payload_sanitized": bool(result.get("payload_sanitized", False)) if isinstance(result, dict) else False,
                "reasoning_replay_tokens": int(result.get("reasoning_replay_tokens", 0) or 0) if isinstance(result, dict) else 0,
                "usage_summary": result.get("usage", {}) if isinstance(result, dict) else {},
            }
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "ok": False,
                "profile": name,
                "api_format": api_format,
                "model": model,
                "elapsed_ms": elapsed_ms,
                "error": str(exc)[:240],
            }

    return router


def _normalize_json_path(config_path: str) -> Path:
    configured = Path(config_path)
    if configured.suffix.lower() == ".json":
        return configured
    if configured.suffix.lower() == ".toml":
        return configured.with_suffix(".json")
    return configured.with_suffix(".json")


def _selectable_profile_names(llm: Any) -> set[str]:
    return set(getattr(llm, "profiles", {}).keys()) | {"main"}


def _sorted_profile_names(names: set[str] | list[str]) -> list[str]:
    return sorted({str(name or "").strip() for name in names if str(name or "").strip()}, key=lambda item: (item != "main", item))


def _providers_payload(llm: Any, *, llm_client: Any = None) -> dict[str, Any]:
    names = _sorted_profile_names(set(getattr(llm, "profiles", {}).keys()) | {"main"})
    rate_limits = _rate_limit_payload(llm_client)
    rate_limit_profiles = (rate_limits.get("profiles", {}) if isinstance(rate_limits, dict) else {}) or {}
    profiles: list[dict[str, Any]] = []
    for name in names:
        profile = llm.resolve_profile(name) if hasattr(llm, "resolve_profile") else None
        if profile is None:
            continue
        profiles.append({
            "name": name,
            "active": name == getattr(llm, "default_profile", "main"),
            "api_format": getattr(profile, "api_format", "anthropic"),
            "base_url": getattr(profile, "base_url", ""),
            "model": getattr(profile, "model", ""),
            "max_tokens": getattr(profile, "max_tokens", None),
            "capabilities": list(getattr(profile, "capabilities", []) or []),
            "api_key_mask": _mask_secret(getattr(profile, "api_key", "")),
            "api_key_present": bool(getattr(profile, "api_key", "")),
            "rate_limit": rate_limit_profiles.get(name, _empty_rate_limit(name)),
            "provider_kind": rate_limit_profiles.get(name, {}).get("provider_kind", ""),
            "provider_mode": rate_limit_profiles.get(name, {}).get("provider_mode", ""),
            "last_cache_hit_pct": rate_limit_profiles.get(name, {}).get("last_cache_hit_pct"),
            "last_cache_hit_pct_by_task": rate_limit_profiles.get(name, {}).get("last_cache_hit_pct_by_task", {}),
            "last_prompt_cache_hit_tokens": rate_limit_profiles.get(name, {}).get("last_prompt_cache_hit_tokens", 0),
            "last_prompt_cache_miss_tokens": rate_limit_profiles.get(name, {}).get("last_prompt_cache_miss_tokens", 0),
            "last_reasoning_replay_tokens": rate_limit_profiles.get(name, {}).get("last_reasoning_replay_tokens", 0),
            "last_payload_sanitized": rate_limit_profiles.get(name, {}).get("last_payload_sanitized", False),
            "last_usage": rate_limit_profiles.get(name, {}).get("last_usage", {}),
        })

    return {
        "default_profile": getattr(llm, "default_profile", "main"),
        "task_profiles": [
            _task_profile_payload(llm, task)
            for task in _LLM_TASKS
        ],
        "profiles": profiles,
        "rate_limits": rate_limits,
        "capability_options": [
            {
                "value": str(capability),
                "label": _CAPABILITY_LABELS.get(str(capability), str(capability)),
            }
            for capability in get_args(LLMCapability)
        ],
        "api_format_options": [
            {"value": api_format, "label": api_format}
            for api_format in _API_FORMATS
        ],
    }


def _normalized_task_selection(
    *,
    llm: Any,
    default_profile: str,
    incoming_task_profiles: dict[Any, Any],
) -> dict[str, str]:
    existing = getattr(llm, "task_profiles", {}) or {}
    selection: dict[str, str] = {}
    for task in _LLM_TASKS:
        current = existing.get(task)
        incoming = incoming_task_profiles.get(task)
        selected = str(incoming or current or default_profile or "main")
        selection[task] = selected
    selection["main"] = default_profile or "main"
    return selection


def _persist_provider_selection(
    *,
    target_json_path: Path,
    fallback_config: Any,
    default_profile: str,
    task_profiles: dict[str, str],
) -> BotConfig:
    data = _read_config_base(target_json_path, fallback_config)
    llm_data = data.get("llm")
    if not isinstance(llm_data, dict):
        llm_data = {}
    llm_data["default_profile"] = default_profile
    existing_task_profiles = llm_data.get("task_profiles")
    if not isinstance(existing_task_profiles, dict):
        existing_task_profiles = {}
    existing_task_profiles.update(task_profiles)
    existing_task_profiles["main"] = default_profile
    llm_data["task_profiles"] = existing_task_profiles
    data["llm"] = llm_data

    model = BotConfig.model_validate(data)
    values = model.model_dump(mode="python")
    target_json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_json_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target_json_path)
    return model


def _persist_provider_definitions(
    *,
    target_json_path: Path,
    fallback_config: Any,
    profiles: list[ProviderDefinitionPayload],
) -> BotConfig:
    normalized_profiles = _normalize_provider_definitions(profiles)
    data = _read_config_base(target_json_path, fallback_config)
    llm_data = data.get("llm")
    if not isinstance(llm_data, dict):
        llm_data = {}

    existing_profiles = llm_data.get("profiles")
    if not isinstance(existing_profiles, dict):
        existing_profiles = {}

    profiles_data: dict[str, dict[str, Any]] = {}
    for profile in normalized_profiles:
        api_key = _resolve_profile_api_key(
            profile=profile,
            llm_data=llm_data,
            existing_profiles=existing_profiles,
            fallback_config=fallback_config,
        )
        profiles_data[profile.name] = {
            "api_format": profile.api_format,
            "base_url": profile.base_url,
            "api_key": api_key,
            "model": profile.model,
            "max_tokens": profile.max_tokens,
            "capabilities": list(profile.capabilities or []),
        }

    main_profile = profiles_data["main"]
    llm_data["api_format"] = main_profile["api_format"]
    llm_data["base_url"] = main_profile["base_url"]
    llm_data["api_key"] = main_profile["api_key"]
    llm_data["model"] = main_profile["model"]
    llm_data["max_tokens"] = (
        main_profile["max_tokens"]
        if main_profile["max_tokens"] is not None
        else llm_data.get("max_tokens")
        or getattr(getattr(fallback_config, "llm", None), "max_tokens", 1024)
    )
    llm_data["profiles"] = profiles_data

    available_profiles = set(profiles_data.keys())
    previous_default = str(llm_data.get("default_profile") or getattr(getattr(fallback_config, "llm", None), "default_profile", "main") or "main")
    default_profile = previous_default if previous_default in available_profiles else "main"
    llm_data["default_profile"] = default_profile

    existing_task_profiles = llm_data.get("task_profiles")
    if not isinstance(existing_task_profiles, dict):
        existing_task_profiles = {}
    fallback_task_profiles = getattr(getattr(fallback_config, "llm", None), "task_profiles", {}) or {}
    normalized_task_profiles: dict[str, str] = {}
    for task in _LLM_TASKS:
        selected = str(
            existing_task_profiles.get(task)
            or fallback_task_profiles.get(task)
            or default_profile
            or "main"
        )
        normalized_task_profiles[task] = selected if selected in available_profiles else default_profile
    normalized_task_profiles["main"] = default_profile
    llm_data["task_profiles"] = normalized_task_profiles
    data["llm"] = llm_data

    model = BotConfig.model_validate(data)
    _write_model(target_json_path, model)
    return model


def _read_config_base(target_json_path: Path, fallback_config: Any) -> dict[str, Any]:
    if target_json_path.is_file():
        return _read_json(target_json_path)

    legacy_toml = target_json_path.with_suffix(".toml")
    if legacy_toml.is_file():
        with open(legacy_toml, "rb") as fh:
            payload = tomllib.load(fh)
        if isinstance(payload, dict):
            return payload

    if fallback_config is not None and hasattr(fallback_config, "model_dump"):
        return fallback_config.model_dump(mode="python")
    return {}


def _normalize_provider_definitions(profiles: list[ProviderDefinitionPayload]) -> list[ProviderDefinitionPayload]:
    if not profiles:
        raise ValueError("至少保留一个 profile")

    normalized: list[ProviderDefinitionPayload] = []
    seen: set[str] = set()
    for item in profiles:
        name = str(item.name or "").strip()
        if not name:
            raise ValueError("profile 名称不能为空")
        if any(ch not in _PROFILE_NAME_CHARS for ch in name):
            raise ValueError(f"profile 名称只能包含字母、数字、下划线或短横线: {name}")
        if name in seen:
            raise ValueError(f"profile 名称重复: {name}")
        if item.api_key_mode == "replace" and not str(item.api_key or "").strip():
            raise ValueError(f"profile {name} 选择了替换密钥，但未填写 api_key")
        normalized.append(item.model_copy(update={"name": name}))
        seen.add(name)

    if "main" not in seen:
        raise ValueError("必须保留 main profile，它会同步 legacy llm.* 字段")
    return normalized


def _resolve_profile_api_key(
    *,
    profile: ProviderDefinitionPayload,
    llm_data: dict[str, Any],
    existing_profiles: dict[str, Any],
    fallback_config: Any,
) -> str:
    mode = str(profile.api_key_mode or "keep")
    if mode == "replace":
        return str(profile.api_key or "").strip()
    if mode == "clear":
        return ""

    existing = existing_profiles.get(profile.name)
    if isinstance(existing, dict) and existing.get("api_key"):
        return str(existing.get("api_key") or "")
    if profile.name == "main" and llm_data.get("api_key"):
        return str(llm_data.get("api_key") or "")

    fallback_llm = getattr(fallback_config, "llm", None)
    if fallback_llm is not None and hasattr(fallback_llm, "resolve_profile"):
        resolved = fallback_llm.resolve_profile(profile.name)
        return str(getattr(resolved, "api_key", "") or "")
    return ""


def _apply_persisted_llm(config: Any, persisted_model: BotConfig) -> Any:
    llm = persisted_model.llm
    if config is not None:
        try:
            setattr(config, "llm", llm)
            candidate = getattr(config, "llm", None)
            if candidate is not None:
                llm = candidate
        except Exception:
            pass
    return llm


def _apply_runtime_profiles(llm: Any, *, llm_client: Any = None) -> bool:
    runtime_applied = False
    if llm_client is not None and hasattr(llm_client, "set_task_profiles"):
        task_profiles = {
            task: llm.resolve_task_profile(task)
            for task in _LLM_TASKS
        }
        task_profile_names = {
            task: llm.profile_name_for_task(task)
            for task in _LLM_TASKS
        }
        llm_client.set_task_profiles(task_profiles, task_profile_names)
        runtime_applied = True
    elif llm_client is not None and hasattr(llm_client, "set_task_profile_names"):
        task_profile_names = {
            task: llm.profile_name_for_task(task)
            for task in _LLM_TASKS
        }
        llm_client.set_task_profile_names(task_profile_names)
    return runtime_applied


def _write_model(target_json_path: Path, model: BotConfig) -> None:
    values = model.model_dump(mode="python")
    target_json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_json_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target_json_path)


def _read_json(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("配置 JSON 必须是对象")
    return payload


def _rate_limit_payload(llm_client: Any = None) -> dict[str, Any]:
    if llm_client is None or not hasattr(llm_client, "provider_rate_limit_payload"):
        return {"profiles": {}, "tasks": {}}
    try:
        payload = llm_client.provider_rate_limit_payload()
        return payload if isinstance(payload, dict) else {"profiles": {}, "tasks": {}}
    except Exception:
        return {"profiles": {}, "tasks": {}}


def _empty_rate_limit(profile: str) -> dict[str, Any]:
    return {
        "profile": profile,
        "status": "ready",
        "cooldown_remaining_seconds": 0.0,
        "cooldown_until": 0.0,
        "total_calls": 0,
        "successes": 0,
        "failures": 0,
        "rate_limited": 0,
        "blocked_calls": 0,
        "consecutive_rate_limits": 0,
        "last_task": "",
        "last_error": "",
        "last_success_at": 0.0,
        "last_limited_at": 0.0,
        "provider_kind": "",
        "provider_mode": "",
        "last_model": "",
        "last_api_format": "",
        "last_cache_hit_pct": None,
        "last_cache_hit_pct_by_task": {},
        "last_prompt_cache_hit_tokens": 0,
        "last_prompt_cache_miss_tokens": 0,
        "last_reasoning_replay_tokens": 0,
        "last_payload_sanitized": False,
        "last_usage": {},
    }


def _task_profile_payload(llm: Any, task: str) -> dict[str, Any]:
    profile_name = llm.profile_name_for_task(task) if hasattr(llm, "profile_name_for_task") else ""
    profile = llm.resolve_task_profile(task) if hasattr(llm, "resolve_task_profile") else None
    return {
        "task": task,
        "profile": profile_name,
        "model": getattr(profile, "model", "") if profile is not None else "",
        "api_format": getattr(profile, "api_format", "") if profile is not None else "",
    }


async def _call_provider_probe(
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_format: str,
) -> dict[str, Any]:
    import aiohttp

    from services.llm.client import call_api

    timeout = aiohttp.ClientTimeout(total=20, sock_read=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await call_api(
            session,
            base_url,
            api_key,
            model,
            [{"type": "text", "text": "You are a health-check endpoint. Reply exactly: OK"}],
            [{"role": "user", "content": "OK"}],
            max_tokens=16,
            api_format=api_format,
        )
