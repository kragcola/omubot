# 群聊连发消息分裂回复 — 跨代解决方案

> 状态：2026-05-27 深度调研完成，架构方案待确认
> 触发：用户连续发送 "@bot 别睡"（15:04:56）+ "来烤"（15:04:59），bot 对两条分别回复形成错位感
> 要求：不接受简单 debounce；需要处理"话说到一半"、"bot 已触发但未发完"、"发出后追加补充"三种场景

## 1. 问题拆解 — 三个独立失败模式

| 编号 | 场景 | 当前行为 | 期望行为 |
|------|------|----------|----------|
| F1 | 用户连发 2-3 条表达同一件事，bot 只看到第一条就开始生成 | 基于不完整上下文回复，形成答非所问 | 等用户"说完"再回复 |
| F2 | bot 已开始生成（或已发出第一段），用户追加了关键补充 | 已发段无法撤回，后续段基于旧上下文继续 | 中止未发段，基于新上下文修正或追加更正 |
| F3 | bot 已完整回复，用户 30s 内补充了改变语义的新信息 | 需要再次 @mention 才能触发新回复 | bot 主动识别"我刚才的回复可能不对"并自我修正 |

## 2. 学术基础

### 2.1 增量对话处理（Incremental Dialogue Processing）

Schlangen & Skantze (2009/2011) 的 **IU Framework** 定义对话单元的三种操作：
- **Add**：新信息到达
- **Revoke**：已输出信息被撤回（"等等，我说错了"）
- **Commit**：信息终态化，不再变更

**DIUM 系统**（Buss & Schlangen, 2011）直接处理"已回复错误信息"：触发显式 self-repair（"啊看到你后面说的了——"）。这是 F3 的学术金标准。

### 2.2 全双工模型（Full-Duplex）

**Moshi**（Kyutai Labs, 2024, arxiv:2410.00037）：取消 turn boundary，bot 同时"听"和"说"。每 160ms 决定输出 token 还是静默。映射到文字聊天：bot 生成过程中持续感知新消息，生成结果 conditioned on 双向流。

**TDM 编码**（EMNLP 2024, "Beyond the Turn-Based Game"）：将对话切为固定时间片（2s），每片独立处理。新输入自然融入下一片。

### 2.3 投机生成 + 回滚（Speculative Generation）

**Remdis 系统**（Chiba et al., 2024; ACL Anthology 2025.coling-main.249）：
1. 每收到新 partial input，spawn 新 LLM 实例基于 input-so-far 生成候选
2. 多实例并行（投机生成）
3. 检测到 end-of-turn 时，选择基于最完整输入的候选
4. 丢弃其余候选

代价：为废弃生成付费。收益：延迟大幅降低（生成提前开始）。

### 2.4 End-of-Turn 检测

**Voice Activity Projection**（Ekstedt & Skantze, 2022）：无监督预测说话者何时停止。映射到文字：
- 标点启发式：`。！？` = 大概率完成；`，` / 省略号 / 连词结尾 = 未完成
- 长度比模型：用户历史平均消息长度 50 字，本条 8 字 → 大概率是片段
- 时序模式：per-user 连发间隔统计，超过 1.5× 平均间隔 → 大概率说完了

**Pipecat Smart Turn**（Daily.co）：分层检测栈 — VAD → AI completeness → min words → silence timeout。

### 2.5 生产实现参考

**Hermes Agent**（NousResearch）：
- 正常文本 0.6s quiet-period
- 接近平台字数限制时 2.0s（续发几乎确定）
- 每条新消息 reset timer
- **merge-while-running**：生成中收到新消息 → merge 进 pending context 而非触发新 turn

## 3. 架构方案 — 五层递进

### Layer 0：Adaptive Burst Gate（替代固定 debounce）

**解决 F1**。不是"等 N 秒"，而是"判断用户是否说完"。

```
on_message(msg):
    slot.pending.append(msg)
    completeness = estimate_completeness(msg, slot.pending)
    
    if completeness >= THRESHOLD_COMMIT:
        # 用户大概率说完了 → 立即 fire
        cancel_burst_timer()
        fire()
    else:
        # 可能还有后续 → 设置/重置 timer
        reset_burst_timer(adaptive_delay(slot))
```

`estimate_completeness` 启发式（不需要 LLM，纯规则）：
- 末尾 `。！？~` → +0.4
- 末尾 `，、` / 连词（"但是"、"然后"、"不过"） → -0.3
- 消息长度 < 用户历史 p25 → -0.2
- 距上条 < 2s 且上条也是同用户 → -0.3
- @mention 本身（无后续文本）→ -0.1（通常后面会跟内容）

`adaptive_delay`：
- base = 1.5s
- 如果 pending 已有 2+ 条且最近一条 completeness < 0.5 → 延长到 3.0s
- 绝对 cap = 5.0s（防止用户等太久）

### Layer 1：Speculative Pre-generation（投机预生成）

**优化延迟**。burst gate 等待期间不浪费时间：

```
on_burst_timer_tick(elapsed):
    if elapsed >= 1.0s and not slot.speculative_task:
        # 等了 1 秒还没说完，先开始生成（但不发出）
        slot.speculative_task = start_generation(slot.pending_snapshot)
    
on_new_message_during_speculation:
    # 新消息到达，废弃当前投机生成
    cancel(slot.speculative_task)
    slot.speculative_task = None
    # burst gate 重新计时

on_burst_committed:
    if slot.speculative_task and speculative_input == final_input:
        # 投机生成的输入恰好是最终输入 → 直接用
        await slot.speculative_task
    else:
        # 输入变了 → 废弃，重新生成
        cancel(slot.speculative_task)
        start_fresh_generation(slot.pending)
```

效果：单条消息延迟 = burst_wait(1.5s) + 0（生成已完成）≈ 1.5s。连发场景延迟 = last_msg_time + burst_wait - speculation_start ≈ 0.5-1s。

### Layer 2：Mid-Flight Interruption（飞行中中断）

**解决 F2**。bot 已开始发送分段回复，用户追加了新内容。

当前 `pause_then_extend` 在段间有 pause。利用这个 pause 窗口：

```
on_segment_pause(before_next_segment):
    if slot.pending has new messages since generation started:
        # 用户在 bot 说话期间追加了内容
        new_msgs = slot.pending_since(generation_start_time)
        
        if invalidates_current_response(new_msgs, already_sent_segments):
            # 新内容改变了语义 → 中止后续段，生成修正
            abort_remaining_segments()
            generate_correction(already_sent_segments, new_msgs)
        else:
            # 新内容是补充但不矛盾 → 让当前回复完成，然后追加
            continue_current_response()
            queue_followup_acknowledgment(new_msgs)
```

`invalidates_current_response` 判定（轻量级，不需要 LLM）：
- 新消息含否定词 + 引用 bot 内容 → invalidate
- 新消息含"不是"、"我是说"、"等等" → invalidate
- 新消息是纯补充信息（无否定/修正信号）→ 不 invalidate

### Layer 3：Post-Emission Self-Repair（发后自修正）

**解决 F3**。bot 已完整回复，用户 30s 内补充了改变语义的信息。

```
on_message_after_recent_reply(msg, reply_elapsed_s):
    if reply_elapsed_s > 30:
        return  # 太久了，当作新话题
    
    if is_correction_signal(msg):
        # "不是啦"、"我是说..."、"等等还有"
        generate_self_repair(original_reply, msg)
        # 输出类似："啊看到了看到了，你是说要烤pjsk是吧~"
    
    elif is_additive_context(msg):
        # 纯补充，不矛盾
        # 走正常 followup 检测（已有 last_assistant_to_user 修复）
        pass
```

这一层与刚修复的 `_last_assistant_replied_to_user` 互补：那个修复让 followup 能被检测到并触发概率回复；这一层让"修正性补充"能绕过概率直接触发。

### Layer 4：Acknowledgment Signal（等待中的存在感）

当 burst gate 等待超过 2s，用户可能以为 bot 没收到。发一个轻量信号：

```
on_burst_wait_exceeded(2.0s):
    if not already_acknowledged:
        send_typing_indicator()  # QQ 的"正在输入..."
        # 或者什么都不做 — QQ 群聊中用户对 2s 延迟容忍度高
```

实际上 QQ 群聊场景下 2-3s 延迟完全自然（人类也需要时间阅读和思考），这一层可能不需要实现。

## 4. 实现优先级

| 层 | 解决 | 复杂度 | 收益 | 建议 |
|----|------|--------|------|------|
| L0 Adaptive Burst Gate | F1 | 中 | 高 — 直接消除连发错位 | **首批实施** |
| L1 Speculative Pre-gen | 延迟优化 | 高 | 中 — 单条场景从 1.5s 降到 ~0s | 二期，需要 LLM 调用管理 |
| L2 Mid-Flight Interrupt | F2 | 中 | 中 — pause_then_extend 段间已有窗口 | **首批实施**（改动小） |
| L3 Post-Emission Repair | F3 | 低 | 中 — 与 followup 修复互补 | **首批实施**（规则简单） |
| L4 Ack Signal | UX polish | 低 | 低 — QQ 群聊容忍度高 | 观察后决定 |

## 5. 与现有架构的接口

| 组件 | 改动 |
|------|------|
| `services/scheduler.py` `notify()` | `is_at` / `is_directed_followup` 分支改为走 L0 burst gate |
| `services/scheduler.py` `GroupSlot` | 新增 `burst_timer`, `speculative_task`, `generation_start_time` |
| `services/llm/client.py` `_stream_with_segments` | 段间 pause 检查 `slot.pending_since()` (L2) |
| `kernel/router.py` reply_workflow | 新增 `is_correction_signal` 检测 (L3) |
| `kernel/config.py` | `BurstGateConfig` 子模型（thresholds, delays, caps） |

## 6. 关键参考文献

- Schlangen & Skantze (2011), "A General, Abstract Model of Incremental Dialogue Processing" — IU Framework
- Buss & Schlangen (2011), "DIUM" — self-repair in incremental DM
- Moshi (Kyutai, 2024), arxiv:2410.00037 — full-duplex dialogue
- Remdis (Chiba et al., 2024), ACL 2025.coling-main.249 — speculative parallel generation
- Ekstedt & Skantze (2022), "Voice Activity Projection" — end-of-turn prediction
- Skantze (2021), "Turn-taking in Conversational Systems" — comprehensive survey (500+ citations)
- EMNLP 2024, "Beyond the Turn-Based Game: Duplex Models" — TDM encoding
- Pipecat Smart Turn (Daily.co) — layered turn detection for voice
- Hermes Agent (NousResearch) — merge-while-running pattern
- Castillo-Lopez et al. (2025), ACL 2025.iwsds-1.27 — turn-taking modeling survey

## 7. 修订方案 — LLM-as-Arbiter 并发架构

> 2026-05-27 第二轮调研后修订。用户反馈：L0/L1 的固定秒数计算体感不佳，L2 的固定词表否定判断会频发失误。核心构思：利用 DeepSeek 500-5000 并发能力，用**短判断 LLM 调用**为主回复工作流提供实时 commit/abort/revise 信号。

### 7.1 学术与生产验证

| 系统 | 模式 | 验证结果 |
|------|------|----------|
| OpenAI Realtime API | server-side semantic VAD + `conversation.interrupted` 事件 → 截断生成 + 回滚记忆 | 生产级，全球部署 |
| LiveKit turn-detector | DeBERTa 分类器并行跑在 partial transcript 上，预测 end-of-turn | 开源，HuggingFace 可用 |
| Pipecat | `InterruptionFrame` 作为 SystemFrame 最高优先级取消所有 pending DataFrame | 生产级 voice pipeline |
| Speculative Interaction Agents (UC Berkeley, 2026-05) | 异步 I/O + 投机执行 + commit point；1.3-1.7× 加速，精度损失极小 | arxiv，OpenAI Realtime 实测 |
| FS-GEN (Fast and Slow) | 小模型 System-1 + 大模型 System-2；仅 ~20% 交互需要 System-2 介入 | 论文验证 |
| tiny-router (DeBERTa-v3-small) | 多头分类：`relation_to_previous`(new/follow_up/correction/cancellation/closure) + `actionability` + `urgency` | ONNX <10ms CPU |

**关键结论**：用并发 LLM 调用做实时控制信号是已验证的生产模式，不是实验性构想。

### 7.2 Omubot 适配架构 — Arbiter Loop

```
┌─────────────────────────────────────────────────────────┐
│                    Main Reply Pipeline                    │
│  pending → flush → LLM generate → segment → send        │
│       ↑ cancel/revise signal                             │
└───────┼─────────────────────────────────────────────────┘
        │
┌───────┴─────────────────────────────────────────────────┐
│                    Arbiter (并发短调用)                    │
│                                                          │
│  触发条件：主 pipeline 运行中 + 新消息到达               │
│                                                          │
│  输入：{                                                 │
│    "bot_pending_reply": "好好好不睡不睡~\n那再陪你...",  │
│    "already_sent": ["好好好，不睡不睡~"],                │
│    "new_user_message": "来烤",                           │
│    "original_context": "别睡"                            │
│  }                                                       │
│                                                          │
│  输出（max_tokens=10）：                                 │
│    { "action": "continue" | "abort" | "revise",          │
│      "reason": "..." }                                   │
│                                                          │
│  延迟预算：50-150ms（DeepSeek ~50 input tokens + 10 out）│
└──────────────────────────────────────────────────────────┘
```

### 7.3 三个 Arbiter 角色

**Arbiter-A：Completeness Judge（替代 L0 固定 debounce）**

触发：每条新消息到达时
输入：最近 2-3 条 pending 消息 + 时间间隔
输出：`{"complete": true/false, "confidence": 0.0-1.0}`
作用：complete=true 且 confidence>0.8 → commit fire；否则 hold

```
system: 你是对话完整性判断器。判断用户是否说完了当前这轮话。
user: 
  消息1 (0s前): "@bot 别睡"
  消息2 (刚到): "来烤"
  间隔: 3s
  问题: 用户说完了吗？
assistant: {"complete": false, "confidence": 0.85}
// 理由："来烤"是对"别睡"的补充说明，用户可能还要说烤什么
```

token 预算：~80 input + 10 output ≈ 90 tokens/call
延迟：50-100ms（DeepSeek flash）

**Arbiter-B：Interruption Judge（替代 L2 固定词表）**

触发：主 pipeline 生成中/发送中 + 新消息到达
输入：bot 已发内容 + bot 待发内容摘要 + 新用户消息
输出：`{"action": "continue" | "abort_unsent" | "revise", "reason": "..."}`
作用：abort_unsent → 取消未发段；revise → 取消未发段 + 触发修正生成

```
system: 你是对话中断判断器。bot正在分段发送回复，用户发了新消息。判断是否需要中断。
user:
  bot已发出: "好好好，不睡不睡~"
  bot待发出: "那再陪你聊一会儿~\n烤什么呀~烤串还是烤肉？"
  用户新消息: "来烤pjsk"
  问题: bot应该继续发送待发内容，还是中断？
assistant: {"action": "revise", "reason": "用户已回答了bot即将问的问题，继续发'烤什么呀'会显得没看到用户消息"}
```

token 预算：~120 input + 20 output ≈ 140 tokens/call
延迟：80-150ms

**Arbiter-C：Post-Reply Correction Judge（替代 L3 固定规则）**

触发：bot 回复完成后 30s 内收到同用户新消息
输入：bot 完整回复 + 新用户消息
输出：`{"needs_correction": true/false, "correction_type": "retract" | "amend" | "acknowledge"}`
作用：needs_correction=true → 触发自修正回复

token 预算：~150 input + 15 output ≈ 165 tokens/call
延迟：80-150ms

### 7.4 并发模型与成本

DeepSeek V4-Flash 定价（生产实际）：
- input: ¥0.5/M tokens (cache hit ¥0.1/M)
- output: ¥2/M tokens

每次 Arbiter 调用成本：
- A: 90 tokens ≈ ¥0.000045 + ¥0.00002 = ¥0.000065
- B: 140 tokens ≈ ¥0.00007 + ¥0.00004 = ¥0.00011
- C: 165 tokens ≈ ¥0.000083 + ¥0.00003 = ¥0.000113

**每次用户消息最多触发 1 个 Arbiter 调用**。按日均 200 条群消息计算：
- 日成本增量：200 × ¥0.00011 = ¥0.022（可忽略）
- 并发压力：峰值 +1 并发请求（主生成 + arbiter），远低于 DeepSeek 500 并发上限

### 7.5 与现有架构的集成点

```python
# services/scheduler.py — notify() 改造
async def notify(self, group_id, ...):
    if is_at:
        slot.pending_at = True
        # 不立即 fire，启动 Arbiter-A
        asyncio.create_task(self._arbiter_completeness(group_id))
        return

# services/scheduler.py — Arbiter-A 循环
async def _arbiter_completeness(self, group_id):
    while True:
        await asyncio.sleep(0.3)  # 最小轮询间隔
        pending = self._timeline.get_pending(group_id)
        if not pending:
            break
        result = await self._arbiter.judge_completeness(pending)
        if result.complete and result.confidence > 0.8:
            self._fire(group_id)
            return
        if elapsed > 5.0:  # 绝对 cap
            self._fire(group_id)
            return

# services/llm/client.py — 段间 pause 改造
async def _pause_between_segments(self, group_id, sent, unsent):
    new_msgs = self._timeline.get_pending(group_id)
    if new_msgs:
        result = await self._arbiter.judge_interruption(sent, unsent, new_msgs)
        if result.action == "abort_unsent":
            raise SegmentAborted()
        if result.action == "revise":
            raise SegmentRevise(new_context=new_msgs)
```

### 7.6 失败模式与兜底

| 失败 | 后果 | 兜底 |
|------|------|------|
| Arbiter 超时 (>500ms) | 等太久 | 超时后 fallback 到 fire（等效于无 arbiter） |
| Arbiter 误判 complete=true | 提前 fire，同当前行为 | 可接受——不比现状差 |
| Arbiter 误判 complete=false | 多等 0.3s 再判一次 | 绝对 cap 5s 兜底 |
| Arbiter 误判 abort | 取消了本该发的段 | 触发 revise 路径补发修正 |
| Arbiter API 不可用 | 无判断信号 | fallback 到固定 1.5s debounce（L0 降级） |
| 主生成与 arbiter 竞态 | 信号到达时段已发出 | 已发出的不撤回，只影响未发段 |

### 7.7 实施路径

| 阶段 | 内容 | 依赖 |
|------|------|------|
| Phase 0 | `services/llm/arbiter.py` — Arbiter 客户端封装（prompt template + parse + timeout） | 无 |
| Phase 1 | Arbiter-A 接入 scheduler `notify()` — 替代立即 fire | Phase 0 |
| Phase 2 | Arbiter-B 接入 `_pause_between_segments` — 段间中断 | Phase 0 + pause_then_extend 已有 |
| Phase 3 | Arbiter-C 接入 reply_workflow — 发后自修正 | Phase 0 + followup 检测已有 |
| Phase 4 | 观测 + 调参（confidence threshold, cap, prompt 迭代） | Phase 1-3 上线后 |

## 8. 设计哲学（修订）

~~这不是"加个 sleep"能解决的问题。~~ 更进一步：这不是任何**规则系统**能优雅解决的问题。

核心洞察：**turn boundary 判断本身就是一个语言理解任务**。用固定秒数或固定词表做语言理解任务，注定粗糙。正确的做法是让语言模型来做语言理解——但用极低成本（90-165 tokens/call, 50-150ms）的并发短调用，而非阻塞主流程。

这是 **dual-process 架构**在对话系统中的应用：
- System 1（Arbiter）：快、便宜、并发、做判断不做生成
- System 2（Main Pipeline）：慢、贵、串行、做生成不做判断

两者通过 asyncio 事件通信，Arbiter 的输出是 Main Pipeline 的控制信号（commit / abort / revise）。

与 OpenAI Realtime API 的 `semantic_vad` + `conversation.interrupted` 是同构设计，只是我们在文字域而非语音域实现。

## 9. 缓存影响分析 — Arbiter 不降低主 pipeline 命中率

### 9.1 当前缓存机制

Omubot 使用 DeepSeek V4-Flash 的 **prefix caching**：服务端按请求的 prompt prefix 做 KV-cache 复用。两个请求只要 system prompt + 前 N 条 messages 的 token 序列相同，后者就能命中前者的缓存。

主 pipeline 的缓存策略（[services/llm/llm_request.py:308](../../services/llm/llm_request.py#L308) `apply_cache_breakpoints`）：
- 4 个 breakpoint 上限（DeepSeek 兼容 Anthropic 协议）
- 按 static / stable / dynamic 三段标记
- persona block（~2000 tokens）+ instruction（~1500 tokens）构成稳定前缀，命中率高

### 9.2 为什么 Arbiter 不影响主 pipeline 缓存

| 维度 | 主 Pipeline | Arbiter |
|------|-------------|---------|
| system prompt | persona + instruction (~3500 tokens) | 判断器指令 (~30 tokens) |
| messages | 完整 history (20-50 turns) | 最近 2-3 条 pending (~50 tokens) |
| 总 input tokens | 4000-8000 | 90-165 |
| prefix 重叠 | — | **零重叠** |

DeepSeek prefix cache 是 **per-prefix** 的，不是全局 LRU 淘汰。Arbiter 的 90-token prompt 和主 pipeline 的 4000-token prompt 没有任何共同前缀，各自独立占据不同的缓存槽。Arbiter 的存在不会：
- 挤占主 pipeline 的缓存空间（不同 prefix = 不同 entry）
- 降低主 pipeline 的命中率（命中取决于自身 prefix 稳定性，与其他请求无关）
- 增加主 pipeline 的 cache_miss 费用（主 pipeline 的 prompt 结构不变）

### 9.3 Arbiter 自身的缓存行为

Arbiter 的 system prompt 是固定模板（"你是对话完整性判断器..."），只有 user 部分随消息变化。连续调用之间：
- system prompt 部分（~30 tokens）可命中 prefix cache
- 但 30 tokens 的 cache hit 节省 ≈ ¥0.000012/call — 可忽略

### 9.4 优化建议

**不需要专门优化。** 理由：

1. Arbiter token 量极小，缓存收益微乎其微
2. 与主 pipeline 无 prefix 竞争
3. 日增成本 ¥0.02，缓存优化即使 100% 命中也只省 ¥0.004

唯一值得遵守的原则：**Arbiter system prompt 保持固定字符串**，不要每次动态拼接不同指令前缀。这确保连续 Arbiter 调用之间 system prompt 部分能命中，虽然收益极小但实现零成本。

### 9.5 监控指标

上线后观察 `storage/usage.db` 的 `prompt_cache_hit_tokens` 字段：
- 主 pipeline（task=main）的 cache hit rate 应保持在当前水平（~85-95%）
- Arbiter（task=arbiter）的 cache hit rate 预期较低（~30-50%，因为 user 部分每次不同），这是正常的
- 如果主 pipeline cache hit rate 下降 >5%，说明有其他因素（不是 Arbiter 导致），需排查 prompt 结构变化

## 10. 回复工作流 Arbiter 交互点审计

> 2026-05-27 全链路审计。覆盖所有触发入口、中断点、输出模态、并发任务，识别 Arbiter 最佳注入位置与架构缺口。

### 10.1 完整流程图（消息到达 → 最终发送）

```text
QQ消息到达
  │
  ├─[router.py] group_listener / private_chat
  │   ├── upstream_filter → 丢弃
  │   ├── bot_pair_guard → 丢弃
  │   ├── 命令分发 → /debug 等
  │   └── 构建 TriggerContext
  │         ├── at_mention（@bot）
  │         ├── directed_followup（追问检测 + semantic_gate LLM）
  │         ├── video_always / video_autonomous（B站视频）
  │         └── qq_interaction（戳一戳/表情回应）
  │
  ├─[scheduler.py] notify()
  │   ├── 概率判定（talk_value × mood × time × RWS）
  │   │   └── 决定 fire / skip
  │   └── _fire() → asyncio.create_task(_do_chat)
  │
  ├─[scheduler.py] _do_chat()
  │   ├── chat_lock 获取
  │   ├── on_segment 回调注册（流式分段发送）
  │   └── llm.chat() 调用（120s 超时）
  │
  └─[client.py] chat()
      ├── ① text_preflight → 可能 skip
      ├── ② thinker（pre-reply 微分类器）
      │     └── action=wait → return None（不回复）
      ├── ③ prompt 构建（system_blocks + messages + tools）
      ├── ④ plan_then_utter（proactive 多段规划，非 force_reply）
      ├── ⑤ 主 LLM 调用（tool loop, max 5 rounds）
      │     ├── streaming_segment（实时流式分段）
      │     ├── pass_turn 检测
      │     └── tool 执行（send_sticker, add_card, etc.）
      ├── ⑥ 后处理管线
      │     ├── persona_drift_detector → repair/block
      │     ├── sentinel_guardrails → strip/rewrite/block
      │     ├── humanization_rewrite → 风格改写
      │     ├── mention_post_processor
      │     └── quote_reply_anchor
      ├── ⑦ 分段发送（on_segment 回调）
      │     └── inter_segment_delay（0.8s 人类节奏）
      └── ⑧ pause_then_extend（追发补充，最多2次）
```

### 10.2 所有触发回复的入口

| 入口 | 触发方式 | force_reply | 可被 Arbiter 拦截的位置 |
|------|----------|-------------|------------------------|
| at_mention | @bot 消息 | True | thinker 前（但 force_reply 会覆盖 pass_turn） |
| directed_followup | 追问检测 + semantic_gate | True | semantic_gate 本身就是 Arbiter 原型 |
| probability fire | talk_value × mood × time | False | scheduler.notify 概率判定 + thinker |
| video_always | B站视频分享 | True | thinker 前 |
| qq_interaction | 戳一戳/表情回应 | True | dispatch 前 |
| thinker | 内置 pre-reply 分类器 | N/A | 它本身就是决策点 |
| schedule | ScheduleGenerator 定时 | N/A | 独立路径，不经过 chat() |
| plan_then_utter | proactive 多段 | False | 在 chat() 内部，thinker 之后 |

### 10.3 所有可中断点及状态分析

| 中断点 | 位置 | 已发送（不可逆） | 缓冲中（可取消） | 需回滚的状态 |
|--------|------|-----------------|-----------------|-------------|
| **A. scheduler 概率判定** | `notify()` | 无 | 无 | 无 |
| **B. thinker 决策** | `chat()` 入口 | 无 | 无 | thinker usage 已记录 |
| **C. 主 LLM 调用前** | prompt 构建后 | 无 | 无 | 无 |
| **D. streaming 流式中** | `_stream_with_segments` | 已 emit 的 segments | segmenter 缓冲区 | timeline 未写入 |
| **E. tool 调用边界** | tool loop 每轮之间 | send_sticker 已发送 | 后续 text 未发送 | tool_call_records |
| **F. 分段发送间隙** | `on_segment` 循环中 | 已发送的 segments | 剩余 segments | timeline 未写入 |
| **G. pause_then_extend 等待** | `asyncio.sleep(wait_s)` | 主回复已发送 | extend 未发送 | 无需回滚 |
| **H. 后处理管线** | guardrail/drift/rewrite | 无 | 完整 reply 在内存 | 无 |

### 10.4 所有输出模态

| 模态 | 发送机制 | Arbiter 可干预 | 备注 |
|------|----------|---------------|------|
| 文本 segments | `on_segment` → `_send_to_group` | 每段间隙可拦截 | 0.8s delay 是天然窗口 |
| 表情包 | `send_sticker` tool | tool 执行前可拦截 | 一旦执行不可撤回 |
| pass_turn | 内部决策 | 可覆盖（force_reply） | 低 confidence 时已有 light_ack |
| thinker thought | 仅内部状态 | 不发送给用户 | 可作为 Arbiter 输入 |
| pause_extend | 追发补充 | 等待期间可取消 | 检查 pending 已有类似逻辑 |
| quote reply | `[CQ:reply,id=X]` 前缀 | 与首段绑定 | 不可单独取消 |

### 10.5 并发异步任务

| 任务 | 生命周期 | 与主回复的关系 | Arbiter 影响 |
|------|----------|---------------|-------------|
| `_do_chat` | scheduler slot.running_task | 主任务本身 | CancelledError 可终止 |
| `pause_then_extend` | 主回复后同步执行 | 串行，在 chat() 内 | sleep 期间可 cancel |
| `speculative_slang_task` | thinker 并行 | 投机执行，可丢弃 | 无副作用 |
| `_refresh_eot` | scheduler 内 fire-and-forget | 独立，不影响回复 | 无需干预 |
| `reconcile_self_mute_loop` | 后台循环 | 独立 | 无需干预 |
| DreamAgent | 独立后台 | 不经过 chat() | 无需干预 |
| ScheduleGenerator | 定时生成 | 不经过 chat() | 需独立 Arbiter 钩子 |

### 10.6 Arbiter 最佳注入点

推荐 5 个注入位置（按时序）：

**① Pre-fire Gate**（替代/增强 scheduler 概率判定）

- 位置：`scheduler.notify()` 内，概率 roll 之后、`_fire()` 之前
- 输入：pending messages、trigger context、mood、RWS score
- 决策：commit / abort / defer
- 延迟预算：50ms（与 RWS 并行）

**② Post-thinker Gate**（替代/增强 thinker wait 决策）

- 位置：`chat()` 内，thinker 返回后、prompt 构建前
- 输入：thinker decision、conversation context、trigger mode
- 决策：commit / abort / revise-tone
- 延迟预算：90ms

**③ Pre-send Gate**（后处理完成后、首段发送前）

- 位置：`chat()` 内，guardrail + rewrite 完成后、`on_segment` 调用前
- 输入：完整 reply text、guardrail hits、drift score
- 决策：commit / abort / revise
- 延迟预算：100ms（用户尚未看到任何内容）

**④ Inter-segment Gate**（分段间隙）

- 位置：segment 循环中 `asyncio.sleep(delay)` 期间
- 输入：已发送 segments、剩余 segments、新到达的 pending messages
- 决策：continue / truncate
- 延迟预算：与 segment delay 重叠（0.8s 内）

**⑤ Tool-call Gate**（tool 执行前）

- 位置：tool loop 内，`self._tools.call()` 之前
- 输入：tool name、tool input、当前 round
- 决策：allow / block / substitute
- 延迟预算：50ms

### 10.7 当前架构缺口（需改造以支持 Arbiter）

| # | 缺口 | 影响 | 改造方向 |
|---|------|------|----------|
| 1 | streaming_segment 与 guardrail 互斥 | `sentinel_guardrail_enabled` 时强制关闭 streaming，Arbiter 无法在流式模式下做 post-generation 审查 | 引入 streaming-aware guardrail 或 buffer-then-release 模式 |
| 2 | send_sticker 不可逆 | tool 执行是 fire-and-forget，一旦成功消息已发送到 QQ | Arbiter 需在 tool dispatch 前拦截（Tool-call Gate） |
| 3 | on_segment 回调无返回值 | 当前 `Callable[[str], Awaitable[None]]`，无法反馈"被拦截" | 改为返回 bool 或引入 abort signal |
| 4 | pause_then_extend 无外部取消信号 | 虽支持 `CancelledError`，但没有优雅的 abort 机制 | 注入 `abort_event: asyncio.Event` |
| 5 | scheduler slot.running_task 粒度太粗 | cancel 整个 task 丢失所有中间状态 | 细粒度 checkpoint + partial-rollback |
| 6 | timeline 写入时机 | `timeline.add()` 在所有 segments 发送完成后才写入 | inter-segment 截断时需只写入已发送部分 |

### 10.8 未来扩展性考虑

| 模态 | Arbiter 要求 | 备注 |
|------|-------------|------|
| 语音消息 | TTS 合成前增加 Arbiter 检查点，合成后不可逆 | 生成成本高于文本 |
| 视频回复 | 生成前决策，生成后不可逆 | 同理 |
| 多模态混合（文字+表情+语音） | 对整个 reply plan 做原子决策，而非逐模态判断 | 需要 reply plan 抽象层 |
| 群内多 bot 协调 | bot_pair_guard 已有雏形，Arbiter 可复用其 suppression 信号 | 扩展为双向协商 |

### 10.9 审计结论

Arbiter 架构与现有工作流的集成是**可行且低侵入**的：

1. 5 个注入点中，④ Inter-segment Gate 和 ① Pre-fire Gate 改动最小（利用已有的 sleep 窗口和概率判定分支）
2. 6 个架构缺口中，#3（on_segment 返回值）和 #4（pause_then_extend abort event）是最小必要改造
3. 不可逆操作（send_sticker、已发送 segments）通过 Tool-call Gate 前置拦截解决，不需要"撤回"能力
4. ScheduleGenerator 独立于 chat() 路径，需要单独的 Arbiter 钩子（Arbiter-C 的 post-reply 角色可覆盖）
5. 未来多模态扩展需要引入 reply plan 抽象层，但不阻塞当前文本域的实现
