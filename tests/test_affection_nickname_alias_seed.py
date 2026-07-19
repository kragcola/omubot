"""RED: SetNicknameTool seeds EntityAliasStore on successful nickname set.

Boundary is async SetNicknameTool.execute (not AffectionEngine sync APIs).
Plugin/runtime may supply alias store via tool constructor, attribute, or getter.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from kernel.types import ToolContext
from plugins.affection.engine import AffectionEngine
from plugins.affection.store import AffectionStore
from services.memory.entity_alias_store import EntityAliasStore
from services.tools.affection_tools import SetNicknameTool


class _SpyAliasStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_exc: BaseException | None = None

    async def observe(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"outcome": "created", **kwargs}


@pytest.fixture
async def affection_engine(tmp_path: Path) -> AffectionEngine:
    store = AffectionStore(storage_dir=str(tmp_path / "affection"))
    await store.startup()
    return AffectionEngine(store)


@pytest.fixture
async def alias_store(tmp_path: Path):
    s = EntityAliasStore(tmp_path / "entity_aliases.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _tool_with_alias(engine: AffectionEngine, alias: Any) -> SetNicknameTool:
    """Attach alias store via GREEN constructor kwargs, else transitional attributes."""
    for kwargs in (
        {"entity_alias_store": alias},
        {"alias_store": alias},
        {"entity_alias_store_getter": (lambda: alias)},
    ):
        try:
            return SetNicknameTool(engine, **kwargs)  # type: ignore[call-arg]
        except TypeError:
            continue
    tool = SetNicknameTool(engine)
    for attr in (
        "_entity_alias_store",
        "entity_alias_store",
        "_alias_store",
        "alias_store",
    ):
        setattr(tool, attr, alias)
    return tool


def test_set_nickname_tool_constructor_accepts_alias_store_port(
    affection_engine: AffectionEngine,
) -> None:
    """Production wiring surface: constructor must accept an alias store port."""
    sig = inspect.signature(SetNicknameTool.__init__)
    names = set(sig.parameters)
    assert names & {
        "entity_alias_store",
        "alias_store",
        "entity_alias_store_getter",
    }, (
        "SetNicknameTool.__init__ must accept entity_alias_store / alias_store / "
        "entity_alias_store_getter for plugin wiring"
    )


@pytest.mark.asyncio
async def test_set_nickname_group_seeds_alias_high_confidence(
    affection_engine: AffectionEngine,
    alias_store: EntityAliasStore,
) -> None:
    tool = _tool_with_alias(affection_engine, alias_store)
    ctx = ToolContext(user_id="123", group_id="456", session_id="group_456")

    result = await tool.execute(ctx, user_id="123", nickname="司君")
    assert "失败" not in result
    assert "司君" in result

    profile = affection_engine._store.get("123")
    assert "司君" in profile.group_nicknames.values() or profile.custom_nickname == "司君"

    resolved = await alias_store.resolve(
        alias="司君", scope="group", scope_id="456",
    )
    assert resolved == "user:qq:123"

    rows = await alias_store.list_aliases(
        entity_key="user:qq:123", scope="group", scope_id="456",
    )
    assert rows
    match = next(
        (r for r in rows if r.alias_surface == "司君" or r.alias_norm),
        rows[0],
    )
    assert match.source == "affection_nickname"
    assert match.confidence >= 0.8
    assert match.scope == "group"
    assert match.scope_id == "456"


@pytest.mark.asyncio
async def test_set_nickname_private_seeds_user_scope(
    affection_engine: AffectionEngine,
    alias_store: EntityAliasStore,
) -> None:
    tool = _tool_with_alias(affection_engine, alias_store)
    ctx = ToolContext(user_id="123", group_id=None, session_id="private_123")

    result = await tool.execute(ctx, user_id="123", nickname="宁宁")
    assert "失败" not in result

    resolved = await alias_store.resolve(
        alias="宁宁", scope="user", scope_id="123",
    )
    assert resolved == "user:qq:123"
    assert (
        await alias_store.resolve(alias="宁宁", scope="group", scope_id="123")
    ) is None


@pytest.mark.asyncio
async def test_set_nickname_alias_failure_does_not_fail_nickname(
    affection_engine: AffectionEngine,
) -> None:
    spy = _SpyAliasStore()
    spy.raise_exc = RuntimeError("alias db down")
    tool = _tool_with_alias(affection_engine, spy)
    ctx = ToolContext(user_id="123", group_id="456", session_id="group_456")

    result = await tool.execute(ctx, user_id="123", nickname="司君")
    assert "失败" not in result
    assert "司君" in result
    # Seed path must attempt observe (best-effort) even when it fails
    assert len(spy.calls) >= 1
    obs = spy.calls[0]
    assert obs.get("entity_key") == "user:qq:123"
    assert obs.get("alias") == "司君"
    assert obs.get("scope") == "group"
    assert str(obs.get("scope_id")) == "456"
    assert obs.get("source") == "affection_nickname"


@pytest.mark.asyncio
async def test_set_nickname_non_numeric_user_id_skips_alias_no_fail(
    affection_engine: AffectionEngine,
    alias_store: EntityAliasStore,
) -> None:
    spy = _SpyAliasStore()
    tool = _tool_with_alias(affection_engine, spy)
    ctx = ToolContext(user_id="not-a-qq", group_id="456", session_id="group_456")

    result = await tool.execute(ctx, user_id="not-a-qq", nickname="路人")
    assert "失败" not in result
    assert spy.calls == []  # no observe for non-decimal QQ id
    assert (
        await alias_store.resolve(alias="路人", scope="group", scope_id="456")
    ) is None


@pytest.mark.asyncio
async def test_set_nickname_group_scope_does_not_cross_groups(
    affection_engine: AffectionEngine,
    alias_store: EntityAliasStore,
) -> None:
    tool = _tool_with_alias(affection_engine, alias_store)
    ctx_a = ToolContext(user_id="123", group_id="100", session_id="g100")
    await tool.execute(ctx_a, user_id="123", nickname="A群名")

    assert (
        await alias_store.resolve(alias="A群名", scope="group", scope_id="100")
    ) == "user:qq:123"
    assert (
        await alias_store.resolve(alias="A群名", scope="group", scope_id="200")
    ) is None


@pytest.mark.asyncio
async def test_set_nickname_alias_persists_across_reopen(
    affection_engine: AffectionEngine,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "entity_aliases.db"
    store = EntityAliasStore(db_path)
    await store.init()
    tool = _tool_with_alias(affection_engine, store)
    ctx = ToolContext(user_id="555", group_id="777", session_id="g777")
    await tool.execute(ctx, user_id="555", nickname="持久")
    await store.close()

    reopened = EntityAliasStore(db_path)
    await reopened.init()
    try:
        assert (
            await reopened.resolve(alias="持久", scope="group", scope_id="777")
        ) == "user:qq:555"
    finally:
        await reopened.close()
