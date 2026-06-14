# arbiter C (correction) 触发路径压测审计 — 2026-06-14

**状态**：压测完成，**未改代码**。结论：arbiter C 在当前运行态下**实际上无法触发**，原因是两个结构性约束，而非配置问题。

## 背景

arbiter A/B 已在前几轮压测端到端验证（A completeness、B abort/revise）。arbiter C（correction）是唯一没压过的 arbiter 路径。配置全开（`arbiter.enabled=True correction_enabled=True correction_window_s=30.0 runtime_groups=[]`）。

## 触发条件（kernel/router.py:1934）

arbiter C 在 router 的 trigger 判定链中，条件：
- `trigger is None`（closing/greeting 未抢先）
- `not is_addressed`（**不能 @bot**——@ 走 at_mention）
- `last_assistant_to_user`（上条 emu 回复发给该用户）
- `correction_enabled` + `arbiter.enabled`
- `slot.last_reply_content` 非空 + `last_reply_time>0` + `time.time()-last_reply_time <= 30s`
- `judge_correction` 返回 `needs_correction=True`

命中后日志 `arbiter_c_correction | group= user= type=`，trigger.mode=correction。

## 压测：3 轮投喂，0 次触发

| 轮 | 时序（emu回复→修正消息被评估） | semantic_gate 判定 | 结果 |
|---|---|---|---|
| 1（苹果→iPhone） | 31s（超窗） | force_reply consumed=True intent=clarify_previous | directed_followup |
| 2（北京票→出差） | 41s（超窗） | force_reply consumed=True intent=clarify_previous | directed_followup |
| 3（猫→狗） | 45s（超窗） | pass consumed=False intent=unrelated | light_reply companion |

三轮全程**无一条 `arbiter_c_correction` 日志，judge_correction 从未被调用**。

## 两个结构性根因（确凿）

### 根因 1：30s 窗口太窄，被串行处理延迟吃满

实测修正消息从发出到被 router 评估，三次分别隔 31/41/45s，**全部超过 30s correction_window**。延迟来源：
- emu 串行处理（chat lock），上一条回复 total 4–11s；
- 非-@ 消息走 debounce（5s）+ coalesce（idle 2.5s/max 12s）+ semantic_gate（~1s LLM 调用）；
- 这些累积起来经常把 30s 窗口耗尽。

correction 块的 `time.time()-last_reply_time <= 30` 直接卡死，judge_correction 没机会跑。

### 根因 2：semantic_gate 的 clarify_previous 职能重叠，抢先消费

router 在 1820 行先评估 semantic_gate，对"澄清/纠正上一轮"类消息判 `intent=clarify_previous action=force_reply consumed=True`（轮 1/2 实测 confidence 0.85/0.90）。虽然它不直接设 trigger（correction 块仍 `trigger is None`），但 `semantic_consumed=True` 会在 correction 之后的 directed_followup 块兜住——**且即便 correction 在窗口内能跑，它和 semantic_gate 的 clarify_previous 在语义职能上完全重叠**。semantic_gate 实际已覆盖了"用户纠正 bot"的全部场景，arbiter C 是冗余路径。

## 结论与建议（未实施）

**arbiter C 在当前架构下是僵尸路径**：窗口太窄 + 职能被 semantic_gate 的 clarify_previous 完全覆盖。两条路：

1. **废弃 arbiter C**：semantic_gate 的 clarify_previous + directed_followup 已经处理了"用户纠正/澄清"，且不受 30s 窗口限制、不依赖串行时序。arbiter C 增加一次 LLM 调用却几乎永不触发，建议下线 `correction_enabled` 或移除该路径，减少 router 复杂度。
2. **若要保留**：需把 correction 提到 semantic_gate 之前判定，并放宽窗口（或改为"上一条 bot 回复后 N 轮内"而非时间窗），但这会和 semantic_gate 抢职能，得先想清两者边界。

推荐 **(1)**——这是压测暴露的真实冗余，semantic_gate 上线后 arbiter C 已无独立价值。

## 验证

3 轮多账号投喂（984198159，Kucycx/七喜bot/爱笑的紫毛奶奶），debug 通道全开抓 router trace；每轮记录 emu 回复时刻、修正消息被评估时刻、semantic_gate 判定、最终 trigger.mode。debug 通道压测后复原 false，emu online。**未改任何代码**。

## 撤回的假设

初判曾怀疑"light_reply 路径不写 `slot.last_reply_content` 导致 correction 永不触发"——核对 scheduler.py:2207-2229 后**撤回**：light_reply 仍在 `_do_chat` 内，`latest_reply` 从 timeline 读取后照常设 last_reply_content。真正根因是上述时序窗口 + 职能重叠。
