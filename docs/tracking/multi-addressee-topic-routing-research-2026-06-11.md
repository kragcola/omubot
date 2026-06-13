# 多寻址回复路由 × 话题块 调研文档

> 状态：调研完成，待讨论 → 实现计划（ExitPlanMode）。
> 触发事件：2026-06-11 用户在群 993065015 对 bot 说「宝宝」并 @，未得到（正确的）回应。
> 关联历史：Issue 17（`fix-at-mention-burst-batching-*.md`）、B 系列话题块设计（`group-multitopic-understanding-b-series-design.md`）、`fix-prob-fire-stale-topic-sticker-2026-05-30.md`。
> 目标定级：用户明确要求「复杂并发寻址场景下仍能从容应对的未来级修复」，非最小补丁。

---

## 0. 用户已给定的方向（讨论锚点，勿擅自更改）

1. **多人@的回复路由 = 话题块决定**：同话题块 → 合并多@引用一次回复；不同话题块 → 分别@引用（各自回复）。
2. **真@优先级**：本质更高，但**回复场景下无差别对待**（真@/伪@都正常回）；优先级只在未来 bot 降级（资源受限）场景才启用。
3. **addressee_hint 升级为多职**，但**必须先调研话题块，防止上下文串话**。
4. **「宝宝」不单独特判**：话题块正常的话，这条本应被 LLM 识别为「在叫 bot」而被回应；当前没回 = 话题块下游表现未达预期。
   - ⚠️ 调研修正见 §3：话题块**归属**其实正确（role=ratified），未达预期的是 ratified 之后的**开口决策**，不是归属本身。

---

## 1. 事故精确还原（群 993065015，2026-06-11 20:06）

bot 自己在 20:06:32–37 连发「姆姆在此~ / 怎么啦 / 有事找我玩？」+ 一张表情（即 bot 正在一个它参与的对话块里）。随后：

| 时间 | 事件 | 系统内部 | 结果 |
|------|------|---------|------|
| 20:06:43 | **你**「宝宝……」（纯文字，无@、非昵称） | role=ratified，但语义门 0.30 / RWS im=0.391 / floor 未命中 | **没回**（三道闸全放行失败） |
| 20:06:56 | **你** `@emu`（真 `[at:qq=384801062]`，msg **1906428072**） | `@ -> arbiter wait`；`slot.trigger=你的@`，burst_pending=[你] | 进 arbiter |
| 20:06:57 | **丛非凡**「姆。」（昵称前缀伪@，msg **1867000812**） | `slot.trigger` 被 covering-write 覆盖成丛非凡的 | 顶掉你的@ |
| 20:07:02 | `arbiter_a_fire pending=2` | `_fire` 读 `slot.trigger`=丛非凡 → `reply targets msg_id=1867000812` | — |
| 20:07:08 | bot「姆——/叫我有什么事呀」 | `[CQ:reply,id=1867000812]` 引用回**丛非凡**，不是你 | **你的真@被合并吞掉、目标被抢** |

**两个独立缺陷叠加**：
- **缺陷①（“宝宝”没回）**：ratified 续话的开口决策对一条明显在回 bot 的短消息仍按概率赌，且赌输（详见 §3）。
- **缺陷②（真@被顶）**：arbiter 突发合并时，回复目标取自标量 `slot.trigger`（latest-wins），后到的伪@覆盖了先到的真@（详见 §2）。

---

## 2. 缺陷②根因：整条链路只有一个标量寻址槽

回复目标的**唯一真相源是 `slot.trigger` 这一个标量字段**，语义 “latest wins”：

```
router 构造 TriggerContext(target_message_id=event.message_id)   [router.py:1591]
  → scheduler.notify: slot.trigger = trigger  # 每条都无条件覆盖     [scheduler.py:598]
  → burst_pending.append(PendingMessage)  # 只存 content/user_id/timestamp，无 target  [arbiter.py:53]
  → arbiter 合并完成 → _fire 读 slot.trigger（=最后一条）          [scheduler.py:1498]
  → [CQ:reply,id=trigger.target_message_id]                        [scheduler.py:1852]
```

三个结构性事实：
- **`PendingMessage` 不带目标身份**（[arbiter.py:53](../../services/llm/arbiter.py)，仅 `content/user_id/timestamp`）。burst 里累积的消息没有各自的 `target_message_id`，合并时拿不回来。
- **`slot.trigger` 标量 + covering write**（[scheduler.py:598](../../services/scheduler.py)）。后到无条件覆盖先到，不分真@/伪@/义务等级。
- **send 站点零校验**（[scheduler.py:1847-1853](../../services/scheduler.py)）：只看 `mode=="at_mention"`，不看 `addressee_self`、不看 `evidence`（at_self vs nickname_original）、不验证目标归属。`addressee_hint`（[addressee_hint.py:37](../../services/llm/addressee_hint.py)）同样只认单个 target_user_id。

**坏状态枚举（不止本次一种）**：

| 场景 | 现状 | 错？ |
|------|------|-----|
| A 真@ → B 真@ | 回复引用 B，A 被无视 | ✗ |
| **本次** A 真@ → B 昵称伪@ | 引用 B（伪@），真@的 A 被顶 | ✗✗ |
| A @bot → A 自己追发 | 引用 A 最新条 | ✓ |
| 合并 flush 路径 [router.py:676](../../kernel/router.py) | `notify(message_text=merged, 无trigger)` → trigger 整个丢失 | ✗ |
| A @bot，生成中 B @bot | B 进 pending_during_generation，re-fire 时 slot.trigger 可能已是 B | ✗ |

共性：**“多个寻址者”被压成“一个标量目标”，选择规则是脆弱的到达顺序，而非寻址强度/发起顺序/义务等级。**

### Issue 17 早识别、只修一半
[fix-at-mention-burst-batching-execution.md:1597-1608](fix-at-mention-burst-batching-execution.md)「场景4：多用户同时@mention」已写明根因，但当年只落地了 `pending_during_generation: list[PendingMessage]`（**内容不丢**，LLM 能看到全部文本统一回复），**目标身份（target_message_id）的丢失没修**——因为 PendingMessage 没存它。原设计提的 `pending_triggers: list` + per-(group,user) burst window，grep 全仓 0 命中，**从未 ship**。

---

## 3. 缺陷①根因：话题块归属正确，但 ratified 续话的开口决策仍在赌

⚠️ **这条修正了用户方向#4 的判断**。日志铁证：

```
20:06:47 scheduler | prob skip (threshold=0.38 ... role=ratified rws=0.39)
20:06:47 scheduler_rws_dual | im=0.391 interrupt=0.500 proactive=False -> False
```

`role=ratified` = 话题块**正确识别 bot 参与了这个块**（bot 刚说完「有事找我玩？」），把「宝宝……」归到同一块。归属内核是健全的。卡点在 ratified 之后的三道开口闸全部放行失败：

| 闸 | 位置 | 对「宝宝」判定 | 为什么没救回 |
|----|------|--------------|-------------|
| ① 路由层语义门 | [router.py:1800](../../kernel/router.py) | `llm_gate 0.30 < 0.78` 不消费 | LLM 判「可能是对他人称呼」，没升级 directed_followup |
| ② 调度器 RWS 双阈 | [scheduler.py:836](../../services/scheduler.py) | `im=0.391 < 0.5` → False | 模型不觉得裸「宝宝」值得回 |
| ③ ratified 续话兜底 | [scheduler.py:855](../../services/scheduler.py) | `roll < 0.55` 没命中 | 复用同一 roll、概率掷骰、这次掷输 |

**关键 bug**：`ratified_continuation_floor=0.55` 在 24h 全量日志命中 **0 次**。它本应救活“用户在 bot 参与的块里接话”的实时一来一回，但：
- 复用 `old_decision` 的**同一个 roll**（[scheduler.py:818](../../services/scheduler.py)），非独立掷骰；
- 只是概率兜底，不是“ratified 续话必回”；
- 语义门（闸①）在调度器之前，且其 features **完全不含话题块/ratified 信号**（[reply_workflow.py:122 ReplyGateFeatures](../../services/reply_workflow.py)），所以 LLM 判“宝宝”时根本不知道“这是在 bot 刚参与的块里接话”。

**结论**：不是话题块没认出来，而是“认出来了（ratified），下游开口决策对一条明显在回 bot 的续话仍按概率赌且赌输”。该修的是 **ratified 续话 → 更强开口义务**，而非话题块归属。

---

## 3.5 缺陷①的修法：降级给弱回复管辖（用户定向 + 弱回复原始设计对齐）

> 用户定向：缺陷①「宝宝」这类消息应**降级给弱回复处理管辖**，而非在强回复概率闸里硬救。
> 对齐前提：先吃透弱回复原始设计（`weak-reply-mechanism-design.md`、`weak-reply-audit-2026-06-07.md`），避免改偏。

### 3.5.1 弱回复原始设计的两条不可动摇原则

源自四条会话分析理论（design §1）：
1. **弱回复分两子型，不能混成一档**（Clark horizontal/vertical）：
   - **companion（陪伴型/horizontal）**：「嗯嗯/哈哈」=「我在，你继续」。
   - **closing（收尾型/vertical）**：「晚安哦」=「收到，到这儿，关系还在」。
   - 混用 → 对「晚安」回「嗯嗯?」（该收尾时发继续信号），反而拖住对话。
2. **拿不准强/弱时，默认弱回复优于 SILENCE**（Williams ostracism + Malinowski phatic）：被忽视的关系代价 > 弱回复成本。**这是「宝宝降级」的理论靠山**——phatic 消息要的是「被回应」本身，不是信息量。

### 3.5.2 当前实装状态（三次迭代后）

| 设计项 | 状态 | 证据 |
|-------|------|------|
| P0 closing | ✅ 已上线+已修 bug | 2026-06-09 方案A 统一 `_handle_light_reply`，同步生成告别 token，fallback `好~` |
| P1 companion hint 注入 | ✅ 已接线 | client.py:2874 `inject_companion_hint` → plugin_dynamic 注入「简短应一声」→ 走主 LLM 出短 ack |
| 昵称点名（姆/emu）→ companion | ✅ 已落地 | 2026-06-07 B 方案，`nickname_only_call` 走主 LLM + 表情 |
| **2d. prob-skip 救济** | ❌ **从未实装** | design §2.5-2d 无对应代码；`ratified_continuation_floor` 是后来另起的概率兜底，非设计里的「skip→降级 LIGHT_ACK」 |
| companion 在 thinker prompt | ⚠️ **半激活** | thinker.py:159 仍写「本期 companion 仍走普通 reply，优先只在 closing 用 light_reply」 |
| **STICKER_ONLY** | ❌ **纯占位枚举，零消费者** | kernel/types.py:46，从无决策路径选中；但 send_sticker by-intent 入口（sticker_tools.py:220）**已落地**，§2.6 阻塞项已解除 |

### 3.5.3 对齐结论：「宝宝降级」= 补完设计缺的 §2.5-2d，不是新造

- 「宝宝」属 **companion（horizontal）**，设计早已归类。
- 它死在 scheduler **之前**（语义门 0.30 pass），到不了 thinker——正是 design §2.1 早警告的「skip 的消息进不了 chat，不可能只在 thinker 判」。解法就是 **§2.5-2d「prob-skip 救济」**：scheduler skip 分支把「该被看见」的消息降级注入 companion 通道，而非 SILENCE。**这一项从未实装。**
- 2d 原触发条件 `last_assistant_to_user`（bot 刚回过该用户）与本次 `role=ratified`（bot 刚在块里说话）**指向同一事实**——bot 刚在这个块里说过话、用户在接话。两信号统一。
- companion **仍走主 LLM 出短句**是 design §2.5 复用点 A 的**有意决定**（不做冷 token，§1.4 Forbes：冷 ack 有害），非 bug。

### 3.5.4 弱回复载体三档策略（用户定向）

| 弱回复场景 | 载体 | 路由 |
|-----------|------|------|
| **语义必须型**（晚安/早安…需语义回应） | **文字 token**（表情替代不了语义） | closing（已实装）/ **greeting（待补，见 Q1）**，走主 LLM 保证语义正确 |
| **偶尔的「在哦」** | 文字，低频 | companion 偏文字，受文字冷却管辖 |
| **其余陪伴型**（宝宝、日常 phatic） | **表情包优先** | companion + 表情优先偏置（复用 `_fallback_ack` by-intent 检索） |

非对称频控（精化 design §3「弱回复冷却+companion 限频」）：**文字弱回复限频**（防「不停嗯嗯」）；**表情弱回复鼓励**（刷屏伤害低、更有温度），但仍接统一频控（见 3.5.5）。

### 3.5.5 STICKER_ONLY 防 SILENCE 地板（用户重点担忧：表情被剥导致 bot 无回复）

> 用户定向：弱回复**复用统一频控**（今天部署的 `StickerDecisionProvider` Bernoulli + thinker_veto + 乘法阻尼），但必须防「表情 only 被剥 → bot 无回复」。

**风险真实存在，两条剥离路径**：
1. **表情构造失败**：`_build_sticker_cq` 可返回 None（client.py:131：库无匹配/文件丢失/>2MiB/stale）。只挑一张、构造失败、无文字兜底 → 无回复。
2. **统一频控 skip 掉表情**：`StickerDecisionProvider.decide` 的布尔语义是「强回复正文之外的额外配图」——skip 了还有正文。但 STICKER_ONLY 下「表情=回复本身」，`sampled_skip`/`thinker_veto` 命中 → 正文空 + 表情被 skip = **彻底 SILENCE**，违背 §1.4 红线。今天日志实证 `sampled_skip` 5 次 / `thinker_veto` 2 次，频率不低。

**好消息：现有 `_fallback_ack`（client.py:2951）已实现正确降级链，复用即可**：
```
被寻址但无文字 → search_by_intent 检索表情(top3) → 逐个 _build_sticker_cq，首个成功即发(表情优先)
              → 全失败/无库/无匹配 → _pick_empty_visible_reply_fallback()(固定池，必非空，防 SILENCE 地板)
```
且 `_finalize_visible_reply`（client.py:2944）只在 `force_reply or not is_group` 才走 fallback_ack，否则 SILENCE——**地板只对「必答」场景生效**，正是弱回复该有的边界。

**三条铁律（实现约束）**：
1. **频控降级载体，不降级回复**：统一频控 skip → **从「发表情」降级为「发文字 ack」**，而非变 SILENCE。`StickerDecisionProvider` 的布尔在 STICKER_ONLY 上下文重解读为「发表情 vs 发文字 ack」，**两分支都是有回复**。这既复用统一频控（用户定向），又守住「绝不无回复」。
2. **表情构造失败必落文字地板**：`_build_sticker_cq` None → `_pick_empty_visible_reply_fallback`（已实装）。
3. **必答场景才有地板**：保持 `force_reply or not is_group` 边界——ratified 续话/真@/closing 保证非空；纯旁观 prob-skip 仍可 SILENCE（对的）。

**频控压制时的文字 ack 形态（用户已定 = (c)）**：
- 语义必须型（晚安/早安）：本就走文字 + 主 LLM（closing/greeting），保证语义正确，不受此降级影响。
- 普通陪伴型（宝宝等）被频控压制时：用 `_pick_empty_visible_reply_fallback` **固定池**（零成本，降级态天然稀疏，无需再走主 LLM）。

---

## 4. 话题块子系统能力盘点（决定多寻址路由可行性）

[services/group/topic_block.py](../../services/group/topic_block.py)：纯 CPU、无 I/O、进程级单例、已 `enabled=true`（运行态 `overhearer_mode=silent`，`ratified_continuation_floor=0.55`）。

**归属信号（强→弱）**：reply-to-bot > reply-to-某人 > @某块参与者（120s 窗）> 同说话人续话（120s 窗）> 词法相似（≥0.4）> 开新块。每条消息归入一个 `TopicBlock{message_ids, participants, bot_involved, at_message_id}`，块有 `representative_message_id()`（优先 at_message_id，否则最新）。

**对用户设计的支撑力**：

| 设计要求 | 话题块能否支撑 | 缺口 |
|---------|--------------|-----|
| 同块多@ → 合并一次回复 | ✅ observe 聚同块，block 有 representative_message_id | 需在 fire 时按 block_id 分组 burst_pending |
| 异块多@ → 分别@引用各自回复 | ⚠️ 能区分块，但 fire 是一次性单回复 | 需新增 per-block fire 控制流（一个 burst → 多次 fire） |
| 多职 addressee_hint | ⚠️ block.participants 有全部参与者 | hint 当前只认单个 target_user_id，需升级多值，且须确认归属准确 |

**两个“防串话”必查风险（对应用户对“防上下文异常”的担忧）**：
1. **@ 发火不经话题块**：纯@走 rule layer 直接 fire（[scheduler.py:617](../../services/scheduler.py)），`observe` 只在确率路径跑（[scheduler.py:552](../../services/scheduler.py)）。**当前纯@消息根本没进话题块归属**——多@路由必须先把@事件也喂给 observe，否则无块可分。
2. **词法相似兜底误并块**：阈值 0.4 偏低，短消息（「宝宝」「在吗」）易跨话题误并。多职 cue 前必须确认两@真在同一块，不能靠相似度兜底。

---

## 5. 多寻址回复路由的结构性障碍（承接 §2）

1. **PendingMessage 不带 block_id 也不带 target_message_id**（[arbiter.py:53](../../services/llm/arbiter.py)）——burst 消息既不知属哪块，也不知自己引用目标。
2. **@ 发火不经话题块**（见 §4 风险1）——多@要路由先得让@参与块归属。
3. **fire 是单回复模型**——`_fire` 一次起一个 `_do_chat`（[scheduler.py:1489](../../services/scheduler.py)）。“异块分别回复”需“一个 burst → 多次 fire/块”，是新控制流，最易引入并发 bug。

---

## 5.5 Q2 深挖：fire 当前处境 + 多话题块并行 = 选路径 Y（不拆并发防御）

> 用户定向（Q2 已定）：异块多@采用**路径 Y（串行多次 fire，块队列逐块消费）**。

### fire 的并发模型：三层「每群单线性对话」硬绑定

整个 fire 链建立在「一个群 = 一条单线性对话」假设上，这是**刻意的防御设计**（防群内回复交错/串话），非缺陷：
1. **`_GroupSlot` 单回复槽**（scheduler.py:125）：`running_task` 单 Task（同群同时只能一个 `_do_chat`）；`chat_lock` 单锁（scheduler.py:169，`_do_chat` 全程持锁 scheduler.py:1800，同群第二次 fire 阻塞到第一次回完）；`trigger/burst_pending/last_user_id` 全单值，无块分区。
2. **`_fire` 把「在跑」当二元**（scheduler.py:1489）：notify 见 running_task 活 → 新消息塞单一 `pending_during_generation` 后 return（scheduler.py:618）；`_do_chat` finally（scheduler.py:2018）跑完倒回 burst_pending 再起一轮 = 严格「回合接力」串行。
3. **timeline pending 是 per-group 单缓冲**（timeline.py:160 `_GroupState.pending: list`，无块维度）。多块并行会把两块的 pending trigger 混进同一 buffer → LLM 上下文串话。

### 三路径对比（为什么不是真并行）

| | 路径 X 真并行 | **路径 Y 串行多 fire（选）** | 路径 Z 单 fire 分段 |
|---|---|---|---|
| 改动量 | 大重构（拆并发防御） | **中等（扩 fire 为块队列）** | 小（prompt+输出） |
| 「分别回复」质量 | 结构保证 | **结构保证** | 靠 LLM 自觉 |
| 上下文串话风险 | 高（需 timeline 分区） | **低（fire 时按 block_id 过滤）** | 无 |
| 并发 bug（D2） | 高（新并发原语） | **低（不动并发模型）** | 无 |
| 延迟 | 并行最快 | 串行（群内双话题罕见，可接受） | 一次调用最快 |

路径 X 撞 chat_lock（并行不了）、running_task 单值（覆盖）、timeline 单 pending（串话）、发送交错——等于拆掉防御、引入真并发，代价极高且违背架构意图。**不采用。**

### 路径 Y 的重构范围（中等，不碰并发防御）

```
slot.trigger 标量        → pending_triggers: list[PendingTrigger]（按 block_id 分组）
PendingMessage           → 增 target_message_id + block_id
_fire 单次               → 按 block 分组：同块合并一次回复（引用 representative_message_id）、
                            异块逐块串行 fire（复用现有「回合接力」，扩成块队列消费）
timeline pending 取用     → fire 时按 block_id 过滤（不重构 timeline 存储）
不动                     → chat_lock / running_task 单值 / 发送串行 / 整个并发模型
```

**对齐用户设计意图**：「同块合并、异块分别」→ 路径 Y 给的是「同块合并一次回复 + 异块串行各回一次」。异块是真「分别回复」（结构保证），仅时序串行而非并行；群聊双话题块同时@bot 本就罕见（本次事故是真@+伪@撞一起，非两独立话题），串行几秒延迟可接受，换零并发风险。

### 结论
**不需要大重构（路径 X）。** 需要的是「单 trigger 标量 → 按块分组的 trigger 队列 + fire 改块队列串行消费」，这是中等结构改动，无新并发原语、无 timeline 存储重构。真正被修的是缺陷②的根因（标量寻址槽→列表），fire 的单线性防御原样保留。

---

## 6. 拟建方案骨架（待讨论确定后细化）

三层 + 一附带：

- **A. 数据模型（根）**：`PendingMessage` 增 `target_message_id` + `block_id`（+ 寻址 evidence/obligation）。`slot.trigger` 标量 → `pending_triggers: list`（或 burst_pending 携带 trigger）。covering write → append。
- **B. @ 入话题块**：让 rule-layer 的@发火前也调用 `topic_tracker.observe`，使@消息有块归属，多@才可按块路由。
- **C. fire 按块路由**：fire 时按 block_id 分组 burst_pending；同块多@合并一次回复（引用 representative）；异块各自 fire（控制流待定，见 §7-Q2）。
- **附带 D. ratified 续话开口义务**：修缺陷①（见 §7-Q1）。

**纪律约束（D1/D2/D4）**：
- D2 cancel-path：arbiter loop 被 shutdown 取消时，pending_triggers/burst 不得污染下一轮。
- D1 同模式扫描：flush 合并丢 trigger（router.py:676）、pending_during_generation re-fire、deferred_addressed_fire 都吃同一标量，需一并纳入。
- D4：多用户突发回归用例（真@vs伪@、先到vs后到、生成中插入、同块vs异块）。

---

## 7. 设计决策（已全部拍板 · 2026-06-11）

**Q1. 「宝宝」（缺陷①）修法 → 降级给弱回复管辖（见 §3.5）。**
- 不在强回复概率闸里硬救；走弱回复 companion（补完 design §2.5-2d「prob-skip 救济」）。
- 语义门/scheduler 把「该被看见」的 ratified 续话注入 companion 通道，而非 SILENCE。
- 附带：新增 `greeting` light_kind（与 closing 对称，处理早安/早上好等 opening 问候，走文字+主 LLM 保证语义）。

**Q2. 异块多@ → 路径 Y（串行多次 fire，块队列消费）。详见 §5.5。**
- 不拆 fire 单线性并发防御；单 trigger 标量 → 按 block_id 分组的 trigger 队列；同块合并一次、异块逐块串行 fire。
- 否决路径 X（真并行，大重构+高并发风险）与路径 Z（单 fire 分段，分别回复靠 LLM 自觉无结构保证）。

**Q3. 多职 addressee_hint → 保守边界：只采纳强信号确认的同块成员。**
- 进 cue 名单：reply-to 边 / 真@ / @块参与者（QQ ground-truth 信号）。
- **不进** cue 名单：词法相似兜底（阈值 0.4）那一档并入的成员——防 §4 风险2 误并块串话。
- 单职 `addressee_hint`（addressee_hint.py:37）升级为多值，但仅 cue 强信号成员。

**Q4. 真@优先级 → 字段先建，策略暂不区分。**
- 数据模型（PendingTrigger）建好 evidence/obligation 字段，为未来 bot 降级（资源受限）场景预留接口。
- 当前 fire 策略对真@/伪@**同等对待**（回复场景无差别，用户定向）。优先级裁决仅在未来降级场景启用。

---

## 附：关键代码坐标速查

| 关注点 | 位置 |
|-------|------|
| TriggerContext 定义 | kernel/types.py:306-322 |
| @-mention trigger 构造 | kernel/router.py:1591 |
| slot.trigger covering write | services/scheduler.py:598 |
| @ rule-layer 发火 / arbiter wait | services/scheduler.py:617-635 |
| arbiter completeness loop | services/scheduler.py:1204-1245 |
| _fire（读 slot.trigger） | services/scheduler.py:1489-1503 |
| [CQ:reply] 构造 | services/scheduler.py:1847-1853 |
| PendingMessage 定义 | services/llm/arbiter.py:53-57 |
| ratified_continuation_floor 应用 | services/scheduler.py:810-857 |
| _receiver_role（addressed/ratified/overhearer） | services/scheduler.py:922-948 |
| topic_block observe（仅确率路径） | services/scheduler.py:552-565 |
| ReplyGateFeatures（语义门，无话题块信号） | services/reply_workflow.py:122 |
| addressee_hint（单职） | services/llm/addressee_hint.py:37 |
| TopicBlock / Tracker | services/group/topic_block.py |
| TopicBlockConfig | kernel/config.py:1922 |
| ArbiterConfig | kernel/config.py:2053 |
