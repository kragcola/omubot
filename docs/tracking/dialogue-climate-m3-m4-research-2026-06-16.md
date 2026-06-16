# Dialogue Climate M3/M4 全量实现 — 调研报告

> 状态：**调研 · 2026-06-16**（M2 引擎已落地休眠，见 [Part A §9](living-persona-partA-dialogue-climate.md)）
> 关联：[Part A](living-persona-partA-dialogue-climate.md)（M3/M4 Phase 定义）、[设计主文](omubot-grayscale-issue17-research-dialogue-climate.md)（§4 Sensor 架构、§5 Phase 3/4）
> 目标：回答"M3 全量（6 个 Sensor + 反馈回路）+ M4（Policy + Adapter 让位）要接的信号源在当前代码里的真实状态"，为实现立项排雷。用户裁定：**不做最小闭环，要做就全量**。

---

## 0. 结论摘要

全量 M3/M4 技术上可行，现有基建（RuntimeStateBus、PromptProviderBus 让位、on_post_reply、MoodEngine on-read）都在热路径上就绪。但代码现实与设计主文 §2 的六系统表**有 4 处关键偏离**，直接改变 M3 实现方式，必须在立项时正面持有：

1. **per-user vs per-group 维度冲突（最高优先级）**：AffectionEngine 的 familiarity 是 **per-user** 槽，而 M2 `ClimateEngine` 状态是 **per-(group, session)** 键。InteractionSensor 把 familiarity 喂进 ClimateState 前必须先解决"按谁聚合"。
2. **两套 DayContext 打架**：mood 路径用的是 `plugins/schedule/calendar.py` 的 `get_day_context`，而更丰富的 `plugins/calendar_context/service.py`（节假日/生日/农历/自报名）**根本没被 mood 消费**。CalendarSensor 必须先定哪个是真值源，naive import "get_day_context" 会拉错。
3. **tension 双写风险**：IrritationSensor 已经在写 MoodEngine 的 `_m1_tension_state`（M1 链路），M3 若再开第二个 tension writer 喂 ClimateEngine 会双计。
4. **MoodClassifier 是死代码**：MessageSensor 的设计源（MoodClassifier）无任何生产调用；但它本该写的 `MOOD_CURRENT_SLOT` 反而**活着**、被 sticker 密度反馈路径占用。MessageSensor 要么复活 classifier、要么另起信号。

---

## 1. 六个 Sensor 信号源现状（file:line 实证）

| Sensor | 设计源 | 现状 | 接入点 / 证据 | 聚合维度 |
| --- | --- | --- | --- | --- |
| **ScheduleSensor** | MoodEngine | ✅ 活 | `mood.py:235` `evaluate`（被 `router.py:524` + `chat/plugin.py:1480` 消费）；`mood.py:313` `register_interaction_signal` | per-(group, session) |
| **IrritationSensor** | @ / poke 频率 | ✅ 活（M1 已建） | router `1550` → `qq_interactions.py:211` `register_m1_mention_irritation` → `mood.py:165` → `mood.py:343` `_m1_tension_state` | per-group（tension）/ per-(group,user)（频率） |
| **InteractionSensor** | AffectionEngine | ✅ 活，**per-user** | `affection/engine.py:48` `record_interaction`（`affection/plugin.py:75` on_post_reply 调）；familiarity 槽 `contract.py:12` ttl=per_user | **per-user**（与 M2 冲突） |
| **CalendarSensor** | CalendarContextService | ⚠️ 活但 mood 不读 | mood 实际用 `schedule/calendar.py:245`；rich 版 `calendar_context/service.py:355` 仅挂 `ctx.calendar_service` 无消费者 | — |
| **MessageSensor** | MoodClassifier | ❌ classifier 死代码 | 无生产 `MoodClassifier(` / `.classify(`；`MOOD_CURRENT_SLOT` 被 `sticker/decision_provider.py:289/274` 占用 | — |
| **CircadianSensor** | 时间/作息 | ✅ 活（内联） | 深夜修正 `mood.py:536-539`、午后 `mood.py:540-542`；CLOCK 槽另由 `thinker.py:567` 写、scheduler/client/register 读 | per-turn 特征 |

---

## 2. 四处关键偏离详解

### 2.1 per-user vs per-group 维度冲突（必须先裁定）

- M2 `ClimateEngine._states` 键是 `(group_id, session_id)`（见 `services/dialogue_climate/dynamics.py`）；trust/familiarity 维注释为"概念上 per-user，M2 暂按 key 存"。
- AffectionEngine familiarity 是 **per-user**（`AFFECTION_FAMILIARITY_SLOT` ttl=per_user，`contract.py:40`），跨 group 不分。
- **冲突**：群里 50 个人各有各的 familiarity，喂进一个 per-group 的 ClimateState.familiarity 该取谁？三个候选：(a) 取当前说话人的 familiarity（per-turn 覆盖）；(b) ClimateEngine 升级为 per-(group, user) 键；(c) familiarity/trust 维不进 per-group ClimateState，单独走 per-user 子状态。**这是 M3 第一个要定的架构点。**

### 2.2 两套 DayContext

- mood 真值源：`plugins/schedule/calendar.py:245` `get_day_context`（`mood.py:11` import，`mood.py:551/659` 调用）。
- 闲置富版：`plugins/calendar_context/service.py:355`（节假日/生日/农历/自报名俱全），仅 `calendar_context/plugin.py:83` 挂 `ctx.calendar_service`，**无人调 `get_day_context`**。
- **裁定点**：CalendarSensor 用哪个？若要富信号（生日/农历）须接 calendar_context，但那是 net-new 接线；若沿用 schedule/calendar 则信号弱。

### 2.3 tension 双写

- IrritationSensor → `_m1_tension_state`（M1，per-(group,session)，on-read 闭式，已上线）。
- M3 若让 IrritationSensor 也喂 `ClimateEngine.register_signal(dim="tension")` → 同一信号进两个 tension 容器，双计。
- **解法**：M3 阶段 tension 维由 ClimateEngine 单一持有，M1 的 `_m1_tension_state` 作为过渡保留/迁移；或 IrritationSensor 只喂一处、另一处从其读。需在 M3 设计明确"tension 的唯一 owner"。

### 2.4 MessageSensor 信号源缺失

- `MoodClassifier`（`services/humanization/mood_classifier.py`）死代码：仅 export + 测试，无生产实例化。
- 它本该写的 `MOOD_CURRENT_SLOT` 活着但被 sticker 密度反馈占用（`sticker/decision_provider.py:289` 写、`:274` 读）。
- **裁定点**：MessageSensor 要么复活 MoodClassifier（注意会撞 sticker 反馈对该槽的语义）、要么新写一个轻量消息语气信号源。

---

## 3. M4 让位机制（已就绪，可复刻）

- **两条总线分清**：状态走 `RuntimeStateBus`（`state_bus.py:43`，**有 ownership 强校验**，写非自有槽 raise `StateSlotOwnershipError`，`state_bus.py:95`）；prompt provider 让位走 `PromptProviderBus`（`provider_bus.py:19`，off/shadow/active 三态）。
- **让位范式（slang/style 实证）**：legacy 插件 startup 查 `provider_bus.has_provider("<name>")` → 置 `_provider_superseded` → 跳过自己的 `ctx.add_block`（slang `plugin.py:190-193/557`；style `plugin.py:87-90/142`）。Provider 端从 `provide()` 返回 `PromptBlockCandidate`（`register_provider.py:26/46`），内部读 RuntimeStateBus 槽。
- **M4 实现要点**：① 注册一个 `ClimateProvider` 到 PromptProviderBus；② SchedulePlugin / AffectionPlugin 的 prompt 注入加 `has_provider("climate")` 让位 guard；③ M4 要写状态须**自有 contract/slot**（不能写 humanization 自有槽，否则 ownership raise），或用 `humanization_source(...)`。

## 4. on_post_reply 反馈回路（就绪，字段须对齐）

- 定义 `kernel/types.py:605`，`ReplyContext` 字段 `kernel/types.py:386-398`：`session_id/group_id/user_id/reply_content/user_msg/tool_calls/elapsed_ms/thinker_action/thinker_thought`。**无 register/length/latency**——latency 是 `elapsed_ms`，设计主文 §4 写的"register/length/latency"需改为现有字段。
- 已有实现：affection（`plugin.py:67`，用 user_id）、memo（`plugin.py:213`，用 user_msg/reply_content）、style（`plugin.py:166`，用 elapsed_ms/thinker_action/tool_calls）。M3 的反馈 sensor 加一个 on_post_reply 即可，按 priority 串行、互不冲突。

---

## 5. 待裁定清单（实现立项前必须回答）

> **2026-06-16 用户裁定（决策冻结，实现按此执行）：**
>
> - **§2.1 familiarity 聚合 → ClimateEngine 升 per-(group, user) 键**。每个群友在每个群有独立 ClimateState；trust/familiarity 自然落位。代价：状态数 × 群友数，需加过期清理（复用 dream `clear_stale_per_session` 思路）。
> - **§2.3 tension owner → 迁移到 ClimateEngine 唯一持有**。M1 `_m1_tension_state` 退役，IrritationSensor 只喂 ClimateEngine.tension；须带 M1→M2 迁移 + 现有 M1 上线路径（guidance/metrics）改读 ClimateEngine 的回归清单（D3）。
> - **§2.2 CalendarSensor → 接富版 calendar_context**（net-new 接线，节假日/生日/农历/自报名）。
> - **§2.4 MessageSensor → 复活 MoodClassifier**（须处理它与 sticker 密度反馈对 `MOOD_CURRENT_SLOT` 的语义共用，避免互相覆盖）。
> - 总原则：**不做最小闭环，全量 6 sensor + 反馈 + M4 让位都做**。

- [x] §2.1 familiarity 聚合 → **per-(group,user)**
- [x] §2.3 tension owner → **ClimateEngine 唯一持有**（M1 迁移退役）
- [x] §2.2 CalendarSensor 源 → **富版 calendar_context**
- [x] §2.4 MessageSensor → **复活 MoodClassifier**
- [ ] M4 让位顺序：ClimateProvider 上线后 SchedulePlugin + AffectionPlugin 两 block 是否合并为单一 climate block（待实现方案细化）
- [ ] 灰度：M3/M4 全量分几个 PR（待实现方案）

---

## 附：核验命令留痕（D4）

```text
mood.py:235/313                          → MoodEngine evaluate / register_interaction_signal
affection/engine.py:48/75/82            → record_interaction / familiarity_score / _write_familiarity_state(per_user)
calendar_context/service.py:355         → get_day_context(rich, 无 mood 消费者)
schedule/calendar.py:245                → get_day_context(mood 实际源)
grep 'MoodClassifier(' 非测试            → 0（死代码确认）
sticker/decision_provider.py:289/274    → MOOD_CURRENT_SLOT 真实生产者/消费者
router.py:451/1550                       → _addressing_triggers_m1_mention 触发
state_bus.py:43/95                        → RuntimeStateBus + ownership 强校验
provider_bus.py:19/36 + slang/style      → PromptProviderBus 让位范式
kernel/types.py:386/605                   → ReplyContext 字段 / on_post_reply（无 register/length）
```

> 本轮**纯 read-only 调研**，未改任何代码。下一步：就 §5 待裁定清单逐条与用户确认后，再出 M3/M4 实现方案。
