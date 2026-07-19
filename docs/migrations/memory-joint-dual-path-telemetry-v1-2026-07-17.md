# Joint Dual-Path Memory Telemetry v1 (jdt_v1)

> 状态：离线终验通过；全仓 **4737 passed / 17 skipped / 186 warnings**；独立 post-fix review **0 Critical / 0 Important / 0 Minor，ACCEPT**。未部署、未 commit、未触 live DB / Docker / NapCat / QZone；`BUILTIN_WIRE_PROFILE.validated=false`。

## 目标

ContextService 与 EpisodeProvider 是两条有意平行的 prompt 路径：前者经 RRF、type caps、PEG、pack 与 EUC 后由 ContextPlugin 注入，后者经 ProviderBus 直接产生 `PromptBlockCandidate`。两者最终在 `PromptBudgetManager` 的同一 request budget 中竞争，但旧 Admin 只能分别查看 Context metrics 与 block traces，无法回答：

- Context 主资料与 Episode 是否同时出现、是否同时幸存；
- 哪一条路径被 accepted / trimmed / rejected；
- Context 主资料被预算丢弃时 Episode 是否仍幸存；
- EUC 约束块与 Episode 是否同时进入最终 prompt。

`jdt_v1` 只补只读联合视图，不合并两条检索/排序路径，不改变 prompt 内容或预算决策。

## 前沿与同类项目依据

- LongMemEval / LongMemEval-V2 将长期记忆拆为 indexing、retrieval、reading/evidence-use；Omubot 需要区分“检索/pack 已产生”与“最终 prompt 预算后仍可见”。
- MemTrace 强调定位知识点在检索、证据可达与实际使用阶段的失败；`jdt_v1` 只定位预算汇合阶段，不声称答案已经正确使用证据。
- Graphiti / Zep 强调 episode provenance 与 temporal graph 的可追溯性；本切片沿用闭集 provenance 计数，不暴露原始 evidence ref。

本地只借鉴能力维度，不声称 LongMemEval、LoCoMo、MemTrace、Graphiti 或 Zep benchmark parity。

## Codex 合同收敛

初始候选是在 `LLMClient` 的 Context/Episode 汇合点新增 request-scoped telemetry。代码复核后收敛为更小且更诚实的所有权：

- `PromptBudgetManager` 已把每个候选的真实 accepted / trimmed / rejected 决策写入 `prompt_block_traces`，并共享同一 `request_id`。
- 因此 `jdt_v1` 不在聊天热路径新增写入，也不重复发射第二份事实。
- `BlockTraceStore` 以一次有界只读 SELECT 选取最近相关 request，并由纯模块生成闭集联合视图。
- 无 DB schema migration；现有 trace 写入、retention、`/stats`、`/alignment` shape 保持不变。

## 新行为

### 纯分类

`services/block_trace/joint_telemetry.py` 只识别四个闭集 role：

| trace source | role |
|---|---|
| `context` | `context_main` |
| `context_temporal_trace` | `context_temporal_trace` |
| `context_evidence_use` | `context_constrained` |
| `episode` | `episode` |

- decision 只接受 `accepted | trimmed | rejected`；`accepted` 与 `trimmed` 视为 survived。
- `shadow_only`、未知 decision、未知 source 和畸形行全部 fail-closed，不进入 present、sample 或动态 metric key。
- outcome 闭集：`both_present`、`both_survived`、`context_only_survived`、`episode_only_survived`、`neither_survived`、`context_main_dropped_episode_survived`、`context_main_absent_episode_survived`、`constrained_and_episode_survived`。

### Store 所有权

`BlockTraceStore.joint_dual_path_snapshot(limit)`：

- kill-switch 关闭时返回 exact disabled zero shape，且不执行 SELECT；
- 开启时以单个 CTE SELECT 选取最近 1..200 个非空、含闭集 source + decision 的 request；
- 同一次查询返回这些 request 的完整相关行，避免 `recent(limit)` 截断半个 request，也避免 N+1 `list_for_request`；
- SQL 只投影 `request_id/source/decision/created_at`，随后再次缩成闭集纯输入；
- 不写数据库，不改变 schema version v1。

### Additive Admin API

`GET /api/admin/block-trace/joint-memory-paths?limit=50` 只调用 store 公共 snapshot：

```json
{
  "ok": true,
  "snapshot": {
    "version": "jdt_v1",
    "enabled": true,
    "sample_size": 0,
    "decision_totals": {
      "context_main": {"accepted": 0, "trimmed": 0, "rejected": 0},
      "context_temporal_trace": {"accepted": 0, "trimmed": 0, "rejected": 0},
      "context_constrained": {"accepted": 0, "trimmed": 0, "rejected": 0},
      "episode": {"accepted": 0, "trimmed": 0, "rejected": 0}
    },
    "outcome_counts": {},
    "recent": []
  }
}
```

实际 `outcome_counts` 始终返回完整闭集零值键。普通异常只返回 `joint_snapshot_failed:<ExceptionType>` 并仅按异常类型记录 warning；`asyncio.CancelledError` 继续传播。

## 隐私与指标边界

允许字段只有闭集 version/enabled/counts/flags。Store 可在内部用 `request_id` 分组和排序，但响应不得包含或透传它，因为运行时 request ID 嵌入 `group_<群号>` 或 `private_<用户号>` session 标识。响应同时不得包含：

- query、content、text、label、hit_reason；
- group/user/session 标识；
- candidate/evidence ID 或 refs、quote；
- provider、metadata、budget_reason；
- 任意动态 source、decision 或攻击者控制的 metric key。

这些指标描述“现有 prompt block trace 中两条记忆路径的预算结果”，不证明回答 grounded，也不提供 candidate-to-fact conversion rate。

## 配置与回滚

```toml
[block_trace]
joint_dual_path_telemetry_enabled = true
```

- 设置 `false` 后仅 restart/recreate **bot**：endpoint 返回 disabled zero shape，跳过联合 SELECT。
- 不关闭既有 block trace 写入、retention、`/stats` 或 `/alignment`。
- 无 DB migration、数据回滚或 NapCat 操作。

## 旧 -> 新

| 位点 | 旧 | 新 |
|---|---|---|
| Context / Episode 联合结果 | 只能人工拼接两个 API | 同 request 闭集预算联合视图 |
| 汇合点观测 | 已有 trace，但没有联合 read model | 复用现有 trace，不重复写 |
| request 采样 | `recent(limit)` 可能截断 request | latest relevant request CTE + 完整相关行 |
| shadow / 未知 decision | 容易误当真实 prompt 路径 | fail-closed，不进入 sample/present |
| 隐私 | 原 trace API 可查看完整诊断字段 | jdt payload 严格闭集、secret-free |
| rollback | 需移除代码 | config kill-switch，零 schema 影响 |

## 文件清单

| 路径 | 变更 |
|---|---|
| `services/block_trace/joint_telemetry.py` | 新增纯分类、闭集聚合与 disabled shape |
| `services/block_trace/store.py` | 配置开关 + 一次有界 SELECT 的公共 snapshot |
| `services/block_trace/__init__.py` | 导出 jdt 公共纯接口 |
| `admin/routes/api/block_trace.py` | additive joint-memory-paths route |
| `kernel/config.py` | `BlockTraceConfig` 默认开关 |
| `bootstrap/chat_runtime.py` | composition root 透传 |
| `tests/test_joint_dual_path_telemetry.py` | 纯层、Store、Admin、配置与回归 TDD |
| 本文档 / tracker / maintenance | 合同、验收、回滚与残留 |

## TDD Ledger

1. Grok 按纯层 -> Store -> Admin/config 纵向切片完成初始 RED/GREEN；中断前 focused **22 passed**、Ruff clean。
2. Codex 独立 review RED：未知 decision 仍把 path 标为 present、shadow-only/空 request 占用 sample、Loguru `%s` 模板不生效：筛选命令 **3 failed**。
3. 根因修正后同筛选 **3 passed**：present 仅在闭集 decision 后成立；Store CTE/outer SELECT 同时滤空 request 与未知 decision；warning 改为 Loguru `{}` 模板。
4. Codex 第二个同模式 RED：纯聚合器接收 unknown-only group 时仍产出虚假 `neither_survived` sample：**1 failed**。
5. 聚合器仅接受至少一个闭集决策的 request 后同筛选 **1 passed**。
6. 独立初审：**1 Critical / 0 Important，REJECT**。Critical：公开 recent 原样返回 request ID，而 runtime request ID 内嵌 group/private session 标识；测试还错误允许该字段。
7. 隐私合同修正 RED：hostile/runtime request ID 必须完全不出现在 JSON，recent 只允许 `decision_totals/outcomes`：筛选 **3 failed**；移除公开 request ID 后 **3 passed**。
8. post-fix 独立 review：**0 Critical / 0 Important / 0 Minor，ACCEPT**；新 jdt **24 passed**。
9. Codex focused（jdt + BlockTrace + Admin retention + application/bootstrap）**56 passed**；全仓 **4737 passed / 17 skipped / 186 warnings**；jdt 相关 Ruff clean；targeted Pyright **0 errors / 0 warnings**；`git diff --check` clean。
10. 全项目 Ruff / Pyright 当前分别被既有无关 dirty/untracked coursework、research、IPv6 工具等 **179 / 378** 项挡住；不把它们误报为 jdt 绿灯，也不在本切片修改。

## 残留风险

- 联合视图依赖现有 block trace retention；被清理的历史 request 不可恢复。
- `jdt_v1` 不包含 ContextPack 的精确 `pack_state`，只观察最终生成的 Context 主块、TemporalTrace、EUC 约束块和 Episode block；这是有意避免扩大 PromptBlock ABI/热路径写入。
- `trimmed` 表示 block 幸存，但现有 trace 的 `char_count` 是原始长度；v1 不伪报 trimmed 后的精确剩余字符。
- Admin 查询有 request 上限，但仍需在真实规模部署后观测 SQLite 延迟；无数据前不新增索引/schema/cache。
- 当前相关记忆切片均未部署，真实 dual-path drop rate 仍需授权部署后以脱敏聚合验证。

## NO-GO

- 将 Episode 合并进 Context RRF / type caps / PEG / EUC
- 修改 PromptBudgetManager priority、预算或 accepted block list
- 修改 Query-Aware、TemporalTrace、Episode decay/rerank、graph hops
- 新 trace 写入、DB schema migration、bulk backfill、live repair
- PPR、GraphRAG 扩展或用本地 synthetic/telemetry 计数过门
- 官方 LongMemEval/LoCoMo 下载、parity 分数或 answer-side LLM judge
- 部署、commit、push、QZone canary、NapCat 操作、`validated=true`

PPR 继续 NO-GO，直至已部署真实脱敏 graph population、evidence-quality、latency 与 prompt-path evidence 存在。
