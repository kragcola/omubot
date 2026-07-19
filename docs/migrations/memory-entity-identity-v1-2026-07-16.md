# Memory Entity Identity v1 Migration (2026-07-16)

> 状态：implemented in code, not deployed.
> 范围：为 Card owner、KnowledgeGraph fact 与 Context provenance 建立 additive、scope-safe 的稳定实体键；不改 SQLite schema、不批量重写历史事实、不启用重型 GraphRAG/PPR。

## Old → New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| Card provenance | `scope:scope_id:category`（owner 与 category 淆合） | user/group owner 使用 `user:qq:<id>` / `group:qq:<id>`；category 留在 metadata |
| KG subject/object | 仅 surface string | 新 active fact 的 `metadata_json` additive 写入 `entity_identity_version=1`、`subject_entity_key`、`object_entity_key` |
| Graph provenance | `scope:scope_id:subject:predicate` | 优先合法 subject entity key；旧 fact 从 scoped surface 派生 |
| Multi-hop join | `normalize_text_key(subject/object)` | 优先 canonical entity key；无 metadata 时使用 `concept:<scope>:<scope_id>:<slug>` fallback |
| Supersede | 仅 fact_id 链，实体连续性依赖同名 | 昵称改名保留 subject key；显式另一个平台实体重算；legacy fact 从旧 subject 恢复；object 随新事实重算 |
| 标点/坏行 | 归一化为空时可抛错并丢整条 graph source | 非空符号 surface 使用 scoped raw hash；空 role 返回空键并从 hop frontier 过滤 |

## Key Contract

- 平台用户：`user:qq:<canonical_numeric_id>`。
- 平台群：`group:qq:<canonical_numeric_id>`。
- 未解析概念：`concept:<scope>:<scope_id>:<normalized_surface>`。
- 只有显式 `用户<数字>` / `群<数字>` surface 自动推断平台实体；bare digits 保持 scoped concept。
- 前导零 canonicalize（例如 `用户 00123` → `user:qq:123`）。
- 非空但归一化为空的符号 surface 使用 `raw-<sha256-prefix>`，仍受 scope/scope_id 隔离。
- `parse_entity_key()` 不接受旧 `user:123:category` 形式；旧数据只走兼容派生，不冒充 v1 key。

## Storage / Compatibility

- **No schema migration**：复用 `graph_facts.metadata_json`；Card/Context 不新增列。
- 新 active fact 在 `promote_directly`、candidate approval、supersede 三条写入路径写 v1 metadata。
- 已存在 active fact 不批量 backfill；读取时优先合法顶层/内嵌 key，并 canonicalize，随后才按 surface fallback。
- malformed 顶层 ghost key 不遮蔽内嵌合法 metadata。
- legacy 正常文本、符号文本、空 subject/object 均不能让整个 graph source 失败。
- 现有 fact listener、pending review、active-only filtering、supersedes、max_hops≤2 与 top_k 上限不变。

## Deployment / Observation

本 packet 未部署。后续部署只允许 recreate bot，永不 recreate NapCat。上线后建议观察：

1. GraphContext source error 中 `graph:ValueError` 是否归零。
2. 新 fact 的 entity metadata 覆盖率与 legacy fallback 比例。
3. 同群同名 surface 被不同 platform key 隔离的命中情况。
4. supersede 中 rename / explicit reassignment 的数量与 provenance 连续性。

## Rollback

1. 还原 `services/memory/entity_identity.py`、`services/context/sources.py` 与 `services/knowledge_graph/service.py` 的本 migration hunks。
2. 删除/还原 `tests/test_entity_identity.py` 与本清单。
3. 数据无需回滚：历史/new fact metadata 为 additive JSON；旧 reader 会忽略未知键。
4. 不删除 graph fact、evidence、supersede 链或 Card 数据。

## Explicit Non-goals

- Episode `linked_memory_ids` / person entity back-link。
- alias registry 与 nickname validity window。
- graph_nodes user/concept projection。
- Personalized PageRank / GraphRAG community summary。
- 生产 DB backfill、部署、canary、QZone transport/profile 变更。
