# Omubot 拟人 Part 6 — 派单版并列执行追踪

> 状态：2026-05-26 立。本文是 [Part 6 主线 v2.3](./omubot-humanization-part6-source-side-generation.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> 启动时机：**Part 2-3 派单全部完成后启动 Part 6**（用户口径："part2-3 已在执行，尽量与 part6 同批次" + "我会在 part2-3 结束后执行"）。Part 6 与 Part 2-3 在调度器层正交（Part 6 仅在已经决定回复后影响生成形态 + 增 trigger 信号源），但 P6.0.y1 入站事件源依赖 Part 2-3 既有 group_timeline 调度器稳定，故必须等 Part 2-3 出口验收过线再起。
>
> **执行原则**（以下规则覆盖任何主线文档的不一致表述）：
>
> 1. **每条独立 commit**——除非本文明确写"合 commit"。
> 2. **同 wave 内任务可并行**——不同 wave 间严格串行。
> 3. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒回滚开关**，缺一不通过验收。
> 4. **遇主线证据与本文冲突，以本文为准**（§1 已记录主线 4 处证据订正）。
> 5. **配置改动一律落 [config/config.json](../../config/config.json)**（运行时源；TOML 是过期文档，详见 [maintenance-log 2026-05-26 B3 灰度切流静默失效](../../maintenance-log.md)）。

---

## 1. 主线自审与证据订正（执行前必读）

下表是我对 Part 6 主线 v2.3 §1 / §6 / §7 进行 grep 实证后发现的与原文不符的项。**派单时按本表订正，不按主线原文**。

| 主线位置 | 主线原文 | grep 实证 | 派单订正 |
|---|---|---|---|
| §6.4 / §7.0.x3 | `services/llm/client.py:359 _reply_segments` | 实际入口在 [services/llm/client.py:397](../../services/llm/client.py#L397) `def _reply_segments(...)`；行 49 已 import `reply_segments as _segment_reply_segments`，主路径委托给 [services/llm/segmentation.py](../../services/llm/segmentation.py) | **订正 file:line 锚点**——后续派单中 client.py 入口锚点统一为 397 行，segmentation 实际工作位于 `segmentation.py` |
| §7.0.x3 / §7.0.y2 | `services/llm/prompt_builder.py:138-180 build_blocks()` | 实际入口在 [services/llm/prompt_builder.py:139](../../services/llm/prompt_builder.py#L139) `async def build_blocks(...)`；layout 注释在第 157 行 `[static, *plugin_static, state_board, *plugin_stable, *plugin_dynamic]` | **订正锚点 + 锚定真实 layout**——P6.0.a layout 改动针对 `build_blocks()` 内 178-179 行的 `blocks.append(state_board_block)` 顺序，而不是把整个 `[static, group_context, ...]` 重写 |
| §7.1 P6.1 | 新建 `services/segmentation/streaming_segmenter.py` ≤ 200 行 | 仓内已有 [services/llm/segmentation.py](../../services/llm/segmentation.py)（natural_split 主实现）；不存在 `services/segmentation/` 目录 | **订正路径**——新增文件统一挂在 `services/llm/streaming_segmenter.py`，与既有 `segmentation.py` 同目录共生 |
| §1.5 / §7.0.x4 | `storage/usage.db` 列 `prompt_cache_hit_tokens / prompt_cache_miss_tokens` 来自 LLM 响应 | grep 实证 [services/llm/client.py:489](../../services/llm/client.py#L489) 同时 fallback 读 `cache_read` / `cache_create_tokens`；DeepSeek 路径下 `cache_create_tokens` 结构性恒为 0 但 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 真有值 | **订正 P6.0.x4 守卫数据源**——hit% 计算用 `prompt_cache_hit_tokens / (prompt_cache_hit_tokens + prompt_cache_miss_tokens)`，避免误读已 dead 的 `cache_create_tokens` |
| §7.0.x1 | `HumanizationConfig` 新增 `profile` 字段 | [kernel/config.py:1024](../../kernel/config.py#L1024) `HumanizationConfig` 现有 9 字段（context_providers / register_classifier / sticker_register_provider / thinker_provider / rewrite_threshold / semantic_gate_dynamic / kaomoji_enforce_strict / runtime_groups），无 `profile` 字段 | **派单订正 — 新增字段位置**：插在 `runtime_groups` 之后；保留所有既有 9 字段不动；`@field_validator("runtime_groups", mode="before")` 保留 |
| **P6.-1 已通过 — 2026-05-26 03:11 CST**（Claude） | 派单前置验证 4 步 | 步骤 1 OK：`runtime_groups=['993065015']`，`group.overrides` 已含 `993065015 / 984198159`；步骤 2 OK：Part 2-3 §6 状态表 Wave 1-5 全 ✅，Wave 6 灰度 P2.7+P3.5+v2-灰度 已转 🟡（用户口径"忽略灰度，单独验收，暂时剃除该 gate"，见本会话 2026-05-26 03:10 CST 指令）；步骤 3 OK：实测 `usage.db` 表名是 `llm_calls`（不是主线写的 `usage`），列 `prompt_cache_hit_tokens / prompt_cache_miss_tokens` 存在 | Wave 0 派单解锁；**追加订正：P6.0.x4 守卫 SQL 必须查 `llm_calls` 表（不是 `usage`）** |

> **派单规则**：执行者拿到本文档后，按 §1 订正版执行；如再次发现主线 ↔ 现状不符，停止派单同步报我。

---

## 2. P6.-1 新增前置任务（runtime config 源 + Part 2-3 出口验证）

派单第 0 步，零代码改动。

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `docker compose exec bot /app/.venv/bin/python -c "from kernel.config import load_config; c = load_config(); print('humanization=', c.humanization); print('groups=', c.group.overrides.keys())"` | 确认运行时 config 源是 `config.json`，runtime_groups 与 group.overrides 已落 `["993065015","984198159"]` |
| 2 | `grep -c "P2\." docs/tracking/omubot-humanization-part2-3-execution.md \| awk '{print "Part 2-3 子任务行数="$1}'` + 看 §6 状态表是否全 ✅ | Part 2-3 出口齐全才允许 Part 6 启动；如有 ⏳ 项停 |
| 3 | `docker compose exec bot /app/.venv/bin/python -c "import sqlite3; con = sqlite3.connect('/app/storage/usage.db'); print(list(con.execute('PRAGMA table_info(usage)')))"` | 确认 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 列存在；P6.0.x4 健康守卫的数据源 |
| 4 | 写 1 行结论到本文 §1 表第 5 行后（追加"P6.-1 已通过"） | 给 Wave 1 派单解锁 |

**P6.-1 不是 commit；是派单前置验证**。我会先看本步骤回执再发后续单。

---

## 3. 并列执行 Wave 表（按依赖图编排）

**依赖关系核心规则**：

- **Wave -1**：P6.-1 前置验证（零代码）
- **Wave 0**：state_board prefix 稳定化（P6.0.a / P6.0.b 并列；P6.0.c 灰度复盘需等 7 日）
- **Wave 1**：profile 切换基础设施（P6.0.x1 → x2 / x3 / x5 三条并列；x4 等 P6.0.c）
- **Wave 2**：QQ 特殊交互能力（P6.0.y1 → y2 / y3 / y4 三条并列）
- **Wave 3**：B 方案 streaming-as-segment（P6.1 → P6.2 → P6.3 灰度 → P6.4 默认开）
- **Wave 4**：D 方案 pause-then-extend（P6.5 → P6.6 → P6.7 灰度 → P6.8 默认开）
- **Wave 5**：A 方案 plan-then-utter pilot（P6.9 → P6.10 → P6.11 决策门）
- **Wave 6**：C 方案锁定 + 文档收口（P6.12 / P6.13）

### 3.1 Wave 0 — state_board prefix 稳定化（P6.0 三段）

> 主线 §7.0；先做 a/b（代码改动）落地后并行灰度 7 日，再做 c（验收 + 默认开）。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.0.a** | state_board 后置：layout 改为 `[static, *plugin_static, *plugin_stable, *plugin_dynamic, state_board]`；feature flag `humanization.state_board.layout=tail`（默认 `head`） | [services/llm/prompt_builder.py:178-179](../../services/llm/prompt_builder.py#L178-L179) `blocks.append(state_board_block)` 顺序调整 + `kernel/config.py:1024` 加嵌套 `state_board: StateBoardConfig` | grep `state_board.layout` 仅命中 schema + tests + prompt_builder.py 单点 | N/A（layout 改动；prompt_builder 已是 idempotent） | feature flag `state_board.layout=head` + restart |
| **P6.0.b** | state_board 字段粒度抬升：`_derive_mentions` 时间字段降级为粗粒度（"刚刚 / 今天早些 / 昨天 / 更早"）；`_derive_frequency` 去 N 分钟具体计数；`_derive_topics` 加 sticky 锚点；feature flag `humanization.state_board.granularity=coarse`（默认 `fine`） | [services/memory/state_board.py](../../services/memory/state_board.py) ~ +60 / -20 行 | grep `_derive_mentions\|_derive_frequency\|_derive_topics` 仅命中 state_board.py 与 tests；fake clock 跨 10 min 渲染 byte-identical | N/A（state_board 是 read-side query 产出；无脏写） | feature flag `state_board.granularity=fine` + restart |
| **P6.0.c** | 7 日生产复盘 + 默认开 + D+E 长尾收口：`scripts/dev/measure_cache_hit_proactive.sh` + 验收阈值 proactive ≥ 85% / chat ≥ 80% / 单日波动 ≤ 15pp | scripts + maintenance-log；P6.0.a / P6.0.b 默认值翻 `tail` + `coarse` | DB 查询脚本输出回执 | N/A | feature flag 双 off + restart |

**Wave 0 commit**：P6.0.a / P6.0.b 各 1 commit；P6.0.c = 灰度报告 + 默认值切换 1 commit。

### 3.2 Wave 1 — profile 切换基础设施（P6.0.x1~x5）

> 主线 §7.0.x；x1 是单点突破（仅 BaseModel 改动），x2 / x3 / x5 三条并列；x4 等 P6.0.c。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.0.x1** | `HumanizationConfig.profile` enum 字段 + 4 个嵌套子 BaseModel（`state_board / streaming_segment / pause_then_extend / plan_then_utter`，全默认 off）+ `resolve_profile(profile_value, group_id)` → `ResolvedHumanization` dataclass（6 决议字段含 `disable_natural_split`） | [kernel/config.py:1024](../../kernel/config.py#L1024) `HumanizationConfig`（≈ +120 行）+ 新增 `ResolvedHumanization` dataclass | grep `class HumanizationConfig\|profile: Literal\|class ResolvedHumanization` 仅命中 config.py + tests | N/A（pure data）；`resolve_profile()` 必须无副作用 | git revert |
| **P6.0.x2** | `GroupOverride.humanization_profile: Literal[...] \| None = None`；`GroupConfig.resolve()` 输出的 `ResolvedGroupConfig` 增 `humanization_profile` 字段 | [kernel/config.py:343](../../kernel/config.py#L343) `GroupOverride`（+5 行）+ [kernel/config.py:471](../../kernel/config.py#L471) `GroupConfig.resolve()` 透传 | grep `humanization_profile` 仅命中 config.py + tests | N/A | git revert |
| **P6.0.x3** | 消费者改读 `resolve_profile()`：(1) [services/llm/prompt_builder.py:139](../../services/llm/prompt_builder.py#L139) `build_blocks()` 入口 resolve layout 决议；(2) [services/memory/state_board.py:71-79](../../services/memory/state_board.py#L71-L79) `to_prompt_text()` 接受 granularity 入参；(3) [services/llm/client.py:397](../../services/llm/client.py#L397) `_reply_segments` 入口 resolve（含 disable_natural_split 互斥）；(4) [plugins/chat/plugin.py:94-114](../../plugins/chat/plugin.py#L94-L114) 新增 `_humanization_resolve(config, group_id)` helper | 4 个文件，合计 ≈ +60 行 | grep `_humanization_resolve\|resolve_profile\|ResolvedHumanization` 命中点全部读取无写入 | reply 进行中 resolve 已冻结，不漂；test 单条覆盖 | feature flag `profile=custom` 退化为 v2.1 前 flag-by-flag |
| **P6.0.x4** | 健康守卫 `services/humanization/health_guard.py` ≤ 100 行：周期 60s 查 [storage/usage.db](../../storage/usage.db) 最近 1h 按 group_id 的 hit%；performance 群 < 80% 写入 in-mem `_degraded_groups`；`resolve_profile()` 在 performance 路径上检查降级；hit% ≥ 85% 持续 10min 自动解除 | 新文件 ≤ 100 行 + `resolve_profile()` 加 1 行降级查询 | grep `_degraded_groups\|HealthGuard` 仅命中 health_guard.py + tests + resolve_profile | 守卫读不到 DB 兜底（默认不降级，避免误伤）；定时器 cancel 不脏写 in-mem dict | health_guard scheduler 关闭 + `_degraded_groups.clear()` |
| **P6.0.x5** | Admin SPA 自动渲染 + dashboard chip：(1) [admin/routes/api/config.py:380](../../admin/routes/api/config.py#L380) `_build_schema()` 自动渲染 enum（已有管线）；(2) [admin/frontend/src/views/](../../admin/frontend/src/views/) 系统配置页拟人化区块加 profile 下拉 + 子配置只读卡片；(3) 群组管理页：群级 humanization_profile 下拉 + "继承全局"；(4) dashboard 显示当前生效档位 chip + 健康守卫降级红点 | admin/frontend ~ +200 行 + admin route 0 行（自动）| grep `humanization_profile` 命中前端 store + 视图组件 | N/A（前端只读 API） | git revert + `npm run build` |

**Wave 1 commit**：x1 / x2 / x3 / x4 / x5 各自独立 commit；建议顺序 x1 → x2 + x3 + x5 并行 → x4 等 P6.0.c 完成后单独 commit。

### 3.3 Wave 2 — QQ 特殊交互能力（P6.0.y1~y4）

> 主线 §7.0.y；y1 入站事件源接 Part 2-3 既有 group_timeline 调度器（**不改 Part 2-3 调度仲裁逻辑**）；y2 / y3 / y4 三条并列。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.0.y1** | 入站事件源：[kernel/router.py:1137-1149](../../kernel/router.py#L1137-L1149) 同级新增 `on_notice` 处理 OneBot v11 [`PokeNotifyEvent`](../../.venv/lib/python3.13/site-packages/nonebot/adapters/onebot/v11/event.py)；NapCat 表情回应走 raw event hook；事件解析为统一 trigger signal `{kind, actor_user_id, target_user_id, raw_message_id?, emoji_code?, is_tome}` 投递 Part 2-3 既有 group_timeline；入站频率护栏（单 user 60s ≥ 5 次 poke → 60s mute） | [kernel/router.py](../../kernel/router.py) ~ +80 行 | grep `PokeNotifyEvent\|MessageReactionEvent\|on_notice` 命中点仅在新 handler 与既有 GroupBan handler | NoneBot Bot 取消时 handler 必须 idempotent 退出；mute dict cancel 不脏写 | feature flag `humanization.qq_interactions.poke_inbound_response_enabled=false` + restart |
| **P6.0.y2** | 出站工具 `services/tools/interaction_tools.py` ≤ 120 行：`poke_user(user_id, group_id?)` → NapCat `send_poke` action；`react_to_message(message_id, emoji_code)` → NapCat `set_msg_emoji_like` action；in-mem token bucket 速率门（单群 60s `poke_out` ≤ 2 / `react_out` ≤ 3；单 user 5min `poke_out` ≤ 1）；profile 守卫 economy 全关 / balanced 仅被动响应 / performance 全开 | 新文件 + [services/tools/registry.py](../../services/tools/registry.py) 注册 | grep `poke_user\|react_to_message\|interaction_tools` 仅命中新文件 + tests | tool call 取消时 token bucket 不双计 | feature flag 关闭对应 outbound 字段 + 工具不注册 |
| **P6.0.y3** | 引用回复：模型在文本里输出 `<quote msg_id="..."/>` 锚点 → [services/llm/client.py:397](../../services/llm/client.py#L397) `_reply_segments` 出站组装时转 `MessageSegment.reply(msg_id) + 文本`；profile 守卫：economy 关；msg_id 不存在 / 解析失败静默 strip 锚点回退纯文本 | [services/llm/client.py](../../services/llm/client.py) + 新增 `_extract_quote_anchor()` helper ≈ +30 行 | grep `<quote msg_id\|MessageSegment\.reply` 仅命中 client.py + tests | reply 取消中锚点已 strip，不残留半状态 | feature flag `humanization.qq_interactions.quote_reply_enabled=false` |
| **P6.0.y4** | `HumanizationConfig.qq_interactions: QQInteractionsConfig` 子 BaseModel（5 bool flag 默认全 off）+ `resolve_profile()` 输出新增 5 字段；[GroupOverride](../../kernel/config.py#L343) 新增 `qq_interactions_profile_override: bool \| None = None`（与 humanization_profile 联动） | [kernel/config.py:1024](../../kernel/config.py#L1024) ≈ +60 行 | grep `qq_interactions\|QQInteractionsConfig` 仅命中 config.py + tests | N/A（pure data） | git revert |

**Wave 2 commit**：y1 / y2 / y3 / y4 各自独立 commit；y1 必须先落地（事件源是 y2/y3 行为依赖）。

### 3.4 Wave 3 — B 方案 streaming-as-segment（P6.1~P6.4）

> 主线 §7.1；profile=balanced 解锁条件之一。**B 与 Part 5 natural_split 互斥**——`resolve_profile()` 在 B 启用时输出 `disable_natural_split=True`，由消费者读取。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.1** | streaming-segmenter 算法：新建 `services/llm/streaming_segmenter.py` ≤ 200 行（订正路径，主线写错为 `services/segmentation/`）；online 在 SSE token-stream 上检测 sentence boundary 并 flush；接受 register / mood 入参（与 Part 5 `natural_split` 行为可比对） | 新文件 ≤ 200 行 | grep `class StreamingSegmenter\|streaming_segmenter` 仅命中新文件 + tests | SSE stream 取消时 buffer 必须 drain（否则下条 reply 串戏） | git rm |
| **P6.2** | streaming hook 接入 [services/llm/client.py:397](../../services/llm/client.py#L397) `_reply_segments` SSE 主路径：新增 `_stream_with_segments()` 方法（feature flag `humanization.streaming_segment.enabled` 默认 off）；与 Part 5 `_reply_segments` 互斥（按 `disable_natural_split` 分流） | [services/llm/client.py](../../services/llm/client.py) ~ +80 行 | grep `_stream_with_segments\|streaming_segment` 仅命中 client.py + tests | abort 时 streaming buffer + token bucket 同时清 | feature flag off + restart |
| **P6.3** | B 方案灰度 + 200 条 group reply 体感比对：`scripts/dev/measure_streaming_vs_natural.sh` 采样灰度群 200 条 reply，比对 `streaming_segmenter` vs `natural_split` 段长方差 / 段间延迟 / 用户感知 | 新脚本 + 灰度报告 | 灰度回执 | N/A | profile=economy 30 秒回滚 |
| **P6.4** | B 方案默认开 + 卸 fallback：删除被 streaming 替代的 `_reply_segments` 部分回退路径；保留 `natural_split` 作为非 streaming profile 的兜底 | client.py 净删 ~ -150 行 | 全量 pytest 回归 | 同 P6.2 | profile=economy + restart |

**Wave 3 commit**：P6.1 / P6.2 各自 1 commit；P6.3 = 灰度报告 commit；P6.4 = 默认开 + 净删 1 commit。

### 3.5 Wave 4 — D 方案 pause-then-extend（P6.5~P6.8）

> 主线 §7.1；profile=balanced 解锁另一半。**D 与 Part 5 完全正交**（D 控制段间，Part 5 控制段内）。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.5** | pause-then-extend 决策器：新建 `services/humanization/pause_extend.py` ≤ 150 行；输入 (last_reply, register, slot, group_state)；输出 (should_extend: bool, wait_seconds: float) | 新文件 ≤ 150 行 | grep `class PauseExtend\|should_extend` 仅命中新文件 + tests | N/A（pure decision，无 IO） | git rm |
| **P6.6** | extend call 接入 LLMClient（追发循环）：[services/llm/client.py:397](../../services/llm/client.py#L397) 增 `_maybe_extend()` 方法（feature flag `humanization.pause_then_extend.enabled` 默认 off）；max_extend_count=2 上限；observe `extend_rate` 写 BlockTrace | [services/llm/client.py](../../services/llm/client.py) ~ +60 行 | grep `_maybe_extend\|pause_then_extend` 仅命中 client.py + tests | extend 等待期间 cancel 必须 drop 追发；BlockTrace 写"cancelled" | feature flag off + restart |
| **P6.7** | D 方案灰度 + extend_rate / 体感比对：`scripts/dev/measure_extend_rate.sh` 采样 200 条 + 用户主观验收 | 新脚本 + 灰度报告 | 灰度回执 | N/A | profile=economy |
| **P6.8** | D 方案默认开：feature flag 默认 on；extend_rate 阈值守卫 ≤ 25% | feature flag 切换 + 全量 pytest 回归 | grep 仅命中 schema 默认值 | 同 P6.6 | profile=economy + restart |

**Wave 4 commit**：P6.5 / P6.6 / P6.7 / P6.8 各自 1 commit。

### 3.6 Wave 5 — A 方案 plan-then-utter pilot（P6.9~P6.11）

> 主线 §7.1；profile=performance 解锁条件。**A 与 Part 5 / B 互斥**——`resolve_profile()` 输出 `disable_natural_split=True` 且优先级高于 B。**仅 proactive 路径开启**；solicited（被 @ / 直接对话）路径继续走 B/D。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P6.9** | A 方案 pilot：新建 `services/llm/plan_then_utter.py` ≤ 250 行；feature flag `humanization.plan_then_utter.enabled` 默认 off + group whitelist 仅灰度群；plan call max_tokens=80 / utter call N=2~3 each max_tokens=150；BlockTrace 写 `proactive_plan` / `proactive_utter` 关系（parent_span_id） | 新文件 + [services/llm/client.py](../../services/llm/client.py) 集成 ~ +60 行 | grep `class PlanThenUtter\|proactive_plan\|proactive_utter` 仅命中新文件 + tests + client.py | plan call 取消 → drop 所有 utter；BlockTrace 写"cancelled" | feature flag off + restart |
| **P6.10** | A 方案 14 日 pilot：cost / latency / persona drift 监控；grafana / log 面板：`proactive_plan` vs `proactive_utter` cost；PersonaScore 5 任务 baseline | 灰度面板 + 14 日数据采集 | DB 查询脚本 | N/A | feature flag off |
| **P6.11** | A 方案决策门：上 / 不上 / 调参后再 pilot；决策报告 + 主线 §10 状态推进；如成本 > 2.5× baseline 即 pilot 失败永久关 | 决策报告 + 主线文档 update | 报告回执 | N/A | profile=balanced |

**Wave 5 commit**：P6.9 / P6.10 / P6.11 各自 1 commit。

### 3.7 Wave 6 — C 方案锁定 + 文档收口（P6.12 / P6.13）

| 编号 | 一句话 | 改动 |
|---|---|---|
| **P6.12** | C 方案 暂搁 — 不开发：在主线 §5 / §10 明确锁定结论"DeepSeek 不支持 stream continuation + abort drift + 因果链复杂度 → C 方案永不进灰度" | 主线文档 update（仅文字） |
| **P6.13** | 文档收口：maintenance-log 当日条目 + 主线 §10 状态表全 ✅ + 本文 §6 状态表全 ✅ + `scripts/dev/measure_humanization_part6.sh` 收尾 | 文档 + 1 个 measure 脚本 |

---

## 4. 灰度 24h / 7 日出口指标矩阵

执行者每阶段灰度结束跑一次对应 `scripts/dev/measure_*.sh`，把下表填进结果。我看到 ≥ 11/15 项达标才放下一阶段。

| 指标 | 目标 | P6.0 economy 实测（7 日） | balanced 实测（24h） | performance 实测（14 日 pilot） |
|---|---|---|---|---|
| proactive cache hit% | ≥ 85% | 等待 P6.0.a/b 部署后 7 日样本 | 阻塞：economy 未验收 | 阻塞 |
| chat cache hit% | ≥ 80% | 同上 | 阻塞 | 阻塞 |
| proactive avg miss tokens | ≤ 1500 | 同上 | 阻塞 | 阻塞 |
| 单日 hit% 波动 | ≤ 15pp | 同上 | 阻塞 | 阻塞 |
| state_board byte-identical 率（fake clock 跨 10min） | ≥ 95% | 单测覆盖 + 灰度抽样 | 阻塞 | 阻塞 |
| streaming 段长方差 | ≥ 50 | N/A | balanced 才有 | 阻塞 |
| streaming 段间延迟 p50 | 1.5~3.5s | N/A | balanced 才有 | 阻塞 |
| extend_rate | ≤ 25% | N/A | balanced 才有 | 阻塞 |
| 单 reply 段数 | ≤ 3 | N/A | balanced 才有 | 阻塞 |
| 戳一戳响应延迟 p50 | < 8s | N/A（economy 关） | balanced 才有 | 阻塞 |
| 戳一戳入站速率 mute 命中率 | ≥ 95% | N/A | balanced 才有 | 阻塞 |
| 引用回复成功率 | ≥ 99% | N/A | balanced 才有 | 阻塞 |
| proactive 单 reply 成本 vs baseline | ≤ 1.0× / ≤ 1.0× / ≤ 2.5× | 等待样本 | 阻塞 | 阻塞 |
| PersonaScore 5 任务 baseline 跌幅 | ≤ 5pp | N/A（economy 不影响 persona） | 阻塞 | 阻塞 |
| 用户主观验收 | "拟人化档位生效"+"不影响日常对话" | 待用户最终验收 | 阻塞 | 阻塞 |

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) / pytest.raises(TimeoutError) 锁脏写）
- [ ] uv run pytest -q 全绿（含本任务新测试）
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] 30 秒回滚演练成功（命令贴本回执）
- [ ] 同 wave 其它任务无冲突（git rebase / merge clean）
- [ ] config 改动一律落 config.json，TOML 跟改并加 "运行时源不读 TOML" 注释
- [ ] feature flag 默认 off 且 profile=custom 退化为原行为（前向兼容）
```

---

## 6. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | wave | 状态 | 落地证据 / 备注 |
|---|---|---|---|
| **P6.-1** | -1 | ✅ | 2026-05-26 接手复核通过：`config/config.json` 为运行时源；`runtime_groups=['993065015']`；`group.overrides` 含 `993065015 / 984198159`；`storage/usage.db` 实表为 `llm_calls` 且含 `prompt_cache_hit_tokens / prompt_cache_miss_tokens`；按用户口径忽略 Part 2-3 灰度 gate |
| **P6.0.a** | 0 | 🟡 | 已落地并自验：`humanization.state_board.layout` 默认 `head`，`tail` 时顺序为 `[static, *plugin_static, *plugin_stable, *plugin_dynamic, state_board]`；focused pytest 43 passed；待用户最终审查 |
| **P6.0.b** | 0 | 🟡 | 已落地并自验：`humanization.state_board.granularity` 默认 `fine`；`coarse` 去分钟/计数字段并稳定 topic tie；focused pytest 62 passed；待用户最终审查 |
| **P6.0.c** | 0 | ⏳ | 7 日生产复盘；按用户口径忽略灰度 gate，不阻塞后续 Wave；默认值仍保持 head/fine |
| **P6.0.x1** | 1 | 🟡 | 已落地并自验：`profile=custom/economy/balanced/performance` + `ResolvedHumanization`；focused pytest 32 passed；待用户最终审查 |
| **P6.0.x2** | 1 | 🟡 | 已落地并自验：`GroupOverride.humanization_profile` → `ResolvedGroupConfig.humanization_profile`；focused pytest 32 passed；待用户最终审查 |
| **P6.0.x3** | 1 | 🟡 | 已落地并自验：`_humanization_resolve()` 接入 PromptBuilder / state_board / LLMClient 分段；focused pytest 121 passed；待用户最终审查 |
| **P6.0.x4** | 1 | 🟡 | 已落地并自验：`HumanizationHealthGuard` 96 行，读 `llm_calls` hit/miss 口径，performance 降级到 balanced；focused pytest 19 passed；待用户最终审查 |
| **P6.0.x5** | 1 | 🟡 | 已落地并自验：配置页 profile 下拉/只读子能力摘要、群级档位覆盖、dashboard 档位与降级 chip；focused pytest 64 passed；frontend vue-tsc/build passed；待用户最终审查 |
| **P6.0.y1** | 2 | ⏳ | 入站 PokeNotifyEvent + NapCat raw 表情回应 hook；投递 Part 2-3 既有 group_timeline |
| **P6.0.y2** | 2 | ⏳ | services/tools/interaction_tools.py；token bucket 速率门 |
| **P6.0.y3** | 2 | ⏳ | `<quote msg_id="..."/>` 锚点解析 + MessageSegment.reply |
| **P6.0.y4** | 2 | ⏳ | qq_interactions 子配置 + GroupOverride.qq_interactions_profile_override |
| **P6.1** | 3 | ⏳ | streaming_segmenter.py；订正路径见 §1 表 |
| **P6.2** | 3 | ⏳ | _stream_with_segments hook 进 client.py |
| **P6.3** | 3 | ⏳ | 灰度脚本 + 200 条比对 |
| **P6.4** | 3 | ⏳ | B 方案默认开 + 卸 fallback |
| **P6.5** | 4 | ⏳ | pause_extend.py 决策器 |
| **P6.6** | 4 | ⏳ | _maybe_extend 接入 |
| **P6.7** | 4 | ⏳ | D 方案灰度 |
| **P6.8** | 4 | ⏳ | D 方案默认开 |
| **P6.9** | 5 | ⏳ | plan_then_utter.py + group whitelist 仅灰度群 |
| **P6.10** | 5 | ⏳ | 14 日 pilot |
| **P6.11** | 5 | ⏳ | A 方案决策门 |
| **P6.12** | 6 | ⏳ | C 方案锁定（仅文档） |
| **P6.13** | 6 | ⏳ | 文档收口 + measure 脚本 |

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P6.-1，回执贴运行时 config 源 + Part 2-3 出口验证 + usage.db 列名结论；再领 Wave 0 任意一条。
2. **多人并行**：同 wave 内任务可同时下发，不同 wave 串行。
3. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行（本仓约定见 [docs/agent-discipline.md](../agent-discipline.md)）。
4. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + PR 链接发我，我跑 §5 验收清单后改 ✅。
5. **冲突冲突**：本文 §1 与主线冲突时**以本文为准**；其它部分以 [Part 6 主线 v2.3](./omubot-humanization-part6-source-side-generation.md) 为准。
6. **遇到证据不成立**：跟我同步，由我决定撤销 / 重订正。
7. **配置改动一律落 [config/config.json](../../config/config.json)**——`config.toml` 是过期文档；详见 [maintenance-log 2026-05-26 B3 灰度切流静默失效](../../maintenance-log.md) 条目（用户：D7 部署前 git hygiene 已记录此教训）。
8. **D6 admin SPA 同步路径**：P6.0.x5 改前端 `npm run build` 即生效，无需 `--build` rebuild bot；改了 .py 才需 rebuild bot。
9. **D5 pytest 防孤儿**：跑全量 pytest 前先 `pkill -9 -f pytest`，否则可能跟 IDE 抢 sqlite 文件锁导致死锁。
10. **D7 git hygiene**：deploy / build / merge 前必跑 `git stash list && git status -uno`；`storage/*.db*` / `*.bak*` 走 .gitignore 物理护栏，不用 `git add -A`。

---

## 8. 与 Part 5 / Part 2-3 的关系

### 8.1 与 Part 5 自然分段重构

[Part 5 主线](./omubot-humanization-part5-segmentation.md) 与 Part 6 在 B/A 方案下**互斥**，D 方案下**完全正交**：

- **B Streaming-as-segment 与 Part 5 natural_split 互斥**——前者在 SSE token-stream 上 online 切，后者在完整文本上 batch 切。`resolve_profile()` 在 B 启用时输出 `disable_natural_split=True`，由 [services/llm/client.py:397](../../services/llm/client.py#L397) `_reply_segments` 入口分流
- **A Plan-then-utter 与 Part 5 互斥**——utter call 输出 max_tokens=150 即单段，natural_split 退化 noop
- **D Pause-then-extend 与 Part 5 完全正交**——D 控制段间，Part 5 控制段内
- **profile=economy / custom 与 Part 5 完全正交**——Part 5 不受影响

### 8.2 与 Part 2-3 输入感知 / 群语境

Part 2-3 已在执行中（用户原话："part2-3 已在执行"）。Part 6 与 Part 2-3 在调度器层正交：

- Part 2-3 拥有 trigger arbitration / group_timeline / addressee 仲裁；Part 6 仅在已经决定回复后影响生成形态
- **P6.0.y1 入站事件源是唯一交集**——戳一戳 / 表情回应解析后投递 Part 2-3 既有 `group_timeline.new_user_message` 总线；**不改 Part 2-3 调度仲裁逻辑**
- 启动顺序：等 Part 2-3 派单全部完成（[Part 2-3 派单文档](./omubot-humanization-part2-3-execution.md) §6 状态表全 ✅）再启动本文 P6.-1

---

## 9. 执行者逐步追踪（2026-05-26 接手）

### P6.-1 接手实证 / 完成记录

- **拆单**：复核运行时配置源、灰度群、Part 2-3 出口口径、usage DB 表与列名。
- **结论**：运行时源为 [config/config.json](../../config/config.json)；`humanization.runtime_groups=['993065015']`；`group.overrides` 已含 `993065015 / 984198159`；`storage/usage.db` 实表为 `llm_calls`，列 `prompt_cache_hit_tokens / prompt_cache_miss_tokens` 存在。
- **口径**：用户已明确“忽略前项未完成灰度内容”，故 Part 2-3 灰度 gate 不再阻塞 Part 6 Wave 0。
- **回填**：§6 `P6.-1` 已置 ✅。

### P6.0.a 领单拆分（执行前）

- **目标**：新增 `humanization.state_board.layout`，默认 `head` 保持现状；设置为 `tail` 时把 state_board 从 plugin_static 后移至 plugin_stable/plugin_dynamic 之后。
- **代码拆分**：`kernel/config.py` 增 `StateBoardConfig` 嵌套模型；`services/llm/prompt_builder.py` 增 layout 入参与顺序分流；`plugins/chat/plugin.py` 注入运行时配置；`config/config.json` 写入默认值。
- **测试拆分**：`tests/test_prompt.py` 覆盖默认 head 与 tail 顺序；`tests/test_humanization_config.py` 覆盖默认值与 JSON/TOML 加载。
- **验收证据计划**：跑 `tests/test_prompt.py tests/test_humanization_config.py tests/test_config_loader.py`，并用 grep 锁定 `state_board.layout` 命中范围。

### P6.0.a 完成记录

- **代码**：新增 `StateBoardConfig.layout`，`PromptBuilder` 支持 head/tail 两种顺序，`ChatPlugin` 从运行时配置注入 layout，[config/config.json](../../config/config.json) 写入默认 `head`。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_prompt.py tests/test_humanization_config.py tests/test_config_loader.py` → `43 passed`。
- **D1 grep**：`state_board.layout / STATE_BOARD_LAYOUT / state_board_layout / StateBoardConfig` 命中 `kernel/config.py`、`services/llm/prompt_builder.py`、`plugins/chat/plugin.py`、tests 与 `config/config.json`。
- **D2 cancel-path**：N/A（纯 prompt layout 分流，无异步脏写；`include_state_board=False` 保持 DeepSeek native 旁路）。
- **回滚**：`config/config.json` 改回/保持 `"layout": "head"` 后重启。
- **回填**：§6 `P6.0.a` 已置 🟡，等待最终审查。

### P6.0.b 领单拆分（执行前）

- **目标**：新增 `humanization.state_board.granularity`，默认 `fine` 保持旧输出；设置为 `coarse` 时去掉分钟级/计数字段，提升 prompt byte stability。
- **代码拆分**：`StateBoardConfig` 增 `granularity`；`PromptBuilder.build_state_board_block()` 传入 granularity；`GroupStateBoard.query_state()` 与 `_derive_frequency/_derive_mentions/_derive_topics` 增 coarse 分支。
- **粗粒度规则**：frequency 仅输出 `活跃/正常/冷清/暂无消息`；mentions 输出 `刚刚/今天早些/昨天/更早`；topics 用确定性排序稳定 tie。
- **测试拆分**：新增 coarse frequency/mentions/topics 测试，并覆盖 fake clock 跨 10 分钟输出 byte-identical。
- **验收证据计划**：跑 `tests/test_state_board.py tests/test_prompt.py tests/test_humanization_config.py`，并用 grep 锁定 `_derive_mentions/_derive_frequency/_derive_topics` 命中范围。

### P6.0.b 完成记录

- **代码**：`StateBoardConfig` 增 `granularity`；`PromptBuilder` 向 `GroupStateBoard.query_state()` 传粒度；`coarse` 分支移除 frequency 具体条数与 mention 分钟数，topic tie 使用 sticky anchor + 字典序稳定排序。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_state_board.py tests/test_prompt.py tests/test_humanization_config.py tests/test_config_loader.py` → `62 passed`。
- **D1 grep**：`_derive_mentions/_derive_frequency/_derive_topics/state_board.granularity/STATE_BOARD_GRANULARITY/state_board_granularity` 命中 `state_board.py`、`prompt_builder.py`、`plugins/chat/plugin.py`、`kernel/config.py`、tests 与 `config/config.json`。
- **D2 cancel-path**：N/A（state_board 为 read-side 派生；新增 sticky anchor 仅内存排序锚，不写 DB）。
- **回滚**：`config/config.json` 改回/保持 `"granularity": "fine"` 后重启。
- **回填**：§6 `P6.0.b` 已置 🟡；`P6.0.c` 为 7 日生产复盘，按用户口径不阻塞后续 Wave。

### P6.0.x1 领单拆分（执行前）

- **目标**：新增拟人化 `profile` 基础设施，默认 `custom` 保持现有 flag-by-flag 行为；提供 `resolve_profile(profile_value, group_id)` 纯函数输出 `ResolvedHumanization`。
- **代码拆分**：`kernel/config.py` 增 profile enum、`StreamingSegmentConfig`、`PauseThenExtendConfig`、`PlanThenUtterConfig`、`ResolvedHumanization` dataclass；保留既有 Part 1/3.5 flags。
- **决议规则**：`custom` 读取显式子配置；`economy` 仅使用 cache 稳定化（state_board tail/coarse），不开 streaming/pause/plan；`balanced` 开 streaming + pause，禁用 natural_split；`performance` 额外开 plan_then_utter，禁用 natural_split。
- **测试拆分**：覆盖默认 custom、三档 profile 决议、custom 子配置退化、`disable_natural_split` 互斥字段。
- **验收证据计划**：跑 `tests/test_humanization_config.py tests/test_config_loader.py`，grep 锁 `profile / ResolvedHumanization / resolve_profile`。

### P6.0.x1 完成记录

- **代码**：新增 `HumanizationProfile`、`StreamingSegmentConfig`、`PauseThenExtendConfig`、`PlanThenUtterConfig` 与 `ResolvedHumanization`；`HumanizationConfig.resolve_profile()` 输出 6 个决议字段。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_config.py tests/test_config_loader.py` → `32 passed`。
- **D1 grep**：`HumanizationProfile / ResolvedHumanization / profile / streaming_segment / pause_then_extend / plan_then_utter` 命中 `kernel/config.py`、`tests/test_humanization_config.py` 与 `config/config.json`；另有既有 `LLMConfig.resolve_profile()` 同名方法。
- **D2 cancel-path**：N/A（pure data / pure resolve，无 IO 与脏写）。
- **回滚**：`humanization.profile=custom`，所有新子配置 `enabled=false`。
- **回填**：§6 `P6.0.x1` 已置 🟡。

### P6.0.x2 领单拆分（执行前）

- **目标**：给群级覆盖增加 `humanization_profile`，并让 `GroupConfig.resolve()` 透传到 `ResolvedGroupConfig`。
- **代码拆分**：`kernel/config.py` 三处小改：`ResolvedGroupConfig` 字段、`GroupOverride` 字段、`resolve()` 三条返回路径填值。
- **测试拆分**：在 `tests/test_config_loader.py::TestGroupConfigResolve` 覆盖默认 None 与 override 透传。
- **验收证据计划**：跑 `tests/test_config_loader.py tests/test_humanization_config.py`，grep 锁 `humanization_profile`。

### P6.0.x2 完成记录

- **代码**：`ResolvedGroupConfig` 与 `GroupOverride` 增 `humanization_profile`；`GroupConfig.resolve()` 在 no-override / override / inactive 三条路径透传。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_config_loader.py tests/test_humanization_config.py` → `32 passed`。
- **D1 grep**：`humanization_profile` 仅命中 `kernel/config.py` 与 `tests/test_config_loader.py`。
- **D2 cancel-path**：N/A（pure data）。
- **回滚**：删除群 override 中 `humanization_profile` 或设为 `null`。
- **回填**：§6 `P6.0.x2` 已置 🟡。

### P6.0.x3 领单拆分（执行前）

- **目标**：新增 `_humanization_resolve(config, group_id)`，让 prompt_builder/state_board/LLM 分段消费者读取 `ResolvedHumanization`。
- **代码拆分**：`plugins/chat/plugin.py` 提供 helper 并在初始化注入全局决议；`PromptBuilder.build_blocks()/build_state_board_block()` 支持每 turn 覆盖；`services/llm/client.py` 在构建 prompt 时按 group resolve；`_reply_segments` 入口接收 `disable_natural_split` 分流。
- **兼容规则**：默认 `profile=custom` + 无群覆盖时读取显式子配置；当前 `config/config.json` 子配置均 off/head/fine，行为保持现状。
- **测试拆分**：覆盖 helper 群 override、PromptBuilder per-turn override、LLMClient 分段禁用 natural_split 的决议传递。
- **验收证据计划**：跑 `tests/test_prompt.py tests/test_client.py tests/test_humanization_config.py tests/test_config_loader.py`，grep 锁 `_humanization_resolve / ResolvedHumanization / disable_natural_split`。

### P6.0.x3 完成记录

- **代码**：`_humanization_resolve(config, group_id)` 支持群 override；`LLMClient` 每轮 resolve 一次并把 layout/granularity 传给 PromptBuilder/state_board，把 `disable_natural_split` 传给分段 planner；`_reply_segments()` 增禁用自然分段旁路。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_prompt.py tests/test_client.py tests/test_chat_plugin_humanization_wire.py tests/test_humanization_config.py tests/test_config_loader.py` → `121 passed`。
- **D1 grep**：`_humanization_resolve / ResolvedHumanization / disable_natural_split / humanization_resolver / state_board_layout / state_board_granularity` 命中 `plugins/chat/plugin.py`、`services/llm/client.py`、`services/llm/prompt_builder.py`、`kernel/config.py` 与对应 tests。
- **D2 cancel-path**：无持久写；profile resolve 异常时 `LLMClient` fallback 到默认 `ResolvedHumanization()`。
- **回滚**：`profile=custom` + 子配置 off/head/fine；群 override 删除 `humanization_profile`。
- **回填**：§6 `P6.0.x3` 已置 🟡。

### P6.0.x4 领单拆分（执行前）

- **目标**：新增 health_guard，按最近 1h `llm_calls.prompt_cache_hit_tokens/(hit+miss)` 监控群 cache hit%，performance 档低于阈值时内存降级。
- **代码拆分**：新增 `services/humanization/health_guard.py`；`HumanizationConfig.resolve_profile()` performance 路径读取降级状态；`ChatPlugin` startup/shutdown 启停 60s guard。
- **降级规则**：hit% < 80% 立即标记 degraded；hit% ≥ 85% 连续 10 分钟解除；读不到 DB 或样本为空默认不降级。
- **测试拆分**：覆盖低 hit 标记、健康持续解除、缺 DB 不误降级、performance profile 降级为 balanced。
- **验收证据计划**：跑 `tests/test_humanization_config.py` + 新 health_guard 测试，grep 锁 `_degraded_groups / HumanizationHealthGuard`。

### P6.0.x4 完成记录

- **代码**：新增 [services/humanization/health_guard.py](../../services/humanization/health_guard.py)（96 行）；`ChatPlugin` startup/shutdown 启停；`HumanizationConfig.resolve_profile()` 在 performance 路径检查 in-memory 降级。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_health_guard.py tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py` → `19 passed`。
- **D1 grep**：`_DEGRADED_GROUPS / HumanizationHealthGuard / is_group_degraded / humanization_health_guard` 命中 health_guard、`kernel/config.py`、`plugins/chat/plugin.py` 与 tests。
- **D2 cancel-path**：守卫只读 DB，状态仅内存；`stop()` cancel task 并 suppress `CancelledError`，不写持久状态。
- **回滚**：关闭 bot 或调用 `clear_degraded_groups()` 清空内存；`profile=custom/balanced` 不读取 performance 降级。
- **回填**：§6 `P6.0.x4` 已置 🟡。

### P6.0.x5 领单拆分（执行前）

- **目标**：把 Part 6 profile 能力露给 Admin SPA：配置页可编辑全局 profile，群管理可按群覆盖，仪表盘展示当前档位与 health guard 降级信号。
- **后端拆分**：`admin/routes/api/groups.py` 持久化/序列化 `humanization_profile`；`admin/routes/api/dashboard.py` 返回 `humanization.profile/runtime_groups/degraded_groups`；`health_guard` 增只读降级列表 helper。
- **前端拆分**：`ConfigView` 新增“拟人化生成”任务导航并复用 schema enum/select 自动渲染；`GroupsView` 增群级档位 radio 与差异 chip；`DashboardView` 在状态 badge 中展示当前档位和降级红点。
- **测试拆分**：更新 groups profile API 持久化测试；新增 dashboard humanization 摘要测试；前端跑 `vue-tsc` 与 build。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；`rg "humanization_profile|humanization\\.profile|degraded_group_ids"` 锁命中范围；回滚用 `git revert` + 全局/群级 profile 置回 `custom/继承全局`。

### P6.0.x5 完成记录

- **代码**：`/api/admin/groups` 与群 profile save/reset 支持 `humanization_profile`；`/api/admin/dashboard` 输出 `humanization` 摘要；配置页新增“拟人化生成”任务，群组抽屉新增“继承全局/四档” radio，dashboard 状态 badge 显示档位与降级数量。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_admin_api.py tests/test_dashboard_cache_pipelines.py tests/test_humanization_health_guard.py` → `64 passed`；`admin/frontend ./node_modules/.bin/vue-tsc --noEmit` → passed；`admin/frontend npm run build` → passed。
- **ruff / pyright**：`uv run ruff check admin/routes/api/groups.py admin/routes/api/dashboard.py admin/routes/api/__init__.py services/humanization/health_guard.py tests/test_admin_api.py tests/test_dashboard_cache_pipelines.py` → passed；`uv run pyright admin/routes/api/groups.py admin/routes/api/dashboard.py admin/routes/api/__init__.py services/humanization/health_guard.py tests/test_dashboard_cache_pipelines.py` → `0 errors`。
- **D1 grep**：`humanization_profile / humanization.profile / degraded_group_ids / 拟人化档位 / 拟人化生成` 命中 admin groups/dashboard、frontend config/dashboard/groups、health_guard、tests 与本追踪文档。
- **D2 cancel-path**：N/A（Admin API/SPA 读写配置与只读 dashboard 摘要；health guard 降级列表只读 helper，无新异步写路径）。
- **回滚**：`git revert <本提交>`；或运行时把全局 `humanization.profile` 置回 `custom`，群级覆盖改回“继承全局”，刷新/重启 admin 与 bot。
- **回填**：§6 `P6.0.x5` 已置 🟡，Wave 1 剩余灰度项继续按用户口径不阻塞后续 Wave。

---

## 10. 执行者记录模板（历史占位）

> 此节由执行者填写。每条任务交付时新增「领单拆分（执行前）+ 完成记录（执行者 GPT）」两段，参考 [Part 1 派单文档](./omubot-humanization-part1-execution.md#9-执行者-gpt-逐步追踪) §9 的格式：
>
> ```
> ### P6.0.a 领单拆分（执行前）
>
> 目标：…
> 详细步骤：1) … 2) … 3) …
> 关键 grep 锚点：…
> 单测覆盖计划：…
> 30 秒回滚命令：…
>
> ### P6.0.a 完成记录（执行者 GPT）
>
> - 改动文件：…（实际 +X / -Y）
> - D1 grep 命中：…
> - D2 cancel-path 测试：…
> - 测试回执：uv run pytest -q tests/test_state_board_layout.py 通过 / N passed
> - ruff / pyright 通过
> - 30 秒回滚演练：…
> ```
