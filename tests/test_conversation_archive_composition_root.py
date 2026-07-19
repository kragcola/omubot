"""Composition-root contracts: ConversationArchive owns storage/messages.db.

These tests are static / tmp-path only — they must not open production DBs or
start the full bot runtime.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from bootstrap.chat_runtime import build_chat_runtime
from services.conversation_archive import ConversationArchive
from services.conversation_archive.scanner import read_scan_batch
from services.memory.message_log import MessageLog
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


def test_messages_catalog_owned_by_conversation_archive() -> None:
    spec = DEFAULT_DATABASE_CATALOG.get("messages")
    assert spec.path == "storage/messages.db"
    assert spec.owner == "services.conversation_archive.store"
    assert "services.conversation_archive.store" in spec.clients
    assert "services.memory.message_log" in spec.clients


def test_build_chat_runtime_instantiates_conversation_archive_for_messages_db() -> None:
    tree = _builder_tree()

    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "ConversationArchive"
    }
    assert imported, "build_chat_runtime must import ConversationArchive"

    archive_calls = _constructor_calls(tree, "ConversationArchive")
    assert archive_calls, "build_chat_runtime must construct ConversationArchive"
    paths = {_db_path_literal(call) for call in archive_calls}
    assert "storage/messages.db" in paths

    message_log_calls = _constructor_calls(tree, "MessageLog")
    assert not message_log_calls, (
        "composition root must not construct MessageLog for messages.db; "
        f"found {len(message_log_calls)} MessageLog(...)"
    )


def test_build_chat_runtime_publishes_msg_log_and_consolidator_archive() -> None:
    """ctx.msg_log and MemoryConsolidator(archive=...) share the same binding."""
    tree = _builder_tree()
    source = textwrap.dedent(inspect.getsource(build_chat_runtime))

    # msg_log assignment from the archive instance
    assigns_msg_log = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
                and target.attr == "msg_log"
            ):
                assigns_msg_log = True
    assert assigns_msg_log, "build_chat_runtime must set ctx.msg_log"

    # MemoryConsolidator archive keyword is ctx.msg_log (same instance)
    consolidator_calls = _constructor_calls(tree, "MemoryConsolidator")
    assert consolidator_calls, "build_chat_runtime must construct MemoryConsolidator"
    archive_kwargs = [
        kw.value
        for call in consolidator_calls
        for kw in call.keywords
        if kw.arg == "archive"
    ]
    assert archive_kwargs, "MemoryConsolidator must receive archive="
    for value in archive_kwargs:
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name) and value.value.id == "ctx"
        assert value.attr == "msg_log"

    # Guard against reintroducing MessageLog at the messages.db site
    assert "MessageLog(db_path=\"storage/messages.db\")" not in source
    assert "MessageLog(db_path='storage/messages.db')" not in source


@pytest.mark.asyncio
async def test_archive_instance_is_scanner_capable_without_production_db(
    tmp_path: Path,
) -> None:
    """Archive-backed consolidator path needs read_scan_batch, not fallback only."""
    db_path = tmp_path / "messages.db"
    archive = ConversationArchive(db_path=str(db_path))
    await archive.init()
    try:
        assert callable(getattr(archive, "read_scan_batch", None))
        assert callable(getattr(archive, "query_term_hits", None))
        # MessageLog lacks cursor scan; archive must not.
        assert not hasattr(MessageLog, "read_scan_batch")

        await archive.record(
            group_id="comp-g",
            role="user",
            speaker="u(1)",
            content_text="scanner seed",
            content_json=None,
            message_id=1,
            created_at=1.0,
        )
        batch = await read_scan_batch(
            archive,
            scanner_name="composition_root_probe",
            group_id="comp-g",
            limit=10,
            scanner_version="v-test",
            params_hash="p-test",
        )
        assert batch.get("source") == "archive"
        assert [row["content_text"] for row in batch.get("rows") or []] == [
            "scanner seed"
        ]
    finally:
        await archive.close()


def test_message_log_protocol_satisfied_by_message_log_and_archive() -> None:
    """Both concrete MessageLog and ConversationArchive satisfy the port."""
    from services.memory.message_log import MessageLogPort

    assert isinstance(MessageLog, type)
    # Structural: class-level methods exist with expected names
    required = (
        "init",
        "close",
        "record",
        "query_recent",
        "query_term_hits",
        "list_group_ids",
        "record_session_msg",
        "query_for_compact",
    )
    for name in required:
        assert callable(getattr(MessageLog, name, None)), name
        assert callable(getattr(ConversationArchive, name, None)), name

    # runtime_checkable on instances after construction (no I/O)
    msg_log = MessageLog(db_path=":memory:")
    archive = ConversationArchive(db_path=":memory:")
    assert isinstance(msg_log, MessageLogPort)
    assert isinstance(archive, MessageLogPort)


def test_build_chat_runtime_constructs_archive_without_cast_any() -> None:
    """Composition root must not wrap ConversationArchive(...) in cast(Any, ...)."""
    source = textwrap.dedent(inspect.getsource(build_chat_runtime))
    assert "cast(Any, ConversationArchive(" not in source
    assert "ConversationArchive(db_path=\"storage/messages.db\")" in source or (
        "ConversationArchive(db_path='storage/messages.db')" in source
    )
