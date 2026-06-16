# Dialogue Climate M3/M4 全量实现方案

> 状态：**实现方案待批 · 2026-06-16**
> 前置：[M3/M4 调研报告](dialogue-climate-m3-m4-research-2026-06-16.md)（含 §5 用户决策冻结）、[Part A](living-persona-partA-dialogue-climate.md)、[M2 已落地 §9]
> 用户裁定（决策冻结，不可改）：① ClimateEngine 升 **per-(group,user)**；② tension **迁移到 ClimateEngine 唯一持有**（M1 退役）；③ CalendarSensor 接 **calendar_context 富版**；④ MessageSensor **复活 MoodClassifier**；⑤ **全量** 6 sensor + 反馈 + M4 让位都做。

---

## 0. 范围与原则

把 M2 休眠引擎接通为完整 Dialogue Climate Runtime：6 个 Sensor 喂信号 → ClimateDynamics 演化 → ClimatePolicy 合成 → Adapter 注入 prompt（M4 让位 schedule/affection 独立 block）。全程灰度开关 `m2_enabled`（已存在）+ 新增 `m3_sensors_enabled` / `m4_policy_enabled`，**默认全关，关时逐字节回归基线**。

**决策冻结表（执行不得改）：**

| # | 冻结决策 | 执行含义 |
| --- | --- | --- |
| F1 | ClimateEngine 键 **per-(group, user)** | `_states` 键从 `(group,session)` 改 `(group, user)`；加过期清理（复用 `clear_stale_per_session` 思路）|
| F2 | tension **唯一 owner = ClimateEngine** | M1 `_m1_tension_state` 退役；guidance/metrics/recorder 三个 M1 消费点改读 ClimateEngine；带 D3 旧→新清单 |
| F3 | CalendarSensor 接 **calendar_context 富版** | 接 `plugins/calendar_context/service.py:355` `get_day_context`（节假日/生日/农历），非 schedule/calendar |
| F4 | MessageSensor **复活 MoodClassifier** | 实例化 `MoodClassifier`，处理与 sticker 对 `MOOD_CURRENT_SLOT` 的语义共用（分槽或分 source）|
| F5 | 全程灰度默认关 | `m2_enabled`/`m3_sensors_enabled`/`m4_policy_enabled` 默认 false；关时零行为变更 |
| F6 | on_post_reply 字段对齐现实 | 用 `elapsed_ms/user_id/reply_content/user_msg`，**无** register/length/latency（设计主文措辞过时）|

---

## 1. Wave 拆分（串行，每 Wave 收口全绿才进下一个）

```
Wave M3-0 (ClimateEngine 升 per-(group,user) + 过期清理 + tension 迁移地基)
   ▼
Wave M3-1 (Sensor 适配层框架 + Schedule/Irritation/Circadian 三成熟 sensor)
   ▼
Wave M3-2 (Interaction[per-user familiarity] + Calendar[富版] + Message[复活 classifier])
   ▼
Wave M3-3 (on_post_reply 反馈回路 + ClimateMetricsRecorder 观测落盘)
   ▼  ← 此处可灰度开 m3_sensors_enabled 观察 m2_climate.db
Wave M4-1 (ClimatePolicy 合成 + ClimateProvider 注册 PromptProviderBus)
   ▼
Wave M4-2 (schedule/affection block 让位 + 单一 climate block + Humanizer/Thinker adapter)
   ▼  ← 灰度开 m4_policy_enabled，新路径夺旧路径前 shadow 对比
全量收口
```

### Wave M3-0 — ClimateEngine 升级 + tension 迁移地基

- **F1**：`dynamics.py` `ClimateEngine._key` 改 `(group_id, user_id)`；`register_signal`/`resolve` 签名加 `user_id`。加 `clear_stale(max_age)` 方法（镜像 dream `clear_stale_per_session`），按 `last_update_ts` prune。
- **F2 地基**：把 M1 `resolve_m1_tension_on_read` 的闭式语义确认与 ClimateDynamics.resolve 的 tension 维一致（已一致：baseline_for("tension")=0）；规划 M1→M2 tension 迁移路径（不在本 wave 切流量，仅建迁移函数 + 测试）。
- 测试：per-(group,user) 键隔离、过期清理、tension 维迁移等价性。
- 收口：`m2_enabled` 仍默认关；改键不影响休眠态（无消费者）。

### Wave M3-1 — Sensor 框架 + 三成熟 sensor

- 新增 `services/dialogue_climate/sensors.py`：`Sensor` 协议（`sense(ctx) -> list[ClimateSignal]`）+ `SensorHub`（聚合调度，`m3_sensors_enabled` 门控）。
- **ScheduleSensor**：读 MoodEngine `evaluate` 的 4 维 → ClimateSignal（energy/valence/openness/tension）。
- **IrritationSensor**：复用现有 router→qq_interactions 链路，改为喂 ClimateEngine（F2 切流量在此 wave，M1 `_m1_tension_state` 停写）。
- **CircadianSensor**：把 `mood.py:533-542` 的深夜/午后修正抽为 sensor（energy 维）。
- 测试 + D3 清单（M1 tension 旧路径 → ClimateEngine 新路径四列）。

### Wave M3-2 — 三个需改造的 sensor

- **InteractionSensor**（F1）：读 AffectionEngine per-user familiarity（`AFFECTION_FAMILIARITY_SLOT`）→ 喂 ClimateState(per-group,user) 的 familiarity/trust 维。per-user 槽与 per-(group,user) 键天然对齐。
- **CalendarSensor**（F3）：接 `calendar_context/service.py` `get_day_context` → 节假日/生日 → valence/energy 维。net-new 接线，注意不要误 import schedule/calendar。
- **MessageSensor**（F4）：实例化 MoodClassifier，classify 用户消息语气 → valence/openness/tension。处理 `MOOD_CURRENT_SLOT` 与 sticker 反馈共用（建议 MessageSensor 走 ClimateEngine 内部状态，不写该公共槽，避免撞 sticker）。

### Wave M3-3 — 反馈回路 + 观测

- 新增 ClimatePlugin（或挂现有插件）`on_post_reply`：用 `elapsed_ms/reply_content/user_id`（F6）注入交互信号回 ClimateEngine。
- 新增 `services/dialogue_climate/m2_metrics.py` `ClimateMetricsRecorder`：镜像 `m1_metrics.py`，写 `storage/living_persona/m2_climate.db`（6 维快照 + signal + ts），供灰度校准。
- **此处可灰度开 `m3_sensors_enabled`**：引擎有信号进、有数据看（回应用户"开了观察什么"）。

### Wave M4-1 — Policy 合成 + Provider

- 新增 `services/dialogue_climate/policy.py` `ClimatePolicy.synthesize(state) -> PolicyOutput`（mood_label/reply_bias/delay_multiplier/openness_hint/sticker_prob/register_override，设计主文 §4）。
- 新增 `ClimateProvider`（PromptProviderBus，复刻 register_provider 范式）：`provide()` 读 ClimateState → PromptBlockCandidate。
- M4 自有 contract/slot（避免 RuntimeStateBus ownership raise）。

### Wave M4-2 — 让位 + Adapter

- schedule（`plugin.py:112/123` 的"当前时间"+"对话气氛" block）+ affection（`plugin.py:65` "与当前用户的关系" block）加 `has_provider("climate")` 让位 guard（复刻 slang/style `plugin.py:190-193/557`）。
- 单一 climate block 替代上述独立 block。
- HumanizerAdapter（delay_multiplier）+ ThinkerAdapter（reply_bias）让位现有 ad-hoc 读取。
- **灰度 shadow → active**：`m4_policy_enabled` 默认关；shadow 态对比新旧 block 再夺路径。

---

## 2. 改动文件清单（预估）

**新增**：`sensors.py`、`policy.py`、`m2_metrics.py`、`provider`（climate）、`tests/test_climate_sensors.py`、`tests/test_climate_policy.py`、`tests/test_climate_m2_metrics.py`。
**改**：`dynamics.py`（per-(group,user) + clear_stale）、`mood.py`（tension 迁移退役 M1 + circadian 抽 sensor）、`qq_interactions.py`（irritation 切 ClimateEngine）、`plugins/schedule/plugin.py`（让位 + flag）、`plugins/affection/plugin.py`（让位）、`plugins/chat/plugin.py`（wiring ClimateEngine/recorder/sensors）、`kernel/types.py`（ctx 透传 flag）、config 4 文件（加 m3/m4 flag）、calendar_context 接线。
**文档**：Part A §9 续写 M3/M4、dispatch 执行回执、maintenance-log。

## 3. 风险与回滚

- **R-迁移**：tension 从 M1 迁 ClimateEngine 是改上线路径（M1 现在 m1_enabled=true 在跑）。缓解：M3-0/M3-1 带 M1→M2 等价性测试 + D3 四列清单；切流量前 shadow 对比 guidance 输出一致。
- **R-per-user 爆量**：per-(group,user) 状态数 = 群数 × 群友数。缓解：`clear_stale` 过期清理 + cap。
- **R-MOOD_SLOT 撞车**：MessageSensor 复活 classifier 不写公共槽，走 ClimateEngine 内部。
- **回滚**：三个 flag 默认关 = 零行为变更；每 Wave 独立可停；M4 让位前 shadow，不验证不夺旧路径。M1 退役在 flag 关时仍走旧 M1（迁移保留双跑过渡）。

## 4. 收口判据

- 每 Wave：D1 grep + 验证命令 + 回滚；flag 关逐字节回归基线；ruff+pyright 0 新增；pytest 全绿（D5 先 pkill）。
- 全量：6 sensor 信号入 ClimateState 可观测（m2_climate.db）；M4 单一 climate block 替代 schedule+affection 独立 block 且 shadow 对比一致；tension 迁移后 M1 guidance 行为不变（D3 清单全绿）；NapCat 红线不动。
