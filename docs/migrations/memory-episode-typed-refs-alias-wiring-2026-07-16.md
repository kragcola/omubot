# Memory Episode Typed Refs / Alias Registry Production Wiring (2026-07-16)

> 状态：implemented in code, **not deployed**.
> 范围：真实 resolver ports、EpisodePromoter 作用域接线、EpisodeProvider typed evidence、EntityAliasStore bootstrap lifecycle、Affection nickname alias seed。
> 非目标：big-bang backfill、旧 episode 重写、QZone、NapCat、`BUILTIN_WIRE_PROFILE.validated`、部署/canary。

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| ConversationArchive | 仅有 range/after_pk 列表 API；promoter 调 `get_messages_by_pks` 会失败/吞异常 | `get_messages_by_pks(message_pks, *, chat_type=None, chat_id=None)` 参数化 SQL；升序、去重、`platform_message_id AS message_id` |
| CardStore | 无 source_msg 反查 | `find_by_source_message_ids(..., *, allowed_scopes=None)`；仅 active；稳定 `card_id` 序 |
| KnowledgeGraph | 无 evidence 反查 | `find_fact_ids_by_evidence_refs(..., *, allowed_scopes=None)`；join `graph_evidence`→active `graph_facts` |
| EpisodePromoter | 假端口接线；无 scope kwargs；缺端口时 enrichment 静默失败 | 生产传入 archive/card/kg/alias；`chat_type='group'` + `chat_id=candidate.group_id`；`allowed_scopes=group + proven users`；非数字 group 只保留 `message_pk` |
| Bootstrap | 无 EntityAliasStore；EpisodePromoter 仅 candidates+episode store | `storage/entity_aliases.db` 构造/init/`ctx.entity_alias_store`；assembly 一次 close；四端口注入 promoter |
| EpisodeProvider | `evidence_refs` 仅 episode_id | 选中 episode 追加 `linked_ref_evidence(...)`；metadata `typed_evidence_count` / `typed_evidence_refs`；prompt 文案不变 |
| Affection nickname | 仅 AffectionStore 持久化 | `SetNicknameTool.execute` 成功后 best-effort `observe(source=affection_nickname)`；群/私聊 scope 分离；alias 失败不拖垮昵称成功 |

## Scope Safety Contract

1. **Archive**：生产 promoter 只请求 `chat_type='group', chat_id=<numeric candidate.group_id>`。他群/私聊 PK 不得返回，不得 hydrate message/user。
2. **Card / Fact**：`allowed_scopes` 仅含 `("group", group_id)` 与归档行证明的 `("user", user_id)`。同 evidence id 的他群 card/fact 不得链接。
3. **状态过滤**：superseded/expired cards、superseded/rejected/pending facts 不得进入新 Episode 的 linked refs。
4. **历史 provenance 保留**：resolver 只控制**新** prompt-eligible Episode 的链接面；不删 graph_evidence、不改旧 episode。
5. **无 backfill**：已存在 episode 不被重写；re-promote 仍走既有幂等 provenance 路径。

## Bootstrap / Lifecycle

- `EntityAliasStore("storage/entity_aliases.db")` → `await init()` → `ctx.entity_alias_store`
- `create_chat_runtime_assembly` 的 `resource_fields` + finalizers 登记 `entity_alias_store`，正常 close 与 build rollback 各 close 一次
- `EpisodePromoter(message_archive=ctx.msg_log, card_store=ctx.card_store, knowledge_graph=ctx.knowledge_graph, entity_alias_store=ctx.entity_alias_store)`
- Affection plugin `on_startup` 读取 `ctx.entity_alias_store`，`register_tools` 注入 `SetNicknameTool`

## Key Files

| 路径 | 变更 |
|------|------|
| `services/conversation_archive/store.py` | `get_messages_by_pks` |
| `services/memory/card_store.py` | `find_by_source_message_ids` |
| `services/knowledge_graph/store.py` / `service.py` | evidence reverse join + public API |
| `services/memory_consolidator/promoter.py` | scoped enrichment |
| `services/block_trace/episode_provider.py` | typed evidence_refs + metadata |
| `bootstrap/chat_runtime.py` | alias store lifecycle + promoter ports |
| `services/tools/affection_tools.py` / `plugins/affection/plugin.py` | nickname seed boundary |
| `kernel/types.py` | `PluginContext.entity_alias_store` |
| `tests/test_episode_typed_refs_resolvers.py` 等 | RED/GREEN 行为与负向证明 |

## Verification Ledger (exact commands)

Environment:

```bash
source ./scripts/dev/env.sh
export PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}
```

| 阶段 | 命令 | 结果 |
|------|------|------|
| RED focused | `uv run pytest -q --tb=line tests/test_episode_typed_refs_resolvers.py tests/test_episode_promoter_typed_refs_integration.py tests/test_episode_provider_typed_evidence.py tests/test_entity_alias_bootstrap_wiring.py tests/test_affection_nickname_alias_seed.py` | **32 failed, 3 passed**（方法缺失 / 无 scope kwargs / 无 wiring / 无 nickname seed） |
| GREEN focused | 同上 | **35 passed** |
| Fake 兼容 + focused | `... + tests/test_memory_consolidator_promote.py` | **54 passed**（更新 fake 接受 `chat_type` / `allowed_scopes`） |
| Related regression | archive/card/kg/episode/alias/affection/composition 组合 | **254 passed** |
| Scoped Ruff | touched prod + tests | **All checks passed** |
| Scoped Pyright | 同上 | **0 errors** |
| Independent review | Grok read-only review + Codex probes | **0 Critical / 2 Important**：PK coercion fake-green；Provider empty-ids typed-property fallback |
| Review remediation RED | seeded PK 1 + duck Episode adapter | **3 failed**（2 PK coercion + 1 Provider fallback） |
| Review remediation GREEN | five-file focused + linked-ref/promoter related | focused **38 passed**；相关组合 **110 passed** |
| Unicode decimal guard | `"²"` archive/promoter 反例 | **2 failed → 2 passed**；改用 `isdecimal()` 后 fail-closed |
| Full suite | `uv run pytest -q` | **3988 passed, 17 skipped, 186 warnings**（相对 3950 基线净增 38） |

## Independent Review Remediation

- `ConversationArchive.get_messages_by_pks` 与 promoter `_positive_ints` 不再使用宽松 `int(raw)`：仅接受正整数和纯数字字符串，显式拒绝 `bool` 与全部 `float`；非集合标量 fail-closed 返回空，generator/重复值仍可用。
- 原 invalid-input 测试改为先写入真实 `message_pk=1`，防止 `1.5 → 1` 因空库而假绿。
- `EpisodeProvider` 在 `linked_memory_ids` 缺失或为空时回退 `linked_memory_refs`；`parse_linked_ref` 可规范化 `LinkedMemoryRef` 对象，raw episode-id evidence 与 prompt 文案保持不变。
- global + numeric `group_id` 仍按保守策略只保留 `message_pk`，不做 source-group enrichment；这是安全取舍，不是本 slice 的未修缺陷。

## Deployment / Observation

本 packet **未部署**。后续部署只允许 recreate bot，永不 recreate NapCat。上线后建议观察：

1. promote 日志中 archive/card/kg lookup warning 率（scope kwargs 不匹配旧 fake/自定义端口时会 best-effort 跳过 enrichment）。
2. 新 episode `linked_memory_ids` 中 `card:` / `fact:` / `entity:user:qq:` 覆盖率；他群 poison 应仍为 0。
3. `storage/entity_aliases.db` 中 `source=affection_nickname` / `archive_speaker` 观察量与 ambiguous 冲突量。
4. EpisodeProvider BlockTrace `evidence_refs` 长度与 metadata `typed_evidence_*` 一致性；prompt 文案应无变化。

## Rollback

1. 还原上表 Key Files 中的 production hunks（含 `kernel/types.py` 的可选字段）。
2. 删除/还原五个新测试文件与 `tests/test_memory_consolidator_promote.py` fake 签名兼容改动。
3. 数据：`entity_aliases.db` 与 episode `linked_memory_ids` 均为 additive；旧 reader 忽略未知键/额外 ref。不需要删 graph/card/archive 行。
4. 不部署则零运行态影响。

## Explicit Non-goals / Residuals

- **No backfill**：不对历史 episode 批量补链。
- **No deploy / canary**。
- **No QZone / NapCat / character pack** 变更。
- **No** `BUILTIN_WIRE_PROFILE.validated=true`。
- 可选后续：episode meta 表达式索引；bounded PPR / GraphRAG；MemGPT 风格 memory tools。
