# S1 D3 实施清单 — bot↔bot 循环行为熔断（待确认后编码）

> 2026-05-31。承接 [reply-scheduling-impl-plan-2026-05-31.md](../tracking/reply-scheduling-impl-plan-2026-05-31.md) S1。**只执行 S1。** 接缝已核实行号。纪律：D3 四列 + D1 同模式 + D2 cancel-path + D4 证据。

## 0. 目标与不做

**做**：让 `BotPairLoopGuard` 的熔断**不再要求对方是"已登记 bot"**——对任意 peer 按 per-pair 60s 滑窗识别"双向交替对刷"，达阈值进 cooldown 静默；`known_other_bots` 清单降级为"已知 bot 用更严阈值"的加权信号；enabled 默认改 true。

**不做**：不碰 RWS/B2/弱回复（S3 已迁出至 RWS plan P7）；不引入平台级 is_bot（QQ 无此标志）；不动 inbound/outbound 接线（已就绪）。

## 1. 接缝（已核实）

| 点 | 位置 | 现状 |
| --- | --- | --- |
| guard 实现 | `kernel/bot_pair_guard.py` | `_events: dict[pair_key, deque[float]]` 60s 滑窗 + `_cooldowns`；`_pair_key` 仅在 `is_known_peer` 真时建 key（缺陷点） |
| inbound 接线 | `kernel/router.py:362`（`_maybe_drop_pair_guard`，在 :1028 入口，早于 is_addressed） | 已接 |
| outbound 接线 | `services/scheduler.py:1520`（`_send_to_group` 后 `record_outbound(group_id, target_user_id)`） | 已接 |
| guard 传入 scheduler | `plugins/chat/plugin.py:1531` `if config.bot_pair_guard.enabled else None` | **enabled=False 时 guard=None**，故 S1 必须同时开 enabled |
| config | `kernel/config.py:1873` `BotPairGuardConfig` | enabled 默认 False；有 max_per_minute/cooldown_seconds/known_other_bots |

## 2. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| A1 | `_events` deque 存 `float`（仅时间戳，inbound/outbound 混计） | deque 存 `(timestamp, direction)`（"in"/"out"），以识别双向交替 | bot_pair_guard.py:26,58-68,79-91 | 改数据结构 | 单测：交替序列 vs 同向序列 |
| A2 | `_pair_key` 仅 `is_known_peer` 真时建 key（未登记 peer→None→不设防） | 对**任意 peer** 建 key（仍排除 self）；`is_known_peer` 改作"加严阈值"的查询，不再是建 key 前提 | bot_pair_guard.py:70-77 | 改门槛（S1 核心） | 单测：未登记 peer 也能熔断 |
| A3 | 熔断 = `len(history) > max_per_minute`（纯计数，含用户连发） | 熔断 = **窗口内"双向交替轮数" ≥ 阈值**（out→in→out… 才算一轮对刷；同向连发不累计交替） | bot_pair_guard.py:58-68 | 改判定 | 单测：用户连发不熔断、bot↔peer 对刷熔断 |
| A4 | 单一 `max_per_minute` 阈值 | 双阈值：`loop_alt_threshold`（任意 peer）+ `known_peer_alt_threshold`（已知 bot，更低更快熔断）；`is_known_peer` 命中走后者 | bot_pair_guard.py:_record | 改判定 | 单测：known peer 更早熔断 |
| A5 | enabled 默认 False；无交替阈值字段 | enabled 默认 **True**；加 `loop_alt_threshold`(默认6) / `known_peer_alt_threshold`(默认3)；保留 cooldown_seconds | kernel/config.py:1873 | 改 config | 默认值测试 |
| A6 | — | `config.json` 显式 `bot_pair_guard.enabled=true` + 注释（线上生效） | config/config.json | 配置 | 上线后验证 |

## 3. 交替熔断算法（A1/A3 细节）

```text
deque 存 (ts, direction)，direction ∈ {"in"(对方发给我), "out"(我发给对方)}
_prune: 同现状，60s cutoff
_count_alternations(history):
    # 数"方向翻转"次数 = 双向交替强度
    # in,out,in,out → 3 次翻转；in,in,in → 0 次翻转（用户连发，不算对刷）
    alt = 0
    for i in 1..len: if history[i].dir != history[i-1].dir: alt += 1
    return alt
熔断条件（_record 末尾）:
    threshold = known_peer_alt_threshold if is_known_peer else loop_alt_threshold
    if _count_alternations(history) >= threshold:
        _cooldowns[key] = now + cooldown_seconds
```

**为什么用"方向翻转"而非"消息计数"**：P0 现场是 bot↔peer 你来我往（out,in,out,in…，翻转密集）；而"用户连发 5 条"是 in,in,in,in,in（0 翻转）。翻转数天然区分"对刷"和"刷屏"，**这是不误伤真人/用户连发的关键**。真人和 bot 在 60s 内一来一回 6 个完整回合极罕见，阈值 6 安全。

## 4. D1 同模式扫描（编码时执行）

- `record_inbound`/`record_outbound` 全调用点（router:362 / scheduler:1520）确认传参不变（仍 group_id+peer_id），新逻辑在 guard 内部。
- `is_suppressed` 调用点（router:_maybe_drop_pair_guard）确认仍返回 bool、语义不变（熔断中→True→入口 drop）。
- `_pair_key` 的所有调用者（`is_suppressed`/`_record`）确认放开 known 后不空指针。
- `bind_self_id`（scheduler:323）确认 self 排除仍生效（self↔self 不建 key）。

## 5. 测试设计（D2 含 cancel-path）

`tests/test_bot_pair_guard.py`（若已存在则补用例）：
1. **未登记 peer 也熔断（S1 核心回归，直接复现 P0）**：self↔peerX（X 不在 known_other_bots），交替 in/out 达 `loop_alt_threshold` → `is_suppressed=True`。
2. **用户连发不误伤**：同 peer 连续 inbound（无 outbound 交替）N+ 条 → 翻转数=0 → 不熔断。
3. **known peer 更快熔断**：登记的 bot 用 `known_peer_alt_threshold`(更低) → 更早 suppress。
4. **cooldown 恢复**：cooldown_seconds 后 `is_suppressed=False`。
5. **滑窗剪枝**：60s 外的旧事件被 prune，不计入交替。
6. **self 排除**：peer==self_id → `_pair_key=None` → 不建 key。
7. **D2**：guard 纯内存同步、无 async/IO，无 cancel-path 污染；但补一条"record 抛异常被 router try/except 吞、不影响主流程"（router:362 已有 try/except，验证不向上抛）。

## 6. 缓存/性能（D4）

guard 是纯内存 deque，不进 prompt、不碰 LLM/缓存。性能：每条消息 O(滑窗长度) 的翻转扫描，60s 窗口内消息数有限，可忽略。

## 7. 灰度与回滚

- **灰度**：A1–A5 改码 + A6 开 enabled，rebuild 上线。先观察 `pair_guard_suppressed` metric（已有，block_trace store:98）频率——确认是否如期掐断 bot↔bot、且不误伤（看 suppressed 的 peer 是不是真 bot/对刷）。
- **回滚**：`config.json` `bot_pair_guard.enabled=false` → guard=None（plugin:1531）→ 完全旁路，回现状（无需 rebuild，config bind mount）。代码层 A2 的"放开 known"可加 flag `require_known_peer`（默认 false=新行为，true=旧行为）以便代码级回退。

## 8. 落地步骤

1. A1/A3：deque 存 (ts,dir) + 交替计数（bot_pair_guard.py）。
2. A2：`_pair_key` 放开 known 门槛。
3. A4：双阈值 + known 加严。
4. A5：config 字段 + enabled 默认 true。
5. 测试（§5）全绿 + 全量 pytest + ruff + pyright。
6. A6：config.json 开 enabled + 登记已知 bot（加严用，非必需）。
7. rebuild 上线 → 观察 metric。

**待确认**：① `loop_alt_threshold=6`（60s 内双向交替 6 次=约 3 个来回）/ `known_peer_alt_threshold=3` 是否合理？② 是否需要 A2 的 `require_known_peer` 回退 flag？确认后按 §8 编码。

## 9. 完成证据（D4，已回填）

**状态：2026-05-31 编码完成、已 rebuild 上线。用户定阈值：5 个来回（loop_alt=10），无回退 flag。**

- **改动**：A1 deque 存 `(ts, direction)`；A2 `_pair_key` 去 `is_known_peer` 门槛（任意 peer 建 key，仅排除 self/空）；A3 熔断改 `_count_alternations(history) >= threshold`（方向翻转计数，同向连发=0 翻转不触发）；A4 双阈值（`loop_alt_threshold`/`known_peer_alt_threshold`，is_known_peer 命中走后者）；A5 config enabled 默认 True + 两阈值字段（默认 10/6）；A6 config.json enabled=true + loop_alt=10 + known_alt=6 + 注释。无回退 flag（用户定）。
- **用户定阈值**：5 个来回 = **10 次方向翻转** → `loop_alt_threshold=10`；known peer `known_peer_alt_threshold=6`。
- **验证**：`tests/test_bot_pair_guard.py` 重写 10 用例（未登记 peer 也熔断/未达阈值不熔/同向连发不误伤/known peer 更快/self 排除/分群隔离/TTL 自愈/对称 key/滑窗剪枝/late bind）；改 `test_humanization_config.py`（enabled 默认 True + 两阈值断言）、`test_b_cluster_pipeline_e2e.py`（改用交替序列触发熔断 + 计数随实际流）。全量 `pytest` → **2295 passed, 8 skipped**；ruff/pyright clean。
- **D1 同模式**：record_inbound(router:362)/record_outbound(scheduler:1520) 接线不变；is_suppressed/_pair_key 调用者无空指针；bind_self_id self 排除生效。
- **D2 cancel-path**：guard 纯内存同步无 async/IO，router:362 已 try/except 吞异常不上抛——无污染。
- **上线**：rebuild 后启动无 error；容器内实测 `enabled=True / loop_alt=10 / known_alt=6 / _count_alternations 在`。
- **回滚**：`config.json bot_pair_guard.enabled=false` → plugin:1531 guard=None → 完全旁路回现状（config bind mount，无需 rebuild）。
- **观察**：扒 `pair_guard_suppressed` metric（block_trace store）——确认 bot↔bot 对刷被掐断、且 suppressed 的 peer 确是对刷方（非误伤真人快聊）。
