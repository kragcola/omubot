# 群聊连发消息 Arbiter 架构 — 派单版执行追踪

> 状态：2026-05-28 立。本文是 [连发消息分裂回复方案](./fix-at-mention-burst-batching.md) 的执行版派单表。
>
> 用途：由执行者按 Phase 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §8 状态表的 ⏳→✅。
>
> **执行原则**（覆盖主线任何不一致项）：
>
> 1. **每条独立 commit**。Phase 间严格串行，Phase 内可并行。
> 2. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒回滚开关**，缺一不通过验收。
> 3. **不允许修改人设文件**（source.md / instruction.md）——所有方案纯运行时 / 代码层。
> 4. **Arbiter 调用超时 / 不可用时必须 fallback 到"立即 fire"**——不比现状差。

---

## 0. 背景速览（执行者必读）

### 0.1 问题是什么

用户连续发送 `@bot 别睡`（15:04:56）+ `来烤`（15:04:59），bot 对两条分别回复形成错位感。根因：当前 `@bot` 消息到达后立即 fire LLM 生成，不等后续消息。

### 0.2 解决思路

用**并发短 LLM 调用**（Arbiter）为主回复工作流提供实时控制信号。Arbiter 是 DeepSeek V4-Flash 的极短调用（90-165 tokens, 50-150ms），做判断不做生成。

三个 Arbiter 角色：

| 角色 | 职责 | 触发时机 |
|------|------|----------|
| **Arbiter-A** | 判断用户是否说完（Completeness Judge） | 每条新 @bot 消息到达时 |
| **Arbiter-B** | 判断是否中断正在发送的回复（Interruption Judge） | 主 pipeline 生成中/发送中 + 新消息到达 |
| **Arbiter-C** | 判断是否需要自修正（Post-Reply Correction Judge） | bot 回复完成后 30s 内收到同用户新消息 |

### 0.3 关键约束

- DeepSeek V4-Flash 并发上限 500-5000，Arbiter 仅增加 +1 并发
- 日成本增量 ≈ ¥0.02（可忽略）
- Arbiter 与主 pipeline 无 prefix cache 竞争（零重叠）
- 所有 Arbiter 超时/失败 fallback 到当前行为（不比现状差）

### 0.4 核心文件地图

| 文件 | 职责 | 关键行号 |
|------|------|----------|
| `services/scheduler.py` | 消息调度、fire 决策 | `notify()`:291, `_fire()`:738, `_do_chat()`:895, `GroupSlot`:93 |
| `services/llm/client.py` | LLM 调用、分段发送、pause_then_extend | `chat()`:3630, `_stream_with_segments()`:2487, `_maybe_extend()`:2949, `on_segment`:2469 |
| `kernel/router.py` | 消息路由、trigger 构建 | `group_listener`, `_last_assistant_replied_to_user`:469 |
| `kernel/config.py` | 配置模型 | `HumanizationConfig`:1203, `CoalesceConfig`:1849 |
| `services/llm/llm_request.py` | LLM 请求封装 | `apply_cache_breakpoints`:308 |

### 0.5 现有回复流程（简化）

```text
@bot 消息到达
  → scheduler.notify() — 判定 is_at=True
  → _fire() — 立即创建 _do_chat task
  → _do_chat() — 获取 chat_lock
  → client.chat() — thinker → prompt → LLM generate → 后处理 → 分段发送
  → _maybe_extend() — pause_then_extend 追发
```

Arbiter 改造后：

```text
@bot 消息到达
  → scheduler.notify() — 判定 is_at=True
  → 启动 Arbiter-A 循环（不立即 fire）
  → Arbiter-A 每 0.3s 判断 completeness
  → complete=true → _fire()
  → _do_chat() → client.chat() → 分段发送
  → 段间 pause 时检查新消息 → Arbiter-B 判断是否中断
  → 回复完成后 30s 内新消息 → Arbiter-C 判断是否自修正
```

---

## 1. 依赖关系与 Phase 编排

```text
Phase 0: Arbiter 客户端封装（无外部依赖）
    │
    ├── Phase 1: Arbiter-A 接入 scheduler（依赖 Phase 0）
    │
    ├── Phase 2: Arbiter-B 接入段间中断（依赖 Phase 0 + on_segment 改造）
    │
    └── Phase 3: Arbiter-C 接入发后自修正（依赖 Phase 0 + followup 检测）
         │
         └── Phase 4: 灰度观测 + 调参（依赖 Phase 1-3 上线）
```

Phase 0 是所有后续 Phase 的前置。Phase 1/2/3 之间无依赖，可并行。

---

## 2. Phase 0 — Arbiter 客户端封装

> 新建 `services/llm/arbiter.py`。纯封装层：prompt template + JSON parse + timeout + fallback。

### 2.1 任务表

| 编号 | 一句话 | 改动文件 | D1 grep 锁 | 回滚 |
|---|---|---|---|---|
| **P0.1** | 新建 `services/llm/arbiter.py`，定义 `ArbiterClient` 类 | `services/llm/arbiter.py`（新建，≈120 行） | `grep -rn "ArbiterClient\|arbiter" services/` 仅命中新文件 | `git rm services/llm/arbiter.py` |
| **P0.2** | 新建 `kernel/config.py` 中 `ArbiterConfig` 子模型 | `kernel/config.py`（+20 行） | `grep -n "ArbiterConfig\|arbiter" kernel/config.py` 仅命中新增段 | `git restore kernel/config.py` |
| **P0.3** | 新建 `tests/test_arbiter.py` 单元测试 | `tests/test_arbiter.py`（新建，≈80 行） | — | `git rm tests/test_arbiter.py` |
| **P0.4** | `config/config.json` 追加 `arbiter` 段（默认 disabled） | `config/config.json`（+8 行） | `grep -n "arbiter" config/config.json` 仅 1 处 | `git restore config/config.json` |

### 2.2 P0.1 详细设计 — `ArbiterClient`

```python
# services/llm/arbiter.py

class ArbiterClient:
    """Lightweight concurrent LLM judge for real-time control signals."""

    def __init__(self, config: ArbiterConfig, http_session: aiohttp.ClientSession):
        ...

    async def judge_completeness(
        self, pending_messages: list[PendingMessage]
    ) -> CompletenessResult:
        """Arbiter-A: 判断用户是否说完当前这轮话。"""
        ...

    async def judge_interruption(
        self, already_sent: list[str], unsent: list[str], new_messages: list[str]
    ) -> InterruptionResult:
        """Arbiter-B: 判断是否中断正在发送的回复。"""
        ...

    async def judge_correction(
        self, bot_reply: str, new_message: str
    ) -> CorrectionResult:
        """Arbiter-C: 判断是否需要自修正。"""
        ...
```

**关键实现要求**：

1. **固定 system prompt**：每个 Arbiter 角色的 system prompt 是常量字符串（不动态拼接），确保 prefix cache 可命中。
2. **max_tokens=15**：Arbiter 只输出 JSON 判断，不生成长文本。
3. **timeout=500ms**：超时后返回 fallback 默认值（A→complete=true, B→continue, C→no_correction）。
4. **JSON parse 容错**：model 输出非法 JSON 时返回 fallback 默认值 + 记录 warning 日志。
5. **独立 task 标记**：usage 记录中 `task="arbiter"`，与主 pipeline `task="main"` 区分。
6. **不复用主 pipeline 的 `call_api`**：Arbiter 是独立的轻量 HTTP 调用，不走 `LLMClient` 的 tool loop / streaming / cache breakpoint 逻辑。直接用 `aiohttp` POST DeepSeek `/chat/completions`。

**数据结构**：

```python
@dataclass
class PendingMessage:
    content: str
    user_id: str
    timestamp: float  # unix epoch

@dataclass
class CompletenessResult:
    complete: bool
    confidence: float  # 0.0-1.0
    fallback: bool = False  # True if timeout/parse error

@dataclass
class InterruptionResult:
    action: Literal["continue", "abort_unsent", "revise"]
    reason: str = ""
    fallback: bool = False

@dataclass
class CorrectionResult:
    needs_correction: bool
    correction_type: Literal["retract", "amend", "acknowledge"] | None = None
    fallback: bool = False
```

### 2.3 P0.2 详细设计 — `ArbiterConfig`

在 `kernel/config.py` 中 `CoalesceConfig` 之前（约 line 1849）新增：

```python
class ArbiterConfig(BaseModel):
    """Dual-process Arbiter for burst message handling."""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    api_base: str = ""  # 空字符串时复用 llm.api_base
    api_key: str = ""   # 空字符串时复用 llm.api_key
    model: str = ""     # 空字符串时复用 llm.model
    timeout_ms: int = 500
    completeness_confidence_threshold: float = 0.8
    completeness_poll_interval_s: float = 0.3
    completeness_max_wait_s: float = 5.0
    interruption_enabled: bool = True
    correction_enabled: bool = True
    correction_window_s: float = 30.0
```

在 `BotConfig`（line 1870）中追加字段：

```python
arbiter: ArbiterConfig = ArbiterConfig()
```

### 2.4 P0.3 测试要求

| 测试名 | 断言 |
|--------|------|
| `test_arbiter_completeness_returns_result` | mock HTTP 返回合法 JSON → 解析为 CompletenessResult |
| `test_arbiter_completeness_timeout_fallback` | mock HTTP 超时 → 返回 fallback(complete=True) |
| `test_arbiter_completeness_invalid_json_fallback` | mock HTTP 返回非 JSON → 返回 fallback + warning log |
| `test_arbiter_interruption_returns_result` | mock HTTP 返回 `{"action":"abort_unsent"}` → 解析正确 |
| `test_arbiter_correction_returns_result` | mock HTTP 返回 `{"needs_correction":true}` → 解析正确 |
| `test_arbiter_config_defaults` | `ArbiterConfig()` 所有字段有合理默认值 |
| `test_arbiter_disabled_skips_call` | `enabled=False` 时所有 judge 方法直接返回 fallback，不发 HTTP |

### 2.5 P0.4 config.json 追加

```json
"arbiter": {
    "enabled": false,
    "timeout_ms": 500,
    "completeness_confidence_threshold": 0.8,
    "completeness_poll_interval_s": 0.3,
    "completeness_max_wait_s": 5.0,
    "interruption_enabled": true,
    "correction_enabled": true,
    "correction_window_s": 30.0
}
```

`api_base` / `api_key` / `model` 留空，运行时从 `config.llm` 继承。

### 2.6 Phase 0 验收标准

```bash
uv run pytest tests/test_arbiter.py -v  # 7 tests passed
uv run ruff check services/llm/arbiter.py kernel/config.py
uv run pyright services/llm/arbiter.py kernel/config.py
grep -rn "arbiter" services/ kernel/ config/ tests/ | wc -l  # 预期 ≤ 30 行
```

---

## 3. Phase 1 — Arbiter-A 接入 scheduler（Completeness Judge）

> 解决 F1：用户连发 2-3 条表达同一件事，bot 只看到第一条就开始生成。
>
> 改造 `scheduler.notify()`：`is_at=True` 时不立即 fire，启动 Arbiter-A 循环判断用户是否说完。

### 3.1 任务表

| 编号 | 一句话 | 改动文件 | D1 grep 锁 | 回滚 |
|---|---|---|---|---|
| **P1.1** | `scheduler.notify()` 中 `is_at` 分支改为启动 `_arbiter_completeness_loop` | `services/scheduler.py`（≈+40 行） | `grep -n "arbiter\|_arbiter_completeness" services/scheduler.py` 仅命中新增代码 | `git restore services/scheduler.py` |
| **P1.2** | `GroupSlot` 新增 `burst_pending: list`, `arbiter_task: Task` 字段 | `services/scheduler.py`（≈+8 行） | 同上 | 同上 |
| **P1.3** | 新增 `tests/test_arbiter_scheduler.py` 集成测试 | `tests/test_arbiter_scheduler.py`（新建，≈100 行） | — | `git rm tests/test_arbiter_scheduler.py` |

### 3.2 P1.1 详细设计 — `notify()` 改造

**当前行为**（[services/scheduler.py:291-350](../../services/scheduler.py#L291-L350)）：

```python
def notify(self, group_id, *, trigger, ...):
    if is_at:
        # 立即 fire（当前行为）
        if slot.running_task and not slot.running_task.done():
            slot.pending_at = True
            return
        self._fire(group_id)
```

**改造后**：

```python
def notify(self, group_id, *, trigger, ...):
    if is_at:
        if not self._arbiter_enabled:
            # Arbiter 未启用，保持原行为
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                return
            self._fire(group_id)
            return

        # Arbiter 启用：收集 pending，启动/重置 completeness 循环
        slot.burst_pending.append(PendingMessage(
            content=msg_text, user_id=user_id, timestamp=time.time()
        ))

        if slot.arbiter_task is None or slot.arbiter_task.done():
            slot.arbiter_task = asyncio.create_task(
                self._arbiter_completeness_loop(group_id)
            )
        # 如果 arbiter_task 已在跑，新消息自然被下一轮 poll 看到
```

**`_arbiter_completeness_loop` 实现**：

```python
async def _arbiter_completeness_loop(self, group_id: str) -> None:
    """Poll Arbiter-A until user is done talking, then fire."""
    slot = self._slots[group_id]
    config = self._config.arbiter
    elapsed = 0.0

    while elapsed < config.completeness_max_wait_s:
        await asyncio.sleep(config.completeness_poll_interval_s)
        elapsed += config.completeness_poll_interval_s

        if not slot.burst_pending:
            return  # 被其他路径消费了

        result = await self._arbiter.judge_completeness(slot.burst_pending)

        if result.complete and result.confidence >= config.completeness_confidence_threshold:
            break  # 用户说完了

    # 超时或判定完成 → fire
    # 把 burst_pending 合并为一条 context 传给 _fire
    self._fire(group_id, burst_context=slot.burst_pending)
    slot.burst_pending = []
    slot.arbiter_task = None
```

**关键行为保证**：

1. `running_task` 正在跑时新 @bot 到达：仍走 `pending_at=True`（Arbiter 不干预已在跑的生成）
2. Arbiter 超时（>5s）：强制 fire，用户最多等 5s
3. Arbiter 不可用：fallback `complete=True` → 立即 fire（等效于无 Arbiter）
4. 非 @bot 消息（概率 fire / followup）：不走 Arbiter，保持原路径

### 3.3 P1.2 — GroupSlot 扩展

在 `GroupSlot.__init__`（line 93-112）追加：

```python
self.burst_pending: list[PendingMessage] = []
self.arbiter_task: asyncio.Task[None] | None = None
```

在 `_reset_slot`（如有）或 `_fire` 完成后清理：

```python
slot.burst_pending = []
if slot.arbiter_task and not slot.arbiter_task.done():
    slot.arbiter_task.cancel()
slot.arbiter_task = None
```

### 3.4 P1.3 测试要求

| 测试名 | 断言 |
|--------|------|
| `test_at_message_with_arbiter_enabled_does_not_fire_immediately` | notify(is_at=True) 后 slot.running_task 仍为 None（未立即 fire） |
| `test_arbiter_completeness_fires_on_complete` | mock arbiter 返回 complete=True → _fire 被调用 |
| `test_arbiter_timeout_fires_anyway` | mock arbiter 始终返回 complete=False → 5s 后 _fire 被调用 |
| `test_arbiter_disabled_fires_immediately` | config.arbiter.enabled=False → 保持原行为立即 fire |
| `test_burst_pending_accumulates_messages` | 连续 2 条 notify → slot.burst_pending 长度 2 |
| `test_running_task_blocks_arbiter` | slot.running_task 非 None 时 → pending_at=True，不启动 arbiter |

### 3.5 Phase 1 验收标准

```bash
uv run pytest tests/test_arbiter_scheduler.py tests/test_arbiter.py -v  # 全绿
uv run ruff check services/scheduler.py
uv run pyright services/scheduler.py
# 手动验证：config.json arbiter.enabled=true → @bot 消息后 ~0.3-5s 才回复
```

---

## 4. Phase 2 — Arbiter-B 接入段间中断（Interruption Judge）

> 解决 F2：bot 已开始发送分段回复，用户追加了关键补充。
>
> 改造 `on_segment` 回调与段间 delay：在 inter-segment pause 期间检查新消息，调用 Arbiter-B 判断是否中断。

### 4.1 前置改造 — `on_segment` 回调签名升级

**当前签名**（[services/llm/client.py:2469](../../services/llm/client.py#L2469)）：

```python
on_segment: Callable[[str], Awaitable[None]] | None
```

**改造为**：

```python
on_segment: Callable[[str], Awaitable[bool]] | None
# 返回 True = 正常发送；False = 被 Arbiter 拦截，中止后续段
```

**影响范围**（D1 grep `on_segment` 全仓）：

- `services/scheduler.py` `_do_chat` 中注册的 lambda — 需改为返回 bool
- `services/llm/client.py` 内部所有 `await on_segment(...)` 调用点 — 需检查返回值
- `tests/` 中 mock on_segment — 需改为返回 True

### 4.2 任务表

| 编号 | 一句话 | 改动文件 | D1 grep 锁 | 回滚 |
|---|---|---|---|---|
| **P2.1** | `on_segment` 签名改为返回 bool；所有调用点检查返回值 | `services/llm/client.py`（≈+15 行改动） | `grep -rn "on_segment\|Callable\[\[str\]" services/ tests/` 全部命中已改 | `git restore services/llm/client.py` |
| **P2.2** | `_do_chat` 中 on_segment lambda 加入 Arbiter-B 检查 | `services/scheduler.py`（≈+30 行） | `grep -n "arbiter.*interrupt\|judge_interruption" services/scheduler.py` | `git restore services/scheduler.py` |
| **P2.3** | 新增 `SegmentAborted` 异常 + client.py 段循环中 catch | `services/llm/client.py`（≈+20 行） | `grep -n "SegmentAborted" services/` | `git restore services/llm/client.py` |
| **P2.4** | 新增 `tests/test_arbiter_interruption.py` | `tests/test_arbiter_interruption.py`（新建，≈80 行） | — | `git rm tests/test_arbiter_interruption.py` |

### 4.3 P2.2 详细设计 — 段间 Arbiter-B 检查

**在 `_do_chat` 中注册的 on_segment 回调**：

```python
# services/scheduler.py — _do_chat() 内
segments_sent: list[str] = []

async def on_segment_with_arbiter(text: str) -> bool:
    """发送一段文本，段间检查是否需要中断。"""
    # 先发送当前段
    await self._send_to_group(group_id, text)
    segments_sent.append(text)

    # 检查是否有新消息到达
    new_msgs = self._timeline.get_messages_since(group_id, generation_start_time)
    if not new_msgs or not self._config.arbiter.interruption_enabled:
        return True  # 继续

    # 调用 Arbiter-B
    result = await self._arbiter.judge_interruption(
        already_sent=segments_sent,
        unsent=["(remaining segments not yet generated)"],
        new_messages=[m.content for m in new_msgs],
    )

    if result.action == "abort_unsent":
        logger.info("arbiter_b_abort | group=%s reason=%s", group_id, result.reason)
        return False  # 中止后续段
    if result.action == "revise":
        logger.info("arbiter_b_revise | group=%s reason=%s", group_id, result.reason)
        # 把新消息加入 burst_pending，触发修正生成
        slot.burst_pending.extend(new_msgs)
        return False  # 中止后续段，后续由 correction 路径处理

    return True  # continue
```

**client.py 段循环改造**：

在 `_stream_with_segments`（line 2487）和普通分段发送循环中，`await on_segment(text)` 改为：

```python
should_continue = await on_segment(text)
if not should_continue:
    raise SegmentAborted(sent_segments=segments_sent)
```

`SegmentAborted` 在 `chat()` 顶层 catch：

```python
try:
    # ... 主生成 + 分段发送 ...
except SegmentAborted as e:
    # 已发送的段写入 timeline（部分回复）
    if e.sent_segments:
        partial_reply = "\n".join(e.sent_segments)
        # timeline 只记录已发送部分
        return partial_reply
```

### 4.4 关键行为保证

1. **已发送的段不撤回**：Arbiter-B 只影响未发送的段，已发出的消息不可逆
2. **timeline 写入部分回复**：中断后 timeline 记录已发送内容，避免 context 丢失
3. **Arbiter-B 超时**：fallback `action="continue"` → 不中断（等效于无 Arbiter）
4. **段间 delay 重叠**：Arbiter-B 调用（80-150ms）与 inter-segment delay（800ms）重叠执行，不额外增加延迟
5. **pause_then_extend 兼容**：`_maybe_extend` 中的段发送同样走 on_segment 回调，自然获得 Arbiter-B 保护

### 4.5 P2.4 测试要求

| 测试名 | 断言 |
|--------|------|
| `test_segment_aborted_on_arbiter_abort` | mock arbiter 返回 abort_unsent → 后续段不发送 |
| `test_segment_continues_on_arbiter_continue` | mock arbiter 返回 continue → 所有段正常发送 |
| `test_segment_abort_writes_partial_timeline` | 中断后 timeline 只含已发送段 |
| `test_no_new_messages_skips_arbiter_call` | 无新消息时不调用 Arbiter-B |
| `test_arbiter_b_timeout_continues` | mock arbiter 超时 → 继续发送 |

### 4.6 Phase 2 验收标准

```bash
uv run pytest tests/test_arbiter_interruption.py tests/test_arbiter.py -v  # 全绿
uv run pytest tests/test_streaming_hook.py tests/test_extend_call.py -v  # 回归不破
uv run ruff check services/llm/client.py services/scheduler.py
uv run pyright services/llm/client.py services/scheduler.py
```

---

## 5. Phase 3 — Arbiter-C 接入发后自修正（Post-Reply Correction Judge）

> 解决 F3：bot 已完整回复，用户 30s 内补充了改变语义的信息。
>
> 改造 `kernel/router.py` 的 followup 检测路径：在 `_last_assistant_replied_to_user` 为 True 且时间窗口内时，调用 Arbiter-C 判断是否需要自修正。

### 5.1 任务表

| 编号 | 一句话 | 改动文件 | D1 grep 锁 | 回滚 |
|---|---|---|---|---|
| **P3.1** | router.py 中 followup 路径加入 Arbiter-C 检查 | `kernel/router.py`（≈+25 行） | `grep -n "arbiter.*correction\|judge_correction" kernel/router.py` | `git restore kernel/router.py` |
| **P3.2** | scheduler.py 中 `_do_chat` 记录 `last_reply_time` + `last_reply_content` | `services/scheduler.py`（≈+5 行） | `grep -n "last_reply_time\|last_reply_content" services/scheduler.py` | `git restore services/scheduler.py` |
| **P3.3** | 新增 `tests/test_arbiter_correction.py` | `tests/test_arbiter_correction.py`（新建，≈60 行） | — | `git rm tests/test_arbiter_correction.py` |

### 5.2 P3.1 详细设计 — followup 路径改造

**当前 followup 检测**（[kernel/router.py:469-514](../../kernel/router.py#L469-L514)）：

`_last_assistant_replied_to_user` 返回 True 时，消息被标记为 `directed_followup`，走概率回复。

**改造后**：在 `_last_assistant_replied_to_user` 返回 True 的基础上，额外调用 Arbiter-C：

```python
# kernel/router.py — group_listener 中 followup 检测后
if is_followup and arbiter_enabled:
    slot = scheduler.get_slot(group_id)
    if (
        slot.last_reply_content
        and time.time() - slot.last_reply_time < config.arbiter.correction_window_s
    ):
        correction = await arbiter.judge_correction(
            bot_reply=slot.last_reply_content,
            new_message=msg_text,
        )
        if correction.needs_correction:
            # 绕过概率判定，强制触发回复
            # trigger.mode 标记为 "correction" 让 thinker 知道这是自修正
            trigger = TriggerContext(
                mode="correction",
                correction_type=correction.correction_type,
                original_reply=slot.last_reply_content,
            )
            scheduler.notify(group_id, trigger=trigger, force=True)
            return
```

**自修正回复的 prompt 注入**：

当 `trigger.mode == "correction"` 时，在 system prompt 末尾追加一行指令：

```
[你刚才的回复可能不完全准确。用户补充了新信息，请自然地修正或补充你的回答，不要生硬地说"抱歉"。]
```

这行指令在 `services/llm/client.py` 的 prompt 构建阶段注入（`_build_system_blocks` 或等价位置）。

### 5.3 P3.2 — 记录最近回复

在 `_do_chat` 完成后记录：

```python
# services/scheduler.py — _do_chat() 末尾
if reply_text:
    slot.last_reply_time = time.time()
    slot.last_reply_content = reply_text
```

`GroupSlot.__init__` 追加：

```python
self.last_reply_time: float = 0.0
self.last_reply_content: str = ""
```

### 5.4 关键行为保证

1. **不重复触发**：Arbiter-C 只在 `correction_window_s`（30s）内触发一次；触发后 `last_reply_content` 清空
2. **与 followup 互补**：Arbiter-C 判定 `needs_correction=False` 时，消息仍走正常 followup 概率路径
3. **force_reply 语义**：correction 触发的回复是 force_reply，不受概率 roll 影响
4. **Arbiter-C 超时**：fallback `needs_correction=False` → 走正常 followup 路径（不比现状差）

### 5.5 P3.3 测试要求

| 测试名 | 断言 |
|--------|------|
| `test_correction_triggered_within_window` | 30s 内新消息 + arbiter 返回 needs_correction=True → force 触发回复 |
| `test_correction_not_triggered_outside_window` | 31s 后新消息 → 不调用 Arbiter-C |
| `test_correction_false_falls_through_to_followup` | arbiter 返回 needs_correction=False → 走正常 followup 路径 |
| `test_correction_clears_last_reply` | correction 触发后 slot.last_reply_content 清空 |

### 5.6 Phase 3 验收标准

```bash
uv run pytest tests/test_arbiter_correction.py tests/test_arbiter.py -v  # 全绿
uv run pytest tests/test_scheduler.py -v  # 回归不破
uv run ruff check kernel/router.py services/scheduler.py
uv run pyright kernel/router.py services/scheduler.py
```

---

## 6. Phase 4 — 灰度观测 + 调参

> Phase 1-3 上线后进入观测期。不写新代码，只调 config 参数 + 迭代 Arbiter prompt。

### 6.1 灰度策略

| 阶段 | 范围 | 持续时间 | 出口条件 |
|------|------|----------|----------|
| 灰度-1 | `runtime_groups` 限定 993065015 单群 | 24h | 出口指标 ≥ 6/8 达标 |
| 灰度-2 | 扩到 993065015 + 984198159 双群 | 48h | 同上 + 无用户投诉 |
| 全量 | `arbiter.enabled=true`（全群生效） | 持续 | — |

### 6.2 出口指标矩阵

| 指标 | 目标 | 观测方式 | 备注 |
|------|------|----------|------|
| 连发消息合并率 | ≥ 80%（2 条以内间隔 <3s 的 @bot 消息被合并处理） | `storage/logs/` grep `arbiter_a_fire` 日志 | 核心指标 |
| Arbiter-A 平均延迟 | ≤ 200ms | usage.db `task=arbiter` 的 latency_ms | 不含 poll 间隔 |
| Arbiter-A fallback 率 | ≤ 5% | grep `arbiter.*fallback=True` | 超时/parse 错误 |
| 用户等待时间（@bot → 首段到达） | ≤ 3s（单条）/ ≤ 6s（连发） | 日志时间戳差 | 不比现状差太多 |
| Arbiter-B 中断触发率 | ≤ 10% per reply | grep `arbiter_b_abort\|arbiter_b_revise` | 过高说明 prompt 过敏 |
| Arbiter-C 自修正触发率 | ≤ 5% per reply | grep `arbiter_c_correction` | 过高说明 window 太宽 |
| 主 pipeline cache hit rate | 保持 ≥ 85% | usage.db `task=main` 的 `prompt_cache_hit_tokens / prompt_tokens` | 不应下降 |
| 用户主观验收 | 「连发不再分裂回复」 | 用户反馈 | — |

### 6.3 调参指南

| 参数 | 默认值 | 调整方向 |
|------|--------|----------|
| `completeness_confidence_threshold` | 0.8 | 降低 → 更快 fire（减少等待）；升高 → 更多合并 |
| `completeness_poll_interval_s` | 0.3 | 降低 → 更灵敏但更多 API 调用；升高 → 更省但更迟钝 |
| `completeness_max_wait_s` | 5.0 | 降低 → 用户等待上限更短；升高 → 允许更长连发 |
| `correction_window_s` | 30.0 | 降低 → 减少误触发；升高 → 覆盖更多补充场景 |
| Arbiter prompt 措辞 | 见 §2.2 | 迭代优化判断准确率 |

### 6.4 Phase 4 不需要代码改动

Phase 4 的所有操作都是：

1. 修改 `config/config.json` 中 `arbiter` 段的参数值
2. `docker compose restart bot`（D6：config-only 不需要 rebuild）
3. 观察 30 分钟 → 填出口指标 → 决定下一步

---

## 7. 失败模式与回滚

### 7.1 逐 Phase 回滚

| Phase | 回滚命令 | 效果 |
|-------|----------|------|
| Phase 0 | `git rm services/llm/arbiter.py tests/test_arbiter.py && git restore kernel/config.py config/config.json` | 删除 Arbiter 客户端 |
| Phase 1 | `git restore services/scheduler.py` | scheduler 恢复立即 fire |
| Phase 2 | `git restore services/llm/client.py services/scheduler.py` | on_segment 恢复无返回值 |
| Phase 3 | `git restore kernel/router.py services/scheduler.py` | followup 恢复原路径 |
| 全部 | `config/config.json` 中 `arbiter.enabled=false` + restart | 运行时关闭，零代码回滚 |

### 7.2 运行时 kill switch

**最快回滚**（30 秒，不需要 git）：

```bash
# config/config.json 中 "arbiter": {"enabled": false}
docker compose restart bot
```

`arbiter.enabled=false` 时所有 Arbiter 路径短路到 fallback 默认值，行为等同于改造前。这是设计上的核心安全保证。

### 7.3 已知风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Arbiter API 持续不可用 | 低 | 所有 @bot 消息延迟 500ms（等超时） | fallback 后立即 fire |
| Arbiter 误判 complete=false 循环 | 中 | 用户等待 5s（max_wait_s cap） | 绝对 cap 兜底 |
| on_segment 返回值改造破坏现有 mock | 低 | 测试失败 | Phase 2 同步修复所有 mock |
| SegmentAborted 异常未被正确 catch | 低 | 回复丢失 | Phase 2 测试覆盖 |
| correction 过度触发 | 中 | bot 频繁自修正显得不自信 | correction_window_s 调低 / Arbiter-C prompt 调严 |

---

## 8. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | Phase | 状态 | 落地证据 / 备注 |
|------|-------|------|----------------|
| **P0.1** | 0 | ✅ | `services/llm/arbiter.py` 已落地 `ArbiterClient` / 3 judge / fallback / usage 记录 |
| **P0.2** | 0 | ✅ | `kernel/config.py` 已新增 `ArbiterConfig` + `BotConfig.arbiter` |
| **P0.3** | 0 | ✅ | `tests/test_arbiter.py` 7 条单测已通过 |
| **P0.4** | 0 | ✅ | `config/config.json` 已存在 `arbiter` 段；当前已切入 `灰度-1` 单群入口态 |
| **P1.1** | 1 | ✅ | `scheduler.notify()` 已接 Arbiter-A completeness loop |
| **P1.2** | 1 | ✅ | `GroupSlot` 已新增 `burst_pending` / `arbiter_task` / `last_reply_*` |
| **P1.3** | 1 | ✅ | `tests/test_arbiter_scheduler.py` 6 条集成测试已通过 |
| **P2.1** | 2 | ✅ | `on_segment` 已升级为 `Awaitable[bool]` |
| **P2.2** | 2 | ✅ | `_do_chat` 已接 Arbiter-B interrupt 判断 |
| **P2.3** | 2 | ✅ | `SegmentAborted` + partial timeline 写入已落地 |
| **P2.4** | 2 | ✅ | `tests/test_arbiter_interruption.py` 5 条测试已补齐并通过 |
| **P3.1** | 3 | ✅ | `kernel/router.py` 已接 Arbiter-C correction 路径 |
| **P3.2** | 3 | ✅ | `scheduler` 已稳定记录/清空 `last_reply_time/content` |
| **P3.3** | 3 | ✅ | `tests/test_arbiter_correction.py` 已新增并通过 |
| **灰度-1** | 4 | 🟡 | `arbiter.enabled=true` + `runtime_groups=["993065015"]` 已入场；24h 观测待执行 |
| **灰度-2** | 4 | ⏳ | 双群 48h 观测 |
| **全量** | 4 | ⏳ | arbiter.enabled=true 全群 |

---

## 9. 执行者交接说明

### 9.1 领单顺序

1. **Phase 0 全部**（P0.1 → P0.2 → P0.3 → P0.4）：纯新增文件，无冲突风险，建议 1 个 commit
2. **Phase 1**（P1.1 → P1.2 → P1.3）：改 scheduler，建议 1 个 commit
3. **Phase 2**（P2.1 → P2.2 → P2.3 → P2.4）：改 client.py + scheduler，建议 1 个 commit
4. **Phase 3**（P3.1 → P3.2 → P3.3）：改 router + scheduler，建议 1 个 commit
5. **Phase 4**：纯 config 调参，不需要 commit

Phase 1/2/3 之间无代码依赖（都只依赖 Phase 0），可并行开发。但建议串行合入避免 merge 冲突。

### 9.3 本地执行回填（2026-05-28，Codex）

#### Phase 0 完成记录

- 改动范围：`services/llm/arbiter.py` / `kernel/config.py` / `config/config.json` / `tests/test_arbiter.py`
- 改动行数声明：`kernel/config.py +282/-0`；`services/llm/arbiter.py` / `tests/test_arbiter.py` 为本轮新增文件（已在 D1 grep 实证中命中）
- D1 grep：
  - `rg -n "ArbiterClient|arbiter" services kernel plugins tests config/config.json` 命中 `services/llm/arbiter.py`、`kernel/config.py`、`config/config.json`、`tests/test_arbiter.py` 等预期路径
- D2 cancel-path：
  - `tests/test_arbiter.py::test_arbiter_completeness_timeout_fallback`
  - `tests/test_arbiter.py::test_arbiter_completeness_invalid_json_fallback`
  - `tests/test_arbiter.py::test_arbiter_disabled_skips_call`
- 验证：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter.py -q`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/arbiter.py kernel/config.py tests/test_arbiter.py`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/arbiter.py kernel/config.py tests/test_arbiter.py`
- 30 秒回滚：
  - `git rm services/llm/arbiter.py tests/test_arbiter.py && git restore kernel/config.py config/config.json`

#### Phase 1 完成记录

- 改动范围：`services/scheduler.py` / `tests/test_arbiter_scheduler.py`
- 改动行数声明：`services/scheduler.py +371/-9`（含 P2/P3 共用字段）；`tests/test_arbiter_scheduler.py` 为本轮新增文件
- D1 grep：
  - `rg -n "_arbiter_completeness_loop|burst_pending|arbiter_task|message_text" services/scheduler.py tests/test_arbiter_scheduler.py`
- D2 cancel-path：
  - `tests/test_arbiter_scheduler.py::test_arbiter_timeout_fires_anyway`
  - `services/scheduler.py::clear_pending()` / `close()` 已显式 cancel `arbiter_task`
- 验证：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter_scheduler.py tests/test_arbiter.py tests/test_scheduler.py -q`
  - 本轮定向回归汇总包含上述用例，结果 `95 passed`
- 30 秒回滚：
  - `git restore services/scheduler.py && git rm tests/test_arbiter_scheduler.py`

#### Phase 2 完成记录

- 改动范围：`services/llm/client.py` / `services/scheduler.py` / `tests/test_arbiter_interruption.py` 及受影响 mock 测试
- 改动行数声明：`services/llm/client.py +865/-72`；`services/scheduler.py +371/-9`（与 P1/P3 共用）；`tests/test_arbiter_interruption.py` 为本轮新增文件
- D1 grep：
  - `rg -n "judge_interruption|arbiter_b_interrupt|SegmentAborted|Awaitable\\[bool\\]" services/llm/client.py services/scheduler.py tests/test_arbiter_interruption.py`
- D2 cancel-path：
  - `tests/test_arbiter_interruption.py::test_arbiter_b_timeout_continues`
  - `tests/test_arbiter_interruption.py::test_segment_abort_writes_partial_timeline`
  - 既有回归：`tests/test_streaming_hook.py` / `tests/test_extend_call.py` / `tests/test_plan_then_utter.py`
- 验证：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter_interruption.py tests/test_arbiter.py tests/test_force_reply.py tests/test_scheduler.py -q`
  - 本轮定向回归汇总包含上述用例，结果 `95 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check ...`
  - `source ./scripts/dev/env.sh && uv run pyright ...`
- 30 秒回滚：
  - `git restore services/llm/client.py services/scheduler.py && git rm tests/test_arbiter_interruption.py`

#### Phase 3 完成记录

- 改动范围：`kernel/router.py` / `services/scheduler.py` / `plugins/chat/plugin.py` / `kernel/types.py` / `tests/test_arbiter_correction.py`
- 额外实况订正：
  - runtime wiring 原方案未显式覆盖 `plugin -> scheduler -> arbiter` 注入；本轮已在 `plugins/chat/plugin.py` 补齐 `set_arbiter()` 与 `arbiter_config=config.arbiter`
  - `scheduler.notify(..., message_text=...)` 已反向补到 `kernel/router.py::_notify_group_scheduler()`
- D1 grep：
  - `rg -n "judge_correction|arbiter_c_correction|mode=\"correction\"|set_arbiter|message_text" kernel/router.py plugins/chat/plugin.py services/scheduler.py tests/test_arbiter_correction.py`
- D2 cancel-path：
  - `tests/test_arbiter_correction.py::test_correction_not_triggered_outside_window`
  - `tests/test_arbiter.py::test_arbiter_disabled_skips_call`（Arbiter-C disabled/short-circuit）
- 验证：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter_correction.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_chat_plugin_humanization_wire.py tests/test_force_reply.py -q`
  - 本轮定向回归汇总包含上述用例，结果 `95 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check kernel/router.py plugins/chat/plugin.py kernel/types.py tests/test_arbiter_correction.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_chat_plugin_humanization_wire.py tests/test_force_reply.py`
  - `source ./scripts/dev/env.sh && uv run pyright kernel/router.py plugins/chat/plugin.py kernel/types.py tests/test_arbiter_correction.py tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_chat_plugin_humanization_wire.py tests/test_force_reply.py`
- 30 秒回滚：
  - `git restore kernel/router.py services/scheduler.py plugins/chat/plugin.py kernel/types.py && git rm tests/test_arbiter_correction.py`

#### 灰度-1 入口回填

- 已执行项：
  - `config/config.json` 已切为：
    - `"arbiter.enabled": true`
    - `"arbiter.runtime_groups": ["993065015"]`
  - `maintenance-log.md` 已补当日条目，记录入口态与回滚开关
- 尚未执行项：
  - 派单要求的 24h 观测
  - §6.2 的 8 项出口指标实测填表
- 观测期 kill switch：
  - `config/config.json` 中将 `"arbiter.enabled": false` 后 `docker compose restart bot`

### 9.2 执行者需要了解的项目知识

**如果你不熟悉这个项目，以下是最关键的几点**：

1. **DeepSeek V4-Flash** 是主 LLM，API 兼容 OpenAI 格式。Arbiter 直接 POST `/chat/completions`，不走项目内的 `LLMClient`。
2. **`services/scheduler.py`** 是消息调度核心。`notify()` 决定何时触发 LLM 生成，`_fire()` 创建异步任务，`_do_chat()` 执行生成。
3. **`services/llm/client.py`** 是 LLM 调用核心。`chat()` 是入口，内部有 thinker → prompt → generate → 后处理 → 分段发送 → pause_then_extend 的完整流程。
4. **`on_segment`** 是分段发送的回调。每生成一段文本就调用一次，由 scheduler 注册的 lambda 实际发送到 QQ。
5. **`GroupSlot`** 是每个群的运行时状态容器。`running_task` 是当前正在跑的生成任务，`pending_at` 标记有待处理的 @bot。
6. **config 优先级**：`config/config.json` 是运行时源（不是 config.toml）。改 config 后 `docker compose restart bot` 即生效。
7. **测试**：`uv run pytest` 跑全量；`uv run ruff check` 做 lint；`uv run pyright` 做类型检查。三者都必须通过。

### 9.3 Arbiter prompt 模板参考

执行者实现 P0.1 时需要写 Arbiter 的 system prompt。以下是参考模板（可迭代优化）：

**Arbiter-A（Completeness Judge）**：

```text
你是对话完整性判断器。根据用户最近发送的消息，判断用户是否说完了当前这轮话。
输出严格 JSON：{"complete": true/false, "confidence": 0.0-1.0}
判断依据：
- 消息末尾有句号/问号/感叹号 → 大概率说完
- 消息末尾有逗号/连词/省略号 → 大概率没说完
- 多条消息间隔 <3s 且最新一条很短 → 大概率还有后续
- 最新消息是对前一条的补充说明 → 大概率没说完
只输出 JSON，不要解释。
```

**Arbiter-B（Interruption Judge）**：

```text
你是对话中断判断器。bot 正在分段发送回复，用户发了新消息。判断 bot 是否应该中断未发送的部分。
输出严格 JSON：{"action": "continue"|"abort_unsent"|"revise", "reason": "..."}
判断依据：
- 用户新消息回答了 bot 即将问的问题 → revise
- 用户新消息否定/修正了 bot 已发内容 → revise
- 用户新消息是无关闲聊 → continue
- 用户新消息是纯补充不矛盾 → continue
只输出 JSON，不要解释。
```

**Arbiter-C（Correction Judge）**：

```text
你是回复修正判断器。bot 刚回复完，用户又发了新消息。判断 bot 是否需要修正刚才的回复。
输出严格 JSON：{"needs_correction": true/false, "correction_type": "retract"|"amend"|"acknowledge"|null}
判断依据：
- 用户说"不是"/"我是说"/"等等" → needs_correction=true, type=amend
- 用户补充了改变语义的关键信息 → needs_correction=true, type=amend
- 用户只是继续聊天/换话题 → needs_correction=false
- 用户表示 bot 理解错误 → needs_correction=true, type=retract
只输出 JSON，不要解释。
```

### 9.4 验收提交格式

每个 Phase commit 后，在本文 §8 状态表把 ⏳ 改 🟡 + 附上：

```text
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径
- [ ] D2 cancel-path 测试落实（Arbiter 超时 / CancelledError 场景）
- [ ] uv run pytest -q 全绿（含本任务新测试）
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] 30 秒回滚演练成功（命令贴本回执）
```

### 9.5 与其他进行中工作的关系

- **Part 6 Bugfix**（[bugfix-part1-execution](./omubot-humanization-part6-bugfix-part1-execution.md)）：B0/B3 灰度切档仍待用户授权。Arbiter 工作与 bugfix 无代码冲突，可并行。
- **Issue 17 连续 @ 压测**（[issue17-at-burst](./omubot-grayscale-issue17-at-burst-humanization.md)）：Arbiter-A 的 completeness 判断直接解决 Issue 17 的 A 类问题（零延迟连发）。Phase 1 上线后 Issue 17-A 可标 ✅。
- **Persona v2**：已全量切换，与 Arbiter 无交集。
- **`_last_assistant_replied_to_user` 修复**：已落地。Arbiter-C 建立在此修复之上（followup 检测正确后才能做 correction 判断）。

---

## 10. 溯源审计（2026-05-28）

> 审计方式：逐 Phase 对照派单文档设计 vs 实际代码实现，验证工作流插入点语义正确性。
> 审计结论：**Phase 0-3 全部已落地，实现与设计语义一致，23 条测试全绿。§8 状态表 🟡 可批量转 ✅。**

### 10.1 Phase 0 — ArbiterClient 封装

| 检查项 | 设计要求 | 实际实现 | 判定 |
|--------|----------|----------|------|
| 文件存在 | `services/llm/arbiter.py` 新建 | 存在，341 行 | ✅ |
| 3 个 judge 方法 | `judge_completeness` / `judge_interruption` / `judge_correction` | 全部实现，签名含 `user_id` / `group_id` 参数（比设计更完整） | ✅ |
| 固定 system prompt | 常量字符串，不动态拼接 | `_COMPLETENESS_SYSTEM_PROMPT` / `_INTERRUPTION_SYSTEM_PROMPT` / `_CORRECTION_SYSTEM_PROMPT` 模块级常量 | ✅ |
| max_tokens=15 | 极短输出 | `_MAX_TOKENS = 15` (line 19) | ✅ |
| timeout fallback | 超时返回安全默认值 | `asyncio.timeout` + except `TimeoutError` → fallback | ✅ |
| 不复用 `LLMClient` | 独立 aiohttp POST | 使用 `create_provider("deepseek", ...)` + `self._session.post()` | ✅ |
| usage 记录 | `task="arbiter"` 区分 | `call_type="arbiter"` 写入 UsageTracker | ✅ |
| `ArbiterConfig` | BaseModel + 所有参数 | `kernel/config.py:1865`，含 `enabled` / `timeout_ms` / `completeness_*` / `interruption_enabled` / `correction_enabled` / `correction_window_s` / `runtime_groups` | ✅ |
| `BotConfig.arbiter` | 字段注册 | `kernel/config.py:1960` `arbiter: ArbiterConfig = Field(default_factory=ArbiterConfig)` | ✅ |
| config.json | `arbiter` 段 | 存在，`enabled: true` + `runtime_groups: ["993065015"]`（已进灰度-1） | ✅ |
| 测试 | 7 条 | `tests/test_arbiter.py` 7 条全绿 | ✅ |

**额外发现**：实际实现比设计多了 `resolved_api_base` / `resolved_api_key` / `resolved_model` 三个 computed 字段（`Field(exclude=True)`），用于运行时从 `llm.*` 继承。设计文档说"空字符串时复用 llm.*"但没明确 resolved 字段机制——实现更完善。

### 10.2 Phase 1 — Arbiter-A scheduler 接入

| 检查项 | 设计要求 | 实际实现 | 判定 |
|--------|----------|----------|------|
| `notify()` is_at 分支 | Arbiter 启用时不立即 fire，启动 completeness loop | `scheduler.py:353-365`：`_arbiter_enabled()` → `burst_pending.append()` → `create_task(_arbiter_completeness_loop)` | ✅ |
| `_arbiter_completeness_loop` | poll 间隔 + max_wait + threshold 判定 | `scheduler.py:726-767`：`while elapsed < max_wait_s` → `sleep(poll_s)` → `judge_completeness()` → `break` on complete | ✅ |
| `GroupSlot` 扩展 | `burst_pending` / `arbiter_task` | `scheduler.py:114-115`：`burst_pending: list[PendingMessage]` / `arbiter_task: asyncio.Task | None` | ✅ |
| running_task 阻塞 | 正在跑时走 pending_at | `scheduler.py:349-352`：`if slot.running_task and not slot.running_task.done(): slot.pending_at = True; return` | ✅ |
| Arbiter 禁用时保持原行为 | 立即 fire | `scheduler.py:366-368`：`self._fire(group_id)` | ✅ |
| 超时兜底 | max_wait_s 后强制 fire | loop 退出后 `self._fire(group_id)` (line 762) | ✅ |
| 测试 | 6 条 | `tests/test_arbiter_scheduler.py` 6 条全绿 | ✅ |

**设计 vs 实现偏差**：
- 设计写 `self._fire(group_id, burst_context=slot.burst_pending)`，实际 `_fire()` 无 `burst_context` 参数。不影响正确性——burst 消息已在 timeline 中，`_do_chat` 自然看到。
- 实际实现在 `finally` 块清理 `slot.burst_pending = []` / `slot.arbiter_task = None`，比设计更健壮（CancelledError 也能清理）。

### 10.3 Phase 2 — Arbiter-B 段间中断

| 检查项 | 设计要求 | 实际实现 | 判定 |
|--------|----------|----------|------|
| `on_segment` 签名 | `Callable[[str], Awaitable[bool]]` | `client.py:2480`：`Callable[[str], Awaitable[bool]] | None` | ✅ |
| 返回值检查 | `should_continue = await on_segment(...)` | `client.py:2540,2842`：`should_continue = await on_segment(visible/candidate)` | ✅ |
| `SegmentAborted` | 异常 + `sent_segments` 字段 | `client.py:747`：`@dataclass class SegmentAborted(Exception): sent_segments: list[str]` | ✅ |
| `SegmentAborted` catch | `chat()` 顶层 catch | `client.py:4140,4454,4473,4779,4798`：多处 `except SegmentAborted as exc` | ✅ |
| scheduler on_segment 回调 | 发送 + Arbiter-B 检查 | `scheduler.py:1108-1156`：`on_segment()` 内含 `_send_to_group` + `_pending_messages_since` + `judge_interruption` | ✅ |
| abort_unsent → return False | 中止后续段 | `scheduler.py:1156`：`return False` | ✅ |
| revise → burst_pending.extend | 新消息入队 | `scheduler.py:1149`：`slot.burst_pending.extend(new_pending)` | ✅ |
| Arbiter-B 禁用时 return True | 不中断 | `scheduler.py:1130-1135`：检查 `interruption_enabled` → `return True` | ✅ |
| 测试 | 5 条 | `tests/test_arbiter_interruption.py` 5 条全绿 | ✅ |

**设计 vs 实现偏差**：
- 设计写 `self._timeline.get_messages_since(group_id, generation_start_time)`，实际用 `self._pending_messages_since(group_id, _baseline)` 基于 pending buffer 索引。语义等价但实现更精确（避免时间戳竞态）。
- 实际 `on_segment` 回调还处理了 `[CQ:reply]` 前缀注入和 humanize 参数，比设计更完整。

### 10.4 Phase 3 — Arbiter-C 发后自修正

| 检查项 | 设计要求 | 实际实现 | 判定 |
|--------|----------|----------|------|
| router.py correction 路径 | followup 检测后调用 Arbiter-C | `router.py:1301-1353`：完整 Arbiter-C 判断 + TriggerContext(mode="correction") 构建 | ✅ |
| correction_window_s 时间窗 | 30s 内才触发 | `router.py:1322`：`time.time() - last_reply_time <= correction_window_s` | ✅ |
| 触发后清空 last_reply | 防重复触发 | `router.py:1350-1352`：`slot.last_reply_content = ""` / `slot.last_reply_time = 0.0` | ✅ |
| scheduler 记录 last_reply | `_do_chat` 完成后写入 | `scheduler.py:1194-1195`：`slot.last_reply_time = time.time()` / `slot.last_reply_content = latest_reply` | ✅ |
| GroupSlot 字段 | `last_reply_time` / `last_reply_content` | `scheduler.py:121-122` | ✅ |
| correction mode 在 scheduler | `is_correction` 分支 force fire | `scheduler.py:379-386`：`if is_correction: ... self._fire(group_id)` | ✅ |
| Arbiter-C 禁用 fallback | `needs_correction=False` → 走正常 followup | `router.py:1312-1313`：检查 `correction_enabled` + `enabled` | ✅ |
| 测试 | 4 条 + 1 辅助 | `tests/test_arbiter_correction.py` 5 条全绿 | ✅ |

**设计 vs 实现偏差**：
- 设计写 `scheduler.get_slot(group_id)`，实际 router 用 `getattr(scheduler, "_arbiter", None)` 间接访问。这是因为 router 不直接 import scheduler 类型——通过 `ctx.scheduler` 动态获取。合理的解耦设计。
- 实际实现额外传了 `user_id` / `group_id` 给 `judge_correction`（用于 usage 记录和 group 过滤），比设计更完整。
- Phase 3 完成记录提到额外补了 `plugins/chat/plugin.py` 的 `set_arbiter()` wiring——这是设计文档未覆盖的运行时注入点，执行者正确识别并补齐。

### 10.5 灰度-1 状态

| 检查项 | 状态 |
|--------|------|
| `config.json` arbiter.enabled=true | ✅ 已确认 (line 504) |
| `runtime_groups=["993065015"]` | ✅ 已确认 (line 512-514) |
| 24h 观测 | ⏳ 尚未执行 |
| §6.2 出口指标填表 | ⏳ 尚未执行 |

### 10.6 综合判定

**验收结论：Phase 0-3 全部通过溯源审计。**

证据汇总：
1. **代码存在性**：4 个新文件 + 4 个改动文件全部落地
2. **语义正确性**：所有工作流插入点（scheduler.notify → completeness loop → fire、on_segment → interruption → SegmentAborted、router → correction → force fire）逻辑链完整闭合
3. **Fallback 安全**：`enabled=false` 短路、timeout fallback、invalid JSON fallback 三层防护均已实现
4. **测试覆盖**：23 条测试全绿，覆盖正常路径 + cancel-path + timeout + disabled 场景
5. **运行时配置**：已进入灰度-1 单群观测态

**§8 状态表建议**：P0.1-P3.3 全部 🟡→✅；灰度-1 保持 🟡（待 24h 观测完成）。

**遗留项**（不阻断验收）：
- 灰度-1 的 24h 观测 + 出口指标填表
- 灰度-2 双群扩展
- Arbiter prompt 迭代优化（基于实际 false positive/negative 率）

---

## 10. Arbiter Prompt 迭代记录

> Phase 4 调参期间在此追加 prompt 版本变更。

| 版本 | 日期 | 变更 | 效果 |
|------|------|------|------|
| v1 | — | 初始模板（§9.3） | 待观测 |
| v2 | 2026-05-28 | Arbiter-A: 移除"句号=说完"规则，增加 QQ 语境特化规则（见 §10.1） | 待部署 |

### 10.1 调研：QQ 句尾标点社会语言学

**问题**：v1 prompt 规则"消息末尾有句号/问号/感叹号 → 大概率说完"在 QQ 群聊场景下产生大量 false positive。实际观测：用户发"姆。"后紧接"姆姆姆"，Arbiter-A 在首条即判 complete=True（confidence=0.95），导致 burst 未合并、bot 回复两次。

**文献证据**：

| 来源 | 发现 |
|------|------|
| Klin et al. 2015 (Computers in Human Behavior 49:581-586) | 短信/IM 中句号传达冷淡/不真诚，非句法终止符 |
| Klin et al. 2018 (同刊 80:15-19) | 感叹号传达真诚；句号效果在 IM 中与书面语完全相反 |
| JustSoSoul 2024《2024 中国青年社交行为报告》 | 80%+ Z 世代避免使用句号；30.2% 看到句号会担心对方生气 |
| PaddleSpeech (NAACL 2022) | 语音识别在韵律停顿处自动插入句号，与语义边界无关 |

**结论**：QQ 群聊中"。"是情绪标记或语音输入副产物，不是轮次完成信号。Arbiter-A prompt 必须移除基于标点的完成判断规则。

**v2 prompt 设计原则**：
1. 删除所有标点 → 完成度的映射规则
2. 增加 QQ 特化信号：消息间隔 <2s + 内容极短（≤4 字）→ 大概率还有后续
3. 增加语音输入识别：含"。"但无其他标点 + 口语化表达 → 不作为完成信号
4. 保留语义完整性判断：完整问句/祈使句/独立话题 → 可判完成

### 10.2 调研：Arbiter 架构对标分析

**设计原则**：主回复流与短判断脉冲间有序而高效的交互——judge 永远不在 token emission 关键路径上。

**对标架构**：

| 架构 | 来源 | 核心机制 | 与 Arbiter 关系 |
|------|------|----------|----------------|
| LSM Concurrent Monitor | Pro-GenAI 2026 (IEEE) | 轻量 transformer 与主 LLM 并行运行，queue-based，发射 interrupt 信号 | Arbiter-B/C 的直接对标：不阻塞主生成，仅在检测到冲突时发射控制信号 |
| Speculative Decoding | Leviathan et al. 2023; Chen et al. 2023 (ICML) | propose-verify loop：小模型提议 N tokens，大模型一次验证 | 验证了"快模型永远不增加慢模型延迟"的可行性；Arbiter-A 的 poll 间隔设计可借鉴 |
| IntentGuard Sidecar | IntentGuard (GitHub, 2024) | DeBERTa 分类器 <20ms，pre-generation 阶段执行 | Arbiter-A 的理想形态：在 generation 启动前完成判断，零额外延迟 |

**关键洞察**：

1. **并发而非串行**：LSM 模式证明 judge 可以与主生成完全并发，通过 queue 异步通信。当前 Arbiter-A 的 poll loop 已是此模式的简化实现。
2. **快模型预算独立**：Speculative Decoding 中 draft model 的 token 预算与 target model 完全独立。当前 `_MAX_TOKENS=15` 过低导致 Arbiter-B 输出截断（`{"action": "continue", "reason": "` 在 char 34 处被截断），需提升至 48。
3. **Debounce 标准**：业界 chat debounce 共识为 quiet period 600-1500ms + max cap 3000-5000ms。当前 `completeness_poll_interval_s=0.3` + `completeness_max_wait_s=8.0` 的设计在合理范围内，无需机械增加 min_wait。
4. **判断质量 > 机械延迟**：正确方向是提升 Arbiter-A 的判断准确率（prompt 优化），而非增加等待时间。IntentGuard 用更好的模型/prompt 在更短时间内达到更高准确率，证明了这一路径。

### 10.3 待执行修改（架构级修复方案）

> §10.2 四条洞察 → 修复方案映射：
> - 洞察 1（并发而非串行）→ 修改 A：Arbiter-B 并发监控 task
> - 洞察 2（快模型预算独立）→ 修改 B：`_MAX_TOKENS` 提升 + Arbiter-B 独立 timeout
> - 洞察 3（Debounce 标准）→ 修改 D：`pending_at` 走 Arbiter-A completeness loop
> - 洞察 4（判断质量 > 机械延迟）→ 修改 C：Arbiter-A prompt v2 + 修改 A 的 gate 机制

| # | 修改 | 文件 | 理由 |
|---|------|------|------|
| A | Emission Gate + 并发 Arbiter-B monitor | `services/scheduler.py` | 核心架构改造：Arbiter 断点随时切入主回复链路 |
| B | `_MAX_TOKENS = 15 → 48` | `services/llm/arbiter.py:19` | 修复 Arbiter-B reason 字段截断 `invalid_json` |
| C | Arbiter-A prompt v2 | `services/llm/arbiter.py:21-28` | 移除错误标点规则，QQ 语境特化 |
| D | `pending_at` → burst re-accumulate + Arbiter-A | `services/scheduler.py` | re-fire 路径重新经过 completeness 判断 |

### 10.4 设计缺陷：emission-before-judgment + pending_at bypass

**发现时间**：2026-05-28
**严重程度**：P1（核心交互质量缺陷）

#### 问题描述

当前 Arbiter-B 打断机制存在结构性缺陷：判断脉冲在 emission 之后执行，且 `pending_at` 再触发路径完全绕过 Arbiter-A。

#### 缺陷 1：发送在判断之前（emission-before-judgment）

[scheduler.py:1108-1156](../../services/scheduler.py#L1108-L1156) 中 `on_segment` 回调的执行顺序：

```python
# 1. 先发送到 QQ（不可撤回）
send_total_elapsed += await self._send_to_group(group_id, text, ...)
sent_segments += 1

# 2. 发完之后才检查是否应该打断
new_pending = self._pending_messages_since(group_id, _baseline)
result = await self._arbiter.judge_interruption(...)
```

**后果**：
- 短回复（单段）：整条回复发完才检查，Arbiter-B 形同虚设
- 长回复第 1 段：已发出不可撤回，只能阻止第 2 段起
- `unsent=[]` 永远为空：流式生成不知道后续内容，Arbiter-B 无法评估"未发送内容"的价值

#### 缺陷 2：`pending_at` 再触发跳过 Arbiter-A

[scheduler.py:1226-1228](../../services/scheduler.py#L1226-L1228)：

```python
finally:
    slot.running_task = None
    if slot.pending_at or slot.msg_count > 0:
        slot.pending_at = False
        self._fire(group_id)  # 直接 fire，不经过 completeness 判断
```

**后果**：
- 第一轮生成期间用户连发 3 条消息 → `pending_at = True`（布尔，不区分 1 条还是 3 条）
- 第一轮结束后立即 fire 第二轮，不等用户说完
- 违背 Arbiter-A "等用户说完再回复"的设计意图

#### 影响矩阵

| 场景 | 期望行为 | 实际行为 |
|------|----------|----------|
| 短回复（单段） | 发送前检测新消息，可拦截 | 发完才检查，无法打断 |
| 长回复第 1 段 | 发送前检测，可拦截 | 发完才检查，第 1 段不可撤回 |
| generation 结束后 re-fire | 重新走 Arbiter-A 等用户说完 | 直接 fire，跳过 completeness |
| 用户连发期间 | 积累消息，判断完整性后统一回复 | 仅设 bool 标记，结束后无条件 fire |

### 10.5 修复方案：Emission Gate 架构（修改 A 详设）

#### 设计原则

**Arbiter 断点随时随处可切入主回复链路。Arbiter 判断未返回时 → wait；判断返回后 → 中断或继续。**

#### 工业实现依据

| 来源 | 机制 | 与本方案的映射 |
|------|------|---------------|
| **SCM** (Li et al. 2025, ICT/CAS, arXiv:2506.09996) | 轻量 monitor 与 LLM generation 并行运行，逐 token 判断，累计 k 个 harmful token 后 terminate | Arbiter-B monitor 并发 task 的直接原型；SCM 在仅看到前 18% tokens 时即达 95%+ F1 |
| **TensorRT Edge-LLM** (NVIDIA, StreamChannel) | cancellation 在 "iteration top" 检查（下一 token emit 前）；`std::atomic cancelled` 由任意线程 fire-and-forget 设置 | gate.arm() 由 monitor task 设置，on_segment 在 emit 前 check()——同一模式 |
| **Speculative Decoding** (Leviathan 2023 / Chen 2023, ICML) | draft tokens 经 verify 后才 accept；rejected 时序列立即终止，不 emit 后续 | verify-then-emit = gate.check() → emit；reject = gate abort → SegmentAborted |
| **LSM** (Pro-GenAI 2026, IEEE, ResearchGate:401283765) | "The LSM does not rewrite or alter model outputs; instead, it interrupts the generation stream and notifies the client with a structured JSON" | Arbiter-B 不改写内容，仅发射 interrupt 信号（action: abort_unsent / revise） |

**关键设计约束（源自 SCM 论文 §4.3）**：monitor 必须与 generation 并行而非串行——SCM 的 inference 阶段仅使用 feature extractor + token scorer（不含 holistic scorer），确保判断延迟 < token 生成间隔。对应到本方案：Arbiter-B API 调用（~700ms）远慢于 segment 生成间隔（~1-3s），因此 monitor 必须提前触发（检测到新消息即 arm），而非等到 on_segment 时才同步调用。

#### 状态机

```
EmissionGate 三态：

  OPEN ──────────────────────────────────────── emit segment
    │                                              │
    │ (new message detected in timeline)           │
    ▼                                              │
  PENDING ─── Arbiter-B call in flight ───┐        │
    │                                     │        │
    │ (on_segment called while PENDING)   │        │
    │         ↓                           │        │
    │   await gate.wait()  ◄──────────────┘        │
    │         │                                    │
    │    ┌────┴────┐                               │
    ▼    ▼         ▼                               │
  ABORT        CONTINUE ───────────────────────────┘
    │
    ▼
  return False → SegmentAborted (当前段不发)
```

#### 核心组件

**1. `_EmissionGate`（新增，scheduler.py 内部类）**

含三层防护：L1 单调截止门 / L2 中断预算 / L3 熔断器（§10.8 推演结论）

```python
_GATE_TIMEOUT_S = 0.8        # L1: 单调截止，不可被新消息延长
_MAX_ABORTS_PER_FIRE = 2     # L2: 单次 fire 周期内最多 abort 次数
_CIRCUIT_BREAKER_THRESHOLD = 3  # L3: 连续超时次数 → 熔断
_CIRCUIT_HALF_OPEN_S = 30.0  # L3: 熔断后半开恢复间隔

class _EmissionGate:
    """Controls segment emission based on concurrent Arbiter-B verdicts.

    Three-layer protection (§10.8):
      L1 — Monotonic Deadline: WAIT 最多 GATE_TIMEOUT_S，超时 fail-open
      L2 — Interrupt Budget: abort 次数超限后强制 emit
      L3 — Circuit Breaker: Arbiter 连续超时后熔断，降级为无 Arbiter 模式
    """
    __slots__ = (
        "_state", "_event", "_verdict",
        "_abort_count", "_consecutive_timeouts", "_circuit_open_until",
        "_segment_index",
    )

    def __init__(self) -> None:
        self._state: Literal["open", "pending", "abort"] = "open"
        self._event: asyncio.Event = asyncio.Event()
        self._event.set()  # initially open
        self._verdict: InterruptionResult | None = None
        # L2: interrupt budget
        self._abort_count: int = 0
        # L3: circuit breaker
        self._consecutive_timeouts: int = 0
        self._circuit_open_until: float = 0.0  # monotonic timestamp
        # 首段免检
        self._segment_index: int = 0

    def arm(self) -> None:
        """Transition OPEN → PENDING. Called when new message detected."""
        if self._state == "open":
            self._state = "pending"
            self._event.clear()

    def resolve(self, verdict: InterruptionResult, *, timed_out: bool = False) -> None:
        """Transition PENDING → OPEN/ABORT. Called when Arbiter-B returns."""
        if timed_out:
            # L1: deadline expired → fail-open
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= _CIRCUIT_BREAKER_THRESHOLD:
                # L3: 熔断
                self._circuit_open_until = time.monotonic() + _CIRCUIT_HALF_OPEN_S
            self._state = "open"
            self._verdict = None
        elif verdict.action == "continue":
            self._consecutive_timeouts = 0  # L3: reset on success
            self._state = "open"
        else:
            self._consecutive_timeouts = 0
            self._abort_count += 1
            if self._abort_count > _MAX_ABORTS_PER_FIRE:
                # L2: budget exhausted → force emit
                self._state = "open"
                _L.warning("arbiter_b_budget_exhausted | forcing emit")
            else:
                self._state = "abort"
            self._verdict = verdict
        self._event.set()

    @property
    def circuit_open(self) -> bool:
        """L3: True if circuit breaker is open (Arbiter-B disabled)."""
        return time.monotonic() < self._circuit_open_until

    async def check(self) -> bool:
        """Called before each emission. Returns True=emit, False=abort.

        Incorporates:
          - 首段免检 (segment_index == 0)
          - L3 circuit breaker bypass
          - L1 monotonic deadline (via asyncio.wait_for in caller)
        """
        self._segment_index += 1
        if self._segment_index == 1:
            return True  # 首段免检：首字延迟最敏感
        if self.circuit_open:
            return True  # L3: 熔断期间直接 emit
        if self._state == "open":
            return True
        if self._state == "abort":
            return False
        # PENDING: wait for Arbiter-B to return (caller wraps with timeout)
        await self._event.wait()
        return self._state != "abort"

    @property
    def verdict(self) -> InterruptionResult | None:
        return self._verdict

    @property
    def abort_count(self) -> int:
        return self._abort_count
```

**2. `_arbiter_b_monitor`（新增，并发 task）**

在 generation 启动时同步创建，与 `_do_chat` 并发运行。
含 L1 单调截止 + 消息合并（§10.8 场景 1 防护）：

```python
async def _arbiter_b_monitor(
    self,
    group_id: str,
    gate: _EmissionGate,
    baseline: int,
    sent_texts: list[str],
    user_id: str,
) -> None:
    """Concurrent monitor: polls timeline for new messages, fires Arbiter-B, arms gate.

    Key behaviors (§10.8):
      - 新消息到达 → arm gate → 单次 Arbiter-B 调用
      - WAIT 期间新消息仅 accumulate，不触发新调用（防场景 1 饿死）
      - Arbiter-B 调用有 GATE_TIMEOUT_S 硬截止（L1 单调截止门）
      - circuit breaker open 时跳过调用（L3）
    """
    poll_interval = 0.15  # 150ms — faster than segment emission cadence
    while True:
        await asyncio.sleep(poll_interval)
        # L3: circuit breaker open → skip all Arbiter-B calls
        if gate.circuit_open:
            continue
        new_pending = self._pending_messages_since(group_id, baseline)
        if not new_pending:
            continue
        # New message detected → arm gate (blocks next emission)
        gate.arm()
        # Fire Arbiter-B with L1 monotonic deadline
        try:
            result = await asyncio.wait_for(
                self._arbiter.judge_interruption(
                    already_sent=list(sent_texts),
                    unsent=[],
                    new_messages=[msg.content for msg in new_pending],
                    user_id=user_id,
                    group_id=group_id,
                ),
                timeout=_GATE_TIMEOUT_S,
            )
            gate.resolve(result)
        except asyncio.TimeoutError:
            # L1: deadline expired → fail-open
            gate.resolve(InterruptionResult(action="continue", fallback=True), timed_out=True)
            _L.warning("arbiter_b_timeout | group={} deadline={}s", group_id, _GATE_TIMEOUT_S)
            continue
        if result.action != "continue":
            _L.info(
                "arbiter_b_gate_abort | group={} action={} reason={}",
                group_id, result.action, result.reason,
            )
            return  # monitor 退出，gate 已 abort
        # continue → gate re-opens, keep monitoring
```

**3. `on_segment` 改造（judgment-before-emission）**

```python
async def on_segment(text, ..., _gate: _EmissionGate = gate):
    # Gate check BEFORE emission
    if not await _gate.check():
        # Arbiter-B 已判定中断 → 当前段不发
        if _gate.verdict and _gate.verdict.action == "revise":
            slot.burst_pending.extend(
                self._pending_messages_since(group_id, _baseline)
            )
        return False

    # Gate open → emit
    send_total_elapsed += await self._send_to_group(group_id, text, ...)
    sent_segments += 1
    _sent_texts.append(text)
    return True
```

#### 时序图：典型场景

```
时间轴 →

User:    "姆。"              "姆姆姆"                    "来烤肉"
          │                    │                          │
Arbiter-A:├── poll ── complete ─┤                          │
          │                    │                          │
Main LLM: │                    ├── generation start ──────────────────── ...
          │                    │     │                    │
Segments: │                    │     │  seg1 ready        │
          │                    │     │    │               │
Gate:     │                    │     │  check() → OPEN    │
          │                    │     │    │               │
QQ send:  │                    │     │  ◄─ seg1 sent      │
          │                    │     │                    │
Monitor:  │                    │     ├── poll ─── poll ───┤ detect!
          │                    │     │                    │
Gate:     │                    │     │                  arm() → PENDING
          │                    │     │                    │
Arbiter-B:│                    │     │                    ├── API call ──┐
          │                    │     │                    │              │
Segments: │                    │     │         seg2 ready │              │
          │                    │     │           │        │              │
Gate:     │                    │     │     check() → WAIT │              │
          │                    │     │           │ (block) │              │
          │                    │     │           │        │   verdict    │
          │                    │     │           │        │◄─ "revise" ──┘
Gate:     │                    │     │           │        │
          │                    │     │     resolve(abort) │
          │                    │     │           │        │
on_segment│                    │     │     return False   │
          │                    │     │           │        │
          │                    │     │  SegmentAborted    │
          │                    │     │                    │
          │                    │     │  seg2 NOT sent ✓   │
```

#### 关键特性

| 特性 | 实现方式 | 对应设计原则 / §10.8 |
|------|----------|---------------------|
| Arbiter 与 generation 并发 | `_arbiter_b_monitor` 独立 task | 洞察 1：并发而非串行 |
| 无新消息时零延迟 | gate 默认 OPEN，check() 立即返回 True | 洞察 4：判断质量 > 机械延迟 |
| 有新消息时精确 wait | gate.arm() → on_segment await | 用户设计：未返回时 wait |
| Arbiter-B 独立 token 预算 | `_MAX_TOKENS=48`，独立 timeout | 洞察 2：快模型预算独立 |
| 当前段可拦截 | check() 在 `_send_to_group` 之前 | 修复缺陷 1 |
| monitor 150ms poll | 快于 segment emission 节奏（~1-3s） | Arbiter 总能在下一段发出前就绑 |
| 首段免检 | `segment_index == 1` → 直接 emit | §10.8 场景 5：降低首字延迟 |
| 单调截止不可延长 | `asyncio.wait_for(timeout=0.8)` | §10.8 场景 1：防饿死 |
| abort 次数有限 | `abort_count > MAX_ABORTS` → force emit | §10.8 场景 2：防死循环 |
| 服务不可用时降级 | circuit breaker → 跳过 Arbiter-B | §10.8 场景 3：防宕机 |

#### 降级与安全（三层防护，详见 §10.8）

| 层 | 防护对象 | 机制 | 最坏情况行为 |
|---|---|---|---|
| L1: Monotonic Deadline | 单次 Arbiter-B 超时 | `asyncio.wait_for(timeout=0.8)` → fail-open emit | 单 segment 最多延迟 800ms |
| L2: Interrupt Budget | abort-restart 死循环 | `abort_count > MAX_ABORTS_PER_FIRE` → force emit | 最多 2 次重启后必出回复 |
| L3: Circuit Breaker | Arbiter 服务持续不可用 | 3 次超时 → `circuit_open=True`，30s 半开 | 降级为无 Arbiter 模式（等同当前行为） |

附加安全：
- Arbiter 未配置 / disabled → gate 永远 OPEN，行为等同当前代码
- 首段免检 → `segment_index == 1` 时直接 emit，首字延迟不受影响
- monitor task 异常 → gate 保持 OPEN，不影响正常发送
- generation 结束 / SegmentAborted → monitor task 被 cancel

### 10.6 修复方案：pending_at 重走 Arbiter-A（修改 D 详设）

#### 工业实现依据

| 来源 | 策略 | 与本方案的映射 |
|------|------|---------------|
| **Vercel Chat SDK** (`vercel/chat` PR #277, 2026-03) | `burst` 策略："Wait for the idle burst window, then respond once with every message in that window" | 完全等价于 Arbiter-A completeness loop：等用户说完，一次性回复 |
| **Vercel Chat SDK** | `debounce` 策略 (default 1500ms)："Without debounce, the bot would respond to 'hey' before the actual question even arrives" | 精确描述了当前 `pending_at` 的缺陷——bot 对"姆。"立即回复，而真正的问题还没到 |
| **Cloudflare Agents** (`cloudflare/agents` PR #1192, 2026-05) | `merge` 策略："Collapse overlapping queued user messages into one follow-up submit" | 等价于 `pending_during_generation` 积累后合并进 `burst_pending` |
| **Cloudflare Agents** | `debounce` (default 750ms)："trailing-edge semantics as chat apps that wait for the user to finish sending a burst of short messages" | 验证了 Arbiter-A poll 间隔 (300ms) + max_wait (8s) 的合理范围 |
| **Vercel Chat SDK** issue #414 (2026-04) | `queue-debounce`："the FIRST message in an idle thread also waits debounceMs so a burst that arrives while the lock..." | 验证了"即使是第一条消息也应等待"的设计——当前 Arbiter-A 已实现此行为，但 re-fire 路径绕过了它 |

**关键设计决策（源自 Cloudflare 文档）**：

> "Users send bursts of short messages (like a messaging app)? Use 'debounce' to wait for a quiet window before responding."

这正是 QQ 群聊的使用模式。当前 `pending_at` 的 bool 标记 + 无条件 re-fire 等价于 Cloudflare 的 `queue` 策略（每条消息独立回复），而正确行为应是 `burst`/`merge`（等待 + 合并）。

#### 当前问题

`pending_at` 是布尔标记，丢失了"几条消息、什么内容"的信息。re-fire 时直接 `_fire()` 跳过 completeness 判断。

#### 改造

```python
# _GroupSlot 变更
# 删除: pending_at: bool = False
# 新增: pending_during_generation: list[PendingMessage] = field(default_factory=list)

# notify() 中 running_task 活跃时的处理
if is_at:
    if slot.running_task and not slot.running_task.done():
        slot.pending_during_generation.append(
            PendingMessage(
                content=message_text or "@我",
                user_id=user_id,
                timestamp=time.time(),
            )
        )
        _L.debug("scheduler | group={} @ queued during generation (n={})",
                 group_id, len(slot.pending_during_generation))
        return

# finally 块改造
finally:
    slot.running_task = None
    if slot.pending_during_generation:
        # 将积存消息转入 burst_pending，走 Arbiter-A completeness loop
        slot.burst_pending.extend(slot.pending_during_generation)
        slot.pending_during_generation = []
        if self._arbiter_enabled(group_id):
            slot.arbiter_task = asyncio.create_task(
                self._arbiter_completeness_loop(group_id)
            )
        else:
            self._fire(group_id)
    elif slot.msg_count > 0:
        self._fire(group_id)
```

#### 效果

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| generation 期间收到 1 条 @ | `pending_at=True` → 立即 re-fire | 进入 Arbiter-A → 判断是否说完 → 说完才 fire |
| generation 期间收到 3 条 @ | `pending_at=True`（丢失 2 条信息） | 3 条全部进入 `burst_pending`，Arbiter-A 看到完整上下文 |
| generation 期间收到普通消息 | `msg_count++` → 立即 re-fire | 保持原行为（非 @ 消息不走 Arbiter） |

#### 与 Emission Gate 的协同

当 Arbiter-B monitor 判定 `revise` 时：
1. `SegmentAborted` 终止当前 generation
2. `finally` 块触发 → `pending_during_generation` 非空
3. 走 Arbiter-A completeness loop → 等用户说完
4. 用户说完后 fire → 新 generation 包含所有积存消息 + 已发段的上下文

这形成完整闭环：**Arbiter-B 中断 → Arbiter-A 等待 → 用户说完 → 统一回复**。

### 10.7 修改 B/C 与架构修改的关系

| 修改 | 性质 | 独立性 | 依据 |
|------|------|--------|------|
| B: `_MAX_TOKENS=48` | bug fix | 完全独立，可先行部署 | TensorRT Edge-LLM: per-slot token budget 独立；当前 15 tokens 导致 Arbiter-B 输出截断（char 34 处 `{"action": "continue", "reason": "` 被切断） |
| C: Arbiter-A prompt v2 | 判断质量提升 | 独立于架构改造 | §10.1 社会语言学调研：Klin 2015/2018 + JustSoSoul 2024 证明句号≠完成；Cloudflare debounce 文档确认"burst of short messages"是 IM 常态 |
| A: Emission Gate | 架构改造 | 依赖 B | SCM (Li 2025): parallel monitor + token-level judgment；TensorRT: cancellation at iteration top；Speculative Decoding: verify-then-emit |
| D: pending_at → burst | 架构改造 | 独立于 A | Vercel `burst` / Cloudflare `merge`：overlapping messages 合并后统一回复是业界共识 |

**部署顺序**：B → C → D → A（每步可独立验证）

#### 与 §10.2 架构对标的关系

| §10.2 洞察 | 旧 §10.3 的落地 | 新方案落地 | 工业依据 |
|------------|-----------------|-----------|----------|
| 1. 并发而非串行 | ❌ 未体现 | ✅ `_arbiter_b_monitor` 并发 task | SCM §4.3: monitor 与 generation 并行 |
| 2. 快模型预算独立 | ⚠️ 仅 max_tokens | ✅ max_tokens + gate 保证 Arbiter-B 有时间返回 | TensorRT: per-slot budget + cancellation 独立于 generation |
| 3. Debounce 标准 | ❌ 未体现 | ✅ pending_at → Arbiter-A re-evaluation | Vercel burst (1500ms) / Cloudflare debounce (750ms) |
| 4. 判断质量 > 机械延迟 | ⚠️ 仅 prompt 优化 | ✅ gate 无新消息时零延迟 + prompt + 结构保证 | SCM: 18% tokens 即达 95%+ F1（质量够高则无需等更多） |

### 10.8 极端场景推演与防护层设计

> 设计原则（用户定义）：Arbiter 断点随时随处可切入主回复链路。Arbiter 判断未返回时 → wait；判断返回后 → 中断或继续。

以下对该原则在极端输入模式下的行为进行穷举推演，并给出三层防护机制。

#### 场景 1：连续语料导致永久阻塞

```
segment_1 ready → Arbiter-B fires → WAIT
  ↳ 300ms 内 msg_A 到达
  ↳ Arbiter-B 返回 → 但 msg_A 让上下文变了，要不要重新判断？
    ↳ 如果重新判断 → 又 WAIT 300ms
      ↳ 300ms 内 msg_B 到达 → 又要重新判断
        ↳ 无限循环，segment 永远不发出 → 用户看到沉默
```

**根因**：若每条新消息触发新一轮 Arbiter-B，且消息到达频率 > Arbiter 响应时间，系统饿死。

**工业界解法**：

| 来源 | 机制 | 核心思想 |
|---|---|---|
| OpenClaw #48478 | `graceMs` + 状态机 `idle→generating→interrupted(collecting)→restarting` | 收集窗口内所有消息，只做一次决策 |
| TU Munich Deadline-Aware Interrupt Coalescing (2023) | 单调截止时间（monotonic deadline） | 中断合并，但有硬性截止保证 |
| Pipecat PR #1931 `InterruptionStrategy` | `MinWordsInterruptionStrategy` + `min_words` 阈值 | 不是每个信号都触发中断，需达到阈值 |

**防护：单调截止门（Monotonic Deadline Gate）**

```
WAIT 进入时刻 t₀ 设定 deadline = t₀ + GATE_TIMEOUT（800ms）

WAIT 期间：
  - 新消息 → 仅 ACCUMULATE，不触发新 Arbiter 调用
  - Arbiter-B 在 deadline 前返回 → 按结果 emit 或 abort
  - deadline 到期 Arbiter-B 仍未返回 → fail-open（emit segment）

关键约束：deadline 是单调的，新消息不能延长它
```

为什么 fail-open（emit）而不是 fail-closed（abort）：
- abort = 用户看到沉默 = 更差体验
- 当前 segment 是基于有效上下文生成的，即使有新消息也不一定失效
- 下一个 segment 边界会用完整新上下文重新判断

#### 场景 2：abort-restart 死循环

```
generation_1 → segment_1 → Arbiter-B: abort → re-fire
  → generation_2 开始 → generation_2 期间又有新消息
    → segment_1' → Arbiter-B: abort → re-fire
      → generation_3 → ...永远在重启，用户永远收不到回复
```

**根因**：活跃群持续有人说话，每次生成都被新消息打断。

**工业界解法**：

| 来源 | 机制 |
|---|---|
| OpenClaw #48478 | 频率限制：N 次 interrupt/window 后降级为 queue 模式 |
| Pipecat #1931 | `min_words` 阈值——不是任何输入都算中断 |
| Rate-Monotonic Scheduling / Sporadic Server | 中断有预算（budget），耗尽后降级 |

**防护：中断预算（Interrupt Budget）**

```python
MAX_ABORTS_PER_FIRE = 2  # 单次 fire 周期内最多 abort 2 次

on Arbiter-B returns "abort":
    abort_count += 1
    if abort_count >= MAX_ABORTS_PER_FIRE:
        mode = FORCE_EMIT  # 降级：强制 emit 剩余 segments
    else:
        abort_and_refire()
```

效果：最坏情况 bot 最多 abort 2 次（~2-3s），第 3 次强制发出回复。用户不会无限等待。

#### 场景 3：Arbiter 超时 / 服务不可用

```
segment ready → Arbiter-B fires → WAIT → DeepSeek 无响应 → 永久 WAIT
```

场景 1 的 Monotonic Deadline Gate 已覆盖单次超时。但若 Arbiter 连续超时，需要熔断。

**防护：熔断器（Circuit Breaker）**

```python
TIMEOUT_THRESHOLD = 3  # 连续 3 次超时 → 熔断

on Arbiter-B timeout:
    consecutive_timeouts += 1
    if consecutive_timeouts >= TIMEOUT_THRESHOLD:
        arbiter_b_circuit = OPEN  # 后续 segments 直接 emit
        schedule_half_open(30s)   # 30s 后半开尝试
    emit_segment()

on Arbiter-B success:
    consecutive_timeouts = 0
    arbiter_b_circuit = CLOSED
```

依据：`services/llm/client.py` context compaction 已使用同模式 circuit breaker——同代码库先例。

#### 场景 4：多用户同时 @mention + 正在生成

```
bot 正在回复 user_A（running_task active）
user_B: @bot 你好     → pending_at = True（当前：丢失内容）
user_C: @bot 帮我查   → pending_at 已经是 True（丢失）
user_D: @bot 今天天气  → 同上（丢失）
```

**根因**：`pending_at: bool` 只记住"有人@了"，丢失具体消息。

**防护**：Change D 的 `pending_during_generation: list[PendingMessage]` 已解决。re-fire 时全部消息进入 Arbiter-A completeness loop，LLM 看到完整上下文统一回复。

#### 场景 5：segment 发送延迟串行瓶颈

```
5 segments × 300ms Arbiter-B = 1.5s 额外延迟
```

**防护：首段免检 + 无新消息快速通道**

```
segment_1: 直接 emit（首段延迟最敏感，此时大概率无新消息）
segment_N (N>1):
  - 无 new_pending → 直接 emit（零延迟，不调用 Arbiter）
  - 有 new_pending → Arbiter-B gate（仅此时付出延迟代价）
```

当前代码 `services/scheduler.py:1136-1138` 已有 `if not new_pending: return True` 快速通道。首段免检是额外优化。

#### 完整状态机

```
                    ┌─────────────────────────────────────────┐
                    │                                         │
    IDLE ──@msg──→ ARBITER-A(collecting) ──complete──→ FIRE
                    │  ↑                                      │
                    │  └── new msg (accumulate only) ──┘      │
                    │  deadline 5s → force fire               │
                    │                                         │
    ┌───────────────┴─────────────────────────────────────────┘
    │
    ▼
 GENERATING ──stream──→ SEGMENT READY
    │                        │
    │                        ├─ no new_pending → EMIT (fast path, 零延迟)
    │                        │
    │                        ├─ has new_pending + is_first_segment → EMIT (首段免检)
    │                        │
    │                        └─ has new_pending + not first →
    │                              │
    │                              ▼
    │                         ARBITER-B GATE (deadline = 800ms)
    │                              │
    │                    ┌─────────┼──────────┐
    │                    │         │          │
    │               returns    returns    deadline
    │              "continue"  "abort"    expires
    │                    │         │          │
    │                    ▼         │          ▼
    │                  EMIT        │      EMIT (fail-open)
    │                              │
    │                              ▼
    │                    abort_count < MAX_ABORTS?
    │                     yes/        \no
    │                      ▼           ▼
    │                   ABORT      FORCE_EMIT (降级)
    │                   + re-fire
    │
    └── finally: pending_during_generation 非空?
              → 喂给 Arbiter-A completeness loop → 新一轮 FIRE
```

#### 设计参数表

| 参数 | 值 | 依据 |
|---|---|---|
| `GATE_TIMEOUT_MS` | 800 | DeepSeek V4 Flash P95 ~400ms；2× 留余量 |
| `MAX_ABORTS_PER_FIRE` | 2 | 最坏 2 次 abort ≈ 2-3s；第 3 次强制发出 |
| `CIRCUIT_BREAKER_THRESHOLD` | 3 | 连续 3 次超时 → 熔断 |
| `CIRCUIT_HALF_OPEN_S` | 30 | 30s 后半开尝试恢复 |
| `FIRST_SEGMENT_EXEMPT` | true | 首段免 Arbiter-B，降低首字延迟 |

#### 三层防护总结

| 层 | 防护对象 | 机制 | 最坏情况行为 |
|---|---|---|---|
| L1: Monotonic Deadline | 单次 Arbiter-B 调用超时 | 800ms 硬截止 → fail-open emit | 单 segment 最多延迟 800ms |
| L2: Interrupt Budget | abort-restart 死循环 | 2 次 abort 后强制 emit | 最多 2 次重启后必出回复 |
| L3: Circuit Breaker | Arbiter 服务持续不可用 | 3 次超时后熔断，30s 半开 | 降级为无 Arbiter 模式（等同当前行为） |

#### 工业来源索引

| 来源 | URL / 标识 | 贡献 |
|---|---|---|
| OpenClaw #48478 | github.com/openclaw/openclaw/issues/48478 | `graceMs` 状态机 + interrupt budget + cancel-and-restart 生命周期 |
| Pipecat #1931 | github.com/pipecat-ai/pipecat/pull/1931 | `InterruptionStrategy` 基类 + `MinWordsInterruptionStrategy` 阈值门控 |
| TU Munich Deadline-Aware Interrupt Coalescing | mediatum.ub.tum.de/doc/1223130 | 单调截止时间 + 中断合并 + 硬性实时保证 |
| Agent Patterns Catalog: Stop/Cancel | agentpatternscatalog.org/patterns/stop-cancel/ | cancellation token 传播 + partial state 保留 + fail-open 原则 |
| Sporadic Server Scheduling | Diva-portal.org/smash/get/diva2:1027222 | 中断预算（budget）防饿死 |

#### 交叉验证：Voice AI 应用 × 学术论文 → Arbiter 设计验证

> 方法：并行 agent 分别从生产系统（Pipecat / LiveKit / ElevenLabs / Vapi / Hume EVI / Retell）和学术论文两个维度调研语音 AI 中断架构，以下为交叉验证结论。

**1. 收敛点（两个维度独立确认）**

| 设计决策 | 应用验证 | 学术验证 | 置信度 |
|---------|---------|---------|--------|
| fail-open（超时放行） | 所有 6 个系统默认 fail-open；Hume 核心哲学 "always interruptible" | FSTTM cost matrix: late-response 代价 > false-cut 代价 (Raux 2009) | **强** |
| Arbiter 与 generation 并行 | LiveKit dual-path (VAD+STT)；Pipecat pipeline frame 并行 | Moshi 双流 (Défossez 2024)；Khouzaimi Scheduler 模块 (2015)；Speculative Decoding | **强** |
| Segment 级 wait/abort | Pipecat InterruptionFrame 双向传播 + 清空缓冲 | IU Framework revoke/commit (Schlangen 2011)；Incremental NLG word buffer + purge | **强** |
| 首段免检 | Pipecat `MinWords` 仅 bot 说话时生效（bot 沉默时 1 词触发） | DeVault "maximal understanding point" — 首次理解点前不打断 (2011) | **强** |
| 消息合并（WAIT 期间累积） | 所有系统有 endpointing 窗口 (500ms-3s) 内累积 | VAP 连续概率预测（不做逐帧二元判断）(Ekstedt 2022) | **强** |
| L1 单调截止 (800ms) | LiveKit `max_endpointing_delay=3s`；Hume `end_of_turn_silence_ms=800ms` | FSTTM cost-based deadline；Ferrer prosodic EOT 固定阈值研究 | **强** |
| L3 熔断器 | **无系统有此设计**（Retell 仅手动 `sensitivity=0`） | Circuit Breaker pattern (Nygard 2007)；Hystrix/Resilience4j 工业验证 | **强**（理论）/ 独创（应用） |
| L2 中断预算 | **无系统有此设计**（LiveKit `backoffSeconds` 间接限频） | Khouzaimi RL reward 中 penalty term 隐含类似约束 | **中**（隐式验证） |

**2. 我们的独创贡献（无直接先例）**

| 特性 | 为什么独创 | 为什么合理 |
|------|-----------|-----------|
| L2 中断预算（显式 max_aborts） | 语音系统用 `min_words`/`backoffSeconds` 间接限频，无显式计数器 | 文字聊天的 abort 代价高于语音（语音可 resume，文字 abort 后需完整重新生成） |
| L3 自动熔断 | 语音系统无自动降级（依赖人工 sensitivity 调节） | 文字 bot 的 Arbiter 依赖外部 API（DeepSeek），语音系统的 VAD 是本地模型（无网络故障风险） |
| LLM-as-judge（语义级判断） | 语音系统用 VAD/prosody（信号级），不用 LLM | 文字无声学信号，只有语义信号；LLM 是唯一能做语义完整性判断的工具 |

**3. 学术界建议的补充机制（评估）**

| 建议 | 来源 | 是否采纳 | 理由 |
|------|------|---------|------|
| Half-Open 恢复探测 | Circuit Breaker pattern | ✅ 已有 | `CIRCUIT_HALF_OPEN_S=30` 后允许 1 个请求通过测试恢复 |
| Confidence score 替代 boolean | VAP (Ekstedt 2022) | ✅ 已有 | Arbiter-A 已返回 `confidence` 字段 + `completeness_confidence_threshold` |
| Revoke 后显式修复消息 | DIUM (Buss & Schlangen 2011) | ❌ 不采纳 | 文字聊天中 abort 后 re-fire 即为"修复"；发送"等等我重新想想"反而打断自然感 |
| Backchannel 生成（WAIT 期间发"嗯"） | Gravano & Hirschberg 2011 | ⏳ 后续考虑 | 有价值但超出当前 fix 范围；可作为 B4 阶段 follow-up |
| RL 自动调优阈值 | Khouzaimi 2015/2016 | ⏳ 后续考虑 | 需要用户满意度反馈数据；当前手工参数作为 baseline |
| Acknowledgment vs new-turn 区分 | Gravano turn-yielding cues | ✅ 已有 | Arbiter-A completeness judge 本身就在做此判断 |

**4. 关键数值对标**

| 参数 | 我们的值 | 行业范围 | 学术建议 | 结论 |
|------|---------|---------|---------|------|
| Gate timeout (L1) | 800ms | LiveKit 3s / Hume 800ms / Vapi sigmoid(0.4-2s) | FSTTM: 取决于 cost ratio | **800ms 合理**（与 Hume 一致，DeepSeek P95 ~400ms 的 2×） |
| Max aborts (L2) | 2 | 无直接对标 | Khouzaimi: RL 学到 ~2-3 次 | **2 合理**（保守起步，可后续 RL 调优） |
| Circuit breaker threshold (L3) | 3 | Hystrix 默认 5 / Resilience4j 默认 5 | Nygard: 3-5 | **3 合理**（偏激进，因为 Arbiter 延迟敏感） |
| Circuit half-open (L3) | 30s | Hystrix 默认 5s / Resilience4j 默认 60s | 取决于恢复预期 | **30s 合理**（DeepSeek 故障通常 10-60s 恢复） |
| First segment exempt | true | Pipecat: min_words 仅 bot 说话时 | DeVault: 首次理解点前不打断 | **合理**（行业+学术双重验证） |

**5. 架构同构映射**

```
Voice AI Pipeline              Arbiter 文字聊天架构
─────────────────              ──────────────────────
VAD (音频信号)          ←→     Timeline new_pending (文字信号)
STT endpointing         ←→     Arbiter-A completeness judge
Barge-in detection      ←→     Arbiter-B interruption judge
TTS cancel + flush      ←→     Emission Gate abort + SegmentAborted
InterruptionFrame       ←→     gate.resolve(abort)
UninterruptibleFrame    ←→     已发送 segment（不可撤回）
min_words threshold     ←→     首段免检 + Arbiter-B 语义判断
endpointing window      ←→     Arbiter-A poll interval + max_wait
resume after false int  ←→     Arbiter-B "continue" → gate re-opens
Moshi dual-stream       ←→     generation stream ∥ arbiter monitor
```

### 10.9 参考文献

| ID | 引用 | 用途 |
|----|------|------|
| [SCM] | Li, Y. et al. "From Judgment to Interference: Early Stopping LLM Harmful Outputs via Streaming Content Monitoring." arXiv:2506.09996, Jun 2025. | Emission Gate 并发 monitor 设计原型 |
| [TRT-LLM] | NVIDIA TensorRT Edge-LLM, StreamChannel cancellation design. nvidia.github.io/TensorRT-Edge-LLM | check-at-iteration-top + fire-and-forget cancellation |
| [SpecDec] | Leviathan, Y. et al. "Fast Inference from Transformers via Speculative Decoding." ICML 2023. / Chen, C. et al. "Accelerating Large Language Model Decoding with Speculative Sampling." 2023. | verify-then-emit gate 模式 |
| [LSM] | "Large Supervisor Models: Real-Time LLM Output Stream Supervision for Interruption." Pro-GenAI 2026, IEEE. ResearchGate:401283765. | interrupt signal 不改写内容 |
| [Vercel] | vercel/chat PR #277 "add concurrency strategies for overlapping messages" + issue #414 "queue-debounce". Mar-Apr 2026. | burst/debounce 策略设计 |
| [CF-Agents] | cloudflare/agents PR #1192 "Add message concurrency controls to AIChatAgent". May 2026. | merge/debounce/latest 策略；750ms default |
| [NeMo] | NVIDIA NeMo Guardrails streaming output guardrails. developer.nvidia.com/blog, May 2025. | 流式输出 + guardrail 中断 |
| [OpenClaw] | openclaw/openclaw#48478 "Cancel and restart generation on new inbound message". Mar 2026. | `graceMs` 状态机 + interrupt budget + cancel-and-restart 生命周期 |
| [Pipecat] | pipecat-ai/pipecat PR #1931 "Add support for interruption strategies". May 2025. | `InterruptionStrategy` 基类 + `MinWordsInterruptionStrategy` 阈值门控 |
| [TU-Munich] | Deadline-Aware Interrupt Coalescing. mediatum.ub.tum.de/doc/1223130. | 单调截止时间 + 中断合并 + 硬性实时保证 |
| [VAP] | Ekstedt, E. & Skantze, G. "Voice Activity Projection." Interspeech 2022. | 连续概率预测 turn-taking；验证 Arbiter-A confidence score 设计 |
| [FSTTM] | Raux, A. & Eskenazi, M. "A Finite-State Turn-Taking Model for Spoken Dialog Systems." NAACL-HLT 2009. | 6 状态机 + cost matrix 决策论；L1 deadline 的理论框架 |
| [IU] | Schlangen, D. & Skantze, G. "A General, Abstract Model of Incremental Dialogue Processing." D&D 2011. | add/revoke/commit 操作；segment 级 abort 的理论基础 |
| [Moshi] | Défossez, A. et al. (Kyutai) "Moshi: Full-Duplex Spoken Dialogue." arXiv 2024. | 双流并行架构；generation ∥ arbiter 的架构原型 |
| [Khouzaimi] | Khouzaimi, H. et al. "RL for Turn-Taking in Incremental SDS." SIGDIAL 2015 / IJCAI 2016. | Scheduler 模块分离 + RL 自动调优阈值 |
| [DeVault] | DeVault, D. et al. "Incremental Interpretation & Maximal Understanding." D&D 2011. | "最大理解点"检测 precision ~0.95；Arbiter-A 触发时机验证 |
| [CircuitBreaker] | Nygard, M. "Release It!" Pragmatic Bookshelf, 2007. / Netflix Hystrix / Resilience4j. | L3 熔断器 Closed→Open→Half-Open 三态模式 |
| [LiveKit] | LiveKit Agents: Semantic Turn Detection + interruption handling. 2024-2025. | 三层检测 (VAD→endpointing→semantic model)；`false_interruption_timeout` + resume |
| [Hume] | Hume EVI: "always interruptible" design. 2024-2025. | `min_interruption_ms=800ms`；fail-open 哲学验证 |
| [Vapi] | Vapi Voice Pipeline: Stop/Start Speaking Plans. 2024-2025. | `backoffSeconds` 冷却期；sigmoid 动态 endpointing |
| [AgentPatterns] | Agent Patterns Catalog: Stop/Cancel. agentpatternscatalog.org. Apr 2026. | cancellation token 传播 + partial state 保留 |

---

## 11. 自审记录

| 自审项 | 验证手段 | 结论 |
|--------|----------|------|
| scheduler.py notify() 当前行为 | Read [services/scheduler.py:291-350](../../services/scheduler.py#L291-L350) | is_at=True 时立即 fire 或 pending_at=True；确认改造点 |
| client.py on_segment 签名 | Read [services/llm/client.py:2469](../../services/llm/client.py#L2469) | `Callable[[str], Awaitable[None]]`；确认需改为返回 bool |
| GroupSlot 字段 | Read [services/scheduler.py:93-112](../../services/scheduler.py#L93-L112) | 已有 running_task / pending_at；确认可追加 burst_pending / arbiter_task |
| router.py followup 检测 | Read [kernel/router.py:469-514](../../kernel/router.py#L469-L514) | `_last_assistant_replied_to_user` 已修复 pause_then_extend 连续 assistant turn 问题 |
| config.py 无 ArbiterConfig | grep `ArbiterConfig` kernel/config.py | 0 命中；确认需新增 |
| 主线方案文档完整性 | Read [fix-at-mention-burst-batching.md](./fix-at-mention-burst-batching.md) §7-§10 | §7 Arbiter 架构 + §9 缓存分析 + §10 工作流审计 完整 |
| 派单格式与 Part 6 bugfix 一致 | 对照 [bugfix-part1-execution](./omubot-humanization-part6-bugfix-part1-execution.md) | §0 背景 / §1 依赖 / §2-5 Phase 详细 / §6 灰度 / §7 回滚 / §8 状态 / §9 交接 / §10 迭代 / §11 自审 全部对齐 |

### 11.1 v2 自审（2026-05-28 交叉验证后）

| # | 漏洞 | 严重度 | 位置 | 修复方案 |
|---|------|--------|------|---------|
| G1 | **D1 同模式位点遗漏**：`pending_at=True` 出现在 5 处，§10.6 只覆盖 line 350（`is_at`）。遗漏：line 372 (`is_directed_followup`)、line 381 (`is_correction`)、line 391 (`is_video_always`)、line 760 (`_arbiter_completeness_loop` race) | P1 | scheduler.py:372,381,391,760 | Change D 必须覆盖全部 5 处；v2 执行文档逐一列出 |
| G2 | **Monitor task 生命周期未指定**：§10.5 说"generation 结束 → monitor cancel"但没指定创建位置和 cancel 机制 | P2 | scheduler.py `_do_chat` | v2 执行文档指定：在 `_do_chat` 内 `_llm.chat()` 调用前创建，`finally` 块 cancel |
| G3 | **Arbiter-A prompt v2 内容未指定**：§10.3 列出 Change C 但没给出具体 prompt 文本 | P2 | arbiter.py:20-27 | v2 执行文档给出完整 prompt 文本 |
| G4 | **`check()` 无内部 timeout**：若 monitor 在 `arm()` 后崩溃（未 `resolve()`），`on_segment` 永久阻塞 | P1 | _EmissionGate.check() | `check()` 内部加 `asyncio.wait_for(self._event.wait(), timeout=_GATE_TIMEOUT_S)` 兜底 |
| G5 | **line 760 race**：Arbiter-A 判定 complete 但 running_task 仍活跃时，`burst_pending` 被 finally 清空后 re-fire 丢失消息来源信息 | P3 | scheduler.py:759-766 | 在 `pending_at=True` 前将 `burst_pending` 转存到 `pending_during_generation`；v2 执行文档覆盖 |
