# Omubot 群聊并发与队列架构调研方案

本文回答一个具体问题：Omubot 能不能通过“多个 LLM 并发回答”解决回答问题时卡住后面对话的问题。

结论先行：

- 可以做 **跨群 LLM 并发**，应该做。
- 不建议做 **同一群多条 LLM 回复同时生成并同时写 timeline**。
- Omubot 更适合采用 **Orleans 风格的每群 actor 队列 + Rasa 风格的会话串行边界 + AutoGen 风格的消息 envelope + 可选 LangGraph 风格 checkpoint**。
- 第一阶段不引入外部 runtime；可以先在 `services/scheduler.py` 内部替换当前 `running_task/pending_at` 布尔状态机，稳定后再抽到 `services/group_runtime.py`。

## 1. 调研范围与本地源码快照

本次不只看项目介绍，已将代表项目浅克隆到本地临时目录：

| 项目 | 本地路径 | 快照 |
| --- | --- | --- |
| Rasa | `/private/tmp/omubot-arch-research/rasa` | `60a3cff` |
| Microsoft AutoGen | `/private/tmp/omubot-arch-research/autogen` | `027ecf0` |
| LangGraph | `/private/tmp/omubot-arch-research/langgraph` | `2e5025e` |
| Microsoft Orleans | `/private/tmp/omubot-arch-research/orleans` | `5989958` |

重点阅读的源码：

- Rasa：`rasa/core/lock.py`、`rasa/core/lock_store.py`、`rasa/core/agent.py`、`rasa/core/processor.py`
- AutoGen：`python/packages/autogen-core/src/autogen_core/_single_threaded_agent_runtime.py`、`_runtime_impl_helpers.py`、`_routed_agent.py`
- LangGraph：`libs/langgraph/langgraph/pregel/main.py`、`pregel/_loop.py`、`pregel/_runner.py`、`pregel/_algo.py`、`libs/checkpoint/.../base/__init__.py`
- Orleans：`src/Orleans.Runtime/Scheduler/WorkItemGroup.cs`、`ActivationTaskScheduler.cs`、`Catalog/ActivationData.cs`、`Orleans.Core.Abstractions/Concurrency/GrainAttributeConcurrency.cs`

## 2. Omubot 当前卡顿根因

当前群聊路径：

```text
router.py
  -> timeline.add(user)
  -> scheduler.notify()
  -> scheduler._fire()
  -> scheduler._do_chat()
  -> LLMClient.chat()
  -> tool loop / segment emit / on_segment send
  -> timeline.add(assistant, flush_pending_count=...)
```

当前关键状态在 `services/scheduler.py`：

- `_GroupSlot.running_task`：每群最多一个 `_do_chat()`。
- `_GroupSlot.pending_at: bool`：运行中收到 `@bot/direct_followup/video_always` 只记一个布尔值。
- `_GroupSlot.trigger`：只保留最近一个 trigger。
- 普通消息忙时会被 `deactivate_latest_pending(..., "scheduler_busy")` 后跳过。

当前已经有两个重要修正：

- `TimelineMessage.pending_state` 把 skipped 内容从 active pending 中剥离。
- `LLMClient.chat()` 记录 `reply_pending_cutoff = timeline.pending_len(group_id)`，写 assistant 时只 flush 本轮 cutoff 前的 pending。

但仍然存在结构性阻塞：

1. **生成和发送绑定在同一个 `_do_chat()` 里**  
   `LLMClient._emit_reply_segments()` 会等待 `on_segment()`；`on_segment()` 内部又会执行 humanizer delay 和 OneBot 发送。也就是说，LLM 已经生成完第一段时，后续段落发送、人类化延迟、发送重试都会继续占用该群的 `running_task`。

2. **`pending_at` 是布尔，不是队列**  
   运行中多个明确寻址只会合并为“一轮待处理”，具体优先级、目标消息、触发原因容易被后来的 trigger 覆盖。

3. **同群并行 LLM 会破坏 timeline 语义**  
   两个并行 LLM 都可能基于不同 pending 快照生成回复，然后同时写 assistant turn。即使 `flush_pending_count` 能避免 flush 后续 pending，也无法保证：
   - 回复顺序等于群里可见发送顺序；
   - B 轮没有读到“已写入但还没发完”的 A 轮 assistant；
   - 工具副作用不会交叉；
   - `on_post_reply`、记忆写入、usage 统计不会重复或错序。

## 3. 外部项目算法拆解

### 3.1 Rasa：TicketLock 会话串行算法

Rasa 对每个 conversation ID 采用 ticket lock。

核心数据结构：

```python
Ticket(number, expires)
TicketLock(conversation_id, deque[Ticket])
```

算法流程：

1. `issue_ticket(conversation_id)` 获取当前 lock 或创建新 lock。
2. `TicketLock.issue_ticket()` 先移除过期 ticket，再用 `last_issued + 1` 分配新 ticket number。
3. `_acquire_lock()` 循环读取 lock。
4. 只有 `ticket == lock.now_serving` 时进入临界区。
5. 请求结束后 `cleanup()` 删除当前 ticket；如果没人等待则删除整个 lock。

源码证据：

- `TicketLock.issue_ticket()` 使用递增 number 和 deque：`rasa/core/lock.py`
- `LockStore.lock()` 是 async context manager：`rasa/core/lock_store.py`
- `Agent.handle_message()`、`MessageProcessor.handle_message()` 在处理用户消息前进入 `lock_store.lock(sender_id)`。

优点：

- 非常清晰地保护“同一会话 tracker 状态”的一致性。
- 可切换 RedisLockStore，支持多进程/多 worker。
- FIFO 公平性强，后来的消息不会插队。

缺点：

- 等锁期间只会等待，不会合并消息或降级低优先级事件。
- 一条消息处理很慢时，同 conversation 后续消息全部排队。
- 对 Omubot 这种群聊机器人来说，单纯 ticket lock 只能保证一致性，不能解决“发送慢导致后续对话卡住”的体验问题。

对 Omubot 的启发：

- **同群 timeline 写入和 LLM 生成应保持单写者语义**。
- 不应在同一群里让多个 LLM 同时写 assistant turn。
- 但 Omubot 需要比 Rasa 多一个“事件合并/降噪层”，否则高频群会把队列拖长。

### 3.2 AutoGen：全局消息队列 + envelope + handler 并发

AutoGen Core 的 `SingleThreadedAgentRuntime` 名字容易误导：它有一个单队列入口，但取出 envelope 后会创建 background task 处理。

核心数据结构：

```python
Queue[PublishMessageEnvelope | SendMessageEnvelope | ResponseMessageEnvelope]
_background_tasks: set[Task]
SubscriptionManager(topic -> recipients)
```

算法流程：

1. `send_message()` 创建 future，将 `SendMessageEnvelope` 放入 `_message_queue`，等待 future。
2. `publish_message()` 将 `PublishMessageEnvelope` 放入 `_message_queue`，不等待具体响应。
3. `_process_next()` 从队列取一个 envelope。
4. 对 direct send：创建 `_process_send()` background task。
5. `_process_send()` 调用目标 agent handler，完成后再把 `ResponseMessageEnvelope` 放回队列。
6. 对 publish：解析 topic subscriptions，`asyncio.gather()` 并发调用所有订阅 agent。
7. intervention handler 可 drop/修改消息。

源码证据：

- `_message_queue`：`_single_threaded_agent_runtime.py`
- `send_message()` 入队并关联 future：同文件
- `_process_next()` 取 envelope 后 `asyncio.create_task(...)`：同文件
- `_process_publish()` 对多个 recipients gather：同文件
- `SubscriptionManager` 缓存 topic 到 recipients：`_runtime_impl_helpers.py`

优点：

- envelope 化让消息生命周期清晰：send、publish、response。
- direct RPC 和 pub/sub event 区分明确。
- intervention/drop 机制适合做策略门禁。
- background task 允许不同 agent 处理并发。

缺点：

- 单 runtime 不是高吞吐生产 runtime；源码注释也说明不适合 high-throughput。
- 单 agent 自身是否会被并发调用取决于 handler/runtime 设计；对有状态 agent 不安全。
- 对 Omubot 来说，如果直接照搬“取出后都 create_task”，同群状态会出现并发写 timeline 的风险。

对 Omubot 的启发：

- 采用 **event envelope** 表达消息触发原因、优先级、快照边界、取消策略。
- 可以有全局入口队列，但必须在 **group actor** 内对同群事件串行化。
- command/plugin 快路径可以是 independent event，不应被 LLM 生成队列阻塞。

### 3.3 LangGraph：Pregel/BSP 超步 + checkpoint

LangGraph 的 Pregel runtime 不是普通聊天队列，而是图计算式 agent workflow。

核心概念：

```text
channels: 保存状态或消息
actors/nodes: 读取 channel，写 channel
superstep:
  Plan -> Execution -> Update
checkpoint:
  channel_values
  channel_versions
  versions_seen
  pending_writes
```

算法流程：

1. 根据上一步 `updated_channels` 和 `trigger_to_nodes` 选择下一批 tasks。
2. 同一 superstep 内，多个 node 并发执行。
3. 执行期间写入只进入 task writes，对其他 node 不可见。
4. step 结束后 `apply_writes()` 统一按 channel reducer 更新状态。
5. checkpoint 记录 channel version、pending writes、interrupt/resume 信息。
6. interrupt 依赖 checkpointer，恢复时可从 checkpoint 重新执行 node。

源码证据：

- `Pregel` 文档注释明确 Plan/Execution/Update 三阶段：`pregel/main.py`
- `PregelRunner.tick()` 并发提交同一步 tasks，失败时停止其他任务：`pregel/_runner.py`
- `apply_writes()` 收集 task writes，再更新 channel version：`pregel/_algo.py`
- `BaseCheckpointSaver` 以 `thread_id` 为主键持久化 checkpoint：`checkpoint/base/__init__.py`

优点：

- 并发安全：同一 superstep 内写入隔离，到 Update 阶段才统一可见。
- checkpoint/resume/interrupt 非常强，适合复杂长流程。
- reducer/channel version 让状态变化可追踪。

缺点：

- 对 Omubot 的普通群聊回复热路径过重。
- 引入后需要把 timeline、trigger、tool loop、reply sending 都建模成 graph/channel，改造面大。
- BSP 的“本步写入下步可见”对群聊自然语言即时回复不是总合适；用户希望最新消息尽快影响下一轮。

对 Omubot 的启发：

- **不要直接引入 LangGraph 到主聊天路径**。
- 可以借鉴 checkpoint 结构：事件队列持久化时记录 `event_id`、`group_id`、`status`、`pending_cutoff`、`result`。
- 将来如果做“长任务/研究型插件/梦境 agent”，LangGraph 更适合作为插件级 workflow runtime，而不是群聊 scheduler 核心。

### 3.4 Orleans：Virtual Actor 单 activation 队列

Orleans 和 Omubot 的问题最像：大量独立实体，每个实体内部状态需要串行，实体之间可以并行。

核心数据结构：

```text
Activation == 一个 grain 实例
WorkItemGroup == activation 的任务队列
ActivationTaskScheduler == 单 activation task scheduler
MayInvokeRequest == interleaving 策略
```

算法流程：

1. 每个 activation 有自己的 `Queue<Task>`。
2. `EnqueueTask()` 加锁入队；如果 activation 原本 Waiting，就标为 Runnable 并调度执行。
3. `Execute()` 在单线程环境中从队列取任务运行。
4. 一次执行可 drain 多个 task，但受 `ActivationSchedulingQuantum` 限制，避免一个 activation 长期霸占 worker。
5. 如果队列未空，重新 schedule；如果空，回到 Waiting。
6. 默认同 activation 不并发；但 `ReadOnly`、`AlwaysInterleave`、`Reentrant`、`MayInterleave` 可以允许受控插队/并发。

源码证据：

- `ActivationTaskScheduler` 注释：single-concurrency, in-order task scheduler。
- `WorkItemGroup.EnqueueTask()` 根据状态从 Waiting -> Runnable。
- `WorkItemGroup.Execute()` 注释说明同一 activation 同时只有一个线程执行。
- `ActivationData.MayInvokeRequest()` 判断 `AlwaysInterleave`、ReadOnly、reentrant、MayInterleave。
- `GrainAttributeConcurrency.cs` 定义 Reentrant、AlwaysInterleave、MayInterleave。

优点：

- 非常适合“每群一个有状态实体”。
- 实体之间天然并行；实体内部默认串行。
- 有明确的 queue length、long turn warning、quantum 公平性。
- reentrancy 是受控 opt-in，而不是默认乱并发。

缺点：

- Orleans 是分布式 actor runtime，完整引入不现实。
- Reentrant 语义复杂，误用会让状态一致性问题变隐蔽。
- Omubot 不需要迁移、placement、grain lifecycle 这些重型能力。

对 Omubot 的启发：

- 采用 **轻量 per-group actor**，不要引入完整 actor 框架。
- 默认同群 LLM generation 串行。
- 明确定义哪些事件可以 interleave：命令、状态查询、发送队列、非 timeline 写入型插件。
- 加 queue length/long turn 日志，定位真实阻塞来源。

## 4. 算法优劣对比

| 模型 | 一致性 | 吞吐 | 延迟 | 实现复杂度 | 适合 Omubot 程度 |
| --- | --- | --- | --- | --- | --- |
| Rasa TicketLock | 强，同会话 FIFO | 跨会话并发 | 同会话慢消息会阻塞 | 中 | 中高：适合作为串行边界 |
| AutoGen Runtime | 取决于 agent 状态设计 | 高，可后台并发 | 事件可快速入队 | 中高 | 中：适合作为 envelope/queue 思路 |
| LangGraph Pregel | 强，superstep 隔离 | 图内并发强 | 有 step barrier | 高 | 低到中：主路径过重，长任务适合 |
| Orleans Actor | 强，实体内单并发 | 跨实体并发强 | 可用 queue/interleave 控制 | 中 | 高：最适合每群一个 actor |

Omubot 应该采用的组合：

```text
Rasa:  同群状态串行边界
AutoGen: event envelope / priority / drop policy
Orleans: per-group actor queue / long turn metrics / controlled interleave
LangGraph: 可选 durable checkpoint，不进第一阶段热路径
```

### 4.1 关键算法维度

| 维度 | Rasa | AutoGen | LangGraph | Orleans | Omubot 推荐 |
| --- | --- | --- | --- | --- | --- |
| 入队模型 | 请求先拿 ticket | envelope 入 runtime queue | channel update 触发 task | activation queue | router 生成 GroupEvent |
| 顺序保证 | conversation FIFO | queue 取出 FIFO，但处理可并发 | superstep barrier 保证阶段顺序 | activation 内 FIFO | group 内 FIFO + priority |
| 并发边界 | conversation lock | agent/runtime 任务 | 同 superstep 多 node | activation 单并发 | group generation 单并发 |
| 可见性 | 临界区内直接更新 tracker | handler 可立即产生 response | writes 下个 step 才可见 | task 完成后状态可见 | assistant 首段可见后释放下一轮 |
| 失败处理 | finally cleanup ticket | future exception / background exception | commit error writes，必要时 panic | long turn log，异常隔离到 task loop | event status + no_visible_reply deactivate |
| 背压策略 | 等待锁 | queue size 可查，但无强策略 | step timeout / checkpoint | pending soft limit warning | max queue + 低优先级丢弃 |
| 适合直接引入 | 否 | 否 | 否 | 否 | 借鉴算法，不引框架 |

### 4.2 为什么不是“多个线程同时答同一群”

同群多 LLM 并行只有在满足下列条件时才安全：

1. 每个 reply job 拥有独立 pending slice。
2. 每个 assistant turn 有 parent turn / dependency。
3. 写 timeline 时能 rebase：如果 parent 已变化，需要重建 prompt 或放弃。
4. 发送层能保证可见顺序，不让后完成的旧问题插到新问题后面。
5. 工具副作用可幂等或可回滚。

这些条件接近一个迷你 LangGraph/Pregel runtime。Omubot 当前已经有 `flush_pending_count` 作为快照边界，但还没有 branch timeline、turn dependency、tool side-effect isolation。因此同群多 LLM 并行不是小改，是一次状态模型升级。

## 5. Omubot 目标架构

### 5.1 新消息流

```text
router.py
  -> command fast path
  -> plugin on_message
  -> timeline.add(user)
  -> group_runtime.submit(GroupEvent)

GroupActor(group_id)
  -> classify/coalesce event
  -> maybe deactivate background pending
  -> if should_reply:
       GenerationJob
         -> acquire global_llm_semaphore
         -> LLMClient.chat(send_mode="return_segments")
         -> timeline.add(assistant, flush_pending_count=cutoff)
         -> send_queue.enqueue(ReplySegmentBatch)
  -> continue processing queued events

GroupSendQueue(group_id)
  -> sequential send first segment / later segments
  -> humanizer delay
  -> retry OneBot send
```

关键变化：

- **LLM generation 与消息发送拆开**。
- **每群 generation 仍然串行**。
- **跨群 generation 通过全局 semaphore 并发**。
- **同群发送仍然串行**，保证群里可见顺序稳定。
- `@bot/direct_followup/video_always` 不再只用 `pending_at=True` 表示，而是保留完整 event。

### 5.2 数据结构

最终形态建议新增 `services/group_runtime.py`，不要把复杂度长期堆进 `scheduler.py`。但 Phase 1 可以先在 `scheduler.py` 内完成队列化，降低改动面。

```python
@dataclass(slots=True)
class GroupEvent:
    event_id: str
    group_id: str
    user_id: str
    kind: Literal["timeline_message", "reply_request", "maintenance"]
    priority: int
    trigger: TriggerContext | None
    message_id: int | None
    created_at: float
    coalesce_key: str
```

职责边界：

- `GroupEvent` 是 runtime envelope，描述队列优先级、合并键、事件生命周期。
- `TriggerContext` 是回复触发语义，描述为什么要回复、回复目标是谁、插件额外数据是什么。
- `kind="reply_request"` 时 `trigger` 必须存在；`kind="timeline_message"` 时 `trigger` 可以为空。
- 不新增 `GroupEvent.mode`，避免和 `TriggerContext.mode` 双重分类。
- 不建议把 queue priority 合入 `TriggerContext`：`TriggerContext` 位于 `kernel/types.py`，是插件/路由共享契约；queue priority 属于 scheduler runtime 策略。

优先级建议：

| 事件 | priority |
| --- | --- |
| debug/admin command | 0 |
| `@bot` | 10 |
| `video_always` | 20 |
| `directed_followup` | 30 |
| 高兴趣视频自主触发 | 50 |
| 普通概率触发 | 100 |
| 普通背景消息 | 200 |

```python
@dataclass(slots=True)
class GroupActorState:
    group_id: str
    queue: asyncio.PriorityQueue[QueuedGroupEvent]
    queue_lock: asyncio.Lock
    queued_by_key: dict[str, str]
    cancelled_event_ids: set[str]
    generation_task: asyncio.Task[None] | None
    send_task: asyncio.Task[None] | None
    send_queue: asyncio.Queue[ReplySegmentBatch]
    last_generation_started_at: float
    last_visible_reply_at: float
    consecutive_skip: int
    last_user_id: str
```

```python
@dataclass(slots=True)
class ReplySegmentBatch:
    group_id: str
    assistant_turn_id: str
    target_message_id: int | None
    segments: list[str]
    first_segment_humanize: str
    later_segment_humanize: str
    created_at: float
```

### 5.3 GroupActor 算法

伪代码：

```python
async def submit(event):
    actor = actors.setdefault(event.group_id, GroupActor(...))
    await actor.enqueue(event)

class GroupActor:
    async def enqueue(event):
        async with self.queue_lock:
            event = self._coalesce_or_drop_locked(event)
            if event is None:
                return
            await self.queue.put((event.priority, event.created_at, event.event_id, event))
            self.queued_by_key[event.coalesce_key] = event.event_id
        self._ensure_worker()

    async def _run():
        while True:
            event = await self.queue.get()
            try:
                await self._handle(event)
            finally:
                self.queue.task_done()
            if self.queue.empty():
                self._maybe_idle_shutdown()
```

`_handle(event)`：

1. 如果 muted，退出。
2. 如果 event 是普通背景消息：
   - running generation 时：只标记 latest pending 为 background；
   - 不 running 时：执行概率/间隔判断，决定是否生成。
3. 如果 event 是强触发：
   - 不覆盖旧 trigger；
   - 以完整 event 入队；
   - 生成时把 trigger marker 写进 pending。
4. 生成前记录：
   - `pending_cutoff = timeline.pending_len(group_id)`
   - `active_pending_count = len(timeline.get_active_pending(group_id))`
5. 若 active pending 为空，跳过。
6. 调用 `LLMClient.chat(..., emit_mode="collect")` 得到 segments。
7. 写 assistant turn，只 flush `pending_cutoff`。
8. 将 segments 投递给 `send_queue`。

合并实现注意：

- 不遍历或原地删除 `asyncio.PriorityQueue` 内部队列；这不是稳定 API，也不是原子操作。
- 使用 `queued_by_key` 旁路索引判断是否已有可合并事件。
- 新事件取代旧事件时，把旧 `event_id` 加入 `cancelled_event_ids`；worker 从 queue 取出旧事件后直接 skip。
- `coalesce/drop + queue.put + index update` 放在 `queue_lock` 内完成，避免并发 enqueue 产生双写。
- 如果 queue 设置 `maxsize` 导致 `put()` 可能 await，应先在锁内做容量决策，再使用 `put_nowait()`；避免持锁等待。

### 5.4 生成与发送拆分

当前 `LLMClient.chat()` 会在生成中调用 `_emit_reply_segments(on_segment=...)`。建议扩展而不是重写：

```python
async def chat(..., emit_mode: Literal["send", "collect"] = "send") -> ChatResult | str | None:
    ...
```

新增结果：

```python
@dataclass(slots=True)
class ChatResult:
    text: str
    segments: list[str]
    segmentation: ReplySegmentationResult
    pending_cutoff: int | None
    usage: UsageSnapshot
    visible: bool
```

第一阶段可以更小：

- 保留现有 `chat()` 返回 `str | None`。
- 新增 `chat_collect_segments()` 或参数 `on_segment=None`，让 `_emit_reply_segments()` 不发送，只返回 `full_reply` 和 segmentation。
- scheduler/group actor 自己把 `full_reply` 分段后入 send_queue。

注意事项：

- timeline 写 assistant 的时机建议在 **生成完成后、入 send_queue 前**。
- 最保守策略：同群下一轮 generation 等 `ReplySegmentBatch` 完整发送后再开始；一致性最好，但改善有限。
- 折中策略：首段发送成功后释放 generation slot，后续段落继续在 send_queue 慢慢发；这能把阻塞从“整条回复发送完”缩短到“首段可见”。
- 折中策略必须满足一个前提：所有可见副作用都进入同一个 `send_queue`。例如 `send_sticker` 不能在下一轮 generation 中直接发出并插到上一轮第 2 段文字之前。
- sticker/tool 可见输出与 `element_detector` 插件可见回复已纳入 `send_queue`；默认仍使用 **完整发送后释放 generation slot**，但发送层允许短可见回复在段间边界受控让位，`first_segment_release` 作为 generation 提前释放实验开关。
- 开启 `first_segment_release` 时，`_build_group_messages()` 不读取尚未完整可见的 assistant 全文；当前通过 `visible_state` 状态占位屏蔽未 complete 正文。

### 5.5 全局 LLM 并发池

新增配置：

```toml
[scheduler.concurrency]
global_llm_limit = 3
per_group_llm_limit = 1
max_group_queue = 8
max_low_priority_queue = 3
first_segment_release = false
drop_stale_low_priority_after_s = 45
```

规则：

- `global_llm_limit` 默认 2 或 3。高并发模型/供应商稳定后再调到 4。
- `per_group_llm_limit` 固定 1，不建议开放配置，除非未来做严格 branch timeline。
- command dispatcher 不占 LLM semaphore，除非命令内部调用 LLM。
- Food 等命令插件保持 router fast path，不能被群聊 LLM 队列阻塞。

### 5.6 事件合并与降噪策略

普通消息：

- actor 忙于生成时，普通消息默认进入 timeline，但标为 background，除非满足以下条件：
  - 距离上一条 active pending 很近；
  - 当前无高优先级事件等待；
  - 群配置允许 backlog summarization。

强触发：

- `@bot`：永不合并，除非同一用户同一 message_id 重复。
- `directed_followup`：可合并窗口 2 秒内同一用户多条短句，保留最后一条，但不吞原 message_id；需要明确测试。
- `video_always`：同一视频 URL 去重。

队列满时：

| 事件 | 队列满处理 |
| --- | --- |
| `@bot` | 保留，必要时丢弃低优先级事件 |
| `directed_followup` | 保留最近 N 条，过期转 background |
| `video_always` | 同 URL 去重 |
| 普通概率触发 | 丢弃/转 background |
| 普通背景消息 | 不入队，只留 timeline |

### 5.7 与现有 active/background pending 的关系

现有 `pending_state` 继续保留。

新增约束：

- 只有 GroupActor 决定 active pending 是否进入下一轮 LLM。
- skip/busy/queue overflow 不删除 timeline，只调用：
  - `deactivate_latest_pending()`
  - `deactivate_pending()`
  - `deactivate_pending_except_latest_active()`
- `LLMClient._build_group_messages()` 继续只读 `get_active_pending()`。
- `flush_pending_count` 继续作为本轮回复归档边界。

### 5.8 受控 interleave

借鉴 Orleans，但只开放有限 interleave：

| 操作 | 是否可与 generation 并行 | 原因 |
| --- | --- | --- |
| command dispatch | 是 | router fast path，不写同一 assistant turn |
| timeline.add(user) | 是，但只 append pending | append-only，可由锁保护 |
| active/background 标记 | 是，但必须由 actor 串行执行 | 防止状态覆盖 |
| LLM generation | 同群否，跨群是 | 保护 timeline 和 reply 顺序 |
| send_queue 发送后续段 | 可与下一轮 generation 部分重叠 | 首段可见后用户体验更好 |
| memory card 写入 | 谨慎，建议跟 assistant turn 完成后异步 | 避免基于未发送内容触发副作用 |
| sticker/tool 可见输出 | 必须串进 send_queue 后才可 interleave | 防止贴纸插到上一轮文字中间 |

### 5.9 状态机

建议把每群运行状态显式化，避免继续依赖多个布尔互相暗示：

```text
IDLE
  on event requiring reply -> GENERATING
  on background event -> IDLE

GENERATING
  on low priority message -> mark background, stay GENERATING
  on high priority trigger -> enqueue trigger, stay GENERATING
  on llm empty -> deactivate active pending, maybe GENERATING next queued event else IDLE
  on llm reply -> WAIT_FIRST_SEGMENT

WAIT_FIRST_SEGMENT
  on first segment sent and first_segment_release=true -> SENDING_TAIL + maybe GENERATING next queued event
  on first segment sent and first_segment_release=false -> SENDING_TAIL only
  on first segment failed permanently -> mark send_failed, maybe GENERATING next queued event

SENDING_TAIL
  send remaining segments sequentially
  generation may run concurrently only after first segment visible and visible side-effects are queue-serialized
```

重要约束：

- `GENERATING` 同群最多一个。
- `SENDING_TAIL` 同群最多一个 send worker，但可以和下一轮 `GENERATING` 局部重叠。
- 如果 `WAIT_FIRST_SEGMENT` 超时，不能无限阻塞 generation；应记录 warning 并按失败策略继续。

## 6. 分阶段实施计划

### Phase 0：观测补强

目标：先看清楚是 LLM 慢、工具慢、发送慢，还是 queue 慢。

改动：

- scheduler 日志增加：
  - `queue_len`
  - `event_priority`
  - `generation_wait_s`
  - `generation_elapsed_s`
  - `first_segment_elapsed_s`
  - `send_total_elapsed_s`
- LLM usage slow alert 增加 group_id、trigger mode。

风险：低。

测试：

- scheduler 单测断言 busy 时日志/metrics 可读。

### Phase 1：每群事件队列替代 `pending_at`

目标：修复布尔状态机吞 trigger/覆盖 trigger 的问题。

改动：

- 新增 `GroupEvent`。
- `_GroupSlot.pending_at` 替换为优先级队列，不使用裸 `deque` 表达所有事件。
- busy 时 `@bot/direct_followup/video_always` 入队，不覆盖 `slot.trigger`。
- `_do_chat()` 完成后 drain 下一条高优先级 event。
- 一次 `_do_chat()` 只消费一个 reply event；剩余事件留在队列中，避免一轮回复吞掉多个目标。
- `_fire()` 必须检查 `running_task`，禁止在同群已有运行任务时重入启动第二个 generation。

保持不变：

- LLM 和发送仍然在 `_do_chat()` 内。
- 不拆 `LLMClient.chat()`。

风险：中低。

收益：

- 明确寻址不丢。
- 后续可平滑演进到 actor。

### Phase 2：全局 LLM semaphore

目标：允许跨群并发，限制总供应商压力。

改动：

- `GroupChatScheduler` 初始化 `asyncio.Semaphore(global_llm_limit)`。
- `_do_chat()` 调用 LLM 前 `async with semaphore`。
- 命令插件不默认占用该 semaphore。

风险：低到中。

注意：

- 如果供应商 rate limit 严格，`global_llm_limit=2` 起步。
- RateLimitError 重试时不要持有 semaphore sleep；应释放后延迟重排。

### Phase 2.5：Send Queue 准备层

目标：在 Phase 3 前统一群内可见输出顺序，避免首段释放后出现贴纸/图片插队。

改动：

- 盘点所有群内可见输出入口：`send_group_msg`、`bot.send`、`send_sticker`、图片/语音/CQ 输出。
- 定义 `SendItem` 或 `ReplySegmentBatch` 作为统一发送任务。
- 新增每群 `GroupSendQueue`，同群可见输出串行发送。
- LLM 对话期间的 `send_sticker` 不再直接发群，而是 enqueue sticker 类型 `SendItem`。
- LLM 对话期间的 `send_group_msg` 同样走 `GroupSendQueue`。
- 命令即时反馈可以保留 fast path，但必须列入例外清单，不能混入 LLM 回复流。
- 设计 assistant `visible_state=pending|first_segment_sent|complete|failed`。
- compact 对 `visible_state != complete` 的 assistant turn 采取跳过、只取已发送部分或显式标记策略。

当前落地状态（2026-05-09）：

- `GroupSendQueue` 最小版已覆盖 scheduler 文字、`send_sticker`、`send_group_msg`。
- assistant turn 已记录 `visible_state` / `visible_updated_at`；scheduler 完成最后一段发送后标记 `complete`。
- prompt 构建会剥离 timeline 元数据；未 complete assistant 仅显示状态占位，不暴露完整正文。
- group compact 与 compact 熔断器会把 split/drop 回退到未 complete assistant 及其对应 user turn 之前。

验收：

- 多段文字 + 贴纸按入队顺序稳定发送。
- 下一轮 LLM 即使提前开始，也不能绕过 send queue 产生群内可见输出。
- 同群两个 ReplySegmentBatch 顺序稳定。
- 发送失败重试不让后续同群输出插队。
- compact 对未完整可见 assistant 有测试覆盖。

### Phase 3：生成/发送拆分

目标：LLM 生成不被长段落发送、人类化延迟、OneBot retry 长时间占用。

改动：

- `LLMClient.chat()` 增加 collect segments 模式。
- `_do_chat()` 生成完成后得到 `ReplySegmentBatch`。
- 新增每群 `send_queue` 串行发送。
- 首段发送成功后释放 generation slot，后续段发送继续排队。

当前落地状态（2026-05-09）：

- 已实现保守 collect 模式：`LLMClient.chat(..., collect_segments=True)` 返回 `CollectedReply(segments, full_reply)`。
- scheduler 默认发送完整 collected batch，并在发送完成后标记 assistant `visible_state=complete`。
- `first_segment_release=true` 时，scheduler 在首段发送成功后释放同群 generation；尾段继续由 send queue 后台发送。
- assistant turn 已具备稳定 `turn_id`，尾段后台完成/失败时按 turn id 精确回写 `complete/failed`，避免误标下一轮回复。
- `GroupSendQueue` 已支持 `ReplySegmentBatch`：同群批次由单群 send worker 串行发送；普通回复批次允许已入队短可见回复在段间边界让位，同时暴露首段完成与批次完成 future。
- 批次发送失败已接入 `visible_state=failed`：首段或尾段失败都会让批次 `done` 失败，scheduler 捕获后标记最新 assistant 为 failed。
- 批次发送观测已接入：scheduler 记录 `reply_batch_queue_wait_s`、`first_segment_elapsed_s`、`tail_send_elapsed_s`、`total_send_elapsed_s`。
- 同群 `running_task` 仍覆盖“生成 + 完整发送”，因此不会发生下一轮同群 generation 抢跑。
- 全局 LLM semaphore 只覆盖 generation；跨群不会因为某一群发送延迟长期占住 LLM 槽。
- `first_segment_release` 默认关闭；配置为 true 时启用实验性首段释放。
- `element_detector` 预设/LLM 生成回复已通过 `scheduler.enqueue_group_text()` 接入同群 send queue，可在上一轮 `ReplySegmentBatch` 段间边界受控让位。

风险：中。

主要风险：

- assistant turn 已写入，但后续段还没发完。
- 下一轮回复可能引用用户尚未看到的后半段。
- 下一轮工具调用或贴纸发送可能插到上一轮第 2、3 段之前。
- compact 可能在上一轮 assistant 尚未完全可见时触发，把未完整发送内容压进 summary。

缓解：

- 默认配置仍使用完整发送后释放 generation slot；`first_segment_release=true` 时才启用实验性首段释放。
- `send_sticker`、`send_group_msg` 等 LLM 可见副作用和 `element_detector` 可见回复已串入同群 send_queue。
- compact 时跳过 `visible_state != complete` 的 assistant turn，当前实现会把 split 回退到该 assistant 及其对应 user turn 之前。
- prompt 中可加入“上一轮回复可能仍在发送，不要引用未发送后半段”，但这只能作为辅助弱约束。
- 更强做法：timeline assistant turn 标记 `visible_state=first_segment_sent|complete`，构建 prompt 时只把 complete 或 first segment 放入上下文。当前最小实现先用状态占位屏蔽未完整正文，后续可扩展为首段可见内容。

### Phase 4：轻量持久化队列

目标：重启不丢高优先级 `@bot` 事件。

改动：

- SQLite 表 `group_events`：
  - `event_id`
  - `group_id`
  - `priority`
  - `trigger_json`
  - `message_id`
  - `status`
  - `created_at`
  - `updated_at`
- 只持久化高优先级事件。
- 普通背景消息不持久化。

风险：中。

是否需要：看 Phase 1-3 后真实体验再决定。

## 7. 不推荐方案

### 7.1 同群多 LLM 并行

表面收益：多个问题同时生成，似乎更快。

实际风险：

- A/B 两轮同时读取 active pending，可能都认为自己应该回答同一批消息。
- 写 assistant turn 顺序取决于完成速度，不等于消息触发顺序。
- 发送顺序可能 B 在 A 前面，群里看起来答非所问。
- 工具调用和记忆写入产生副作用竞争。
- 需要 branch timeline、merge policy、reply dependency graph，复杂度接近 LangGraph，但收益不稳定。

除非未来引入：

- 每条 trigger 独立 pending slice；
- assistant turn dependency；
- per-message reply target；
- merge/rebase 策略；
- 可见发送状态。

否则不建议。

### 7.2 直接引入 AutoGen/LangGraph/Orleans

不建议直接引入：

- AutoGen runtime 不是为 Omubot 的 timeline/pending 语义设计。
- LangGraph 对普通群聊热路径太重。
- Orleans 是 .NET 分布式 actor runtime，不适合 Python 项目直接引入。

建议只借鉴算法。

## 8. 测试方案

### Scheduler / Actor

- 同群运行中连续收到 3 个 `@bot`：应按 message_id 顺序产生 3 个高优先级 event，不互相覆盖。
- 同群运行中普通消息 10 条：不触发 10 轮 LLM；应按策略 background 或合并。
- 两个群同时 `@bot`：应允许并发，占用 global semaphore 两个 slot。
- `global_llm_limit=1` 时两个群排队，但互不污染 timeline。

### Timeline

- 并发 append user pending 与 actor deactivate 时，pending 状态稳定。
- `flush_pending_count` 仍只 flush 本轮 cutoff。
- 运行期间新增 `@bot` pending 不被上一轮 assistant flush。

### Send Queue

- 多段回复：首段发送后允许下一轮 generation。
- 后续段发送失败重试不阻塞下一轮 event 入队。
- 同群两个 ReplySegmentBatch 发送顺序稳定。

### LLMClient

- collect segments 模式不调用 `on_segment`。
- 默认 send 模式兼容旧测试。
- `allow_empty_fallback=False` 行为不变。

### 回归

- `/吃什么` 命令 fast path 不进入 group actor LLM 队列。
- `@其他用户 /吃什么` 不触发命令。
- directed follow-up 空回复静默。
- Food 教程持久化不受影响。

## 9. 最小可执行设计

如果只做一版最小但可靠的实现：

1. 在 `services/scheduler.py` 中把 `_GroupSlot.pending_at: bool` 改为 `PriorityQueue[QueuedTrigger]` 或 `heapq`。
2. busy 时强触发按 priority 入队，不覆盖 `slot.trigger`。
3. `_do_chat()` finally 中如果 queue 非空，pop 最高优先级触发下一轮。
4. 增加全局 `asyncio.Semaphore` 包住 `self._llm.chat()`。
5. 暂不拆发送。

这一步能解决“明确寻址被布尔状态吞掉”的问题，但不能完全解决“发送慢卡住后续对话”。

真正解决卡顿，需要继续做：

6. `LLMClient.chat()` collect segments。
7. 每群 send_queue。
8. 首段发送后释放 generation slot。

## 10. 推荐最终方案

推荐实施顺序：

```text
Phase 0 观测补强
  -> Phase 1 每群事件队列
  -> Phase 2 全局 LLM semaphore
  -> Phase 2.5 Send Queue 准备层
  -> Phase 3 生成/发送拆分
  -> Phase 4 可选持久化
```

推荐默认参数：

```toml
[scheduler.concurrency]
global_llm_limit = 2
per_group_llm_limit = 1
max_group_queue = 8
max_low_priority_queue = 3
first_segment_release = false
drop_stale_low_priority_after_s = 45
```

首轮上线仍建议把 `first_segment_release` 设为 `false`；需要验证提速时，可先在烤群单独改为 `true` 做实验观察。

上线策略：

- 先在烤群开启 `global_llm_limit=2` 和 actor queue。
- 观察 24 小时：
  - `@bot` 平均等待时间；
  - 普通消息被 background 比例；
  - LLM rate limit 次数；
  - send retry 次数；
  - queue overflow 次数。
- 再决定是否开启发送拆分。

## 11. 方案评估

优点：

- 保留 Omubot 现有 timeline/pending 设计，不推倒重来。
- 跨群并发能提升总体吞吐。
- 同群串行 generation 保护对话一致性。
- 事件队列比 `pending_at` 布尔更可解释、可测试。
- 发送拆分针对当前真实阻塞点，不把所有问题都误认为 LLM 慢。

风险：

- Phase 3 会引入“assistant 已归档但未完全可见”的新状态，需要观测与测试。
- compaction 需要感知 `visible_state`，避免未完整发送内容过早进入 summary。
- 可见工具输出必须进入 send_queue，否则首段释放或段间让位会造成贴纸/图片乱序。
- 队列策略如果过于激进，会让普通群聊显得 bot 冷淡。
- 全局并发过高会触发供应商 rate limit。
- 如果后续插件在 `on_post_reply` 中依赖“消息已完整发出”，需要明确 hook 时机。

最终判断：

- **不要做同群多 LLM 同时回答。**
- **要做跨群 LLM 并发。**
- **要把同群从布尔 pending 改成 actor/event queue。**
- **要把“生成完成”和“发送完成”拆成两个阶段。**

## 12. 源码锚点

本节记录本次本地源码阅读的关键锚点，便于复核。

### Omubot

- `services/scheduler.py:118`：`notify()` 入口，当前根据 trigger/probability 决定是否 `_fire()`。
- `services/scheduler.py:142`：运行中 `@bot/direct_followup` 只设置 `pending_at=True`。
- `services/scheduler.py:169`：普通消息 busy 时 deactivate latest pending。
- `services/scheduler.py:313`：`_fire()` 把 `slot.trigger` 快照到 `_do_chat()`。
- `services/scheduler.py:369`：`_do_chat()` 覆盖 trigger marker、LLM 调用、发送、no visible reply 处理。
- `services/llm/client.py:890`：`_emit_reply_segments()` 等待 `on_segment()` 和段间 sleep。
- `services/llm/client.py:1561`：group chat 记录 `reply_pending_cutoff`。
- `services/memory/timeline.py:240`：assistant add 支持 `flush_pending_count`。

### Rasa

- `rasa/core/lock.py:37`：`TicketLock`。
- `rasa/core/lock.py:70`：递增 ticket number 并 append 到 deque。
- `rasa/core/lock_store.py:89`：`LockStore.lock()` async context manager。
- `rasa/core/lock_store.py:110`：`_acquire_lock()` 轮询直到 `ticket == now_serving`。
- `rasa/core/agent.py:418`：处理消息时按 sender_id 加锁。
- `rasa/core/processor.py:580`：processor 层也围绕 sender_id lock。

### AutoGen

- `autogen_core/_single_threaded_agent_runtime.py:257`：runtime 全局 `_message_queue`。
- `autogen_core/_single_threaded_agent_runtime.py:332`：`send_message()` 创建 future 并入队。
- `autogen_core/_single_threaded_agent_runtime.py:557`：publish 时对订阅 recipients gather。
- `autogen_core/_single_threaded_agent_runtime.py:671`：`_process_next()` 从队列取 envelope。
- `autogen_core/_single_threaded_agent_runtime.py:724`：direct send 创建 background task。
- `autogen_core/_runtime_impl_helpers.py:32`：`SubscriptionManager`。

### LangGraph

- `langgraph/pregel/main.py:448`：`Pregel` actor + channel + BSP 说明。
- `langgraph/pregel/_runner.py:175`：`PregelRunner.tick()`。
- `langgraph/pregel/_runner.py:258`：并发 submit task。
- `langgraph/pregel/_runner.py:573`：`commit()` 保存 task writes/errors/interrupts。
- `langgraph/pregel/_algo.py:232`：`apply_writes()`。
- `langgraph/pregel/_algo.py:294`：按 channel 汇总 writes。
- `langgraph/pregel/_algo.py:316`：统一更新 channel version。
- `langgraph/checkpoint/base/__init__.py:182`：checkpointer 使用 `thread_id`。

### Orleans

- `Orleans.Runtime/Scheduler/ActivationTaskScheduler.cs:11`：single-concurrency, in-order scheduler。
- `Orleans.Runtime/Scheduler/WorkItemGroup.cs:70`：`EnqueueTask()`。
- `Orleans.Runtime/Scheduler/WorkItemGroup.cs:130`：`Execute()` 注释说明同 activation 同时只有一个线程执行。
- `Orleans.Runtime/Scheduler/WorkItemGroup.cs:177`：long turn warning。
- `Orleans.Runtime/Catalog/ActivationData.cs:1182`：`MayInvokeRequest()`。
- `Orleans.Core.Abstractions/Concurrency/GrainAttributeConcurrency.cs:25`：`ReentrantAttribute`。
- `Orleans.Core.Abstractions/Concurrency/GrainAttributeConcurrency.cs:96`：`AlwaysInterleaveAttribute`。
- `Orleans.Core.Abstractions/Concurrency/GrainAttributeConcurrency.cs:109`：`MayInterleaveAttribute`。
