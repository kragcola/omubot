# Dialogue Climate 后续整合与低优先级缺陷收口

> 状态：active
> mode: task-bug
> 最后更新：2026-07-15 CST
> 当前下一步：精确提交实现，创建 rollback image tag 后执行 bot-only build/recreate 与运行态验收。
> 阻塞：无。
> 验证证据：full pytest 3446 passed / 17 skipped；任务文件 Ruff clean、Pyright 0、JSON/diff-check clean；最终独立 review 0 Critical / 0 Important。
> 回滚入口：关闭 `dialogue_climate.m4_policy_enabled` 可熄火 prompt/adapter 消费；代码部署只允许 bot-only recreate，NapCat 不得重建。

## Resume Capsule

- objective: 完成 PromptProviderBus 让位、affection+climate 单 block、Humanizer/Thinker adapter、MessageSensor 生产分类器、durable baseline/state、M1 tension 完全退役，并修复 persona drift 尾字残留。
- next_step: 精确 stage/commit；创建 rollback image tag 后 bot-only build/recreate，完成 provider/store/M1/静默群/NapCat 运行验收；取得真实部署证据后再写 maintenance-log。
- current_files: `services/dialogue_climate/`、`plugins/chat/plugin.py`、`plugins/schedule/`、`plugins/affection/plugin.py`、`services/humanizer.py`、`services/llm/`、`bootstrap/chat_runtime.py`、相关 tests/docs/config。
- last_verified: provider/snapshot/classifier/adapter/durable baseline/M1 retirement 已实现；两轮 reviewer Important 与 persona boundary Minor 均关闭；最终 review 0/0，full pytest 3446 passed，scoped static 全绿。
- do_not_redo: 不重做 M2/M3/M4 架构审计，不重新设计 per-(group,user) key，不碰 NapCat，不用 `git add -A`，不覆盖 character-pack/deep-delivery/coursework 等既有 dirty 内容。
- rollback: 代码回退本任务提交并 bot-only rebuild/recreate；持久化必须可停写且不删除既有 metrics/raw 数据。

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | ACTIVE、Part A §10.2/§10.4、M3/M4 plan、handoff 已恢复 | 无 |
| Plan | done | 六切片与 D3 清单已执行 | 无 |
| Implementation | done | provider/classifier/adapters/store/M1 retirement/persona + review fixes 完成 | 无 |
| Verification | in_progress | full pytest 3446；Ruff/Pyright/JSON/diff clean；最终 review 0 Critical / 0 Important | 运行态验收 |
| Handoff | pending | 任务未完成 | 完成后清 ACTIVE |

## Next Session Starts Here

- Direction: 不再改架构；只做精确提交、bot-only runtime validation 与部署证据回填。
- First action: 执行 D7 preflight 与精确提交；maintenance-log 必须等真实 image/container/观察窗证据产生后再写。
- Open questions: 无实现问题；仅待部署后的真实消息/post_reply/store/M1 停写与 silent 群观察窗证据。
- Do not redo: 不再证明 PromptProviderBus 已存在、不再调查 MoodClassifier 是否有生产调用、不再讨论是否保留 M1 双写；这些结论已成立。

## Todo

- [x] 恢复仓库、ACTIVE、历史实现与既有 dirty worktree。
- [x] 修复 `我是凤笑梦呀` 尾字残留并完成 focused 验证。
- [x] 创建 tracker、D3 迁移清单并冻结共享 contract。
- [x] MessageSensor 接生产 MoodClassifier，且不污染 `MOOD_CURRENT_SLOT`。
- [x] 发布 climate state/policy snapshot，注册 ClimateProvider，合并 affection+climate 单 block 并让旧 block 让位。
- [x] Thinker 消费 `reply_bias`，Humanizer 消费 `delay_multiplier`。
- [x] durable 保存/恢复 slow baseline，覆盖 schema/CAS/cancel/关闭路径。
- [x] ClimateEngine 成为 tension 唯一 owner，迁走 dream/schedule 消费并删除 legacy M1 state。
- [x] D1 同模式扫描、首轮独立 review、focused + full pytest、Ruff、Pyright、diff-check。
- [x] post-fix 独立 review 无 blocker/important。
- [ ] bot-only build/recreate、运行态与公开群零发送碰撞验证、NapCat 身份不变验证。
- [ ] 更新 Part A、maintenance-log、ACTIVE 并精确提交任务文件。

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| 状态主键 | `(group_id, user_id)` | 沿用已冻结 F1 与现有 ClimateEngine | 2026-07-15 |
| tension owner | 仅 ClimateEngine | 双写会产生漂移与不可解释状态 | 2026-07-15 |
| classifier 输出 | 直接传 SensorHub/专用 climate snapshot，不写 `MOOD_CURRENT_SLOT` | 避免与 sticker/mood 公共槽语义碰撞 | 2026-07-15 |
| prompt 输出 | provider-bus 上唯一关系/气候 candidate | schedule/affection 并列 block 会重复甚至冲突 | 2026-07-15 |
| adapter 输入 | 同一 resolved state + policy snapshot | 避免 prompt、Thinker、Humanizer 各自重新 resolve | 2026-07-15 |
| poke 身份保证 | 同一事件对象重复解析/dispatch 幂等；不同到达对象分别处理 | OneBot/NapCat 无稳定 notice ID，不能同时区分跨反序列化 replay 与同秒完全相同的合法 poke | 2026-07-15 |
| 部署范围 | 只 rebuild/recreate bot | NapCat 设备指纹与登录态红线 | 2026-07-15 |

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `services/llm/persona_drift_stripper.py` | bot-name 声明吸收紧邻 `呀` 与尾标点 | done, uncommitted |
| `tests/test_persona_drift_stripper.py` | 尾字回归测试 | done, uncommitted |
| `docs/tracking/dialogue-climate-integration-completion-2026-07-15.md` | active tracker | implementation done, deploy pending |
| `docs/migrations/dialogue-climate-runtime-completion-2026-07-15.md` | D3 old-to-new checklist | implementation done, deploy pending |
| `docs/tracking/ACTIVE.md` | 当前任务指针 | in_progress |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| persona RED | `uv run pytest tests/test_persona_drift_stripper.py::test_strip_declarations_fails_closed_on_lone_name_claim_with_particle -q` before fix | failed: actual `呀` |
| persona GREEN | same focused test after fix | 1 passed |
| persona regression | persona/overshare/sentinel focused suite | 27 passed |
| persona static | focused Ruff + Pyright + `git diff --check` | clean |
| integrated focused | Dialogue Climate/provider/store/scheduler/sentinel 组合；线程异常 warning 升格为 error | 344 passed |
| final reviewer | 最终 stable snapshot focused review | 139 passed；0 Critical / 0 Important |
| full pytest | `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest -q` | 3446 passed, 17 skipped |
| task static | task Python files Ruff + Pyright | clean / 0 errors |
| config/diff | 3 schedule JSON + tracked/untracked diff check | clean |
| warning diagnostic | full pytest 将 `PytestUnhandledThreadExceptionWarning` 升格为 error | 暴露 command/retrieval/router B-cluster 既有 fixture 连接清理债；本任务 focused 范围已清零，不扩大全仓范围 |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| PD-1 | `strip_declarations("我是凤笑梦呀", bot_name="凤笑梦")` before fix | cleaned=`呀` | bot-name prefix regex did not absorb adjacent sentence particle | 2026-07-15 |
| PD-2 | same input after fix | cleaned=`""`, matched original | root-cause fix works and fail-closed behavior is restored | 2026-07-15 |
| DC-R1 | long-idle energy peak 30d before fix | energy/baseline=`0.523687` | drift-before-decay 固化瞬态峰值 | 2026-07-15 |
| DC-R2 | mention IDs `10,10,11` before fix | SensorInput mention=`1,2,3` | 去重晚于频率 mutation | 2026-07-15 |
| DC-R3 | repeated poke notice before fix | replay 再次 notify；poke=`1,2,3` | notice 无稳定 ID 且累计频次重复施加 | 2026-07-15 |
| DC-R4 | climate `0.85` + legacy cold before fix | Humanizer multiplier=`1.105`；first/only segment 0 calls | climate 未拥有 delay，真实路径绕过 adapter | 2026-07-15 |
| DC-R5 | cancelled SQLite old writer after newer flush | DB 回退到 old snapshot（review 复现） | UPSERT 缺 updated_at CAS | 2026-07-15 |
| DC-R6 | future/wrong schema、NaN/inf、late stage | 旧实现接受或降级/丢写 | store 缺迁移与关闭门禁 | 2026-07-15 |
| DC-R7 | 24h one-step vs hourly partition baseline advance | 差异约 `2.2e-16` | 2x2 连续系统矩阵推进满足分段一致性 | 2026-07-15 |
| DC-R8 | close waiter cancel / concurrent start / close during schema worker | single-flight drain；仅一个 schema worker；close 等待完成 | lifecycle cancellation 与并发门禁关闭 | 2026-07-15 |
| DC-R9 | equal timestamp cancelled old writer，old-last/new-last | 两种顺序均保留 newer stage，revision `2` | 持久 revision CAS 不再依赖 wall clock | 2026-07-15 |
| DC-R10 | 模拟 object-id reuse 的两个同秒 poke | 两个 event nonce 不同；同一对象重复解析 nonce 相同 | 去除 `id(event)` 复用风险；跨反序列化 replay 因上游无 ID 保留为明确限制 | 2026-07-15 |

## Handoff

任务 active，implementation/review complete、deployment pending。以 Todo 与 Test Ledger 为准；勿重做架构审计，下一步只做精确提交、bot-only 部署与运行验收。
