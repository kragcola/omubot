# Omubot 中期架构 Phase 0：正确性与回放基线

> 状态：done
> mode: task-bug
> 最后更新：2026-07-12 CST
> 当前下一步：Phase 0 完成；中期 Phase 1 Composition Root 另行启动。
> 阻塞：无。
> 回滚入口：仅回退本 tracker 所列 TopicBlock、测试和回放基线增量；不操作 NapCat。

## Resume Capsule

- objective: 在启动 Composition Root 前，先关闭已实证的 TopicBlock 状态错误，并建立不依赖真实群原文入库的最小行为回放门禁。
- next_step: 以本 tracker 与迁移清单为基线，另立 Phase 1 Composition Root tracker。
- current_files: `services/group/topic_block.py`, `tests/test_topic_block.py`, `tests/topic_block_replay_runner.py`, `tests/test_topic_block_replay.py`, `tests/fixtures/topic_block_replay/v1/core.json`。
- last_verified: 相关 134 passed；全量 2871 passed / 17 skipped；targeted Ruff/Pyright 0；独立复审无 Critical/Important。
- do_not_redo: 不重新审计同类项目/论文；不改 research schema、stable block UUID、在线参数或群策略。
- rollback: TopicBlock 修复保持纯内存、无 schema/config 变更；回退对应代码和测试即可。

## Scope

1. 修复全量冷却后新块写入失去注册表所有权。
2. 修复达到 `max_blocks` 时新块在首次 activity 计数前被错误淘汰。
3. 修复 reservoir 引用复活一次 arrival 被累计两次 activity。
4. 建立最小确定性回放 fixture/runner，锁定消息边、归属结果和关键不变量；真实准确率评测等待双标注数据。

## Out Of Scope

- 不提取 Composition Root，不拆 Router/Scheduler/LLMClient。
- 不改 `research_message_event` schema，不实施 Phase 2 stable block UUID/versioned assignment。
- 不调整 L1 权重、衰减参数、候选阈值或主动回复概率。
- 不部署新的记忆算法、向量库或多 Agent runtime。
- 不修改公开群 `silent_learn` 与开发群 allowlist。

## Root Cause

| ID | 现象 | 根因 |
| --- | --- | --- |
| B1 | 新块在 `_msg_to_block` 中可见，但 active/reservoir 均不可达 | `observe()` 在 `_active()` 前缓存 `group_blocks`；全冷却时 `_active()` 从 `self._blocks` 删除该字典，后续新块写进失去注册的旧引用 |
| B2 | 容量满时新 arrival 直接进入 reservoir | `while len(active_now) > max_blocks` 发生在 `_apply()` 首次 activity bump 之前，新块以 0 activity 参与淘汰 |
| B3 | reservoir 复活单条消息 activity 增加 2 | `_revive_from_reservoir()` 与 `_apply()` 都调用 `_bump_activity()` |

## Acceptance Gates

- [x] 三个行为切片各自有明确 RED，再转 GREEN。
- [x] 每个已索引 message ID 指向 active 或 reservoir 中真实存在的 block。
- [x] 一条 arrival 对目标 block 的 activity 只增加一次。
- [x] 超容量淘汰在当前 arrival 完整落入目标 block 后执行，并按最终 activity 选择冷块。
- [x] 现有 reply/@/L1/reservoir/anchor 行为测试保持通过。
- [x] 回放基线不保存真实群号、QQ、原始正文或 HMAC secret。
- [x] 定向测试、相关 scheduler/research 回归、Ruff、targeted Pyright、全量 pytest 通过。
- [x] 独立代码复审无 Critical/Important。
- [x] 仅替换 `qq-bot`；公开 silent 群出站为 0；NapCat 身份与 restart count 不变。

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T0 | 三段 `TopicBlockTracker` 无写入状态模拟 | B1=`b2` active/reservoir 均 False；B2=`b2` 首次 arrival 即进 reservoir；B3 activity delta=`2.0` | 三个问题均可重复，根因位于 `observe()` 的注册、应用、淘汰与复活顺序 | 2026-07-12 |
| T1 | `uv run pytest -q tests/test_topic_block.py -k new_block_after_full_decay_stays_registered` | RED: `KeyError: 'g1'`; GREEN: 1 passed | 新块仅在需要创建时重新取得 canonical registry，B1 关闭 | 2026-07-12 |
| T2 | `uv run pytest -q tests/test_topic_block.py -k capacity_eviction_counts_current_arrival_first` | RED: `KeyError: 'b2'`; GREEN: 1 passed | 当前 arrival 完整 apply 后再做容量治理，B2 关闭 | 2026-07-12 |
| T3 | `uv run pytest -q tests/test_topic_block.py -k reservoir_revival_counts_arrival_once` | RED: actual `2.000000002...`, expected `1.000000002...`; GREEN: 1 passed | revive 只迁移池归属，activity 由 apply 单一计数，B3 关闭 | 2026-07-12 |
| T4 | `uv run pytest -q tests/test_topic_block.py` + registry/bump/eviction `rg` | 27 passed；`_bump_activity` 业务调用仅剩 `_apply()` | 三个修复与现有 L0-L2、reply/@、anchor 行为兼容；同模式计数所有权已收口 | 2026-07-12 |
| T5 | `uv run pytest -q tests/test_topic_block_replay.py` | RED: `case_count=0`；首次实现后 3 个合成不同话题因共享 `topic_` n-gram 被归为同块；修正 fixture 输入后 GREEN: 2 passed | 6 cases / 13 events 的匿名确定性回放成立；runner 不读写生产 DB、不回显正文 | 2026-07-12 |
| T6 | 复审 Important RED：runner 禁止在 observe 外推进 tracker | RED: `runner mutated tracker before observe`; GREEN: replay 3 passed | runner 改用纯计算 decay baseline；B1 回放不再因测试预变异产生假绿 | 2026-07-12 |
| T7 | topic/replay/scheduler/arbiter/research 相关回归 + targeted Ruff/Pyright | 134 passed；Ruff passed；Pyright 0 | 修复、匿名回放与回复/采集主链兼容 | 2026-07-12 |
| T8 | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -vv` | 2871 passed / 17 skipped / 130 warnings in 42.20s | 全量回归通过；终端 PTY 未回显汇总，已从完整 `/tmp/omubot-phase0-full-pytest.log` 核对到 100% | 2026-07-12 |
| T9 | 独立两轮代码复审 | 首轮 2 Important / 2 Minor；修复后无 Critical/Important，3 Minor 随后关闭 | runner 假绿与隐私门禁问题关闭；实现状态顺序未发现新缺陷 | 2026-07-12 |
| T10 | 全仓 `uv run ruff check` | 177 个既有错误，集中于 `docs/coursework`、`research/`、`tools/ipv6/` 等本次外文件 | 全仓 Ruff 基线非绿且与本改动无关；本次文件 targeted Ruff 为 0 | 2026-07-12 |
| T11 | Docker SHA/import/state smoke + 启动日志/公开群负向窗口 | host/staging/runtime SHA=`74f3bc...`; 三场景 smoke ok；OneBot/capture/guard/trace ok；6 条公开群文本标记 silent_learn，send/scheduler/Traceback/ERROR=0 | image `5bd0e04205cf` / container `9f7a27bee21c` / restart 0；NapCat `19f6cf13607c` 身份与 StartedAt 不变 | 2026-07-12 |

## Migration Checklist

见 `docs/migrations/architecture-mid-term-phase0-2026-07-12.md`。

## Next Session Starts Here

- Direction: Phase 0 已实现、验证并上线；下一阶段是独立 Composition Root。
- First action: 另立 Phase 1 tracker，先锁启动顺序、逆序关闭和部分启动失败清理。
- Open questions: 真实 graph-F/F1、错并错拆率和阈值调优等待双标注数据。
- Do not redo: 外部同行和论文对比已完成，结论见 2026-07-12 架构审计对话与历史审计文档。
