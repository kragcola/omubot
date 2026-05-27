# P0 派单 2 — B 簇入口三件合并 PR（执行追踪）

> 状态：2026-05-27 回填。本文是 [P0 待审清单](omubot-grayscale-p0-pending-2026-05-27.md) 的「派单 2」执行版。
>
> 当前结论：Wave 0 ~ 4 已落地并完成本地定向验证；Wave 5 已完成本地 e2e / `ruff` / `pyright`，待 PR 合并与 24h 灰度观察后才能整单收口。
>
> 范围：F7 BotPairLoopGuard + F3 message coalescer + F10 per-group lock（B 簇入口 normalization 三件，1 个 PR）。
>
> 配套：[原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 7 / §Issue 3 / §Issue 10（路径 B 治本层 lock 部分）。
>
> 上游决策：用户已拍板「方案全按推荐来 / PR 自主分阶段 / 一个派发单需完成全部修复」——本派单**收口才闭环**，不接受单件分发布。
>
> 依赖：派单 1（A 簇出口）应已合并 main——本派单的 dedup gate 部分（A2.1）会被本派单 group lock 在 `_do_chat` 入口持锁覆盖；如派单 1 未完成，Wave 4 的集成测试无法验证"lock + dedup 双层"完整链路。
>
> 执行原则（覆盖任何冲突项）：
> 1. **整单 1 个 PR**——三件共 B 簇 router 入口骨架，必须一次合并；不允许"先 F7 单独发"再补 F3/F10 lock。
> 2. **Wave 内可并行，跨 Wave 串行**——下游 Wave 必须基于上游 Wave 的真实 hook 写测试。
> 3. **每条 Wave 自带 D1 grep 证据 + D2 cancel-path 测试 + 30 秒回滚开关**，缺一不闭环。
> 4. **不引入 Redis 依赖**——3B Redis-backed coalescer 已被解决方案文档判定为"不推荐当下做"，本派单不实现。

---

## 1. 主线自审与证据订正（执行前必读）

下表是对 [原解决方案文档](omubot-grayscale-issues-2026-05-26-solutions.md) §Issue 7 / §Issue 3 / §Issue 10 路径 B 的 grep 实证修订。

| 解决方案文档位置 | 初稿表述 | grep 实证（2026-05-27） | 订正 |
|---|---|---|---|
| §Issue 7 形态 | "kernel/router.py group_listener 入口检查 is_suppressed，命中即 drop" | 现行 group listener 在 `kernel/router.py:906`；`blocked_users` 检查在 `kernel/router.py:924`；pair guard 实际通过 `_maybe_drop_pair_guard()` 接在 `kernel/router.py:927-931`，位于 `blocked_users` 之后、timeline / scheduler 之前 | Wave 1 实际 hook 位点不是文档初稿的旧行号；应以 `group_listener -> blocked_users -> _maybe_drop_pair_guard()` 这条链为准 |
| §Issue 3 形态 | "kernel/router.py group_listener 拿到消息后先入 bucket" | coalescer 实际不拦 MessageContext / timeline；helper 在 `kernel/router.py:355-420`，调用点在 `kernel/router.py:1060-1067` 与 `kernel/router.py:1251-1258`；`_should_bypass_coalescer()` 在 `kernel/router.py:347-352`，条件为 `is_addressed` 或 `trigger is not None` | Wave 2 实现改为"只延迟 `scheduler.notify()`，不吞 timeline"；priority bypass 由 router 调 `discard() + direct notify` 完成，不是 coalescer 内部 `bypass=True` 开关 |
| §Issue 10 lock 部分形态 | "services/scheduler.py:_do_chat 入口 async with lock：覆盖 prompt-build → LLM call → segments persist 整段" | `_GroupSlot.chat_lock` 定义在 `services/scheduler.py:47-75`；`slot.running_task = create_task(...)` 在 `services/scheduler.py:643`；`_do_chat()` 在 `services/scheduler.py:736`，`async with slot.chat_lock:` 在 `services/scheduler.py:741`，LLM 调用 `wait_for()` 在 `services/scheduler.py:802-815` | Wave 3 实际实现是 `_GroupSlot.__init__()` 持有 `asyncio.Lock()`，并在 `_do_chat()` 内包裹 prompt-build -> LLM -> send 整段，且 `wait_for()` 防止永久占锁 |
| §Issue 7 risk | "60s cooldown 期间双向静音会让群里人类用户也短暂感受不到 bot 回复" | `BotPairLoopGuard.is_known_peer()` 在 `kernel/bot_pair_guard.py:32-39` 只认 `known_other_bots[group_id]`；`_pair_key()` 在 `kernel/bot_pair_guard.py:70-77` 会直接过滤 self-pair 与非 peer 用户 | Wave 1 实现确认：只压已知 bot 对，人类消息一律放行；self-pair 也短路 |

---

## 2. Wave 0 — 前置零代码验证

派单第 0 步，零代码改动。Wave 1 ~ 3 hook 位点选择依赖本步骤回执。

| 步骤 | 命令 / 操作 | 预期 |
|---|---|---|
| 1 | `grep -n "on_message\|blocked_users\|MessageContext\|is_addressed\|enqueue\|trigger" kernel/router.py services/scheduler.py 2>/dev/null \| head -60` | 确认 router → scheduler 调用路径：router group_listener 完成 MessageContext 构造后，调 scheduler.enqueue 或类似接口；定位 enqueue 接口实际签名 |
| 2 | `grep -n "running_task\|_GroupSlot\|chat_lock\|asyncio.Lock" services/scheduler.py services/scheduler/*.py 2>/dev/null \| head -30` | 确认 _GroupSlot 当前字段，asyncio.Lock 是否已存在；定位 running_task 包络范围（line 624 创建 task，task done 后 lock 释放） |
| 3 | `grep -rn "known_other_bots\|bot_pair_guard\|sentinel_user_id" config/ kernel/ services/ 2>/dev/null` | 确认这些字段尚未存在（B 簇是新加） |
| 4 | `grep -n "self.self_id\|bot.self_id\|self_id" kernel/router.py services/scheduler.py 2>/dev/null \| head -15` | 确认 outbound (self) bot user id 在 outbound 钩子能拿到 |
| 5 | 写 1 行结论到本文 §6 自审表末行 | Wave 1-3 拍板：① pair_guard 三个 API hook 位点 ② coalescer bucket 入口 + 出口 ③ chat_lock 与 running_task 包络关系 |

**Wave 0 不是 commit；是派单前置验证**。

---

## 3. 并列执行 Wave 表

依赖关系：Wave 0 → Wave 1（F7 pair_guard）→ Wave 2（F3 coalescer）→ Wave 3（F10 group lock）→ Wave 4（router/scheduler 接线 + e2e）→ Wave 5（PR 合并 + 灰度切流）。

### 3.1 Wave 1 — F7 BotPairLoopGuard（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B1.1** | 新建 `kernel/bot_pair_guard.py` `BotPairLoopGuard`：keyspace = `(group_id, frozenset({self_id, other_id})) → deque[ts]`；API = `record_inbound(gid, sender_id) / record_outbound(gid, target_id) / is_suppressed(gid, peer_id) -> bool`；越过 `max_per_minute`（默认 3）即 `cooldown_seconds`（默认 60）双向静音；self-pair 短路；只对 `peer_id in known_other_bots[gid]` 触发；TTL 自愈（旧 ts 滑窗 drop） | `kernel/bot_pair_guard.py`（新文件 ~180 行） | `grep -rn "BotPairLoopGuard\|bot_pair_guard\|known_other_bots" kernel/ services/ tests/` 仅本文件 + tests 命中 | `tests/test_bot_pair_guard.py` cancel-path：record_inbound 中协程被取消后 deque 状态不污染下次 is_suppressed 判定 | `git restore kernel/bot_pair_guard.py tests/test_bot_pair_guard.py` |

**Wave 1 收口**：`uv run pytest tests/test_bot_pair_guard.py -v` 全绿；含测试用例 = ① 互引 4 条触发 cooldown ② cooldown 内单方继续也压 ③ self-pair 不触发 ④ 跨群独立 ⑤ TTL 自愈 ⑥ 人类用户在 cooldown 期不被误压 ⑦ pair key 排序对称（A→B 与 B→A 同 counter）。

### 3.2 Wave 2 — F3 message coalescer（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B2.1** | 新建 `services/coalesce.py` `MessageCoalescer`：bucket keyspace = `(group_id, sender_id) → CoalesceBucket{messages: deque, idle_timer: Task, max_window_timer: Task}`；API = `enqueue(gid, sender_id, msg) -> None / flush(gid, sender_id) -> list[msg] / discard(gid, sender_id) -> list[msg] / close() -> None`；idle window 默认 5s（用户停顿即 flush），max window 默认 12s（强制上限）；priority bypass 由 router 在 `_notify_group_scheduler()` 中 `discard() + direct notify` 实现；shutdown flush 钩子接 graceful shutdown | `services/coalesce.py`（新文件 ~120 行） | `grep -rn "MessageCoalescer\|CoalesceBucket\|discard(" services/ kernel/ tests/` 仅本文件 + tests 命中 | `tests/test_coalesce.py` cancel-path：idle_timer 被取消时 bucket 不丢失消息（取消即 flush）；shutdown 触发时全部 flush | `git restore services/coalesce.py tests/test_coalesce.py` |

**Wave 2 收口**：`uv run pytest tests/test_coalesce.py -v` 全绿；当前测试覆盖 = ① idle 静默 flush ② max window 强制 flush ③ sender / group 隔离 ④ `discard()` 仅回收不 flush ⑤ `close()` flush 全部 bucket ⑥ idle timer cancel-path。priority bypass 与 scheduler 接线在 Wave 4 的 router wiring 测试锁定。

### 3.3 Wave 3 — F10 per-group asyncio.Lock（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B3.1** | `services/scheduler.py` `_GroupSlot` 在 `__init__()` 中加 `chat_lock = asyncio.Lock()`；`_do_chat` 在 `services/scheduler.py:736` 主体外层包 `async with slot.chat_lock:`；lock 范围 = prompt-build + LLM call + send 段；LLM call 包 `asyncio.wait_for(..., timeout=_CHAT_LOCK_LLM_TIMEOUT_S)` 防 lock 永久持有 | `services/scheduler.py`（+ chat_lock 字段 + 包裹 + wait_for） | `grep -n "chat_lock\|asyncio.Lock\|wait_for" services/scheduler.py` 命中点 = 字段定义 + 1 包裹 + 1 wait_for | `tests/test_scheduler_chat_lock.py` cancel-path：① lock 持有期 task 被取消 → lock 自动释放（不死锁）② wait_for timeout 触发 → lock 释放 + slot 状态干净 ③ 并发两次 _do_chat 同 group → 第二次必须等第一次完成后进入 | `git restore services/scheduler.py` |

**Wave 3 收口**：`uv run pytest tests/test_scheduler_chat_lock.py -v` 全绿；并发场景 dedup gate（派单 1 A2.1）能在第二次 _do_chat 看到第一次 persist 后的 last_assistant_text。

### 3.4 Wave 4 — router/scheduler 接线 + config + metrics（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B4.1** | 多文件接线：① `kernel/router.py:927-931` 用 `_maybe_drop_pair_guard()` 接入 suppress + inbound metric；② `services/scheduler.py:710-727` 在 `_send_to_group()` 成功后记录 outbound；③ `kernel/router.py:355-420` 新 `_notify_group_scheduler()` 统一直通 / coalesce / bypass；④ `kernel/config.py:1598-1678` 加 `BotPairGuardConfig` + `CoalesceConfig`；`config/config.json:406-415` 补默认段；⑤ `plugins/chat/plugin.py:1034-1043` 装配 guard / coalescer；⑥ `services/block_trace/store.py:93-100` 加 6 个 runtime metrics | `kernel/router.py` + `services/scheduler.py` + `kernel/config.py` + `config/config.json` + `plugins/chat/plugin.py` + `services/block_trace/store.py` | `grep -rn "bot_pair_guard\|coalesce\|MessageCoalescer\|BotPairLoopGuard" kernel/ services/ plugins/ config/` 命中点全部一致 | `tests/test_router_b_cluster_wiring.py` cancel-path：is_suppressed → return 路径 / coalescer bypass / record_outbound fail-soft | `git restore kernel/router.py services/scheduler.py kernel/config.py config/config.json plugins/chat/plugin.py services/block_trace/store.py` |

**Wave 4 收口**：本地接线验证已完成：`tests/test_router_b_cluster_wiring.py` 锁定 router helper 行为，`tests/test_humanization_config.py` 锁定配置默认值与归一化，`tests/test_humanization_metrics_persist.py` 锁定 6 个 runtime metrics 的聚合；运行中 `/api/admin/block-trace/stats` 与灰度群观测留待 Wave 5 部署后执行。

### 3.5 Wave 5 — 集成测试 + PR 合并 + 灰度切流（1 条单点）

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **B5.1** | `tests/test_b_cluster_pipeline_e2e.py` 端到端：① 模拟 known bot 互引 5 条 → cooldown 双向 ② 同时多 sender 入 bucket → idle/max 触发各 1 次 flush ③ 并发两次 _do_chat → lock 序列化（dedup gate 能命中第一次的 last_assistant）④ enabled=false 各旗标短路 | `tests/test_b_cluster_pipeline_e2e.py`（新文件 ~200 行） | `uv run pytest tests/test_b_cluster_pipeline_e2e.py tests/test_bot_pair_guard.py tests/test_coalesce.py tests/test_scheduler_chat_lock.py tests/test_router_b_cluster_wiring.py -v` 全绿 | E2E cancel-path：bucket flush 中段取消 → 不丢消息；lock 持有中取消 → 释放；pair_guard 被取消 → counter 不脏写 | `git restore tests/test_b_cluster_pipeline_e2e.py` |

**Wave 5 收口**：① 全单元 + e2e 全绿 ② `uv run ruff check` + `uv run pyright` 无新增错误 ③ 灰度群 993065015 / 984198159 实跑 24 小时，pair_guard / coalescer / lock 行为符合预期，未观察到回复显著延迟（idle window 5s 是用户感知阈值边界），未观察到丢消息，dedup gate（派单 1）命中率因 lock 而提升。
**Wave 5 回填（2026-05-27）**：本地已完成 `source ./scripts/dev/env.sh && uv run pytest tests/test_b_cluster_pipeline_e2e.py tests/test_bot_pair_guard.py tests/test_coalesce.py tests/test_scheduler_chat_lock.py tests/test_router_b_cluster_wiring.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py -q`，结果 `37 passed`；`uv run ruff check ...` 通过；`uv run pyright kernel/bot_pair_guard.py services/coalesce.py tests/test_bot_pair_guard.py tests/test_coalesce.py tests/test_scheduler_chat_lock.py tests/test_router_b_cluster_wiring.py tests/test_b_cluster_pipeline_e2e.py` 为 `0 errors`。本 Wave 剩余未完成项仅有 PR 合并与 24h 灰度观察，因此状态保留 `🟡`。

---

## 4. 状态表

| Wave | 编号 | 内容 | 状态 | 验收人 | 验收时间 |
|---|---|---|---|---|---|
| 0 | B0 | 零代码前置验证 | ✅ | Codex 自审 | 2026-05-27 |
| 1 | B1.1 | F7 BotPairLoopGuard | ✅ | Codex 自审 | 2026-05-27 |
| 2 | B2.1 | F3 MessageCoalescer | ✅ | Codex 自审 | 2026-05-27 |
| 3 | B3.1 | F10 per-group chat_lock | ✅ | Codex 自审 | 2026-05-27 |
| 4 | B4.1 | router/scheduler 接线 + config + metric | ✅ | Codex 自审 | 2026-05-27 |
| 5 | B5.1 | e2e 测试 + PR 合并 + 灰度切流 | 🟡 | Codex 自审 | 2026-05-27 |

---

## 5. 验收口径（整单收口标准）

整单收口前必须**同时**满足：

1. §4 6 行 Wave 状态全部 ✅
2. `uv run pytest`（全量）+ `uv run ruff check` + `uv run pyright` 全绿
3. PR 单合并到 main（commit message 格式：`feat(router): P0 dispatch 2 — B cluster ingress (F7 + F3 + F10 lock)`）
4. 灰度群 993065015 / 984198159 24 小时观察期内 6 个 metric 写入；用户主观感受 = bot 回复未明显延迟（idle window 体感 ≤ 6s 是基线）
5. `maintenance-log.md` 顶部追加当日条目，记录"B 簇入口三件 1 PR 落地、coalesce idle/max 默认值、回滚开关位置"

2026-05-27 本地执行回填结论：当前已满足定向 `pytest` / `ruff` / `pyright` 与本地 e2e，但**尚未**满足"PR 合并到 main"、"全量 `uv run pytest`"与"24h 灰度观察"三项，因此整单仍处于待收口状态。

---

## 6. 回滚预案

整单回滚（PR 合并后发现严重问题）：

```bash
git revert <merge_commit_hash>
docker compose restart bot
```

旗标级别 kill-switch：

```bash
# config.json:
#   bot_pair_guard.enabled = false
#   coalesce.enabled = false
# chat_lock 没有 kill-switch（行为变更最小，本身就是 mutex）；如真要回退，git revert
docker compose restart bot
```

`enabled=false` 时 ① pair_guard 永远返回 not suppressed ② coalescer 永远 bypass（消息直通 router 出口）；等价于 PR 合并前行为。

---

## 7. 自审表（执行者填）

| 时间 | 自审项 | 结论 / 证据 |
|---|---|---|
| 2026-05-27 | Wave 0 完成：hook 位点 + chat_lock 包络确认 | `group_listener` 在 `kernel/router.py:906`，`blocked_users` 在 `kernel/router.py:924`，pair guard 调用点在 `kernel/router.py:927-931`；coalescer helper 在 `kernel/router.py:355-420`，只包装 `scheduler.notify()`；`slot.running_task` 在 `services/scheduler.py:643`，`slot.chat_lock` 在 `services/scheduler.py:47-75`，`_do_chat()` 包锁在 `services/scheduler.py:741`，LLM `wait_for()` 在 `services/scheduler.py:802-815` |
| 2026-05-27 | Wave 1 完成：pair_guard 测试覆盖 | `kernel/bot_pair_guard.py:9-91` 已落地；`tests/test_bot_pair_guard.py` 6 个用例全绿，覆盖 cooldown 触发、self-pair / human ignore、跨群隔离、TTL 自愈、pair key 对称、late bind self_id |
| 2026-05-27 | Wave 2 完成：coalescer 测试覆盖 | `services/coalesce.py:26-120` 已落地；`tests/test_coalesce.py` 6 个用例全绿，覆盖 idle flush、max-window flush、sender/group 隔离、`discard()`、`close()`、idle timer cancel-path |
| 2026-05-27 | Wave 3 完成：chat_lock + wait_for + cancel 三测试覆盖 | `services/scheduler.py:75` / `services/scheduler.py:741` / `services/scheduler.py:802` 已接线；`tests/test_scheduler_chat_lock.py` 3 个用例全绿，覆盖串行化、取消释放、超时释放 |
| 2026-05-27 | Wave 4 完成：router/scheduler 接线 + 6 个 metric | `kernel/router.py:304-420, 927-931, 1060-1067, 1251-1258`、`services/scheduler.py:710-727`、`kernel/config.py:1598-1678`、`config/config.json:406-415`、`plugins/chat/plugin.py:1034-1043`、`services/block_trace/store.py:93-100` 已对齐；`tests/test_router_b_cluster_wiring.py`、`tests/test_humanization_config.py`、`tests/test_humanization_metrics_persist.py` 全绿 |
| 2026-05-27 | Wave 5 完成：本地 e2e / lint / type，灰度待补 | `source ./scripts/dev/env.sh && uv run pytest tests/test_b_cluster_pipeline_e2e.py tests/test_bot_pair_guard.py tests/test_coalesce.py tests/test_scheduler_chat_lock.py tests/test_router_b_cluster_wiring.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py -q` => `37 passed`；`uv run ruff check ...` 通过；`uv run pyright kernel/bot_pair_guard.py services/coalesce.py tests/test_bot_pair_guard.py tests/test_coalesce.py tests/test_scheduler_chat_lock.py tests/test_router_b_cluster_wiring.py tests/test_b_cluster_pipeline_e2e.py` => `0 errors`；PR 合并与灰度 24h 未在本地完成 |
