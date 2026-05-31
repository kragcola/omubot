# Arbiter Emission Gate 架构 — v2 执行派单

> 状态：2026-05-28 立。
>
> 前置文档：[v1 调研与设计](./fix-at-mention-burst-batching-execution.md) §10.1-10.8
>
> 本文是可直接执行的派单。每个 Phase 独立 commit，按顺序串行。

---

## 0. 执行者必读

### 0.1 你要解决什么

用户连续 @bot 发消息时，bot 对每条分别回复（错位感）。更严重的是：bot 生成回复期间收到新消息时，已生成的段落照发不误（无法打断），且 re-fire 时跳过"用户是否说完"判断。

### 0.2 设计原则（不可违背）

> **Arbiter 断点随时随处可切入主回复链路。Arbiter 判断未返回时 → wait；判断返回后 → 中断或继续。**

### 0.3 三层防护（不可省略任何一层）

| 层 | 防护对象 | 机制 | 参数 |
|----|---------|------|------|
| L1 | 单次 Arbiter-B 超时 | 单调截止门：800ms hard deadline → fail-open emit | `_GATE_TIMEOUT_S = 0.8` |
| L2 | abort-restart 死循环 | 中断预算：单次 fire 周期最多 abort 2 次 → 强制 emit | `_MAX_ABORTS_PER_FIRE = 2` |
| L3 | Arbiter 服务持续不可用 | 熔断器：连续 3 次超时 → 跳过 Arbiter-B，30s 后半开探测 | `_CB_THRESHOLD = 3`, `_CB_HALF_OPEN_S = 30` |

### 0.4 安全约束

- **不允许修改人设文件**（source.md / instruction.md）
- **Arbiter 超时/不可用时必须 fallback 到"立即发送"**——不比现状差
- **首段免检**：第一个 segment 不经过 Arbiter-B（首字延迟最敏感）

### 0.5 部署顺序

```
B（独立 bug fix）→ C（独立 prompt 优化）→ D（pending_at 改造）→ A（Emission Gate 架构）
```

每步可独立验证、独立回滚。D 和 A 有弱依赖（A 依赖 D 的 `pending_during_generation` 字段存在），但 D 不依赖 A。

---

## 1. Phase B — `_MAX_TOKENS` 修复

### 1.1 问题

`services/llm/arbiter.py:18` 的 `_MAX_TOKENS = 15` 导致 Arbiter-B/C 的 JSON 输出被截断。

示例：`{"action": "continue", "reason": "` ← 在第 34 字符处被切断 → `invalid_json` 错误。

### 1.2 修改

**文件**：`services/llm/arbiter.py`

```python
# 修改前（line 18）
_MAX_TOKENS = 15

# 修改后
_MAX_TOKENS = 48
```

### 1.3 为什么是 48

- Arbiter-A 输出：`{"complete": true, "confidence": 0.95}` ≈ 15 tokens ✓
- Arbiter-B 输出：`{"action": "abort_unsent", "reason": "用户否定了之前的内容"}` ≈ 35 tokens
- Arbiter-C 输出：类似 B ≈ 35 tokens
- 48 tokens 留有余量，不会浪费（DeepSeek 按实际输出计费）

### 1.4 验证

```bash
# 1. grep 确认只有一处
grep -rn "_MAX_TOKENS" services/llm/arbiter.py

# 2. 跑 pytest（如果有 arbiter 相关测试）
uv run pytest tests/ -k arbiter -v

# 3. 手动验证：启动 bot，在群里 @bot 发消息，观察日志无 invalid_json
docker compose restart bot
docker compose logs bot --tail=50 | grep -i "invalid_json\|arbiter"
```

### 1.5 回滚

```bash
# 改回 15
sed -i 's/_MAX_TOKENS = 48/_MAX_TOKENS = 15/' services/llm/arbiter.py
docker compose restart bot
```

---

## 2. Phase C — Arbiter-A Prompt v2

### 2.1 问题

当前 prompt（line 20-27）有错误规则："消息末尾有句号 → 大概率说完"。

QQ 社会语言学调研（§10.1）证明：QQ 群聊中句号表达冷淡/不满，不表示"说完了"。真正的完成信号是语义完整性 + 时间间隔。

### 2.2 修改

**文件**：`services/llm/arbiter.py`

```python
# 修改前（line 20-27）
_COMPLETENESS_SYSTEM_PROMPT = """你是对话完整性判断器。根据用户最近发送的消息，判断用户是否说完了当前这轮话。
输出严格 JSON：{"complete": true/false, "confidence": 0.0-1.0}
判断依据：
- 消息末尾有句号/问号/感叹号 -> 大概率说完
- 消息末尾有逗号/连词/省略号 -> 大概率没说完
- 多条消息间隔 <3s 且最新一条很短 -> 大概率还有后续
- 最新消息是对前一条的补充说明 -> 大概率没说完
只输出 JSON，不要解释。"""

# 修改后
_COMPLETENESS_SYSTEM_PROMPT = """你是 QQ 群聊完整性判断器。判断用户是否说完了当前这轮话。
输出严格 JSON：{"complete": true/false, "confidence": 0.0-1.0}

判断依据（QQ 群聊语境）：
- 语义完整且可独立回应 → complete
- 明显话说一半（"我觉得"、"就是那个"、"等一下"） → incomplete
- 连续短消息（每条<6字）且最新一条无独立语义 → incomplete（用户在分条打字）
- 最新消息是对前一条的补充/修正/追加条件 → incomplete

注意：QQ 中句号(。)不代表说完，常表达语气（冷淡/强调）。不要依赖标点判断。
只输出 JSON，不要解释。"""
```

### 2.3 验证

```bash
# 1. D1 同模式扫描：确认没有其他地方硬编码了"句号=说完"逻辑
grep -rn "句号\|。.*说完\|punctuation.*complete" services/ kernel/ plugins/

# 2. 重启 bot，观察 arbiter_a_fire 日志
docker compose restart bot
# 在群里连发两条短消息（间隔 <2s），观察是否等第二条到了才 fire
docker compose logs bot --tail=100 | grep "arbiter_a"
```

### 2.4 回滚

```bash
git checkout -- services/llm/arbiter.py
docker compose restart bot
```

---

## 3. Phase D — `pending_at` → `pending_during_generation`

### 3.1 问题

`pending_at: bool` 在 5 处被设为 True，但只记住"有消息来了"，丢失具体内容和数量。re-fire 时直接 `_fire()` 跳过 Arbiter-A completeness 判断。

### 3.2 涉及位点（D1 同模式扫描结果）

| # | 位置 | 触发条件 | 当前行为 |
|---|------|---------|---------|
| 1 | scheduler.py:350 | `is_at` + running_task active | `pending_at = True` |
| 2 | scheduler.py:372 | `is_directed_followup` + running_task active | `pending_at = True` |
| 3 | scheduler.py:381 | `is_correction` + running_task active | `pending_at = True` |
| 4 | scheduler.py:391 | `is_video_always` + running_task active | `pending_at = True` |
| 5 | scheduler.py:760 | `_arbiter_completeness_loop` 完成但 running_task 仍活跃 | `pending_at = True`（burst_pending 随后被 finally 清空） |

### 3.3 修改步骤

**步骤 1：`_GroupSlot` 加字段**

```python
# scheduler.py _GroupSlot.__slots__ 追加：
"pending_during_generation",

# __init__ 追加：
self.pending_during_generation: list[PendingMessage] = []
```

**步骤 2：修改位点 1-4（notify 路径）**

对 line 350、372、381、391 统一改为：

```python
# 修改前
slot.pending_at = True

# 修改后
slot.pending_during_generation.append(
    PendingMessage(
        content=message_text or (trigger.reason if trigger is not None else "") or "@我",
        user_id=user_id,
        timestamp=time.time(),
    )
)
```

> **注意**：line 350 已有类似逻辑（line 354-358 的 `burst_pending.append`），可复用同一 PendingMessage 构造。

**步骤 3：修改位点 5（completeness loop race）**

```python
# scheduler.py:759-761 修改前
if slot.running_task and not slot.running_task.done():
    slot.pending_at = True
    return

# 修改后
if slot.running_task and not slot.running_task.done():
    # 将 Arbiter-A 已判定 complete 的消息转存，防止 finally 清空后丢失
    slot.pending_during_generation.extend(slot.burst_pending)
    return
```

**步骤 4：修改 `finally` 块（re-fire 路径）**

```python
# scheduler.py:1226-1228 修改前
finally:
    if slot:
        slot.running_task = None
        if slot.pending_at or slot.msg_count > 0:
            slot.pending_at = False
            self._fire(group_id)

# 修改后
finally:
    if slot:
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

**步骤 5：清理 `pending_at` 残留**

```bash
# 确认所有 pending_at 引用都已处理
grep -n "pending_at" services/scheduler.py
```

需要同步修改：
- `__slots__` 中删除 `"pending_at"`
- `__init__` 中删除 `self.pending_at: bool = False`
- `mute()` (line 234)：`slot.pending_at = False` → `slot.pending_during_generation = []`
- `reset_slot()` (line 524)：同上
- `status()` (line 287)：`"pending_at": slot.pending_at` → `"pending_during_generation": len(slot.pending_during_generation)`

### 3.4 验证

```bash
# 1. grep 确认 pending_at 已完全移除
grep -n "pending_at" services/scheduler.py
# 预期：0 命中

# 2. lint + type check
uv run ruff check services/scheduler.py
uv run pyright services/scheduler.py

# 3. pytest
uv run pytest tests/ -k "scheduler" -v

# 4. 集成验证：bot 生成期间连发 3 条 @，观察日志
docker compose restart bot
# 发送 @bot msg1, @bot msg2, @bot msg3（间隔 <1s）
# 预期日志：
#   "scheduler | group=xxx @ queued during generation (n=1)"
#   "scheduler | group=xxx @ queued during generation (n=2)"
#   "scheduler | group=xxx @ queued during generation (n=3)"
#   generation 结束后：
#   "arbiter_a_fire | group=xxx pending=3 confidence=..."
```

### 3.5 回滚

```bash
git checkout -- services/scheduler.py
docker compose restart bot
```

---

## 4. Phase A — Emission Gate 架构（核心改造）

> **前置依赖**：Phase D 已合并（`pending_during_generation` 字段存在）

### 4.1 概述

在 `services/scheduler.py` 中新增 `_EmissionGate` 类和 `_arbiter_b_monitor` 并发 task，实现"先判后发"。

### 4.2 新增常量

在 `services/scheduler.py` 顶部（import 区域之后）：

```python
import time as _time_mod  # 如果尚未 import

_GATE_TIMEOUT_S: float = 0.8
_MAX_ABORTS_PER_FIRE: int = 2
_CB_THRESHOLD: int = 3
_CB_HALF_OPEN_S: float = 30.0
```

### 4.3 新增 `_EmissionGate` 类

在 `_GroupSlot` 类之后、`GroupChatScheduler` 类之前插入：

```python
class _EmissionGate:
    """Segment emission gate with three-layer protection.

    L1: Monotonic deadline — WAIT 最多 _GATE_TIMEOUT_S，超时 fail-open
    L2: Interrupt budget — abort 次数超限后强制 emit
    L3: Circuit breaker — 连续超时后熔断，降级为无 Arbiter 模式
    """

    __slots__ = (
        "_state", "_event", "_verdict",
        "_abort_count", "_consecutive_timeouts", "_circuit_open_until",
        "_segment_index",
    )

    def __init__(self) -> None:
        self._state: Literal["open", "pending", "abort"] = "open"
        self._event: asyncio.Event = asyncio.Event()
        self._event.set()
        self._verdict: InterruptionResult | None = None
        self._abort_count: int = 0
        self._consecutive_timeouts: int = 0
        self._circuit_open_until: float = 0.0
        self._segment_index: int = 0

    def arm(self) -> None:
        if self._state == "open":
            self._state = "pending"
            self._event.clear()

    def resolve(self, verdict: InterruptionResult, *, timed_out: bool = False) -> None:
        if timed_out:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= _CB_THRESHOLD:
                self._circuit_open_until = _time_mod.monotonic() + _CB_HALF_OPEN_S
                _L.warning("arbiter_b_circuit_open | will retry after {}s", _CB_HALF_OPEN_S)
            self._state = "open"
            self._verdict = None
        elif verdict.action == "continue":
            self._consecutive_timeouts = 0
            self._state = "open"
        else:
            self._consecutive_timeouts = 0
            self._abort_count += 1
            if self._abort_count > _MAX_ABORTS_PER_FIRE:
                self._state = "open"
                _L.warning("arbiter_b_budget_exhausted | forcing emit")
            else:
                self._state = "abort"
            self._verdict = verdict
        self._event.set()

    @property
    def circuit_open(self) -> bool:
        return _time_mod.monotonic() < self._circuit_open_until

    async def check(self) -> bool:
        self._segment_index += 1
        if self._segment_index == 1:
            return True  # 首段免检
        if self.circuit_open:
            return True  # L3 熔断期间直接放行
        if self._state == "open":
            return True
        if self._state == "abort":
            return False
        # PENDING: wait with L1 deadline as safety net
        try:
            await asyncio.wait_for(self._event.wait(), timeout=_GATE_TIMEOUT_S)
        except asyncio.TimeoutError:
            # G4 fix: monitor 崩溃时 on_segment 不会永久阻塞
            self._state = "open"
            self._consecutive_timeouts += 1
            _L.warning("gate_check_timeout | monitor may have crashed")
            return True
        return self._state != "abort"

    @property
    def verdict(self) -> InterruptionResult | None:
        return self._verdict

    @property
    def abort_count(self) -> int:
        return self._abort_count
```

### 4.4 新增 `_arbiter_b_monitor` 方法

在 `GroupChatScheduler` 类中，`_arbiter_completeness_loop` 方法之后插入：

```python
async def _arbiter_b_monitor(
    self,
    group_id: str,
    gate: _EmissionGate,
    baseline: int,
    sent_texts: list[str],
    user_id: str,
) -> None:
    """Concurrent monitor: polls timeline, fires Arbiter-B, arms gate."""
    poll_interval = 0.15
    while True:
        await asyncio.sleep(poll_interval)
        if gate.circuit_open:
            continue
        new_pending = self._pending_messages_since(group_id, baseline)
        if not new_pending:
            continue
        gate.arm()
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
            gate.resolve(
                InterruptionResult(action="continue", reason="timeout", fallback=True),
                timed_out=True,
            )
            _L.warning("arbiter_b_timeout | group={}", group_id)
            continue
        except Exception:
            gate.resolve(
                InterruptionResult(action="continue", reason="error", fallback=True),
                timed_out=True,
            )
            _L.exception("arbiter_b_monitor_error | group={}", group_id)
            continue
        if result.action != "continue":
            _L.info(
                "arbiter_b_abort | group={} action={} reason={}",
                group_id, result.action, result.reason,
            )
            return
```

### 4.5 修改 `_do_chat` — 创建 monitor task + 改造 `on_segment`

**位置**：`_do_chat` 方法内，在 `self._llm.chat(...)` 调用之前。

**步骤 1：创建 gate 和 monitor task**

在 `generation_pending_baseline = ...`（约 line 1097）之后插入：

```python
# Emission Gate setup
gate: _EmissionGate | None = None
monitor_task: asyncio.Task[None] | None = None
if (
    self._arbiter is not None
    and self._arbiter_enabled(group_id)
    and bool(getattr(self._arbiter_config, "interruption_enabled", False))
):
    gate = _EmissionGate()
    monitor_task = asyncio.create_task(
        self._arbiter_b_monitor(
            group_id=group_id,
            gate=gate,
            baseline=generation_pending_baseline,
            sent_texts=sent_texts,
            user_id=uid,
        )
    )
    monitor_task.add_done_callback(lambda _: None)
```

**步骤 2：改造 `on_segment` 回调**

将当前的 `on_segment`（line 1108-1156）替换为：

```python
async def on_segment(
    text: str,
    _prefix: str = reply_prefix,
    _target_user_id: str = uid,
    _baseline: int = generation_pending_baseline,
    _uid: str = uid,
    _sent_texts: list[str] = sent_texts,
    _gate: _EmissionGate | None = gate,
) -> bool:
    nonlocal first_segment, sent_segments, send_total_elapsed

    # Gate check BEFORE emission
    if _gate is not None:
        if not await _gate.check():
            # Arbiter-B 判定中断
            if _gate.verdict and _gate.verdict.action == "revise":
                new_msgs = self._pending_messages_since(group_id, _baseline)
                slot.pending_during_generation.extend(
                    PendingMessage(content=m.content, user_id=m.user_id, timestamp=m.timestamp)
                    for m in new_msgs
                    if not any(p.content == m.content and p.timestamp == m.timestamp
                              for p in slot.pending_during_generation)
                )
            _L.info("on_segment_aborted | group={} action={}",
                    group_id, _gate.verdict.action if _gate.verdict else "abort")
            return False

    # Emit
    is_first = first_segment
    if first_segment:
        if _prefix:
            text = _prefix + text
        first_segment = False
    send_total_elapsed += await self._send_to_group(
        group_id,
        text,
        humanize="skip" if is_first else "normal",
        target_user_id=_target_user_id,
    )
    sent_segments += 1
    _sent_texts.append(text)
    return True
```

**步骤 3：在 `finally` 块中 cancel monitor task**

在 `_do_chat` 的 `finally` 块（line 1223 附近），在 `slot.running_task = None` 之前插入：

```python
# Cancel monitor task
if monitor_task is not None and not monitor_task.done():
    monitor_task.cancel()
    try:
        await monitor_task
    except (asyncio.CancelledError, Exception):
        pass
```

### 4.6 验证

```bash
# 1. lint + type check
uv run ruff check services/scheduler.py --fix
uv run pyright services/scheduler.py

# 2. pytest
uv run pytest tests/ -v

# 3. 集成验证 — 场景 1：无新消息（零延迟）
docker compose up bot -d --build
# 在群里 @bot 发一条消息，观察回复速度与改造前无差异
# 日志中不应出现 arbiter_b 相关条目（因为无 new_pending）

# 4. 集成验证 — 场景 2：生成期间有新消息
# 在群里 @bot 发一条长问题（触发多段回复）
# 在 bot 回复第一段后立即发送新消息
# 预期：
#   - 第一段正常发出（首段免检）
#   - 日志出现 "arbiter_b_abort" 或 "arbiter_b_timeout"
#   - 如果 abort：后续段不发出，generation 结束后 re-fire
#   - 如果 continue：后续段正常发出

# 5. 集成验证 — 场景 3：Arbiter 超时（L1 验证）
# 临时将 DeepSeek API base 改为无效地址，触发超时
# 预期：日志出现 "arbiter_b_timeout"，segments 正常发出（fail-open）
# 连续 3 次后出现 "arbiter_b_circuit_open"

# 6. D2 cancel-path 测试
uv run pytest tests/ -k "test_emission_gate" -v
# 如果没有现成测试，需要新增（见 §4.7）
```

### 4.7 必须新增的测试

**文件**：`tests/test_emission_gate.py`

```python
"""Tests for _EmissionGate three-layer protection."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

# 导入路径根据实际调整
from services.scheduler import _EmissionGate, _GATE_TIMEOUT_S, _MAX_ABORTS_PER_FIRE


@pytest.mark.asyncio
async def test_gate_open_by_default():
    gate = _EmissionGate()
    assert await gate.check() is True  # 首段免检
    assert await gate.check() is True  # 无 pending，直接通过


@pytest.mark.asyncio
async def test_gate_pending_then_continue():
    gate = _EmissionGate()
    await gate.check()  # consume first segment exempt
    gate.arm()
    # Simulate Arbiter-B returning "continue"
    from services.llm.arbiter import InterruptionResult
    gate.resolve(InterruptionResult(action="continue", reason="ok"))
    assert await gate.check() is True


@pytest.mark.asyncio
async def test_gate_pending_then_abort():
    gate = _EmissionGate()
    await gate.check()  # first segment
    gate.arm()
    from services.llm.arbiter import InterruptionResult
    gate.resolve(InterruptionResult(action="revise", reason="user corrected"))
    assert await gate.check() is False
    assert gate.verdict.action == "revise"


@pytest.mark.asyncio
async def test_l1_timeout_failopen():
    """G4: check() 内部 timeout 防止永久阻塞"""
    gate = _EmissionGate()
    await gate.check()  # first segment
    gate.arm()
    # 不 resolve → check() 应在 _GATE_TIMEOUT_S 后 fail-open
    result = await asyncio.wait_for(gate.check(), timeout=_GATE_TIMEOUT_S + 1.0)
    assert result is True  # fail-open


@pytest.mark.asyncio
async def test_l2_budget_exhausted():
    """超过 MAX_ABORTS 后强制 emit"""
    gate = _EmissionGate()
    await gate.check()  # first segment
    from services.llm.arbiter import InterruptionResult
    for i in range(_MAX_ABORTS_PER_FIRE + 1):
        gate._state = "open"
        gate._event.set()
        gate.arm()
        gate.resolve(InterruptionResult(action="revise", reason=f"abort {i}"))
    # 第 3 次 abort 应被 budget 拦截 → state 变回 open
    assert gate._state == "open"


@pytest.mark.asyncio
async def test_l3_circuit_breaker():
    """连续超时后熔断"""
    gate = _EmissionGate()
    await gate.check()  # first segment
    from services.llm.arbiter import InterruptionResult
    for _ in range(3):
        gate.arm()
        gate.resolve(InterruptionResult(action="continue", reason="timeout", fallback=True), timed_out=True)
        gate._state = "open"
        gate._event.set()
    assert gate.circuit_open is True
    # 熔断期间 check() 直接返回 True
    gate.arm()  # 即使 arm 了
    assert await gate.check() is True  # circuit open → bypass
```

### 4.8 回滚

```bash
git checkout -- services/scheduler.py
# 如果新增了测试文件，保留（不影响运行时）
docker compose up bot -d --build
```

---

## 5. 灰度策略

| 阶段 | 范围 | 持续时间 | 观测指标 |
|------|------|---------|---------|
| Phase B+C | 全量（仅改 Arbiter 内部参数/prompt） | 1 天 | `invalid_json` 错误率降为 0 |
| Phase D | 全量（re-fire 路径改造） | 2 天 | `arbiter_a_fire` 日志中 `pending` 字段 > 1 的比例 |
| Phase A | 仅 `arbiter_enabled` 的群（当前 2 个测试群） | 3 天 | 首字延迟无退化；`arbiter_b_abort` 触发率；`gate_check_timeout` 率 < 5% |
| Phase A 全量 | 所有群 | 持续 | 同上 |

---

## 6. 全局回滚

```bash
# 一键回滚到改造前
git stash  # 保存当前工作
git log --oneline -10  # 找到 Phase B 之前的 commit
git revert --no-commit HEAD~N..HEAD  # N = 已合并的 phase 数
docker compose up bot -d --build
```

或逐 phase 回滚（每个 phase 独立 commit，可单独 revert）。

---

## 7. 状态表

### 7.1 2026-05-28 执行回填（Codex）

**Phase B — `_MAX_TOKENS` 修复**

- 已完成：`services/llm/arbiter.py` 将 `_MAX_TOKENS` 从 `15` 提升到 `48`，消除 Arbiter-B/C JSON 被截断的风险。
- 自审：
  - `rg -n "_MAX_TOKENS" services/llm/arbiter.py` 仅 1 处定义。
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter.py tests/test_arbiter_scheduler.py tests/test_arbiter_interruption.py tests/test_scheduler.py tests/test_emission_gate.py -q`
  - 结果：`71 passed`

**Phase C — Arbiter-A Prompt v2**

- 已完成：`services/llm/arbiter.py` completeness prompt 改为 QQ 群聊语境，不再把句号当作“说完”信号，强调语义完整性、连续短消息和补充/修正关系。
- 自审：
  - prompt 文本已显式加入“QQ 中句号(。)不代表说完”规则。
  - 相关回归仍通过：`tests/test_arbiter.py` / `tests/test_arbiter_scheduler.py`

**Phase D — `pending_at` → `pending_during_generation`**

- 已完成：
  - `services/scheduler.py` 删除 `_GroupSlot.pending_at`
  - 新增 `_GroupSlot.pending_during_generation: list[PendingMessage]`
  - `notify()` 中 `at_mention` / `directed_followup` / `correction` / `video_always` 的 running-task 路径全部改为积存真实消息
  - `_arbiter_completeness_loop()` race 路径改为转存 `burst_pending`
  - `_do_chat()` finally 改为优先把 `pending_during_generation` 重新送回 Arbiter-A completeness loop
  - `mute()` / `clear_pending()` / admin slot status 已同步到新字段
- 自审：
  - `rg -n "pending_at" services/scheduler.py` 0 命中
  - `tests/test_scheduler.py` / `tests/test_arbiter_scheduler.py` 已改为断言积存消息列表而非布尔标记

**Phase A — Emission Gate + monitor**

- 已完成：
  - `services/scheduler.py` 新增 `_EmissionGate`
  - 新增 `_arbiter_b_monitor()` 并发轮询任务
  - `on_segment()` 改为 emit 前 `gate.check()`，保留首段免检、L1 超时 fail-open、L2 abort budget、L3 circuit breaker
  - `_do_chat()` finally 新增 monitor cancel-path 清理
  - 新增 `tests/test_emission_gate.py`
- 关键订正：
  - 监控任务启动时不再预先吞掉 `baseline` 之后的首批新消息；否则 monitor 若晚于首段发送启动，会把 generation 期间第一条用户补充误记为已处理，导致漏判。该竞态已修正。
- 自审：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_arbiter_interruption.py -q` -> `5 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check ...` -> `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright ...` -> `0 errors, 0 warnings`
  - router/chat 接线回归：`source ./scripts/dev/env.sh && uv run pytest tests/test_router_b_cluster_wiring.py tests/test_router_qq_interactions.py tests/test_chat_plugin_humanization_wire.py -q` -> `21 passed`

| Phase | 状态 | 验证人 | 日期 |
|-------|------|--------|------|
| B: _MAX_TOKENS=48 | ✅ 已完成 | Codex | 2026-05-28 |
| C: Arbiter-A prompt v2 | ✅ 已完成 | Codex | 2026-05-28 |
| D: pending_at → pending_during_generation | ✅ 已完成 | Codex | 2026-05-28 |
| A: Emission Gate + monitor | ✅ 已完成 | Codex | 2026-05-28 |
| 灰度 Phase A（测试群） | ⏳ | | |
| 灰度 Phase A（全量） | ⏳ | | |

---

## 8. 常见问题

### Q: Phase A 部署后首字延迟变慢了？

检查：是否首段免检生效？日志中第一个 segment 应该没有 `gate_check` 相关条目。如果有，说明 `_segment_index` 计数有误。

### Q: 日志大量 `arbiter_b_timeout`？

检查 DeepSeek API 状态。如果连续 3 次超时，L3 熔断器会自动启用（日志出现 `arbiter_b_circuit_open`），此时行为等同于无 Arbiter（不比现状差）。30s 后自动尝试恢复。

### Q: bot 在活跃群里完全不回复了？

可能是 L2 中断预算 + abort 循环。检查日志是否有 `arbiter_b_budget_exhausted`。如果有，说明 Arbiter-B 连续判定 abort 超过 2 次后强制 emit 了——bot 应该有回复。如果确实无回复，检查 `on_segment_aborted` 后 `finally` 块是否正确触发 re-fire。

### Q: `pending_during_generation` 内存泄漏？

不会。`finally` 块在每次 generation 结束后清空该列表。`mute()` 和 `reset_slot()` 也会清空。

### Q: 如何临时禁用 Arbiter-B 而不回滚代码？

在 config 中设置 `arbiter.interruption_enabled = false`。Gate 会永远保持 OPEN，零开销。
