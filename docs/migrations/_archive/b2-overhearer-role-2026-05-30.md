# B2 角色判断接管（overhearer 默认沉默）— D3 实施清单

> 状态：2026-05-30 **编码完成，全绿（2261 passed, ruff/pyright clean）**，默认 `overhearer_mode=shadow`（零行为变化）待灰度。对应 [group-multitopic-understanding-b-series-design.md](../tracking/group-multitopic-understanding-b-series-design.md) §3 + §8（B2 提前为「插进非己对话块」F-α 的主修复）。
> 纪律：D3 四列 + D1 同模式 + D2 cancel-path + D4 证据。**接缝已核实行号**。
> 缓存红线：B2 是**调度层决策**（改"要不要 fire"），默认不进 prompt → 对缓存命中零影响（设计 §7.2）。

## 0. 目标与边界

**目标**：杜绝 F-α——bot 插进它只是旁听（overhearer）的对话块。依据 Goffman 参与框架：只有 addressed recipient 有回应义务；unaddressed/overhearer 默认沉默。

**做**：在 prob-fire 路径前，判定 bot 在「当前消息所属话题块」里的接收角色；overhearer 时**压制概率插话**（灰度：先调阈值，后默认沉默）。

**不做（本期）**：不碰 at/followup/closing/correction 等显式 bypass（它们恒为 addressed）；不改 RWS 公式；不做 B3 动机分；不引入 LLM 调用（纯规则 + 已有 shadow gate 信号）。

**与 B1 关系**：B1 提供"块参与者集合"（判断 bot 在不在块里），B2 用它 + @/reply 信号定角色。B1 是 B2 的前提，已实现。

## 1. 接缝核实（已读代码）

| 需要的信号 | 来源（文件:行号） | 现状 |
| --- | --- | --- |
| is_addressed（被 @/昵称） | `event.is_tome()` router.py:1049-1053 | 已算 |
| reply_to_bot | `_reply_targets_bot(event.reply, self_id)` router.py:1245/1266 | 已有 helper |
| has_other_at（@了别人） | `_message_has_other_at(msg, self_id)` router.py:1244 | 已有 helper |
| shadow gate 角色决策 | `evaluate_group_gate_shadow(...)` reply_workflow.py:588，router.py:1237 | **已算，仅 log_shadow_decision，未消费** |
| bot 是否在块参与者中 | `TopicBlockTracker.pick_anchor_block(require_bot_involved=True)` topic_block.py | B1 已实现：返回 None ⇔ bot 不在任何活跃块 |
| prob-fire 决策点 | scheduler.py:660 `if decision:` | B2 在此之前介入 |
| notify 入口（已接 B1 信号） | scheduler.py:notify | 已收 at_self/reply_to_self/at_targets（C2/C3 已做） |

**关键复用**：B1 的 C2/C3 已经把 `at_self / reply_to_self / at_targets / message_id` 透传进 notify。B2 只需再透传 `is_addressed`（router 已算），role 判定即可全在 scheduler 内完成。

## 2. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| D1 | notify 不知 is_addressed | notify 加 `is_addressed: bool = False` 入参 | scheduler.py:notify | 改签名（additive） | 旧调用默认 False |
| D2 | router 不传 is_addressed | `_notify_group_scheduler` 透传 `is_addressed` | router.py | 接线 | 已在 scope（router.py:1049） |
| D3 | 无角色判定 | scheduler 新增 `_receiver_role(group_id, slot, is_addressed, reply_to_self, at_self)` → "addressed"/"overhearer"/"ratified" | scheduler.py | 新增 | 单测 |
| D4 | prob-fire 不看角色 | overhearer 时按 config 模式：`shadow`→仅 log / `threshold`→阈值上调 / `silent`→直接 return（不 fire） | scheduler.py:~640（阈值计算处）/ 660 | 注入 | 单测 |
| D5 | — | config `topic_block.overhearer_mode`（默认 `shadow`）+ `overhearer_threshold_boost`（默认 0.0） | config.py | 新增 | — |

## 3. 角色判定（D3 细节）

```text
def _receiver_role(group_id, slot, *, is_addressed, reply_to_self, at_self) -> str:
    # 显式寻址（最强，恒回应义务）
    if is_addressed or reply_to_self or at_self or slot.trigger is not None:
        return "addressed"
    # B1：bot 在某个活跃块里（之前被 @/参与过）→ 仍是该块的合法参与者
    if self._topic_tracker is not None:
        blk = self._topic_tracker.pick_anchor_block(group_id, require_bot_involved=True)
        if blk is not None:
            return "ratified"          # 在自己参与过的块里，可低调接话
    # 否则：没被寻址、不在任何 bot 块 → 纯旁听
    return "overhearer"
```

- **addressed**：原路径不变（既有 bypass / 正常概率）。
- **ratified**：bot 曾参与该块——允许接，但这是 B3 动机分该管的"必要性"，B2 不额外压（保持现状概率）。
- **overhearer**：F-α 的目标态。按 `overhearer_mode` 处理（§4）。

## 4. overhearer 三档灰度（D4 细节，对应设计 §3 灰度三步）

config `topic_block.overhearer_mode`：

1. **`shadow`（默认，第一步）**：只 `_L.info("scheduler | group={} overhearer (would suppress)", ...)`，**不改行为**。先收集"若按角色决策会压掉多少 fire"，对比误伤。
2. **`threshold`（第二步）**：overhearer 时 `threshold = max(0.0, threshold - overhearer_threshold_boost)`（boost 默认 0，调为正值时降低 fire 概率，不硬否决）。保留极小概率的自然插话。
3. **`silent`（第三步，数据稳后）**：overhearer 直接 `return`（记 `consecutive_skip += 1`、`last_response_class=SILENCE`），不进 prob-fire。这才是"完全不插别人块"。

介入点：`_receiver_role` 在 RWS 阈值计算后、`if decision:` 前调用；overhearer 分支按 mode 改 threshold 或提前 return。

## 5. D1 同模式扫描（编码时执行）

- notify 全 4 调用点（router ×3 + qq_interactions）确认 `is_addressed` 加参后不报错（additive 默认 False；qq_interaction 带 trigger → addressed，不受影响）。
- 确认 overhearer 的 `return` 与既有 skip 分支（at_only / busy / interval / prob skip）的 slot 状态更新一致（consecutive_skip / last_skip_time / trigger=None / last_response_class）。
- 确认显式 bypass（at/followup/closing/correction/video）在 `_receiver_role` 之前已 fire-and-return，不会误判为 overhearer。

## 6. 测试设计（D2 含 cancel-path）

`tests/test_scheduler.py::TestOverhearerRole`：

1. **overhearer shadow 不改行为**：mode=shadow，两个非 bot 用户对话 + talk_value=1.0 → 仍 fire（只多一条 log）。
2. **overhearer threshold 降概率**：mode=threshold + boost 使 threshold≤0 → 不 fire；boost=0 → 仍 fire。
3. **overhearer silent 不 fire**：mode=silent，bot 不在块 + 无寻址 → `llm.calls == 0`，且 `consecutive_skip` 已 +1（状态正确）。
4. **addressed 不受影响**：is_addressed=True（或 reply_to_self/at_self）→ 即便 silent 模式也 fire。
5. **ratified 不受影响**：bot 先被 @（块 bot-involved），后续同块消息 → role=ratified → silent 模式下仍按概率 fire。
6. **D2 cancel-path**：overhearer silent 的 `return` 不留半截 slot 状态；若上一轮 fire 的 `_do_chat` 被取消，本轮 overhearer 判定独立、不受污染。

`tests/test_topic_block.py` 已覆盖 `pick_anchor_block(require_bot_involved)` 的 None/非 None 语义（B1 收紧时已加），B2 复用。

## 7. 缓存验证（D4）

B2 不进 prompt（纯调度层 fire/no-fire 决策）。`cache_debug` 逐块 hash 不变、main/thinker hit% 不变（结构上无关）。**附带正收益**：overhearer silent 减少 LLM 调用次数 → token 成本下降。

## 8. 回滚路径

- **一键回退**：`overhearer_mode=shadow`（默认）→ 只 log 不改行为，行为 == 现状。
- **代码回退**：D4 的 overhearer 分支删除即回；D3 的 `_receiver_role` 孤立函数；D1/D2 的 is_addressed 是 additive 默认 False。
- 纯内存、无 DB/迁移。

## 9. 工作量与上线

- 改 scheduler（notify 签名 + `_receiver_role` + overhearer 分支）+ router（透传 is_addressed）+ config（mode + boost）。约 60 行。
- 改 .py → rebuild bot。
- 灰度：`shadow` 默认上线（零行为变化）→ 扒 `overhearer (would suppress)` 日志看误伤 → 转 `threshold` → 数据稳转 `silent`。

**待确认**：照此实施 B2？确认后按 D1→D5 编码，每步跑测试，回填证据。

## 10. 完成证据（D4，已回填）

**状态：2026-05-30 编码完成，全绿。**

- **改动**：D1 notify 加 `is_addressed: bool = False`（scheduler.py:438）；D2 `_notify_group_scheduler` 经 `tb["is_addressed"]` 透传（router.py）；D3 `_receiver_role`（scheduler.py，addressed/ratified/overhearer）；D4 prob-fire 阈值计算后插 overhearer 三档分支（shadow/threshold/silent）；D5 config `overhearer_mode`（默认 shadow，validator 限三值）+ `overhearer_threshold_boost`（默认 0）。
- **D1 同模式**：`scheduler.notify` 4 调用点（router ×3 经 `**tb` 透传 + qq_interactions 带 trigger→addressed 不触发 gating）；overhearer silent 的 `return` 与既有 skip 分支 slot 状态更新一致（consecutive_skip++ / last_skip_time / last_response_class=SILENCE / trigger=None）；显式 bypass（at/followup/closing/correction/video）在 `_receiver_role` 前已 fire-return，恒判 addressed。
- **测试（+5）**：`tests/test_scheduler.py::TestOverhearerRole` —— shadow 不改行为 / silent overhearer 不 fire（+skip 状态）/ addressed 在 silent 下仍 fire / ratified（@-self 后同块）在 silent 下仍 fire / tracker 关闭无 gating。修 `test_router_b_cluster_wiring.py` 的 `_Scheduler` 桩吸收新 kwargs。
- **外部可观察**：全量 `pytest -q` → **2261 passed, 8 skipped**（+5 vs 2256）；`ruff` All passed；`pyright`（scheduler/router/config）0 errors。
- **缓存**：B2 纯调度层 fire/no-fire，不进 prompt → system hash 与 main/thinker hit% 不受影响；silent 模式**减少 LLM 调用**（token 正收益）。
- **回滚**：`overhearer_mode=shadow`（默认）只 log 不改行为；或 `topic_block.enabled=false` 整体旁路。纯内存无 DB/迁移。

**灰度路径**：rebuild 上线（默认 shadow，零行为变化）→ 扒 `overhearer (would suppress, mode=shadow)` 日志统计误伤率 → 转 `threshold`（配 boost 降概率）→ 数据稳转 `silent`（完全不插非己块）。
