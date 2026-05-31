# B1 话题块归属 — D3 实施清单

> 状态：2026-05-30 **编码完成，全绿（2253 passed, ruff/pyright clean）**，默认关闭待灰度。对应 [group-multitopic-understanding-b-series-design.md](../tracking/group-multitopic-understanding-b-series-design.md) §2（B1）。
> 纪律：D3 四列迁移清单 + D1 同模式扫描 + D2 cancel-path + D4 完成证据。**接缝已逐处核实行号**（非猜测）。
> 缓存红线：B1 锚点落 pending → 最后一条消息（缓存前缀外），对 hit% 零影响（设计文档 §7.2）。

## 0. 范围与不做

**做**：新增 `TopicBlockTracker`（per-group 内存旁路）；把每条群消息（含 reply-to / @ / speaker / 时间）喂给它增量聚块；prob-fire 需锚点时，锚点对象从"最新一条"升级为"bot 该参与的块代表消息"。

**不做（本期）**：B2 角色接管、B3 动机分；不改 RWS 概率本身；不改 closing/at/followup 等既有 bypass；不引入向量库/训练模型；不改缓存断点逻辑。

**前置依赖**：无。输入数据全部已存在（见 §2 接缝表）。

## 1. 接缝核实（已读代码，行号为证）

| 需要的数据 | 来源（文件:行号） | 现状 |
| --- | --- | --- |
| 当前消息文本 | `notify(message_text=)` scheduler.py:419 | 已是 notify 入参 |
| 说话人 QQ | `notify(user_id=)` scheduler.py:418 | 已是 notify 入参 |
| 消息 id | `event.message_id` router.py:1075/1156 | router 有，**notify 未传**，需补 |
| reply-to 目标消息发送者 | `event.reply.sender.user_id` router.py:1067 | router 有，**notify 未传**，需补 |
| reply-to 是否指向 bot | `_reply_targets_bot(event.reply, bot.self_id)` router.py:1203 | router 有 helper |
| @ 目标列表 | `seg.type=="at"` 的 `seg.data["qq"]` router.py:683-685 | router 解析，**notify 未传**，需补 |
| notify 调用点 | `_notify_group_scheduler` router.py:358,374,403,419 | 三处 `scheduler.notify(...)` |
| slot 定义 | `_GroupSlot.__slots__` scheduler.py:101-142 | tracker 状态可挂 scheduler 实例级，不必进 slot |
| prob-fire 锚点注入点 | `if decision:` … `self._fire(group_id)` scheduler.py:626-642 | 锚点在此处、`_fire` 之前注入 |
| 锚点写入 API | `timeline.add_pending_trigger(reason,message_id,target_user_id)` timeline.py:256 | 复用，closing P0 同款 |
| 相似度兜底 | `NgramSimilarityProvider` / `TopicDriftDetector` services/group/topic_drift.py:10,34 | 复用现成 |

## 2. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归验证 |
| --- | --- | --- | --- | --- | --- |
| C1 | 无话题块概念 | 新增 `TopicBlockTracker` + `TopicBlock` | `services/group/topic_block.py`（新） | 新增 | 单测 test_topic_block.py |
| C2 | notify 只收 message_text/user_id | 加可选 `message_id` / `reply_to_sender_id` / `reply_to_self` / `at_targets` 入参（全部默认空，additive） | `services/scheduler.py:413` | 改签名（向后兼容） | 旧调用不传新参仍工作（默认值） |
| C3 | router 不传 reply/@/msgid | `_notify_group_scheduler` 透传上述结构（从 event.reply / @ 段提取） | `kernel/router.py:358-419` | 接线 | router 单测断言透传 |
| C4 | scheduler 不喂 tracker | notify 入口处把消息喂 `tracker.observe(...)`（在 muted/proactive 检查之后、决策之前） | `services/scheduler.py:~444` | 接线 | tracker.observe 被调用 |
| C5 | prob-fire 锚点指向最新（实为 trigger=None 无锚点） | prob-fire 命中且 `slot.trigger is None` → 查 tracker 取块代表 → `add_pending_trigger` | `services/scheduler.py:626-642` | 注入 | 单测：表情块不捞旧块 |
| C6 | — | 配置开关 `topic_block.enabled`（默认 False，灰度） | `kernel/config.py` | 新增 config | 关闭时完全旁路（行为 == 现状） |

## 3. TopicBlockTracker 设计（C1 细节）

```text
TopicBlock (dataclass):
  block_id: str
  message_ids: list[int]
  participants: set[str]
  last_active: float        # monotonic
  last_text: str
  bot_involved: bool

TopicBlockTracker (per-group, 挂 scheduler 实例: dict[group_id, deque[TopicBlock]]):
  observe(group_id, *, message_id, speaker, text, reply_to_sender_id,
          reply_to_self, at_targets, now) -> None
      # 归属算法（信号强度降序，§2.2 设计）：
      # 1. reply_to_self → 命中含 bot 的块 / 或建 bot 块；bot_involved=True
      # 2. reply_to_sender_id 命中某活跃块 participants → 归该块（skip-connecting）
      # 3. at_targets 命中某活跃块 participants 且近 T 秒 → 归该块；@bot → bot_involved
      # 4. speaker 近 T 秒在某块发过言 → 倾向同块
      # 5. 相似度(text vs block.last_text) ≥ 阈值 → 最高块
      # 6. 都不命中 → 新块
      # 维护：超 K 块或 last_active 过 _STALE_S 归档；更新 participants/last_active/last_text

  pick_anchor_block(group_id, now) -> TopicBlock | None
      # bot_involved 块优先；否则最活跃块（last_active 最新 + participants 最多）；空→None

  representative_message_id(block) -> int        # 块内最后一条（或被@的那条）
```

常量：`_BLOCK_WINDOW=30`（最多保留块数取小，实际 2-4 活跃）、`_STALE_S=300`（块归档）、`_ATTRIB_RECENT_S=120`（同说话人/@延续窗）、`_SIM_THRESHOLD=0.4`（兜底相似度）。均可配。

**纯 CPU、无 I/O、无 await**（相似度用同步 `NgramSimilarityProvider.similarity`）。挂 scheduler 实例级 dict，不进 `_GroupSlot.__slots__`（避免动 slot 契约）。

## 4. 锚点注入（C5 细节）

prob-fire 命中分支（scheduler.py:626 `if decision:` 内，`self._fire(group_id)` 之前）：

```text
if decision:
    ... 既有日志/计数 ...
    if self._topic_block_enabled(group_id) and slot.trigger is None:
        block = self._topic_tracker.pick_anchor_block(group_id, now_wall)
        if block is not None:
            rep_id = self._topic_tracker.representative_message_id(block)
            self._timeline.add_pending_trigger(
                group_id,
                reason="<块代表消息的简述>，你接这条所在的话题，可只回表情或短一句",
                message_id=rep_id,
                target_user_id="<块代表说话人>",
            )
    self._fire(group_id)
```

- **不构造 TriggerContext**（避免触发既有 bypass 加成）——只调 `add_pending_trigger`，与 closing P0 写法一致。
- reason 文案对表情块用"群里在发表情"，对文字块用首句简述——给 LLM 回应当前块的合法出口，不强接旧块。
- `slot.trigger is not None` 时（at/followup/closing 等显式触发）**不注入**——它们已有锚点，B1 只补"无显式触发的概率插话"这一空缺。

## 5. 测试设计（D2 含 cancel-path）

新增 `tests/test_topic_block.py`：

1. **复现 §1 并验证修复**：序列 = [旧话题A×3条停顿] + [表情×2]，断言 ① 表情自成块 B（与 A 分离）；② `pick_anchor_block` 在 A 已 stale 时返回 B 或 None，**不返回 A**；③ 锚点 message_id 指向表情而非"鱼鱼烧"。
2. **skip-connecting**：消息 reply-to 跨 5 轮指向块 A 的老消息 → 断言归回 A，不因紧邻是 B 而误入 B。
3. **@他人归属**：消息 @ 了块 B 的参与者 → 归 B，不归 bot 块。
4. **bot_involved 优先**：存在含 @bot 的块 + 一个更活跃的闲聊块 → `pick_anchor_block` 选 bot 块。
5. **新块开启**：语义无关 + 无 reply/@ + 新说话人 → 开新块（schisming）。
6. **stale 归档**：块超 `_STALE_S` 不活跃 → 不再被 pick。
7. **D2 cancel-path**：`observe` 与锚点注入是同步无 await——但需断言：prob-fire 注入锚点后若 `_fire` 的 `_do_chat` 被 shutdown 取消，**tracker 状态不被污染**（observe 已完成、幂等；锚点已写 pending，下次正常消费或被 clear_pending 清理）。模拟 `_do_chat` 抛 CancelledError，断言 tracker 的块集合不残留半截状态、下条消息 observe 正常。

`tests/test_scheduler.py` 加 `TestTopicBlockAnchor`：prob-fire 命中 + trigger=None + tracker 有块 → 断言 `add_pending_trigger` 被调用且 message_id = 块代表；开关关闭时**不调用**（行为 == 现状）。

## 6. D1 同模式扫描（编码时执行，结果记入完成日志）

- notify 的**所有调用点**：`_notify_group_scheduler` 三处（router.py:374/403/419）+ 是否有其他 `scheduler.notify(` 调用（grep 全仓），确认 C2 加参后无遗漏调用点报错。
- reply/@ 提取点：确认 router 内 reply-to（event.reply.sender）与 @ 段（seg.type=="at"）是 tracker 输入的唯一来源，无第二处需同步。
- `add_pending_trigger` 既有调用（scheduler.py:1331 closing/trigger 路径）与 C5 新增不冲突（C5 仅在 `slot.trigger is None` 时触发，与既有互斥）。

## 7. 缓存验证（D4，设计文档 §7）

- **逐块 hash**：`cache_debug | system=[...]`（client.py:663）——B1 上线前后录同群场景，断言 **system static/stable 块 hash 逐字节不变**（B1 不碰 system，锚点只进 messages 最后一条）。
- **hit%**：usage 表 `cache_r/hit%`——main/thinker hit% 上线前后不下降（容差 ±2%）。预期：B1 锚点落最后一条消息（缓存前缀外），hit% 无变化。

## 8. 完成证据（D4，编码后回填）

**状态：2026-05-30 编码完成，全绿。**

- **D1 同模式扫描**：`scheduler.notify(` 全调用点 = 4 处——router.py 三处（412/442/459）+ `qq_interactions.py:185`；前三经 `_notify_group_scheduler` 统一透传，第四带 `trigger`（qq_interaction）故锚点 `slot.trigger is None` 守卫天然跳过；新增 notify 参数全 additive 默认值，4 处调用无一报错。reply/@ 提取唯一来源 = `_extract_topic_block_signals`（router.py，读 `event.reply.sender` + @ 段）。`add_pending_trigger` 既有调用（scheduler.py closing/trigger 路径）与 C5 互斥（C5 仅 `slot.trigger is None`）。
- **外部可观察证据**：
  - `pytest tests/test_topic_block.py` → **8 passed**（含 §1 复现 `test_reproduces_stale_topic_bug`：表情块锚点不指向 stale 鱼鱼烧）。
  - `pytest tests/test_scheduler.py` → **54 passed**（含 3 新 `TestTopicBlockAnchor`：注入锚点 / 关闭旁路 / 显式 trigger 不覆盖）。
  - 全量 `pytest -q` → **2253 passed, 8 skipped**（+11 vs 基线 2242）。
  - `ruff check` → All checks passed；`pyright`（5 改动文件 + 测试）→ 0 errors。
- **缓存证据**：B1 锚点只调 `add_pending_trigger` → 写 pending → 合并进**最后一条消息**（`_build_group_messages` 缓存断点在 `len-2`，最后一条永在前缀外，client.py:3611-3625）。**system static/stable 块零改动**（B1 不碰 prompt_builder / system_blocks）。故 main/thinker 的 cache hit% 结构上不受影响——与设计文档 §7.2 判定一致。运行时 before/after hit% 对照在灰度开启后用现有 `cache_debug` + usage `hit%` 验证。
- **回滚路径**：见 §9，`topic_block.enabled=False`（默认）即完全旁路。

## 9. 回滚路径

- **一键回退**：`topic_block.enabled=False`（默认即 False）→ tracker 不接线、锚点不注入，**行为完全 == 现状 P0 之前**。
- **代码回退**：C5 锚点注入块删除即回；C2/C3 的 notify 新参是 additive 默认空，删 tracker.observe 调用即旁路；C1 新文件孤立，删除无副作用。
- 不涉及 DB / 配置迁移 / 持久状态——tracker 纯内存，重启即清。

## 10. 工作量与上线

- 新增 1 文件（topic_block.py ~150 行）+ 改 3 处接线（notify 签名、router 透传、scheduler 注入）+ 1 config 字段。
- 改 .py → **需 rebuild bot**（D6：非 admin/static）。
- 灰度：默认关；先在 1 个烤群开 `enabled=True` 观察块划分日志与"回旧话题"是否消失，再逐群放开。

**实施状态**：C1–C6 全部完成，测试全绿，默认 `enabled=False`。**下一步**：rebuild bot 上线（代码改动）后，在单一烤群开 `topic_block.enabled=true` 灰度，扒 `topic-block anchor -> msg=` 日志确认锚点指向当前块、"回旧话题"消失，再逐群放开 + before/after hit% 对照。
