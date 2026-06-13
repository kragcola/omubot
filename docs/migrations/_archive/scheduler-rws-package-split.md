# Scheduler RWS Package Split

Date: 2026-05-26

Part 3.5 主线原计划把 RWS / Hawkes / EOT 放入 `services/scheduler/` package，但当前仓库已有 `services/scheduler.py` 单文件热路径。为缩小 blast radius，本轮不做单文件到 package 的 D3 大迁移，而是新增同级 package。

| 原计划路径 | 实际路径 | 迁移原因 | 运行接线 |
|---|---|---|---|
| `services/scheduler/rws.py` | `services/scheduler_rws/` | 避免与现有 `services/scheduler.py` 模块名冲突 | `GroupChatScheduler` 在 `humanization.rws_shadow` / `rws_primary` 打开时调用 |
| `services/scheduler/hawkes_offline.py` | `services/scheduler_hawkes/` | 保留 scheduler 热路径单文件，离线 cache 独立演进 | `ChatPlugin` 在 `humanization.rws_hawkes=true` 时启动 `HawkesOfflineRefresher` |
| `services/scheduler/eot_classifier.py` | `services/scheduler_eot/` | EOT 是 LLM task 调用层，不与 scheduler 类混放 | `GroupChatScheduler` 后台预热 `scheduler_eot` cache，当前轮 fallback 0.5 |
| `services/scheduler/counterfactual_replay.py` | `services/scheduler_replay/` | replay 属离线报表/评审域，不进热路径模块 | `/api/admin/replay/weekly` 读取 `ReplayStore` run summary |

Rollback: all feature flags default off. To return to Wave 1 behavior, keep `humanization.rws_shadow=false`, `rws_primary=false`, `rws_hawkes=false`, `rws_eot=false`, `rws_bandit=false`, `counterfactual_replay=false`, `pass_turn_confidence_gate=false`.
