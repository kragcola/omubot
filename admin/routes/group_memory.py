"""Group memory & nickname config editor + card browser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Query, Request

from admin.templates import render


def create_group_memory_router(
    card_store: Any,
    group_memory_config: Any,
    config_path: str = "config/group-memory.json",
) -> APIRouter:
    router = APIRouter()

    import json

    def _read_config_json() -> str:
        p = Path(config_path)
        if p.is_file():
            return p.read_text(encoding="utf-8")
        return ""

    def _write_config_json(raw: str) -> None:
        p = Path(config_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(p)
        # Hot-reload: re-validate and update the in-memory config
        if group_memory_config is not None:
            from kernel.config import GroupMemoryConfig
            try:
                new_cfg = GroupMemoryConfig.load(str(p))
                # Copy fields into the existing instance (reference is shared via PluginContext)
                for field_name in ("version", "memory", "nickname", "card_ttl_days"):
                    setattr(group_memory_config, field_name, getattr(new_cfg, field_name))
            except Exception:
                pass  # keep old config on parse failure

    @router.get("/admin/group-memory")
    async def group_memory_page(
        request: Request,
        card_scope: str = Query("group", alias="card_scope"),
        card_scope_id: str = Query("", alias="card_scope_id"),
    ):
        config_json = _read_config_json()
        config_valid = True
        try:
            json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            config_valid = False

        # Load cards for the selected scope
        cards: list[dict[str, Any]] = []
        entities: list[str] = []
        if card_store is not None and card_scope:
            try:
                if card_scope_id:
                    raw_cards = await card_store.get_entity_cards(card_scope, card_scope_id)
                    cards = [_card_to_dict(c) for c in raw_cards]
                else:
                    entities = await card_store.list_entities(card_scope)
            except Exception:
                pass

        return await render("group_memory.html", {
            "request": request,
            "active_page": "group_memory",
            "config_json": config_json,
            "config_valid": config_valid,
            "card_scope": card_scope,
            "card_scope_id": card_scope_id,
            "cards": cards,
            "entities": entities,
        })

    @router.post("/admin/group-memory/save")
    async def save_group_memory(
        request: Request,
        config_json: str = Form(...),
    ):
        try:
            data = json.loads(config_json)
            formatted = json.dumps(data, ensure_ascii=False, indent=2)
            _write_config_json(formatted)
            messages = [{"type": "success", "text": "配置已保存，热加载生效（无需重启）。"}]
        except json.JSONDecodeError as e:
            messages = [{"type": "danger", "text": f"JSON 格式错误: {e}"}]
            formatted = config_json
        except Exception as e:
            messages = [{"type": "danger", "text": f"保存失败: {e}"}]
            formatted = config_json

        config_valid = True
        try:
            json.loads(formatted)
        except json.JSONDecodeError:
            config_valid = False

        return await render("group_memory.html", {
            "request": request,
            "active_page": "group_memory",
            "config_json": formatted,
            "config_valid": config_valid,
            "messages": messages,
            "card_scope": "",
            "card_scope_id": "",
            "cards": [],
            "entities": [],
        })

    @router.post("/admin/group-memory/cards/delete")
    async def delete_card(
        request: Request,
        card_id: str = Form(...),
    ):
        ok = False
        if card_store is not None and card_id:
            ok = await card_store.expire_card(card_id)
        messages = [
            {"type": "success", "text": f"卡片 {card_id} 已标记为过期"}
            if ok else
            {"type": "danger", "text": f"删除卡片 {card_id} 失败"}
        ]
        return await render("group_memory.html", {
            "request": request,
            "active_page": "group_memory",
            "config_json": _read_config_json(),
            "config_valid": True,
            "messages": messages,
            "card_scope": "",
            "card_scope_id": "",
            "cards": [],
            "entities": [],
        })

    return router


CATEGORY_LABELS = {
    "preference": "偏好",
    "boundary": "边界",
    "relationship": "关系",
    "event": "事件",
    "promise": "承诺",
    "fact": "事实",
    "status": "状态",
}

SCOPE_LABELS = {
    "user": "私聊",
    "group": "群聊",
    "global": "全局",
}


def _card_to_dict(card: Any) -> dict[str, Any]:
    return {
        "card_id": card.card_id,
        "category": card.category,
        "category_label": CATEGORY_LABELS.get(card.category, card.category),
        "scope": card.scope,
        "scope_label": SCOPE_LABELS.get(card.scope, card.scope),
        "scope_id": card.scope_id,
        "content": card.content,
        "confidence": card.confidence,
        "status": card.status,
        "priority": card.priority,
        "source": card.source,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
