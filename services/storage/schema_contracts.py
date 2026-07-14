"""Shared semantic schema contracts for governed SQLite databases."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import aiosqlite


@dataclass(frozen=True, slots=True)
class ColumnContract:
    name: str
    declared_type: str
    not_null: bool = False
    default_sql: str | None = None
    primary_key_position: int = 0


@dataclass(frozen=True, slots=True)
class ForeignKeyContract:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: str = "NO ACTION"
    on_delete: str = "NO ACTION"
    match: str = "NONE"


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[ColumnContract, ...]
    unique_constraints: tuple[tuple[str, ...], ...] = ()
    foreign_keys: tuple[ForeignKeyContract, ...] = ()
    required_sql_fragments: tuple[str, ...] = ()

    @property
    def required_columns(self) -> frozenset[str]:
        return frozenset(column.name for column in self.columns)


@dataclass(frozen=True, slots=True)
class IndexContract:
    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool = False
    partial: bool = False
    where_sql: str | None = None
    compatible_where_sql: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaContract:
    db_id: str
    target_user_version: int
    tables: tuple[TableContract, ...]
    indexes: tuple[IndexContract, ...]


def _column(
    name: str,
    declared_type: str,
    *,
    not_null: bool = False,
    default_sql: str | None = None,
    primary_key: int = 0,
) -> ColumnContract:
    return ColumnContract(
        name=name,
        declared_type=declared_type,
        not_null=not_null,
        default_sql=default_sql,
        primary_key_position=primary_key,
    )


BLOCK_TRACE_V1_CONTRACT = SchemaContract(
    db_id="block_trace",
    target_user_version=1,
    tables=(
        TableContract(
            "prompt_block_traces",
            (
                _column("trace_id", "TEXT", primary_key=1),
                _column("request_id", "TEXT", not_null=True),
                _column("task", "TEXT", not_null=True),
                _column("source", "TEXT", not_null=True),
                _column("provider", "TEXT", not_null=True),
                _column("candidate_id", "TEXT", not_null=True),
                _column("decision", "TEXT", not_null=True),
                _column("hit_reason", "TEXT", not_null=True, default_sql="''"),
                _column("evidence_refs", "TEXT", not_null=True, default_sql="'[]'"),
                _column("token_estimate", "INTEGER", not_null=True, default_sql="0"),
                _column("char_count", "INTEGER", not_null=True, default_sql="0"),
                _column("position", "TEXT", not_null=True, default_sql="'dynamic'"),
                _column("label", "TEXT", not_null=True, default_sql="''"),
                _column("priority", "INTEGER", not_null=True, default_sql="100"),
                _column("decay_state", "TEXT", not_null=True, default_sql="''"),
                _column("budget_reason", "TEXT", not_null=True, default_sql="''"),
                _column("metadata_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("created_at", "TEXT", not_null=True),
            ),
        ),
        TableContract(
            "humanization_metrics",
            (
                _column("metric_id", "TEXT", primary_key=1),
                _column("request_id", "TEXT", not_null=True),
                _column("group_id", "TEXT", not_null=True, default_sql="''"),
                _column("session_id", "TEXT", not_null=True, default_sql="''"),
                _column("turn_id", "TEXT", not_null=True, default_sql="''"),
                _column("score", "REAL", not_null=True, default_sql="0"),
                _column("axes_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("issues_json", "TEXT", not_null=True, default_sql="'[]'"),
                _column("metadata_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("created_at", "TEXT", not_null=True),
            ),
        ),
        TableContract(
            "runtime_metric_events",
            (
                _column("metric_id", "TEXT", primary_key=1),
                _column("metric_key", "TEXT", not_null=True),
                _column("group_id", "TEXT", not_null=True, default_sql="''"),
                _column("amount", "INTEGER", not_null=True, default_sql="1"),
                _column("metadata_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("created_at", "TEXT", not_null=True),
            ),
        ),
    ),
    indexes=(
        IndexContract("idx_bt_request", "prompt_block_traces", ("request_id",)),
        IndexContract("idx_bt_source", "prompt_block_traces", ("source", "candidate_id")),
        IndexContract("idx_bt_created", "prompt_block_traces", ("created_at",)),
        IndexContract("idx_hm_request", "humanization_metrics", ("request_id",)),
        IndexContract(
            "idx_hm_group_session_created",
            "humanization_metrics",
            ("group_id", "session_id", "created_at"),
        ),
        IndexContract("idx_hm_created", "humanization_metrics", ("created_at",)),
        IndexContract(
            "idx_rme_metric_key",
            "runtime_metric_events",
            ("metric_key", "created_at"),
        ),
        IndexContract(
            "idx_rme_group_key",
            "runtime_metric_events",
            ("group_id", "metric_key", "created_at"),
        ),
    ),
)


USAGE_V1_CONTRACT = SchemaContract(
    db_id="usage",
    target_user_version=1,
    tables=(
        TableContract(
            "llm_calls",
            (
                _column("id", "INTEGER", primary_key=1),
                _column("ts", "TEXT", not_null=True),
                _column("call_type", "TEXT", not_null=True),
                _column("user_id", "TEXT"),
                _column("group_id", "TEXT"),
                _column("model", "TEXT", not_null=True),
                _column("provider_kind", "TEXT", not_null=True, default_sql="''"),
                _column("input_tokens", "INTEGER", not_null=True),
                _column("cache_read_tokens", "INTEGER", not_null=True),
                _column("cache_create_tokens", "INTEGER", not_null=True),
                _column("output_tokens", "INTEGER", not_null=True),
                _column(
                    "prompt_cache_hit_tokens",
                    "INTEGER",
                    not_null=True,
                    default_sql="0",
                ),
                _column(
                    "prompt_cache_miss_tokens",
                    "INTEGER",
                    not_null=True,
                    default_sql="0",
                ),
                _column(
                    "reasoning_replay_tokens",
                    "INTEGER",
                    not_null=True,
                    default_sql="0",
                ),
                _column("tool_rounds", "INTEGER", not_null=True),
                _column("elapsed_s", "REAL", not_null=True),
                _column("error", "TEXT"),
            ),
            required_sql_fragments=("autoincrement",),
        ),
    ),
    indexes=(
        IndexContract("idx_llm_calls_ts", "llm_calls", ("ts",)),
        IndexContract("idx_llm_calls_user", "llm_calls", ("user_id",)),
        IndexContract("idx_llm_calls_group", "llm_calls", ("group_id",)),
        IndexContract("idx_llm_calls_type", "llm_calls", ("call_type",)),
    ),
)


EPISODIC_V1_CONTRACT = SchemaContract(
    db_id="episodic",
    target_user_version=1,
    tables=(
        TableContract(
            "episodes",
            (
                _column("episode_id", "TEXT", primary_key=1),
                _column("group_id", "TEXT", not_null=True, default_sql="''"),
                _column("scope", "TEXT", not_null=True, default_sql="'group'"),
                _column("situation", "TEXT", not_null=True),
                _column("observed_context", "TEXT", not_null=True, default_sql="''"),
                _column("action_taken", "TEXT", not_null=True, default_sql="''"),
                _column("outcome_signal", "TEXT", not_null=True, default_sql="''"),
                _column("reflection", "TEXT", not_null=True, default_sql="''"),
                _column("linked_memory_ids", "TEXT", not_null=True, default_sql="'[]'"),
                _column("confidence", "REAL", not_null=True, default_sql="0.5"),
                _column("episode_state", "TEXT", not_null=True, default_sql="'dry_run'"),
                _column("source", "TEXT", not_null=True, default_sql="'consolidator'"),
                _column("decay_at", "TEXT", not_null=True, default_sql="''"),
                _column("last_used_at", "TEXT", not_null=True, default_sql="''"),
                _column("created_at", "TEXT", not_null=True),
                _column("updated_at", "TEXT", not_null=True),
                _column("disabled_by_admin", "INTEGER", not_null=True, default_sql="0"),
                _column("cross_group_visible", "INTEGER", not_null=True, default_sql="0"),
                _column(
                    "cross_group_enabled_by",
                    "TEXT",
                    not_null=True,
                    default_sql="''",
                ),
                _column(
                    "cross_group_enabled_at",
                    "TEXT",
                    not_null=True,
                    default_sql="''",
                ),
                _column(
                    "cross_group_enabled_for_groups",
                    "TEXT",
                    not_null=True,
                    default_sql="'[]'",
                ),
                _column(
                    "cross_group_enabled_reason",
                    "TEXT",
                    not_null=True,
                    default_sql="''",
                ),
                _column("meta_json", "TEXT", not_null=True, default_sql="'{}'"),
            ),
        ),
        TableContract(
            "episode_revisions",
            (
                _column("revision_id", "TEXT", primary_key=1),
                _column("episode_id", "TEXT", not_null=True),
                _column("action", "TEXT", not_null=True),
                _column("actor", "TEXT", not_null=True, default_sql="'system'"),
                _column("prev_state", "TEXT", not_null=True, default_sql="''"),
                _column("new_state", "TEXT", not_null=True, default_sql="''"),
                _column("before_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("after_json", "TEXT", not_null=True, default_sql="'{}'"),
                _column("reason", "TEXT", not_null=True, default_sql="''"),
                _column("created_at", "TEXT", not_null=True),
                _column("meta_json", "TEXT", not_null=True, default_sql="'{}'"),
            ),
            foreign_keys=(
                ForeignKeyContract(
                    columns=("episode_id",),
                    referenced_table="episodes",
                    referenced_columns=("episode_id",),
                    on_delete="CASCADE",
                ),
            ),
        ),
        TableContract(
            "episode_observations",
            (
                _column("id", "INTEGER", primary_key=1),
                _column("episode_id", "TEXT", not_null=True),
                _column("scope", "TEXT", not_null=True),
                _column("group_id", "TEXT", not_null=True, default_sql="''"),
                _column("observed_at", "TEXT", not_null=True),
                _column("trigger_type", "TEXT", not_null=True),
                _column("message_id", "TEXT", not_null=True, default_sql="''"),
                _column("meta", "TEXT", not_null=True, default_sql="'{}'"),
            ),
            unique_constraints=(("episode_id", "message_id", "trigger_type"),),
            required_sql_fragments=("autoincrement", "on conflict ignore"),
        ),
    ),
    indexes=(
        IndexContract("idx_episode_state", "episodes", ("episode_state", "group_id")),
        IndexContract(
            "idx_episode_group",
            "episodes",
            ("scope", "group_id", "episode_state"),
        ),
        IndexContract(
            "idx_episode_decay",
            "episodes",
            ("decay_at",),
            partial=True,
            where_sql="decay_at != ''",
        ),
        IndexContract(
            "idx_episode_cross_group",
            "episodes",
            ("cross_group_visible", "episode_state"),
            partial=True,
            where_sql="cross_group_visible in (1, 2)",
            compatible_where_sql=("cross_group_visible = 1",),
        ),
        IndexContract(
            "idx_episode_rev",
            "episode_revisions",
            ("episode_id", "created_at"),
        ),
        IndexContract(
            "idx_episode_obs_today",
            "episode_observations",
            ("observed_at", "episode_id"),
        ),
        IndexContract(
            "idx_episode_obs_scope",
            "episode_observations",
            ("scope", "group_id", "observed_at"),
        ),
    ),
)

_CONTRACTS = {
    contract.db_id: contract
    for contract in (
        BLOCK_TRACE_V1_CONTRACT,
        USAGE_V1_CONTRACT,
        EPISODIC_V1_CONTRACT,
    )
}


def get_schema_contract(db_id: str) -> SchemaContract | None:
    return _CONTRACTS.get(db_id)


def verify_catalog_schema(
    db_id: str,
    connection: sqlite3.Connection,
    user_version: int,
) -> bool | None:
    """Verify a restore/adoption payload against its semantic schema contract."""
    contract = get_schema_contract(db_id)
    if contract is None:
        return None
    if user_version not in (0, contract.target_user_version):
        return False
    for table in contract.tables:
        column_rows = list(
            connection.execute(f"PRAGMA table_info({_quote(table.name)})")
        )
        index_rows = list(
            connection.execute(f"PRAGMA index_list({_quote(table.name)})")
        )
        foreign_key_rows = list(
            connection.execute(f"PRAGMA foreign_key_list({_quote(table.name)})")
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table.name,),
        ).fetchone()
        index_columns = {
            str(row[1]): tuple(
                str(info[2])
                for info in connection.execute(
                    f"PRAGMA index_info({_quote(str(row[1]))})"
                )
            )
            for row in index_rows
        }
        if not _table_matches(
            table,
            column_rows=column_rows,
            index_rows=index_rows,
            foreign_key_rows=foreign_key_rows,
            table_sql=str(sql_row[0] or "") if sql_row else "",
            index_columns=index_columns,
        ):
            return False
    for index in contract.indexes:
        index_rows = list(
            connection.execute(f"PRAGMA index_list({_quote(index.table)})")
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index.name,),
        ).fetchone()
        if not _index_matches(
            index,
            index_rows=index_rows,
            columns=tuple(
                str(row[2])
                for row in connection.execute(
                    f"PRAGMA index_info({_quote(index.name)})"
                )
            ),
            index_sql=str(sql_row[0] or "") if sql_row else "",
        ):
            return False
    return True


async def verify_catalog_schema_async(
    db_id: str,
    connection: aiosqlite.Connection,
    user_version: int,
) -> bool | None:
    contract = get_schema_contract(db_id)
    if contract is None:
        return None
    if user_version not in (0, contract.target_user_version):
        return False
    for table in contract.tables:
        column_rows = await _fetchall(
            connection,
            f"PRAGMA table_info({_quote(table.name)})",
        )
        index_rows = await _fetchall(
            connection,
            f"PRAGMA index_list({_quote(table.name)})",
        )
        foreign_key_rows = await _fetchall(
            connection,
            f"PRAGMA foreign_key_list({_quote(table.name)})",
        )
        sql_row = await _fetchone(
            connection,
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table.name,),
        )
        index_columns: dict[str, tuple[str, ...]] = {}
        for row in index_rows:
            name = str(row[1])
            index_columns[name] = tuple(
                str(info[2])
                for info in await _fetchall(
                    connection,
                    f"PRAGMA index_info({_quote(name)})",
                )
            )
        if not _table_matches(
            table,
            column_rows=column_rows,
            index_rows=index_rows,
            foreign_key_rows=foreign_key_rows,
            table_sql=str(sql_row[0] or "") if sql_row else "",
            index_columns=index_columns,
        ):
            return False
    for index in contract.indexes:
        index_rows = await _fetchall(
            connection,
            f"PRAGMA index_list({_quote(index.table)})",
        )
        sql_row = await _fetchone(
            connection,
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index.name,),
        )
        columns = tuple(
            str(row[2])
            for row in await _fetchall(
                connection,
                f"PRAGMA index_info({_quote(index.name)})",
            )
        )
        if not _index_matches(
            index,
            index_rows=index_rows,
            columns=columns,
            index_sql=str(sql_row[0] or "") if sql_row else "",
        ):
            return False
    return True


def _table_matches(
    contract: TableContract,
    *,
    column_rows: list[Any],
    index_rows: list[Any],
    foreign_key_rows: list[Any],
    table_sql: str,
    index_columns: dict[str, tuple[str, ...]],
) -> bool:
    actual_columns = {str(row[1]): row for row in column_rows}
    for expected in contract.columns:
        actual = actual_columns.get(expected.name)
        if actual is None:
            return False
        if _normalize_type(actual[2]) != _normalize_type(expected.declared_type):
            return False
        if bool(actual[3]) is not expected.not_null:
            return False
        if _normalize_default(actual[4]) != _normalize_default(expected.default_sql):
            return False
        if int(actual[5]) != expected.primary_key_position:
            return False

    normalized_table_sql = _normalize_sql(table_sql)
    if any(
        _normalize_sql(fragment) not in normalized_table_sql
        for fragment in contract.required_sql_fragments
    ):
        return False

    for required_columns in contract.unique_constraints:
        if not any(
            bool(row[2])
            and index_columns.get(str(row[1]), ()) == required_columns
            for row in index_rows
        ):
            return False

    grouped_foreign_keys: dict[int, list[Any]] = {}
    for row in foreign_key_rows:
        grouped_foreign_keys.setdefault(int(row[0]), []).append(row)
    for expected in contract.foreign_keys:
        if not any(
            _foreign_key_matches(expected, rows)
            for rows in grouped_foreign_keys.values()
        ):
            return False
    return True


def _foreign_key_matches(
    expected: ForeignKeyContract,
    rows: list[Any],
) -> bool:
    ordered = sorted(rows, key=lambda row: int(row[1]))
    if not ordered:
        return False
    return (
        str(ordered[0][2]) == expected.referenced_table
        and tuple(str(row[3]) for row in ordered) == expected.columns
        and tuple(str(row[4]) for row in ordered) == expected.referenced_columns
        and str(ordered[0][5]).upper() == expected.on_update.upper()
        and str(ordered[0][6]).upper() == expected.on_delete.upper()
        and str(ordered[0][7]).upper() == expected.match.upper()
    )


def _index_matches(
    contract: IndexContract,
    *,
    index_rows: list[Any],
    columns: tuple[str, ...],
    index_sql: str,
) -> bool:
    row = next((item for item in index_rows if str(item[1]) == contract.name), None)
    if row is None:
        return False
    if bool(row[2]) is not contract.unique or bool(row[4]) is not contract.partial:
        return False
    if columns != contract.columns:
        return False
    if contract.where_sql is None:
        return True
    allowed_where = {
        _normalize_sql(contract.where_sql),
        *(_normalize_sql(value) for value in contract.compatible_where_sql),
    }
    return _where_clause(index_sql) in allowed_where


async def _fetchall(
    connection: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[Any]:
    cursor = await connection.execute(sql, params)
    try:
        return list(await cursor.fetchall())
    finally:
        await cursor.close()


async def _fetchone(
    connection: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> Any | None:
    cursor = await connection.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _normalize_type(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().split())


def _normalize_sql(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _where_clause(index_sql: str) -> str:
    match = re.search(r"\bwhere\b(?P<where>.*)$", index_sql, flags=re.IGNORECASE | re.DOTALL)
    return _normalize_sql(match.group("where")) if match else ""


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
