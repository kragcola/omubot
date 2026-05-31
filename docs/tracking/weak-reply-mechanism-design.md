# 弱回复机制设计文档（强/弱回复二分 · 四档响应分级激活）

> 状态：设计待审 | 创建：2026-05-30 | 作者协同：用户 + Claude
> 关联代码：`kernel/types.py:ResponseClass`、`services/scheduler.py`、`services/reply_workflow.py`、`services/llm/thinker.py`、`services/llm/client.py`

## 0. 背景与诉求

日常聊天中，很多消息并不需要**有信息量的准确回复**，而是需要**陪伴感**与**"我已经看见你发的消息了"的确认**。当前 bot 的回复决策是二元的（回完整内容 / 完全沉默），缺少中间态。

线上痛点（烤群）：用户说"好吧晚安"后，被 scheduler 概率门判定 `prob skip → SILENCE`，bot 毫无反应。在会话结构上，这等于"对方说晚安，你直接转身走开"。

**诉求**：把回复分为**强回复**（现状的完整内容回复）与**弱回复**（简短 token / 表情包为载体，表达"看见了/陪伴"）。

## 1. 社会工程学论证（调研结论）

用户的"弱回复"直觉，在会话分析（Conversation Analysis）有四条独立证据线支撑，均为教科书级结论：

### 1.1 Schegloff & Sacks「Opening Up Closings」(1973)——对话必须双方协作关闭

对话**不能单方面终止**。"晚安"是 **pre-closing**，结构上**强制要求对方回一个 terminal token** 完成 **terminal exchange**，对话才算合法关闭。原文："a conversation does not simply end, but is brought to a close"。
→ 用户说"晚安"被 skip = bot 拒绝完成 terminal exchange。bot 该回的不是内容，是一个**对称的 terminal token**（"晚安哦"）。
来源：[Opening Up Closings](https://www.researchgate.net/publication/229068180_Opening_Up_Closings) · [emcawiki: Closing](https://emcawiki.net/Closing)

### 1.2 Clark & Schaefer「project markers」——"继续"和"收尾"用不同标记词（强/弱二分的语言学原型）

人类把回应词按功能分两类：

- **horizontal（继续话题）**：uh-huh / m-hm / yeah —— "我在听，你接着说"
- **vertical（进入/退出话题）**：okay / all right —— "接收了，可以翻篇"

→ 这就是"弱回复"的两个子型。**弱回复不是一档，是两档**。把它们混成一档，会让 bot 在"该收尾"时发"继续"信号（对"晚安"回"嗯嗯?"），反而拖住对话。
来源：[Navigating joint projects with dialogue (Clark)](https://onlinelibrary.wiley.com/doi/pdfdirect/10.1207/s15516709cog2702_3)

### 1.3 Malinowski「phatic communion」——大量交流目的是联结而非传信息

"phatic communion"：一类语言"既不交换信息也无特定目的"，纯粹用来建立/维持/强化社会纽带。
→ 正是"陪伴感"。reply-gate 现在按"这条要不要有信息量的回复"判，而 phatic 理论说：很多消息要的是"被回应"本身。
来源：[Phatic Communion as Unifier](https://jurnal.uns.ac.id/pjl/article/download/39416/34570)

### 1.4 Williams「ostracism / left on read」——不回应有真实关系伤害，且代价不对称

被忽视/"已读不回"显著降低自尊与控制感，即便极低风险场景。
→ 弱回复成本极低，skip 关系代价很高。**拿不准强/弱时，默认弱回复优于 skip**——有量化依据的偏置方向。
补充（Forbes "Noted"）：敷衍的冷 ack（"收到"）同样有害——弱回复**必须带温度**（语气、表情包），不能是冷"哦"。
来源：[Being Left on Read (Pitt)](https://sova.pitt.edu/social-media-guide-being-left-on-read-2) · [Forbes: "Noted"](https://www.forbes.com/sites/benjaminlaker/2026/05/27/what-your-colleagues-really-mean-when-they-reply-with-noted/)

### 1.5 调研给出的深化（用户描述里未明说）

弱回复**分两子型**，对应 Clark 的两方向：

| 维度 | 陪伴型 (companion / horizontal) | 收尾型 (closing / vertical) |
|---|---|---|
| Clark 标记 | uh-huh / yeah | okay / 晚安 |
| 触发场景 | 日常闲聊、对方在叙述 | 对方说晚安/睡了/先这样 |
| 载体 | "嗯嗯""哈哈""草"（纯文字）¹ | "晚安哦""好的呀"（纯文字）¹ |
| 语义意图 | "我在，你继续" | "收到，到这儿，关系还在" |
| 烤群例 | — | ✅ 就是这个 |

> ¹ **载体修正（2026-05-30 第三轮）**：原文写"轻表情包/挥手晚安表情包"是想当然——当前表情包是**按 ID 从频繁/最近池挑 + mood 调概率**（`StickerDecisionProvider`），**不支持"按 closing/companion 语义检索对应表情"**。`send_sticker` 工具只收 `sticker_id`，无 by-intent 入口。故弱回复载体**本期只做纯文字 token**；表情包载体（STICKER_ONLY）依赖新前置项「表情包语义检索」（见 §2.6），落地后再接入。注：issue17 pre-part0 表情包更新是**接收侧角色识别**（CCIP/AnimeTrace 识别图里是谁），与发送侧语义检索无关，且尚未落地。

## 2. 现状代码地图（关键发现：脚手架已存在）

仓里**早已定义四档** `ResponseClass`（[kernel/types.py:40](../../kernel/types.py#L40)）：

```python
class ResponseClass(StrEnum):
    SILENCE = "silence"
    LIGHT_ACK = "light_ack"      # ← 弱回复（文字 token）
    FULL_REPLY = "full_reply"
    STICKER_ONLY = "sticker_only" # ← 弱回复（纯表情包）
```

连弱回复占位文案都有：`_PASS_TURN_LIGHT_ACK = "嗯，我在。"`（[client.py:111](../../services/llm/client.py#L111)）。

**但决策链仍是二元的**，三处事实：

1. **scheduler 只在两档间切**：`prob fire → FULL_REPLY`（[scheduler.py:600](../../services/scheduler.py#L600)）/ `prob skip → SILENCE`（[scheduler.py:605](../../services/scheduler.py#L605)）。`LIGHT_ACK` / `STICKER_ONLY` **从不被 scheduler 选中**。
2. **LIGHT_ACK 唯一活路是事后兜底**：`pass_turn` 工具低置信度时（[client.py:4281](../../services/llm/client.py#L4281)）。这发生在**已决定回复、进了 chat、LLM 调了 pass_turn 之后**——是事后降级，不是主动决策。
3. **"晚安"场景死在更前面**：scheduler `prob skip → SILENCE`，**根本进不了 chat**，LIGHT_ACK 那段代码够不着。

**结论**：四档枚举存在，但缺真正的决策逻辑。本方案 = 给枚举补上触发入口，不是从零造。

### 2.1 这改变了"判定层"的可行性

用户初选"thinker 内判定"，但 thinker 在 `_do_chat → chat()` 内部（[scheduler.py:1268](../../services/scheduler.py#L1268)、[client.py:3872](../../services/llm/client.py#L3872)），而 **skip 的消息进不了 chat**。故"晚安→弱回复"**不可能只在 thinker 判**——必须在 **scheduler 层**就有"别 skip，降级成 LIGHT_ACK"的分支。

正确架构是**两层协作**：

```text
scheduler 层（决定 ResponseClass 档位）
  ├─ 强触发(@/directed) ─────────→ FULL_REPLY → _do_chat → thinker
  ├─ closing 关键词(晚安/睡了) ──→ LIGHT_ACK  → 轻量生成（不进完整 chat）   ★新
  ├─ prob fire ──────────────────→ FULL_REPLY → _do_chat → thinker
  ├─ prob skip 但"该被看见" ─────→ LIGHT_ACK（纯文字；STICKER_ONLY 待 §2.6）       ★新
  └─ prob skip 且 真无关 ────────→ SILENCE（现状）

thinker（FULL_REPLY 路径内，反向降级）
  └─ 判"这条只配弱回复" → light_reply → 回收过度回复                        ★P1
```

## 2.5 架构重审（2026-05-30 第二轮）：复用现有组件，不孤立新建

第一版第 3 层新建了三个组件（`_fire_light`/`_do_light_ack`/独立 closing 触发），重审发现 omubot 近期更新的组件**已提供等价机制，应复用而非重造**。逐一对照：

### 复用点 A：thinker→主生成的 hint 注入管道（addressee_hint / plugin_dynamic）

近期 Issue 15 instruction_gate 落地时已确立：thinker 的结构化决策要影响主生成，**唯一活管道是 `plugin_dynamic`**（`addressee_hint` 同款，[client.py:1321](../../services/llm/client.py#L1321) `_build_addressee_hint` → prompt 末尾）。instruction_gate 的 `_apply_instruction_gate`（[client.py:2370](../../services/llm/client.py#L2370)）就是把 hint append 进这条管道。
→ **companion 型弱回复（P1）应复用同管道**：thinker 判 `light_reply` → 注入一条"只回一句简短的、表示在场即可"的 hint 进 plugin_dynamic，让主 LLM 自然生成短回复，**而非新建 backchannel token 池**。语气/长短已被上一轮 mood→how 改造 + 该 hint 共同约束。

### 复用点 B：instruction_gate 的「DENY 直发 + 跳过主 LLM」模式（= 弱回复的结构原型）

`_apply_instruction_gate` 的 DENY 分支（[client.py:2424](../../services/llm/client.py#L2424)）已经实现了**弱回复要的全部机制**：`on_segment(text)` 直发一句话 + `[CQ:reply,id=...]` 引用 + `return None` 跳过主 LLM 调用。closing 弱回复在结构上**与 DENY 完全同型**（发一句固定/轻量话术，不走主生成）。
→ **closing 弱回复（P0）应在同一个 hook 点**（thinker reply 之后、主 LLM build 之前，[client.py:3974](../../services/llm/client.py#L3974)）以同样模式实现，而非在 scheduler 新建 `_do_light_ack` 平行管线。这样复用了 on_segment、CQ:reply、usage 记账、timeline 写入的全套既有逻辑。

### 复用点 C：directed_followup 的 scheduler bypass（closing 进 chat 的入口）

第一版误判"晚安会死在 scheduler skip"，需要在 scheduler 新建 LIGHT_ACK 分支。但近期 `feat(humanization): add directed followup scheduler bypass`（commit 4e04938）已建好**绕过概率掷骰直接 fire 的通道**：router 注入 `TriggerContext(mode="directed_followup")` → scheduler [notify:468](../../services/scheduler.py#L468) 识别后 `_fire()` 直接进 `_do_chat`。correction 模式（[notify:481](../../services/scheduler.py#L481)）同款。
→ **closing 应复用此 bypass**：router 检测 closing → 注入 `TriggerContext(mode="closing")` → scheduler 加一个与 directed_followup **并列的 bypass 分支** `_fire()` 进 chat。closing 消息因此**正常进 chat → thinker**，根本不需要新建 scheduler 平行管线。thinker 在 chat 内判 `light_kind=closing` → 走复用点 B 直发 terminal token。

### 复用点 D：并行 LLM（SpeculativeExecutor）—— 弱回复判定零延迟化

chat() 内已有 `SpeculativeExecutor`（[client.py:3862](../../services/llm/client.py#L3862)、[speculative_executor.py](../../services/llm/speculative_executor.py)）在 thinker 阶段并行预取（slang lookup）。它是通用的 `submit(coro, timeout)` 预测执行器。
→ **closing 的 terminal token 生成可预测执行**：thinker 一旦倾向 closing，可 speculative.submit 一个极短 LLM 生成 terminal token，与主链路其他预取并行；若最终确为 closing 则直取结果（零额外延迟），否则 `__aexit__` 自动 cancel。这正是 SpeculativeExecutor 的设计用途，无需新建并发原语。

### 重审结论：从「scheduler 平行管线」改为「复用 directed_followup bypass + thinker 内判 + instruction_gate 同款直发」

原方案的 scheduler `_fire_light`/`_do_light_ack` **整体废弃**。新落点全部在已有管道上：

| 原方案（孤立新建） | 重审后（复用现有） |
|---|---|
| scheduler `_fire_light()` 新管线 | 复用 directed_followup bypass，加 `mode="closing"` 并列分支 → `_fire()` 进 chat |
| scheduler `_do_light_ack()` 独立 LLM | thinker 内判 `light_kind` + instruction_gate 同款 `on_segment` 直发（复用点 B） |
| companion 新建 backchannel token 池 | thinker `light_reply` → plugin_dynamic hint 注入（复用点 A），主 LLM 生成短句 |
| closing token 同步阻塞生成 | SpeculativeExecutor 预测执行（复用点 D），零额外延迟 |
| 弱回复语气染色另写 | 复用上一轮 mood→how 改造（thinker 心情段 + provider mood_fit_target） |

## 2.6 前置项依赖：表情包语义检索（STICKER_ONLY 的阻塞项）

第三轮核实暴露：**当前表情包不支持按语义意图检索**，故弱回复的 STICKER_ONLY 载体**无法在本方案内实现**，须依赖一个独立前置项。

**现状证据**（`services/sticker/decision_provider.py`）：

- 候选池来源是四个 **ID 列表**（tool_call/kaomoji/frequent/thinker candidates），全是 `sticker_id`。
- 发送 `tool.execute(sticker_id=pool[0])`（[client.py:1520](../../services/llm/client.py#L1520)）——**取频繁/最近池第一个**。
- `rerank_strategy`（emotion/intent/persona）只是对**已有 ID 池重排序**（`fairmatch_rerank`）；mood 只影响**发不发/概率**（`_send_probability`），**无"按语义去库里找对应表情"的能力**。
- `send_sticker` 工具签名只收 `sticker_id`（[sticker_tools.py:138](../../services/tools/sticker_tools.py#L138)），无 by-intent 入口。

**与 issue17 pre-part0 的区别**（避免混淆）：

| | issue17 pre-part0 | 本前置项 |
|---|---|---|
| 方向 | **接收侧**：bot 收到表情包识别图里是谁（CCIP/AnimeTrace） | **发送侧**：按 closing/companion 语义从库里检索该发哪张 |
| 落地 | 未落地（调研完成，实现范围全空） | 未立项 |
| 与弱回复 | 无关（识别≠选发） | STICKER_ONLY 直接依赖 |

**前置项定义（待立项，不在本方案实施）**：给 `sticker_store` 加 description 向量检索 / 给 `send_sticker` 加 `by_intent` 入口，使"晚安"能检索到挥手类表情。STICKER_ONLY 排在其后。

**本方案对 STICKER_ONLY 的处理**：从 P1 主线**移出**，标注为"依赖 §2.6 前置项"。弱回复本期**只做纯文字 token**——closing/companion 的文字载体已足够覆盖烤群痛点（"晚安哦"/"嗯嗶"）。

### 第 1 层（P0，命中烤群痛点）：closing 复用 directed_followup bypass + instruction_gate 同款直发

**1a. closing 检测**（[services/reply_workflow.py](../../services/reply_workflow.py)）
新增 `classify_closing_intent(text) -> bool`：检测句尾/独立成句的 terminal token（晚安/睡了/我去睡/先这样/下了/撤了/明天见/拜拜/溜了）。规则层而非 LLM——terminal token 是高度约定化封闭集，规则足够准、零成本。要求消息短且 token 在句尾，长句带后续内容不算（防"先这样吧，我觉得X更好"误伤）。

**1b. router 注入 closing trigger**（[kernel/router.py:1360](../../kernel/router.py#L1360) 一带，directed_followup 注入点旁）
命中 closing 且 `last_assistant_to_user`/`has_recent_assistant` → 注入 `TriggerContext(mode="closing", target_message_id=..., target_user_id=...)`。与现有 directed_followup/correction 注入**同一处、同一模式**。

**1c. scheduler bypass 复用**（[scheduler.py:notify:468](../../services/scheduler.py#L468) directed_followup 分支旁）
加一个**与 directed_followup 并列的 closing 分支**：`is_closing → _fire()` 直接进 chat（复用点 C）。**不新建 `_fire_light`/`_do_light_ack`**。closing 消息因此正常进 `_do_chat → thinker`。

**1d. thinker 内判 + instruction_gate 同款直发**（[client.py:3974](../../services/llm/client.py#L3974) hook 点）
thinker 看到 `trigger.mode="closing"` 倾向输出 `action=light_reply, light_kind=closing`。chat() 在 thinker reply 之后、主 LLM build 之前（与 `_apply_instruction_gate` **同一 hook 点**）加一段 closing 短路：

- 生成对称 terminal token（依据 §1.2 用 vertical marker，不用 horizontal），通过 `on_segment(text)` 直发 + `[CQ:reply,id=...]` 引用 + `return None` 跳过主 LLM——**完全复刻 DENY 分支结构**（复用点 B）。
- token 生成走 SpeculativeExecutor 预测执行（复用点 D）：thinker 阶段一旦倾向 closing 即 submit 极短 LLM 生成，零额外延迟；超时 fallback `_PASS_TURN_LIGHT_ACK` 同款静态池（degraded 不静默）。
- 语气染色复用 mood→how 改造（低落→"晚安"安静些，心情好→"晚安啦~明天见"），不另写。

### 第 2 层（P1）：companion 弱回复（plugin_dynamic hint）+ prob-skip 救济

**2a. thinker 三态**（[thinker.py:30](../../services/llm/thinker.py#L30)、[:155](../../services/llm/thinker.py#L155)）
`_ALLOWED_ACTIONS` 加 `light_reply`，输出 JSON 加 `light_kind: companion|closing`。`ThinkDecision` 加 `light_kind` 字段——照抄 instruction_signal 的扩展模式（[thinker.py:184](../../services/llm/thinker.py#L184)：`__slots__` + ctor + `_normalize` + `_decision_from_data` + prompt 段，fallback `""`）。

**2b. companion 走 plugin_dynamic hint 注入**（复用点 A，**不新建 token 池**）
FULL_REPLY 路径内 thinker 判 `light_reply/companion` → 在 `_apply_instruction_gate` 旁注入一条 hint（"这条只需回一句简短的、表示在场即可，别展开"）进 plugin_dynamic（addressee_hint 同管道）→ 主 LLM 自然生成短句。**回收过度回复**：本来长篇降级成陪伴短句，且语气由 mood→how 改造约束。

**2c. STICKER_ONLY 出口（移出本方案，依赖 §2.6 前置项）**
当前表情包按 ID 挑选、不支持语义检索（见 §2.6），故"按 closing/companion 语义发对应表情"无法实现。STICKER_ONLY **本期不做**，待「表情包语义检索」前置项落地后再接入。`ResponseClass.STICKER_ONLY` 枚举保留占位。

**2d. prob-skip 救济**（[scheduler.py:602](../../services/scheduler.py#L602) skip 分支）
"本要 skip 但 `last_assistant_to_user`/关系近/对方刚分享" → SILENCE 降级为低频 closing-trigger 注入（复用 1b/1c 通道），而非新写救济管线。依据 §1.4：skip 关系代价 > 弱回复成本。

### 第 3 层（P2，防回潮节制）

- **弱回复冷却**：同一群 N 秒内至多一次 light，防"陪伴感"退化成"机器人不停嗯嗯"。新增 `slot.last_light_time`。
- **closing 去重**：一次 closing 只回一个 terminal token；对方再发"晚安×2"不重复回（§1.1：terminal exchange 完成后对话已合法关闭）。新增 `slot.closing_done` 旗标，超时/新话题复位。
- **companion 限频**：连续闲聊里 companion 也要稀疏，不逐条 backchannel。

## 4. 数据结构变更（最小化，复用现有扩展点）

```python
# kernel/types.py — TriggerContext.mode 增加 "closing"（与 directed_followup/correction 并列）
# services/scheduler.py — _GroupSlot 新增：
#   last_light_time: float = 0.0      # 弱回复冷却
#   closing_done: bool = False        # closing 去重
# services/llm/thinker.py — ThinkDecision 新增（照抄 instruction_signal 9 处穿透模式）：
#   light_kind: str = ""              # "companion" | "closing" | ""
#   _ALLOWED_ACTIONS 加 "light_reply"
# ⚠️ 不新增 _fire_light / _do_light_ack / backchannel token 池 / 独立 LLM 管线
```

## 5. 测试清单（D2/D4）

| 用例 | 断言 |
|---|---|
| `classify_closing_intent("好吧晚安")` | True |
| `classify_closing_intent("晚安是什么意思")` | False（带疑问，非纯 closing） |
| `classify_closing_intent("先这样吧我觉得X更好")` | False（长句带后续，非句尾 token） |
| `classify_closing_intent("今天好累")` | False |
| 晚安 → router 注入 `mode="closing"` → scheduler bypass `_fire` 进 chat | ✓ |
| thinker 见 closing trigger → `action=light_reply, light_kind=closing` | ✓ |
| closing 短路：`on_segment` 直发 terminal token + `return None`（不调主 LLM） | ✓ |
| SpeculativeExecutor 超时 → fallback 静态 token，不静默 | ✓ |
| closing 去重：连发两条晚安只回一次 | ✓ |
| 弱回复冷却：N 秒内第二次 light 被压成 SILENCE | ✓ |
| thinker `light_reply/companion` 解析 + plugin_dynamic hint 注入 | ✓ |
| mood 低落时 closing token 语气收敛（承接 mood→how 改造） | 快照断言 |
| cancel-path（D2）：closing 短路被 shutdown 取消，不污染 slot.closing_done / timeline | ✓ |

## 6. 划界（不做 / 留观察）

- 不动 FULL_REPLY 现有强回复管线。
- 不动 SILENCE 在"真无关"场景的判定（那是对的）。
- 不新建 scheduler 平行管线 / backchannel token 池——全部复用 §2.5 现有组件。
- `_PASS_TURN_LIGHT_ACK` 固定文案：作为 closing token 的 fallback 静态池保留并复用；不再单独清理。
- scheduler 的 `mood_mult`（自发概率链）不动——已确认作用在"自发 initiation"轴，符合 WTC 文献，非错配。

## 7. 风险与回滚

- **风险 1**：closing 词表误伤。缓解：检测要求消息短且 token 在句尾/独立成句（见 5 的负例）。
- **风险 2**：弱回复刷屏感。缓解：第 3 层冷却 + 去重必须与第 1/2 层**同批上线**。
- **风险 3**：closing token 生成延迟。缓解：SpeculativeExecutor 预测执行（与 thinker 并行）+ 超时 fallback 静态池——零额外串行延迟。
- **回滚**：closing 走 `TriggerContext(mode="closing")`，scheduler bypass 分支 + chat 短路注释即回到二元决策；thinker `light_kind` additive（fallback `""`）不影响旧逻辑。改 .py 需 rebuild bot。

## 8. 落地顺序建议

1. **P0 先行单独上线**：1a 检测 + 1b/1c bypass + 1d closing 短路 + 第 3 层 closing 去重/冷却（节制必须同批）。最小改动、全程复用现有管道，直接修烤群痛点。
2. **P1 次轮**：thinker 三态完整化 + companion hint 注入 + prob-skip 救济（纯文字 LIGHT_ACK）。STICKER_ONLY 不在本期，依赖 §2.6 前置项。
3. 每轮独立 maintenance-log 条目 + 回归测试 + rebuild。
