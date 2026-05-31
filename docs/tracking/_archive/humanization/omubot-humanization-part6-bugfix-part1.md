# Humanization Part 6 Bugfix Part 1 — balanced 灰度故障审查与修复方案

> 编制：2026-05-27
> 触发：balanced profile 上线后生产观察到「所有回复一段话、多段对话表现极差」
> 状态：审查完成，修复方案待实施（Phase 0/1/2/3 分级）

---

## 0. 背景与症状

### 0.1 上下文

- 2026-05-27 当日：Part 6 三档机制（economy / balanced / performance + custom）pytest 45 条全 pass，用户验收通过
- 同日：[config/config.json:382](config/config.json#L382) `humanization.profile` 由 `"custom"` 切换到 `"balanced"`
- 同日：因镜像 commit 早于 P6.0.y4 QQInteractionsConfig 字段落地，跑 `dot_clean . && docker compose up bot -d --build` 重建镜像，11 字段 ResolvedHumanization 全部就位
- 灰度群：`993065015` / `984198159`（由 `humanization.runtime_groups` 限定，但 balanced 也对其它群生效，runtime_groups 仅是 plan_then_utter 等部分子模块的额外白名单）

### 0.2 生产症状

用户在 balanced 落地后反馈：

> part6 实装后，观察 bot 表现，所有内容都是一段话，多段对话表现极差。

日志侧证据：4 次 @ 提及触发的回复，`reply_segment_plan` 全部回 `segments=1, raw=1`，包括一条 76 字节、内含 `\n` 的回复（自然分段语义本应切成 ≥2 段）。

### 0.3 用户立场

> 当前状态紧急，但不能急切。全量审查新回复流，避免类似问题出现，之后给出修复方案。

→ 本文档为审查输出 + 修复方案；**不在本文档中执行 Phase 0 回滚**，等用户书面确认。

---

## 1. 根因分析（三层）

### 1.1 第一层：`_streaming_segment_enabled` 在工具非空时硬关闭

**位点**：[services/llm/client.py:1758-1771](services/llm/client.py#L1758-L1771)

```python
def _streaming_segment_enabled(
    self, humanization, *, on_segment, tool_defs, is_group, force_reply,
) -> bool:
    if on_segment is None:
        return False
    if not is_group or not force_reply:
        return False
    if not bool(getattr(humanization, "streaming_segment_enabled", False)):
        return False
    return not tool_defs   # ← 关键：tool_defs 非空 → 直接 False
```

群聊里 `tool_defs` 一般包含 `send_sticker` / `append_memo` / `update_memo` 等 4–7 个常驻业务工具，因此 `not tool_defs` 几乎永远为 False。balanced 把 `streaming_segment_enabled=True` 起来，但流式分段实际从未启用。

**影响**：streaming-as-segment（边推边切）走不到，整段回复回退到「全文回完后再分段」的传统路径。

### 1.2 第二层：传统路径被 `disable_natural_split=True` 短路成单段

**位点**：[services/llm/client.py:432-438](services/llm/client.py#L432-L438)

```python
def _reply_segment_plan(reply, cfg=None, *, register=None, slot_energy=1.0,
                       disable_natural_split=False) -> ReplySegmentPlan:
    if disable_natural_split:
        return ReplySegmentPlan(
            segments=[fix_cq_codes(reply)],
            raw_count=1,
            limit_status="none",
            inter_segment_delays=[],
        )
    ...
```

**实际调用点（共 4 处，全仓 grep）**：

| 文件:行 | 角色 |
|---|---|
| [services/llm/client.py:1740](services/llm/client.py#L1740) | `_visible_reply_segment_plan` 形参定义 |
| [services/llm/client.py:1753](services/llm/client.py#L1753) | 中转传入 `_reply_segment_plan` |
| [services/llm/client.py:3465](services/llm/client.py#L3465) | reply 主路径 |
| [services/llm/client.py:3704](services/llm/client.py#L3704) | reply 第二路径（rewrite 后） |

```python
plan = self._visible_reply_segment_plan(
    reply, session_id=..., group_id=..., user_id=..., turn_id=...,
    disable_natural_split=humanization.disable_natural_split,
)
```

balanced profile 把 `disable_natural_split=True` 也起来——这层语义本应是「相信流式已经分好段了，传统再切一刀就重复」，但与第一层组合后，**流式没分段、传统也不分段**，结果就是单段。

**setter 侧位点（kernel/config.py 共 5 处）**：[kernel/config.py:1524](kernel/config.py#L1524) (balanced) / [1536](kernel/config.py#L1536) (performance degraded) / [1548](kernel/config.py#L1548) (performance) / [1564](kernel/config.py#L1564) (custom) / [1225](kernel/config.py#L1225) (default 字段声明)。其中 custom 分支表达式 `disable_natural_split=streaming_enabled or plan_enabled` —— 已经隐含 invariant，但 balanced/performance 分支是 hardcode True，绕过了表达式。

### 1.3 第三层：契约层缺陷——决策与保护未联动

[kernel/config.py:1216-1230](kernel/config.py#L1216-L1230) `ResolvedHumanization` 是 frozen dataclass，11 个字段是「决策结果」而非「行为契约」。`disable_natural_split=True` 的语义条件（**前提是流式确实把段切好了**）没有在结构上表达，下游消费者也没建立「X 决策 → 必须先确认 Y 前置条件成立」的映射。

balanced 的 resolve 默认假设了「streaming_segment_enabled=True 一定生效」，但 `_streaming_segment_enabled` 的实际门禁要求 `tool_defs` 为空——这是一个**未文档化的隐藏前提**，profile 设计阶段没法看到。

---

## 2. 审查发现表（A–J，共 9 条）

通过 2 个并行 Explore agent 扫了「reply 流端到端」+「测试覆盖率」两条线，归并出 9 条 finding。

| ID | 严重 | 位点 | 现象 | 用户可见症状 |
|---|---|---|---|---|
| **A** | Blocking | [services/scheduler.py:35-42](services/scheduler.py#L35-L42) | `_should_force_reply` 白名单仅含 `at_mention` / `video_always` / `directed_followup`，`qq_interaction` mode 不在内 | QQ 戳一戳 / 表情回应入站事件触发的 trigger，bot 永远不回 |
| **B** | High | [services/llm/client.py:1764-1771](services/llm/client.py#L1764-L1771) + 1.1 节 | tool_defs 非空时 streaming_segment 强关；`is_group AND force_reply` 也是硬条件 | 所有非 @ 触发（debounce / batch / proactive）丢失 streaming 流式分段；@ 触发但有业务工具时也丢失 |
| **C** | Medium | [config/soul/instruction.md:316-324](config/soul/instruction.md#L316-L324) vs streaming 解析器 | instruction.md 教模型直接发 `[CQ:reply,id=消息ID]`；解析器 `_extract_quote_anchor` 只读 `<quote msg_id="...">` XML（[client.py:85-88](services/llm/client.py#L85-L88)）。模型直发 CQ 不进 strip 路径，被 `fix_cq_codes` 原样保留并直接 emit | `humanization.qq_interactions.quote_reply.enabled=false` kill-switch 失效——把开关关了模型还会带 quote |
| **D** | Medium | [services/llm/client.py:432-438](services/llm/client.py#L432-L438) + 1.2 节 | streaming 零增量回退也走 `disable_natural_split=True` 单段路径 | balanced/performance 下，model 一次性吐出 → fallback → 单段 |
| **E** | Low | `_quote_reply_enabled` 默认分支 | 部分配置组合下 fallback 路径不可达（dead branch） | 无直接症状，仅维护风险 |
| **F** | Info | [services/tools/interaction_tools.py:115-146](services/tools/interaction_tools.py#L115-L146) + grep 结果 | `register_interaction_tools` 只在 tests 中被调用，生产代码从未 wire | performance profile 即使打开 `poke_outbound` / `reaction_outbound`，模型也调不到工具，等于摆设 |
| **G** | Low | [services/humanization/health_guard.py](services/humanization/health_guard.py) | 60s poll 周期内单 turn 中切档（performance ↔ balanced）行为不可预测 | 仅在 performance 启用且降级触发时显现；当前 balanced 不会触发 |
| **H** | Medium | resolve_profile 中 plan_then_utter 与 disable_natural_split 组合 | plan_then_utter.enabled=false 时仍 force disable_natural_split=True，custom/performance 下双重死锁 | 与第 1 层 + 第 2 层叠加生效，是当前生产症状的根因之一 |
| **I** | Low | group_id 类型一致性 | str/int 在 ResolvedHumanization 各消费者间一致使用 str | 无症状，记录用 |
| **J** | Medium | pause_extend 触发时机 vs streaming "end" 语义 | `_pause_extend_enabled` 打开时，streaming 路径下 pause 起点是「最后一个 segment 已 emit」还是「整段回复已收完」语义模糊 | 偶尔出现 pause 后无下文 / pause 提前的轻微抖动 |

---

## 3. 横切契约缺陷分析（5 条结构性问题）

| # | 缺陷 | 现状 | 期望 |
|---|---|---|---|
| 1 | ResolvedHumanization 是决策结果而非行为契约 | 11 字段全是 bool / Literal，消费者各自判 | 决策应携带「前置条件已确认」标志或建立 invariant 验证 |
| 2 | `disable_natural_split` 名字歧义 | 它的「真实语义」是 `streaming_already_emitted`（流式已切好段，传统别再切） | 改名 + 由调用方按运行时事实传，不再由 profile 静态指定 |
| 3 | streaming_segment 隐藏门禁 | tool_defs 非空 / 非 force_reply / 非群聊都会静默关闭 | 若被静默关闭，必须 fallback 到传统自然分段而非走 `disable_natural_split` 单段 |
| 4 | trigger.mode 白名单分散 | `_should_force_reply` 是字符串集合 + extra 字段查 addressee | mode 应为 enum；force_reply 决策应在 trigger 工厂处统一 |
| 5 | qq_interactions outbound 链路从 prompt 到 tool 全断 | tool 实现存在，prompt 提示存在，但 register_interaction_tools 没 wire | performance profile 启用时应在 tool registry 注册路径上断言 |

---

## 4. 测试金字塔倒挂分析

### 4.1 现状

50 条 humanization 相关测试中：

- **~30 条 BaseModel/resolver 层**：[tests/test_humanization_config.py:11-204](tests/test_humanization_config.py#L11-L204) — 只断言字段反序列化与 resolve_profile 的字段值
- **~6 条 E2E**：均使用 `ToolRegistry` 为空的桩 → 永远命中 `not tool_defs == True`，绕开了第一层 bug 的检查
- **0 条 balanced「长回复→≥2 段」断言**：没有任何测试验证 `disable_natural_split` 与 `streaming_segment_enabled` 共启时实际产出分段数 ≥ 2

### 4.2 倒挂结果

profile 设计阶段，开发者通过 resolve_profile 单测拿到 11 字段全绿；生产阶段，11 字段绿但行为黑——因为没人写「ResolvedHumanization → 实际段数」的桥接断言。

### 4.3 必加 4 条回归（must-have）

| # | 测试名 | 断言 |
|---|---|---|
| T1 | `test_balanced_long_reply_yields_multi_segments` | balanced + 非空 tool_defs + 含 `\n` 的 80 字节回复 → segments ≥ 2 |
| T2 | `test_qq_interaction_mode_force_reply` | trigger.mode=`qq_interaction` → `_should_force_reply` 返回 True |
| T3 | `test_streaming_disabled_fallback_uses_natural_split` | streaming 被门禁关闭时，传统路径走自然分段而非 `disable_natural_split` 短路 |
| T4 | `test_quote_reply_kill_switch_strips_cq_reply` | `quote_reply.enabled=false` 时，模型输出含 `[CQ:reply,id=xxx]` 应被剥离 |

### 4.4 选加 4 条（nice-to-have）

| # | 测试名 | 断言 |
|---|---|---|
| T5 | `test_register_interaction_tools_wired_for_performance` | performance profile 下 ToolRegistry 含 `poke_user` / `react_to_message` |
| T6 | `test_health_guard_no_mid_turn_switch` | turn 进行中 health_guard 状态切换不影响当前 turn 的 ResolvedHumanization |
| T7 | `test_pause_extend_timing_after_last_segment` | pause_extend 起点固定为「最后 segment emit 完成后」 |
| T8 | `test_resolve_profile_invariants` | 任意 profile 下，`disable_natural_split=True` 蕴含 `streaming_segment_enabled=True OR plan_then_utter_enabled=True`（custom 分支 [kernel/config.py:1564](kernel/config.py#L1564) 已表达此 invariant；balanced/performance 应同步） |

---

## 5. 修复方案（Phase 0 / 1 / 2 / 3 分级）

### 5.1 Phase 0：紧急回滚（30 秒，0 行代码）

**目的**：先把生产从「单段灾难」恢复到 P6 落地前的可用状态，给后续 Phase 1 让出窗口。

**操作**：

```bash
# 1. config.json:382 由 "balanced" 改回 "custom"
# 2. docker compose restart bot
```

`custom` profile 下 11 字段沿用 [config/config.json:375-398](config/config.json#L375-L398) 显式指定值（`streaming_segment.enabled=false` / `disable_natural_split=false` / `plan_then_utter.enabled=false`）——回到 P6 之前的纯传统分段路径，不存在第 1.1 / 1.2 节的双重死锁。

**前置确认**：用户书面同意回滚。当前文档不擅自执行。

### 5.2 Phase 1：核心修复（3 个改动 + 测试）

#### 5.2.1 Phase 1A — 改名 `disable_natural_split` → `streaming_already_emitted`

**核心思路**：把这个参数从「profile 静态决策」改成「调用方运行时事实」。

**改动**：

[services/llm/client.py:425-444](services/llm/client.py#L425-L444)

```python
def _reply_segment_plan(
    reply, cfg=None, *, register=None, slot_energy=1.0,
    streaming_already_emitted: bool = False,   # 改名
) -> ReplySegmentPlan:
    if streaming_already_emitted:
        return ReplySegmentPlan(
            segments=[fix_cq_codes(reply)], raw_count=1,
            limit_status="none", inter_segment_delays=[],
        )
    ...
```

**所有 4 处调用点（client.py）+ 5 处 setter（kernel/config.py）必须同步改名**：

| 文件:行 | 改动 |
|---|---|
| [services/llm/client.py:430](services/llm/client.py#L430) | `_reply_segment_plan` 形参 |
| [services/llm/client.py:1740](services/llm/client.py#L1740) | `_visible_reply_segment_plan` 形参 |
| [services/llm/client.py:1753](services/llm/client.py#L1753) | 中转传入 |
| [services/llm/client.py:3465](services/llm/client.py#L3465) | reply 主路径调用 |
| [services/llm/client.py:3704](services/llm/client.py#L3704) | reply rewrite 路径调用 |
| [kernel/config.py:1225](kernel/config.py#L1225) | `ResolvedHumanization.disable_natural_split` 字段（保留 + 标 deprecated 或同步重命名） |
| [kernel/config.py:1524/1536/1548/1564](kernel/config.py#L1524) | resolve_profile 4 个分支 setter |

**主路径调用点改写**（client.py:3459-3466）：

```python
plan = self._visible_reply_segment_plan(
    reply, session_id=..., group_id=..., user_id=..., turn_id=...,
    streaming_already_emitted=bool(streamed_segments),   # 由运行时事实决定
)
```

`streamed_segments` 是流式路径已 emit 的 segment 列表；为空表示「流式没启用 / 流式启用但 0 增量」，此时**回退到自然分段**（即 `_segment_reply_segment_plan`），而非短路成单段。注意 client.py:3704 的 rewrite 路径也要同款处理（rewrite 出来的 reply 与 streaming 已 emit 的内容是替换关系，所以这里 `streaming_already_emitted=False`，因为 streaming segments 已经被 rewrite 抛弃了）。

**ResolvedHumanization 字段处理**：

`disable_natural_split` 字段保留（向后兼容），但调用方不再使用；profile 文档标注为 deprecated，下个 part 移除。或：直接在 resolve_profile 里把 `disable_natural_split` 与 `streaming_segment_enabled OR plan_then_utter_enabled` 绑成同值（与 custom 分支 [kernel/config.py:1564](kernel/config.py#L1564) 现有表达式对齐），让契约 invariant 成立（T8）。

#### 5.2.2 Phase 1B — `_should_force_reply` 加入 `qq_interaction`

[services/scheduler.py:35-42](services/scheduler.py#L35-L42)

```python
def _should_force_reply(trigger):
    if trigger is None:
        return False
    if trigger.mode in {"video_always", "directed_followup", "qq_interaction"}:
        return True
    if trigger.mode != "at_mention":
        return False
    return bool(trigger.extra.get("addressee_self", True))
```

QQ 戳一戳 / 表情回应入站事件随后能触发 LLM 生成。

#### 5.2.3 Phase 1C — `_streaming_segment_enabled` 门禁松绑

**两条选择，二选一**：

**选项 A**（保守）：区分业务 vs 非业务 tool。维护一个 `NON_STREAMING_BLOCKING_TOOLS = {...}` 集合，只有这些 tool 出现时才关闭 streaming；`send_sticker` / `append_memo` 不在其中。

**选项 B**（激进，推荐）：始终允许 streaming，运行时若 model 真返回 `tool_use` block，由 streaming 解析器降级处理（中断 streaming，转入工具循环，剩余文本进 fallback）。

倾向选项 B 的理由：业务工具在 group 聊天里几乎常驻，选项 A 维护成本高、漏白名单风险大；选项 B 把降级责任放到运行时观测点，决策一致。

#### 5.2.4 Phase 1 配套：4 条 must-have 回归测试同步落地（见 4.3）

### 5.3 Phase 2：选加测试（4 条 nice-to-have）

按 4.4 节执行，可挑 T8（结构 invariant）优先，其余看人手。

### 5.4 Phase 3：剩余契约缺陷收尾

| Finding | 处置 |
|---|---|
| C（quote_reply kill-switch） | 二选一：① 把 instruction.md 的 `[CQ:reply,id=]` 教法改成 `<quote msg_id="...">` XML（与解析器对齐）；② 在 message 出口处统一 strip `[CQ:reply,...]` 当 `quote_reply.enabled=false`。倾向 ②，无需 retrain prompt 习惯 |
| F（register_interaction_tools 未 wire） | performance profile 启用时在 tool registry 注册 `poke_user` / `react_to_message`；或文档标注 outbound 为「下个 part 实现」 |
| G（health_guard 中 turn 切档） | 文档标注为已知问题；下个 part 解决：health_guard 决策只在 turn 边界生效 |
| E（dead branch） | 单纯清理，无回归风险 |
| J（pause_extend 时机） | 与 Phase 1C 选项 B 同步处理：streaming 路径定义 pause 起点 = 最后 segment emit 完成 |

---

## 6. 推荐执行顺序

```
Phase 0 立即（用户确认后）
  ↓
Phase 1A + Phase 2 T1（今天）
  ↓
Phase 1B + Phase 1C + T2/T3/T4（本周）
  ↓
Phase 1 全部回归 pass 后，再次切 balanced 灰度
  ↓
Phase 2 T5/T6/T7/T8（看人手）
  ↓
Phase 3（按优先级排）
```

**关键约束**：

- Phase 0 与 Phase 1 之间不要平行——回滚后再改代码，避免 balanced 状态下改 client.py 引入二次故障
- Phase 1 三项可并行，但合并前必须 T1+T2+T3+T4 全绿
- 重新切 balanced 前在两个灰度群（993065015 / 984198159）做 30 分钟生产观察

---

## 7. 风险与回滚

### 7.1 Phase 0 风险

- 几乎无风险；config.json 单字段切换，30 秒内可逆
- 唯一注意：`runtime_groups` 限定的灰度群在 custom 下对应的 plan_then_utter / streaming_segment 字段值仍是 `false`，行为完全等同 P6 之前

### 7.2 Phase 1 风险

| 改动 | 风险 | 缓解 |
|---|---|---|
| 1A 改名 | client.py 调用点漏改 → ReplySegmentPlan 错误 single segment | grep `disable_natural_split` 全仓扫，确认 4 处 client.py 调用点 + 5 处 kernel/config.py setter 全部触达；类型检查兜底 |
| 1B 加白名单 | qq_interaction trigger 在某些边缘场景 addressee 不是 self → 误触发回复 | 加 `extra.get("addressee_self", True)` 同款保护 |
| 1C 选项 B | streaming 中遭遇 tool_use 降级路径不健壮 → 回复截断 | T3 覆盖；额外加监控日志「streaming_to_tool_fallback」counter |

### 7.3 总回滚路径

```bash
git revert <Phase 1 commit>  # Phase 1 出问题
# config.json:382 改回 "custom"     # 同时 + 兜底
docker compose restart bot
```

---

## 8. 维护日志纪律（D7）

- Phase 0 执行后必须在 [maintenance-log.md](maintenance-log.md) 顶部追加条目，记录回滚动机 + 时间点 + 灰度群影响范围
- Phase 1 合并后追加条目，记录 1A/1B/1C 三组改动 + 4 条回归测试 + 重新切 balanced 的观察窗口
- Phase 2/3 按粒度独立成条

---

## 9. 复审签收

本文档为审查 + 方案，**不含代码改动**。等待用户书面确认以下三件事的执行授权：

1. ☐ Phase 0 紧急回滚（config.json `balanced` → `custom` + restart）
2. ☐ Phase 1 三组改动（1A/1B/1C）的实施时间窗
3. ☐ Phase 1C 选项 A vs 选项 B 的取舍（默认推荐 B）

签收后切到执行档（建议另起 part2 文档）。

---

## 10. 自审记录（2026-05-27）

| 自审项 | 验证手段 | 结论 |
|---|---|---|
| `disable_natural_split` 调用点全集 | `grep -rn "disable_natural_split" services/llm/client.py kernel/config.py` | client.py 4 处 + kernel/config.py 5 处，已在 §1.2 与 §5.2.1 表格列出 |
| `_should_force_reply` 白名单文本 | 直接 Read [services/scheduler.py:35-42](services/scheduler.py#L35-L42) | 仅 `at_mention` / `video_always` / `directed_followup`，确认 `qq_interaction` 不在内 |
| `qq_interaction` mode 字面值 | grep `services/humanization/qq_interactions.py` | line 189 `mode="qq_interaction"`，与 §A finding / §5.2.2 改动文本一致 |
| `_extract_quote_anchor` 仅匹配 `<quote>` XML | Read [services/llm/client.py:85-88](services/llm/client.py#L85-L88) 正则 + grep 调用点（6 处） | 正则 `<quote\b[^>]*\bmsg_id\s*=...>`，确认无 `[CQ:reply` 匹配分支；§C finding 已补 anchor |
| custom 分支 invariant 现状 | Read [kernel/config.py:1564](kernel/config.py#L1564) | `disable_natural_split=streaming_enabled or plan_enabled` 已成立；balanced/performance 是 hardcode True 绕过；T8 描述已对齐 |
| balanced 当前 config.json 状态 | Read [config/config.json:382](config/config.json#L382) | `"profile": "balanced"`，confirms Phase 0 仍在生效中（未回滚） |
| 11 字段 ResolvedHumanization 全集 | Read [kernel/config.py:1216-1230](kernel/config.py#L1216-L1230) | 与 §0.1 表述、§B finding 11-field 表述一致 |

**未验证项（依赖运行时观察）**：

- balanced 灰度群 30 分钟生产观察的具体观察指标——本文档不包含，留给 Phase 1 回归后另起执行档
- 选项 B（streaming 中遭遇 tool_use 的降级路径）的具体 SSE 解析点——Phase 1C 实施时再贴 anchor
