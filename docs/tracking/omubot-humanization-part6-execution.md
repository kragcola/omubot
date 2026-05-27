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
| **P6.0.y1** | 2 | 🟡 | 已落地并自验：`on_notice` 桥接 PokeNotifyEvent + NapCat raw reaction NoticeEvent，显式 flag 开启时投递 `GroupTimeline.add_pending_trigger()` + scheduler；poke 60s/5 次内存 mute；focused pytest 55 passed；待用户最终审查 |
| **P6.0.y2** | 2 | 🟡 | 已落地并自验：`poke_user` / `react_to_message` NapCat action、profile 守卫、in-memory token bucket 与取消路径释放；focused pytest 35 passed；待用户最终审查 |
| **P6.0.y3** | 2 | 🟡 | 已落地并自验：`<quote msg_id="..."/>` 解析、非法/关闭 strip、OneBot `[CQ:reply,id=...]` 首段前缀；focused pytest 6 passed；待用户最终审查 |
| **P6.0.y4** | 2 | 🟡 | 已落地并自验：`QQInteractionsConfig` 5 flag、ResolvedHumanization 5 决议字段、三档映射与群级 override schema；focused pytest 53 passed；待用户最终审查 |
| **P6.1** | 3 | 🟡 | 已落地并自验：`services/llm/streaming_segmenter.py` 179 行，支持 online boundary flush、register/mood target、finish/cancel drain、CQ/URL/ASCII 保护；focused pytest 36 passed；待用户最终审查 |
| **P6.2** | 3 | 🟡 | 已落地并自验：provider 逐行 `extract_text_delta()`、`call_api/_call/_dispatch_call on_text_delta` 透传、`LLMClient._stream_with_segments()` 安全门控接入；focused pytest 25 passed；ruff passed；pyright 0 errors；待用户最终审查 |
| **P6.3** | 3 | 🟡 | 已落地并自验：新增 `scripts/dev/measure_streaming_vs_natural.sh`，只读 immutable messages.db，采样 200 条 group reply 对比 streaming vs natural；all/984198159 达标，993065015 短回复样本未达段长方差与 delay p50 目标；待用户最终审查 |
| **P6.4** | 3 | 🟡 | 已落地并自验：balanced/performance/custom streaming 启用时默认走 `_stream_with_segments()`；streaming path 支持 quote anchor 首段 CQ reply、provider 无 delta 时 fallback result text；natural_split 仅保留给 economy/custom 非 streaming 路径；focused pytest 27 passed；待用户最终审查 |
| **P6.5** | 4 | 🟡 | 已落地并自验：`services/humanization/pause_extend.py` 143 行 pure decisioner，输出 `should_extend/wait_seconds/reasons`；focused pytest 8 passed；ruff passed；pyright 0 errors；待用户最终审查 |
| **P6.6** | 4 | 🟡 | 已落地并自验：`LLMClient._maybe_extend()` 接入群聊追发循环，开关默认 off，等待期用户插话/取消均 drop；usage 写 `proactive_extend`，BlockTrace 观测 extend 事件；focused pytest 20 passed；ruff passed；pyright 0 errors；待用户最终审查 |
| **P6.7** | 4 | 🟡 | 已落地并自验：新增 `scripts/dev/measure_extend_rate.sh`，只读 usage/messages DB；actual usage 与离线估算双口径采样 200 条，all/984198159/993065015 extend_rate 均 0%，gray gate yes；待用户最终审查 |
| **P6.8** | 4 | 🟡 | 已落地并自验：`pause_then_extend.enabled` schema/runtime config 默认 on；extend_rate guard all/984198159/993065015 均 0%，gray gate yes；全量 pytest `1964 passed, 8 skipped`；待用户最终审查 |
| **P6.9** | 5 | 🟡 | 已落地并自验：`services/llm/plan_then_utter.py` 189 行；默认 off + whitelist/profile gate；主动群聊无业务工具路径写 `proactive_plan/proactive_utter` usage 与 BlockTrace parent_span_id；focused pytest 30 passed；ruff passed；pyright 0 errors；待用户最终审查 |
| **P6.10** | 5 | 🟡 | 已落地并自验：新增 `scripts/dev/measure_plan_then_utter_pilot.sh`，只读 usage/block_trace DB，按单 proactive reply 汇总 `plan+utter` token 成本、latency、BlockTrace parent_span 与 PersonaScore baseline；当前 14 日窗口无 `proactive_plan/proactive_utter` 样本，决策为继续收样；focused pytest 2 passed；待用户最终审查 |
| **P6.11** | 5 | 🟡 | 已完成决策门：当前不上默认、不永久关闭，结论为“继续 pilot collect samples / 调参后再 pilot”；硬阈值仍为成本 >2.5× 或 PersonaScore 跌 >5pp 即回滚 `plan_then_utter.enabled=false`；待用户最终审查 |
| **P6.12** | 6 | 🟡 | 已文档锁定：C 方案不开发、不灰度；理由为 DeepSeek 不支持 stream continuation、abort 后 reasoning/prompt 因果链漂移、输出浪费与复杂度不可控；待用户最终审查 |
| **P6.13** | 6 | 🟡 | 已收口：新增 `scripts/dev/measure_humanization_part6.sh` 串联 P6.3/P6.7/P6.10 只读测量；追踪文档回填至 P6.13；`maintenance-log.md` 因共享脏改本轮不触碰；待用户最终审查 |

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

### P6.0.y1 领单拆分（执行前）

- **目标**：新增 QQ 入站交互信号源：戳一戳与 NapCat 表情回应统一解析为 trigger signal，投递现有 `GroupTimeline.add_pending_trigger()` + scheduler，不改 Part 2-3 仲裁逻辑。
- **代码拆分**：新增 `services/humanization/qq_interactions.py` 承载 `QQInteractionSignal`、Poke/Reaction raw NoticeEvent 解析与 poke 单用户速率护栏；`kernel/router.py` 只增 `on_notice` handler；新建 router focused tests。
- **启用口径**：读取 `humanization.qq_interactions.poke_inbound_response_enabled / reaction_inbound_response_enabled`，字段未存在时默认 off；后续 P6.0.y4 再补 schema。
- **D2 计划**：handler 无持久写；disabled/非 bot 目标/被速率 mute 时不碰 timeline/scheduler；速率护栏仅内存状态。
- **验收证据计划**：跑新 router tests + scheduler/force-reply 触发回归；ruff/pyright 改动范围；grep `PokeNotifyEvent|MessageReactionEvent|on_notice|QQInteractionSignal`。

### P6.0.y1 完成记录

- **代码**：新增 [services/humanization/qq_interactions.py](../../services/humanization/qq_interactions.py)，负责 Poke/Reaction NoticeEvent 解析、显式 flag gate、active-group/blocked-user 过滤、poke 60s/5 次内存 mute；[kernel/router.py](../../kernel/router.py) 仅新增轻量 `on_notice` 桥接。
- **投递路径**：开启 `poke_inbound_response_enabled` 或 `reaction_inbound_response_enabled` 后，信号写入 `GroupTimeline.add_pending_trigger()`，再以 `TriggerContext(mode="qq_interaction")` 调用 scheduler；默认字段不存在时 off。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_router_qq_interactions.py tests/test_force_reply.py tests/test_scheduler.py` → `55 passed`。
- **ruff / pyright**：`uv run ruff check kernel/router.py services/humanization/qq_interactions.py tests/test_router_qq_interactions.py` → passed；`uv run pyright services/humanization/qq_interactions.py tests/test_router_qq_interactions.py` → `0 errors`。
- **D1 grep**：`PokeNotifyEvent / MessageReactionEvent / on_notice / QQInteractionSignal / poke_inbound_response_enabled / reaction_inbound_response_enabled` 命中 router、qq_interactions、tests 与本追踪文档。
- **D2 cancel-path**：测试覆盖 disabled / 非 bot 目标不碰 timeline/scheduler；速率 mute 只写内存 dict，不写 DB/config，重启即清空。
- **回滚**：`git revert <本提交>`；运行时快速回滚保持/设置 `humanization.qq_interactions.poke_inbound_response_enabled=false` 与 `reaction_inbound_response_enabled=false` 后重启。
- **回填**：§6 `P6.0.y1` 已置 🟡，Wave 2 可继续并列推进 y2/y3/y4。

### P6.0.y2 领单拆分（执行前）

- **目标**：新增 QQ 出站交互工具 `poke_user` / `react_to_message`，通过 NapCat action 发送戳一戳与消息表情回应。
- **代码拆分**：新增 `services/tools/interaction_tools.py`，封装 profile 守卫、NapCat `send_poke` / `set_msg_emoji_like` 调用与 in-memory token bucket；`ToolRegistry` 增交互工具注册 helper，默认失败关闭，等待 P6.0.y4 resolved flag 接入。
- **速率规则**：单群 60s `poke_out <= 2` / `react_out <= 3`；单 user 5min `poke_out <= 1`；取消或异常路径释放 reservation，不写 DB/config。
- **profile 口径**：`economy/custom/字段缺失` 默认不注册；`performance` 可注册双工具；`balanced` 仅 passive 上下文允许执行主动 poke/react。
- **测试拆分**：新建 `tests/test_qq_interaction_tools.py`，覆盖 poke 调用、react 调用、群级/用户级限流、economy 不注册、balanced 主动拒绝、取消路径不消耗 token。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；grep 锁 `poke_user / react_to_message / interaction_tools`。

### P6.0.y2 完成记录

- **代码**：新增 [services/tools/interaction_tools.py](../../services/tools/interaction_tools.py)，提供 `poke_user` / `react_to_message` 两个 QQ 出站工具；[services/tools/registry.py](../../services/tools/registry.py) 增 `register_interaction_tools()`，默认失败关闭，支持后续 resolved flags 接入。
- **行为**：`send_poke` action 传 `user_id/group_id`；`set_msg_emoji_like` action 传 `message_id/emoji_id`；单群 60s `poke_out <= 2` / `react_out <= 3`，单 user 5min `poke_out <= 1`；`economy/custom/缺字段` 不注册，`balanced` 仅 passive 上下文可执行，`performance` 可执行。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_qq_interaction_tools.py tests/test_tools.py` → `35 passed`。
- **ruff / pyright**：`uv run ruff check services/tools/interaction_tools.py services/tools/registry.py tests/test_qq_interaction_tools.py` → passed；`uv run pyright services/tools/interaction_tools.py services/tools/registry.py tests/test_qq_interaction_tools.py` → `0 errors`。
- **D1 grep**：`poke_user / react_to_message / interaction_tools / send_poke / set_msg_emoji_like` 命中 interaction_tools、registry helper、tests 与本追踪文档。
- **D2 cancel-path**：测试覆盖 `asyncio.CancelledError` 时释放 token bucket reservation，同一 user/group 随后可重新成功执行；所有速率状态仅内存，不写 DB/config。
- **回滚**：`git revert <本提交>`；运行时不调用 `register_interaction_tools()` 或保持后续 `qq_interactions.*_outbound_enabled=false` 即工具不可见。
- **回填**：§6 `P6.0.y2` 已置 🟡；`maintenance-log.md` 当前含他人未提交 Part 5 变更，本单只回填追踪文档，避免混入共享脏文件。

### P6.0.y3 领单拆分（执行前）

- **目标**：支持模型输出 `<quote msg_id="..."/>` 引用锚点，出站时转为 OneBot reply segment 语义；非法或关闭时静默 strip 回纯文本。
- **代码拆分**：在 `services/llm/client.py` 增 `_extract_quote_anchor()` / `_apply_quote_reply_anchor()`；在主回复路径与 tool-exhausted 兜底回复路径中，先 strip 锚点再 rewrite，最终给首段加 `[CQ:reply,id=...]` 前缀，由现有 `Message(text)` 发送层解析。
- **profile 口径**：显式读取后续 P6.0.y4 的 `qq_interactions_quote_reply_enabled`；字段缺失时仅对 balanced/performance 形态启用，economy/custom 默认 strip。
- **测试拆分**：新建 `tests/test_quote_reply_segment.py`，覆盖合法锚点、非法 msg_id、关闭时 strip、多锚点取第一个。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；grep 锁 `<quote msg_id / _extract_quote_anchor / CQ:reply`。

### P6.0.y3 完成记录

- **代码**：在 [services/llm/client.py](../../services/llm/client.py) 新增 `_extract_quote_anchor()`、`_quote_reply_enabled()`、`_apply_quote_reply_anchor()`；主回复路径与 tool-exhausted 兜底路径均在 rewrite 前 strip 锚点，发送前为首段加 `[CQ:reply,id=...]`。
- **行为**：合法 `<quote msg_id="123"/>` 转 OneBot CQ reply 前缀；非法 / 不存在可解析 msg_id / profile 关闭时静默移除 quote tag 并回退纯文本；多锚点取第一个并 strip 全部 tag。
- **兼容说明**：当前出站层由 scheduler/router 统一 `Message(text)` 发送，OneBot 会解析 `[CQ:reply,id=...]` 为 reply segment；未改 LLMClient 返回类型，避免冲击分段管线。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_quote_reply_segment.py tests/test_llm_client_reply_segment_plan.py` → `6 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/client.py tests/test_quote_reply_segment.py` → passed；`uv run pyright services/llm/client.py tests/test_quote_reply_segment.py` → `0 errors`。
- **D1 grep**：`<quote msg_id / _extract_quote_anchor / _apply_quote_reply_anchor / CQ:reply / qq_interactions_quote_reply_enabled` 命中 client、quote tests 与本追踪文档。
- **D2 cancel-path**：锚点解析只在内存字符串上操作，无 DB/config 写入；reply 被取消时 quote tag 已在本地 reply 文本中 strip，不残留持久半状态。
- **回滚**：`git revert <本提交>`；运行时后续 `qq_interactions.quote_reply_enabled=false` 或 economy/custom 档会 strip 锚点回纯文本。
- **回填**：§6 `P6.0.y3` 已置 🟡。注意 `services/llm/client.py` 在接手前已有他人未提交分段重构 diff，本单仅在其当前版本上追加 quote 锚点逻辑。

### P6.0.y4 领单拆分（执行前）

- **目标**：补齐 `humanization.qq_interactions` 子配置、`ResolvedHumanization` 5 个 QQ 交互决议字段，以及群级 `qq_interactions_profile_override` schema。
- **代码拆分**：`kernel/config.py` 新增 `QQInteractionsConfig`；`HumanizationConfig` 挂载 `qq_interactions`；`resolve_profile()` custom 读显式 flag，economy 全关，balanced 开入站/quote 关主动出站，performance 全开；GroupOverride/ResolvedGroupConfig 透传 `qq_interactions_profile_override`。
- **测试拆分**：新建 `tests/test_humanization_qq_interactions.py`，覆盖 economy、balanced、performance、custom 显式 flag 与 group override schema。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；grep 锁 `qq_interactions / QQInteractionsConfig / qq_interactions_profile_override`。

### P6.0.y4 完成记录

- **代码**：[kernel/config.py](../../kernel/config.py) 新增 `QQInteractionsConfig`（5 个 bool flag 默认全 off），`HumanizationConfig.qq_interactions`，`ResolvedHumanization` 5 个 `qq_interactions_*` 决议字段；`GroupOverride` / `ResolvedGroupConfig` 透传 `qq_interactions_profile_override`。
- **三档决议**：`economy` 全 off；`balanced` 开 `poke_inbound / reaction_inbound / quote_reply`，主动 `poke_outbound / reaction_outbound` 仍 off；`performance` 全 on；`custom` 直读 `humanization.qq_interactions.*`。
- **联动验证**：y1 入站 gate 现在能读到 schema 字段；y2 注册 helper 可读取 resolved outbound 字段；y3 quote helper 可读取 resolved quote 字段。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_humanization_qq_interactions.py tests/test_humanization_config.py tests/test_config_loader.py tests/test_quote_reply_segment.py tests/test_qq_interaction_tools.py` → `53 passed`。
- **ruff / pyright**：`uv run ruff check kernel/config.py services/llm/client.py services/tools/interaction_tools.py services/tools/registry.py tests/test_humanization_qq_interactions.py tests/test_quote_reply_segment.py tests/test_qq_interaction_tools.py` → passed；`uv run pyright kernel/config.py services/llm/client.py tests/test_humanization_qq_interactions.py tests/test_quote_reply_segment.py tests/test_qq_interaction_tools.py` → `0 errors`。
- **D1 grep**：`qq_interactions / QQInteractionsConfig / qq_interactions_profile_override` 命中 config、QQ interaction tests、quote/tool tests 与本追踪文档。
- **D2 cancel-path**：N/A（pure data / BaseModel 决议）；无 IO、无 DB/config 写入副作用。
- **回滚**：`git revert <本提交>`；运行时保持 `profile=custom` 且 `qq_interactions.*=false` 等价于 QQ 特殊交互全关。
- **回填**：§6 `P6.0.y4` 已置 🟡，Wave 2 四条 y1-y4 均已进入待最终审查状态。

### P6.1 领单拆分（执行前）

- **目标**：新增 `services/llm/streaming_segmenter.py`，提供可在 SSE token/chunk 流上在线切段的 `StreamingSegmenter`，不接主链路。
- **代码拆分**：实现 `StreamingSegmenterConfig`、`StreamingSegmenter.push()`、`finish()`、`cancel()`；按句末标点优先、软上限按分句标点、硬上限兜底；接受 register / mood 调整目标段长。
- **D2 计划**：`cancel()` drain 并清空 buffer，下一条 reply 不串上一次未发尾巴；算法不写 DB/config。
- **测试拆分**：新建 `tests/test_streaming_segmenter.py`，覆盖句末 flush、min chars、hard limit、finish drain、cancel reset、register/mood 调整、CQ code 保护、URL/ASCII token 保护。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；grep 锁 `class StreamingSegmenter / streaming_segmenter`。

### P6.1 完成记录

- **代码**：新增 [services/llm/streaming_segmenter.py](../../services/llm/streaming_segmenter.py)（179 行），提供 `StreamingSegmenterConfig` 与 `StreamingSegmenter.push()/finish()/cancel()`；按句末标点、分句软边界、硬上限兜底在线 flush。
- **行为**：支持 register/mood 调整目标段长；`finish()` drain 尾段；`cancel()` drain 并清空 buffer，避免下一条 reply 串上一次残留；保护 CQ code、URL 与 ASCII token，不在保护区内硬切。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_streaming_segmenter.py tests/test_segmentation.py tests/test_inter_segment_delay.py` → `36 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/streaming_segmenter.py tests/test_streaming_segmenter.py` → passed；`uv run pyright services/llm/streaming_segmenter.py tests/test_streaming_segmenter.py` → `0 errors`。
- **D1 grep**：`class StreamingSegmenter / StreamingSegmenterConfig / streaming_segmenter` 命中 streaming_segmenter、tests 与本追踪文档。
- **D2 cancel-path**：`test_cancel_drains_and_prevents_next_reply_contamination` 覆盖 cancel drain + 清 buffer；算法无 IO、无持久写。
- **回滚**：`git rm services/llm/streaming_segmenter.py tests/test_streaming_segmenter.py`；P6.2 未接入前运行时行为不变。
- **回填**：§6 `P6.1` 已置 🟡。

### P6.2 领单拆分（执行前）

- **目标**：把 P6.1 的 `StreamingSegmenter` 接入 LLM SSE 主路径，支持 token 到达时在线 flush 给 `on_segment`；feature flag 仍由 `humanization.streaming_segment.enabled/profile` 控制，默认关闭。
- **代码拆分**：`LLMProvider` 新增逐行 `extract_text_delta()`；DeepSeek/OpenAI/Anthropic provider 按各自 SSE 格式提取文本 delta；`call_api()` / `LLMClient._dispatch_call()` / `_call()` 透传可选 `on_text_delta`；`LLMClient` 新增 `_stream_with_segments()` 封装 segmenter。
- **安全口径**：只在 `on_segment` 存在、`streaming_segment_enabled=True`、当前 round 无真实工具（允许仅 `pass_turn` 被 `force_reply` 移除）且关闭 rewrite/quote/kaomoji 二次路径时在线发送；否则仅保留 hook 能力，不改变原完整文本后处理。
- **D2 计划**：`on_segment` 抛 `CancelledError` 时调用 `segmenter.cancel()` 清空 buffer 后重新抛出；provider token hook 不写 DB/config；取消不污染下一条 reply。
- **测试拆分**：新增 `tests/test_streaming_hook.py`，覆盖 provider delta 抽取、`call_api(on_text_delta)`、client `_dispatch_call()` 透传、streaming cancel 清 buffer、chat 安全门控下在线发段并避免重复发送已流出的段。
- **验收证据计划**：focused pytest + ruff/pyright 改动范围；grep 锁 `_stream_with_segments / extract_text_delta / on_text_delta / streaming_segment`。

### P6.2 完成记录

- **代码**：[services/llm/provider.py](../../services/llm/provider.py) 新增默认 `extract_text_delta()`；DeepSeek/OpenAI/Anthropic provider 按各自 SSE 行解析 visible text delta；[services/llm/client.py](../../services/llm/client.py) 透传 `on_text_delta` 并新增 `_stream_with_segments()`，在群聊强制回复、无工具、streaming profile 开启、rewrite/quote 关闭时在线发段。
- **行为**：安全门控外仍走旧完整文本后处理；安全门控内每个 ready segment 经 `_clean_reply` / `_strip_control_tokens` / `fix_cq_codes` 后调用 `on_segment`，并返回空字符串避免 scheduler 二次发送。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_streaming_hook.py tests/test_call_api.py tests/test_streaming_segmenter.py tests/test_llm_client_reply_segment_plan.py tests/test_quote_reply_segment.py` → `25 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/client.py services/llm/provider.py services/llm/providers/deepseek.py services/llm/providers/openai.py services/llm/providers/anthropic.py services/llm/streaming_segmenter.py tests/test_streaming_hook.py tests/test_call_api.py tests/test_streaming_segmenter.py tests/test_llm_client_reply_segment_plan.py` → passed；`uv run pyright ...` → `0 errors`。
- **D1 grep**：`_stream_with_segments / extract_text_delta / on_text_delta / streaming_segment` 命中 client、provider 三实现、streaming hook tests 与本追踪文档。
- **D2 cancel-path**：`tests/test_streaming_hook.py::test_stream_with_segments_cleans_buffer_on_cancel` 覆盖 `CancelledError` 时调用 segmenter.cancel 清 buffer，下一次 streaming 不串入旧内容；无 DB/config 写入。
- **回滚**：`humanization.profile=custom/economy` 或 `humanization.streaming_segment.enabled=false` 后重启；安全门控外自动回退旧完整文本分段路径。
- **回填**：§6 `P6.2` 已置 🟡；P6.3 继续推进脚本与 200 条对比采样。

### P6.3 领单拆分（执行前）

- **目标**：新增只读灰度测量脚本，采样 200 条 group assistant reply，比对 `StreamingSegmenter` 与 Part 5 `reply_segment_plan()` 的段数、段长、段间延迟和体感代理指标。
- **代码拆分**：新增 `scripts/dev/measure_streaming_vs_natural.sh`；默认读取 `storage/messages.db`，支持 `GROUP_ID / LIMIT / REGISTER / MOOD / STREAM_* / NATURAL_MAX_SEGMENT_CHARS` 环境变量；SQLite 使用 `mode=ro&immutable=1` 避免锁住运行中 bot。
- **指标拆分**：输出 sample window、segment count distribution、segment length mean/p50/p95/variance、delay avg/p50/p95、文本保真率、>3 段比例、灰度阈值是否命中。
- **验收证据计划**：`bash -n` + 实跑 all/灰度群 200 条；P6.3 无代码主链路改动，D2 N/A。

### P6.3 完成记录

- **代码**：新增 [scripts/dev/measure_streaming_vs_natural.sh](../../scripts/dev/measure_streaming_vs_natural.sh)，用现有 `group_messages.content_text` 离线重放；natural 路径调用 `reply_segment_plan()`，streaming 路径按 chunk 模拟 SSE 并调用 `StreamingSegmenter.push()/finish()`。
- **只读口径**：`sqlite3.connect("file:...mode=ro&immutable=1", uri=True)`，不写 DB/config；缺表或缺样本时明确输出状态。
- **灰度结果**：all groups 200 条：streaming variance `124.955`、delay p50 `2.75s`、文本保真率 `1.0`，命中 P6.3 阈值；`984198159` 200 条：variance `101.2829`、delay p50 `2.76s`，命中阈值；`993065015` 近 200 条短回复占比高，variance `17.8034`、delay p50 `1.05s`，不命中体感收益阈值。
- **自验**：`bash -n scripts/dev/measure_streaming_vs_natural.sh` → passed；`source ./scripts/dev/env.sh && GROUP_ID=984198159 LIMIT=200 scripts/dev/measure_streaming_vs_natural.sh` → sample 200 且 gray gate yes。
- **D1 grep**：`measure_streaming_vs_natural / Streaming vs Natural Measurement / StreamingSegmenter` 命中脚本与本追踪文档。
- **D2 cancel-path**：N/A（灰度测量脚本只读 DB + 纯本地分段模拟，无异步运行状态）。
- **回滚**：删除脚本或忽略报告；运行时回滚仍为 `profile=economy` 或 `humanization.streaming_segment.enabled=false` 后重启。
- **回填**：§6 `P6.3` 已置 🟡。

### P6.4 领单拆分（执行前）

- **目标**：让 B 方案在 balanced/performance/custom streaming 启用时成为真实默认路径，同时保留 economy/custom 非 streaming 的 Part 5 natural fallback。
- **代码拆分**：收口 `_streaming_segment_enabled()`，不再因 rewrite/quote 开启而把 balanced 默认 streaming 全挡掉；`_stream_with_segments()` 自己处理 quote anchor 首段 CQ reply；provider 没有 text delta 时用最终 result text fallback 发段。
- **安全口径**：仍要求 `on_segment` 存在、群聊、`force_reply=True`、无工具定义、`streaming_segment_enabled=True`；pass_turn/tool 复杂路径继续走完整文本 fallback。
- **测试拆分**：扩展 `tests/test_streaming_hook.py`，覆盖 balanced quote+rewrite 开启时仍 streaming、无 delta fallback result text；保留 cancel buffer 测试。
- **验收证据计划**：focused pytest + ruff/pyright；脚本跑 200 条灰度群确认 P6.3 指标仍可复现。

### P6.4 完成记录

- **代码**：[services/llm/client.py](../../services/llm/client.py) 的 streaming gate 现在只保留 `on_segment/group/force_reply/no tools/profile enabled` 关键条件；`_stream_with_segments()` 增 `quote_reply_enabled`，首个有效 `<quote msg_id="..."/>` 转 `[CQ:reply,id=...]` 前缀，只作用于首个可见段。
- **fallback 收口**：当 provider 不提供逐 token text delta 时，streaming path 会用 `result["text"]` 再跑一次 segmenter；economy/custom 非 streaming path 继续使用完整文本 `reply_segment_plan()`，`natural_split` 未被删除。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_streaming_hook.py tests/test_call_api.py tests/test_streaming_segmenter.py tests/test_llm_client_reply_segment_plan.py tests/test_quote_reply_segment.py` → `27 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/client.py tests/test_streaming_hook.py tests/test_call_api.py tests/test_streaming_segmenter.py tests/test_llm_client_reply_segment_plan.py tests/test_quote_reply_segment.py` → passed；`uv run pyright ...` → `0 errors`。
- **灰度复测**：`GROUP_ID=984198159 LIMIT=200 scripts/dev/measure_streaming_vs_natural.sh` → sample 200，streaming variance `101.2829`、delay p50 `2.76s`、文本保真率 `1.0`，gray gate yes。
- **D1 grep**：`_stream_with_segments / _streaming_segment_enabled / quote_reply_enabled / measure_streaming_vs_natural` 命中 client、streaming hook tests、脚本与本追踪文档。
- **D2 cancel-path**：沿用 `test_stream_with_segments_cleans_buffer_on_cancel`，`CancelledError` 时 `segmenter.cancel()` 清 buffer；新增 quote/fallback 不写 DB/config。
- **回滚**：`profile=economy` 或 `humanization.streaming_segment.enabled=false` 后重启；若需代码级回滚，恢复 `_streaming_segment_enabled()` 对 streaming 的保守门控。
- **回填**：§6 `P6.4` 已置 🟡，Wave 3 完成，继续 Wave 4 P6.5。

### P6.5 领单拆分（执行前）

- **目标**：新增 pause-then-extend 纯决策器，输入上一条回复、register、slot 与群状态，输出是否追发与等待秒数。
- **代码拆分**：新建 `services/humanization/pause_extend.py`，控制在 150 行内；实现 `PauseExtendConfig`、`PauseExtendDecision`、`PauseExtend.decide()`，不读取 DB/config，不写运行态。
- **决策规则**：空文本、过短、过长、用户已插话直接拒绝；开放尾巴、延续词、未完成表面提升追发概率；疑问句、低能量、礼貌疏离、热群降低追发概率；等待秒数按 register/energy/group heat 调整并 clamp。
- **测试拆分**：新增 `tests/test_pause_extend.py`，覆盖开放尾巴、疑问拒绝、用户插话拒绝、register/slot/群热度、长度边界与等待 clamp。
- **验收证据计划**：`wc -l` 确认 ≤150 行；focused pytest + ruff/pyright；grep 锁 `class PauseExtend / should_extend`。

### P6.5 完成记录

- **代码**：新增 [services/humanization/pause_extend.py](../../services/humanization/pause_extend.py)，143 行 pure decisioner；无 IO、无全局状态、无 DB/config 写入。
- **行为**：输出 `PauseExtendDecision(should_extend, wait_seconds, reasons)`；支持 register/slot/group_state 字典或对象输入；等待秒数稳定 clamp 到配置上下限。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_pause_extend.py` → `8 passed`。
- **ruff / pyright**：`uv run ruff check services/humanization/pause_extend.py tests/test_pause_extend.py` → passed；`uv run pyright services/humanization/pause_extend.py tests/test_pause_extend.py` → `0 errors`。
- **D1 grep**：`class PauseExtend / should_extend / PauseExtendDecision` 命中 `services/humanization/pause_extend.py`、`tests/test_pause_extend.py` 与本追踪文档。
- **D2 cancel-path**：N/A（纯决策，无异步等待，无持久写）。
- **回滚**：删除 `services/humanization/pause_extend.py` 与对应测试；P6.6 未开启前运行时无行为变化。
- **回填**：§6 `P6.5` 已置 🟡，继续 P6.6。

### P6.6 领单拆分（执行前）

- **目标**：把 P6.5 决策器接入 `LLMClient` 出站后追发链路；仅在 `humanization.pause_then_extend_enabled=True`、群聊、存在 `on_segment` 时启用，默认 off。
- **代码拆分**：`services/llm/client.py` 增 `_maybe_extend()`、追发 gate、群状态读取与 BlockTrace 观测；普通分段/streaming/tool-exhausted 成功出站后调用；追发 LLM call 复用 main profile，usage `call_type=proactive_extend`。
- **安全口径**：最多追发 2 次；等待期间 `GroupTimeline.get_pending(group_id)` 出现新用户消息则 drop；`CancelledError` 在 wait/call/send 三段均重新抛出，不写 timeline/usage；BlockTrace 仅做观测。
- **测试拆分**：新增 `tests/test_extend_call.py`，覆盖 feature off 不调用、feature on 先发送首轮再追发、等待期间用户插话 drop、取消期间不写 timeline/usage。
- **验收证据计划**：focused pytest + ruff/pyright；grep 锁 `_maybe_extend / proactive_extend / pause_then_extend_enabled`。

### P6.6 完成记录

- **代码**：[services/llm/client.py](../../services/llm/client.py) 新增 `_maybe_extend()`、`_pause_extend_enabled()`、`_pause_extend_group_state()` 与 `proactive_extend` 观测/usage；主回复 normal、streaming、tool-exhausted 三个成功出站点均接入。
- **行为**：开关关闭或无 `on_segment` 时完全旁路；开关开启时先把首轮回复完整送出，再按 `PauseExtend` 等待并追发；等待期间检测到群 pending 新用户消息则放弃追发；最多追发 2 次。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_extend_call.py tests/test_pause_extend.py tests/test_streaming_hook.py tests/test_llm_client_reply_segment_plan.py` → `20 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/client.py services/humanization/pause_extend.py tests/test_extend_call.py` → passed；`uv run pyright services/llm/client.py services/humanization/pause_extend.py tests/test_extend_call.py` → `0 errors`。
- **D1 grep**：`_maybe_extend / proactive_extend / pause_then_extend_enabled / PauseExtend` 命中 client、pause decisioner、extend tests 与本追踪文档。
- **D2 cancel-path**：`tests/test_extend_call.py::test_maybe_extend_cancel_during_wait_re_raises_without_extension` 覆盖等待期取消重新抛出且不写 timeline；`test_maybe_extend_user_reply_during_wait_drops_extension` 覆盖用户插话后不发追发、不调用 LLM。
- **回滚**：运行时保持 `humanization.pause_then_extend.enabled=false` 或 `profile=economy/custom` 后重启；代码级回滚移除 `_maybe_extend()` 调用与 `tests/test_extend_call.py`。
- **回填**：§6 `P6.6` 已置 🟡，继续 Wave 4 P6.7。

### P6.7 领单拆分（执行前）

- **目标**：新增 D 方案灰度脚本，输出真实 usage 追发率与历史消息离线估算追发率，验证 `extend_rate <= 25%` gate。
- **代码拆分**：新增 `scripts/dev/measure_extend_rate.sh`；只读 `storage/usage.db` 与 `storage/messages.db`；支持 `GROUP_ID / LIMIT / USAGE_LIMIT / EXTEND_RATE_MAX / REGISTER / SLOT_ENERGY`。
- **口径拆分**：`actual_usage` 统计 `llm_calls` 中 `proactive_extend / proactive|main`；`offline_estimate` 抽最近 200 条 assistant reply，单文件加载 P6.5 `PauseExtend` 决策器，并按等待窗口内是否有下一条 user 消息估算实际会被打断的追发量。
- **测试拆分**：脚本 `bash -n`；实跑 all/984198159/993065015 三组 200 条采样；P6.7 无主链路代码改动。
- **验收证据计划**：三组 `gray_gate.overall_gate=yes`，并把 actual/offline 结果回填本追踪文档。

### P6.7 完成记录

- **代码**：新增 [scripts/dev/measure_extend_rate.sh](../../scripts/dev/measure_extend_rate.sh)，SQLite 均使用 `mode=ro&immutable=1`，不写 DB/config；为避免包级重依赖，脚本按文件路径加载 `services/humanization/pause_extend.py`。
- **actual usage**：all 最近 usage 500 行：primary 500、extend 0、extend_rate `0.00%`；`984198159` primary 125、extend 0、extend_rate `0.00%`；`993065015` primary 438、extend 0、extend_rate `0.00%`。
- **offline estimate**：all 最近 200 条 assistant reply：decision_rate `3.00%`、interrupted `100.00%`、estimated_extend_rate `0.00%`；`984198159`：decision_rate `3.00%`、interrupted `100.00%`、estimated_extend_rate `0.00%`；`993065015`：decision_rate `0.00%`、estimated_extend_rate `0.00%`。
- **自验**：`bash -n scripts/dev/measure_extend_rate.sh` → passed；`scripts/dev/measure_extend_rate.sh`、`GROUP_ID=984198159 LIMIT=200 scripts/dev/measure_extend_rate.sh`、`GROUP_ID=993065015 LIMIT=200 scripts/dev/measure_extend_rate.sh` → 三组 `overall_gate: yes`。
- **D1 grep**：`measure_extend_rate / Pause Extend Rate Measurement / proactive_extend / extend_rate` 命中脚本、client 观测/usage 与本追踪文档。
- **D2 cancel-path**：N/A（灰度测量脚本只读 DB + 离线决策模拟，无异步追发状态）。
- **回滚**：删除脚本或忽略报告；运行时回滚仍为 `profile=economy` 或 `humanization.pause_then_extend.enabled=false` 后重启。
- **回填**：§6 `P6.7` 已置 🟡，继续 P6.8。

### P6.8 领单拆分（执行前）

- **目标**：将 D 方案 pause-then-extend 从灰度开关推进为默认开启，并保留 `profile=economy` / 显式关闭开关的快速回滚路径。
- **代码拆分**：`kernel/config.py` 将 `PauseThenExtendConfig.enabled` 默认值改为 `true` 并同步 help；`config/config.json` 同步运行时源；`tests/test_humanization_config.py` 更新默认值断言。
- **守卫拆分**：复跑 `scripts/dev/measure_extend_rate.sh`，确认默认开启后实际/离线 `extend_rate <= 25%`；复跑全量 pytest。
- **验收证据计划**：focused config/extend/streaming 测试 + ruff/pyright + 全量 pytest + extend_rate 三组 gate。

### P6.8 完成记录

- **代码**：[kernel/config.py](../../kernel/config.py) `PauseThenExtendConfig.enabled=True`，help 明确默认开启与回滚方式；[config/config.json](../../config/config.json) 运行时源同步 `humanization.pause_then_extend.enabled=true`；[tests/test_humanization_config.py](../../tests/test_humanization_config.py) 默认配置断言同步。
- **守卫结果**：`scripts/dev/measure_extend_rate.sh` 默认开启后 all / `984198159` / `993065015` 三组均为 `overall_gate: yes`，actual usage 与 offline estimate 的 extend_rate 均为 `0.00%`。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q` → `1964 passed, 8 skipped`。
- **ruff / pyright**：Python 相关文件 focused ruff passed；`uv run pyright kernel/config.py services/llm/client.py services/humanization/pause_extend.py tests/test_extend_call.py tests/test_humanization_config.py` → `0 errors`。
- **D1 grep**：`PauseThenExtendConfig / pause_then_extend.enabled / proactive_extend` 命中 schema、运行时 config、LLMClient、extend tests 与本追踪文档。
- **D2 cancel-path**：沿用 P6.6 覆盖；等待期取消重新抛出且不写 timeline/usage，用户插话后 drop 追发。
- **回滚**：设置 `humanization.pause_then_extend.enabled=false` 或切 `humanization.profile=economy` 后重启；无需 DB migration。
- **回填**：§6 `P6.8` 已置 🟡，继续 Wave 5 P6.9。

### P6.9 领单拆分（执行前）

- **目标**：落地 A 方案 pilot：先短 plan call 生成 2~3 段大纲，再逐段 utter call 生成并发送；仅主动群聊灰度路径生效。
- **代码拆分**：新增 `services/llm/plan_then_utter.py`，控制在 250 行内；`LLMClient` 增 `_maybe_plan_then_utter()`、gate、usage 与 BlockTrace 观测；`kernel/config.py` 确认 `humanization.plan_then_utter.enabled` 默认 off，performance 也必须显式开 flag 才启用。
- **安全口径**：仅 `on_segment` 存在、群聊、非 `force_reply`、runtime group 允许、`plan_then_utter_enabled=True`、无业务工具时启用；内置 `pass_turn` 不算业务工具；plan 无效或 utter 空/重复时 fallback 旧 main 路径；取消路径重新抛出，不写半截 timeline。
- **测试拆分**：新增 `tests/test_plan_then_utter.py`，覆盖 plan 解析、token caps、默认关闭、灰度命中、多段发送、BlockTrace parent_span_id、无效 plan fallback、取消路径、业务工具阻断。
- **验收证据计划**：focused pytest + ruff/pyright；`wc -l services/llm/plan_then_utter.py` ≤250；grep 锁 `class PlanThenUtter / proactive_plan / proactive_utter`。

### P6.9 完成记录

- **代码**：新增 [services/llm/plan_then_utter.py](../../services/llm/plan_then_utter.py) 189 行，提供 `PlanThenUtter`、plan JSON/bullet 解析、plan request `max_tokens=80`、utter request `max_tokens=150`；[services/llm/client.py](../../services/llm/client.py) 接入主动群聊早期分支。
- **gate 行为**：默认 off；`performance` profile 仍要求 `humanization.plan_then_utter.enabled=true` 且 group whitelist 命中；仅非 @/非强制的 proactive 群聊、存在 `on_segment`、无业务工具时触发；`pass_turn` 被视为内置静默工具，不阻断 pilot。
- **观测**：plan call 写 usage `proactive_plan`；每段 utter call 写 usage `proactive_utter`；BlockTrace 记录 `proactive_plan` / `proactive_utter`，metadata 带统一 `parent_span_id`、status、outline 与 utter_index。
- **fallback / cancel**：plan 无效或 utter 空/重复时回到旧 main path；plan/utter/send 任一阶段 `CancelledError` 重新抛出，未完成前不写 timeline；取消 trace 异步 best-effort 写 `status=cancelled`。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_plan_then_utter.py tests/test_humanization_config.py tests/test_extend_call.py tests/test_streaming_hook.py tests/test_llm_client_reply_segment_plan.py tests/test_client.py::test_main_chat_aggregated_usage_with_per_round_diagnostic` → `30 passed`。
- **ruff / pyright**：`uv run ruff check services/llm/plan_then_utter.py services/llm/client.py tests/test_plan_then_utter.py kernel/config.py tests/test_humanization_config.py` → passed；`uv run pyright services/llm/plan_then_utter.py services/llm/client.py tests/test_plan_then_utter.py kernel/config.py tests/test_humanization_config.py` → `0 errors`。
- **D1 grep**：`class PlanThenUtter / proactive_plan / proactive_utter / _maybe_plan_then_utter / plan_then_utter_enabled` 命中 plan 模块、client、config、tests 与本追踪文档。
- **D2 cancel-path**：`tests/test_plan_then_utter.py::test_chat_plan_then_utter_plan_cancel_re_raises_without_dirty_write` 覆盖 plan call 取消重新抛出、无发送、无 usage、无 timeline 写入。
- **回滚**：运行时设置 `humanization.plan_then_utter.enabled=false` 或切 `profile=balanced/economy` 后重启；代码级回滚移除 `_maybe_plan_then_utter()` 分支与 `tests/test_plan_then_utter.py`。
- **回填**：§6 `P6.9` 已置 🟡，继续 P6.10。

### P6.10 领单拆分（执行前）

- **目标**：补齐 A 方案 14 日 pilot 监控入口，覆盖 cost、latency、BlockTrace 配对与 PersonaScore baseline，不依赖 Grafana 即可本地复核。
- **代码拆分**：新增 `scripts/dev/measure_plan_then_utter_pilot.sh`，只读 `storage/usage.db` 与 `storage/block_trace.db`；支持 `GROUP_ID / DAYS / COST_RATIO_MAX / PERSONA_DRIFT_MAX_PP / MIN_PILOT_PARENTS`。
- **指标口径**：baseline 按 `proactive/main` 单 reply 平均 token；pilot 按 `proactive_plan` 次数聚合 `proactive_plan + proactive_utter` 总 token，避免把一条主动回复拆成多 call 后低估成本。
- **PersonaScore**：优先读 `humanization_metrics` 表；缺表/缺 pilot 样本时输出 `no_sample`，不伪造结论。
- **验收证据计划**：`bash -n`、真实 DB all/灰度群实跑；新增临时 SQLite pytest 锁住 per-reply 成本口径与 no-sample 口径。

### P6.10 完成记录

- **代码**：新增 [scripts/dev/measure_plan_then_utter_pilot.sh](../../scripts/dev/measure_plan_then_utter_pilot.sh)，输出 `usage_cost_latency`、`block_trace_pairing`、`persona_score_baseline`、`rollout_gate` 四段。
- **测试**：新增 [tests/test_plan_then_utter_pilot_measure.py](../../tests/test_plan_then_utter_pilot_measure.py)，用临时 SQLite 覆盖有样本时 `avg_pilot_tokens_per_reply=(plan+utter)/plan_count`、配对 gate、persona gate，以及无样本时 `continue_pilot_collect_samples`。
- **实测**：14 日 all groups baseline `129` 个 proactive/main reply，pilot plan/utter `0/0`；`993065015` baseline `122`、pilot `0/0`；`984198159` baseline `5`、pilot `0/0`。
- **决策输出**：三组均为 `decision: continue_pilot_collect_samples`；`cost_gate/pairing_gate/persona_gate` 均为 `no_sample`，说明当前是未产生 A pilot 样本，不是 A 方案已失败。
- **自验**：`source ./scripts/dev/env.sh && uv run pytest -q tests/test_plan_then_utter_pilot_measure.py` → `2 passed`；`uv run ruff check tests/test_plan_then_utter_pilot_measure.py` → passed；`bash -n scripts/dev/measure_plan_then_utter_pilot.sh` → passed。
- **D1 grep**：`measure_plan_then_utter_pilot / proactive_plan / proactive_utter / avg_pilot_tokens_per_reply` 命中脚本、测试与本追踪文档。
- **D2 cancel-path**：N/A（只读 SQLite + 本地聚合，无异步运行状态和持久写）。
- **回滚**：删除/忽略脚本；运行时回滚仍为 `humanization.plan_then_utter.enabled=false` 或切 `profile=balanced/economy`。
- **回填**：§6 `P6.10` 已置 🟡，继续 P6.11。

### P6.11 领单拆分（执行前）

- **目标**：基于 P6.10 输出完成 A 方案决策门：上 / 不上 / 调参后再 pilot。
- **决策规则**：成本 > 2.5× baseline、BlockTrace 配对失败、PersonaScore 跌幅 > 5pp 任一命中则关闭 A；样本不足则不上默认也不永久关闭，继续 pilot 收集。
- **文档拆分**：回填本执行文档 §6 / §9，并推进主线 §10 状态说明。
- **验收证据计划**：复用 P6.10 脚本输出作为报告回执，D2 N/A。

### P6.11 完成记录

- **结论**：当前选择 **调参后再 pilot / 继续收集样本**。不把 A 方案升为默认，也不判定永久失败。
- **依据**：P6.10 真实 DB 14 日窗口中 `proactive_plan=0`、`proactive_utter=0`，缺少成本、latency、PersonaScore pilot 样本；`rollout_gate.decision=continue_pilot_collect_samples`。
- **执行口径**：保持 `humanization.plan_then_utter.enabled=false` 为默认；若后续开启灰度，必须先看到 `MIN_PILOT_PARENTS>=20` 且 cost/persona/pairing 三 gate 通过，再进入“上默认”讨论。
- **失败阈值**：单 reply `plan+utter` token 成本 > baseline 2.5×、PersonaScore 跌 >5pp、或 parent_span 配对失败，即回滚 `humanization.plan_then_utter.enabled=false` / `profile=balanced`。
- **D1 grep**：`P6.11 / continue_pilot_collect_samples / plan_then_utter.enabled=false` 命中本执行文档与主线 §10 状态。
- **D2 cancel-path**：N/A（决策文档，无运行时代码）。
- **回填**：§6 `P6.11` 已置 🟡，继续 P6.12。

### P6.12 领单拆分（执行前）

- **目标**：锁定 C reactive replan 暂搁，不开发、不灰度。
- **文档拆分**：在主线 §10 和执行文档记录锁定理由，保持 §5 “不实现 mid-stream resume” 结论不变。
- **锁定理由**：DeepSeek 不支持 stream continuation；abort 后已输出 token 与 reasoning replay 会引入因果链漂移；Part 5 / B / A 已覆盖主要拟人收益，C 的复杂度和浪费不成比例。
- **验收证据计划**：grep `C 方案 / stream continuation / 永不进灰度`；D2 N/A。

### P6.12 完成记录

- **结论**：C 方案继续锁定为 **不开发 / 不灰度**。
- **依据**：主线 §5 已写明 DeepSeek 不支持 “中断 SSE → 继续 SSE” 的 mid-stream resume；§1.3.4/§1.4 的 abort 成本分析显示 replan 后需要回填已见文本和 reasoning 上下文，因果链复杂度高。
- **边界**：后续如需“被打断后再说”，只走 D pause-then-extend 或 A plan-then-utter 的多 call 边界，不实现真正 mid-stream abort/resume。
- **D1 grep**：`C 方案 / DeepSeek 不支持 stream continuation / 永不进灰度` 命中主线与本执行文档。
- **D2 cancel-path**：N/A（仅文档锁定）。
- **回填**：§6 `P6.12` 已置 🟡，继续 P6.13。

### P6.13 领单拆分（执行前）

- **目标**：Part 6 执行收口，提供一键只读测量入口，并回填本执行追踪。
- **代码拆分**：新增 `scripts/dev/measure_humanization_part6.sh`，串联 P6.3 streaming、P6.7 pause-extend、P6.10 plan-then-utter 三个测量脚本。
- **文档拆分**：更新 §6 状态表和主线 §10；`maintenance-log.md` 当前共享脏改，本轮不触碰，避免混入其他人的未提交内容。
- **验收证据计划**：`bash -n` + 小 limit 实跑总览脚本；D2 N/A。

### P6.13 完成记录

- **代码**：新增 [scripts/dev/measure_humanization_part6.sh](../../scripts/dev/measure_humanization_part6.sh)，按顺序输出 P6.3 / P6.7 / P6.10 三个测量结果，每段带 `section_status`。
- **收口状态**：P6.9~P6.13 均已进入 🟡 待最终审查；P6.0.c 仍按用户口径“忽略前项未完成灰度内容”保留 ⏳，不阻塞本次 Part 6 后续交付。
- **自验**：`bash -n scripts/dev/measure_humanization_part6.sh` → passed；`LIMIT=5 bash scripts/dev/measure_humanization_part6.sh` → 三段均执行完成。
- **D1 grep**：`measure_humanization_part6 / P6.13 / section_status` 命中脚本与本追踪文档。
- **D2 cancel-path**：N/A（只读测量脚本，无运行态）。
- **回滚**：删除/忽略总览脚本；所有运行时回滚仍由各子功能 flag 控制。
- **回填**：§6 `P6.13` 已置 🟡。`maintenance-log.md` 因共享脏改未触碰，交由后续拆分提交时单独补条目。

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
