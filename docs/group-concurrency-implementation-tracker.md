# Omubot 群聊并发改造跟踪

本文用于实时跟踪 `docs/group-concurrency-architecture-plan.md` 的落地状态。每次实现、测试或发现风险后都应更新本文件。

## 当前状态

- 状态：Phase 0-3 已实现；真人验收通过，正在做收口防回退
- 当前阶段：Phase 3 验证收口
- 默认策略：`first_segment_release=false`；发送层允许同群短可见回复在多段回复段间受控让位
- 目标范围：事件优先级队列、强触发不丢失、全局 LLM 并发限制、关键观测日志

## 决策记录

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-05-09 | 先实现 Phase 0-2，不直接做 Phase 3 首段释放 | 可见工具输出尚未统一进入 send queue，贸然释放会造成贴纸/文字顺序交错 |
| 2026-05-09 | 同群 LLM generation 保持单并发 | 保护 timeline、pending cutoff、工具副作用和群里可见顺序 |
| 2026-05-09 | 跨群 LLM 通过全局 semaphore 并发 | 解决一个群慢回复阻塞其他群的问题，同时避免供应商限流 |
| 2026-05-09 | Phase 3 前必须先做 Phase 2.5 Send Queue 准备层 | 统一文字、贴纸等可见输出顺序后，才能安全开启首段释放 |

## 实施清单

| 阶段 | 项目 | 状态 | 备注 |
| --- | --- | --- | --- |
| Phase 0 | scheduler 队列/等待/发送耗时日志 | 已实现 | 增加 LLM wait/slow 日志与 queued trigger 数 |
| Phase 1 | `pending_at` 布尔改为强触发优先级队列 | 已实现 | 保留 `pending_at` 兼容属性 |
| Phase 1 | busy 时 `@bot/direct_followup/video_always` 入队且不覆盖 | 已实现 | 普通消息仍 background |
| Phase 1 | `_do_chat()` 完成后按优先级续跑下一事件 | 已实现 | 一轮只消费一个 trigger |
| Phase 2 | 全局 LLM semaphore | 已实现 | 默认限制 2，可由 `[scheduler.concurrency]` 配置 |
| Phase 2 | RateLimit 重试时释放 semaphore | 已实现 | semaphore 只包裹单次 `llm.chat()` |
| Phase 2.5 | 盘点所有群内可见输出入口 | 已盘点 | LLM 文字、`send_sticker`、`send_group_msg` 与 `element_detector` 可见回复已入队；命令即时反馈暂列 fast path |
| Phase 2.5 | 定义 `SendItem`/`ReplySegmentBatch` 统一发送任务 | 已实现最小版 | `SendItem(kind="text|message")`，覆盖文字、群发文本和贴纸图片段 |
| Phase 2.5 | 新增每群 `GroupSendQueue` 串行发送器 | 已实现最小版 | 当前调用方仍 await 完成，保持旧语义 |
| Phase 2.5 | 改造 LLM 对话期间的 `send_sticker` 可见输出 | 已实现最小版 | 仅当 `ToolContext.extra["send_queue"]` 存在时入队 |
| Phase 2.5 | 改造 LLM 对话期间的 `send_group_msg` 可见输出 | 已实现最小版 | 仅当 `ToolContext.extra["send_queue"]` 存在时入队 |
| Phase 2.5 | 为 assistant turn 增加可见状态设计 | 已实现最小版 | `pending|first_segment_sent|complete|failed`，scheduler 完成发送后标记 `complete` |
| Phase 2.5 | compaction 跳过或降级处理未完整可见 assistant | 已实现最小版 | prompt 屏蔽未完整正文；compact split 避开未 complete assistant 及其用户输入 |
| Phase 3 | `LLMClient.chat()` collect segments 模式 | 已实现 | 返回未发送 `CollectedReply`，不再在 LLM 调用链里发送分段 |
| Phase 3 | scheduler 统一发送 collected reply batch | 已实现 | 默认等完整批次发送完成；实验开关开启时首段后释放 |
| Phase 3 | `ReplySegmentBatch` 批次发送状态机 | 已实现 | send queue 暴露首段完成与批次完成句柄；默认回复批次允许同群短可见回复在段间受控让位 |
| Phase 3 | 批次发送失败状态 | 已实现 | 首段/尾段失败会让批次 `done` 失败，scheduler 标记 assistant `failed` |
| Phase 3 | 批次发送运行观测 | 已实现 | 记录 queue wait、首段耗时、尾段耗时、总发送耗时 |
| Phase 3 | 首段发送后释放 generation slot | 已实现实验版 | 仅配置开启时启用；尾段用 assistant `turn_id` 精确回写状态 |
| Phase 4 | 持久化高优先级事件 | 暂缓 | 视 Phase 1-2 运行效果决定 |

## 风险跟踪

| 风险 | 等级 | 状态 | 缓解 |
| --- | --- | --- | --- |
| 同群多个强触发被旧 `pending_at` 合并 | 高 | 已修复 | 优先级队列保留每个 trigger |
| 全局 LLM 并发过高触发 rate limit | 中 | 观察中 | 默认 `global_llm_limit=2` |
| Phase 3 首段释放导致贴纸/短回复乱序 | 中 | 已有保护 | LLM 可见输出入 send queue；多段回复只在段落边界让位，仍由单群 send worker 串行发送 |
| compaction 处理未完整可见 assistant | 中 | 已有保护 | `visible_state != complete` 不向 prompt 暴露全文，compact split 退回到该轮 user/assistant 前 |
| Phase 3 首段释放误标尾段状态 | 中 | 已修复 | assistant turn 使用稳定 `turn_id`，尾段按 id 回写状态 |
| 表情包收录授权误判 | 低 | 已修复 | `requested_by` 代表管理员授权；非管理员图片来源记为 `stolen` |
| 插件或工具绕过 send queue 直发群消息 | 中 | 部分收口 | LLM 回复流和 `element_detector` 可见回复已入队；命令即时反馈暂保留 fast path 并作为例外记录 |

## Phase 2.5：Send Queue 准备层

Phase 2.5 是 Phase 3 的前置阶段。目标不是提速，而是先让“群里看得见的输出”拥有统一顺序边界。

### 背景

当前可见输出不只有 scheduler 文字发送：

- scheduler `_send_to_group()` 发送文字段落。
- LLM 工具可能直接发送贴纸，例如 `send_sticker`。
- 部分插件/命令可能直接调用 bot 发送消息。

已盘点的主要入口：

- LLM 回复流：`services/scheduler.py` 的 `send_group_text()` / `_send_to_group()`，已改为 `GroupSendQueue.send_group_text()`。
- LLM 可见工具：`services/tools/sticker_tools.py` 的 `send_sticker`，在 scheduler 注入 `send_queue` 时已改为排队发送。
- LLM 其他可见工具：`services/tools/group_admin.py` 的 `send_group_msg` 在 scheduler 注入 `send_queue` 时已改为排队发送。
- 要素察觉插件：`plugins/element_detector/plugin.py` 的预设与 LLM 生成可见回复已改走 `scheduler.enqueue_group_text()`，只在 send queue 段间边界受控让位。
- 命令/插件 fast path：`services/command.py`、`plugins/food/plugin.py`、`plugins/chat/plugin.py` 调试命令、`plugins/echo/plugin.py` 仍直发；这些不属于 LLM 回复流，暂不与 Phase 3 的首段释放混用。
- 私聊路径：`kernel/router.py` 与贴纸私聊发送仍直发，不参与同群 send queue。

如果 Phase 3 直接开启 `first_segment_release=true`，可能出现：

```text
上一轮第 1 段文字
下一轮工具贴纸
上一轮第 2 段文字
```

这会让群里可见顺序和 LLM 回复逻辑不一致。

### 任务拆分

1. **可见输出盘点**
   - 搜索 `send_group_msg`、`bot.send`、`send_sticker`、CQ 图片/语音发送。
   - 标记每个入口属于：LLM 回复流、命令即时反馈、后台通知、管理调试。
   - 只有 LLM 回复流必须强制进入 send queue；命令即时反馈可暂时保留 fast path，但要记录例外。

2. **发送任务契约**
   - 定义最小结构，例如：

     ```python
     SendItem(
         group_id: str,
         kind: Literal["text", "sticker", "image"],
         payload: Any,
         target_message_id: int | None,
         reply_batch_id: str | None,
         created_at: float,
     )
     ```

   - 文字段落、贴纸、图片都转换为 `SendItem` 后入队。

3. **每群串行发送器**
   - `GroupSendQueue(group_id)` 内部持有 `asyncio.Queue[SendItem]`。
   - 同群只有一个 send worker。
   - 发送失败可重试，但不能让后续同群 `SendItem` 插队。

4. **工具输出改造**
   - `send_sticker` 在 LLM 对话上下文中不直接发群，而是 enqueue `SendItem(kind="sticker")`。
   - 如果工具在非 LLM 回复流中被命令调用，可继续走直发或显式 fast path。

5. **assistant 可见状态**
   - 设计 timeline assistant turn 的可见状态：
     - `pending`：已生成，尚未开始可见发送。
     - `first_segment_sent`：首段已发送，但全文未完成。
     - `complete`：本轮可见输出全部发送完成。
     - `failed`：发送失败或被静音/取消。
   - 当前最小实现：
     - timeline assistant turn 写入 `visible_state` 与 `visible_updated_at`。
     - LLM 回复流写入时根据分段状态标记 `pending` 或 `first_segment_sent`。
     - scheduler 最后一段发送完成后标记最新 assistant 为 `complete`。
     - 非 scheduler 直接调用 LLMClient 时保持 `complete`，兼容旧测试与工具调用。

6. **compact 保护**
   - compact 不应把 `visible_state != complete` 的 assistant 全文直接压入 summary。
   - 当前最小实现：
     - `_build_group_messages()` 使用 `get_turns_for_prompt()`，剥离 timeline 元数据。
     - `visible_state != complete` 的 assistant 在 prompt 中只显示状态占位，不暴露完整正文。
     - `_compact_group()` 先用 `clamp_compact_split_to_visible()` 回退 split，避免把未完整 assistant 及其对应 user turn 压进 summary；compact 熔断器的 `drop_oldest` 也使用同一保护。

### Phase 2.5 验收条件

- 多段文字 + 贴纸按 send queue 串行边界稳定发送，不出现直发绕队列。
- 下一轮 LLM generation 即使提前开始，也不能绕过 send queue 直接产生群内可见输出。（LLM 文字、`send_sticker`、`send_group_msg`、`element_detector` 可见回复最小链路已覆盖）
- 多段回复发送时，已入队的同群短可见回复可在段间边界受控让位，避免长尾段把实时短回应压到整批之后。
- 同群两个 `ReplySegmentBatch` 可见顺序稳定。
- 发送失败重试时，同群后续输出不插队。
- compact 对未完整可见 assistant 有明确保护策略和测试。
- 命令 fast path 例外列表清楚，且不与 LLM 回复流混用。

## Phase 3：生成/发送拆分与首段释放

当前默认保持完整发送屏障；当 `first_segment_release=true` 时启用实验性首段释放：

- `LLMClient.chat(..., collect_segments=True)` 返回 `CollectedReply(segments, full_reply)`。
- collect 模式只负责生成、分段、写 assistant turn，不在 LLM 调用链里等待 `on_segment()`。
- scheduler 收到 `CollectedReply` 后通过 `ReplySegmentBatch` 发送完整批次。
- 默认模式下，同群 `running_task` 仍覆盖“生成 + 完整发送”，所以不会让下一轮同群 generation 抢跑。
- 全局 LLM semaphore 只覆盖 `llm.chat()`，因此跨群不会因为本群发送延迟长期占住 LLM 槽。
- `first_segment_release=false` 时，同群 `running_task` 仍覆盖“生成 + 完整发送”，但 send queue 允许段间短回复让位。
- `first_segment_release=true` 时，首段发送成功后释放同群 generation；尾段继续后台发送并按 `turn_id` 回写状态。

### 后续步骤

1. 真人测试与观测：
   - Docker 重建后先真人测试 `@bot`、directed follow-up、多段回复 + 贴纸、跨群并发。
   - 观察 `reply_batch_queue_wait_s`、`first_segment_elapsed_s`、`tail_send_elapsed_s` 是否符合预期。
2. 再评估是否进入 actor 化：
   - 若 bool/heap trigger 仍足够稳定，暂不重写成完整 `GroupActor`。
   - 若出现队列取消、持久化、低优先级合并需求，再进入 Phase 4/Actor 化。

## 验证计划

- `source ./scripts/dev/env.sh`
- `uv run pytest tests/test_scheduler.py tests/test_group_timeline.py tests/test_client.py -q`
- `uv run pytest tests/test_send_queue.py tests/test_sticker_tools.py tests/test_scheduler.py tests/test_group_timeline.py tests/test_client.py -q`
- `uv run ruff check services/send_queue.py services/scheduler.py services/tools/sticker_tools.py tests/test_send_queue.py tests/test_sticker_tools.py tests/test_scheduler.py`

## 更新日志

- 2026-05-09：创建跟踪文档，确定先落 Phase 0-2。
- 2026-05-09：实现强触发优先级队列、全局 LLM semaphore、配置入口与聚焦测试；`tests/test_scheduler.py tests/test_config_loader.py -q` 通过（67 passed）。
- 2026-05-09：完成相关回归；`tests/test_scheduler.py tests/test_group_timeline.py tests/test_client.py tests/test_config_loader.py -q` 通过（155 passed），ruff 通过。
- 2026-05-09：同步 `config/config.toml` 与 `config.example.toml` 的 `[scheduler.concurrency]` 示例配置。
- 2026-05-09：Phase 2.5 补充可见输出盘点；新增最小 `GroupSendQueue`，scheduler 文字与 LLM `send_sticker` 开始共享同群串行发送路径。
- 2026-05-09：Phase 2.5 聚焦验证通过：`ruff check` 通过；`tests/test_send_queue.py`、`SendStickerTool` 发送相关用例、`tests/test_scheduler.py`、`tests/test_group_timeline.py`、`tests/test_client.py` 共 138 passed。完整 `tests/test_sticker_tools.py` 仍有既有 `test_save_sticker_bot_steal` 权限断言失败，和发送队列改动无关。
- 2026-05-09：`send_group_msg` 工具在 LLM 对话上下文中接入 `send_queue`；无 `send_queue` 的命令/调试路径保持旧直发语义。聚焦验证通过：`ruff check` 通过，相关测试 165 passed。
- 2026-05-09：assistant `visible_state` 最小闭环落地；prompt 构建会屏蔽未完整可见正文，group compact 与 compact 熔断器都会避开未完成可见轮次。聚焦验证通过：`ruff check` 通过，相关测试 173 passed。
- 2026-05-09：Phase 3 保守拆分落地：LLM collect segments、scheduler 默认发送完整 collected batch；首段释放当时仍未启用。聚焦验证通过：`ruff check` 通过，相关测试 175 passed。
- 2026-05-09：`ReplySegmentBatch` 状态机落地：send queue 以批次为原子单位发送多段回复，暴露 `first_segment_sent` / `done` future，scheduler 改为发送完整批次。聚焦验证通过：`ruff check` 通过，相关测试 176 passed。
- 2026-05-09：批次发送失败状态落地：首段失败与尾段失败都有 future 语义，scheduler 捕获发送异常并标记最新 assistant `visible_state=failed`。聚焦验证通过：`ruff check` 通过，相关测试 179 passed。
- 2026-05-09：批次发送观测落地：`BatchSendHandle.started` 暴露 queue wait，scheduler 记录 `reply_batch_queue_wait_s`、`first_segment_elapsed_s`、`tail_send_elapsed_s`、`total_send_elapsed_s`。聚焦验证通过：`ruff check` 通过，相关测试 179 passed。
- 2026-05-09：首段释放实验开关落地：`first_segment_release=true` 时首段发送后释放同群 generation；尾段后台完成并通过 assistant `turn_id` 精确标记 `complete/failed`。聚焦验证通过：`ruff check` 通过，相关测试 182 passed。
- 2026-05-09：收口回归扩大到 command/Food/sticker；修复 `save_sticker` 在管理员授权收录他人图片时误用 `ctx.user_id` 判权的问题，非管理员来源正确记为 `stolen`。更宽回归通过：`ruff check` 通过，相关测试 240 passed。
- 2026-05-09：Docker 已重建并仅重启 `bot` 容器；`napcat` 未重建以保留设备指纹。启动日志确认 Bot 384801062 已连接 OneBot，允许群历史加载完成。
- 2026-05-09：真人日志复查发现 `@emu不吃小杯面 /吃什么` 被当普通 @ 对话，原因是启动时只采用 NoneBot `nickname`，未合并 `BOT_NICKNAMES`。已改为合并两处昵称，并补充文本式 `@昵称 (QQ号)` 的命令门禁测试；`tests/test_command.py tests/test_chat_plugin.py -q` 与聚焦 `ruff check` 通过。
- 2026-05-09：同轮复查发现贴纸补发后若模型返回控制符-only 文本，会在控制符剥离后被 `tool_visible` 静默，导致 `@bot` 只发贴纸。已在 suppress 后回退到保存正文，并补充 `test_kaomoji_enforce_control_token_uses_deferred_reply`；相关 client/command/chat 测试 28 passed，聚焦 `ruff check` 通过。
- 2026-05-09：真人日志复查发现 `element_detector` 的可见回复绕过同群 send queue，可能插入上一轮多段回复中间；已新增 `scheduler.send_group_text()` 公共发送入口，并让 `element_detector` 预设/LLM 回复走同群发送队列。同期补充 LLM 正文开头 `[CQ:reply]` 清理回归，避免和 scheduler 引用前缀叠加。聚焦验证通过：`tests/test_element_detector.py tests/test_client.py::test_strip_leading_reply_cq_removes_model_leaked_quote_prefixes tests/test_send_queue.py -q` 16 passed，聚焦 `ruff check` 通过。
- 2026-05-09：重建后真人日志复查：NapCat 外显顺序确认 `element_detector` 的“对”已等上一轮 4 段回复全部发送后才出现，插队问题收住；但 `element_detector` 在 `on_message` 中等待队列完成导致 hook 超过 5s 预算。已新增 `GroupSendQueue.enqueue_group_text()` / `scheduler.enqueue_group_text()`，让 `element_detector` 只入队后返回，并用 assistant `visible_state=pending -> complete/failed` 后台回写。同期为强触发 LLM 调用增加“优先回应本轮最后一条触发消息”的聚焦指令，降低 @ 短句被历史/知识上下文带偏的概率。聚焦验证通过：`tests/test_element_detector.py tests/test_send_queue.py tests/test_scheduler.py tests/test_client.py::test_strip_leading_reply_cq_removes_model_leaked_quote_prefixes -q` 65 passed，聚焦 `ruff check` 通过。
- 2026-05-09：再次复查 23:29-23:30 真人日志，确认仍有两点：分段器把 `——` 切成跨段 `—`/`—比如`；`first_segment_release=false` 下 element 短回复被完整批次屏障压到最后。已修复连续破折号/省略号不可拆分，并让 `ReplySegmentBatch` 在段间边界对已入队短可见回复受控让位。聚焦验证通过：`tests/test_segmentation.py tests/test_send_queue.py tests/test_element_detector.py tests/test_scheduler.py tests/test_client.py::test_strip_leading_reply_cq_removes_model_leaked_quote_prefixes -q` 78 passed，聚焦 `ruff check` 通过。
- 2026-05-09：真人复测发现生成层按心情/话题生成长回复后，发送层仍用 `max_send_segments=4` 把 13 个 raw segments 合并成 4 条，导致最后一条过长。已将默认 `max_send_segments` 改为 `0`（不限制正常分段数量），保留正数配置作为显式防刷屏保险丝，避免发送层压过动态长度策略。
- 2026-05-10：真人验收通过：长回复分段不再合并成大尾段，段间短回复可受控让位，连续破折号不再跨段拆开，命令路径验收通过。收口新增 `soft_max_send_segments=12` 软防刷屏：极端长回复会截断并追加自然收尾，不再使用合并大尾段作为默认保护。
- 2026-05-10：观测日志补强：分段日志新增 `segmentation_limit=none|soft|hard|soft_then_hard`，回复批次发送日志新增 `interleave_count`，用于快速判断是否触发软/硬限段以及段间让位次数；不改变分段与回复策略。
