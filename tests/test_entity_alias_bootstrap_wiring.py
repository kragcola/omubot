"""RED composition-root contracts: EntityAliasStore + EpisodePromoter ports.

Mirrors the AST/source inspection style of
tests/test_conversation_archive_composition_root.py.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from bootstrap.chat_runtime import build_chat_runtime, create_chat_runtime_assembly
from services.memory.entity_alias_store import EntityAliasStore
from services.storage.catalog import DEFAULT_DATABASE_CATALOG


def _builder_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(build_chat_runtime)))


def _constructor_calls(tree: ast.AST, constructor: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == constructor
    ]


def _db_path_literal(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if (
            keyword.arg == "db_path"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value
    if call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        if isinstance(value, str):
            return value
    return None


def test_entity_aliases_catalog_path() -> None:
    spec = DEFAULT_DATABASE_CATALOG.get("entity_aliases")
    assert spec is not None
    assert spec.path == "storage/entity_aliases.db"


def test_build_chat_runtime_constructs_entity_alias_store() -> None:
    tree = _builder_tree()
    source = textwrap.dedent(inspect.getsource(build_chat_runtime))

    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "EntityAliasStore"
    }
    assert imported, "build_chat_runtime must import EntityAliasStore"

    alias_calls = _constructor_calls(tree, "EntityAliasStore")
    assert alias_calls, "build_chat_runtime must construct EntityAliasStore"
    paths = {_db_path_literal(call) for call in alias_calls}
    assert "storage/entity_aliases.db" in paths

    assert "entity_alias_store" in source
    # await init
    assert "entity_alias_store.init" in source or "await ctx.entity_alias_store.init" in source


def test_build_chat_runtime_publishes_entity_alias_store_on_ctx() -> None:
    tree = _builder_tree()
    assigns = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
                and target.attr == "entity_alias_store"
            ):
                assigns = True
    assert assigns, "build_chat_runtime must set ctx.entity_alias_store"


def test_create_chat_runtime_assembly_owns_entity_alias_store_close() -> None:
    source = textwrap.dedent(inspect.getsource(create_chat_runtime_assembly))
    assert "entity_alias_store" in source, (
        "create_chat_runtime_assembly must track entity_alias_store ownership"
    )
    # resource_fields tuple or finalizer close_attr must include it
    assert (
        '"entity_alias_store"' in source
        or "'entity_alias_store'" in source
    )


def test_episode_promoter_wiring_receives_four_ports() -> None:
    """EpisodePromoter must receive msg_log, card_store, knowledge_graph, entity_alias_store."""
    source = textwrap.dedent(inspect.getsource(build_chat_runtime))
    tree = _builder_tree()
    promoter_calls = _constructor_calls(tree, "EpisodePromoter")
    assert promoter_calls, "build_chat_runtime must construct EpisodePromoter"
    call = promoter_calls[0]
    kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}

    required = {
        "message_archive": "msg_log",
        "card_store": "card_store",
        "knowledge_graph": "knowledge_graph",
        "entity_alias_store": "entity_alias_store",
    }
    for kw, ctx_attr in required.items():
        assert kw in kwargs, f"EpisodePromoter must receive {kw}="
        value = kwargs[kw]
        assert isinstance(value, ast.Attribute), f"{kw} must be ctx.<attr>"
        assert isinstance(value.value, ast.Name) and value.value.id == "ctx"
        assert value.attr == ctx_attr, f"{kw} must be ctx.{ctx_attr}, got ctx.{value.attr}"

    # Negative: bare two-arg constructor without ports is no longer sufficient
    # once GREEN lands — this RED requires the four ports present.
    assert "message_archive=" in source
    assert "entity_alias_store=" in source


@pytest.mark.asyncio
async def test_entity_alias_store_close_is_idempotent_for_assembly_pattern(
    tmp_path: Path,
) -> None:
    """Negative proof: bootstrap closes store; close twice is safe."""
    path = tmp_path / "entity_aliases.db"
    store = EntityAliasStore(path)
    await store.init()
    await store.observe(
        entity_key="user:qq:1",
        alias="小明",
        scope="group",
        scope_id="g1",
        confidence=0.8,
        source="test",
    )
    await store.close()
    await store.close()  # must not raise


@pytest.mark.asyncio
async def test_alias_collision_resolve_returns_none(tmp_path: Path) -> None:
    """Negative proof: ambiguous alias resolve → None (existing store contract)."""
    store = EntityAliasStore(tmp_path / "entity_aliases.db")
    await store.init()
    try:
        await store.observe(
            entity_key="user:qq:1",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=0.8,
            source="test",
        )
        await store.observe(
            entity_key="user:qq:2",
            alias="小明",
            scope="group",
            scope_id="g1",
            confidence=0.8,
            source="test",
        )
        assert (
            await store.resolve(alias="小明", scope="group", scope_id="g1")
        ) is None
    finally:
        await store.close()
