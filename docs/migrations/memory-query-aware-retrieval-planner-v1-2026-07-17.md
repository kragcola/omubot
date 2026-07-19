# Memory Query-Aware Retrieval Planner v1 Migration

> 状态：离线代码验收完成；未部署、未 commit、未触 live DB / Docker / NapCat。日期：2026-07-17。

## 目标

在不改变 Thinker `retrieve_mode`、RetrievalGate、RRF/Card 排序权重、图跳数、Temporal Trace 授权、Episode 或存储 schema 的前提下，根据查询的确定性 need 调整 RRF 后类型占用和 prompt pack 多桶预算。

## 旧行为

- `ContextPlugin` 将 `rewritten_query || conversation_text` 与四态 `retrieve_mode` 直接交给 `ContextService`。
- `top_k=max_hits`；仅固定 `doc_chunk <= max_doc_hits`。
- pack 使用单一配置预算；查询意图不会调整 memory/doc/graph 桶占用。
- `ContextService` metrics 不记录 query plan。

## 新行为

- 新增纯同步 `services/context/query_plan.py`，闭集 need 最多两个：ordinary、preference、current/earlier temporal、premise、relation、broad recall、doc grounding。
- 英文 marker 使用单词边界；中文只保留较强短语，弱/歧义表达回退 ordinary identity。
- planner 永不 widening/narrowing `skip|doc|fact|hybrid`；RetrievalGate 仍为 Card 唯一权威 owner。
- ordinary 或关闭开关时保持原 `top_k`、原 budget 对象与 `{"doc_chunk": max_doc_hits}`。
- 非 identity profile 仅生成 RRF 后 `type_caps` 与 pack budget；bucket 总和不超过 `max(0, total_tokens-buffer_tokens)`。
- metrics 仅保留闭集 version/needs/profile/mode/top_k/type_caps/reasons/enabled/identity；未知字段丢弃，嵌套值重建，调用方后续 mutation 不影响记录。

## 配置

```json
{
  "query_aware_plan": {
    "enabled": true
  }
}
```

- 默认开启；插件版本 `0.1.11`。
- 配置属于 `restart_required_fields`。
- kill-switch：设为 `false` 后恢复 pre-v1 hit order / caps / pack 行为，仅保留 additive identity metrics。

## 文件清单

- `services/context/query_plan.py`
- `services/context/service.py`
- `services/context/__init__.py`
- `plugins/context/plugin.py`
- `plugins/context/config.default.json`
- `plugins/context/config.schema.json`
- `plugins/context/plugin.json`
- `tests/test_context_query_plan.py`
- `tests/test_context_plugin.py`
- `tests/test_context_service.py`

## 验收

- 首轮 RED：新 planner 不存在，约 22 个合同测试失败。
- 独立 pre-fix review：英文/中文 marker、定制预算、metrics 浅拷贝等 Important 均可复现。
- remediation RED：四类 Important 的 adversarial tests 全部先失败，再转绿。
- focused：`251 passed`；最终 sanitizer/marker 补强：`119 passed`。
- 全仓：`4543 passed, 17 skipped, 188 warnings`。
- Ruff clean；生产 Pyright `0 errors`；`git diff --check` clean。
- post-fix Grok review：`0 Critical / 0 Important，ACCEPT`。

## NO-GO / 残留

- 不宣称改变底层 source recall/ranker；`type_caps` 仍在 RRF 后生效，budget 只影响 pack。
- 不做 query rewrite、RetrievalGate tier/full-periodic、RRF/Card weight、PPR、GraphRAG、MemGPT tools、Episode 合并或 schema migration。
- marker 规则刻意保守；未识别表达回退 ordinary，不冒充官方 LongMemEval/LoCoMo/HippoRAG 指标。
- 生产时延、profile 分布与真实对话收益仍需部署窗观测。

## 回滚

1. 首选将 `query_aware_plan.enabled=false` 并仅重启/recreate bot；不触 NapCat。
2. 代码回滚可移除 `ContextPlugin` planner 接线、`ContextService.plan_meta`、query_plan 模块及对应测试/配置项。
3. 无数据库迁移，无数据回滚。
