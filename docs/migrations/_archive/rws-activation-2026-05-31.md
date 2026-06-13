# RWS 激活 D3 实施清单 — 从空壳到名副其实的回复价值打分（已编码上线）

> 2026-05-31。落地 [rws-activation-plan-2026-05-31.md](../tracking/rws-activation-plan-2026-05-31.md) P1–P7 全量 + 开 flag 激活。承接前一会话「进行到中间中断」的半成品（P1 reward 回路 / P3 关系信号 / P4 thompson 已写但未收尾、未提交、测试/lint/类型红）。本清单四列 + D1 同模式 + D2 cancel-path + D4 证据。

## 0. 中断现场（接手时的事实）

- 新文件 `services/scheduler_rws/reward.py`（`RWSRewardQueue`）、`tests/test_rws_reward.py`、`docs/tracking/rws-activation-plan-2026-05-31.md`、`services/scheduler_rws/memory_signals.py` 全部 **untracked**，从未提交、维护日志无对应实施条目。
- `scheduler.py`/`config.py`/`bandit.py`/`rws.py`/`weights.py`/`__init__.py` 有未提交改动（混在 B 系列/S1/C0-C3/F-γ 其它未提交工作里）。
- **三处红**：① `bandit.py` 残留 `import time`/`_last_decay_ts`（ruff E + 死字段）；② `memory_signals.py` 8 个 pyright `object` narrowing 错；③ `test_admin_bandit.py`/`test_rws.py` 旧断言假设 epsilon 语义，被 P4 thompson 默认打破（2 failed）。
- config.json 只开了 `rws_shadow`/`rws_primary`，P1/P2/P3/P4 的 flag 全关 → RWS 在跑但 reward 回路空转、特征空置、bandit 不学。

## 1. 落地清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| **收尾** | bandit.py `import time` + `_last_decay_ts` 死代码 | 删除（decay 是 per-obs 非 time-based） | `services/scheduler_rws/bandit.py:19,40` | 清理 | ruff clean |
| **收尾** | memory_signals.py `getattr+callable` narrow 成 `object` → 8 pyright 错 | getattr 结果标 `: Any` + 调用点 `cast(Any, fn)(...)` | `services/scheduler_rws/memory_signals.py` | 类型修复 | pyright 0 errors |
| **收尾** | `test_rws_bandit_freeze_and_negative_reward_bounds_theta` 假设 epsilon `theta≈0.4` | 拆成 epsilon（显式 `algo="epsilon"`）+ thompson 方向测试（fire+负 reward → 升 theta） | `tests/test_rws.py:27` | 测试修正 | 3 测试过 |
| **收尾** | `test_admin_bandit` 断言 `theta<0.5` | 改 thompson 语义 `theta>0.5`（坏 fire 升 theta=少发） | `tests/test_admin_bandit.py:37` | 测试修正 | 过 |
| **P1/D2** | reward 队列无 cancel-path 测试 | 加 `test_settle_due_propagates_cancellation_without_double_observe`（CancelledError 必上抛、不重复 observe、不污染下次 run） | `tests/test_rws_reward.py` | 测试新增 | 过 |
| **P4** | bandit 构造写死类默认，config 无 algo/min_obs/decay 字段 | config 加 `rws_bandit_algo`/`rws_bandit_min_obs`/`rws_bandit_decay_per_obs`；scheduler 构造时 `_hstr_global`/`_hint_global`/`_hfloat_global` 读入 | `kernel/config.py:1465+`、`services/scheduler.py:292,1290` | 改 config + 接线 | config load 实测 + 构造实测 |
| **P5** | `compute_rws` 单 score / 单 theta | `RWSExplanation` 加 `im_score`/`interrupt_score`（同一组 term 拆 intent vs timing 双 sigmoid）；新增 `dual_decision()`；scheduler 在 `rws_dual_threshold` 开时按 role 选 interrupt 阈值（proactive 更高）gate | `services/scheduler_rws/rws.py`、`services/scheduler.py:735`、`kernel/config.py` | 改决策结构 | 4 P5 测试（双分、双门、proactive 更严） |
| **P6** | 无 RWS 看板、无 reward 只读 API | scheduler 加 `get_rws_reward_summary`（读 `rws_reward` runtime_metric + bandit state + flags）；bandit router 加 `GET /bandit/rws/summary`；SchedulerView 加 RWS 面板（flag chips + 4 stat） | `services/scheduler.py:428`、`admin/routes/api/bandit.py:33`、`admin/frontend/.../SchedulerView.vue` | 新增只读 API + 前端 | vue-tsc + build 过 |
| **P7** | 规则层/灰区边界靠隐式代码顺序 | notify 内插 `RULE LAYER`/`GRAY ZONE` 双标记注释固化边界 + 防回潮约束 | `services/scheduler.py:583,690` | 边界固化 | `TestP7RuleLayerBoundary` 2 测试（@bot 不算 RWS、非寻址才算） |
| **激活** | config.json 只开 shadow/primary | 开 `rws_reward`/`rws_eot`/`rws_hawkes`/`rws_bandit`(unfreeze)/`rws_dual_threshold` + 全部阈值显式写 | `config/config.json:395+` | 配置激活 | load 实测全 True + 构造实测 |

## 2. P1 reward 回路（已在中断前实现，本次收尾验证）

- 入队：`scheduler.py:760`(fire)/`:780`(skip) 两个概率分支末尾 `_enqueue_reward`，存 `PendingDecision(group_id, decision, t0, turn_baseline, rws_score)`。
- 结算 loop：`_rws_reward_loop`（poll = window/4）→ `RWSRewardQueue.settle_due(measure=_measure_reaction, observe=_observe_reward)`。
- 反应度量 `_measure_reaction`：读 timeline turn 增量——fire 后有新 turn=被理睬、无=致冷；skip 后有 turn=skip 对了、无=冷场。explicit_negative 暂留 False（禁言/制止 hook 未接）。
- reward 公式（P6 结构性硬负，写死在 `reward.py:compute_reward`）：`ack(+1.0) − cold(0.8) − neg(1.0)`，clamp[-1,1]。**ack 永远压不过 neg**（被理睬不能赎回被明确拒绝的发言 → 防 bandit 学挑衅，Pang et al.）。
- 回灌：`_observe_reward` → `RWSBandit.observe` + `record_runtime_metric(metric_key="rws_reward")` 落盘（复用 block_trace store，无新 schema）。

## 3. D1 同模式扫描

- `compute_rws` 全调用点：`scheduler.py:1303`（生产唯一）+ 测试。P5 双 score 是**additive**（新字段默认 0，旧 `score`/`decision` 不动），所有现有调用者行为不变。
- `RWSBandit.observe` 调用点：`_observe_reward`（P1 自动）+ `observe_rws_bandit`（admin 手动）。两者都走新 thompson/epsilon 分支，签名不变。
- `_hflag_global`/`_hfloat_global` 同模式新增 `_hint_global`/`_hstr_global`，同一 `getattr(humanization_config, ...)` 范式。
- eot/hawkes feature 注入点（`scheduler.py:1283/1296`）受 `rws_eot`/`rws_hawkes` flag 门控，关时回退 0.5/0.0——开关即生效，无新接线。

## 4. D2 cancel-path

- `_rws_reward_loop`：cancel 点是 `asyncio.sleep`；`settle_due` 的 per-item `except Exception` **不吞 CancelledError**（BaseException），cancel 干净上抛。
- `due()` 先 pop 再 settle → 部分结算被 cancel 时剩余项直接丢弃，不重复 observe、不污染下次 run。`test_settle_due_propagates_cancellation_without_double_observe` 断言：CancelledError 上抛 + 恰好 2 次 observe 尝试 + `pending_count()==0` + 再 settle 返回 0。
- `set_bot` 起的 `_rws_reward_task` 在 `close()` 被 cancel（`scheduler.py:922`）。

## 5. EOT / Hawkes / Bandit 集成验证（激活后无崩）

- `rws_eot=true`：scheduler `__init__` 自建 `EOTCache`/`EOTClassifier`（`:287`）；`_eot_probability` 只读 cache、miss 时 `loop.create_task(_refresh_eot)` 异步刷（非阻塞），无 loop/无 llm `_call` 时回退 0.5——优雅降级。
- `rws_hawkes=true`：chat plugin（`plugin.py:1502`）建 `HawkesCache` + `HawkesOfflineRefresher.start()` 并传入；`_hawkes_rho` cache miss 回退 `estimate_rho_from_times`。
- `rws_bandit=true`+`freeze=false`：scheduler 自建 `RWSBandit(algo=thompson, min_obs=50, decay=0.99)`；`current_theta` 在 `observations<min_obs` 时只用先验 base theta（冷启动不漂移）。
- 集成实测：全 flag 开构造 scheduler → reward_queue/bandit(thompson,unfrozen)/eot_classifier 全在、`get_rws_reward_summary().available=True`、close 干净。

## 6. 验证（D4 完成证据）

- **全量 `pytest`（D5 先 `pkill -9 -f pytest`）→ 2314 passed, 8 skipped, 0 failed**（中断时基线 2 failed → 全绿；净 +新增 RWS/dual/P7/cancel 测试）。
- **ruff**：All checks passed（全项目）。
- **pyright**：`services/scheduler_rws/`、`config.py`、`admin/routes/api/bandit.py` 0 errors。`scheduler.py` 残留 5 个 `slot.* reportOptionalMemberAccess` 是 `_do_chat` 深嵌套 narrowing 假阳性、**本次未触碰、C0/C1/C3 维护日志已记为既有 pyright 债**（顶部 `if slot is None: return` 已护），不在本次范围。
- **前端**：`vue-tsc --noEmit` clean；`npm run build` ✓ built（admin/static bind mount，D6——无需 rebuild bot 即生效）。
- **config 激活实测**：`load_config()` dump 15 个 RWS flag 全为目标值（reward/eot/hawkes/bandit=True, freeze=False, algo=thompson, dual=True + 阈值）。

## 7. 回滚

- 一键全退：`config.json humanization.rws_primary=false` → RWS 不再主决策，回固定阈值概率（`_rws_primary` gate）。
- 分阶退：`rws_reward=false`（队列空转）/ `rws_eot=false`（eot 回 0.5）/ `rws_hawkes=false`（rho 回 0）/ `rws_dual_threshold=false`（回单 score `rws.decision`）/ `rws_bandit_freeze=true`（theta 冻结）/ `rws_bandit_algo="epsilon"`（回旧学习器）。
- 纯内存 + 复用 block_trace `runtime_metric` 落盘，**无新 DB schema、无迁移**。
- config 走 bind mount，flag 回退无需 rebuild（改 .py 才需）。

## 8. 观察（上线后回看，非前置门）

- admin 调度器页 RWS 面板：`待结算/已结算`、`平均 reward`、`正向率`、bandit `θ/观测数` —— 确认 reward 回路真在结算（settled 增长）、bandit 在 min_obs 后开始动 theta。
- `rws_reward` runtime_metric：确认 fire/skip 的 reaction 被算成 reward 回灌、且 cold/neg 硬负在压制（无 reward-hacking 漂移）。
- 灰区 vs 规则层：`scheduler_rws_dual` 日志确认 @bot 走规则层不打分、非寻址才走 im/interrupt 双门。
