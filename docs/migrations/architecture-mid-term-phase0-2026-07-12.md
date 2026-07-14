# 中期架构 Phase 0 迁移清单

> 状态：active（2026-07-12）
> 原则：先修纯内存正确性，再建立回放门禁；不改变 schema、配置、群策略和运行拓扑。

## 旧到新

| ID | 旧行为 | 新行为 | 兼容与回滚 |
| --- | --- | --- | --- |
| B1 | 全冷却后可能向失去注册的旧 dict 写新块 | `_active()` 后重新取得 canonical group registry，新块始终可达 | 纯内存改动；回退 `observe()` 注册顺序 |
| B2 | 新块以 0 activity 参与超容量淘汰 | 当前 arrival 完整 `_apply()` 后再执行容量治理 | 不改变 `max_blocks` 或淘汰评分；回退调用顺序 |
| B3 | reservoir revive 与 apply 各 bump 一次 | revive 只迁移所有权，arrival 只在 apply bump 一次 | 不改变 decay 公式；回退 revive helper 即可 |
| R1 | 仅有零散单元场景 | 增加匿名、确定性、版本化的 TopicBlock 回放 fixture/结果 | 不读取或写入生产研究 DB；删除 fixture/runner 即回退 |

## 顺序

1. B1 RED/GREEN，验证 block/index 所有权不变量。
2. B2 RED/GREEN，验证 apply-before-evict。
3. B3 RED/GREEN，验证 one-arrival-one-bump。
4. 相关回归全部绿色后才建立 R1，不让回放设施掩盖底层错误。

## 同模式扫描

- 扫描 `_blocks` / `_reservoir` 字典被 pop 后是否仍保留局部引用。
- 扫描所有 `_bump_activity()` 调用，确认同一 arrival 只有一个计数所有者。
- 扫描所有容量/retention 淘汰，确认对象完整写入后再比较。
- 扫描 `_msg_to_block` 清理和 reset，确认不存在悬空 block ID。

## 回滚与部署

- 无数据库迁移、配置迁移或 down migration。
- 单测失败可逐切片回退 B1/B2/B3，不影响研究采集库。
- 运行态验证只允许重建 `qq-bot`；不得重启或 recreate NapCat。
- 部署前后记录 qq-bot/NapCat container ID、Created、StartedAt 与 restart count。
