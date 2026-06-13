# 弱回复机制审计报告

> 状态：**审计完成 · 2026-06-07**
> 触发：生产日志发现 `closing_light_reply token='嗯，我在。'`——告别场景输出了陪伴 token,语义完全错误。
> 设计参考：[weak-reply-mechanism-design.md](weak-reply-mechanism-design.md)、[weak-reply-p0-impl-plan.md](weak-reply-p0-impl-plan.md)

---

## 一、问题全景

弱回复机制设计为两层:
- **P0 closing(收尾型)**:对方道别时回一个对称告别 token("晚安哦""明天见")
- **P1 companion(陪伴型)**:不需要有信息量的回复时回一个简短 ack("嗯嗯""哈哈")

落地后生产表现:
- closing 场景 **3 次触发,全部输出错误的 `"嗯，我在。"`**(语义是陪伴,不是告别)
- companion 场景 **1 次触发,掉进主 LLM 输出了完整内容**(根本没走弱回复)

---

## 二、Critical 问题

### C1. Speculative Token 启动条件与判断条件不一致

**位置**:`services/llm/client.py:4108` vs `:4285-4288`

**启动 token 生成的条件(line 4108):**
```python
if str(getattr(trigger, "mode", "") or "") == "closing":
    closing_token_task = speculative.submit(self._gen_closing_token, ...)
```

**判断是否进入 closing 短路的条件(line 4285-4288):**
```python
light_kind = str(getattr(thinker_decision, "light_kind", "") or "")
is_closing_turn = light_kind == "closing" or (
    str(getattr(trigger, "mode", "") or "") == "closing"
    and thinker_action != "wait"
)
```

**问题**:当 thinker 自己判断 `light_kind == "closing"` 时(如"非凡说睡了"),speculative token 任务**根本没启动**(`trigger.mode` 不是 `"closing"`),导致 `closing_token = None`,fallback 到硬编码 `_PASS_TURN_LIGHT_ACK = "嗯，我在。"`。

**生产证据**:
```
06-05 14:44:33.967 | thinker | action=light_reply thought='非凡说睡了，给个晚安收尾'
06-05 14:44:34.303 | closing_light_reply | token='嗯，我在。' speculative=False
```
`speculative=False` 说明 token 生成任务没跑。

**影响**:所有 thinker 自主判断的 closing 场景都会输出错误的告别语。

### C2. Companion 路径完全没有实现

**位置**:`services/llm/client.py` 无 companion 分支

**设计意图**(design doc §2.5):
> "companion 走 plugin_dynamic hint 注入...主 LLM 自然生成短句"

**实际实现**:
- thinker 正确解析 `light_kind=companion`([thinker.py:330-338](../../services/llm/thinker.py#L330))
- 但 client.py **没有任何 companion 处理逻辑**
- `action=light_reply, light_kind=companion` 直接掉进主 LLM 路径,主 LLM 不知道要简短回复

**生产证据**:
```
06-01 14:04:14.745 | thinker | action=light_reply thought='他们在聊雪糕口味，我应一声表示在听'
...
06-01 14:04:20.286 | '巧乐兹好吃！\n巧克力脆皮咬下去咔嚓一声超幸福的~' | len=24
```
thinker 想要简短 ack,实际输出了完整内容回复。

**影响**:P1 companion 功能**名存实亡**,所有陪伴场景都变成完整回复,违背设计初衷。

---

## 三、Medium 问题

### M1. closing_done 只有超时重置,没有新话题重置

**位置**:`services/scheduler.py:651`

**设计意图**(design doc §3):
> "超时/新话题复位"

**实际实现**:只有超时(30 分钟)重置:
```python
if slot.closing_done and (now_wall - slot.last_light_time) >= _CLOSING_RESET_S:
    slot.closing_done = False
```

**问题**:用户说"晚安"→ bot 回复 → 用户 5 分钟后发新消息 → 这是新对话,但 `closing_done=True` 还没过期,可能影响后续判断。

### M2. Scheduler 状态更新在 emit 之前

**位置**:`services/scheduler.py:669-670`

```python
slot.closing_done = True
slot.last_light_time = now_wall
# 然后才 fire _do_chat
```

**问题**:如果 client 的 `on_segment(token)` 抛异常(网络/限频),状态已经写了,用户不会收到重试。状态更新应该在 emit 成功后。

### M3. light_kind 提取没有先检查 action

**位置**:`services/llm/client.py:4284`

```python
light_kind = str(getattr(thinker_decision, "light_kind", "") or "")
```

没有先检查 `thinker_action == "light_reply"`。虽然 thinker 会清理非 light_reply 的 light_kind,但消费侧没有防御性检查。

### M4. D2 测试覆盖不完整

**位置**:`tests/test_scheduler.py`

现有 D2 测试只覆盖 `_gen_closing_token` 的 cancel 传播,没有测试 chat 任务 cancel 后 `closing_done` 状态污染的场景。

---

## 四、Low 问题

### L1. reply-necessity gate 对 light_reply 的豁免是隐式的

**位置**:`services/llm/client.py:4184-4202`

豁免逻辑是 `thinker_action == "reply"` 条件不匹配 `"light_reply"`,而非显式检查。未来重构可能破坏。

### L2. _PASS_TURN_LIGHT_ACK 语义与 closing 场景不符

**位置**:`services/llm/client.py:149`

```python
_PASS_TURN_LIGHT_ACK = "嗯，我在。"
```

这是陪伴型 ack,用在 closing fallback 语义完全错误。即使修复 C1,这个常量的命名和内容也需要重新考虑。

---

## 五、根因分析

这些问题的共同根因是**信号源和消费者的解耦不完整**:

1. **closing 信号有两个来源**:router 设的 `trigger.mode` 和 thinker 判的 `light_kind`。但 speculative 只看前者。
2. **companion 完全没有消费者**:thinker 会产出 `light_kind=companion`,但 client 里没有分支处理。
3. **状态管理没有事务性**:`closing_done` 的写入和 emit 成功不是原子的。

设计文档对这些边界描述得很清楚,但实现时漏掉了。

---

## 六、系统性修复方案

### 方案 A:统一 light_reply 入口(推荐)

**核心思路**:在 thinker 决策返回后、主 LLM 调用前,统一处理所有 `action=light_reply` 的分支,而不是把 closing/companion 分散在不同位置。

**改动**:

1. **新增 `_handle_light_reply()` 方法**,在 thinker 决策后立即调用:
   ```python
   if thinker_action == "light_reply":
       result = await self._handle_light_reply(
           light_kind=thinker_decision.light_kind,
           conversation_text=conversation_text,
           mood_text=mood_text,
           ...
       )
       if result is not None:
           return result  # 短路,不走主 LLM
   ```

2. **`_handle_light_reply()` 内部分发**:
   ```python
   async def _handle_light_reply(self, *, light_kind: str, ...) -> dict | None:
       if light_kind == "closing":
           # 同步调用 _gen_closing_token(非 speculative,但语义正确优先于延迟)
           token = await self._gen_closing_token(...)
           if token:
               await on_segment(token)
               return {"text": token, "light_reply": True}
           # fallback: 静默 or 通用告别
           return {"text": "", "light_reply": True, "fallback": True}
       
       elif light_kind == "companion":
           # P1: 注入 hint 让主 LLM 短回复,或直接生成简短 ack
           # 当前先走主 LLM + hint 注入(设计 §2.5 复用点 A)
           return None  # 继续走主 LLM,但 hint 已注入
       
       return None  # 未知 light_kind,走主 LLM
   ```

3. **删除 speculative closing 逻辑**:当前的 `closing_token_task` 只在 `trigger.mode == "closing"` 时启动,覆盖不全。改为在 `_handle_light_reply()` 里同步调用。延迟略增(~200-500ms),但:
   - closing 场景本身低频(生产 3 次/周)
   - 语义正确优先于延迟优化
   - 后续可优化为"thinker 返回前 200ms 预判 closing 意图并 speculative"

4. **companion hint 注入**:在 `_handle_light_reply()` 里,当 `light_kind == "companion"` 时,向 `plugin_dynamic` 注入:
   ```
   "【弱回复提示】对方不需要有信息量的回复,只需简短应一声(嗯/哈哈/是吧)表示在听。不要展开话题。"
   ```
   然后 return None 让主 LLM 继续,但主 LLM 会看到 hint。

5. **修复 `_PASS_TURN_LIGHT_ACK`**:
   - 重命名为 `_LIGHT_REPLY_FALLBACK_ACK`
   - 改为通用的 `"嗯~"` 或分场景:closing 用 `"好~"`,companion 用 `"嗯~"`

### 方案 B:修复 speculative 条件(打补丁)

只修 C1:在 speculative 启动条件里加上对消息内容的 closing 意图预判。

**缺点**:
- 没解决 C2(companion 仍然没实现)
- speculative 逻辑更复杂,边界更难测
- 不治本

**不推荐。**

### 方案 C:删除弱回复机制

如果弱回复的价值不高,可以删除整个机制,让 thinker 只返回 `reply/wait`,不返回 `light_reply`。

**缺点**:
- 违背设计初衷
- 丧失"陪伴感"和"对称告别"两个用户体验改进点

**不推荐,除非明确决定弃用该功能。**

---

## 七、方案 A 详细改动清单

| # | 改动 | 文件 | 行数 | 风险 |
|---|------|------|------|------|
| A1 | 新增 `_handle_light_reply()` 方法 | client.py | ~50 | 低 |
| A2 | 在 thinker 决策后调用 `_handle_light_reply()` | client.py:~4150 | ~10 | 中(主路径改动) |
| A3 | 删除 speculative closing_token_task 逻辑 | client.py:4107-4148 | -40 | 低(删代码) |
| A4 | 删除现有 closing 短路分支 | client.py:4280-4320 | -40 | 低(移入新方法) |
| A5 | companion hint 注入(设计§2.5 复用点 A) | client.py | ~15 | 低 |
| A6 | 重命名/修复 `_PASS_TURN_LIGHT_ACK` | client.py:149 | ~5 | 低 |
| A7 | scheduler 状态更新移到 callback | scheduler.py:669 | ~20 | 中(状态管理) |
| A8 | 新增 D2 测试:cancel 后状态污染 | tests/ | ~30 | 低 |
| A9 | 更新现有测试适配新结构 | tests/ | ~50 | 中 |

**总计**:~100 行新增,~80 行删除,净+20 行左右。

---

## 八、验收指标

1. **closing 场景**:thinker 返回 `light_kind=closing` 时,输出的 token 是语义正确的告别语(晚安哦/好的呀/拜拜~),不是 `"嗯，我在。"`
2. **companion 场景**:thinker 返回 `light_kind=companion` 时,输出简短 ack(嗯嗯/哈哈/是吧),不是完整内容回复
3. **fallback 安全**:token 生成失败时,静默或输出通用 ack,不输出语义错误的内容
4. **D2 覆盖**:cancel 场景不污染 `closing_done` 状态
5. **回归**:全量 pytest 通过,现有 closing 测试适配后通过

---

## 九、执行建议

1. 先修 C1(closing token 生成),因为这是用户已观察到的 bug
2. 同时修 C2(companion 实现),因为这是设计承诺但未交付的功能
3. M1/M2/M3/M4 可以在后续迭代中修复
4. L1/L2 作为代码卫生问题,随手修
