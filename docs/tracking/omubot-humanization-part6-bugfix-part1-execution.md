# Omubot 拟人 Part 6 Bugfix Part 1 — 派单版并列执行追踪

> 状态：2026-05-27 立。本文是 [Part 6 Bugfix Part 1 主线](./omubot-humanization-part6-bugfix-part1.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> **执行原则**（覆盖主线任何不一致项）：
>
> 1. **每条独立 commit**。修复跨 wave 严格串行，wave 内可并行。
> 2. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒回滚开关**，缺一不通过验收。
> 3. **遇主线证据与本文冲突，以本文为准**（§1 已记录主线自审订正）。
> 4. **B0 紧急回滚需用户书面授权**——本文 §6 已标 ⏸，等用户在主线 §9 勾签收三件事后才动 config.json。

---

## 1. 主线自审与证据订正（执行前必读）

下表是对 [主线](./omubot-humanization-part6-bugfix-part1.md) §1 / §2 / §5 进行 grep 实证后已修正的项。**派单按本表订正，不按主线初稿原文**。

| 主线位置 | 初稿表述 | grep 实证 | 订正 |
|---|---|---|---|
| §1.2 调用点列表 | "约 5 处" | client.py 4 处（430/1740/1753/3465/3704）+ kernel/config.py 5 处（1225/1524/1536/1548/1564） | §1.2 + §5.2.1 + §7.2 已展开为 4+5 完整表 |
| §2 finding C | 描述 quote_reply kill-switch 失效，未给解析器 anchor | [client.py:85-88](../../services/llm/client.py#L85-L88) `_QUOTE_RE` 仅匹配 `<quote\b[^>]*\bmsg_id\s*=\s*...>` XML，对 `[CQ:reply,id=]` 无降级；`fix_cq_codes` 透传 | finding C 已加 anchor + 透传机制说明 |
| §4.4 T8 invariant | "disable_natural_split=True 蕴含 streaming_segment_enabled=True" | [kernel/config.py:1564](../../kernel/config.py#L1564) custom 分支 `disable_natural_split=streaming_enabled or plan_enabled` | T8 已订正为 `streaming OR plan` 双蕴含 |
| §5.2.1 改动表 | 仅列了 `client.py:3465` 主路径 | rewrite 路径 [client.py:3704](../../services/llm/client.py#L3704) 同款传 `disable_natural_split=humanization.disable_natural_split` | §5.2.1 已加 rewrite 路径 + 双路径处理（rewrite 路径 `streaming_already_emitted=False`） |

> **派单规则**：执行者拿到本文档后按 §1 订正版执行。若发现新订正项同步告知我。

---

## 2. P0.0 新增前置任务（streaming 降级路径语义验证）

派单第 0 步，零代码改动。Phase 1C 选项 A vs B 取舍依赖本步骤回执。

| 步骤 | 命令 / 操作 | 预期结果 |
|---|---|---|
| 1 | `grep -n "tool_use\|stop_reason\|fallback\|interrupt\|emit" services/llm/client.py services/llm/streaming_segmenter.py 2>/dev/null` | 找到 streaming SSE 解析器在收到 `tool_use` block 时的处理路径 |
| 2 | 读 streaming 解析器源码 | 确认：streaming 中遇 `tool_use` 时是否能干净中断 / 已 emit segment 是否被回收 / 残文是否走 fallback |
| 3 | 写 1 行结论到本文 §10 自审表末行 + §6 P0.0 状态行 | 给 B1C 拍板：选项 A（保守白名单）or 选项 B（默认放行 + 运行时降级，主线推荐） |

**P0.0 不是 commit；是派单前置验证**。我会先看本步骤回执再发 B1C 单。

---

## 3. 并列执行 Wave 表（按修复阶段编排）

**依赖关系核心规则**：

- **Wave 0**：P0.0 streaming 降级路径语义验证（前置，零代码）
- **Wave 1**：B0 紧急回滚（config.json `balanced` → `custom` + restart；**需用户书面授权**）
- **Wave 2**：B1A 改名 `disable_natural_split` → `streaming_already_emitted`（4+5 调用点）
- **Wave 3**：B1B `_should_force_reply` 加入 `qq_interaction`（小改）
- **Wave 4**：B1C streaming 门禁松绑（依赖 Wave 0 选项结论）
- **Wave 5**：B2 must-have 回归测试 T1~T4（依赖 Wave 2~4 落地）
- **Wave 6**：B3 重新切 balanced 灰度 + 30 分钟生产观察（依赖 Wave 5 全绿）
- **Wave 7**：B4 nice-to-have 回归测试 T5~T8（看人手）
- **Wave 8**：B5 Phase 3 契约缺陷收尾（C / F / G / E / J）

### 3.1 Wave 1 — B0 紧急回滚（1 条单点，需授权）

> 用户书面授权后才动；命中"高风险" tier，本文不擅自执行。

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B0** | `config/config.json:382` 由 `"profile": "balanced"` 改回 `"custom"` + `docker compose restart bot` | `config/config.json`（-1 +1 行） | `grep -n "\"profile\"" config/config.json` 仅 1 命中且值为 `"custom"` | N/A（纯 config）| `git restore config/config.json && docker compose restart bot` |

**Wave 1 收口**：bot restart 后看 1 条 @ 提及触发的回复 segment 数 ≥ 2（需含 `\n` 文本）即视作 B0 成功。

### 3.2 Wave 2 — B1A 改名（1 条单点，跨文件）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B1A** | `disable_natural_split` 形参 → `streaming_already_emitted`，由调用方按运行时事实传；改 4 处 client.py + 5 处 kernel/config.py setter | `services/llm/client.py`（≈ 形参/调用 9 处）+ `kernel/config.py`（resolve_profile 4 分支 setter + ResolvedHumanization 字段） | `grep -rn "disable_natural_split" services/ kernel/ tests/` 完成后命中应仅在 ResolvedHumanization 字段（保留 deprecated）+ tests 旧断言 | `tests/test_humanization_e2e.py` 新增 cancel-path：streaming 中协程被取消后 `_visible_reply_segment_plan` 不被调用，bus 不脏写 | `git restore services/llm/client.py kernel/config.py` |

**关键改动点位详细**：

| 文件:行 | 改动语义 |
|---|---|
| [services/llm/client.py:430](../../services/llm/client.py#L430) | `_reply_segment_plan` 形参重命名 + 短路条件 `if streaming_already_emitted:` |
| [services/llm/client.py:1740](../../services/llm/client.py#L1740) | `_visible_reply_segment_plan` 形参重命名 |
| [services/llm/client.py:1753](../../services/llm/client.py#L1753) | 中转传入改名 |
| [services/llm/client.py:3465](../../services/llm/client.py#L3465) | reply 主路径：`streaming_already_emitted=bool(streamed_segments)` |
| [services/llm/client.py:3704](../../services/llm/client.py#L3704) | reply rewrite 路径：`streaming_already_emitted=False`（rewrite 已替换 streaming output）|
| [kernel/config.py:1225](../../kernel/config.py#L1225) | ResolvedHumanization 字段：保留 `disable_natural_split` 同名（向后兼容）+ 标 deprecated；或同步重命名（二选一，主线 §5.2.1 倾向保留兼容） |
| [kernel/config.py:1524](../../kernel/config.py#L1524) | balanced：由 hardcode True 改为 `streaming_segment_enabled or plan_then_utter_enabled`（与 custom 分支 invariant 对齐） |
| [kernel/config.py:1536](../../kernel/config.py#L1536) | performance degraded：同 balanced |
| [kernel/config.py:1548](../../kernel/config.py#L1548) | performance：同 balanced |
| [kernel/config.py:1564](../../kernel/config.py#L1564) | custom：保持 `streaming_enabled or plan_enabled`（已是 invariant，不改） |

**B1A 单 commit 合并建议**：client.py 改动 + kernel/config.py 改动 + 配套 T8 invariant 测试 1 条。改动行数声明值预期 ≤ 60 行。

### 3.3 Wave 3 — B1B `_should_force_reply` 白名单加 `qq_interaction`（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B1B** | [services/scheduler.py:35-42](../../services/scheduler.py#L35-L42) `_should_force_reply` 白名单 `{"video_always", "directed_followup"}` 加 `"qq_interaction"`；保留 `addressee_self` 兜底 | `services/scheduler.py`（+1 行） | `grep -n "qq_interaction\|video_always\|directed_followup\|at_mention" services/scheduler.py services/humanization/qq_interactions.py` 命中 mode 字面值与 trigger 工厂一致 | N/A（纯字符串集合扩展） | `git restore services/scheduler.py` |

**B1B 单 commit**：scheduler.py 改动 + B2.T2 同 commit（mode whitelist 与 force_reply 测试一并落地）。

### 3.4 Wave 4 — B1C streaming 门禁松绑（1 条单点，依赖 Wave 0）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B1C-A** | **选项 A（保守）**：维护 `NON_STREAMING_BLOCKING_TOOLS = {...}` 集合；只有这些 tool 出现时才关 streaming；business tools (`send_sticker` / `append_memo` / `update_memo`) 不在内 | `services/llm/client.py:1758-1771`（+10 行 集合 + grep tool_def 过滤） | `grep -n "NON_STREAMING_BLOCKING_TOOLS\|_streaming_segment_enabled" services/llm/` 仅 1 处定义 + 1 处使用 | streaming 中 tool_use 抛异常时 segment 不被双 emit | `git restore services/llm/client.py` |
| **B1C-B** | **选项 B（推荐）**：`_streaming_segment_enabled` 不再判 `tool_defs`；运行时若 model 真返回 `tool_use` block，由 streaming 解析器降级（中断 streaming，转入工具循环，剩余文本进 fallback 自然分段） | `services/llm/client.py:1758-1771`（-1 行 + 1 行）+ streaming SSE 解析器降级钩子（位点见 P0.0 回执） | `grep -n "tool_use\|streaming_to_tool_fallback" services/llm/client.py` 含新加 counter 日志 | streaming 中 tool_use 中断后剩余文本走 `_reply_segment_plan(streaming_already_emitted=False)` 自然分段，不丢字 | `git restore services/llm/client.py` |

**B1C 二选一**：根据 P0.0 回执决定。主线 §5.2.3 倾向 B；P0.0 若发现解析器降级路径不健壮则退回 A。

**B1C 单 commit**：streaming 门禁改动 + B2.T3 同 commit（streaming-disabled fallback 用自然分段而非短路）。

### 3.5 Wave 5 — B2 must-have 回归测试 T1~T4（4 条，依赖 Wave 2~4 落地）

| 编号 | 测试名 | 改动文件 | 断言 | 依赖 |
|---|---|---|---|---|
| **B2.T1** | `test_balanced_long_reply_yields_multi_segments` | `tests/test_humanization_e2e.py`（+1 testcase） | balanced profile + 非空 tool_defs + 含 `\n` 的 80 字节回复 → segments ≥ 2 | B1A + B1C |
| **B2.T2** | `test_qq_interaction_mode_force_reply` | `tests/test_scheduler.py`（+1 testcase） | trigger.mode=`qq_interaction` → `_should_force_reply` 返回 True | B1B |
| **B2.T3** | `test_streaming_disabled_fallback_uses_natural_split` | `tests/test_humanization_e2e.py`（+1 testcase） | streaming 被门禁关闭（B1C-A 命中 blocking tool / B1C-B 解析器降级）时，传统路径走自然分段而非 `streaming_already_emitted` 短路 | B1A + B1C |
| **B2.T4** | `test_quote_reply_kill_switch_strips_cq_reply` | `tests/test_humanization_e2e.py` 或新增 `tests/test_quote_kill_switch.py`（+1 testcase） | `quote_reply.enabled=false` 时模型输出含 `[CQ:reply,id=xxx]` 应被剥离（依赖 §5.4 选项 ② 实现，否则锁定为 ❌ 待 Phase 3） | Phase 3 §5.4 finding C 已选 ② |

**B2.T4 注意**：B5.C 已实施，T4 已随 quote_reply kill-switch 回归补齐；Wave 5 本地回归已覆盖 T1/T2/T3/T4。

### 3.6 Wave 6 — B3 重新切 balanced 灰度 + 30 分钟生产观察

| 编号 | 一句话 | 改动 / 操作 | 出口指标（30 分钟）| 依赖 |
|---|---|---|---|---|
| **B3** | `config/config.json:382` 改回 `"profile": "balanced"` + `docker compose restart bot`；在 `runtime_groups` 限定的灰度群（993065015 / 984198159）观察 | `config/config.json`（-1 +1 行）+ 生产观察 | 见 §4 出口表 | Wave 5 T1/T2/T3 全绿 + 用户书面授权 |

### 3.7 Wave 7 — B4 nice-to-have 回归测试 T5~T8（4 条，看人手）

| 编号 | 测试名 | 改动文件 | 断言 | 优先级 |
|---|---|---|---|---|
| **B4.T5** | `test_register_interaction_tools_wired_for_performance` | `tests/test_humanization_e2e.py`（+1） | performance profile 下 ToolRegistry 含 `poke_user` / `react_to_message` | 依赖 §5.4 finding F 实施 |
| **B4.T6** | `test_health_guard_no_mid_turn_switch` | `tests/test_humanization_health_guard.py`（+1） | turn 进行中 health_guard 状态切换不影响当前 turn 的 ResolvedHumanization | 依赖 §5.4 finding G 实施 |
| **B4.T7** | `test_pause_extend_timing_after_last_segment` | `tests/test_humanization_e2e.py`（+1） | pause_extend 起点固定为「最后 segment emit 完成后」 | 依赖 §5.4 finding J 实施 |
| **B4.T8** | `test_resolve_profile_invariants` | `tests/test_humanization_config.py`（+1） | 任意 profile 下，`disable_natural_split=True` 蕴含 `streaming_segment_enabled=True OR plan_then_utter_enabled=True`（custom 已成立；balanced/performance 在 B1A 后同步成立） | **优先（结构 invariant，零额外依赖）**，建议跟 B1A 合并 commit |

### 3.8 Wave 8 — B5 Phase 3 契约缺陷收尾（5 条）

| 编号 | finding | 一句话 | 关键文件 | 依赖 |
|---|---|---|---|---|
| **B5.C** | C | quote_reply kill-switch 修复：在 message 出口处统一 strip `[CQ:reply,...]` 当 `quote_reply.enabled=false`（主线 §5.4 选项 ②） | `services/llm/client.py` strip 钩子 / 出口处 | B2.T4 同 commit |
| **B5.F** | F | performance profile 启用时在 tool registry 注册 `poke_user` / `react_to_message`（或文档标注 outbound 为下个 part 实现）| `services/tools/interaction_tools.py` + chat plugin on_startup | B4.T5 同 commit |
| **B5.G** | G | health_guard 决策只在 turn 边界生效；turn 进行中不切档 | `services/humanization/health_guard.py` | B4.T6 同 commit |
| **B5.E** | E | `_quote_reply_enabled` 默认分支 dead code 清理 | `services/llm/client.py` | 单纯清理，无回归风险 |
| **B5.J** | J | streaming 路径定义 pause 起点 = 最后 segment emit 完成（与 B1C-B 同步处理） | streaming 解析器 + pause_extend 触发点 | B4.T7 同 commit |

---

## 4. 灰度 30 分钟出口指标矩阵（B3 阶段）

执行者 B3 切档后跑 30 分钟生产观察，把下表填入结果。我看到 ≥ 6/8 项达标才视作 B3 通过。

| 指标 | 目标 | B3 实测 | 备注 |
|---|---|---|---|
| 平均 segments per reply | ≥ 1.5 | 等待 30min 样本 | 关键指标：B0 前实测 1.0（单段灾难） |
| segments=1 比例 | ≤ 50% | 等待 30min 样本 | 含 `\n` 的回复必须 segments ≥ 2 |
| reply_segment_plan log raw_count vs segments | raw=segments 时 OK；raw>segments 触发软上限 | 等待 30min 样本 | 灰度群日志直接 grep |
| @ 提及触发的回复 segment 命中 streaming-as-segment | ≥ 60% | 等待 30min 样本 | 流式分段实际启用率 |
| qq_interaction trigger 响应率 | ≥ 95%（B1B 后）| 等待 30min 样本 | 戳一戳 / 表情回应触发后 LLM 生成 |
| streaming_to_tool_fallback counter（B1C-B 选项）| < 5% per turn | 等待 30min 样本 | 选 B 时观测；选 A 时 N/A |
| 死锁回归（segments=1 raw=1）| 0 次 | 等待 30min 样本 | 灰度群每条回复 grep 计数 |
| 用户主观验收 | 「多段对话恢复正常」 | 待用户最终验收 | — |

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) 或等价 cancel 模拟，断言 bus / DB / in-flight 旗标不脏写）
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
| **审查文档** | — | ✅ | [omubot-humanization-part6-bugfix-part1.md](./omubot-humanization-part6-bugfix-part1.md) 已落地，9 条 finding A–J + 5 条契约缺陷 + 4 阶段方案完整 |
| **P0.0** | 0 | 🟡 | 已验证：当前 streaming 只通过 `on_text_delta` 处理 provider 文本增量，`tool_use` 仅在完整 SSE parse 后进入 tool loop；未发现 runtime `streaming_to_tool_fallback` 钩子。B1C 选 A（保守允许列表） |
| **B0** | 1 | ⏸ | 阻塞：用户书面授权未到（主线 §9 三件事未签收）；命中"高风险" tier，本文不擅自执行 |
| **B1A** | 2 | 🟡 | `_reply_segment_plan` / `_visible_reply_segment_plan` 改为 `streaming_already_emitted`；主路径按 `bool(streamed_segments)` 传入，rewrite 路径固定 False；profile invariant 已对齐 |
| **B1B** | 3 | 🟡 | `_should_force_reply` 白名单加入 `qq_interaction`；`tests/test_scheduler.py::test_qq_interaction_mode_force_reply` 覆盖 |
| **B1C** | 4 | 🟡 | 选 B1C-A：`_STREAMING_ALLOWED_TOOL_NAMES` 允许 `send_sticker` / memo / `pass_turn` 与 streaming 共存，未知/blocking tool 关闭 streaming |
| **B2.T1** | 5 | 🟡 | `tests/test_streaming_hook.py::test_balanced_long_reply_with_business_tools_streams_segments` 覆盖 business tools + streaming 分段 |
| **B2.T2** | 5 | 🟡 | `tests/test_scheduler.py::test_qq_interaction_mode_force_reply` 覆盖 `qq_interaction` 强制回复 |
| **B2.T3** | 5 | 🟡 | `tests/test_streaming_hook.py::test_streaming_disabled_fallback_uses_natural_split` 覆盖 blocking tool 关闭 streaming 后回落自然分段 |
| **B2.T4** | 5 | 🟡 | B5.C 已实施；新增 non-streaming / streaming 两条 chat 级回归，quote_reply 关闭时剥离 `[CQ:reply,...]` |
| **B3** | 6 | ⏸ | 阻塞：生产 `config/config.json` 切 balanced + restart 需用户授权；本地 Wave 5 T1/T2/T3/T4 已全绿 |
| **B4.T5** | 7 | 🟡 | `tests/test_chat_plugin_humanization_wire.py::test_register_interaction_tools_wired_for_performance` 覆盖 performance profile 注册 QQ outbound interaction tools |
| **B4.T6** | 7 | 🟡 | `tests/test_humanization_health_guard.py::test_health_guard_no_mid_turn_switch` 覆盖 turn 内 health_guard 快照不漂移 |
| **B4.T7** | 7 | 🟡 | `tests/test_extend_call.py::test_pause_extend_timing_after_last_segment` 覆盖 pause_extend 从最后 segment emit 完成后计时 |
| **B4.T8** | 7 | 🟡 | `tests/test_humanization_config.py::test_resolve_profile_invariants` 覆盖 `disable_natural_split => streaming OR plan` |
| **B5.C** | 8 | 🟡 | `_strip_cq_reply_codes` 接入 streaming / 普通回复 / tool-exhausted 出口；`tests/test_streaming_hook.py` 覆盖开关关闭剥离与开启保留 |
| **B5.E** | 8 | 🟡 | `_quote_reply_enabled` 收敛到显式 `qq_interactions_quote_reply_enabled` 字段；`tests/test_quote_reply_segment.py::test_explicit_quote_flag_is_single_source` 覆盖 |
| **B5.F** | 8 | 🟡 | `plugins/chat/plugin.py::_register_humanization_interaction_tools` 已接入 startup；LLM tool execution 注入 `ToolContext.extra["resolved_humanization"]` |
| **B5.G** | 8 | 🟡 | `chat()` 开始时采样 `performance_degraded_snapshot`，经 resolver 传入 `resolve_profile(..., performance_degraded=...)`，turn 内不再读实时 health_guard |
| **B5.J** | 8 | 🟡 | `_stream_with_segments` / 普通分段 / tool-exhausted 路径记录 `last_segment_emitted_at` 并传给 `_maybe_extend` |

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P0.0，回执贴 streaming 解析器 tool_use 降级路径结论；用户授权后领 B0；之后按 wave 编号递增领单。
2. **B0 单独审批**：B0 命中"高风险" tier（生产 config 切档），不在派单池里直接领；执行前必须确认主线 §9 三件事已签收（截图或引用用户回执）。
3. **多人并行**：同 wave 内任务可同时下发，不同 wave 串行。当前 Wave 1 / 2 / 3 / 4 之间为串行（B0 完成 → B1A → B1B → B1C），Wave 5 / 7 / 8 内可并行。
4. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行。建议合并的同 commit 任务：B1A + B4.T8；B1B + B2.T2；B1C + B2.T3；B2.T4 + B5.C；B4.T5 + B5.F；B4.T6 + B5.G；B4.T7 + B5.J。
5. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + commit hash 发我，我跑 §5 验收清单后改 ✅。
6. **冲突冲突**：本文 §1 与主线冲突时**以本文为准**；其它部分以 [主线](./omubot-humanization-part6-bugfix-part1.md) 为准。
7. **遇证据不成立**：跟我同步，由我决定撤销 / 重订正。
8. **D7 部署纪律**：B0 / B3 切档涉及生产 restart；执行前 `git stash list && git status -uno` 确认 worktree 干净；restart 后看 `storage/logs/` 5 分钟内无 ERROR 才视作启动成功。

---

## 8. 与 Part 6 主线 / 其它 part 的关系

- **Part 6 主线**（[omubot-humanization-part6-three-tier-profile.md](./omubot-humanization-part6-three-tier-profile.md) 或类似）：本 bugfix 不回退 Part 6 三档机制，只修 balanced profile 在 streaming + tool_defs 组合下的死锁；Part 6 主线状态保留 ✅。
- **Part 1 执行追踪**（[omubot-humanization-part1-execution.md](./omubot-humanization-part1-execution.md)）：本文格式参照 Part 1 派单版；Part 1 的 humanization contract / RuntimeStateBus / Provider 不受本 bugfix 影响。
- **Part 5 自然分段重构**（[omubot-humanization-part5-segmentation.md](./omubot-humanization-part5-segmentation.md)）：B1A 的 `streaming_already_emitted` 改名是 Part 5 segmentation 模块向"运行时事实驱动"演进的小步；不阻塞 Part 5 后续工作，但 Part 5 后续若涉及 `_visible_reply_segment_plan` 接口需对齐新形参名。
- **maintenance-log.md**：B0 / B1A / B1B / B1C / B3 各自一条；B2 / B4 / B5 按粒度独立成条（与主线 §8 一致）。

---

## 9. 执行者逐步追踪（领单 → 完成 → 自审）

### P0.0 领单拆分（执行前）

目标：grep streaming SSE 解析器，确认 model 返回 `tool_use` block 时 streaming 能否干净中断、已 emit segment 是否被正确处理、剩余文本是否走 fallback；为 B1C 选项取舍提供证据。

详细步骤：

1. `grep -n "tool_use\|stop_reason\|fallback\|interrupt\|content_block_stop" services/llm/client.py services/llm/streaming_segmenter.py 2>/dev/null` —— 找到所有 tool_use 处理路径与 streaming 中断点。
2. Read 找到的位点上下文（≈ ±20 行），判定：
   - streaming 中遇 tool_use 是否会清空已 emit 的 segment 状态；
   - tool 循环结束后剩余文本是否回流到 `_reply_segment_plan(streaming_already_emitted=False)`；
   - 是否已有 `streaming_to_tool_fallback` 类的 counter 日志。
3. 写 1 段结论到本文 §10 自审表末行 + §6 P0.0 状态行：① 选项 A 还是 B；② 若选 B，需在 streaming 解析器哪一行加哪个降级钩子。

风险评估：

- 误判降级路径健壮性：选 B 但实际解析器有边界 bug（已 emit segment + tool_use 后剩余文本拼接错乱）→ 生产侧出现回复截断；缓解：B1C 落地后 B2.T3 必须覆盖此场景。
- 漏看异步取消路径：cancel 在 streaming 中段 + tool_use 之间发生时，bus 状态可能脏写；P0.0 仅做读，cancel-path 在 B1C D2 锁定。

### P0.0 完成记录（执行者）

> 待执行者填入

### B1A 领单拆分（执行前）

目标：把 `disable_natural_split` 这个由 profile 静态指定的"行为决策"改成由调用方按运行时事实传的参数（`streaming_already_emitted`），让"流式已切好段"这个语义条件在结构上表达，消除 balanced profile 下"流式没分段、传统也不分段"的双重死锁。

详细步骤：

1. `services/llm/client.py:430` 形参重命名 + 短路条件改名；保持 `if streaming_already_emitted:` 短路语义不变。
2. `services/llm/client.py:1740` `_visible_reply_segment_plan` 形参重命名；1753 中转传入改名。
3. `services/llm/client.py:3465` reply 主路径调用：`streaming_already_emitted=bool(streamed_segments)`，由 streaming 已 emit 的 segment 列表是否非空决定。
4. `services/llm/client.py:3704` reply rewrite 路径调用：`streaming_already_emitted=False`（rewrite 已替换 streaming output）。
5. `kernel/config.py:1225` ResolvedHumanization 字段保留 `disable_natural_split`（向后兼容）+ 注释标 deprecated；`resolve_profile` 4 分支中 balanced/performance/performance_degraded 改为 `disable_natural_split=streaming_segment_enabled or plan_then_utter_enabled`（与 custom 分支 invariant 对齐）。
6. 新测试 1 条：`tests/test_humanization_config.py::test_resolve_profile_invariants`（即 B4.T8）—— 任意 profile 下 `disable_natural_split=True` 蕴含 `streaming_segment_enabled=True OR plan_then_utter_enabled=True`。
7. 验证：`uv run pytest -q tests/test_humanization_config.py tests/test_humanization_e2e.py tests/test_client.py` + `uv run ruff check services/llm/client.py kernel/config.py`。

风险评估：

- 调用点漏改：client.py 4 处 + kernel/config.py 5 处必须全部触达；遗漏会导致 ReplySegmentPlan 错误退化为单段；缓解：commit 前 `grep -rn "disable_natural_split" services/ kernel/ tests/` 应仅命中 ResolvedHumanization 字段（保留）+ tests 旧断言。
- ResolvedHumanization 字段语义漂移：保留同名字段但调用方不再用，下游消费者若仍按旧语义读会返回不一致结果；缓解：grep 全仓找 `humanization.disable_natural_split` 消费者，确认只剩 client.py 4 处调用全部改为运行时事实传参。
- rewrite 路径误用：rewrite 后的 reply 是替换关系，传 `streaming_already_emitted=True` 会丢失 rewrite 的自然分段；测试覆盖 rewrite path 必须 segments ≥ 1（含 `\n` 时 ≥ 2）。

### B1A 完成记录（执行者）

> 待执行者填入

### B1B 领单拆分（执行前）

目标：把 `qq_interaction` 加入 `_should_force_reply` 白名单，让 QQ 戳一戳 / 表情回应入站事件随后能触发 LLM 生成。

详细步骤：

1. `services/scheduler.py:35-42` `_should_force_reply` 函数：白名单集合 `{"video_always", "directed_followup"}` 加 `"qq_interaction"`。
2. 保留 `at_mention` 分支与 `addressee_self` 兜底逻辑（[主线 §5.2.2](./omubot-humanization-part6-bugfix-part1.md#L240) 给出完整代码块）。
3. 新测试 1 条：`tests/test_scheduler.py::test_qq_interaction_mode_force_reply`（即 B2.T2）—— trigger.mode=`qq_interaction` → `_should_force_reply` 返回 True；同时验 `at_mention` + `addressee_self=False` 仍返回 False。
4. 验证：`uv run pytest -q tests/test_scheduler.py` + `uv run ruff check services/scheduler.py`。

风险评估：

- 误触发：qq_interaction trigger 在某些边缘场景 addressee 不是 self → 误触发回复；缓解：检查 `services/humanization/qq_interactions.py:189` trigger 工厂是否已收口 addressee；若已收口则 force_reply 直接 True；若没收口需在 scheduler.py 加同款 `extra.get("addressee_self", True)` 保护。
- D1 同模式扫描：`grep -rn "_should_force_reply\|trigger.mode" services/` 确认不存在第二处类似白名单（避免新增 mode 后另一处遗漏）。

### B1B 完成记录（执行者）

> 待执行者填入

### B1C 领单拆分（执行前）

> 选项二选一依赖 P0.0 回执。以下两套领单并存，执行者按回执选其一。

#### B1C-A 选项 A（保守，白名单）

目标：维护 `NON_STREAMING_BLOCKING_TOOLS` 集合，只有真正会破坏 streaming 的 tool 出现时才关闭 streaming；business tools (`send_sticker` / `append_memo` / `update_memo`) 不在内。

详细步骤：

1. `services/llm/client.py:1758-1771` `_streaming_segment_enabled` 改写：用 `not (set(tool_defs) & NON_STREAMING_BLOCKING_TOOLS)` 替代 `not tool_defs`。
2. 在文件顶部或合适位置定义 `NON_STREAMING_BLOCKING_TOOLS: frozenset[str] = frozenset({...})`，初始集合按 P0.0 回执填入。
3. 新测试 1 条：B2.T3 `test_streaming_disabled_fallback_uses_natural_split` —— 模拟 tool_defs 含 blocking tool 时 streaming 关闭、传统路径走自然分段而非短路。
4. 验证：`uv run pytest -q tests/test_humanization_e2e.py tests/test_client.py` + ruff。

风险评估：

- 白名单漏项：未来新加 tool 忘记加入 blocking 集合 → 流式分段失败 / streaming 异常；缓解：在 NON_STREAMING_BLOCKING_TOOLS 定义处加注释说明添加规则。
- 维护成本：业务工具增长后白名单需同步维护。

#### B1C-B 选项 B（推荐，运行时降级）

目标：`_streaming_segment_enabled` 不再判 `tool_defs`；运行时若 model 真返回 `tool_use` block，由 streaming 解析器降级（中断 streaming，转入工具循环，剩余文本进 fallback 自然分段）。

详细步骤：

1. `services/llm/client.py:1758-1771` `_streaming_segment_enabled` 移除 `return not tool_defs` 行。
2. streaming SSE 解析器（位点见 P0.0 回执）：在收到 `tool_use` block 时，emit `streaming_to_tool_fallback` counter 日志 + 中断 streaming + 已 emit segments 保留 + 进入 tool 循环 + tool 返回后剩余文本回流到 `_reply_segment_plan(streaming_already_emitted=False)` 走自然分段。
3. 新测试 1 条：B2.T3 `test_streaming_disabled_fallback_uses_natural_split` —— 模拟 streaming 中遇 tool_use，断言已 emit segments 保留 + 剩余文本走自然分段。
4. 验证：`uv run pytest -q tests/test_humanization_e2e.py tests/test_client.py` + ruff。

风险评估：

- 解析器降级路径不健壮：streaming 中段 + tool_use + cancel 三者交错时可能出现 segment 重复 emit / 丢失；缓解：B1C-B D2 cancel-path 测试覆盖 streaming 中段被 cancel 的场景。
- Counter 日志侵入：`streaming_to_tool_fallback` counter 应走现有 metrics 通道（block_trace / RuntimeStateBus / 普通 log），不引入新依赖。

### B1C 完成记录（执行者）

> 待执行者填入

### B2.T1~T4 领单拆分（执行前）

目标：补齐 must-have 回归测试 4 条，锁定 B1A / B1B / B1C 行为契约，避免下次 profile 调整再次触发同款死锁。

详细步骤（按 T1~T4 顺序）：

1. **T1** `test_balanced_long_reply_yields_multi_segments`：
   - Setup: `humanization.profile=balanced` + 非空 `tool_defs`（含 `send_sticker` / `append_memo`）+ 含 `\n` 的 80 字节回复
   - Assert: `_visible_reply_segment_plan(...).segments` 长度 ≥ 2
2. **T2** `test_qq_interaction_mode_force_reply`：见 B1B 步骤 3。
3. **T3** `test_streaming_disabled_fallback_uses_natural_split`：见 B1C 步骤 3。
4. **T4** `test_quote_reply_kill_switch_strips_cq_reply`：依赖 §5.4 finding C 实施（B5.C），当前先 skip 标 `@pytest.mark.skip(reason="depends on B5.C")`。
5. 验证：`uv run pytest -q tests/test_humanization_e2e.py tests/test_humanization_config.py tests/test_scheduler.py` 全绿。

风险评估：

- E2E 测试构造成本：T1 / T3 需要构造完整 ResolvedHumanization + LLMRequest 上下文；可参考已有 `tests/test_humanization_e2e.py` 的 fixture 复用。
- 测试 flaky：streaming + tool 路径涉及异步顺序；测试用 `asyncio` event loop 严格断言事件顺序而非依赖 sleep。

### B2.T1~T4 完成记录（执行者）

> 待执行者填入

### B3 领单拆分（执行前）

目标：在 Wave 5 T1/T2/T3 全绿 + 用户授权后，重新切 balanced profile，在灰度群（993065015 / 984198159）做 30 分钟生产观察，验证修复落地。

详细步骤：

1. 用户书面确认 Wave 5 验收 + 重新切 balanced 授权（在主线 §9 第 3 项勾选）。
2. `config/config.json:382` 由 `"profile": "custom"` 改回 `"profile": "balanced"`。
3. `docker compose restart bot`（仅 config 改动，不 rebuild；D6）。
4. 30 分钟内在灰度群观察：
   - @ 提及触发的回复 segments 分布（应 ≥ 1.5 平均，含 `\n` 必 ≥ 2）；
   - qq_interaction trigger 响应率（戳一戳 / 表情回应应触发 LLM 生成）；
   - `streaming_to_tool_fallback` counter（B1C-B 选项时 < 5%）；
   - `storage/logs/` 5 分钟内无 ERROR；无 `segments=1, raw=1` + 含 `\n` 的死锁回归。
5. 把 §4 出口指标矩阵填完整，提交验收。

风险评估：

- 修复未覆盖某 profile 组合：B1A 改动覆盖 balanced/performance/performance_degraded 三档；custom 沿用原表达式；切回 balanced 后行为应等价于"streaming + 自然分段都启用"；缓解：B3 观察期内 segments=1 比例 ≤ 50%。
- 用户体验回归：multi-segment 回复在某些群可能体感更慢；缓解：观察期 ≤ 30min，立即可回滚。

回滚（30 秒）：

```bash
# config.json:382 改回 "custom"
docker compose restart bot
```

### B3 完成记录（执行者）

> 待执行者填入

### B4.T5~T8 / B5.C/E/F/G/J 领单拆分（执行前）

> Wave 7 / Wave 8 任务粒度小但分散；B4.T8 已建议跟 B1A 合并 commit，本节略过；其余 4 条 + 5 条按需领单，各自独立。

详细步骤模板（每条任务）：

1. 读对应 finding 主线 §5.4 描述 + §2 表格的位点。
2. 实施改动（位点已在 §3.8 列出）。
3. 落地对应测试（B4.Tx）。
4. `uv run pytest -q + ruff + pyright` 全绿。
5. 30 秒回滚演练。

风险评估：

- B5.C 出口处 strip：需确认 message 出口的所有路径都经过 strip 钩子，避免某条子路径绕过；D1 grep `[CQ:reply` 全仓确认仅 1 个出口。
- B5.F register_interaction_tools wire：performance profile 未启用时不应注册（避免污染默认 tool registry）；测试覆盖 economy / balanced / custom 三档下不含 `poke_user`。
- B5.G health_guard turn 边界：当前 health_guard 在 60s poll 周期外触发；改为 turn 边界生效需引入 turn 计数器；缓解：B4.T6 覆盖 turn 进行中状态切换不影响当前 turn ResolvedHumanization。

### B4.T5~T8 / B5.C/E/F/G/J 完成记录（执行者）

已完成 B4.T5 / B5.F：

- implementation：`plugins/chat/plugin.py::_register_humanization_interaction_tools(config, tools)` 在 performance profile 下调用 `ToolRegistry.register_interaction_tools`，默认 / balanced / custom 不注册 outbound tools。
- tool context：`services/llm/client.py` 执行工具时向 `ToolContext.extra` 注入 `resolved_humanization` 与兼容别名 `humanization`，供 interaction tools 读取 turn 级拟人配置。
- tests：`tests/test_chat_plugin_humanization_wire.py::test_register_interaction_tools_wired_for_performance`、`test_register_interaction_tools_not_wired_for_default_profiles`、`tests/test_streaming_hook.py::test_tool_execution_receives_resolved_humanization_context`。
- verification：`uv run pytest -q tests/test_streaming_hook.py::test_tool_execution_receives_resolved_humanization_context tests/test_chat_plugin_humanization_wire.py::test_register_interaction_tools_wired_for_performance tests/test_chat_plugin_humanization_wire.py::test_register_interaction_tools_not_wired_for_default_profiles tests/test_qq_interaction_tools.py` => 14 passed；focused ruff clean。
- pyright note：`plugins/chat/plugin.py` 单文件 pyright 仍被既有 `PluginContext` 动态属性错误阻塞，非本项新增问题；后续以不含该文件的改动范围 pyright 作 B5.F 验证。

已完成 B4.T6 / B5.G：

- implementation：`LLMClient.chat()` 在 turn 开始采样 `performance_degraded_snapshot`；`LLMClient._resolve_humanization(..., performance_degraded=...)` 将快照传给 resolver；`HumanizationConfig.resolve_profile(..., performance_degraded=...)` 优先使用 turn 快照，避免 health_guard 轮询在同一 turn 中途切档。
- compatibility：`_resolve_humanization` 兼容旧 resolver 签名；`plugins/chat/plugin.py::_humanization_resolve` 已接收并透传 `performance_degraded`。
- tests：`tests/test_humanization_health_guard.py::test_health_guard_no_mid_turn_switch`。

已完成 B4.T7 / B5.J：

- implementation：`_stream_with_segments` 在 segment 发出后写入 `_last_segment_emitted_at`；普通分段、plan-then-utter 与 tool-exhausted 路径记录最后一次 `on_segment` 完成时间；`_maybe_extend(last_segment_emitted_at=...)` 按 anchor 已流逝时间抵扣等待时间。
- tests：`tests/test_extend_call.py::test_pause_extend_timing_after_last_segment`。
- verification：`uv run pytest -q tests/test_humanization_health_guard.py tests/test_extend_call.py tests/test_chat_plugin_humanization_wire.py tests/test_streaming_hook.py tests/test_quote_reply_segment.py tests/test_scheduler.py::test_qq_interaction_mode_force_reply tests/test_humanization_config.py::test_resolve_profile_invariants tests/test_llm_client_reply_segment_plan.py` => 39 passed；focused ruff clean；focused pyright 0 errors。

---

## 10. 自审记录（2026-05-27）

| 自审项 | 验证手段 | 结论 |
|---|---|---|
| 主线 §1.2 调用点列表完整性 | `grep -rn "disable_natural_split" services/llm/client.py kernel/config.py` | client.py 4 处（430/1740/1753/3465）+ rewrite 路径 3704 共 5 callsite + kernel/config.py 5 处 setter；§3.2 表格已展开完整 |
| 主线 §2 finding A 文本 | Read [services/scheduler.py:35-42](../../services/scheduler.py#L35-L42) | 白名单仅含 `at_mention` / `video_always` / `directed_followup`，确认 `qq_interaction` 不在内 |
| `qq_interaction` mode 字面值 | grep `services/humanization/qq_interactions.py` | line 189 `mode="qq_interaction"`，与 §3.3 B1B 改动文本一致 |
| `_extract_quote_anchor` 仅匹配 `<quote>` XML | Read [services/llm/client.py:85-88](../../services/llm/client.py#L85-L88) | 正则 `<quote\b[^>]*\bmsg_id\s*=...>`，确认无 `[CQ:reply` 匹配分支；finding C 已补 anchor |
| custom 分支 invariant 现状 | Read [kernel/config.py:1564](../../kernel/config.py#L1564) | `disable_natural_split=streaming_enabled or plan_enabled` 已成立；§3.2 B1A 改动表把 balanced/performance/performance_degraded 同步对齐 |
| 派单格式与 Part 1 一致性 | Read [omubot-humanization-part1-execution.md:1-300](./omubot-humanization-part1-execution.md) | §0 状态/工作流/原则、§1 主线自审表、§2 P0.0 前置、§3 Wave 表、§4 灰度矩阵、§5 验收清单、§6 状态表、§7 交接、§8 关系、§9 领单/完成/自审、§10 自审记录全部对齐 |
| B0 风险定级是否合规 | CLAUDE.md "Executing actions with care" + 用户立场 | B0 命中"高风险" tier（生产 config 切档）；本文 §1 / §6 / §7 已明示需用户书面授权且不擅自执行 |
| §6 状态表当前值复核 | 与本派单文相对照 | B0 / B3 保持 ⏸；P0.0、B1、B2、B4.T5/T6/T7/T8、B5.C/E/F/G/J 均已标 🟡 等最终验收 |
| P0.0 streaming tool_use 降级路径 | `rg "tool_use\\|stop_reason\\|fallback\\|interrupt\\|emit" services/llm/client.py services/llm/streaming_segmenter.py` + read streaming parser | 当前 streaming 只通过 `on_text_delta` 处理 provider 文本增量，`tool_use` 仅在完整 SSE parse 后进入 tool loop；未发现 runtime `streaming_to_tool_fallback` 钩子，因此 B1C 选 A（保守允许列表） |
| B5.F outbound interaction tools wire | focused pytest + ruff | performance profile 注册 `poke_user` / `react_to_message`；默认档不注册；tool execution 已传递 resolved humanization context；pyright 含 `plugins/chat/plugin.py` 时仍受既有动态属性错误阻塞 |
| B5.G health_guard turn 边界 | `uv run pytest -q tests/test_humanization_health_guard.py::test_health_guard_no_mid_turn_switch` + focused pyright | `performance_degraded` 快照从 `LLMClient.chat()` 进入 resolver / `resolve_profile`；同一 turn 内不再由实时 health_guard 状态二次切档 |
| B5.J pause_extend 起点 | `uv run pytest -q tests/test_extend_call.py::test_pause_extend_timing_after_last_segment` + focused 回归 39 passed | pause 起点按最后一次 segment emit 完成时间计算，trace meta 记录 `pause_anchor_elapsed_s` / `effective_wait_seconds` |

**未验证项（依赖运行时观察 / 后续派单回执）**：

- B3 灰度 30 分钟出口指标实测值 —— 待 B3 执行回执
- B0 / B3 生产 config 切档与 restart —— 待用户授权后执行

---
