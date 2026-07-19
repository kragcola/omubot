# Memory Card Category Time Eligibility v1 Migration

> 状态：离线代码验收通过；未部署、未 commit、未触 live DB / Docker / NapCat。日期：2026-07-17。

## 目标

为 prompt recall 增加**读时、非破坏性、fail-closed** 的 Card 类别日历 TTL 过滤，仅影响 RetrievalGate 与 TemporalTrace active-head 路径；Admin / Dream / list / tool 历史视图与 `CardStore` 直接 API 不变。

## 旧行为

- `RetrievalGate` 对 active cards 做 full / keyword / semantic / count，无类别时效。
- `TemporalTraceAssembler` 以 active chain head 为入口，不检查日历年龄。
- `ttl_turns` 为 schema 保留字段，未被解释。

## 新行为

- 共享纯模块 `services/memory/card_eligibility.py`：
  - 不可变 `CardEligibilityPolicy`（`enabled` + `category_ttl_days`）
  - `parse_card_anchor_timestamp`：offset-less → `Asia/Shanghai`；显式 offset / `Z` 保留；畸形非空 → `None`（衰减类 fail-closed）
  - 锚点：`updated_at` 优先，空则 `created_at`
  - 默认仅 `status=30d`、`event=180d` 衰减；`preference` / `boundary` / `relationship` / `promise` / `fact` 无自动过期
  - **不**解释 `ttl_turns`；**不**写 status / 无 schema 列 / 无 backfill
- `RetrievalGate`：full 缓存存 raw active 集合，**每次读再过滤**；keyword / semantic / `_count_active` 同政策；`total_active` / `matched_active` 仅计合格卡
- `TemporalTraceAssembler`：仅过滤 active heads；按硬上限 24 扫描候选后再做 eligibility，`walk_supersedes_chain` 父节点不被 TTL 剔除（轨迹保留）
- keyword 路径复用 shared eligible full-cache，避免过期高优先级行占据 SQL `LIMIT` 后饿死新匹配；不改变 RRF / Card score 权重
- kill-switch：`plugins/context` → `card_eligibility.enabled=false` 时与 pre-v1 active-only **identity**
- 插件版本 `0.1.12`；配置 restart-required

## 配置

```json
{
  "card_eligibility": {
    "enabled": true,
    "status_ttl_days": 30,
    "event_ttl_days": 180
  }
}
```

- TTL 运行时夹紧到 `[1, 3650]`。
- 默认 offline 代码开启。

## 文件清单

- `services/memory/card_eligibility.py`（新建）
- `services/memory/retrieval.py`
- `services/memory/temporal_trace.py`
- `plugins/context/plugin.py`
- `plugins/context/config.default.json`
- `plugins/context/config.schema.json`
- `plugins/context/plugin.json`
- `tests/test_card_eligibility.py`（新建）
- `tests/test_context_plugin.py`（版本与配置合同）
- `tests/test_temporal_trace.py`（malformed head budget 与父链保留回归）
- 本迁移清单

## NO-GO / 残留

- 不改 RRF / Card score 权重、Query-Aware planner、graph hops、Episode 检索。
- 不实现 pack-time confidence gating、PPR、GraphRAG、MemGPT tools。
- 不改 `CardStore` list API；不部署；不触 live DB。
- recency-score 时区/排序行为不变（仍用既有 UTC naive 约定）。

## 回滚

1. 首选：`card_eligibility.enabled=false` 后仅 restart/recreate **bot**（不触 NapCat）。
2. 代码回滚：移除 eligibility 模块接线与配置字段、版本回退 `0.1.11`。
3. 无数据库迁移，无数据回滚。

## 验收证据

- focused eligibility / CardStore / RetrievalGate / TemporalTrace / ContextService / ContextPlugin：**252 passed**
- 全仓 pytest：**4581 passed, 17 skipped, 186 warnings**
- Ruff touched scope：clean；生产 Pyright：**0 errors**；`git diff --check`：clean
- 独立 Grok review：**0 Critical / 0 Important，ACCEPT**；keyword starvation 与 TemporalTrace head-limit 边界均复核
