# Migration: Arbiter-B 打断合并接通 + 确定性取消重开 (2026-06-11)

## 背景

烤群实测(group 993065015):同一用户连发三条 `emu`(@-only),bot 回了**两条**带 `[CQ:reply]` 的独立回复(分别引用第 1、第 2 条),既没合并也没被打断。

诊断三个洞(对照设计文档 `docs/tracking/fix-at-mention-burst-batching*.md` 的「Arbiter-B 中断 → Arbiter-A 等待 → 统一回复」闭环):

1. **洞①(通道断裂·根因)**:Arbiter-B monitor 读 `_pending_messages_since(baseline)` = timeline `pending[baseline:]`。emu#2/#3 在「Arbiter-A 决定开火」到「`_do_chat` 取 `generation_pending_baseline` 快照」的窗口内已落入 timeline pending,排在 baseline **之下** → monitor 永远空 → 6 小时 0 次 arm。它们只活在 `slot.pending_during_generation`,只驱动 finally 的第二次 fire,与打断器是两条不通的水管。
2. **洞②(语义)**:`_INTERRUPTION_SYSTEM_PROMPT` 把「纯补充不矛盾 → continue」,裸 `emu` 连发被判 continue。
3. **洞③(已发段不可逆)**:gate 首段免检 + 「已发段不撤回」,首段一旦出库 abort 模型上限就是「首段+重开」两条。但本次首段 23:41:02 才发,emu#2/#3 在 23:40:58–59 到达 → 有 4 秒空窗,首段未发,合一物理可行。

设计原则(用户确认):**不加延迟/min-wait/限制**,纯事件驱动。

## 改动:旧 → 新

| 维度 | 旧行为 | 新行为 | 位点 |
|------|--------|--------|------|
| during-gen 同人/同块 @ | 无脑塞 `pending_during_generation`,等 reply#1 跑完再开第二枪(两条回复) | 首段未发 + 同 block_id 或同 user_id → `running_task.cancel()`,finally 重新合并 burst_pending → Arbiter-A 统一开一条 | `notify` is_at 分支 [scheduler.py:653-685](../../services/scheduler.py#L653-L685) |
| Arbiter-B monitor 输入 | `_pending_messages_since(group_id, baseline)`(timeline baseline-since,有 race 黑洞) | 直读 `slot.pending_during_generation`(无窗口,canonical 源) | `_arbiter_b_monitor` [scheduler.py:1447-1481](../../services/scheduler.py#L1447-L1481) |
| 中断 prompt | 仅「回答/否定修正 → revise」「纯补充 → continue」 | 增「连续追问/重复呼叫(同一人多次@或反复叫名)→ abort_unsent(折进统一回复)」 | `_INTERRUPTION_SYSTEM_PROMPT` [arbiter.py:33-43](../../services/llm/arbiter.py#L33-L43) |
| 在飞身份追踪 | 无 | `_GroupSlot` 新增 `firing_block_id` / `firing_user_id`(`_fire` 写入)、`first_segment_sent`(首段真正 send 后置 True;新 fire 重置 False) | [scheduler.py:196-198](../../services/scheduler.py#L196-L198), [1723-1725](../../services/scheduler.py#L1723-L1725), [2149](../../services/scheduler.py#L2149)/[2184](../../services/scheduler.py#L2184) |

时间轴覆盖:首段未发 → 改动1 确定性取消(真合并);首段已发 → 改动2+3 Arbiter-B 砍未发段(诚实的 abort-unsent,无法合一)。

## D1 同模式扫描

- **所有 `pending_during_generation.append` 位点**(notify 的 6 个 live-task 分支):`is_at`(654)、`is_directed_followup`(700)、`is_correction`(713)、`is_closing`(741)、`is_greeting`(766)、`is_video_always`(781)。
  - **仅 `is_at` 加 cancel-and-remerge**。其余 5 个是不同 turn 类型(续话/修正/告别/招呼/视频),不是同一寻址者的 @ 连发;`firing_block_id`/`firing_user_id` 也只在 @ 触发的 fire 上有意义。**刻意不扩散**,避免把「告别+招呼」这类正常多轮误判成需要取消的连发。
- **所有 `running_task.cancel()` 位点**:mute(422)、clear_pending(1184)、close(1214)、新增的 cancel-remerge(679)。前三者在 cancel **前**都清空了 `pending_during_generation`(mute 425 / clear_pending 1181),不会被 finally 的 re-merge 分支复活;新增位点**故意保留** pending 让 finally 重开。已验证无冲突。
- **`_pending_messages_since` 残留调用**:仅 on_segment 的 revise 分支(2115)仍用它读 timeline 补 pending —— 保留,与 monitor 的 slot 直读互不影响。

## 测试映射

| 测试 | 覆盖 |
|------|------|
| `test_same_user_burst_cancels_and_remerges_into_one_reply` | 洞①核心:同人连发首段前取消重开,最终 2 次 chat()(取消的+合并的),一条真回复 |
| `test_first_segment_sent_blocks_cancel_remerge` | 洞③护栏:`first_segment_sent=True` 时同人连发**不取消**,只入队 |
| `test_different_block_burst_does_not_cancel` | 异块异人不取消(交给 finally re-fire) |
| `test_block_fire_queue_cleared_on_cancel`(既有) | D2:取消后 block_fire_queue 不污染下一轮 |
| `test_segment_aborted_on_arbiter_abort` 等 3 个(改) | monitor 改读 slot 后,inject 改向 `pending_during_generation`,abort/continue/timeout 行为不变 |
| `test_interruption_prompt_covers_repeated_calls` | 洞②:prompt 含「重复呼叫/统一回复」规则,防回归删除 |

D2 cancel-path:`test_same_user_burst_*` 与 `test_block_fire_queue_cleared_on_cancel` 共同断言 cancel 后无孤儿(无半条 assistant turn 污染、queue 清空、finally 正确重开)。

## 回滚路径

四处改动相互独立,可单独回滚:
1. `notify` is_at 的 same_addressee 取消块 → 删除后退回「无脑入队」(回到旧两条回复行为,但不崩)。
2. `_arbiter_b_monitor` 改回 `_pending_messages_since(group_id, baseline)` → 退回 race 黑洞(monitor 哑火,但不崩)。
3. `_INTERRUPTION_SYSTEM_PROMPT` 删新规则行 → 退回旧语义。
4. `_GroupSlot` 三字段 + `_fire`/on_segment 赋值 → 删除(改动1依赖它,需一起回滚)。

整体 git revert 本次 commit 即可完全回退;不涉及 DB/schema/config 迁移。

## 验证证据

- `uv run pytest`:2632 passed / 17 skipped(全量)。
- `uv run ruff check`(改动文件):All checks passed。
- `uv run pyright services/scheduler.py services/llm/arbiter.py`:0 errors。
