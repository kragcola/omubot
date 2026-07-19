# Memory Temporal Trace / False-Premise Awareness v1 Migration (2026-07-16)

> 状态：implemented and independently reviewed in code, **not deployed**.
> 范围：在普通 active-only 记忆上下文之外，按显式历史意图或错误前提注入
> current-first 的 supersedes 时间轨迹 sidecar。
> 非目标：修改 RRF/普通检索、历史 backfill、PPR/GraphRAG、QZone、NapCat、部署。

## Old -> New Mapping

| 面 | 旧 | 新 |
|----|----|----|
| 普通检索 | 仅 active card；历史链不可见 | **保持不变**；trace 不进入 `ContextHit` / RRF / full cache |
| 历史/前提问题 | 只能看到当前 active fact | 独立 `TemporalTraceAssembler` 输出 current / earlier / trajectory |
| 当前消息授权 | 依赖聚合 conversation/rewrite | `PromptContext.current_message` 为唯一触发/前提授权真值；rewrite 只辅助历史链匹配 |
| 群 scope | raw group_id 易漏 pool | 复用 `GroupMemoryConfig.resolve_group_pools()`，支持 pool / `__global__`，总 heads <= 24 |
| 链读取 | 无有界 primitive | active chain heads + head-first supersedes walk；depth <= 4，断链/跨 owner/category/cycle fail-closed |
| 证据 | 无历史 node refs | `card:` / `message:` / `obs:` typed refs；obs 每 card <= 2，refs 每 KP <= 12 |
| 输出预算 | 无 trace | KP <= 2、chars 100..600；低于 100 fail-closed，不截断说明/ref |
| Context 注入 | ordinary pack only | `记忆时间轨迹` dynamic block，priority 45，source `context_temporal_trace` |
| 配置 | 无 | `context.temporal_trace` restart-required；默认 enabled / 24 / 2 / 4 / 600 |

## Safety Contract

1. 普通 retrieval / memory pack 始终 active-only；superseded/expired 不回流普通上下文。
2. group chat 只读 resolved group/pool scopes；private 只读 `user/<user_id>`；不以 user 卡补群聊。
3. premise conflict 必须由 `current_message` 命中 earlier-only 多字符证据；`rewritten_query` 不得补授权。
4. head active、parent superseded、scope/scope_id/category 连续；broken/cycle/expired/cross-owner/cross-category 返回空。
5. `CancelledError` 传播；普通 Exception 降级为无 trace，不能留下 partial block。
6. Dream trusted fact->status correction 继续可写，但 temporal v1 对跨 category chain 保守 no-op。
7. `max_chars < 100` 的程序化错误配置 fail-closed；Pydantic 与 JSON schema 同时限制 100..600。

## Key Files

| 路径 | 变更 |
|------|------|
| `services/memory/temporal_trace.py` | **NEW** detector / scope / chain / DTO / typed refs / coherent render |
| `services/memory/card_store.py` | bounded observations、active chain heads、supersedes walk |
| `kernel/types.py` | `PromptContext.current_message` |
| `services/llm/client.py` | 单次解析最新人类消息并传入 PromptContext |
| `plugins/context/plugin.py` | Option A sidecar 与真实 startup wiring |
| `plugins/context/config.default.json` / `config.schema.json` / `plugin.json` | 配置、边界、restart、版本 0.1.10 |
| `tests/test_temporal_trace.py` | R1-R14、pool、premise、budget、startup integration |
| `tests/test_context_plugin.py` / `tests/test_card_store.py` | plugin gates、cancel、schema、store primitives |

## Verification

| 阶段 | 结果 |
|------|------|
| 初始 TDD | RED **40 failed, 88 passed** -> focused GREEN **128 passed** |
| 第一轮独立 review | 发现 group pool 与 current-state `还住上海` 两个 deploy blocker，另有 rewrite/refs/budget/integration 缺口 |
| 第一轮 correction | 新回归 **13 failed, 1 passed** -> focused **145 passed**；related **234 passed** |
| post-fix review | **0 Critical**；runtime minimum / startup evidence / false-green tests 进入第二轮修正 |
| 第二轮 correction + Codex tightening | focused **149 passed**；related smoke **94 passed**；Ruff clean；Pyright **0 errors** |
| 最终全仓 | **4116 passed, 17 skipped, 186 warnings** |

## Deployment / Observation

本切片未部署。后续若获授权，只 recreate bot，永不 recreate NapCat。上线后观察：

1. `context_temporal_trace` block 触发率、空结果率、异常降级率。
2. premise_conflict 中 earlier-only 命中是否只来自 current message。
3. group pool / `__global__` 下 trace 与 ordinary retrieval scope 是否一致。
4. block chars、KP/ref/observation 上限是否保持；普通 pack 是否仍无 superseded 内容。

## Rollback

1. 配置 `context.temporal_trace.enabled=false` 并 restart bot，即恢复零 trace 行为。
2. 代码回滚本文件 Key Files 的 temporal hunks；删除新 assembler 与测试。
3. CardStore 新 observation/chain 查询均为 read/additive API；无需删除 cards 或 backfill 数据。
4. 不 touch QZone、NapCat 或 live DB。
