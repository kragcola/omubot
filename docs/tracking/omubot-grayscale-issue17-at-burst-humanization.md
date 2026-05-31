# Issue 17 — 连续 @ 压测下拟人行为异常（独立立项）

> 状态：P2 独立立项，待方案定夺
>
> 来源：2026-05-27 从 P2/P3 决策文档拆出。原始描述"丢 @ 目标"经日志验证**不成立**；实际问题是 burst @ 场景下的拟人行为异常。
>
> 约束：与 P0/P1 同——**不允许修改人设文件**。所有方案纯运行时 / 代码层。

---

## 问题重新定义

### 原始描述（已否定）

~~`slot.trigger` covering write 导致连续 @ 时丢失前一条 @ 目标。~~

**日志验证结论**：@ 目标不丢失。所有 @ 消息都进入 GroupTimeline，LLM context 能看到全部。`slot.trigger` 只影响 `reply_prefix`（引用回复指向哪条消息），不影响 LLM 是否感知到被 @ 的事实。

### 实际问题（压测复现）

连续 @ bot 时出现三类拟人预期外行为：

| # | 现象 | 根因 | 严重程度 |
|---|---|---|---|
| A | 两次回复之间几乎零延迟 | `finally` 块 re-fire 无 inter-fire cooldown | 高（破真实感） |
| B | 对同一话题重复回复内容相似 | re-fire 时 LLM 看到自己刚回的内容但无"已回过"抑制 | 中（破真实感） |
| C | 被反复骚扰 @ 后情绪态度不变 | mood 系统是只读快照，无"被骚扰→烦躁"反馈回路 | 中（破沉浸） |

---

## 根因分析

### A — re-fire 无 cooldown

```python
# services/scheduler.py:862-866
finally:
    if slot:
        slot.running_task = None
        if slot.pending_at or slot.msg_count > 0:
            slot.pending_at = False
            self._fire(group_id)  # ← 立即 re-fire，无任何延迟
```

`_fire()` 本身不更新 `last_fire_time`（只有概率路径 line 346 才更新），所以 re-fire 完全绕过 `planner_smooth` 间隔检查。从用户视角：bot 回复完第 1 条 @，**瞬间**开始处理第 2 波。

加上 `on_segment` 首段 `humanize="skip"`（因为有 `reply_prefix`），两次回复之间的体感延迟接近 0。

### B — 重复回复无抑制

re-fire 时 timeline 已包含 bot 刚才的回复。thinker 收到 `force_reply=True`（@ 触发），跳过"要不要回复"判定直接进 LLM。LLM 看到：

```
[用户 @bot 消息1]
[bot 回复1]          ← 刚写入 timeline
[用户 @bot 消息2]    ← pending_at 排队的那条
[用户 @bot 消息3]    ← slot.trigger 指向这条
```

没有任何机制告诉 LLM "消息2 和消息1 是同一波 burst，你已经回过了"。LLM 倾向于对每条 @ 都给出完整回复。

### C — mood 无反馈回路

当前 mood 系统架构：

```
MoodClassifier.classify() → 读 timeline 最近 12 条 → 输出 MoodDecision
                            ↑ 只看用户消息的文本特征（短回复率、语气词率、贴纸密度）
                            ↑ 不看"用户是否在骚扰 bot"
                            ↑ 不看"bot 已经被连续 @ 了 N 次"
```

- `MoodClassifier`（`services/humanization/mood_classifier.py`）只分析**用户消息的文本特征**
- 没有"被 @ 频率"信号输入
- mood 结果通过 `RuntimeStateBus` 写入，TTL 300s，但**不会因为 bot 被骚扰而主动 shift**
- `_get_mood_multiplier()` 只影响 `talk_value`（是否主动说话的概率），不影响回复态度/语气

---

## 方案 17A — inter-fire cooldown + burst dedup hint + mood irritation feedback（推荐）

三层修复，分别对应三个根因：

### 第 1 层：inter-fire cooldown（修 A）

**修改位点**：`services/scheduler.py:862-866`

**改动**：

```python
finally:
    if slot:
        slot.running_task = None
        if slot.pending_at or slot.msg_count > 0:
            slot.pending_at = False
            # 新增：re-fire 前注入 inter-fire cooldown
            cooldown = self._inter_fire_cooldown(group_id)
            if cooldown > 0:
                await asyncio.sleep(cooldown)
            self._fire(group_id)
```

`_inter_fire_cooldown(group_id)` 逻辑：

- 基础值：`random.uniform(2.0, 5.0)` 秒（模拟"看到新消息→决定回复"的思考时间）
- mood 调节：mood=tired/cold 时 ×1.5（懒得理）；mood=playful/high 时 ×0.7（积极回应）
- 上限 cap：8.0 秒（避免用户等太久觉得 bot 挂了）

**注意**：`finally` 块当前是同步的（`_fire` 是同步方法）。需要把 re-fire 逻辑改为 `asyncio.create_task(_delayed_refire(group_id, cooldown))`，避免阻塞 `_do_chat` 的 task 清理。

### 第 2 层：burst dedup hint（修 B）

**修改位点**：`services/scheduler.py:760-764`（trigger 写入 timeline 的位置）

**改动**：当 re-fire 路径触发时，在 `add_pending_trigger` 的 reason 里追加 burst 标记：

```python
if trigger is not None:
    reason = trigger.reason
    if getattr(trigger, "_is_refire", False):
        reason = f"[burst续] {reason}"
    self._timeline.add_pending_trigger(
        group_id, reason=reason,
        message_id=trigger.target_message_id,
        target_user_id=trigger.target_user_id,
    )
```

同时在 `_fire()` 被 `finally` 块调用时，给 `slot.trigger` 打标：

```python
# finally 块 re-fire 前
if slot.trigger is not None:
    slot.trigger = slot.trigger._replace(_is_refire=True)  # 或 extra 字段
```

LLM 看到 `[burst续]` 标记后，自然理解"这是同一波连续 @，不需要重复回答相同内容"。

**备选增强**：在 thinker prompt 加一条规则——"如果 timeline 中最近 1 条 bot 回复距今 < 10s 且 trigger 含 [burst续]，可以用更短/更随意的方式回应，不必重复完整回答"。

### 第 3 层：mood irritation feedback（修 C）

**新增模块**：`services/humanization/mood_feedback.py`

**核心机制**：在 `on_post_reply` 钩子中，根据"短时间内被同一用户/群连续 @ 的次数"向 mood 系统注入 irritation 信号。

```python
@dataclass(frozen=True, slots=True)
class IrritationSignal:
    group_id: str
    at_count_5min: int      # 5 分钟内被 @ 次数
    same_user_streak: int   # 同一用户连续 @ 次数
    irritation_score: float # 0.0-1.0

class MoodFeedbackEngine:
    """Post-reply hook: inject irritation into mood based on @ burst patterns."""

    def __init__(self, bus: RuntimeStateBus):
        self._bus = bus
        self._at_history: dict[str, list[float]] = {}  # group_id → timestamps

    def record_at(self, group_id: str, user_id: str, ts: float) -> None:
        """Called on every @ trigger."""
        ...

    def compute_irritation(self, group_id: str) -> IrritationSignal:
        """Compute irritation score from recent @ history."""
        ...

    def apply_to_mood(self, group_id: str, scope: Scope) -> None:
        """Shift mood toward 'cold'/'tired' when irritation is high."""
        signal = self.compute_irritation(group_id)
        if signal.irritation_score < 0.3:
            return  # 低频 @ 不影响心情
        current = self._bus.get(MOOD_CURRENT_SLOT, scope=scope)
        # irritation_score 0.3-0.7 → mood 向 tired 偏移
        # irritation_score > 0.7 → mood 向 cold 偏移
        ...
```

**irritation_score 计算**：

| 5 分钟内 @ 次数 | 同一用户连续 | irritation_score |
|---|---|---|
| 1-2 | 否 | 0.0（正常互动） |
| 3-4 | 否 | 0.2（轻微） |
| 3-4 | 是 | 0.4（有点烦） |
| 5-7 | 是 | 0.6（明显烦躁） |
| 8+ | 是 | 0.85（很烦） |

**mood shift 效果**：

- irritation 0.3-0.5 → mood label 不变，但 `openness` 下降 0.1-0.2（回复变简短）
- irritation 0.5-0.7 → mood shift 向 `tired`（回复更敷衍、delay 更长）
- irritation > 0.7 → mood shift 向 `cold`（回复冷淡、可能不回）

**与现有系统的集成点**：

- 挂 `on_post_reply` 钩子（同 affection plugin 模式）
- 写入 `RuntimeStateBus` 的 `MOOD_CURRENT_SLOT`（同 `MoodClassifier.classify_and_write`）
- `_get_mood_multiplier()` 自动消费新 mood（无需改 scheduler）
- humanizer `_runtime_multiplier()` 自动消费新 mood（无需改 humanizer）

**衰减机制**：

- irritation 记录 TTL = 5 分钟（滑动窗口）
- 用户停止 @ 后，下一次 `classify_and_write` 会用正常文本信号覆盖 irritation 注入的 mood
- 不持久化——重启后 irritation 归零（合理：bot "忘了"之前被骚扰）

### 配置

```json
"at_burst_humanization": {
  "enabled": true,
  "inter_fire_cooldown": {
    "base_min_s": 2.0,
    "base_max_s": 5.0,
    "mood_tired_mult": 1.5,
    "mood_playful_mult": 0.7,
    "cap_s": 8.0
  },
  "burst_dedup_hint": true,
  "mood_irritation": {
    "enabled": true,
    "window_s": 300,
    "thresholds": [3, 5, 8],
    "same_user_weight": 1.5
  }
}
```

**成本**：~200-280 行（cooldown 40-60 / dedup hint 30-50 / mood feedback 120-160 / config 20）+ 5-7 用例测试

**优势**：

1. 三层独立——可逐层灰度开启，任一层单独也有价值
2. 不改 LLM 行为——cooldown 和 mood 都是运行时层面，LLM 只通过 hint 和 mood 间接感知
3. 复用已有基础设施——`RuntimeStateBus` / `MOOD_CURRENT_SLOT` / `on_post_reply` / `_humanizer_runtime`
4. 符合拟人直觉——真人被反复 @ 会：① 不会秒回 ② 不会每次都认真回 ③ 会越来越烦

**风险**：

- cooldown 的 `asyncio.sleep` 在 shutdown 时需要 cancel-safe（D2 要求）
- burst dedup hint 依赖 LLM 理解 `[burst续]` 标记——需灰度验证 v4-flash 是否遵循
- mood irritation 与 `MoodClassifier` 的正常 classify 可能冲突（两者都写 `MOOD_CURRENT_SLOT`）——需要 merge 策略而非覆盖

---

## 方案 17B — 仅 inter-fire cooldown（最小修复）

只做第 1 层。

**成本**：~40-60 行

**效果**：解决"回复太快"（A），但不解决重复内容（B）和情绪不变（C）。

**适用场景**：快速止血，后续再叠加 B/C。

---

## 方案 17C — cooldown + dedup hint（不含 mood feedback）

第 1 层 + 第 2 层。

**成本**：~80-120 行

**效果**：解决 A + B，不解决 C。mood feedback 作为独立 follow-up。

**适用场景**：mood 系统改动风险较大时，先稳定 A/B。

---

## 与其他 Issue 的关系

| 关联 Issue | 关系 |
|---|---|
| P0 F1 sentinel_registry | mood feedback 写入 `RuntimeStateBus` 同骨架 |
| P1 F4 persona drift Layer 3 | drift detector 的 EWMA 和 mood irritation 的滑动窗口同模式 |
| P1 F11 addressee binding | 共 `trigger.target_user_id`，burst 场景下 addressee 需要正确指向 |
| P2 Issue 15 echo humanizer | echo plugin 的 humanizer 调用同样受 mood 影响 |
| P2 Issue 15 instruction gate | irritation 高时拒绝执行指令的阈值应该更低（mood modulation 共享） |

---

## 决策模板

```text
Issue 17 / @ burst 拟人异常：
[ ] 17A 推荐（cooldown + dedup hint + mood irritation）
[ ] 17B 仅 cooldown
[ ] 17C cooldown + dedup hint
[ ] 暂不做

执行偏好：
[ ] 独立 PR
[ ] 与 P1 E 簇（F11/F14）同批（共 trigger 数据结构）
[ ] 第 1 层先做，第 3 层等 mood 系统稳定后再加
[ ] 其他：___
```
