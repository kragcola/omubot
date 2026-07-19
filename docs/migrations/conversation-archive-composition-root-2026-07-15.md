# ConversationArchive Composition-Root Migration (2026-07-15)

> 状态：implemented (code path), not deployed by this packet.
> 范围：把 `storage/messages.db` 的 composition-root 所有者从 `MessageLog` 切到 `ConversationArchive`，补齐 `query_term_hits` 兼容缺口；**不**改生产数据格式、**不**新建 DB 路径、**不**复制/删除历史行。

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| Composition root 构造 | `bootstrap/chat_runtime.py` → `MessageLog(db_path="storage/messages.db")` | `ConversationArchive(db_path="storage/messages.db")` |
| `ctx.msg_log` 类型 | `services.memory.message_log.MessageLog` | `services.conversation_archive.store.ConversationArchive`（仍挂 `ctx.msg_log`） |
| `MemoryConsolidator.archive` | 同上 MessageLog 实例 → scanner 只能 `query_recent` fallback | 同一 archive 实例 → `read_scan_batch` 游标扫描 |
| Catalog owner `messages` | `services.memory.message_log` | `services.conversation_archive.store` |
| Catalog clients | `(message_log, conversation_archive.store)` | `(conversation_archive.store, message_log)` — message_log 仍为兼容/客户端模块 |
| 公共 API 缺口 | Archive 缺 `query_term_hits` | Archive 提供与 MessageLog 行为对等的 `query_term_hits` |
| DB 路径 | `storage/messages.db` | **不变** |
| 主兼容表 | `group_messages` | **仍为 canonical 兼容读表** |
| 归档表 | `conversation_messages` 等 | 启动时幂等 backfill + 写入 dual-write（既有行为） |

## Same-file Schema / Backfill Behavior

- 仍打开 **同一文件** `storage/messages.db`。
- `ConversationArchive.init()`：
  1. 确保 legacy `group_messages` + 索引存在；
  2. 确保 archive 表（`conversation_messages`、cursors/runs/policies/refs）存在；
  3. `backfill_legacy_messages()`：按 `legacy_row_id` 幂等把尚未镜像的 `group_messages` 行写入 `conversation_messages`（`INSERT OR IGNORE` / unique partial index）。
- 热路径 `record`：先写 `group_messages`，再 dual-write archive；archive 侧失败只打日志，legacy 行保留。
- **No copy / no delete**：不复制整库到新路径，不删除 `group_messages` 或历史 archive 行；backfill 只补缺，不覆盖业务内容。

## Caller Compatibility

| 调用方 | 期望 | 迁移后 |
|--------|------|--------|
| GroupTimeline / StateBoard | `record` / `query_recent` | 不变（duck-typed） |
| Slang backlog reviewer | `query_term_hits` | Archive 现提供 |
| Compact / session | `query_for_compact` / `record_session_msg` / `list_group_ids` | 不变 |
| MemoryConsolidator | `archive` + scanner `read_scan_batch` | 获得真实游标路径 |
| 直接 `from services.memory.message_log import MessageLog` 的测试/工具 | 仍可独立使用 MessageLog | 兼容模块保留，非 composition root |

## Rollback

1. 代码回退：将 `bootstrap/chat_runtime.py` 改回 `MessageLog(...)`，catalog owner 改回 `services.memory.message_log`。
2. 数据：无需数据回滚；`group_messages` 始终是兼容真源，多出来的 archive 表可保留（只读残留，不阻塞 MessageLog）。
3. 不删除 `conversation_*` 表，除非后续有独立清理任务。

## Startup Cost

- 相对纯 MessageLog：多一轮 schema ensure + 幂等 backfill 扫描。
- Backfill 成本与「尚未镜像的 legacy 行数」成正比；已同步库上为 O(扫描空批) 接近常数。
- 不引入第二连接池或第二 DB 文件。

## Explicit Non-goals (this packet)

- 不部署、不重启容器、不打开生产 volume DB。
- 不改 `services/context`、`services/memory/retrieval.py`、plugins/context、memory eval。
- 不改 QZone 文件。
- 不 commit / push。

## Verification (local)

```bash
source ./scripts/dev/env.sh
uv run pytest \
  tests/test_conversation_archive_store.py \
  tests/test_conversation_archive_composition_root.py \
  tests/test_memory_consolidator.py \
  tests/test_database_catalog.py \
  -q
uv run ruff check \
  services/conversation_archive/store.py \
  bootstrap/chat_runtime.py \
  services/storage/catalog.py \
  tests/test_conversation_archive_store.py \
  tests/test_conversation_archive_composition_root.py
uv run pyright \
  services/conversation_archive/store.py \
  bootstrap/chat_runtime.py \
  services/storage/catalog.py \
  tests/test_conversation_archive_store.py \
  tests/test_conversation_archive_composition_root.py
```
