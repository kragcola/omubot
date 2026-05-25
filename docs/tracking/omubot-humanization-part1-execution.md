# Omubot 拟人 Part 1 — 派单版并列执行追踪

> 状态：2026-05-25 立。本文是 [Part 1 主线](./omubot-humanization-part1-language-feel.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> **执行原则**（以下规则覆盖任何主线文档的不一致表述）：
>
> 1. **每条独立 commit**——除非本文明确写"合 commit"。Part 1 主线 §5.2 的 8-commit 节奏是**理想合并目标**，不是派单单位。
> 2. **同 wave 内任务可并行**——不同 wave 间严格串行。
> 3. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒回滚开关**，缺一不通过验收。
> 4. **遇主线证据与本文冲突，以本文为准**（§1 已记录主线 5 处证据订正）。

---

## 1. 主线自审与证据订正（执行前必读）

下表是我对 Part 1 主线 §0a / §3 / §5 进行 grep 实证后发现的与原文不符的项。**派单时按本表订正，不按主线原文**。

| 主线位置 | 主线原文 | grep 实证 | 派单订正 |
|---|---|---|---|
| §0a.2 P0.3 | `reply_workflow.shadow_log_private` 死字段 | [kernel/router.py:1076](../../kernel/router.py#L1076) 真正读取；[kernel/config.py:988](../../kernel/config.py#L988) 默认值；测试 `test_reply_workflow.py:29` 锁定 | **P0.3 撤销**——不是死字段。位置标记 `❌ 证据不成立` |
| §0a.2 P0.4 | mood block 双注入（mood.py:270 build_mood_block 与 client.py:1794 `_build_thinker_mood_text` 同源 valence 写两次）| 两个函数服务于**不同 LLM call**：`build_mood_block` 给主 LLM prompt（[plugins/schedule/mood.py:270](../../plugins/schedule/mood.py#L270)）；`_build_thinker_mood_text` 给 thinker haiku prompt（[services/llm/client.py:1955](../../services/llm/client.py#L1955)）。**不是同 prompt 双注入** | **P0.4 撤销**——并存合理。位置标记 `❌ 证据不成立` |
| §0a.2 P0.1 | `reply_segmentation` 9 个字段是死字段 | [services/llm/segmentation.py](../../services/llm/segmentation.py) 已实现 `max_send_segments` / `soft_max_send_segments` / `boundary_backend` / `preserve_ascii_tokens` / `merge_short_tail` 等字段；[tests/test_segmentation.py](../../tests/test_segmentation.py) 覆盖软/硬上限、token 保护、短尾合并；管理端配置页也暴露这些字段 | **P0.1 撤销**——不是死字段。当前真正问题是 U1 尚未把 `services/llm/client.py` 旧 `_reply_segments` 合并到新分段模块，不能在 P0 清债阶段删除新模块字段 |
| §0a.2 P0.2 | `[scheduler.concurrency]` 5 个字段是死字段 | 全仓 grep 仅命中 `kernel/config.py` schema、`config/config.*` 默认值、管理端配置页展示；生产运行代码 0 caller。Pydantic 默认忽略旧配置残留，旧 `scheduler.concurrency` 不会导致加载失败 | **P0.2 保留删除任务**——删 schema / 默认配置 / 管理端配置页暴露面；运行时无行为变更 |
| §0a.2 P0.6 | GroupSendQueue 0 caller 死代码 | 生产代码 0 caller 属实，但 `tests/test_send_queue.py` 有 **8 个 use case / 223 行**测试；[group-concurrency-implementation-tracker.md](../group-concurrency-implementation-tracker.md) 记录 `ReplySegmentBatch` 是可见发送收口基础；[reply-segmentation-persona-response-research.md](./reply-segmentation-persona-response-research.md) 明确要求普通回复后续走 `ReplySegmentBatch` | **P0.6 评估结论：保留**——不是普通死代码，是 U1 / Part 5 发送收口的已测预留基础；当前任务只记录证据，不删除 |
| §0a.2 P0.7 | admin/`__init__.py` 与 plugin 启动钩子各启一次 | 实际是 [bot.py:206](../../bot.py#L206) `_plugin_ctx.backup_scheduler = BackupScheduler(...)` + 原 [bot.py:308](../../bot.py#L308) `_backup_scheduler = BackupScheduler(...)`，**两处都在 bot.py**，不是 admin + plugin。admin backup API 已读 `ctx.backup_scheduler`；`kernel/router.py` 也启动/停止 ctx 实例 | **P0.7 已按代码事实反向收口**——保留 `ctx.backup_scheduler` 单实例，删除 bot.py 后半段全局 `_backup_scheduler`；system backup settings reload 改读同一个 ctx 实例 |
| §0a.2 P0.8 | thinker `on_thinker_decision` 0 订阅 hook | hook 是 [kernel/types.py:503](../../kernel/types.py#L503) `AmadeusPlugin` **基类协议字段**（noop），fire 点在 [services/llm/client.py:1637](../../services/llm/client.py#L1637) 真在生产 fire；目前确实 0 子类 override，**但这是 PluginBus 协议预留的扩展点，0 subscriber ≠ 死代码** | **P0.8 改语义**——不删 hook（删 hook 破坏 PluginBus 协议契约）；改为「V13 改走 RuntimeStateBus 之后，hook 仍保留为插件扩展点，但生产代码不再依赖 fan-out 结果」。文案订正后保留 |
| §3 U13 | 与 V13 / U11 关系 | 没有变更 | 无 |
| §5.1 Provider priority | V2 RegisterProvider=25 / V3 CatchphraseProvider=15 / U8 StickerRegisterProvider=18 / V15 ThinkerProvider=12 | 实证 [budget_manager.py:75](../../services/block_trace/budget_manager.py#L75) 按 `priority ASC` 排序并先处理先占预算；[slang_provider.py:79](../../services/block_trace/slang_provider.py#L79) priority=**40**；[style_provider.py:79,115](../../services/block_trace/style_provider.py#L79) priority=**42 / 45**；[episode_provider.py:41](../../services/block_trace/episode_provider.py#L41) 注释写法与实际 low-wins 语义相反但数值 50 的裁剪目标成立 | **P0.0 结论：BlockTrace 是 low-wins**——数值越小越先入预算、越不容易被裁。新 Provider priority 不可照主线使用 12/15/18/25 这类过低值，否则会压过现有 slang/style；后续 Provider 应围绕现有 40 / 42 / 45 / 50 校准（要弱于 style expression 则用 >45，要强于 slang 则用 <40） |
| §6.1 测试条数 | "≥110 条" | 逐行加和实际 ≈ 104 条（test_humanizer_register / test_mood_engine 与 U3 / U2 双计 11 条，扣除后） | 验收按 ≥ **104 条新测试** + Part 0 单测 ≥ 6 条（P0.3/P0.4 撤销后）= **≥ 110 条**——数量修正后凑齐 |
| §11 状态表 | 30 行 | P0.3 / P0.4 撤销后剩 28 行 | 状态表保留空位，标 ❌ |

> **派单规则**：执行者拿到本文档后，**P0.3 / P0.4 不要做**，按 §1 订正版执行。

---

## 2. P0.0 新增前置任务（Provider priority 语义验证）

派单第 0 步，零代码改动。

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `grep -n "priority\|sort.*priority\|key=lambda.*priority\|reverse=" services/block_trace/*.py` | 找到 PromptBudgetManager / PromptProviderBus 排序逻辑 |
| 2 | 读 `services/block_trace/budget_manager.py` priority 排序代码 | 确定 high-wins / low-wins 语义 |
| 3 | 写 1 行结论到本文 §1 第 7 行（替换"待验证"） | 给 V2 / V3 / U8 / V15 派单确定 priority 数值 |

**P0.0 不是 commit；是派单前置验证**。我会先看本步骤回执再发后续单。

---

## 3. 并列执行 Wave 表（按依赖图编排）

**依赖关系核心规则**：

- **Wave 0**：P0.0 priority 语义验证（前置，零代码）
- **Wave 1**：P0 屎山清债（P0.1 / P0.2 / P0.5 / P0.6 / P0.7 / P0.8）—— P0.3 / P0.4 已撤销
- **Wave 2**：U 系列重构（U1~U5 + U10）—— 6 条互不依赖，可并行
- **Wave 3**：U6 humanization ModuleContract（**v3 单点突破，必须独立完成**）
- **Wave 4**：依赖 U6 的扩展（U7 / U11 / V0 / V1 / V5 / V13 / V14）—— 7 条可并行（V13 依赖 U11，组成 sub-pair）
- **Wave 5**：Provider 系列（V2 / V3 / V4 / V6 / U8 / V15）—— 6 条可并行
- **Wave 6**：Scorer / Rewrite / Runtime 装配（V8 / V9 / V10 / V11 / U12 / V16 / V17 / U13）—— 8 条
- **Wave 7**：种子 + 灰度（V7 + 灰度阶段 1~3 配置）
- **Wave 8**：V12 文档收口

### 3.1 Wave 1 — P0 屎山清债（6 条并列）

> 撤销：P0.3 / P0.4。剩 6 条。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P0.1** | 删 `[reply_segmentation]` 死字段 9 个（保留 enabled / max_segment_chars / min_segment_chars / prefer_sentence_break） | `config/config.toml`+`config/config.json`+`kernel/config.py:ReplySegmentationConfig` | grep `max_send_segments\|soft_max_send_segments\|soft_limit_notice\|boundary_backend\|preserve_ascii_tokens\|merge_short_tail\|first_segment_humanize\|later_segment_humanize\|inter_segment_delay_s` 仅命中 schema 删除项与 tests | N/A（纯 schema） | git revert |
| **P0.2** | 删 `[scheduler.concurrency]` 死字段 5 个 | 同上路径 | grep `global_llm_limit\|max_group_queue\|max_low_priority_queue\|first_segment_release\|drop_stale_low_priority_after_s` 0 caller | N/A | git revert |
| **P0.5** | 删 `card_store.close()` 重复一处（保留 [plugins/chat/plugin.py:1052](../../plugins/chat/plugin.py#L1052)） | `plugins/chat/plugin.py`（-1 行） | grep `card_store.close` 仅 1 处命中 | N/A（close 幂等） | git revert |
| **P0.6** | **先评估再决定**：grep `send_queue` commit 历史确认是否为预留；若是则保留，否则删 | 见 §1 P0.6 升风险说明 | grep `from services.send_queue\|GroupSendQueue` 仅 tests 命中 | N/A | git revert |
| **P0.7** | BackupScheduler 单一实例化：删 [bot.py:206](../../bot.py#L206) `_plugin_ctx.backup_scheduler = BackupScheduler(...)`，保留 [bot.py:308](../../bot.py#L308) 的；admin SPA 改读 `_backup_scheduler` 全局 | `bot.py`（≈ -10 行）+ `admin/routes/api/backup.py`（如有 inject 依赖更新） | grep `BackupScheduler(` 仅 1 处命中（除 tests） | 24h 后看 `storage/logs/` 备份运行只有 1 次 | git revert |
| **P0.8** | 文案订正：hook 保留作扩展点，client.py:1624-1641 `_fire_thinker_decision` 不动；本任务**仅在主线文档加 1 段说明**（无代码改） | `docs/tracking/omubot-humanization-part1-language-feel.md` §0a.2 第 P0.8 行 | N/A | N/A | git revert |

**Wave 1 单 commit 合并建议**：P0.1 + P0.2 + P0.5 = C0a；P0.6 / P0.7 各自独立 commit；P0.8 跟 V13 一起做（hook 改语义不删）。

### 3.2 Wave 2 — U 系列重构（6 条并列）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **U1** | segmentation 双实现合并：`services/llm/client.py:359-538 _reply_segments` 替换为 `services/llm/segmentation.py` 导入；行为等价合并 | `client.py`（≈ -180 行）+ `segmentation.py`（导出 `reply_segments()`） | `tests/test_reply_segmentation.py` 全绿不变 | — |
| **U2** | MoodEngine cache 改 dict[(group, session), (profile, ts)]；`recent_interaction_count` 真接 GroupTimeline 60s 计数 | `plugins/schedule/mood.py:115-225` | `tests/test_mood_engine.py` +6 | — |
| **U3** | Humanizer 新签名 `delay(text, *, group_id, register, slot, mood)`；老签名兼容 1.0x | `services/humanizer.py`（≈ +20 行） | `tests/test_humanizer_register.py` +5 | U2（mood 入参） |
| **U4** | StyleStore `_expression_relevance` 加 mood_fit / persona_fit 加权 0.3 / 0.2 | `services/style/store.py:1826-1843` | `tests/test_style_relevance.py` +4 | U2（mood 入参） |
| **U5** | learning_normalizer 注册 domain="catchphrase" | `services/learning_normalizer/normalize.py:55-119`+`store.py` | `tests/test_learning_normalizer_catchphrase.py` +3 | — |
| **U10** | `services/runtime_clock.py` 新建 ≤ 80 行；mood.py / client.py:881 改 import | `services/runtime_clock.py`（new）+ `mood.py`+`client.py:881-885` | `tests/test_runtime_clock.py` +6 | — |

**Wave 2 单 commit**：U1 / U2 / U3 / U4 / U5 / U10 各自独立 commit（重构粒度，不合并），共 6 个 commit；可并列开发但 commit 顺序 U2 → U3/U4 → 其它。

### 3.3 Wave 3 — U6 humanization ModuleContract（独立单点）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **U6** | RuntimeStateBus 首个生产 owner：新建 `services/humanization/{__init__,contract,state}.py` ≤ 180 行；声明 owner 拥有 `state.register.*` / `state.sticker.recent_used` / `humanization.last_metrics`；PluginContext 装配 | `services/humanization/`（new）+ `bot.py` 装配 1 行 | `tests/test_humanization_contract.py` +5（owner enforcement / TTL / cancel-path / per_turn / 多 owner raise） | — |

**v3 最关键 commit**——本 wave 单条不合并。

### 3.4 Wave 4 — 依赖 U6 的扩展（7 条并列）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **U7** | DreamAgent 加段：清 RuntimeStateBus per_session > 30 min | `plugins/dream/plugin.py:274-281`（+30 行） | `tests/test_dream_humanization_cleanup.py` +3 | U6 |
| **U11** | thinker 决策写 `bus.state.thinker.last_decision` per_turn；hook 保留（P0.8 订正） | `services/llm/thinker.py:320-403`（+15 行） | 与 V13 共享 | U6 |
| **V0** | `[humanization]` config 段 + 6 字段全默认 off | `kernel/config.py`（+30 行） | `tests/test_humanization_config.py` +6 | — |
| **V1** | RegisterClassifier（haiku one-shot, 5 轮窗口） | `services/humanization/classifier.py` ≤ 140 行 | `tests/test_register_classifier.py` +6 | U6 + V0 |
| **V5** | AffectionEngine.familiarity_score 扩展，写 `bus.state.affection.<uid>.familiarity` per_user TTL=60min | `plugins/affection/engine.py`（+25 行） | `tests/test_affection_familiarity.py` +4 | U6 |
| **V13** | thinker.last_decision 写 RuntimeStateBus（U11 落地点）| 与 U11 同改动，加测试 | `tests/test_thinker_runtime_state.py` +5 | U6 + U11 |
| **V14** | thinker time_text 注入：`_build_thinker_time_text(slot, weekend)` | `services/llm/thinker.py` + `services/runtime_clock.py` 复用 | 共享 U10 测试 | U10 + U6 |

**Wave 4 commit 顺序**：V0 → U6 已落 → 7 条并列开发；建议两轮提交（V0+V1+V5 / U7+U11+V13+V14）。

### 3.5 Wave 5 — Provider 系列（6 条并列）

> 数值待 P0.0 priority 语义验证后回填。

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **V2** | RegisterProvider stable bucket | `services/block_trace/register_provider.py` ≤ 95 行 | `tests/test_register_provider.py` +5 | U6 + V1 |
| **V3** | CatchphraseProvider dynamic bucket | `services/block_trace/catchphrase_provider.py` ≤ 100 行 | `tests/test_catchphrase_provider.py` +5 | U5 + V1 |
| **V4** | EpisodeProvider register-aware 过滤 | `episode_provider.py`（+10 行） | `tests/test_episode_provider_register.py` +3 | V1 |
| **V6** | SlangProvider mood-fit 加权 | `services/block_trace/slang_provider.py`（+8 行） | `tests/test_slang_provider_mood.py` +3 | U2 + U4 |
| **U8** | StickerRegisterProvider | `services/block_trace/sticker_register_provider.py` ≤ 90 行 | `tests/test_sticker_register_provider.py` +4 | U6 |
| **V15** | ThinkerProvider 旁路迁移（默认 off）| `services/block_trace/thinker_provider.py` ≤ 90 行 | `tests/test_thinker_provider.py` +4 | V13 + V0 |

**Wave 5 commit**：6 个 Provider 各自独立 commit。

### 3.6 Wave 6 — Scorer / Rewrite / Runtime 装配（8 条并列）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **V8** | StylometricScorer 5 轴本地评分 | `services/humanization/scorer.py` ≤ 200 行 | `tests/test_humanization_scorer.py` +12 | U6 + V1 |
| **V9** | BlockTraceStore 扩 humanization_metrics 表 | `services/block_trace/store.py`（+40 行） | `tests/test_humanization_metrics_persist.py` +3 | V8 |
| **V10** | Humanizer 真接 mood / register / slot 入参（scheduler 装配） | `services/scheduler/_send_to_group` | `tests/test_humanizer_runtime.py` +3 | U3 + V1 |
| **V11** | critic-rewrite-loop（默认 off） | `services/llm/client.py`（≈ +60 行） | `tests/test_llm_client_rewrite.py` +4 | V8 + V0 |
| **U12** | semantic_gate threshold 动态化（默认 off） | `kernel/router.py:935-979`（+8 行） | `tests/test_semantic_gate_dynamic.py` +5 | V5 + V0 |
| **V16** | semantic_gate 落地 + shadow log | 与 U12 同改动，加 shadow log 写 | 共享 U12 测试 | U12 |
| **V17** | chat plugin on_startup 装配 humanization | `plugins/chat/plugin.py:on_startup`（+30 行） | `tests/test_chat_plugin_humanization_wire.py` +3 | U6 + V1 + V13 |
| **U13** | 双 haiku trace（仅观测，不合并） | `kernel/router.py`+`thinker.py`（各 +3 行 trace） | `tests/test_block_trace.py` +1 | — |

**Wave 6 commit**：V8 / V9 / V10 / V11 / U12+V16 / V17 / U13 共 7 个独立 commit。

### 3.7 Wave 7 — 种子 + 灰度阶段 1~3

| 编号 | 一句话 | 改动 | 出口指标（24h）| 依赖 |
|---|---|---|---|---|
| **V7** | 30 条 catchphrase 种子（从 EpisodeStore 抽） | `scripts/dev/seed_catchphrase_pool.py` 一次性脚本 | 入库 30 条 | U5 |
| **灰度-1** | 单群 993065015 启 `humanization.context_providers=true`（其余 off） | `config/config.json` | 见 §4 出口表 | Wave 5 全完成 |
| **灰度-2** | 双群启动 | `config/config.json` | 双群 register 差异可见 | 灰度-1 通过 |
| **灰度-3** | 开 `rewrite_threshold=0.4` | `config/config.json` | 二次 round 触发率 < 5% | 灰度-2 通过 |

### 3.8 Wave 8 — V12 文档收口

| 编号 | 一句话 | 改动 |
|---|---|---|
| **V12** | maintenance-log 当日条目 + migration §12 第 7 行 ⏳→✅ + 本文 §6 状态表全 ✅ + measure_humanization.sh | 文档 |

---

## 4. 灰度 24h 出口指标矩阵

执行者每阶段灰度结束跑一次 `scripts/dev/measure_humanization.sh`，把下表填进结果。我看到 ≥ 10/14 项达标才放下一阶段。

| 指标 | 目标 | 灰度-1 实测 | 灰度-2 实测 | 灰度-3 实测 |
|---|---|---|---|---|
| em-dash 率（per 1K chars） | < 4 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞：灰度-1 未验收 | 阻塞：灰度-2 未验收 |
| ☆ ♪ ✦ 装饰符出现 | 0 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 句末 ～ | < 0.5 / 100 句 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 句末有句号率 | 20%~30% | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 平均句长 | 12~20 字 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 句长方差 | ≥ 50 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| catchphrase 命中率 | ≥ 30% | 当前 catchphrase cluster=0；EpisodeStore 源样本 0/30 | 阻塞 | 阻塞 |
| 同质化分（成对 Rouge-L） | < 0.18 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 重复 catchphrase 率（30min） | < 15% | 当前 catchphrase cluster=0；无法评价 | 阻塞 | 阻塞 |
| register fit（admin vs 群） | 句长差 ≥ 5 字 | 等待灰度-1上线后样本 | 阻塞 | 阻塞 |
| sticker 复读率（30min） | < 10% | sticker provider 仍 off，等待后续灰度 | 阻塞 | 阻塞 |
| mood-text alignment | 高 valence 时短句 emoji 多 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| affection-register match | tier=stranger → polite_distant 主导 | 等待 24h 样本；当前 metrics 表缺失 | 阻塞 | 阻塞 |
| 用户主观验收 | 「不再用力过猛」+「群 vs admin 有差异」 | 待用户最终验收 | 阻塞 | 阻塞 |

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) 锁脏写）
- [ ] uv run pytest -q 全绿（含本任务新测试）
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] 30 秒回滚演练成功（命令贴本回执）
- [ ] 同 wave 其它任务无冲突（git rebase / merge clean）
```

---

## 6. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | wave | 状态 | 落地证据 / 备注 |
|---|---|---|---|
| **P0.0** | 0 | ✅ | low-wins：budget_manager.py `priority ASC`，先处理先占预算；Provider priority 后续按 40/42/45/50 校准 |
| **P0.1** | 1 | ❌ | 证据不成立：字段已由 `services/llm/segmentation.py` + `tests/test_segmentation.py` 覆盖；留待 U1 合并旧 `_reply_segments` |
| **P0.2** | 1 | ✅ | 已删除 scheduler.concurrency schema/default/UI 暴露；旧配置残留可被 Pydantic 忽略；配置/API 测试与 `vue-tsc` 通过 |
| ~~P0.3~~ | — | ❌ | 主线 §0a.2 证据不成立（router.py:1076 真用） |
| ~~P0.4~~ | — | ❌ | 主线 §0a.2 证据不成立（两个 mood text 给不同 LLM call） |
| **P0.5** | 1 | ✅ | 已删前一处重复 `card_store.close()`，保留 shutdown 后段唯一调用；grep 仅 1 命中，chat/context plugin 测试通过 |
| **P0.6** | 1 | ✅ | 评估后保留：生产 caller 现为 0，但 `ReplySegmentBatch` 被并发追踪和回复切分研究列为后续发送收口基础；`tests/test_send_queue.py` 8 passed |
| **P0.7** | 1 | ✅ | 已删除 bot.py 后半段全局 `_backup_scheduler`，保留 `ctx.backup_scheduler` 由 `kernel/router.py` 启停；system settings reload 改读 ctx；backup/admin API 测试 73 passed，ruff 通过 |
| **P0.8** | 4 | ✅ | hook 保留为 PluginBus 扩展协议；生产读取已随 V13 落到 `bus.state.thinker.last_decision`，不再依赖 fan-out 结果 |
| **U1** | 2 | ✅ | segmentation 双实现已合并：`LLMClient` 生产 `_reply_segments` 委托 `services.llm.segmentation.reply_segments()`；`ChatPlugin` 注入 `config.reply_segmentation`；旧 `_MAX_SEND_SEGMENTS=4` 退出生产路径；专属分段/客户端/chat 测试 35 passed，ruff 通过。补充风险：`tests/test_scheduler.py::TestMood::test_bad_mood_suppresses_reply` 单独重跑卡边界 `15 < 15`，属 U2 mood 区域既有概率边界，未在 U1 改调度器 |
| **U2** | 2 | ✅ | MoodEngine cache 已改为 `dict[(group_id, session_id), (profile, ts)]`；`GroupTimeline.recent_interaction_count(..., window_s=60)` 已接入 `SchedulePlugin` 与 ChatPlugin runtime mood_getter；LLM thinker / scheduler mood_getter 均优先带 group/session 调用并保留无参兼容；相关 mood/timeline/scheduler/client/chat 测试 157 passed，ruff 通过 |
| **U3** | 2 | ✅ | `Humanizer.delay(text, *, group_id, register, slot, mood)` 已兼容落地；旧签名默认 1.0x，`quiet` + 低 slot/mood energy 为 1.5x，`playful` 为 0.7x；新增 `tests/test_humanizer_register.py` 5 条，humanizer/chat/scheduler/send_queue 测试 54 passed，ruff 通过 |
| **U4** | 2 | ✅ | `StyleStore` prompt 召回已支持 `mood_fit_target` / `persona_fit_target`；文本相关为硬门槛，fit 仅细排；`tests/test_style_store.py` + `tests/test_style_relevance.py` 共 19 passed，ruff 通过 |
| **U5** | 2 | ✅ | `learning_normalizer` 已注册 `domain/profile="catchphrase"`；默认复用 slang 式清洗并新增 `reuse_stats()` 观测；`tests/test_learning_normalizer.py` + `tests/test_learning_normalizer_catchphrase.py` 共 8 passed，ruff 通过 |
| **U6** | 3 | ✅ | 新增 `services/humanization/` contract/state 工厂，复用 `services.system_module.RuntimeStateBus` 作为首个生产 owner；`PluginContext`/`bot.py` 已装配；owner/TTL/cancel/per_turn/多 owner 测试 14 passed，ruff 通过 |
| **U7** | 4 | ✅ | DreamAgent 每轮整理前清理 RuntimeStateBus 中空闲 >30min 的 per_session 状态；per_user familiarity 不受影响；专项/dream/system 测试 26 passed，ruff 通过 |
| **U8** | 5 | ✅ | StickerRegisterProvider 已落地；`send_sticker` 成功后写 `state.sticker.recent_used` per_session recent；Provider 双开关注册，priority=47；专项/provider/tool/contract 测试 62 passed，ruff 通过 |
| **U10** | 2 | ✅ | 新增 `services/runtime_clock.py` 单源时间 API；`mood.py` 与 debug block 已改读 `now_cst()` / `format_cn_datetime()` / `today_key()`；`tests/test_runtime_clock.py` + mood/client 共 92 passed，ruff 通过 |
| **U11** | 4 | ✅ | thinker 决策已写 RuntimeStateBus per_turn；`on_thinker_decision` hook 保留并继续 fire；专项测试 31 passed，旧 hook 测试 1 passed，ruff 通过 |
| **U12** | 6 | ✅ | `semantic_gate_threshold()` 已落地；默认 off 使用固定阈值，开启后按 familiarity/mood energy 计算 effective threshold；专项/reply_workflow 测试 27 passed，ruff 通过 |
| **U13** | 6 | ✅ | 新增 U13 双 haiku 观测 trace：semantic_gate 实际 LLM 调用后写 `shadow_only` trace；若触发 directed_followup，经 `TriggerContext.extra`/`ToolContext.extra` 透传同一 request id，thinker 再写第二条 `shadow_only` trace；专项 24 passed，ruff/pyright 通过 |
| **V0** | 4 | ✅ | 新增 `HumanizationConfig` 与 `[humanization]`/`humanization` 默认段；6 字段默认 off，`rewrite_threshold=-1.0` 关闭 rewrite；配置测试 30 passed，ruff 通过 |
| **V1** | 4 | ✅ | 新增 `RegisterClassifier`，5 轮窗口 one-shot 分类并写 `state.register.label`；LLM 失败/非法 JSON 降级 neutral；cancel-path 不脏写；专项测试 12 passed，ruff 通过 |
| **V2** | 5 | ✅ | RegisterProvider stable bucket 已落地；读取 register / affection / clock runtime state，默认降级 neutral；仅在 `humanization.context_providers=true` 时由 ChatPlugin 注册；专项/provider/contract 测试 34 passed，ruff 通过 |
| **V3** | 5 | ✅ | CatchphraseProvider dynamic bucket 已落地；基于 `learning_normalizer` catchphrase domain 只读召回，per_session recent 去重；仅 `humanization.context_providers=true` 时注册；专项/provider/normalizer 测试 24 passed，ruff 通过 |
| **V4** | 5 | ✅ | EpisodeProvider register-aware 可选过滤已落地；仅当 episode meta 显式声明 register 适用/避开时过滤，无 meta 保持旧召回；专项/register/contract 测试 30 passed，ruff 通过 |
| **V5** | 4 | ✅ | `AffectionEngine` 新增短期 `familiarity_score()` 与可选 RuntimeStateBus 写入；`state.affection.<uid>.familiarity` per_user TTL=60min；专项测试 42 passed，ruff 通过 |
| **V6** | 5 | ✅ | SlangProvider mood-fit 可选加权已落地；`QueryContext` / `LLMClient` / `SlangStore` 通路打通，无 mood meta 时旧排序保持；slang/provider/thinker runtime 测试 42 passed，ruff 通过 |
| **V7** | 7 | ✅ | `scripts/dev/seed_catchphrase_pool.py` 已落地并验证；宿主/容器 live dry-run 与真实执行均 `selected=0 written=0`，原因是 live `EpisodeStore` 当前 `episodes_total=0`，未伪造 30 条 |
| **V8** | 6 | ✅ | StylometricScorer 5 轴已落地；`scorer.py` 182 行；13 条专项测试覆盖五轴、bus 写入与 cancel-path；ruff/pytest 通过 |
| **V9** | 6 | ✅ | `BlockTraceStore` 已新增 `humanization_metrics` 表、索引、记录/查询/统计 API；专项 + block_trace 测试 26 passed，ruff/pyright 通过 |
| **V10** | 6 | ✅ | `GroupChatScheduler._send_to_group()` 已把 register/mood/slot runtime 入参传给 `Humanizer.delay()`；专项/scheduler/humanizer 测试 46 passed，ruff/pyright 通过 |
| **V11** | 6 | ✅ | critic-rewrite-loop 默认 off 已落地；阈值开启后低分只追加一次 rewrite round，最终回复确定后写 RuntimeStateBus/metrics；专项/回归 91 passed，ruff/pyright 通过 |
| **V12** | 8 | ✅ | `scripts/dev/measure_humanization.sh` 已落地；本文、主方案、migration §12、maintenance-log 已同步；当前指标输出为 metrics 表缺失 / catchphrase=0 / episode=0 / 灰度窗口 pending |
| **V13** | 4 | ✅ | 新增 `bus.state.thinker.last_decision` slot 与 `write_thinker_decision_state()`；`LLMClient`/ChatPlugin 已接 `runtime_state`；cancel-path 不脏写 |
| **V14** | 4 | ✅ | 新增 `bus.state.clock.current` per_turn；thinker 注入 runtime clock time_text；clock/thinker decision 共 turn scope；专项测试 40 passed，ruff 通过 |
| **V15** | 5 | ✅ | ThinkerProvider PromptBlock 已落地；默认 off 保留旧 thinker 旁路，双开关开启后 Provider 接管且不双注入；专项/runtime/provider 测试 38 passed，ruff 通过 |
| **V16** | 6 | ✅ | `kernel/router.py` 已统一用 effective threshold 消费 semantic gate，并在 shadow log 写 fixed/effective/dynamic/familiarity/mood/adjustments；router pyright 仅剩既有动态属性旧债 |
| **V17** | 6 | ✅ | ChatPlugin 已挂 `humanization_register_classifier`；`register_classifier=false` 默认 off，开启后 on_message 只写 `state.register.label` 且不消费消息；专项 16 passed，ruff/测试 pyright 通过 |
| 灰度-1 | 7 | 🟡 | `config/config.json` / TOML 已切单群 `993065015`：`context_providers=true`、`register_classifier=true`、`runtime_groups=["993065015"]`，rewrite/sticker/thinker/dynamic gate 仍 off；待 rebuild/restart 后跑 24h 出口指标 |
| 灰度-2 | 7 | ⏸ | 阻塞：灰度-1 未满 24h 且出口矩阵未通过，未把 `984198159` 加入 humanization runtime_groups |
| 灰度-3 | 7 | ⏸ | 阻塞：灰度-2 未通过，未开启 `rewrite_threshold=0.4` |

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P0.0，回执贴 priority 语义结论；再领 Wave 1 任意一条。
2. **多人并行**：同 wave 内任务可同时下发，不同 wave 串行。
3. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行（本仓约定见 [docs/agent-discipline.md](../agent-discipline.md)）。
4. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + PR 链接发我，我跑 §5 验收清单后改 ✅。
5. **冲突冲突**：本文 §1 与主线冲突时**以本文为准**；其它部分以 [Part 1 主线](./omubot-humanization-part1-language-feel.md) 为准。
6. **遇到证据不成立**：跟我同步，由我决定撤销 / 重订正。

---

## 8. 与 Part 5 的关系

[Part 5 自然分段重构](./omubot-humanization-part5-segmentation.md) **阻塞于本文 U1（segmentation 双实现合并）**——U1 是 Part 5 的前置基线。U1 落地 ✅ 后 Part 5 才能动手。

---

## 9. 执行者 GPT 逐步追踪

### U4 领单拆分（执行前）

目标：激活 `StyleStore` 已持久化的 `mood_fit` / `persona_fit`，让表达习惯召回在保持文本相关的前提下，更偏向当前 mood / persona 适配度高的表达。

详细步骤：

1. 接口扩展：给 `get_prompt_expressions()`、`build_prompt_block()`、`build_prompt_block_with_refs()` 增加可选 `mood_fit_target` / `persona_fit_target` 参数；默认 `None` 保持旧行为。
2. 排序实现：扩展 `_expression_relevance()`，先计算旧文本相关 `base_score`，只有 `base_score > 0` 才叠加 fit 权重；权重按 U4 派单为 `0.3 * mood_component + 0.2 * persona_component`。
3. fit 计算：目标值为 0..1；表达的 fit 越接近目标，component 越高；缺省目标不参与加权。
4. 生产兼容：StylePlugin / StyleProvider 暂不强行发明 mood/persona 来源；本轮先开放 store 层生产入口，后续 V6 / V17 接 RuntimeStateBus 时传入真实目标。
5. 测试：新增 U4 专项测试，覆盖 mood 改变排序、persona 改变排序、默认旧行为不变、无文本相关不召回。

风险评估：

- 相关性漂移：若 fit 可单独加分，会把无关表达注入 prompt；本轮以 base_score 为硬门槛规避。
- API 破坏：现有调用方不传新参数必须保持完全可用；新参数全部 keyword-only 且默认 `None`。
- 生产未完全闭环：当前上下文没有稳定 mood/persona target 来源；本轮如实只完成 StyleStore 激活点，真实运行时动态目标后续由 RuntimeStateBus / Provider 接入。

### U4 完成记录（执行者 GPT）

改动：

- `services/style/store.py`：`get_prompt_expressions()` / `build_prompt_block()` / `build_prompt_block_with_refs()` 新增可选 `mood_fit_target` / `persona_fit_target`。
- `_expression_relevance()`：保留旧文本相关硬门槛，命中后叠加 `0.3 * mood_alignment + 0.2 * persona_alignment`；默认不传 target 时分数等价旧行为。
- `tests/test_style_relevance.py`：新增 4 条 U4 专项测试，覆盖 mood 排序、persona 排序、默认旧排序、无文本相关不召回。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_style_store.py tests/test_style_relevance.py` → 19 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/style/store.py tests/test_style_relevance.py` → passed。

自审：

- D1：`rg "mood_fit_target|persona_fit_target|_fit_alignment" services/style/store.py tests/test_style_relevance.py` 命中仅为 U4 新入口、helper 和专项测试。
- D2：本任务没有异步取消路径写入；风险边界由“无文本相关不召回”测试覆盖。
- 回滚：`git restore services/style/store.py tests/test_style_relevance.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U4（注意该命令会同时撤销本追踪文档后续追加，真实回滚前需先确认）。

### U5 领单拆分（执行前）

目标：把 `catchphrase` 注册为 `learning_normalizer` 的一等 domain/profile，为后续 CatchphraseProvider / catchphrase 灰度指标提供去重、复用和跨 domain 隔离基础。

详细步骤：

1. Domain/profile 注册：扩展 `NormalizationProfile` 与 `NormalizerDomain`，让 `catchphrase` 可作为 `attach_candidate(domain="catchphrase")` 的合法值。
2. 默认 profile 映射：`domain="catchphrase"` 默认使用 `profile="catchphrase"`；归一化规则复用 slang 的 NFKC、去标点、折叠重复字符逻辑。
3. 锁定兼容：`lock_cluster()` 的 profile 白名单加入 `catchphrase`，避免 catchphrase cluster 被锁定时退回 general。
4. 复用率统计：新增 store 只读方法，按 domain/scope/group 统计 total_items、auto_merged_items、reused_items、reuse_rate，供后续 catchphrase 命中率/复用率观测使用。
5. 测试：新增 `tests/test_learning_normalizer_catchphrase.py`，覆盖 domain 注册、复用率统计、跨 domain 隔离。

风险评估：

- 行为扩散：catchphrase profile 如果走 style 规则会过度保留空白/语气符；本轮明确复用 slang 清洗逻辑。
- 误合并：domain 必须参与 cluster 查询；专项测试用相同文本分别写 slang/catchphrase，确保不跨 domain 合并。
- 统计口径：source_update 不能算作新复用候选；本轮统计 `auto_merged` revision 与 item_count，输出明确字段，后续灰度脚本可直接引用。

### U5 完成记录（执行者 GPT）

改动：

- `services/learning_normalizer/normalize.py`：`NormalizationProfile` 新增 `catchphrase`，清洗规则复用 slang/general 的 NFKC、去链接/标点、折叠重复字符逻辑。
- `services/learning_normalizer/store.py`：`NormalizerDomain` 新增 `catchphrase`；`attach_candidate()` 默认按 domain 选择 profile；`lock_cluster()` 支持 catchphrase profile；新增 `reuse_stats()` 只读统计。
- `tests/test_learning_normalizer_catchphrase.py`：新增 3 条 U5 专项测试，覆盖 domain 注册、复用率统计、跨 domain 隔离。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_learning_normalizer.py tests/test_learning_normalizer_catchphrase.py` → 8 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/learning_normalizer/normalize.py services/learning_normalizer/store.py tests/test_learning_normalizer_catchphrase.py` → passed。

自审：

- D1：`rg "catchphrase|reuse_stats|_default_profile_for_domain" services/learning_normalizer tests/test_learning_normalizer_catchphrase.py` 命中仅为 U5 新 domain/profile、统计入口和专项测试。
- D2：本任务无取消路径写入；写路径保持原 `attach_candidate()` 事务，统计为只读 SQL。
- 回滚：`git restore services/learning_normalizer/normalize.py services/learning_normalizer/store.py tests/test_learning_normalizer_catchphrase.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U5（真实回滚前需保留后续追踪记录）。

### U10 完成记录（执行者 GPT）

改动：

- `services/runtime_clock.py`：新增 `CST`、`now_cst()`、`today_key()`、`weekday_cn()`、`format_cn_datetime()`、`is_weekend()`、`is_holiday()`、`slot_features()`。
- `plugins/schedule/mood.py`：移除本地上海时区与星期格式 helper，mood 计算和 mood block 统一使用 runtime clock。
- `services/llm/client.py`：`_build_debug_block()` 的今日日期改用 `today_key()`，移除局部 timezone 构造。
- `tests/test_runtime_clock.py`：新增 6 条 U10 专项测试，覆盖 CST tz、today_key 时区转换、中文时间格式、weekend、holiday stub、slot_features。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_runtime_clock.py tests/test_mood.py tests/test_client.py` → 92 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/runtime_clock.py plugins/schedule/mood.py services/llm/client.py tests/test_runtime_clock.py` → passed。

自审：

- D1：`rg "from services.runtime_clock|now_cst|today_key|format_cn_datetime" plugins/schedule/mood.py services/llm/client.py services/runtime_clock.py tests/test_runtime_clock.py` 命中仅 U10 接入点。
- D2：本任务为只读时间 helper 与调用点替换，无取消路径写入；未提前写 RuntimeStateBus，避免混入 V14。
- 回滚：`git restore services/runtime_clock.py plugins/schedule/mood.py services/llm/client.py tests/test_runtime_clock.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U10（真实回滚前需保留后续追踪记录）。

### Wave 2 汇总自审（执行者 GPT）

范围：U1 / U2 / U3 / U4 / U5 / U10。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_segmentation.py tests/test_client.py tests/test_chat_plugin.py tests/test_mood.py tests/test_group_timeline.py tests/test_scheduler.py tests/test_humanizer_register.py tests/test_style_store.py tests/test_style_relevance.py tests/test_learning_normalizer.py tests/test_learning_normalizer_catchphrase.py tests/test_runtime_clock.py` → 213 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/llm/segmentation.py services/llm/client.py plugins/chat/plugin.py plugins/schedule/mood.py plugins/schedule/plugin.py services/memory/timeline.py services/scheduler.py services/humanizer.py services/style/store.py services/learning_normalizer/normalize.py services/learning_normalizer/store.py services/runtime_clock.py tests/test_humanizer_register.py tests/test_style_relevance.py tests/test_learning_normalizer_catchphrase.py tests/test_runtime_clock.py` → passed。

结论：Wave 2 当前基线可进入 Wave 3 / U6。

### U10 领单拆分（执行前）

目标：抽出 `services/runtime_clock.py` 作为上海时区、日期、星期、时段特征的单一来源，让 mood block 与 debug block 不再各自实例化时间逻辑，并为后续 V14 thinker time_text 复用留入口。

详细步骤：

1. 新建模块：导出 `CST`、`now_cst()`、`today_key()`、`weekday_cn()`、`format_cn_datetime()`、`is_weekend()`、`is_holiday()`、`slot_features()`，保持 ≤80 行。
2. mood 接入：`plugins/schedule/mood.py` 移除本地 `ZoneInfo("Asia/Shanghai")` 与 `_weekday_cn()`，统一调用 `now_cst()` / `format_cn_datetime()`。
3. debug 接入：`services/llm/client.py::_build_debug_block()` 移除局部 `datetime/timezone` 导入，改用 `today_key()`。
4. 测试：新增 `tests/test_runtime_clock.py` 6 条，覆盖 CST tz、周末、holiday stub、slot_features、中文时间格式、多源 today 一致性。
5. 验证：运行 runtime_clock、mood、client 相关最小测试和 ruff。

风险评估：

- 时间漂移：原 `mood._compute()` 一轮内多次 `datetime.now()`；本轮只改来源，不强制同一 timestamp，避免行为变化。
- 循环依赖：`runtime_clock` 不导入 schedule/calendar 类型，只做 duck typing；mood 仍负责 `get_day_context()`。
- V14 未落地：本轮只提供 reusable clock API，不写 RuntimeStateBus；V14 再负责 thinker 注入和 bus.state.clock.current。

### U6 领单拆分（执行前）

目标：把现有 `services.system_module.RuntimeStateBus` 从 dry-run 骨架推进为 Part 1 的首个生产挂载点；新增 `services/humanization/` 声明 humanization owner contract，并在 `PluginContext`/`bot.py` 装配为后续 U7/V1/V2/V8/V13/V17 共用状态中枢。

详细步骤：

1. 合同声明：新增 `services/humanization/contract.py`，声明 `humanization.runtime` owner，拥有 `state.register.label`、`state.register.recent_used`、`state.sticker.recent_used`、`humanization.last_metrics`。
2. 状态工厂：新增 `services/humanization/state.py`，提供 `create_humanization_state_bus(extra_contracts=())`，复用现有 `RuntimeStateBus` 并导出常用 slot/source 常量。
3. Context 装配：`PluginContext` 增加 `runtime_state` / `humanization_contract` 字段；`bot.py` 初始化时创建 bus 并挂到 `_plugin_ctx`。
4. Bus 小补强：若现有 `RuntimeStateBus` 已有 owner/per_turn 能力则复用；仅在必要时补 `decay_at` 到期清理，支持后续 recent_used TTL。
5. 测试：新增 `tests/test_humanization_contract.py`，覆盖 owner enforcement、decay TTL、cancel-path 不脏写、per_turn 清理、多 owner raise。

风险评估：

- 平行架构风险：不能新建第二套总线；本轮必须复用 `services.system_module.RuntimeStateBus`。
- 热路径风险：U6 只装配状态中枢，不让现有回复流程开始依赖它；实际读写由后续 wave 逐步接入。
- owner 冲突风险：humanization slots 不应与现有 catalog slots 重名；多 owner raise 用测试锁定。

### U6 完成记录（执行者 GPT）

改动：

- `services/humanization/`：新增 `contract.py` / `state.py` / `__init__.py`，声明 `humanization.runtime` owner，拥有 `state.register.label`、`state.register.recent_used`、`state.sticker.recent_used`、`humanization.last_metrics`。
- `services/system_module/state_bus.py`：保留现有 owner/scope/per_turn 语义，补 `decay_at` 过期清理与 `clear_expired()`。
- `kernel/types.py`：`PluginContext` 增加 `runtime_state` / `humanization_contract` 字段。
- `bot.py`：启动时创建 `create_humanization_state_bus()` 并挂到 `_plugin_ctx.runtime_state`。
- `tests/test_humanization_contract.py`：新增 6 条 U6 专项测试，覆盖 owner enforcement、decay TTL、cancel-path 不脏写、per_turn、多 owner、PluginContext 装配。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_contract.py tests/test_system_module.py` → 14 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/humanization services/system_module/state_bus.py kernel/types.py bot.py tests/test_humanization_contract.py` → passed。

自审：

- D1：`rg "humanization.runtime|state.register|state.sticker|runtime_state|clear_expired" services/humanization services/system_module/state_bus.py kernel/types.py bot.py tests/test_humanization_contract.py` 命中仅 U6 contract、bus 补强、装配和专项测试。
- D2：cancel-path 已由 `test_humanization_state_cancel_path_does_not_dirty_write` 覆盖，任务取消后 `REGISTER_LABEL_SLOT` 保持空。
- 回滚：`git restore services/humanization services/system_module/state_bus.py kernel/types.py bot.py tests/test_humanization_contract.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U6（真实回滚前需保留后续追踪记录）。

### V0 领单拆分（执行前）

目标：新增 `[humanization]` 配置基座，提供 Part 1 后续模块的统一 feature flag，并确保 6 个字段默认全 off，不改变当前生产行为。

详细步骤：

1. Schema：在 `kernel/config.py` 新增 `HumanizationConfig`，字段为 `context_providers`、`register_classifier`、`sticker_register_provider`、`thinker_provider`、`rewrite_threshold`、`semantic_gate_dynamic`。
2. 根配置挂载：将 `humanization: HumanizationConfig` 挂到 `BotConfig`，让 JSON/TOML/默认加载都能解析。
3. 默认配置：在 `config/config.toml` 和 `config/config.json` 增加 humanization 段，所有布尔 flag 为 `false`，`rewrite_threshold = -1.0` 表示 rewrite loop 关闭。
4. 测试：新增 `tests/test_humanization_config.py`，覆盖空默认、TOML 加载、JSON 加载、单字段覆盖、rewrite 阈值关闭语义、未知旧配置不影响加载。
5. 验证：运行新测试、配置加载测试和 ruff，确认 V0 不接入热路径。

风险评估：

- 默认值误开：任一 flag 默认 true 都会让后续模块误以为已灰度；测试逐项锁定默认 off。
- 命名漂移：后续 V1/V15/V16 会读取这些字段；本轮用 execution 文档里的短命名，避免同时出现 `*_enabled` 双入口。
- 配置兼容：只新增字段，不删除旧配置；Pydantic 旧残留仍按当前配置策略忽略。
- 行为扩散：V0 只做 schema 与默认文件，不在 runtime 读取这些 flag。

### V0 完成记录（执行者 GPT）

改动：

- `kernel/config.py`：新增 `HumanizationConfig`，并挂到 `BotConfig.humanization`。
- `config/config.toml` / `config/config.json`：新增 humanization 默认段，布尔开关全为 `false`，`rewrite_threshold=-1.0`。
- `tests/test_humanization_config.py`：新增 6 条 V0 专项测试，覆盖默认 off、TOML/JSON 加载、单 flag 覆盖、rewrite 关闭语义、未知旧字段兼容。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_config.py tests/test_config_loader.py` → 30 passed。
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py tests/test_humanization_config.py` → passed。

自审：

- D1：`rg "HumanizationConfig|humanization|rewrite_threshold|semantic_gate_dynamic" kernel/config.py config/config.toml config/config.json tests/test_humanization_config.py` 命中仅 V0 schema、默认配置和专项测试。
- D2：本任务只新增配置 schema/default，无异步写入与取消路径；默认全 off 避免运行时行为变化。
- 回滚：`git restore kernel/config.py config/config.toml config/config.json tests/test_humanization_config.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V0（真实回滚前需保留后续追踪记录）。

### V1 领单拆分（执行前）

目标：新增 `RegisterClassifier`，用 5 轮最近对话做一次轻量语域判断，结果写入 `RuntimeStateBus` 的 `state.register.label`，为后续 RegisterProvider / CatchphraseProvider / Humanizer runtime 装配提供稳定输入。

详细步骤：

1. 数据模型：定义 `RegisterDecision(label, confidence, reason, evidence, window_size)`，label 约束为 `neutral` / `quiet` / `playful` / `affectionate` / `serious` / `distant`。
2. 格式化窗口：只读取最近 5 条消息，兼容 `content` / `content_text` / `role` / `speaker` / `user_id`，空窗口直接 neutral。
3. LLM 调用：沿用现有 `LLMRequest` + `llm_client._call()` 模式，`task="thinker"` 临时复用已存在 haiku/thinker profile，不新增 profile；只要求 JSON 文本，不接热路径开关。
4. 解析兜底：解析 fenced/embedded JSON；LLM 缺失、调用失败、JSON 异常、非法 label 全部返回 neutral，confidence 保守夹到 0..1。
5. 状态写入：`classify_and_write()` 仅在 classify 成功返回后写 `REGISTER_LABEL_SLOT`；写入 evidence_path 固定 `register_classifier:classify`，scope 由调用方传入。
6. 测试：覆盖 happy path、LLM 失败默认、非法 JSON 默认、多群/多 session 隔离、cancel-path 不脏写、confidence 夹取。

风险评估：

- 热路径耗时：V1 只提供模块，不在生产启动或回复流程自动调用；V17 再做 worker 装配。
- 语域过拟合：label 集合保守，默认 neutral；后续 Provider 读不到或失败时都能降级。
- 状态污染：取消路径必须在 LLM 返回前取消时不写 bus；状态 scope 用 `session_id` 隔离群。
- profile 不一致：不在 V1 新增 LLM profile，避免配置面扩散；后续可通过 task profile 调整。

### V1 完成记录（执行者 GPT）

改动：

- `services/humanization/classifier.py`：新增 `RegisterClassifier` / `RegisterDecision`，用最近 5 条消息构造 `LLMRequest(task="thinker")`，解析 register JSON，失败降级 neutral。
- `services/humanization/__init__.py`：导出 classifier 类型。
- `tests/test_register_classifier.py`：新增 6 条 V1 专项测试，覆盖 happy 写 bus、LLM 失败兜底、非法 JSON/label、session 隔离、cancel-path、confidence clamp 与 5 轮窗口。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_register_classifier.py tests/test_humanization_contract.py` → 12 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/humanization tests/test_register_classifier.py` → passed。

自审：

- D1：`rg "RegisterClassifier|RegisterDecision|REGISTER_LABEL_SLOT|register_classifier:classify" services/humanization tests/test_register_classifier.py` 命中仅 V1 classifier、导出和专项测试。
- D2：`test_register_classifier_cancel_path_does_not_dirty_write` 已覆盖 LLM 未返回前取消不写 `state.register.label`。
- 回滚：`git restore services/humanization/classifier.py services/humanization/__init__.py tests/test_register_classifier.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V1（真实回滚前需保留后续追踪记录）。

### V5 领单拆分（执行前）

目标：扩展 `AffectionEngine` 的短期 familiarity 观测，把长期好感度与当天互动强度压成 0..1 分数，写入 `RuntimeStateBus` 供后续 RegisterProvider / semantic gate 动态阈值读取。

详细步骤：

1. Contract：在 humanization contract 新增 `state.affection.<uid>.familiarity` slot；slot id 保留文档占位写法，实际用户隔离由 `Scope.user_id` 的 per_user key 完成。
2. Engine 注入：`AffectionEngine` 增加可选 runtime state bus 注入方法，不改变现有构造调用方；未注入 bus 时行为与旧版一致。
3. 分数计算：新增 `familiarity_score(user_id)`，用长期 score 与 daily_count 做保守加权并 clamp 到 0..1；不写 profile 新字段，避免扩持久化 schema。
4. 写入时机：`record_interaction()` 在正常互动保存后写 bus，`decay_at=now+60min`；daily cap 命中时不改原持久化行为，可写当前 familiarity 快照但不增加计数。
5. 测试：新增 `tests/test_affection_familiarity.py`，覆盖 TTL=60min、多 user 隔离、on_post_reply/record_interaction 累积写入、mood_bonus/score 同步。

风险评估：

- 持久化污染：不向 `AffectionProfile` 增字段，避免旧 JSON 读写迁移。
- owner 冲突：slot 必须加入 humanization contract 后再写，否则 RuntimeStateBus 会拒绝。
- 行为改变：record_interaction 原有 score/daily_cap 逻辑不改；bus 未注入时完全无额外动作。
- 取消路径：本任务没有 async LLM 等待；写入发生在现有同步保存之后，后续取消不会造成半写 LLM 状态。

### V5 完成记录（执行者 GPT）

改动：

- `services/humanization/contract.py` / `__init__.py`：新增 `AFFECTION_FAMILIARITY_SLOT = "state.affection.<uid>.familiarity"`，归属 `humanization.runtime` owner，TTL 为 `per_user`。
- `plugins/affection/engine.py`：新增 `set_runtime_state_bus()`、`familiarity_score()`，并在 `record_interaction()` 后写入短期 familiarity 快照，`decay_at=now+60min`。
- `tests/test_affection_familiarity.py`：新增 4 条 V5 专项测试，覆盖 TTL、多 user 隔离、互动累积、tier/mood_bonus 同步。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_affection_familiarity.py tests/test_affection.py tests/test_humanization_contract.py` → 42 passed。
- `source ./scripts/dev/env.sh && uv run ruff check plugins/affection/engine.py services/humanization tests/test_affection_familiarity.py` → passed。

自审：

- D1：`rg "AFFECTION_FAMILIARITY_SLOT|familiarity_score|set_runtime_state_bus|affection:familiarity" services/humanization plugins/affection tests/test_affection_familiarity.py` 命中仅 V5 slot、engine 写入和专项测试。
- D2：本任务无 LLM/长等待取消路径；写入发生在原 `record_interaction()` 保存后，bus 未注入时旧行为保持。
- 回滚：`git restore services/humanization/contract.py services/humanization/__init__.py plugins/affection/engine.py tests/test_affection_familiarity.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V5（真实回滚前需保留后续追踪记录）。

### U7 领单拆分（执行前）

目标：让 DreamAgent 在每轮整理前顺手清理 humanization runtime state 中空闲超过 30 分钟的 `per_session` 槽位，避免 register / sticker recent state 长期滞留。

详细步骤：

1. Bus API：在 `RuntimeStateBus` 新增 `clear_stale_per_session(max_age, now=None)`，只扫描 slot.ttl == `per_session` 的快照，按 `updated_at` 年龄删除。
2. DreamAgent 注入：`DreamAgent.__init__` 增加可选 `runtime_state`，默认 None；DreamPlugin 创建 agent 时传 `ctx.runtime_state`。
3. 清理时机：在 `_run()` 开始时调用 `_cleanup_runtime_state()`，清理 30 分钟以上的 per_session 状态；只记录清理数量，不参与 LLM 工具循环。
4. 测试：新增 `tests/test_dream_humanization_cleanup.py`，覆盖旧 per_session 被清、per_user 不被清、未注入 bus 时 cleanup 为 0 且不报错。
5. 验证：跑 dream cleanup、dream 既有测试、humanization contract/system module 测试和 ruff。

风险评估：

- 误删 per_user：U7 只清 per_session，不能删除 V5 familiarity 等用户级短期状态；测试锁定。
- DreamAgent 低频运行：Dream 默认 disabled，且清理只在 dream run 前触发，不改变回复热路径。
- 时间边界：按 `updated_at <= now - max_age` 删除，边界明确；`decay_at` 清理由现有 `clear_expired()` 继续负责。
- 兼容性：未注入 RuntimeStateBus 时 DreamAgent 旧行为保持。

### U7 完成记录（执行者 GPT）

改动：

- `services/system_module/state_bus.py`：新增 `clear_stale_per_session(max_age, now=None)`，只按 `updated_at` 清理 `ttl="per_session"` 快照。
- `plugins/dream/plugin.py`：`DreamAgent` 增加可选 `runtime_state`，每次 `_run()` 开始调用 `_cleanup_runtime_state()`；`DreamPlugin` 创建 agent 时传入 `ctx.runtime_state`。
- `tests/test_dream_humanization_cleanup.py`：新增 3 条 U7 专项测试，覆盖旧 per_session 清理、per_user familiarity 保留、无 bus no-op。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_dream_humanization_cleanup.py tests/test_dream.py tests/test_system_module.py tests/test_humanization_contract.py` → 26 passed。
- `source ./scripts/dev/env.sh && uv run ruff check plugins/dream/plugin.py services/system_module/state_bus.py tests/test_dream_humanization_cleanup.py` → passed。

自审：

- D1：`rg "clear_stale_per_session|_cleanup_runtime_state|runtime_state=ctx.runtime_state" services/system_module/state_bus.py plugins/dream/plugin.py tests/test_dream_humanization_cleanup.py` 命中仅 U7 bus API、DreamAgent 接入和专项测试。
- D2：本任务不引入新异步写入；cleanup 是同步删除，且测试证明只清 per_session，不清 V5 per_user familiarity。
- 回滚：`git restore services/system_module/state_bus.py plugins/dream/plugin.py tests/test_dream_humanization_cleanup.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U7（真实回滚前需保留后续追踪记录）。

### U11 + V13 领单拆分（执行前）

目标：把 thinker 决策写入 `RuntimeStateBus` 的 per-turn 状态，作为后续 ThinkerProvider / RegisterProvider 的稳定读取点；同时保留 `on_thinker_decision` hook 作为插件扩展协议，完成 P0.8 语义订正。

详细步骤：

1. Contract：在 humanization contract 新增 `bus.state.thinker.last_decision` slot，TTL=`per_turn`，owner 仍为 `humanization.runtime`。
2. Helper：在 `services/llm/thinker.py` 增加 `write_thinker_decision_state()`，把 action / thought / retrieve_mode / rewritten_query / sticker / tone / usage 写成 dict；无 bus 时 no-op。
3. LLMClient 注入：`LLMClient.__init__` 增加 `runtime_state` 可选参数；ChatPlugin 构造 LLMClient 时传 `ctx.runtime_state`。
4. 写入时机：`chat()` 中 thinker 返回后、原 `_fire_thinker_decision()` 前写 RuntimeStateBus；scope 使用 `session_id/group_id/user_id/turn_id`，turn_id 采用当前 session + monotonic timestamp 构造，保证 per-turn 隔离。
5. Hook 语义：不删除 `on_thinker_decision` 协议，不删除 `_fire_thinker_decision()`；P0.8 改为“生产读取不依赖 fan-out，hook 保留扩展点”。
6. 测试：新增 `tests/test_thinker_runtime_state.py`，覆盖 happy 写入、LLMClient 接线、失败 fallback 写 reply、cancel-path 不脏写、多 session/group 隔离。

风险评估：

- 协议破坏：不删 hook，避免破坏 PluginBus/插件基类契约。
- 热路径异常：写 RuntimeStateBus 必须 no-op/fail-soft；bus 写入异常不应阻断 thinker 主决策。
- per-turn 键稳定性：turn_id 只用于隔离当前轮状态，不参与外部持久化；测试锁多 session 隔离。
- 取消路径：LLM 未返回前取消，不应写 bus；专项测试覆盖 helper 上层调用取消。

### U11 + V13 完成记录（执行者 GPT）

改动：

- `services/humanization/contract.py` / `__init__.py`：新增 `THINKER_LAST_DECISION_SLOT = "bus.state.thinker.last_decision"`，TTL 为 `per_turn`。
- `services/llm/thinker.py`：新增 `write_thinker_decision_state()`，将 action / thought / retrieve_mode / rewritten_query / sticker / tone / usage 写入 RuntimeStateBus；写入异常 fail-soft。
- `services/llm/client.py`：`LLMClient.__init__` 增加 `runtime_state`；thinker 返回后先写 per-turn RuntimeStateBus，再继续 `_fire_thinker_decision()`。
- `plugins/chat/plugin.py`：构造 `LLMClient` 时传入 `ctx.runtime_state`。
- `tests/test_thinker_runtime_state.py`：新增 5 条 U11/V13 专项测试，覆盖 happy、LLMClient 接线、per_turn 清理、cancel-path、多 group 隔离。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_thinker_runtime_state.py tests/test_thinker.py tests/test_humanization_contract.py` → 31 passed。
- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_client.py -k thinker_retrieve_mode_propagates_to_hook` → 1 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/llm/thinker.py services/llm/client.py services/humanization plugins/chat/plugin.py tests/test_thinker_runtime_state.py` → passed。

自审：

- D1：`rg "THINKER_LAST_DECISION_SLOT|write_thinker_decision_state|runtime_state=ctx.runtime_state|bus.state.thinker.last_decision" services/humanization services/llm plugins/chat tests/test_thinker_runtime_state.py` 命中仅 U11/V13 slot、helper、接线和专项测试。
- D2：`test_thinker_runtime_state_cancel_path_does_not_dirty_write` 已覆盖 thinker 未返回前取消不写 RuntimeStateBus。
- P0.8 订正：`on_thinker_decision` hook 不删除，保留作插件扩展点；生产读取使用 RuntimeStateBus，不依赖 fan-out。
- 回滚：`git restore services/humanization/contract.py services/humanization/__init__.py services/llm/thinker.py services/llm/client.py plugins/chat/plugin.py tests/test_thinker_runtime_state.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U11/V13（真实回滚前需保留后续追踪记录）。

### V14 领单拆分（执行前）

目标：复用 U10 的 `services/runtime_clock.py` 单源时间特征，让 thinker 在决策前看到当前日期、星期、工作日/周末/节假日和当前日程 slot；同时把本轮 clock 快照写入 `RuntimeStateBus` 的 per-turn slot，供后续 Provider / scorer 读取。

详细步骤：

1. Contract：在 humanization contract 增加 `bus.state.clock.current`，TTL=`per_turn`，owner 仍为 `humanization.runtime`，避免新增平行状态源。
2. time_text 构造：在 thinker 层新增 `build_thinker_time_text(features)`，只接收 `slot_features()` 的 dict 输出，渲染短动态块；空特征时返回空串。
3. thinker 注入：`think()` 增加可选 `time_text` 参数，并把它放在 dynamic block 头部，让后续 mood / affection / slang 继续保持原顺序语义。
4. LLMClient 接线：新增可选 `clock_context_getter`；每次 thinker 前读取 `slot_features()` 或 ChatPlugin 提供的 schedule/day_context 特征，传入 `think(time_text=...)`。
5. bus 写入：为 clock 快照新增 fail-soft helper；只在 thinker 正常返回后，与 `bus.state.thinker.last_decision` 使用同一个 `turn_id` 写入，避免取消路径脏写。
6. ChatPlugin 日程上下文：提供 getter，读取 `ctx.schedule_store.current` 与 `get_day_context(now_cst())`，不让 LLMClient 直接 import schedule plugin 细节。
7. 测试：覆盖 thinker dynamic block 注入、RuntimeStateBus clock 快照写入、clock 与 thinker 共 turn scope、取消路径不脏写、日程/节假日格式。

风险评估：

- 取消路径污染：如果在 thinker 调用前写 bus，取消会留下半轮 clock；本轮只在 thinker 返回后写入。
- 循环依赖：`runtime_clock` 不导入 schedule 类型；ChatPlugin 负责 day_context，LLMClient 只消费 dict。
- prompt 噪音：time_text 必须短，不能替代 mood block，也不能指导主 LLM 具体措辞。
- 行为扩散：默认 getter 缺省时只写基础时间特征；没有 schedule_store 时不影响现有 thinker 行为。

### V14 完成记录（执行者 GPT）

改动：

- `services/humanization/contract.py` / `__init__.py`：新增 `CLOCK_CURRENT_SLOT = "bus.state.clock.current"`，TTL 为 `per_turn`。
- `services/llm/thinker.py`：新增 `build_thinker_time_text()` 与 `write_clock_state()`；`think()` 增加可选 `time_text`，并把时间动态块放在 thinker dynamic block 头部。
- `services/llm/client.py`：新增 `clock_context_getter` 注入；thinker 前构造 runtime clock 特征，thinker 正常返回后与 `bus.state.thinker.last_decision` 共用同一 `turn_id` 写入 clock state。
- `plugins/chat/plugin.py`：提供 runtime clock getter，读取 `ctx.schedule_store.current` 和 `get_day_context(now_cst())`，避免 LLMClient 直接依赖 schedule 插件细节。
- `tests/test_thinker.py` / `tests/test_thinker_runtime_state.py`：新增 time_text、clock state、同 turn scope 覆盖。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_thinker.py tests/test_thinker_runtime_state.py tests/test_runtime_clock.py tests/test_humanization_contract.py` → 40 passed（仅 aiohttp/Python 3.13 已知 warning）。
- `source ./scripts/dev/env.sh && uv run ruff check services/humanization services/llm/thinker.py services/llm/client.py plugins/chat/plugin.py tests/test_thinker.py tests/test_thinker_runtime_state.py tests/test_runtime_clock.py` → passed。

自审：

- D1：`rg "CLOCK_CURRENT_SLOT|build_thinker_time_text|write_clock_state|clock_context_getter|bus.state.clock.current" services/humanization services/llm plugins/chat tests/test_thinker.py tests/test_thinker_runtime_state.py` 命中仅 V14 slot、helper、接线和专项测试。
- D2：`test_thinker_runtime_state_cancel_path_does_not_dirty_write` 覆盖 thinker 未返回前取消时 clock/decision 都不写 RuntimeStateBus。
- D3：clock state 与 thinker decision 共用同一 `turn_id`，后续 Provider / scorer 可按 per-turn scope 同步读取。
- 回滚：`git restore services/humanization/contract.py services/humanization/__init__.py services/llm/thinker.py services/llm/client.py plugins/chat/plugin.py tests/test_thinker.py tests/test_thinker_runtime_state.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V14（真实回滚前需保留后续追踪记录）。

### Wave 5 共用前置审查（执行前）

目标：进入 Provider 系列前，确认现有 `ContextProvider` / `PromptProviderBus` / `PromptBudgetManager` 的真实代码语义，避免把新 Provider 插到错误优先级或读不到 runtime state。

详细步骤：

1. 协议审查：读取 `services/block_trace/providers.py`、`provider_bus.py`、`budget_manager.py`，确认 `QueryContext` 字段、Provider 并发执行、budget 裁剪顺序。
2. priority 校准：对照现有 `SlangProvider(priority=40)`、`StyleProvider(priority=42/45)`、`EpisodeProvider(priority=50)`，按 P0.0 的 low-wins 结论为新 Provider 重新定数值。
3. runtime state 通路：确认 V2/V3/U8/V15 需要读 `RuntimeStateBus`，而现有 `QueryContext` 没有 `runtime_state` / `turn_id`；先扩展可选字段，旧 Provider 不受影响。
4. client 接线：把 `LLMClient.chat()` 中 V13/V14 生成的 `thinker_turn_id` 保留下来，创建 provider `QueryContext` 时传入 `runtime_state` 和 `turn_id`。
5. 验证：跑既有 provider/budget 测试，确保字段扩展对旧 Provider 是 ABI 兼容。

风险评估：

- Provider ABI 风险：`QueryContext` 是 dataclass，新增带默认值字段不会破坏旧构造；测试会覆盖旧构造和 chat 路径构造。
- per_turn 读取风险：没有 `turn_id` 时 Provider 必须降级，不得猜最近一轮状态。
- priority 误判风险：本轮以代码实测为准，Provider priority 均围绕 40/42/45/50 校准，不使用主线旧表中的 12/15/18/25。

### V2 领单拆分（执行前）

目标：新增 `RegisterProvider`，走 ContextProvider/PromptBudgetManager 管线，在 stable bucket 注入当前语域目标，让主 LLM 更稳定地“按当前关系和场景说话”，但不照抄人设文字。

详细步骤：

1. Provider 协议底座：扩展 `QueryContext(runtime_state=None, turn_id="")`，`LLMClient` 在 provider qctx 中传当前 `RuntimeStateBus` 和 thinker `turn_id`。
2. 状态读取：按 scope 读取 `state.register.label`、`state.affection.<uid>.familiarity`、`bus.state.clock.current`；缺失时降级为 `neutral` / familiarity=0。
3. register 决策：把 classifier label 与 affection tier/familiarity、时间 slot 组合成保守目标，如 `quiet`、`playful`、`casual_close`、`polite_distant`；默认 neutral。
4. Prompt 渲染：生成短 stable block，说明“语域目标/边界/不要机械复读”，只提供表达策略，不提供具体句子。
5. priority：使用 stable priority=43，低于 slang=40 / style profile=42 的先占预算，但强于 style expression=45 和 episode=50。
6. 装配：ChatPlugin 只有 `humanization.context_providers=true` 时注册 RegisterProvider，默认 off 不改变生产。
7. 测试：新增 `tests/test_register_provider.py`，覆盖 happy path、缺 bus 降级、affection close 改档、无 group/无 state、chat qctx 传 bus/turn_id。

风险评估：

- 过度拟人风险：Provider 只写“目标与边界”，不写模板句，避免僵硬套话。
- 默认行为风险：V0 默认 `context_providers=false`，ChatPlugin 默认不注册；单测直接实例化 Provider 验证。
- 状态读错风险：per_session/per_user/per_turn scope 不同，测试逐个锁定。
- budget 风险：priority=43 不会压过 slang/style profile，仍比 style expression/episode 更不容易被裁。

### V2 完成记录（执行者 GPT）

改动：

- `services/block_trace/providers.py`：`QueryContext` 新增可选 `runtime_state` / `turn_id`，保持旧 Provider 构造兼容。
- `services/llm/client.py`：thinker 正常返回后保留本轮 `turn_id`，创建 Provider `QueryContext` 时传入 `RuntimeStateBus` 与 `turn_id`。
- `services/block_trace/register_provider.py`：新增 `RegisterProvider`，读取 `state.register.label`、`state.affection.<uid>.familiarity`、`bus.state.clock.current`，输出 stable 语域目标块。
- `services/block_trace/__init__.py` / `plugins/chat/plugin.py`：导出并在 `humanization.context_providers=true` 时注册 `RegisterProvider`，默认 off。
- `tests/test_register_provider.py` / `tests/test_thinker_runtime_state.py`：新增 Provider happy path、无 bus 降级、affection close、turn_id mismatch、private scope、LLMClient qctx 传参覆盖。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_register_provider.py tests/test_providers.py tests/test_thinker_runtime_state.py tests/test_humanization_contract.py` → 34 passed（仅 aiohttp/Python 3.13 已知 warning）。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/providers.py services/block_trace/register_provider.py services/block_trace/__init__.py services/llm/client.py plugins/chat/plugin.py tests/test_register_provider.py tests/test_thinker_runtime_state.py` → passed。

自审：

- D1：`rg "RegisterProvider|register_provider|runtime_state|turn_id" services/block_trace services/llm/client.py plugins/chat/plugin.py tests/test_register_provider.py tests/test_thinker_runtime_state.py` 命中 V2 Provider、QueryContext 扩展、LLMClient 接线和专项测试。
- D2：V2 自身无异步 LLM 写入；per-turn clock 读取必须匹配 `turn_id`，`test_register_provider_requires_matching_turn_for_clock_state` 已锁定读错降级。
- D3：默认生产行为由 `humanization.context_providers=false` 保护；Provider 直接实例化时也可无 bus 降级为 neutral。
- 回滚：`git restore services/block_trace/providers.py services/block_trace/register_provider.py services/block_trace/__init__.py services/llm/client.py plugins/chat/plugin.py tests/test_register_provider.py tests/test_thinker_runtime_state.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V2（真实回滚前需保留后续追踪记录）。

### V3 领单拆分（执行前）

目标：新增 `CatchphraseProvider`，基于 `LearningNormalizerStore(domain="catchphrase")` 的已归一化 cluster 生成 dynamic 口头禅候选，让回复能自然沾一点群内/全局常用短语，但不把口头禅硬塞成模板句。

详细步骤：

1. 数据源审查：确认当前没有独立 catchphrase pool/store；V3 只能从 U5 的 `learning_normalizer_clusters` 读取 `domain="catchphrase"`，不新建平行持久化。
2. Store 只读入口：给 `LearningNormalizerStore` 增加 `list_prompt_candidates(domain, group_id, limit, exclude_cluster_ids)`，按 group scope + cross_group_visible 候选读取 active/locked cluster，返回 canonical_text / cluster_id / item_count / confidence 等结构。
3. Provider 实现：新增 `services/block_trace/catchphrase_provider.py`，`store_getter` 懒加载，要求 group_id；按 register label 决定是否更适合注入：serious/distant 降低数量，playful/affectionate 放宽。
4. 去重状态：读取/写入 `REGISTER_RECENT_USED_SLOT` 的 per_session value，记录最近使用的 catchphrase cluster_id，避免 30 分钟内重复推荐同一口头禅；写入 fail-soft。
5. Prompt 渲染：输出 dynamic block，明确“可以自然借用，不要硬套/不要逐字解释”，包含最多 2 条候选；priority=46，弱于 style expression=45，强于 episode=50。
6. 装配：ChatPlugin 只有 `humanization.context_providers=true` 时注册 `CatchphraseProvider`；默认 off 不改变生产。
7. 测试：新增 `tests/test_catchphrase_provider.py`，覆盖候选输出、无 store/no group 降级、recent 去重、serious/distant 保守、ChatPlugin/ProviderBus 注册。

风险评估：

- 数据源风险：normalizer cluster 未必都是“可直接说”的短语；本轮只做短文本过滤与数量限制，不生成新文案。
- 机械套话风险：prompt 文案只提供候选和边界，禁止强行逐字套用。
- 状态污染风险：recent 写入必须 fail-soft；无 RuntimeStateBus 时 Provider 仍可只读候选并输出。
- budget 风险：priority=46 让口头禅低于 slang/style 表达参考，高于 episode，避免挤掉更关键的语境块。

### V3 完成记录（执行者 GPT）

改动：

- `services/learning_normalizer/store.py`：新增 `LearningNormalizerPromptCandidate` 与 `list_prompt_candidates()`，只读召回 `domain="catchphrase"` 的 group/global/cross-group active/locked cluster。
- `services/block_trace/catchphrase_provider.py`：新增 `CatchphraseProvider`，读取 register label 与 `REGISTER_RECENT_USED_SLOT`，输出 dynamic 口头禅候选块并写 per_session recent 去重状态。
- `services/block_trace/__init__.py` / `services/learning_normalizer/__init__.py`：导出新增 Provider 与候选类型。
- `plugins/chat/plugin.py`：仅在 `humanization.context_providers=true` 时初始化 `storage/learning_normalizer.db` 的 catchphrase normalizer 并注册 `CatchphraseProvider`；shutdown 关闭该 store。
- `tests/test_catchphrase_provider.py`：新增 5 条 V3 专项测试，覆盖 normalizer 召回、无 store/no group、recent 去重、serious register 保守、ProviderBus active 输出。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_catchphrase_provider.py tests/test_learning_normalizer_catchphrase.py tests/test_providers.py` → 24 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/learning_normalizer/store.py services/learning_normalizer/__init__.py services/block_trace/catchphrase_provider.py services/block_trace/__init__.py plugins/chat/plugin.py tests/test_catchphrase_provider.py` → passed。

自审：

- D1：`rg "CatchphraseProvider|list_prompt_candidates|REGISTER_RECENT_USED_SLOT|catchphrase_normalizer" services plugins tests` 命中仅 V3 Provider、normalizer 只读入口、ChatPlugin 接线和专项测试。
- D2：V3 没有 LLM 等待取消路径；recent 写入在 Provider 只读召回后 fail-soft，bus 不存在时不写状态但仍可降级输出。
- D3：数据源未新增平行池，Provider 与后续 V7 种子都指向 `storage/learning_normalizer.db` 的 `domain="catchphrase"`。
- 回滚：`git restore services/learning_normalizer/store.py services/learning_normalizer/__init__.py services/block_trace/catchphrase_provider.py services/block_trace/__init__.py plugins/chat/plugin.py tests/test_catchphrase_provider.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V3（真实回滚前需保留后续追踪记录）。

### V4 领单拆分（执行前）

目标：让 `EpisodeProvider` 在已有 `enabled_for_prompt` 不变量之上支持 register-aware 可选过滤：当 episode meta 明确声明适用/避开的 register 时，按当前 `state.register.label` 过滤；没有声明的 episode 保持旧行为。

详细步骤：

1. 代码证据审查：确认 `EpisodeStore` 已有 `meta_json`，`EpisodeProvider` 当前只按 `enabled_for_prompt` + group + confidence/update 排序召回，不存在 register 字段或 schema。
2. register 读取：Provider 从 `RuntimeStateBus` 读取 `REGISTER_LABEL_SLOT`，scope 采用当前 session/group/user；无 bus 或无 label 时降级为 `neutral`。
3. meta 约定：支持 `register_labels` / `allowed_registers` / `target_registers` 作为允许列表，支持 `avoid_register_labels` / `blocked_registers` 作为排除列表；字段缺失则不过滤。
4. 过采样：为了过滤后仍有足够候选，`list_for_recall(limit=top_k*3)`，过滤后再截断 `top_k`；无 meta 时旧 top_k 顺序不变。
5. 测试：新增/扩展 episode provider 测试，覆盖 playful 只召回匹配 meta、serious 避开 playful-only、无 runtime state 保持旧行为。
6. 验证：跑 `tests/test_episode_context_provider.py`、`tests/test_register_provider.py`、`tests/test_humanization_contract.py` 与 ruff。

风险评估：

- schema 风险：不改 episodes 表结构，只读现有 `meta_json`，避免迁移。
- 召回回归风险：没有 register meta 的 episode 不被过滤；现有测试应保持通过。
- 过采样风险：最多 `top_k*3`，默认 9 条，仍是纯 SQLite 只读，小于现有 prompt 渲染成本。
- 状态读错风险：register 缺失一律 neutral；不会猜测关系，也不会读取 V2 target register 文本。

### V4 完成记录（执行者 GPT）

改动：

- `services/block_trace/episode_provider.py`：从 `RuntimeStateBus` 读取 `REGISTER_LABEL_SLOT`；`list_for_recall()` 过采样后按 episode `meta` 的 `register_labels` / `allowed_registers` / `target_registers` 与 `avoid_register_labels` / `blocked_registers` 做可选过滤。
- `tests/test_episode_context_provider.py`：新增 3 条 V4 专项测试，覆盖 register meta 匹配、avoid meta 排除、无 meta/无 runtime state 旧行为保持。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_episode_context_provider.py tests/test_register_provider.py tests/test_humanization_contract.py` → 30 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/episode_provider.py tests/test_episode_context_provider.py` → passed。

自审：

- D1：`rg "REGISTER_LABEL_SLOT|_episode_matches_register|register_labels|avoid_register_labels" services/block_trace/episode_provider.py tests/test_episode_context_provider.py` 命中仅 V4 register-aware 读取、过滤 helper 和专项测试。
- D2：V4 无新增异步写入；`update_last_used()` 仍只对过滤后真正召回的 episode best-effort stamp，既有 cancel-path 测试继续覆盖 store 一致性。
- D3：不改 episodes schema；没有 meta 的历史 episode 不被过滤，避免既有 prompt recall 大面积回归。
- 回滚：`git restore services/block_trace/episode_provider.py tests/test_episode_context_provider.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V4（真实回滚前需保留后续追踪记录）。

### V6 领单拆分（执行前）

目标：让 `SlangProvider` 在保留直接命中和旧排序语义的前提下，支持 mood-fit 可选加权；当 slang term 的 `meta_json` 明确含有 mood fit 信息时，优先选择更贴合当前 mood 的表达。

详细步骤：

1. 代码证据审查：确认 `SlangStore` 没有独立 mood_fit 列，`SlangTerm.meta` 是现成扩展面；当前召回先分 direct/indirect，再按 group scope、confidence、usage、last_seen 排序。
2. Store 入口扩展：`get_injectable_terms()` / `build_prompt_block()` / `build_prompt_block_with_refs()` 增加可选 `mood_fit_target`，默认 `None` 保持旧行为。
3. 加权实现：若 term meta 有 `mood_fit` / `mood_fit_target` / `mood_profile_fit` 数值，则按与 target 的接近度生成 alignment，作为 rank 中 confidence 之后、usage 之前的温和因子；无 meta 时 alignment 为 0，不改变旧排序主轴。
4. Provider 接线：`QueryContext` 增加可选 `mood_fit_target`；`SlangProvider` 传给 store；fake store 测试锁定参数传递。
5. LLMClient 接线：复用已注入的 `mood_getter`，从 `MoodProfile` 计算 0..1 target，放进 Provider `QueryContext`；无 mood_getter 时传 `None`。
6. 测试：覆盖 store mood-fit 改变排序、无 target 保持旧排序、provider 参数传递；回归 provider 与 slang store 既有测试。

风险评估：

- 旧数据风险：大量 term 没有 mood meta；本轮无 meta 不加权，避免旧黑话排序漂移。
- 直接命中风险：不跨 direct/indirect 合并重排，直接命中仍优先，mood 只在同类候选内细排。
- 目标口径风险：mood target 只是 0..1 的“活跃/开放”近似值，不把 mood prompt 语义硬编码进 slang。
- ABI 风险：新增参数全部可选，旧调用方和 fake store 若不支持新参数需保持兼容。

### V6 完成记录（执行者 GPT）

改动：

- `services/block_trace/providers.py`：`QueryContext` 新增可选 `mood_fit_target`。
- `services/llm/client.py`：新增 `_build_provider_mood_fit_target()`，复用现有 `mood_getter` 将 energy / openness / valence 压成 0..1 target，并传入 Provider qctx。
- `services/block_trace/slang_provider.py`：调用 `SlangStore.build_prompt_block_with_refs()` / `build_prompt_block()` 时传递 `mood_fit_target`。
- `services/slang/store.py`：`get_injectable_terms()` / `build_prompt_block()` / `build_prompt_block_with_refs()` 新增可选 `mood_fit_target`；仅当 term meta 有 `mood_fit` / `mood_fit_target` / `mood_profile_fit` 时在同类候选内加权排序。
- `tests/test_slang_store.py` / `tests/test_providers.py` / `tests/test_thinker_runtime_state.py`：新增真实排序、Provider 参数传递、LLMClient qctx mood target 覆盖。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_slang_store.py tests/test_providers.py tests/test_thinker_runtime_state.py` → 42 passed（仅 aiohttp/Python 3.13 已知 warning）。
- `source ./scripts/dev/env.sh && uv run ruff check services/slang/store.py services/block_trace/slang_provider.py services/block_trace/providers.py services/llm/client.py tests/test_slang_store.py tests/test_providers.py tests/test_thinker_runtime_state.py` → passed。

自审：

- D1：`rg "mood_fit_target|_build_provider_mood_fit_target|_mood_fit_alignment" services/slang services/block_trace services/llm tests` 命中仅 V6 参数通路、helper 和专项测试。
- D2：V6 无新增异步写入；只读召回排序变化由测试锁定，旧无 target / 无 meta 场景不改变 direct/indirect 分层。
- D3：不新增 RuntimeStateBus 槽位、不改 slang schema；mood target 由现有 runtime mood_getter 临时计算。
- 回滚：`git restore services/slang/store.py services/block_trace/slang_provider.py services/block_trace/providers.py services/llm/client.py tests/test_slang_store.py tests/test_providers.py tests/test_thinker_runtime_state.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V6（真实回滚前需保留后续追踪记录）。

### U8 领单拆分（执行前）

目标：新增 `StickerRegisterProvider`，并把 `send_sticker` 成功发送后的 sticker id 写入 `RuntimeStateBus` 的 `state.sticker.recent_used` per-session 槽位；后续 prompt provider 读取 30 分钟内近期已发 sticker，提示 LLM 换图，降低拟人回复里的表情包复读感。

详细步骤：

1. 现状审查：确认 `StickerStore.format_prompt_view()` 当前仍作为 stable 表情包库全量视图；`SendStickerTool.execute()` 成功后只调用 `record_send()`；`StickerPlugin.register_tools()` 没有把 `ctx.runtime_state` 传给工具。
2. 工具写状态：给 `SendStickerTool` 增加可选 `runtime_state` 参数；发送成功且 `record_send()` 后，fail-soft 写 `STICKER_RECENT_USED_SLOT`，value 保存 `sticker_ids`、`updated_at`，scope 使用 `ToolContext.session_id/group_id/user_id`，TTL 30 分钟。
3. Provider 实现：新增 `services/block_trace/sticker_register_provider.py`，读取 `STICKER_RECENT_USED_SLOT`；无 bus / 无 recent / 已过期时空输出；有 recent 时输出 dynamic block，标记“近期已用，建议换”。
4. Provider 内容边界：Provider 不重复 dump 全表表情库；只列近期已发 sticker，并可根据 `StickerStore.list_all()` 提示“库里还有其它候选”，避免挤占旧 stable 表情包库预算。
5. Priority 校准：使用 priority=47，弱于 slang/style/catchphrase（40/42/45/46），强于 episode（50），符合 P0.0 low-wins 结论。
6. 装配：`ChatPlugin` 仅在 `humanization.context_providers=true` 且 `humanization.sticker_register_provider=true` 时注册 Provider；`StickerPlugin` 注册 `SendStickerTool` 时传入 `ctx.runtime_state`，默认无 runtime_state 时旧行为不变。
7. 测试：新增 `tests/test_sticker_register_provider.py` 覆盖 recent 输出、无 bus/no recent 空输出、TTL 过期空输出、ProviderBus active 输出；扩展 `tests/test_sticker_tools.py` 覆盖发送成功写 bus、发送失败不写。

风险评估：

- 默认行为风险：Provider 注册受 `context_providers` 与 `sticker_register_provider` 双开关保护；工具写 bus 是 fail-soft，不影响发送成功路径。
- 状态污染风险：只在真实发送成功后写；发送失败、未找到、无 bot 都不写。
- Prompt 膨胀风险：Provider 不复制表情包库，只写近期已发列表和换图提示。
- Scope 风险：per_session slot 用当前 session/group/user scope；群聊和私聊不会串读。
- 回滚风险：新增 Provider、工具可选参数和 ChatPlugin/StickerPlugin 接线均可用 git restore 在 30 秒内撤销。

### U8 完成记录（执行者 GPT）

改动：

- `services/block_trace/sticker_register_provider.py`：新增 81 行 `StickerRegisterProvider`，读取 `STICKER_RECENT_USED_SLOT`，输出 dynamic 表情包近期使用提示；priority=47，不重复注入全量表情包库。
- `services/tools/sticker_tools.py`：`SendStickerTool` 增加可选 `runtime_state`，发送成功并 `record_send()` 后 fail-soft 写 `state.sticker.recent_used`，30 分钟 TTL；发送失败/取消路径不写。
- `plugins/sticker/plugin.py` / `plugins/chat/plugin.py` / `services/llm/client.py`：工具注册传入 `ctx.runtime_state`；主回复工具上下文补 `session_id`；ChatPlugin 仅在 `humanization.context_providers=true` 且 `humanization.sticker_register_provider=true` 时注册 Provider。
- `services/block_trace/__init__.py`：导出 `StickerRegisterProvider`。
- `tests/test_sticker_register_provider.py` / `tests/test_sticker_tools.py`：新增 recent 输出、无 bus/no recent、TTL 过期、ProviderBus active、成功写 bus、失败/取消不脏写覆盖。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_sticker_register_provider.py tests/test_sticker_tools.py tests/test_humanization_contract.py tests/test_providers.py` → 62 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/sticker_register_provider.py services/block_trace/__init__.py services/tools/sticker_tools.py plugins/chat/plugin.py plugins/sticker/plugin.py tests/test_sticker_register_provider.py tests/test_sticker_tools.py` → passed。

自审：

- D1：`rg "StickerRegisterProvider|sticker_register_provider|STICKER_RECENT_USED_SLOT|send_sticker:recent_used|test_send_sticker_cancel_path" services/block_trace services/tools plugins/chat/plugin.py plugins/sticker/plugin.py tests/test_sticker_register_provider.py tests/test_sticker_tools.py` 命中仅 U8 Provider、工具状态写入、双开关接线和专项测试。
- D2：`test_send_sticker_cancel_path_does_not_write_recent_state` 覆盖发送 await 被取消时 `record_send()` 与 RuntimeStateBus 都不写。
- D3：默认行为由 `context_providers=false` / `sticker_register_provider=false` 保护；无 RuntimeStateBus 时 `SendStickerTool(store)` 保持旧行为。
- 回滚：`git restore services/block_trace/sticker_register_provider.py services/block_trace/__init__.py services/tools/sticker_tools.py plugins/chat/plugin.py plugins/sticker/plugin.py tests/test_sticker_register_provider.py tests/test_sticker_tools.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U8（真实回滚前需保留后续追踪记录）。

### V15 领单拆分（执行前）

目标：新增 `ThinkerProvider`，把 V13 写入 `RuntimeStateBus` 的 `bus.state.thinker.last_decision` 迁移到 PromptBlock 管线；默认 off 时完全保留旧 `LLMClient` 旁路 system block，开关 on 时由 Provider 输出 dynamic block 并跳过旧旁路，避免同一 thinker 决策双注入。

详细步骤：

1. 现状审查：确认 thinker 决策已在 V13 写入 `THINKER_LAST_DECISION_SLOT` per_turn，`LLMClient` 创建 `QueryContext` 时已传 `runtime_state/turn_id`；旧旁路仍在 `services/llm/client.py` 直接追加 `【你决定说话...】` system block。
2. Provider 实现：新增 `services/block_trace/thinker_provider.py` ≤90 行，读取 `THINKER_LAST_DECISION_SLOT`；无 bus、无 turn_id、turn scope 不匹配、action 非 reply 时空输出。
3. Prompt 文案：输出 dynamic block，包含本轮意图、语气、sticker yes/no 和 retrieve_mode；不使用“你要说：xxx”式模板，避免主 LLM 机械复读 thought。
4. Priority 校准：使用 priority=48，弱于 register/style/catchphrase/sticker，强于 episode=50；满足 P0.0 low-wins 语义。
5. 装配与双注入控制：ChatPlugin 仅在 `humanization.context_providers=true` 且 `humanization.thinker_provider=true` 时注册；`LLMClient` 增加 `thinker_provider_enabled` 只控制旧旁路 fallback，默认 False 保持旧行为。
6. 测试：新增 `tests/test_thinker_provider.py` 覆盖读取输出、无 bus/no turn 空输出、turn mismatch 空输出、ProviderBus active；扩展 runtime/client 测试覆盖开关 off 保留旁路、on 时不再追加旧旁路。

风险评估：

- 行为回归风险：默认 `thinker_provider=false`，旧旁路保留；仅开关 on 时迁移到 PromptBlock。
- 双注入风险：必须在 `LLMClient` 里用同一 flag 跳过旧旁路，否则主 LLM 会收到两份 thinker 指令。
- 复读风险：Provider 文案改成“意图/边界/语气”结构，不写“你决定说话：原 thought”作为近因模板。
- 状态错读风险：per_turn 读取必须匹配 `turn_id`；缺失时降级空输出，不猜最近一次 thinker。
- cancel-path 风险：V15 自身只读状态；已有 `test_thinker_runtime_state_cancel_path_does_not_dirty_write` 覆盖 thinker 未返回前不写 RuntimeStateBus，Provider 因无状态空输出。

### V15 完成记录（执行者 GPT）

改动：

- `services/block_trace/thinker_provider.py`：新增 88 行 `ThinkerProvider`，读取 `THINKER_LAST_DECISION_SLOT` per_turn 状态，输出 dynamic `本轮意图` PromptBlock；action 非 reply、无 bus、无 turn_id、turn mismatch 均空输出。
- `services/llm/client.py`：新增 `thinker_provider_enabled` fallback 开关；默认 False 时保留旧 `【你决定说话...】` 旁路，开关 True 时跳过旧旁路，避免与 Provider 双注入。
- `plugins/chat/plugin.py`：仅当 `humanization.context_providers=true` 且 `humanization.thinker_provider=true` 时注册 `ThinkerProvider`，并同步传 `thinker_provider_enabled=true` 给 LLMClient。
- `services/block_trace/__init__.py`：导出 `ThinkerProvider`。
- `tests/test_thinker_provider.py` / `tests/test_thinker_runtime_state.py`：新增 Provider 读取/空输出/ProviderBus active 覆盖，以及 LLMClient 默认旧旁路、Provider 接管不双注入覆盖。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_thinker_provider.py tests/test_thinker_runtime_state.py tests/test_humanization_contract.py tests/test_providers.py` → 38 passed（仅 aiohttp/Python 3.13 已知 warning）。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/thinker_provider.py services/block_trace/__init__.py services/llm/client.py plugins/chat/plugin.py tests/test_thinker_provider.py tests/test_thinker_runtime_state.py` → passed。

自审：

- D1：`rg "ThinkerProvider|thinker_provider|THINKER_LAST_DECISION_SLOT|thinker_provider_enabled|你决定说话|test_llm_client_uses_thinker_provider" services/block_trace services/llm/client.py plugins/chat/plugin.py tests/test_thinker_provider.py tests/test_thinker_runtime_state.py` 命中仅 V15 Provider、fallback 开关、旧旁路保留测试和双注入保护测试。
- D2：V15 自身只读 per_turn 状态；`test_thinker_runtime_state_cancel_path_does_not_dirty_write` 继续覆盖 thinker 未返回前不写状态，Provider 无状态时空输出。
- D3：默认 off 行为保持：`test_llm_client_keeps_legacy_thinker_block_when_provider_disabled` 锁定旧旁路；开关 on 行为：`test_llm_client_uses_thinker_provider_without_legacy_double_injection` 锁定不双注入。
- 回滚：`git restore services/block_trace/thinker_provider.py services/block_trace/__init__.py services/llm/client.py plugins/chat/plugin.py tests/test_thinker_provider.py tests/test_thinker_runtime_state.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V15（真实回滚前需保留后续追踪记录）。

### Wave 6 共用前置审查（执行前）

目标：进入 scorer / rewrite / runtime 装配前，确认所有新逻辑都默认 off 或只读，避免把 humanization 的评价面直接变成生产热路径行为。

详细步骤：

1. scorer 边界：V8 只新增本地评分器，不接主 LLM；V11 才读取 rewrite threshold，V9 才持久化 metrics。
2. 状态边界：复用 U6 的 `LAST_METRICS_SLOT`，V8 可选写 per-turn snapshot，但不得新增平行 bus 或持久化表。
3. 热路径边界：V10/V11/U12/V16/V17 都必须受已有 V0 flags 保护；默认配置下回复行为保持旧路径。
4. 依赖顺序：V8 → V9 → V11；V10 独立接 Humanizer runtime；U12+V16 合并做 semantic gate；V17 最后装配 worker；U13 只观测不改行为。
5. 验证策略：每条任务跑自身专项 + 最近相关热路径测试；ruff 只扫改动范围，避免把未触碰旧债混进本轮。

风险评估：

- scorer 误判风险：V8 分数只作为观测，不直接阻断回复；V11 默认 off。
- 状态污染风险：V8 写 `LAST_METRICS_SLOT` 时必须是显式调用、per-turn scope；取消路径不写。
- 预算膨胀风险：Wave 6 不新增 PromptBlock Provider，除已完成 Wave 5 外不增加 prompt token。
- 灰度风险：V11/U12/V16 的行为开关默认关闭，Wave 7 再进入配置灰度。

### V8 领单拆分（执行前）

目标：新增 `StylometricScorer`，用本地规则和 rapidfuzz 低成本评估回复的五个拟人维度：content、register、mood、surface、sticker_reuse；输出总分和 issues，供后续 V9 持久化、V11 rewrite-loop、V12 度量脚本复用。

详细步骤：

1. 数据结构：新增 `HumanizationScore` dataclass，包含五轴分数、总分、issues、meta，并提供 `to_state_value()`。
2. Scorer API：新增 `StylometricScorer.score(text, *, register=None, mood=None, recent_sticker_ids=(), references=(), bus=None, scope=None)`；默认纯函数，不传 bus 不写状态。
3. content 轴：基于空回复、过短/过长、与 references 的 rapidfuzz 相似度粗评内容稳健性；不接新检索。
4. register 轴：按 register label 检查长度、感叹/颜文字/过熟表达等表面信号，避免 quiet/distant/playful 互相跑偏。
5. mood 轴：读取 mood energy/valence，判断低能量时过度兴奋、高能量时过度冷淡等粗信号。
6. surface 轴：惩罚 em dash、装饰符、模板口吻、过多标点、句末句号率异常等拟人表面问题。
7. sticker_reuse 轴：检测 `«表情包:xxx»` 是否命中 recent sticker ids，命中则扣分。
8. 状态写入：只有显式传 `bus+scope` 时写 `LAST_METRICS_SLOT`，source=`humanization_source("stylometric_scorer:score")`；写入在所有同步评分完成后发生，取消前不脏写。
9. 测试：新增 `tests/test_humanization_scorer.py` 至少 12 条，覆盖五轴各类扣分、总分阈值、bus 写入、cancel-path。

风险评估：

- 误杀自然表达风险：V8 只观测不决策；分数阈值后续 V11 默认 off。
- 中英文表面规则风险：仅使用保守 regex，不把所有口语化表达判坏。
- reference 风险：references 只是可选对照，不读取 CardStore 或外部检索，避免新增依赖。
- 状态写入风险：默认不写 bus；测试锁定取消路径和 per-turn scope。

### V8 完成记录（执行者 GPT）

改动：

- `services/humanization/scorer.py`：新增 182 行 `HumanizationScore` / `StylometricScorer`，提供 content、register、mood、surface、sticker_reuse 五轴本地评分；默认纯函数，不接主 LLM、不持久化。
- `services/humanization/__init__.py`：导出 `HumanizationScore` / `StylometricScorer`，供 V9/V11/V12 复用。
- `tests/test_humanization_scorer.py`：新增 13 条测试，覆盖好回复高分、五轴扣分、`LAST_METRICS_SLOT` 写入、取消路径不脏写。

验证：

- `source ./scripts/dev/env.sh && uv run ruff check services/humanization/scorer.py services/humanization/__init__.py tests/test_humanization_scorer.py` → passed。
- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_scorer.py tests/test_humanization_contract.py` → 19 passed。

自审：

- D1：`rg "StylometricScorer|HumanizationScore|LAST_METRICS_SLOT|stylometric_scorer|test_scorer_cancel" services/humanization tests/test_humanization_scorer.py` 命中仅 V8 scorer、导出、contract slot 和专项测试。
- D2：`test_scorer_cancel_path_does_not_dirty_write` 覆盖 `score_async()` 在评分前被取消时不写 `LAST_METRICS_SLOT`。
- D3：V8 只观测不决策；不改 prompt、不改调度、不新增持久化表，后续 V9/V11 才接存储与 rewrite。
- 回滚：`git restore services/humanization/scorer.py services/humanization/__init__.py tests/test_humanization_scorer.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V8（真实回滚前需保留后续追踪记录）。

### V9 领单拆分（执行前）

目标：在 `BlockTraceStore` 中新增 `humanization_metrics` 持久化面，让 V8 的 `HumanizationScore` 可以按 request/group/session/turn 记录、查询和统计；本轮只提供存储 API，不自动接主回复热路径。

详细步骤：

1. schema 审查：沿用 `BlockTraceStore.init()` 当前 `CREATE TABLE IF NOT EXISTS` 模式，不引入迁移框架；新增表与 `prompt_block_traces` 并列。
2. 表结构设计：字段包含 `metric_id`、`request_id`、`group_id`、`session_id`、`turn_id`、`score`、`axes_json`、`issues_json`、`metadata_json`、`created_at`；JSON 字段全部 `ensure_ascii=False`。
3. 索引设计：按 `request_id`、`group_id/session_id/created_at`、`created_at` 建索引，满足 V12 度量脚本和调试查询；不改旧 trace 索引。
4. API 设计：新增 `record_humanization_metrics()`、`list_humanization_metrics()`、`humanization_metric_stats()`；输入接受 `HumanizationScore` 或 dict，输出稳定 dict，避免把 scorer dataclass 强绑到 block_trace 类型层。
5. 兼容边界：`record_batch()` / `stats()` / `prune()` 旧行为保持；`prune()` 同步清理两张表，但返回值仍只代表 prompt trace 删除数，避免破坏旧调用方。
6. 测试：新增 `tests/test_humanization_metrics_persist.py` 覆盖建表、记录/查询 JSON round-trip、统计均值/issue 计数、prune 清理旧 metrics；回归 `tests/test_block_trace.py`。

风险评估：

- schema 风险：新增独立表和索引，不改现有表列；旧 SQLite 文件 init 时可幂等建表。
- 依赖风险：Store 层不 import V8 scorer 类；用 duck typing 读取 `total/axes/issues/meta`，减少循环依赖。
- 热路径风险：V9 不自动调用记录 API；只有后续 V11/V12 显式接入时才写库。
- prune 风险：旧 `prune()` 返回值不变；metrics 清理由同一 cutoff 执行但不改变旧测试断言。

### V9 完成记录（执行者 GPT）

改动：

- `services/block_trace/store.py`：新增 `humanization_metrics` 表与 `idx_hm_request` / `idx_hm_group_session_created` / `idx_hm_created` 索引；新增 `record_humanization_metrics()`、`list_humanization_metrics()`、`humanization_metric_stats()`。
- `services/block_trace/store.py`：新增 `_conn()` 类型窄化 helper，Store 未初始化时明确抛 `RuntimeError`；`prune()` 同步清理旧 humanization metrics，但返回值继续只代表 prompt trace 删除数。
- `tests/test_humanization_metrics_persist.py`：新增 4 条测试，覆盖建表、`HumanizationScore` 写入/查询 JSON round-trip、按 group 统计平均分和 issue 计数、prune 清理旧 metrics。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_metrics_persist.py tests/test_block_trace.py` → 26 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/store.py tests/test_humanization_metrics_persist.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright services/block_trace/store.py tests/test_humanization_metrics_persist.py` → 0 errors。

自审：

- D1：`rg "humanization_metrics|record_humanization_metrics|list_humanization_metrics|humanization_metric_stats|idx_hm_" services/block_trace/store.py tests/test_humanization_metrics_persist.py` 命中仅 V9 schema、API 与专项测试。
- D2：V9 不新增异步热路径调用；写入 API 是显式 await，未被主回复链路调用，cancel-path 风险留给 V11 接入点验证。
- D3：不改 `prompt_block_traces` schema；旧 `stats()` / `recent()` / `list_for_request()` 回归测试继续通过。
- 回滚：`git restore services/block_trace/store.py tests/test_humanization_metrics_persist.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V9（真实回滚前需保留后续追踪记录）。

### V10 领单拆分（执行前）

目标：把 U3 已落地的 `Humanizer.delay(text, *, group_id, register, slot, mood)` 真正接到 `GroupChatScheduler._send_to_group()`；发送前读取当前群的 register、mood、time slot runtime 信息并传给 Humanizer，使延迟节奏能随语域/心情变化。

详细步骤：

1. 构造入口：`GroupChatScheduler.__init__` 增加可选 `runtime_state`，默认 `None`；ChatPlugin 创建 scheduler 时传 `ctx.runtime_state`。
2. register 读取：发送前用 `Scope(session_id=f"group_{group_id}", group_id=group_id, user_id=slot.last_user_id)` 读取 `REGISTER_LABEL_SLOT`；无 bus/无 snapshot 时传 `None`，保持旧 1.0x multiplier。
3. mood 读取：复用 scheduler 现有 `_mood_getter`，新增 `_get_current_mood()` helper，优先带 `group_id/session_id` 调用，兼容无参旧 getter。
4. slot 读取：优先从 `runtime_state` 读取 `CLOCK_CURRENT_SLOT`；由于 scheduler 发送路径没有稳定 thinker `turn_id`，本轮同时提供 `_current_slot_payload()` 从 `talk_schedule.current_slot()` 生成低风险 fallback dict，避免引入 per-turn 猜测。
5. 发送路径：`_send_to_group()` 调用 `self._humanizer.delay(text, group_id=group_id, register=..., slot=..., mood=...)`；`humanize="skip"` 仍完全跳过 Humanizer。
6. 测试：新增 `tests/test_humanizer_runtime.py` 覆盖 register/mood/slot 参数传入、无 runtime_state 降级、`humanize="skip"` 不调用 delay；回归 scheduler/humanizer 测试。

风险评估：

- 热路径风险：只改变 Humanizer 的入参，不改变发送/重试语义；无 runtime_state 或无状态时旧延迟 multiplier 保持 1.0。
- scope 风险：register 是 per_session，Scope 的 per_session key 只看 `session_id`；`group_id/user_id` 只作 trace 上下文，不影响读取。
- turn 风险：scheduler 没有主回复 turn_id，不猜最新 per_turn clock；slot fallback 来自已存在 `talk_schedule`，无法取到则传 `None`。
- cancel-path 风险：本任务只读状态、不写 bus；取消发生在 `Humanizer.delay()` sleep 或发送 await 时不会产生脏状态。

### V10 完成记录（执行者 GPT）

改动：

- `services/scheduler.py`：`GroupChatScheduler` 新增可选 `runtime_state`；发送前只读 `REGISTER_LABEL_SLOT` / `CLOCK_CURRENT_SLOT` 和现有 `mood_getter`，通过 `_humanizer_runtime()` 传给 `Humanizer.delay(text, group_id=..., register=..., slot=..., mood=...)`。
- `services/scheduler.py`：`humanize="skip"` 保持完全跳过 Humanizer；无 runtime state、无 register、无 clock 时降级为 `None`，旧 1.0x multiplier 保持。
- `plugins/chat/plugin.py`：创建 scheduler 时传入 `ctx.runtime_state`。
- `tests/test_humanizer_runtime.py`：新增 3 条测试，覆盖 runtime register/mood/slot 传入、无 runtime state 降级、`humanize="skip"` 不调用延迟。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanizer_runtime.py tests/test_humanizer_register.py tests/test_scheduler.py` → 46 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/scheduler.py plugins/chat/plugin.py tests/test_humanizer_runtime.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright services/scheduler.py tests/test_humanizer_runtime.py` → 0 errors。

自审：

- D1：`rg "runtime_state=ctx.runtime_state|_humanizer_runtime|_current_register|_current_slot_payload|REGISTER_LABEL_SLOT|CLOCK_CURRENT_SLOT|test_scheduler_passes_runtime" services/scheduler.py plugins/chat/plugin.py tests/test_humanizer_runtime.py` 命中仅 V10 scheduler helper、ChatPlugin 接线和专项测试。
- D2：V10 不写 RuntimeStateBus；取消路径只会中断 `Humanizer.delay()` 或发送 await，不产生脏状态。
- D3：`humanize="skip"` 测试锁定首段/引用回复仍不走 Humanizer；无 runtime state 降级测试锁定旧入参兼容。
- 回滚：`git restore services/scheduler.py plugins/chat/plugin.py tests/test_humanizer_runtime.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V10（真实回滚前需保留后续追踪记录）。

### V11 领单拆分（执行前）

目标：在 `LLMClient` 主回复路径加入 critic-rewrite-loop，但默认由 `humanization.rewrite_threshold=-1.0` 关闭；开启后用 V8 `StylometricScorer` 本地评分，分数低于阈值才追加一次重写 LLM round。

详细步骤：

1. 配置接线：`LLMClient.__init__` 增加 `humanization_rewrite_threshold=-1.0`；ChatPlugin 传 `config.humanization.rewrite_threshold`；默认不改变现有行为。
2. 本地评分：新增 `_score_humanization_reply()`，读取 `REGISTER_LABEL_SLOT`、`STICKER_RECENT_USED_SLOT`、现有 `mood_getter`，调用 `StylometricScorer.score()`；有 `runtime_state+turn_id` 时写 `LAST_METRICS_SLOT`。
3. metrics 持久化：若存在 `BlockTraceStore`（从 `budget_manager._store` best-effort 取得），调用 V9 `record_humanization_metrics()`；失败只 debug，不影响回复。
4. rewrite 触发：仅当 `rewrite_threshold >= 0` 且 score < threshold 时，构造一次主模型 rewrite 请求；请求用原 system blocks/messages + 一条 user 指令，明确“保留事实/不要解释评分/只输出改写后回复”。
5. 热路径位置：在 `_finalize_visible_reply()` 后、kaomoji enforcement/segmentation/记忆写入前评分和可能重写，保证最终写 timeline 的是最终回复；工具耗尽 fallback 同样走 helper。
6. 只重写一次：本轮不做多轮 critic；rewrite 结果再次本地评分并记录 metadata `rewrite_applied=true`，但不再递归重写。
7. 测试：新增 `tests/test_llm_client_rewrite.py`，覆盖默认 off 不多打一轮、开启阈值低分触发一次重写、取消 rewrite call 不写最终 metrics/不落 timeline。

风险评估：

- 默认行为风险：阈值负数直接返回原 reply，不调用 scorer/rewrite，测试锁定 call_api 只调用一次。
- 延迟/成本风险：开启后最多额外一次主模型调用；灰度-3 才设 `0.4`。
- OOC 风险：rewrite 指令只要求修表面拟人问题，保留事实和意图，不引入新 persona 设定。
- 状态污染风险：评分/metrics 在 reply 已成形后写；rewrite call 被取消时不进入最终 timeline 写入，metrics 记录保持 best-effort。

### V11 执行中拆分复核（执行者 GPT）

落地前把 V11 细拆为 6 个可审查点：

1. 默认关闭短路：`rewrite_threshold < 0` 时 `_maybe_rewrite_humanization_reply()` 直接返回原回复，不实例化第二次 LLM 请求，不写 RuntimeStateBus，不写 metrics 表。
2. 上下文读取：只读 `REGISTER_LABEL_SLOT` / `STICKER_RECENT_USED_SLOT` / `mood_getter`；scope 使用当前 `session_id/group_id/user_id/turn_id`，缺失时全部降级，不猜最近状态。
3. 初评分边界：低分判定前只做本地 scorer，不写 `LAST_METRICS_SLOT`；这样 rewrite LLM call 被取消时不会留下半成品指标。
4. 单次 rewrite：低于阈值时追加一次无工具 `LLMRequest(task="main")`，要求只改写上一条助手文本，保留事实/数字/意图/工具结果，不解释评分，不新增设定。
5. 最终指标：rewrite 成功或无需 rewrite 后才写最终 score；RuntimeStateBus 写 `LAST_METRICS_SLOT`，BlockTraceStore 写 `humanization_metrics`，两者均 fail-soft。
6. 接入点：普通无工具回复与 tool-loop exhausted fallback 都在 `_finalize_visible_reply()` 后、kaomoji enforcement/segmentation/timeline 写入前调用 helper；最终落库文本必须是 rewrite 后文本。

风险复核：

- 默认行为：新增 helper 必须被阈值负数硬短路，专项测试锁 call_api 只调用一次。
- 取消路径：取消 rewrite await 时不得进入 timeline/short_term/message_log/post_reply 写入；RuntimeStateBus 不应出现 final metrics。
- 成本边界：开启后最多多一次主模型调用，usage 聚合要把 rewrite round token 并入本次 chat 行。
- 语义边界：rewrite prompt 只能修表层语言，不允许依据 scorer issue 发明 persona 内容。

### V11 完成记录（执行者 GPT）

改动：

- `services/llm/client.py`：新增 `HumanizationRewriteResult` 与 humanization rewrite helper；`rewrite_threshold < 0` 时硬短路，默认不评分、不写状态、不多打一轮。
- `services/llm/client.py`：普通无工具回复与 tool-loop exhausted fallback 在 `_finalize_visible_reply()` 后、kaomoji enforcement/segmentation/timeline 写入前调用 rewrite helper；rewrite 成功后最终文本才进入分段、记忆和 post_reply。
- `services/llm/client.py`：开启阈值后用 `StylometricScorer` 先本地评分；低于阈值时追加一次无工具主模型 rewrite 请求；rewrite round token/耗时并入本次 usage 聚合。
- `services/llm/client.py`：最终回复确定后才 best-effort 写 `LAST_METRICS_SLOT` 和 V9 `humanization_metrics` 表；rewrite await 取消时不落 assistant、不写最终 metrics。
- `plugins/chat/plugin.py`：`LLMClient` 创建时传入 `config.humanization.rewrite_threshold`。
- `tests/test_llm_client_rewrite.py`：新增 3 条专项测试，覆盖默认 off 不多打一轮、低分触发一次 rewrite 并持久化 metrics、rewrite cancel-path 不脏写。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_llm_client_rewrite.py tests/test_humanization_scorer.py tests/test_humanization_metrics_persist.py tests/test_client.py tests/test_thinker_runtime_state.py` → 91 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/llm/client.py plugins/chat/plugin.py tests/test_llm_client_rewrite.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright services/llm/client.py tests/test_llm_client_rewrite.py` → 0 errors。

自审：

- D1：`rg "HumanizationRewriteResult|_maybe_rewrite_humanization_reply|humanization_rewrite_threshold|LAST_METRICS_SLOT|record_humanization_metrics|test_humanization_rewrite" services/llm/client.py plugins/chat/plugin.py tests/test_llm_client_rewrite.py` 命中仅 V11 helper、ChatPlugin 接线和专项测试。
- D2：`test_humanization_rewrite_cancel_path_does_not_write_assistant_or_metrics` 覆盖 rewrite LLM call 被取消时 short_term 仅保留 user、RuntimeStateBus 无 `LAST_METRICS_SLOT`。
- D3：默认 off 测试锁定 `call_api.await_count == 1`；未开启阈值时不引入 scorer/metrics/rewrite 热路径行为。
- 回滚：`git restore services/llm/client.py plugins/chat/plugin.py tests/test_llm_client_rewrite.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V11（真实回滚前需保留后续追踪记录）。

### U12 + V16 领单拆分（执行前）

目标：把 `reply_workflow.semantic_force_threshold` 从固定值扩展为可选动态阈值；默认由 `humanization.semantic_gate_dynamic=false` 关闭，关闭时完全保持现有固定阈值行为。开启后读取当前用户 familiarity 与当前群 mood energy，对 semantic gate 的消费阈值做小幅调整，并在现有 `semantic_gate` shadow log 中写入固定/动态阈值对比。

详细步骤：

1. 纯函数：在 `services/reply_workflow.py` 新增 `SemanticGateThreshold` 数据结构与 `semantic_gate_threshold()`；输入 fixed threshold、dynamic flag、familiarity、mood_energy，输出最终阈值和调整项。
2. 调整规则：仅 dynamic flag 开启时生效；`familiarity > 0.6` 时 `-0.1`，`mood_energy < 0.3` 时 `+0.05`，动态阈值 clamp 到 `[0.6, 0.85]`。
3. Router 读取：`kernel/router.py` 在 group semantic gate 路径中读取 `ctx.runtime_state` 的 `AFFECTION_FAMILIARITY_SLOT` per_user；mood energy 复用 `ctx.mood_engine.evaluate()` 当前上下文，缺失即 `None`。
4. Router 消费：`should_consume_semantic_gate()` 的两处 threshold 参数统一改为 effective threshold，避免 log 与实际消费阈值不一致。
5. Shadow log：`semantic_gate` log extra 增加 `fixed_threshold`、`effective_threshold`、`dynamic_enabled`、`familiarity`、`mood_energy`、`threshold_adjustments`，用于 V16 灰度比对。
6. 测试：新增 `tests/test_semantic_gate_dynamic.py` 覆盖 fixed fallback、familiarity 降阈值、low mood 升阈值、缺失 state 降级、现有 gate timeout/cancel failed-closed 保持。

风险评估：

- 默认行为风险：dynamic flag 默认 false；纯函数 off 时直接返回 fixed threshold，不读取/消费动态调整。
- 误触发风险：动态只在 `[0.6, 0.85]` 范围内小幅移动；低 mood 会提高阈值，避免状态低时抢话。
- 状态读取风险：RuntimeStateBus/mood_engine 读取全部 fail-soft；无 state 时回到 fixed threshold。
- 日志一致性风险：两处 `should_consume_semantic_gate()` 必须使用同一个 effective threshold，shadow log 同步写实际消费阈值。
- cancel-path 风险：U12/V16 不新增新的长 await；仍只有既有 `evaluate_semantic_gate()` LLM call，timeout/cancel failed-closed 回归测试锁定。

### U12 + V16 完成记录（执行者 GPT）

改动：

- `services/reply_workflow.py`：新增 `SemanticGateThreshold` 与 `semantic_gate_threshold()`，默认 dynamic off 时返回固定阈值；dynamic on 时按 familiarity 高降阈值、mood energy 低升阈值，并 clamp 到 `[0.6, 0.85]`。
- `kernel/router.py`：group semantic gate 路径新增 fail-soft 的 familiarity / mood energy 读取；两处 `should_consume_semantic_gate()` 统一使用同一个 effective threshold。
- `kernel/router.py`：`semantic_gate` shadow log extra 已写 `fixed_threshold`、`effective_threshold`、`dynamic_enabled`、`familiarity`、`mood_energy`、`threshold_adjustments`。
- `tests/test_semantic_gate_dynamic.py`：新增 7 条专项测试，覆盖默认 fixed fallback、动态降/升阈值、clamp、缺失状态降级、effective threshold 消费、timeout failed-closed。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_semantic_gate_dynamic.py tests/test_reply_workflow.py` → 27 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/reply_workflow.py kernel/router.py tests/test_semantic_gate_dynamic.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright services/reply_workflow.py tests/test_semantic_gate_dynamic.py` → 0 errors。
- `source ./scripts/dev/env.sh && uv run pyright kernel/router.py` → 仍有 33 个既有动态属性旧债，剩余项集中在 `PluginContext` 动态字段 / object helper 属性访问；本轮新增的 semantic gate 类型问题已清零。

自审：

- D1：`rg "SemanticGateThreshold|semantic_gate_threshold|semantic_effective_threshold|_semantic_gate_familiarity|_semantic_gate_mood_energy|threshold_adjustments|test_semantic_gate" services/reply_workflow.py kernel/router.py tests/test_semantic_gate_dynamic.py` 命中仅 U12/V16 纯函数、router 接线与专项测试。
- D2：`test_semantic_gate_timeout_still_fails_closed` 继续锁定 semantic gate LLM timeout 返回 `None`，不会强制触发回复；U12/V16 不新增新的长 await 或状态写入。
- D3：`humanization.semantic_gate_dynamic=false` 默认关闭；关闭时 effective threshold 等于 `reply_workflow.semantic_force_threshold`，默认行为保持固定阈值。
- 回滚：`git restore services/reply_workflow.py kernel/router.py tests/test_semantic_gate_dynamic.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 U12/V16（真实回滚前需保留后续追踪记录）。

### V17 领单拆分（执行前）

目标：在 `ChatPlugin.on_startup()` 完成 Part 1 humanization 生产装配闭环：保留现有 PromptProviderBus / LLMClient / runtime_state 接线，补上 V1 `RegisterClassifier` 的生产挂载点。默认由 `humanization.register_classifier=false` 关闭；开启后只在消息入口写 `state.register.label`，不消费消息、不改 trigger、不直接影响是否回复。

详细步骤：

1. 装配审查：确认 `bot.py` 已创建 `ctx.humanization_contract` / `ctx.runtime_state`；确认 ChatPlugin 已给 `LLMClient` 传 `runtime_state`、`humanization.rewrite_threshold`、`thinker_provider_enabled`，并已按 `context_providers` 注册 Register/Catchphrase/Sticker/Thinker Provider。
2. Classifier 实例：在 `ChatPlugin.on_startup()` 的 LLMClient 创建后，按 `config.humanization.register_classifier` 创建 `RegisterClassifier(llm)`，挂到 `ctx.humanization_register_classifier`；关闭时显式设为 `None`。
3. 消息入口：新增 `ChatPlugin.on_message()`；仅群聊、非 silent/off、flag 开、runtime_state 存在、classifier 存在、文本非空时执行；永远返回 `False`，不消费消息。
4. 窗口构造：从 `ctx.timeline.get_turns(group_id)` 取最近 4 条可读文本，加当前消息组成最多 5 条窗口；只传 `speaker/content_text` 给 classifier，不读取图片二进制。
5. 状态写入：scope 使用 `Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id)`，调用 `classify_and_write()`；失败 fail-soft 只 debug，CancelledError 不吞。
6. 测试：新增 `tests/test_chat_plugin_humanization_wire.py`，覆盖默认 off 不分类、开启后写 `REGISTER_LABEL_SLOT` 且不消费、取消路径不脏写；回归 register classifier / provider 测试。

风险评估：

- 默认行为风险：`register_classifier=false` 默认关闭；关闭时 `on_message()` 直接返回 `False`，不增加 LLM call。
- 热路径成本风险：开启后每条可发言群消息最多一次 thinker-profile LLM 分类；仅灰度开启，失败降级不阻断主回复。
- 消息消费风险：V17 的 `on_message()` 必须始终返回 `False`，不得影响 Bilibili/Echo/命令等后续拦截器。
- silent_learn 风险：ChatPlugin 不声明 `silent_safe`，silent 模式下不会运行；即使直接调用，也检查 `allow_speaking`。
- 状态污染风险：取消路径让 `CancelledError` 透出，`RegisterClassifier.classify_and_write()` 已覆盖取消前不写 bus；本轮新增 ChatPlugin 级测试复核。

### V17 完成记录（执行者 GPT）

改动：

- `kernel/types.py`：`PluginContext` 新增 `humanization_register_classifier` 字段，作为 ChatPlugin 与 V1 `RegisterClassifier` 的命名空间隔离挂载点。
- `plugins/chat/plugin.py`：新增 `_wire_humanization_runtime()`；`on_startup()` 创建 `LLMClient` 后按 `humanization.register_classifier` 挂载 `RegisterClassifier(llm)`，默认关闭时显式置 `None`。
- `plugins/chat/plugin.py`：新增 `on_message()` register 分类入口；仅群聊、可发言、flag 开、runtime_state/classifier 存在、文本非空时写 `REGISTER_LABEL_SLOT`，始终返回 `False` 不消费消息。
- `plugins/chat/plugin.py`：新增 `_register_classifier_window()`，从 timeline 最近 4 条 finalized turn + 当前消息构造最多 5 条窗口，只传文本给 classifier。
- `tests/test_chat_plugin_humanization_wire.py`：新增 5 条测试，覆盖 startup 装配、默认 off、开启写 state 且不消费、取消路径不脏写。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_chat_plugin_humanization_wire.py tests/test_register_classifier.py tests/test_register_provider.py` → 16 passed。
- `source ./scripts/dev/env.sh && uv run ruff check plugins/chat/plugin.py kernel/types.py tests/test_chat_plugin_humanization_wire.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright tests/test_chat_plugin_humanization_wire.py kernel/types.py` → 0 errors。
- `source ./scripts/dev/env.sh && uv run pyright plugins/chat/plugin.py kernel/types.py` → `plugins/chat/plugin.py` 仍有 52 个既有动态 `PluginContext` 字段错误；本轮新增的 override/测试类型问题已清零，未把 V17 扩大为 ChatPlugin 组合根类型重构。

自审：

- D1：`rg "_wire_humanization_runtime|humanization_register_classifier|_register_classifier_window|register_classifier_default_off|register_classifier_writes_state|register_classifier_cancel" plugins/chat/plugin.py kernel/types.py tests/test_chat_plugin_humanization_wire.py` 命中仅 V17 装配、上下文窗口和专项测试。
- D2：`test_chat_plugin_register_classifier_cancel_path_does_not_dirty_write` 覆盖 ChatPlugin 消息入口取消时 `REGISTER_LABEL_SLOT` 不写脏值；`CancelledError` 不被吞。
- D3：`test_chat_plugin_register_classifier_default_off_does_not_consume_or_write` 锁定默认 off 不调用 classifier、不消费消息、不写 RuntimeStateBus。
- 回滚：`git restore plugins/chat/plugin.py kernel/types.py tests/test_chat_plugin_humanization_wire.py docs/tracking/omubot-humanization-part1-execution.md` 可在 30 秒内撤销 V17（真实回滚前需保留后续追踪记录）。

### U13 领单拆分（执行前）

目标：只观测同一 turn 内 reply_workflow semantic gate 与 thinker 可能形成的双 haiku 调用，不合并、不改 prompt、不改回复行为；把 “同 turn 双 haiku” 作为 BlockTrace 侧的可查询 trace/observation，为 Part 2 判断是否值得合并 token 预算提供证据。

详细步骤：

1. 现状确认：定位 router 中 `evaluate_semantic_gate()` 调用点与 LLMClient/thinker 的 thinker 调用点，确认两者仍保持独立 await 与原 timeout。
2. Trace API：优先复用 `BlockTraceStore` 的轻量记录/观测面；若已有 observation API 可直接写入，不新增业务表。
3. Router 标记：semantic gate 实际调用前/后记录本 turn/session/group/user/event 的 `semantic_gate` haiku trace，失败也只记录观测，不影响 gate failed-closed 语义。
4. Thinker 标记：thinker 决策调用处记录 `thinker` haiku trace；不改 thinker prompt、不改决策、不改 RuntimeStateBus 写入时机。
5. 双调用识别：trace 记录应包含 `session_id/group_id/user_id/turn_id/task/source`，让同一 turn 的 semantic_gate + thinker 可在后续脚本/查询中聚合；本轮不做在线合并。
6. 测试：新增或扩展 `tests/test_block_trace.py`，验证同一 request/turn 的两条 haiku trace 能被记录/查询；必要时为纯 helper 写单测，避免构造完整 NoneBot 事件。

风险评估：

- 行为风险：U13 只写 trace，不改变 gate/thinker 调用顺序、timeout、prompt 或消费阈值。
- 存储风险：优先复用现有 block trace 表/API；新增字段必须向后兼容，旧 trace 查询不破坏。
- 热路径风险：记录失败必须 fail-soft；trace 写入不得阻断回复。
- 归因风险：没有稳定 turn_id 的路径必须降级为 session/event 粒度，不猜测跨 turn 合并。

### U13 完成记录（执行后）

实现结果：

1. 新增 `services/block_trace/llm_call_trace.py`，提供 `record_llm_call_trace()`，写入 `PromptBlockTrace(decision="shadow_only")`；metadata 固定包含 `session_id/group_id/user_id/turn_id/event_id/observer`，写失败只记录 debug，不影响主链路。
2. `kernel/router.py` 在 semantic gate 实际调用 `evaluate_semantic_gate()` 后记录 `provider="semantic_gate"` / `task="reply_gate"` trace；只观测真实 haiku 调用，不记录候选但未调用的情况。
3. directed_followup 被 semantic gate 消费时，把同一 `u13_double_haiku_request_id` 放入 `TriggerContext.extra`；`services/scheduler.py` 仅透传到 `ToolContext.extra`，不改变调度、force_reply、prompt 或 timeout。
4. `services/llm/client.py` 在 thinker 决策写 RuntimeStateBus 后，如检测到上游 U13 request id，则记录 `provider="thinker"` / `task="thinker"` trace；没有上游 id 时不写，避免把普通 thinker 误判为双调用。
5. `tests/test_block_trace.py` 新增同一 request 下 semantic_gate + thinker 两条 trace 查询测试，并新增 fail-soft 测试；同时把本文件 `_make_trace(decision=...)` 类型收窄为 `BudgetDecision`，消除新增 pyright 检查暴露的局部类型问题。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_block_trace.py` → 24 passed。
- `source ./scripts/dev/env.sh && uv run ruff check services/block_trace/llm_call_trace.py kernel/router.py services/scheduler.py services/llm/client.py tests/test_block_trace.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright services/block_trace/llm_call_trace.py tests/test_block_trace.py` → 0 errors。

自审结论：

- 行为面：U13 只增加观测 trace，不改 semantic gate/thinker 调用顺序、prompt、timeout、消费阈值或回复策略。
- 归因面：semantic gate trace 用 event 粒度，thinker trace 用 turn 粒度；两者通过同一 request id 聚合，metadata 保留 `correlation_key` 便于 Part 2 做窗口聚合。
- 风险面：trace helper fail-soft；唯一额外 await 是本地 SQLite trace 写入，发生在已完成 haiku 调用之后，且失败不影响回复。
- 回滚：`git restore services/block_trace/llm_call_trace.py kernel/router.py services/scheduler.py services/llm/client.py tests/test_block_trace.py docs/tracking/omubot-humanization-part1-execution.md` 可撤销 U13（真实回滚前需保留后续追踪记录）。

### Wave 7 共用前置审查（执行前）

目标：完成 V7 catchphrase 种子脚本，并按灰度-1 → 灰度-2 → 灰度-3 准备可控配置。执行前先确认三个事实：运行时默认读取 `config/config.json`；现有 `humanization.*` 是全局开关，若直接开会影响所有 active 群；当前 `storage/episodic.db` 中 `episodes total = 0`，所以 V7 真实执行不能伪造“从 EpisodeStore 抽入 30 条”。

详细步骤：

1. 灰度范围模型：在 `HumanizationConfig` 增加 `runtime_groups`，空列表表示跟随全局开关但默认仍 off；非空时只有 listed group 可消费 humanization classifier/provider/rewrite。
2. ChatPlugin 装配：新增轻量判断函数，约束 register classifier、catchphrase normalizer、humanization Provider 注册与 `LLMClient` rewrite threshold；灰度-1 只允许 `993065015` 生效，灰度-2 扩到 `993065015/984198159`。
3. Provider 范围：不修改每个 Provider 的业务逻辑，优先在 ChatPlugin/LLMClient 边界控制；若 ProviderBus 仍是全局实例，则补一个 group-aware wrapper 避免非灰度群执行 humanization Provider。
4. V7 脚本：新增 `scripts/dev/seed_catchphrase_pool.py`，从 `EpisodeStore.list_episodes()` 读取 approved/enabled/candidate episode，抽取短句候选，写入 `LearningNormalizerStore.attach_candidate(domain="catchphrase", profile="catchphrase", source_table="episode")`。
5. V7 执行：先 dry-run 统计候选数，再真实 seed；若 EpisodeStore 源样本不足 30，记录真实 seed 数和阻塞原因，不造假数据。
6. 灰度配置：更新 `config/config.json` 为灰度-1 安全态：`context_providers=true`、`register_classifier=true`、`runtime_groups=["993065015"]`、`rewrite_threshold=-1.0`；灰度-2/3 作为文档化命令/配置片段，不在未验收灰度-1 前直接放量。
7. 验证：配置加载测试覆盖 `runtime_groups`；ChatPlugin 测试覆盖非灰度群不写 register；脚本测试覆盖 dry-run、seed、source empty fail-safe；实际运行脚本并记录结果。

风险评估：

- 放量风险：没有 `runtime_groups` 时全局开 Provider 会影响多个 active 群；本轮先补范围阀，再改配置。
- 数据风险：当前 EpisodeStore 为空，V7 脚本必须如实返回不足样本，不能写合成口头禅到生产 normalizer。
- 成本风险：register classifier / rewrite 都可能新增 LLM call；灰度-1 开 classifier/provider，rewrite 继续 off。
- 回滚风险：配置灰度可通过恢复 `context_providers=false/register_classifier=false/runtime_groups=[]/rewrite_threshold=-1.0` 快速关闭；脚本幂等依赖 normalizer source 唯一索引。

### Wave 7.1 灰度阀补测拆分（执行前）

目标：把已经接入 `humanization.runtime_groups` 的灰度边界补成可验证契约，避免灰度-1 开关打开后非目标群也执行 register classifier 或 rewrite-loop。

详细步骤：

1. 配置复核：确认 `HumanizationConfig.runtime_groups` 已把 int / 带空格字符串规范化为非空字符串列表，空列表语义仍为“开关开启后不限制群”。
2. ChatPlugin 边界：补 `on_message()` 专项测试，构造 `register_classifier=true` 且 `runtime_groups=["200"]`，当前群 `100` 时必须返回 `False`、不调用 classifier、不写 `REGISTER_LABEL_SLOT`。
3. LLMClient 边界：补 `_maybe_rewrite_humanization_reply()` 入口级测试，构造 `rewrite_threshold=0.95` 且 `humanization_runtime_groups=["200"]`，当前群 `100` 时只调用一次主模型、不写 `LAST_METRICS_SLOT`、不写 `humanization_metrics`。
4. 允许路径回归：保留已有默认 off / 低分 rewrite / cancel-path 测试，确保新增灰度阀不破坏灰度群内原有 rewrite 行为。
5. 验证：运行配置、ChatPlugin、LLMClient rewrite 专项测试；运行相关 ruff；必要时只对新增测试与配置文件跑 pyright，避免扩大处理 ChatPlugin 既有动态字段旧债。

风险评估：

- 放量风险：若只在配置层加字段但热路径未测，灰度配置会变成心理安慰；本单元用非灰度群测试锁死 register/rewrite 两条新增成本路径。
- 兼容风险：空 `runtime_groups` 仍表示不限制群，不能让历史“开关全局开启”的语义被误改。
- 测试构造风险：ChatPlugin 测试必须直接观察 classifier 调用次数和 RuntimeStateBus，而不是只看返回值；LLMClient 测试必须观察 LLM 调用次数和 metrics 状态。

### Wave 7.1 灰度阀补测完成记录（执行者 GPT）

改动：

- `tests/test_chat_plugin_humanization_wire.py`：`_plugin_ctx()` 支持 `runtime_groups`，新增非灰度群测试；`register_classifier=true` 且 `runtime_groups=["200"]` 时，群 `100` 不调用 classifier、不写 `REGISTER_LABEL_SLOT`、不消费消息。
- `tests/test_llm_client_rewrite.py`：`_client()` 支持 `group_timeline` 与 `humanization_runtime_groups`，新增非灰度群 rewrite 测试；`rewrite_threshold=0.95` 且 `runtime_groups=["200"]` 时，群 `100` 只打一轮主模型、不写 `LAST_METRICS_SLOT`，最终原文进入群 timeline。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py` → 16 passed。
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py plugins/chat/plugin.py services/llm/client.py tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py tests/test_humanization_config.py` → 0 errors。

自审：

- D1：非灰度群 register/rewrite 两条新增 LLM 成本路径均被测试锁住；不是只检查函数返回值。
- D2：空 `runtime_groups` 语义仍由既有 rewrite 低分测试覆盖，灰度群内行为未被禁掉。
- D3：本单元只补测试和测试 helper，没有改变生产代码；生产灰度阀由前置审查中确认的 `HumanizationConfig` / ChatPlugin / LLMClient 接线承担。

### Wave 7.2 V7 seed 脚本拆分（执行前）

目标：新增一次性脚本 `scripts/dev/seed_catchphrase_pool.py`，从真实 `EpisodeStore` 抽取短口头禅候选并写入 `LearningNormalizerStore(domain="catchphrase")`；当前生产 `storage/episodic.db` 若为空，脚本必须如实输出 `selected=0 / written=0`，不能合成数据。

详细步骤：

1. API 对齐：使用 `EpisodeStore.list_episodes(state_filter=["enabled_for_prompt", "approved", "candidate"], limit=scan_limit)` 获取候选源；不读取 README 或假设表结构，字段以 `Episode` dataclass 为准。
2. 候选抽取：从 `situation`、`observed_context`、`action_taken`、`outcome_signal`、`reflection` 和 `meta` 文本值中拆短句；过滤 URL、CQ 码、换行残片、纯标点、过长/过短、明显总结性句子。
3. 去重策略：同一 raw text 只保留第一条来源；输出 `Candidate(raw_text, episode_id, group_id, source_field)`，便于 dry-run 审计。
4. 写入策略：非 dry-run 时调用 `LearningNormalizerStore.attach_candidate(domain="catchphrase", profile="catchphrase", scope="group", group_id=episode.group_id, source_table="episode", source_id=episode.episode_id, meta={...})`；无 `group_id` 的 episode 跳过，不写全局口头禅。
5. CLI：支持 `--dry-run`、`--limit`、`--scan-limit`、`--episode-db`、`--normalizer-db`；默认路径指向 `storage/episodic.db` 与 `storage/learning_normalizer.db`。
6. 测试：新增 `tests/test_seed_catchphrase_pool.py`，覆盖空 EpisodeStore 返回 0、dry-run 不写 normalizer、有 episode 时写入 catchphrase domain、同脚本重复执行依赖 normalizer 幂等 source 更新。
7. 真实执行：先 `--dry-run --limit 30`，再真实 `--limit 30`；记录生产库实际 selected/written 数。

风险评估：

- 数据真实性风险：当前 `storage/episodic.db` 可能为 0，V7 的正确结果可能就是 0 条；追踪文档必须记录不足样本，不能为了“完成 30 条”伪造。
- 语料污染风险：抽取规则偏保守，跳过长总结、URL/CQ、无 group_id；宁可少种，不把 episode 反思全文当口头禅。
- 幂等风险：Normalizer 已有唯一索引，但重复执行会增加 item count；脚本结果中需区分 selected/written，测试记录重复执行不会产生新 cluster。
- 热路径风险：脚本是离线 dev 工具，不接 runtime；失败只影响种子，不影响 bot。

### Wave 7.2 V7 seed 脚本完成记录（执行者 GPT）

改动：

- 新增 `scripts/dev/seed_catchphrase_pool.py`：从 `EpisodeStore.list_episodes(state_filter=["enabled_for_prompt", "approved", "candidate"])` 读取真实 episode，保守抽取 2~28 字短句，写入 `LearningNormalizerStore.attach_candidate(domain="catchphrase", profile="catchphrase", scope="group", source_table="episode")`。
- 脚本支持 `--dry-run`、`--limit`、`--scan-limit`、`--episode-db`、`--normalizer-db`、`--force`；非 dry-run 时复用 `scripts/dev/_bot_guard.py`，避免宿主直接写 Docker named volume。
- 新增 `_candidate_exists()` 预检查，避免重复执行同一 `(episode_id, raw_text)` 时触发 normalizer `count + 1`，保证 seed 脚本自身幂等。
- 新增 `tests/test_seed_catchphrase_pool.py`：覆盖空 EpisodeStore、dry-run 不写库、真实写入 catchphrase domain、无 group_id 跳过、重复执行不涨 item count。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_seed_catchphrase_pool.py` → 6 passed。
- `source ./scripts/dev/env.sh && uv run ruff check scripts/dev/seed_catchphrase_pool.py tests/test_seed_catchphrase_pool.py` → passed。
- `source ./scripts/dev/env.sh && uv run pyright scripts/dev/seed_catchphrase_pool.py tests/test_seed_catchphrase_pool.py` → 0 errors。
- 宿主 dry-run：`source ./scripts/dev/env.sh && uv run python scripts/dev/seed_catchphrase_pool.py --dry-run --limit 30` → `scanned_episodes=0 extracted=0 selected=0 written=0`。
- 宿主真实写入被 guard 拦截：当前 `storage` 是 Docker named volume `omubot-storage`，不能从宿主写 live DB。
- 容器 live volume 复核：`/app/storage/episodic.db` 中 `episodes_total=0`；`/app/storage/learning_normalizer.db` 中 `catchphrase_clusters=0`。
- 容器 dry-run：`.venv/bin/python /app/scripts/dev/seed_catchphrase_pool.py --dry-run --limit 30` → `scanned_episodes=0 extracted=0 selected=0 written=0`。
- 容器真实执行：`.venv/bin/python /app/scripts/dev/seed_catchphrase_pool.py --limit 30` → `scanned_episodes=0 extracted=0 selected=0 written=0`。

自审：

- D1：V7 没有伪造 30 条；当前真实 live EpisodeStore 为空，所以 seed 结果是 `0/30`，阻塞原因是源样本不足。
- D2：脚本只走 `LearningNormalizerStore.attach_candidate()`，没有新建平行 catchphrase 池，也没有直接拼 SQL 写业务数据。
- D3：抽取规则偏保守，过滤 URL/CQ/长总结/无 group_id；宁可不种，也不把 episode 反思全文污染成口头禅。
- D4：真实写入在 live 容器内验证为 no-op；宿主写 named volume 被 guard 拦截，符合当前存储治理约束。

### Wave 7.3 灰度-1 配置拆分（执行前）

目标：把 Part 1 humanization 从“全关”切到灰度-1安全态，只允许群 `993065015` 使用 register classifier 与 humanization context providers；rewrite、semantic dynamic gate、sticker/thinker provider 继续关闭。灰度-2/3 必须等待灰度-1 24h 指标，不提前放量。

详细步骤：

1. 配置边界：只修改 `config/config.json` 的 `humanization` 段；运行时主配置是 JSON，避免碰 LLM key、admins、persona_v2 等无关字段。
2. 兼容同步：同步修改 `config/config.toml` 的 `[humanization]` 段，保持 legacy 文件与 JSON 口径一致；仍明确 JSON 优先生效。
3. 灰度-1 值：设置 `context_providers=true`、`register_classifier=true`、`runtime_groups=["993065015"]`；保持 `sticker_register_provider=false`、`thinker_provider=false`、`rewrite_threshold=-1.0`、`semantic_gate_dynamic=false`。
4. 配置验证：运行 `tests/test_humanization_config.py` 与最小配置加载检查，确认新增 `runtime_groups` 可被 JSON/TOML 读取。
5. 灰度-2/3 记录：在文档中写明灰度-2 扩双群与灰度-3 rewrite=0.4 的配置片段，但状态为“等待灰度-1 24h 出口矩阵”，不写进 live config。
6. 部署提醒：当前 bot 容器仍是旧镜像/旧进程；配置和代码落地后需 rebuild/restart 才能在线生效，不能把“已在线”写成事实。

风险评估：

- 放量风险：`humanization.context_providers/register_classifier` 是全局开关；必须依赖 `runtime_groups` 限制群，否则会影响所有 active 群。
- 成本风险：灰度-1 会对目标群新增 register classifier LLM call；rewrite 继续关闭，避免每轮主回复多打一轮。
- 配置漂移风险：JSON/TOML 两份都改，但运行时以 JSON 为准；后续若通过 admin 保存，会以 JSON 主格式覆盖。
- 发布风险：代码尚未 rebuild 到运行容器，配置落库不等于已生效；验收必须把 restart/rebuild 作为后续操作。

### Wave 7.3 灰度-1 配置完成记录（执行者 GPT）

改动：

- `config/config.json`：`humanization` 切到灰度-1安全态：`context_providers=true`、`register_classifier=true`、`runtime_groups=["993065015"]`；`sticker_register_provider=false`、`thinker_provider=false`、`rewrite_threshold=-1.0`、`semantic_gate_dynamic=false` 保持关闭。
- `config/config.toml`：同步 `[humanization]` 段，作为 legacy 可读副本；运行时仍以 JSON 优先。
- 灰度-2 未放量：未把 `984198159` 加入 `humanization.runtime_groups`。
- 灰度-3 未放量：未开启 `rewrite_threshold=0.4`。

验证：

- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py tests/test_seed_catchphrase_pool.py` → 22 passed。
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py plugins/chat/plugin.py services/llm/client.py scripts/dev/seed_catchphrase_pool.py tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py tests/test_seed_catchphrase_pool.py` → passed。
- `source ./scripts/dev/env.sh && uv run python - <<'PY' ... load_config('config/config.json') / load_config('config/config.toml') ... PY` → JSON/TOML 均读到 `context_providers=True`、`register_classifier=True`、`runtime_groups=['993065015']`、`rewrite_threshold=-1.0`。

自审：

- D1：灰度-1 只打开目标群 register classifier + context providers；非灰度群成本路径已由 Wave 7.1 测试锁住。
- D2：rewrite-loop 继续关闭，避免未观测阶段引入第二轮主模型成本和文本改写风险。
- D3：当前运行容器仍是旧镜像/旧进程；本记录只声明配置已落库，不声明线上已生效。上线需 `docker compose up -d --build bot` 或等价 rebuild/restart。
- 灰度-2/3 出口：等待灰度-1 连续 24h 指标矩阵；未满窗口前不推进。

### Wave 8 / V12 文档与度量收口拆分（执行前）

目标：完成 Part 1 的可交接收口材料：补 `scripts/dev/measure_humanization.sh`，同步执行文档状态表、主方案状态表、maintenance-log，并给灰度-1/2/3 留出真实出口指标位置。不能把未满 24h 的灰度结果伪装成通过。

详细步骤：

1. 度量脚本：新增 `scripts/dev/measure_humanization.sh`，只读 `storage/block_trace.db` / `storage/learning_normalizer.db` / `storage/episodic.db`，输出 humanization metrics、catchphrase normalizer、U13 double-haiku trace、灰度出口表占位。
2. 脚本容错：SQLite 文件或表不存在时输出 `0 / missing`，不失败；便于新环境、容器 named volume 导出副本和本地宿主 stale DB 都能跑。
3. 文档状态：把执行文档 §6 的 V7、灰度-1、V12 标记为已完成/待验收；灰度-2/3 标为阻塞于 24h 出口指标，不伪造 ✅。
4. 主方案同步：更新 `omubot-humanization-part1-language-feel.md` §10/§11，说明实际施工已迁移到派单文档且 Wave 0-7 已落地，V12 已补度量入口；7 天/用户主观验收仍未完成。
5. migration §12：当前 `docs/migrations/persona-v2-importer.md` §12 没有 Part 1 humanization 行；本轮新增第 7 行记录“Part 1 humanization runtime 编排 + 灰度-1”，状态标为已实现但运行时验证待 24h。
6. maintenance-log：追加 2026-05-25 Humanization Part 1 Wave 7/8 收口条目，记录配置、seed 真实 0/30、度量脚本、灰度阻塞条件和部署提醒。
7. 验证：跑度量脚本、相关 pytest/ruff/pyright、配置加载检查；最后做 `git diff --check`。

风险评估：

- 口径风险：V12 是工程收口，不是灰度通过；文档必须区分“代码/配置/脚本已落地”和“24h 指标/用户验收未完成”。
- 数据风险：当前 live EpisodeStore 为 0，measure 脚本不能给出假 catchphrase 命中率；只能输出当前样本不足。
- 运维风险：Docker named volume 下宿主 `storage/*.db` 可能是 stale 副本；脚本只读，文档注明 live 指标建议在容器内或导出副本跑。
- 回滚风险：灰度-1 可通过改 `config/config.json humanization.context_providers=false/register_classifier=false/runtime_groups=[]` 并重启快速关闭。

### Wave 8 / V12 完成记录（执行者 GPT）

改动：

- 新增 `scripts/dev/measure_humanization.sh`：只读 `storage/block_trace.db`、`storage/learning_normalizer.db`、`storage/episodic.db`，输出 `humanization_metrics`、catchphrase normalizer、episode source、U13 double-haiku、rollout gate；表缺失或样本为 0 时输出 `missing_table` / `0`，不失败。
- 本文 §4 灰度指标矩阵已填入当前真实状态：灰度-1 等待 24h 样本，当前 metrics 表缺失、catchphrase=0、episode=0；灰度-2/3 阻塞。
- 本文 §6 状态表已更新：V7 / V12 标 ✅，灰度-1 标 🟡 待上线观察，灰度-2/3 标 ⏸ 阻塞。
- `docs/tracking/omubot-humanization-part1-language-feel.md` §10/§11 已从旧“待执行计划”同步为“派单文档为准，工程项收口到灰度-1准备态，灰度未通过”。
- `docs/migrations/persona-v2-importer.md` §12 新增第 7 行 “Part 1 humanization runtime 编排 + 灰度-1”，状态为 ✅ 工程收口，运行时验证待 24h。
- `maintenance-log.md` 追加置顶条目，记录 Wave 7/8 收口、V7 seed 真实 0/30、灰度-1配置、V12度量入口、灰度-2/3阻塞条件和回滚口径。

验证：

- `scripts/dev/measure_humanization.sh` → 输出 `humanization_metrics status=missing_table`、`catchphrase_normalizer cluster_count=0`、`episode_source total=0`、`u13_double_haiku paired_requests=0`、`gray_1_24h_window=pending`。
- `bash -n scripts/dev/measure_humanization.sh` → passed。
- `git diff --check -- scripts/dev/measure_humanization.sh scripts/dev/seed_catchphrase_pool.py tests/test_seed_catchphrase_pool.py tests/test_chat_plugin_humanization_wire.py tests/test_llm_client_rewrite.py config/config.json config/config.toml docs/tracking/omubot-humanization-part1-execution.md` → passed。

自审：

- D1：V12 文档明确区分“工程收口”与“灰度通过”；没有把 24h 指标、7 天指标或用户主观验收伪造为完成。
- D2：度量脚本是只读脚本；不会在 Docker named volume / 宿主 stale DB 场景写入或迁移任何 SQLite。
- D3：灰度-2/3 未推进；当前配置只允许 `993065015` 单群灰度-1，rewrite 仍关闭。
- D4：当前运行容器未 rebuild/restart，本轮只声明代码与配置落库；线上生效需后续部署窗口。
