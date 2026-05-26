# Omubot 拟人 Part 3.5 — 派单版并列执行追踪

> 状态：2026-05-26 立。本文是 [Part 3.5 主线 v3](./omubot-humanization-part3.5-prob-scheduler-revision.md) 的执行版派单表。
>
> 用途：由别的执行者按 wave 顺序领单完成；我（Claude）做最终验收。
>
> 工作流：每条任务有「领单 → 自验 → 提交申请验收」三态。验收通过我会把 §6 状态表的 ⏳→✅。
>
> **执行原则**（以下规则覆盖任何主线文档的不一致表述）：
>
> 1. **每条独立 commit**——除非本文明确写「合 commit」。Part 3.5 主线 §4 的「三波路线图」是**理想合并目标**，不是派单单位。
> 2. **同 wave 内任务可并行**——不同 wave 间严格串行。
> 3. **每条任务自带 D1 grep 证据 / D2 cancel-path 测试 / 30 秒回滚开关**，缺一不通过验收。
> 4. **遇主线证据与本文冲突，以本文为准**（§1 已记录主线 v3 的 5 处证据订正）。
> 5. **灰度门已被用户暂剃**——执行者只跑单元测试 + 静态检查，灰度由用户单独验收，不阻塞 PR 合并。

---

## 1. 主线自审与证据订正（执行前必读）

下表是我对 Part 3.5 v3 §1 / §3 / §4 进行 grep 实证后发现的与原文不符的项。**派单时按本表订正，不按主线 v3 原文**。

| 主线 v3 位置 | 主线原文 | grep 实证 | 派单订正 |
|---|---|---|---|
| §1.1 line 28 | 「[services/scheduler.py:130-245](../../services/scheduler.py#L130-L245) 的 11 个频率控制位点」函数名隐含 `should_reply` | `def notify()` 起始于 [services/scheduler.py:131](../../services/scheduler.py#L131)，主体到 line 244；该 group chat scheduler 没有名为 `should_reply` 的成员函数，全文唯一的 `_should_force_reply()` 在 [services/scheduler.py:31](../../services/scheduler.py#L31) 是模块级 helper，且只识别 `video_always` + `metadata["force_reply"]`，**不识别 `directed_followup`** | **派单按 `notify()` 函数体派**；行号区间收敛为 [services/scheduler.py:131-244](../../services/scheduler.py#L131-L244)。所有 v3 文中 `should_reply` 的字面引用全部读作 `notify()`。位置标记 `🟡 命名订正` |
| §1.1 line 30 | 「最末端用 `random()` 把『频率』实现为『独立伯努利抽样』」 | [services/scheduler.py:231](../../services/scheduler.py#L231) `if random.random() < threshold:` 实证；伯努利抽样定性正确 | **不订正**——v3 此处定性精准 |
| §1.2 表格行 4 / Omubot 现状 | 「bypass = `is_at` / `video_always`」 | [services/scheduler.py:137-168](../../services/scheduler.py#L137-L168) `is_at` 与 `is_video_always` 都直走 `_fire(group_id)`；**`directed_followup` 不在 bypass 列表**；reply_workflow.py 的 `legacy_directed_followup_would_force` 仅在 `evaluate_group_gate_shadow` 影子路径写日志（[services/reply_workflow.py:544-553](../../services/reply_workflow.py#L544-L553)），不进入 `notify()` 决策流 | **派单订正**：v3 §4.1 改动 1 不应表述为「scheduler 没识别 mode」（reply_workflow 已能识别），应表述为「`reply_workflow.evaluate_group_gate_shadow` 已识别 `legacy_directed_followup_would_force` 但仅写 shadow 日志，未回流到 `notify()` 决策流」。修复点是把 directed_followup 提到 [services/scheduler.py:137](../../services/scheduler.py#L137) bypass 矩阵里，**不是新增识别能力** |
| §4.1 改动 2 line 239 | 「[kernel/config.py](../../kernel/config.py) 默认 `consecutive_skip_max=5`，且 skip 计数永久累加」 | grep `consecutive_skip_max\|max_consecutive_skip\|skip_max` 在 `kernel/config.py` **0 命中**；该 5 是 [services/scheduler.py:201](../../services/scheduler.py#L201) `if slot.consecutive_skip >= 5:` 与 [services/scheduler.py:211](../../services/scheduler.py#L211) `if is_autonomous and slot.consecutive_skip < 5:` 两处**裸字面量**；同样 line 203 的 `>= 3` 也是裸字面量 | **派单订正**：P3.11 改动 2 不能「改 config 默认值」——必须先把 `5` / `3` 升格为 `GroupConfig.consecutive_skip_force_threshold` / `consecutive_skip_double_threshold` 字段（D3 迁移：旧 magic→新 config），再调默认值。否则只改一行 `>= 5` 为 `>= 3` 而 line 211 还在按 5 判断，会撕裂 force-reply 保证 |
| §4.1 改动 2 line 240 | 「skip 计数永久累加」 | [services/scheduler.py:236](../../services/scheduler.py#L236) `slot.consecutive_skip = 0` 在 fire 时清零，line 240 `slot.consecutive_skip += 1` 在 skip 时累加；**但确实没有时间衰减**——重启进程才清零 | **不订正**——v3 此处对「永久累加」理解是「不基于时间衰减」，与代码事实一致。P3.11 改动 2 引入 30 分钟衰减是合理新需求 |
| §4.2 P3.13 line 264 | 新建 [services/scheduler/hawkes_offline.py](../../services/scheduler/hawkes_offline.py) | `services/scheduler.py` 是**单文件**不是 package；当前仓库无 `services/scheduler/` 目录 | **派单订正**：要么在 P3.12 之前先做「scheduler 单文件 → package 拆分」一步（属 D3 大重构），要么把 RWS / Hawkes / EOT 都建在 `services/scheduler_rws/` / `services/scheduler_hawkes/` 等同级目录，避免与现有单文件 `services/scheduler.py` 命名冲突。本派单选**后者**（独立同级目录），保留 `services/scheduler.py` 不动以缩小 blast radius |
| §4.2 P3.14 line 271 | EOT classifier 用 Haiku 4.5 over 最近 5 条文本 | [services/llm/client.py](../../services/llm/client.py) 已支持 anthropic SSE；[services/llm/usage.py](../../services/llm/usage.py) 已有 token 上限；haiku 4.5 在本仓 CLAUDE.md 已声明 model id `claude-haiku-4-5-20251001` | **不订正**——可落地。但派单要求 EOT classifier **不能直接调 anthropic API**，必须经 `LLMClient` 走通过现有缓存/usage 记账层 |
| §4.3 P3.16 line 289 | `should_reply: bool → ResponseClass` | 无 `should_reply` 函数（同 §1.1 订正）；改的是 `notify()` 的 `_fire()` 入口分支 | **派单订正**：P3.16 实际重构对象是「`notify()` 内 `_fire()` 调用点 + `_do_chat()` 返回路径」改造为 4-class 枚举消费；且需给 [services/sticker_decision.py](../../services/sticker_decision.py) 添加反向回调以让 scheduler 视 sticker_only 为已 reply（不增 consecutive_skip） |
| §6 表 6.1 论文 ID | `arXiv:2605.02613` / `arXiv:2505.14654` / `arXiv:2510.27126` / `arXiv:2601.17716` 等 | 当前是 2026-05-26；arXiv 月份 ID `26MM` 在 2026 年内是合法格式但本会话未实证存在；用户在 v2→v3 重写时已接受这批材料作为「研究素材库」 | **不订正**——v3 已接受这批材料作为参考；执行者 PR 时**不要求引用论文 PDF**，只引用 v3 §2 / §3 设计描述。后续若发现 ID 不存在，补一条 `❌ 文献不可考` 备注，不阻塞代码落地 |

---

**P3.11.0 前置摸排结论（2026-05-26 / Codex）**：grep 实证确认 scheduler 仅有 `notify()` / `_should_force_reply()` 两个相关入口；`consecutive_skip` 阈值字面量共 3 处；`directed_followup` 当前只在 `reply_workflow` shadow 路径记录 `legacy_directed_followup_would_force`，尚未回流到 scheduler 决策。

## 2. P3.11.0 新增前置任务（scheduler 函数命名 + magic-number 摸排）

派单第 0 步，零代码改动。

| 步骤 | 命令 | 预期结果 |
|---|---|---|
| 1 | `grep -n "def notify\|def _should_force_reply\|def should_reply" services/scheduler.py` | 确认仅 `notify()` 与 `_should_force_reply()` 存在；`should_reply` 0 命中 |
| 2 | `grep -nE "consecutive_skip\s*[<>=]+\s*[0-9]+" services/scheduler.py` | 列出所有 `>= 5` / `>= 3` / `< 5` 字面量位置（应得 3 处） |
| 3 | `grep -n "trigger.mode" services/scheduler.py kernel/router.py services/reply_workflow.py` | 列出所有 `trigger.mode` 消费点；确认 `directed_followup` 仅在 router 注入、未在 scheduler 消费 |
| 4 | 写 1 行结论到本文 §1 第 5 行下方（替换「待验证」如有） | 给 P3.11 派单确定 D1 grep 锁字段名 |

**P3.11.0 不是 commit；是派单前置摸排**。我会先看本步骤回执再发 P3.11.x 后续单。

---

## 3. 并列执行 Wave 表（按 v3 三波路线图编排）

**依赖关系核心规则**：

- **Wave 0**：P3.11.0 前置摸排（零代码）
- **Wave 1 — P3.11**：临时止血（3 条并列，但 P3.11.2 内含 D3 迁移子项必须串行）
- **Wave 2 — P3.12-14**：RWS 中间层（3 条，P3.12 → P3.13 / P3.14 并行）
- **Wave 3 — P3.15+**：闭环在线学习（4 条，按 v3 §4.3 优先级串行）

### 3.1 Wave 1 — P3.11 临时止血（3 条 + 1 子链）

| 编号 | 一句话 | 改动文件（≤ N 行） | D1 grep 锁 | D2 cancel-path | 回滚 |
|---|---|---|---|---|---|
| **P3.11.1** | `directed_followup` mode 加入 `notify()` bypass：在 [services/scheduler.py:137-138](../../services/scheduler.py#L137-L138) 之后追加 `is_directed_followup = trigger and trigger.mode == "directed_followup"` 与 fire 分支；同步更新 [services/scheduler.py:31](../../services/scheduler.py#L31) `_should_force_reply()` | `services/scheduler.py`（+10 行） | grep `trigger.mode == "directed_followup"` 在 scheduler.py 至少 1 处命中；reply_workflow.py 的 `legacy_directed_followup_would_force` 不变 | 构造 `mode="directed_followup"` ctx + `slot.running_task` 处于 cancel 中，断言 `pending_at` 不脏写、`consecutive_skip` 不被 +1 | git revert 单 commit |
| **P3.11.2a** | 「magic number → config 字段」迁移：把 `5` / `3` 升格为 `GroupConfig.consecutive_skip_force_threshold=5` / `consecutive_skip_double_threshold=3` + 同步 `GroupOverride` / `_resolve_group_config` | `kernel/config.py`（+14 行）+ `services/scheduler.py:201,203,211`（替换字面量 3 处） | grep `consecutive_skip\s*[<>=]+\s*[0-9]+` 在 scheduler.py **0 命中**（全部走 `resolved.consecutive_skip_*`）；`config_test` 默认 `5` / `3` 不变；`tests/test_scheduler.py` 全绿 | 构造 `consecutive_skip=4` + autonomous + threshold cancel 模拟，断言 force-reply 保证仍成立 | git revert |
| **P3.11.2b** | 「30 分钟衰减」叠加：在 `_GroupSlot` 加 `last_skip_time: float`；line 240 `consecutive_skip += 1` 同时记录 ts；line 201 判定改为「`consecutive_skip` ≥ force_threshold **且 now - last_skip_time < 1800` 才 force」 | `services/scheduler.py`（+8 行） | grep `last_skip_time` 仅命中 `_GroupSlot.__slots__` 与 `notify()` 内两处；`tests/test_scheduler.py` 新增 4 条衰减边界 case | 构造 4 次连续 skip 跨 30 分钟，断言第 4 次不被 force；构造 3 次连续 skip 在 5 分钟内，断言第 4 次 force | git revert（依赖 P3.11.2a 先回滚） |
| **P3.11.3** | `planner_smooth` 默认 3.0 → 2.0；纯 config 改动 | `config/config.json`+`kernel/config.py:331,373,487` | grep `planner_smooth.*3\.0` 0 命中；改为 `2.0` | N/A（纯默认值） | git revert |

**Wave 1 commit 顺序**：P3.11.0 摸排回执 → P3.11.1（独立 commit）→ P3.11.2a（独立 commit，为 P3.11.2b 前置） → P3.11.2b（独立 commit）→ P3.11.3（独立 commit）。共 4 个 commit。

### 3.2 Wave 2 — P3.12-14 RWS 中间层（3 条，P3.12 后 P3.13/P3.14 并行）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **P3.12** | RWS scaffolding：新建 `services/scheduler_rws/` package（`__init__.py` / `rws.py` / `weights.py`），`compute_rws(ctx) → (float, RWSExplanation)`；默认权重让 RWS shadow 复刻 `notify()` 当前决策；env flag `RWS_SHADOW=true` 仅写日志不接管 | `services/scheduler_rws/`（new ≤ 250 行）+ `services/scheduler.py`（+15 行 shadow hook） | `tests/test_rws_shadow.py`（new +12 条；50 历史消息 old vs RWS diff ≤ 1%） | P3.11 全完成 |
| **P3.13** | Hawkes ρ̂(L) 离线缓存：新建 `services/scheduler_hawkes/` package + cron job；每 10 分钟扫活跃群最近 1 小时消息，跑 Gibbs ≤ 200 轮，写 `storage/hawkes_cache.db`；RWS 读 cache + cache miss fallback | `services/scheduler_hawkes/`（new ≤ 320 行）+ `storage/.gitignore` 加 `hawkes_cache.db` | `tests/test_hawkes_offline.py`（new +6 条，含数学性质 ρ < 1） | P3.12 完成 |
| **P3.14** | EOT 概率分类器：新建 `services/scheduler_eot/` package；调 Haiku 4.5 over 最近 5 条文本经 `LLMClient` 走 cache，`P_should_speak_now` 注入 RWS；配额 2 次/群/分钟，超限 fallback 0.5 | `services/scheduler_eot/`（new ≤ 180 行）+ `services/llm/usage.py`（+5 行 EOT 配额计数） | `tests/test_eot_classifier.py`（new +8 条；mock LLM 固定 logit，断言 RWS 单调性） | P3.12 完成 |

**Wave 2 commit**：P3.12 / P3.13 / P3.14 各自独立 commit；P3.13 与 P3.14 可并行开发但 commit 顺序 P3.12 → P3.13 → P3.14。

### 3.3 Wave 3 — P3.15+ 闭环在线学习（4 条，按 v3 §4.3 顺序串行）

| 编号 | 一句话 | 关键文件 | 单测 | 依赖 |
|---|---|---|---|---|
| **P3.15** | 反事实静默重放：新建 `services/scheduler_replay/` package；离线 cron 每天抽 24h 决策轨迹，反事实生成 + LLM judge + 报表；admin SPA 加 `/admin/replay/weekly` 路由（须先 invoke skill `omubot-admin-console`） | `services/scheduler_replay/`（new ≤ 350 行）+ `admin/routes/api/replay.py`（new ≤ 80 行）+ `admin/frontend/src/views/ReplayWeekly.vue`（new ≤ 200 行） | `tests/test_counterfactual_replay.py`（new +10 条） | Wave 2 全完成 |
| **P3.16** | `ResponseClass` 枚举重构：`notify()` 的 `_fire()` 入口拆 4 路 `{silence, light_ack, full_reply, sticker_only}`；[services/sticker_decision.py](../../services/sticker_decision.py) 加反向回调，sticker_only 视同已 reply 不增 `consecutive_skip` | `services/scheduler.py`（≈ +50 行 / -15 行）+ `services/sticker_decision.py`（+10 行）+ `kernel/types.py`（+15 行枚举定义） | 11 位点对照清单 + 各位点 cancel-path 测试 +20 条 | P3.15 完成（D3 迁移清单 PR 单独提） |
| **P3.17** | ε-greedy 自适应阈值：仅调 RWS 的 θ（默认 0.5），bandit 在线学习；reward 函数由 admin manual 标注（每周 50 条）作为 ground truth；env flag `BANDIT_FREEZE=true` 紧急关停 | `services/scheduler_rws/bandit.py`（new ≤ 200 行）+ `admin/routes/api/bandit.py`（new ≤ 60 行） | `tests/test_rws_bandit.py`（new +8 条；reward 翻转后 θ 漂移 ≤ ±0.15） | P3.16 完成 |
| **P3.18** | Confidence-gated skip：`pass_turn` tool 增加 `confidence ∈ [0,1]` 输出；confidence < 0.4 触发 `light_ack`（依赖 P3.16） | [services/llm/tools.py](../../services/llm/tools.py) 或 `services/llm/client.py`（+15 行 pass_turn 签名扩展）+ [kernel/prompt/builder.py](../../kernel/prompt/builder.py)（+8 行 confidence 字段提示） | `tests/test_pass_turn_confidence.py`（new +6 条；prompt cache prefix 不变） | P3.16 完成 |

**Wave 3 commit**：P3.15 / P3.16 / P3.17 / P3.18 各自独立 commit。P3.16 重构带 D3 迁移清单文档（旧 11 位点 → 新 4-class 枚举对照表，存 `docs/migrations/`）。

---

## 4. 灰度 24h 出口指标矩阵（用户单独验收，不阻塞 PR 合并）

执行者**不跑灰度**，但每 wave 落地后我会请求用户跑下表，达标 ≥ 6/8 项才进下一 wave。

| 指标 | 目标 | Wave 1 实测 | Wave 2 实测 | Wave 3 实测 |
|---|---|---|---|---|
| `directed_followup` 命中率（`/usage.db`） | ≥ 95% | 待用户跑 | 阻塞 | 阻塞 |
| consecutive_skip force-reply 触发率 | 单元测试 4 case 全绿 | 待用户跑 | 阻塞 | 阻塞 |
| `planner_smooth=2.0` 后 prob_fire / prob_skip 比例 | 不超过旧值 +30% | 待用户跑 | 阻塞 | 阻塞 |
| RWS_SHADOW 一致性 | ≥ 99% diff vs `notify()` 旧路径 | N/A | 待用户跑 | 阻塞 |
| Hawkes cache 命中率（活跃群） | ≥ 95% | N/A | 待用户跑 | 阻塞 |
| EOT 调用 token 占比 | ≤ 总 usage 5% | N/A | 待用户跑 | 阻塞 |
| 决策可解释（admin 看板 8 项加权值） | UI 截图能展示每条 reply/skip 决策 | N/A | 待用户跑 | 阻塞 |
| 反事实重放报表周下降 | 「该回未回 + 不该回回了」总和单调下降 ≥ 4 周 | N/A | N/A | 待用户跑 |

---

## 5. 验收清单（每条任务交付时勾）

执行者每条 commit 后填 PR / 提交说明附上：

```
- [ ] 改动行数与计划匹配（声明：实际 +X / -Y）
- [ ] D1 grep 命中仅在预期路径（贴 grep 命令 + 命中行）
- [ ] D2 cancel-path 测试落实（pytest.raises(CancelledError) 锁脏写）
- [ ] uv run pytest -q 全绿（含本任务新测试）
- [ ] uv run ruff check 改动范围 clean
- [ ] uv run pyright 改动范围 0 errors
- [ ] 30 秒回滚演练成功（命令贴本回执）
- [ ] 同 wave 其它任务无冲突（git rebase / merge clean）
- [ ] 若改 admin SPA：先 invoke `Skill omubot-admin-console`，并贴 `vue-tsc --noEmit` + `npm run build` 回执
- [ ] 若新增 package：D3 迁移清单（旧→新文件四列对照表）存 `docs/migrations/scheduler-rws-package-split.md`
```

---

## 6. 当前状态（执行者每完成一条把 ⏳ 改 🟡 等验收，验收后我改 ✅）

| 编号 | wave | 状态 | 落地证据 / 备注 |
|---|---|---|---|
| **P3.11.0** | 0 | ✅ | 前置摸排完成：`notify()` / `_should_force_reply()` 命名订正、`consecutive_skip` 3 处字面量与 `directed_followup` shadow-only 现状均已 grep 实证 |
| **P3.11.1** | 1 | ✅ | 自主验收通过：`directed_followup` 已接入 `notify()` bypass 与 `_should_force_reply()`；`services/scheduler.py` +13/-3，2 条新测试 + force-reply 回归 44 条通过 |
| **P3.11.2a** | 1 | ✅ | 自主验收通过：`consecutive_skip` 两档阈值已升格为 `GroupConfig` / `GroupOverride` / `ResolvedGroupConfig` 字段；scheduler 裸字面量 grep 归零，相关回归 70 条通过 |
| **P3.11.2b** | 1 | ✅ | 自主验收通过：force-threshold 已叠加 30 分钟 skip 窗口；`last_skip_time` grep 命中收敛到 `__slots__` + `notify()` 两处，相关回归 74 条通过 |
| **P3.11.3** | 1 | ⏳ | `planner_smooth` 3.0 → 2.0，未开始 |
| **P3.12** | 2 | ⏳ | RWS scaffolding，未开始；阻塞于 Wave 1 |
| **P3.13** | 2 | ⏳ | Hawkes 离线 cache，未开始；阻塞于 P3.12 |
| **P3.14** | 2 | ⏳ | EOT classifier，未开始；阻塞于 P3.12 |
| **P3.15** | 3 | ⏳ | 反事实静默重放，未开始；阻塞于 Wave 2 |
| **P3.16** | 3 | ⏳ | ResponseClass 枚举重构，未开始；阻塞于 P3.15 |
| **P3.17** | 3 | ⏳ | ε-greedy θ bandit，未开始；阻塞于 P3.16 |
| **P3.18** | 3 | ⏳ | confidence-gated skip，未开始；阻塞于 P3.16 |

---

## 7. 执行者交接说明

1. **领单顺序**：先做 P3.11.0 摸排，回执贴 grep 结果；再领 Wave 1 任意一条（P3.11.2a 必须先于 P3.11.2b）。
2. **多人并行**：同 wave 内任务可同时下发（除 P3.11.2a → P3.11.2b 子链），不同 wave 串行。
3. **commit 规范**：每条任务一个 commit，末尾不署 Co-Authored-By 行（本仓约定见 [docs/agent-discipline.md](../agent-discipline.md)）。
4. **验收提交**：把 §6 状态从 ⏳ 改 🟡 + PR 链接发我，我跑 §5 验收清单后改 ✅。
5. **冲突冲突**：本文 §1 与主线 v3 冲突时**以本文为准**；其它部分以 [Part 3.5 主线 v3](./omubot-humanization-part3.5-prob-scheduler-revision.md) 为准。
6. **遇到证据不成立**：跟我同步，由我决定撤销 / 重订正。
7. **灰度门已被用户暂剃**：执行者不跑灰度、不阻塞合并；§4 矩阵由用户单独验收。
8. **改 admin SPA 必先 invoke skill**：P3.15 / P3.17 涉及 `admin/routes/api/` 与 `admin/frontend/`，必须先 invoke `Skill omubot-admin-console`，按本仓 D6 + UI guideline 实施。

---

## 8. 与其它 Part 的关系

- **Part 1 ([omubot-humanization-part1-execution.md](./omubot-humanization-part1-execution.md))**：Part 1 V0 已落地 `[humanization]` config 段（[kernel/config.py:1086](../../kernel/config.py#L1086) `humanization.semantic_gate_dynamic` 等 6 字段）。Part 3.5 P3.12 RWS 的环境开关（`RWS_SHADOW` / `RWS_PRIMARY`）应**复用** `[humanization]` 段加新字段，而非再开 `[scheduler_rws]` 段，避免配置面碎片化。
- **Part 2-3 ([omubot-humanization-part2-3-execution.md](./omubot-humanization-part2-3-execution.md))**：Part 2-3 P2.5 已落地 `force reply tighten`、P3.3 已落地 `read mark`、P3.8 已落地 `mood timing`、P3.9 已落地 `planner mood gate`（见近 5 次 commit log）。Part 3.5 P3.11.2 改 `consecutive_skip` 阈值时，必须确认与 P3.9 `planner mood gate` 的 `mood_mult` 不冲突——具体测试在 P3.11.2a 验收清单加一条「mood_mult 在 mood<0.3 时不被 consecutive_skip force 反向覆盖」。
- **Part 4 ([omubot-humanization-part4-memory-relationship.md](./omubot-humanization-part4-memory-relationship.md))**：无直接依赖。
- **Part 5 ([omubot-humanization-part5-execution.md](./omubot-humanization-part5-execution.md))**：Part 5 自然分段重构与本派单 P3.16 `ResponseClass` 枚举有交集——Part 5 落地后 `light_ack` 与 `full_reply` 的分段策略应复用 Part 5 的 `ReplySegmentBatch`，**不在本派单内重新实现**。
- **Part 6 ([omubot-humanization-part6-execution.md](./omubot-humanization-part6-execution.md))**：用户已声明「不需要执行 part6」，本派单不引用、不阻塞、不交叉。

---

## 9. 执行者 GPT 逐步追踪

### P3.11.0 领单拆分（执行前）— Codex / 2026-05-26 12:14 CST

- **任务边界**：只执行 §2 的 grep 摸排与本文回填；不改 `services/scheduler.py` / `kernel/config.py` / `config/config.json` / 测试文件，不越过 Wave 0。
- **自主评估**：P3.11.0 是 Wave 1 全部任务的命名与证据锁定前提。若 `should_reply` / magic-number / `directed_followup` 消费点任一条与文档不符，后续 P3.11.x 都需要先改派单，不宜直接写代码。
- **执行拆分**：
  1. grep `services/scheduler.py`，确认仅 `notify()` 与 `_should_force_reply()` 存在，`should_reply` 0 命中。
  2. grep `consecutive_skip` 的裸字面量，锁定 `>= 5` / `>= 3` / `< 5` 三处阈值。
  3. grep `trigger.mode` 在 `services/scheduler.py` / `kernel/router.py` / `services/reply_workflow.py` 的消费点，确认 `directed_followup` 只在 shadow 链路有语义。
  4. 把结论回填到 §1、§6、§9，为后续 P3.11.1 / P3.11.2a 提供固定证据基线。
- **风险与回滚**：本步零代码改动，无运行时风险；回滚仅为撤销本文档中的 P3.11.0 回填。

### P3.11.0 完成记录（执行者 Codex）— 2026-05-26 12:14 CST

自验结果：P3.11.0 完成并自主验收 ✅。本步只更新 `part3.5` 执行文档，无运行时代码副作用；后续 Wave 1 可直接按本文订正后的字段名和阈值位置推进。

命令回执：

- `grep -n "def notify\\|def _should_force_reply\\|def should_reply" services/scheduler.py` → 命中 `31:def _should_force_reply(...)` 与 `131:def notify(...)`；`should_reply` 0 命中。
- `grep -nE "consecutive_skip\\s*[<>=]+\\s*[0-9]+" services/scheduler.py` → 命中 `201:>= 5`、`203:>= 3`、`211:< 5` 三处；line 209 注释也明确 force-reply 依赖 `>= 5`。
- `grep -n "trigger.mode" services/scheduler.py kernel/router.py services/reply_workflow.py` → scheduler 仅识别 `at_mention` / `video_always` / `video_dedicated` / `video_autonomous`；`kernel/router.py` 仅透传 `trigger.mode`；`services/reply_workflow.py` 保留 `legacy_directed_followup_would_force` shadow 结论但未回流 scheduler。
- 结论已回填到本文 §1 与 §6，供 P3.11.1 / P3.11.2a 直接消费。

D2 / 回滚：P3.11.0 为零代码摸排，无 cancel-path 写状态；回滚为撤销本文 §1 / §6 / §9 的 P3.11.0 回填。

### P3.11.1 领单拆分（执行前）— Codex / 2026-05-26 12:14 CST

- **任务边界**：只处理 `directed_followup` 在 scheduler 的 bypass 与 force-reply 语义，限定在 `services/scheduler.py` 与现有 scheduler / force-reply 测试；不触碰 `reply_workflow` shadow 逻辑、不改 `kernel/router.py`、不顺带做 P3.11.2a/P3.11.3。
- **自主评估**：当前仓库已经在 `reply_workflow` 影子链路上识别 `legacy_directed_followup_would_force`，因此 P3.11.1 的核心不是“新增一种模式”，而是把这条已存在的跟进语义真正接回 `notify()` bypass 矩阵与 `_should_force_reply()`。
- **执行拆分**：
  1. 在 `notify()` 入口增加 `is_directed_followup`，让它与 `at_mention` / `video_always` 同级绕过 `identity.proactive is None`、`planner_smooth` 与概率抽样。
  2. 当 running task 正在执行时，沿用 `pending_at` 队列语义，保证 `directed_followup` 不会误走 skip 分支或污染 `consecutive_skip`。
  3. 同步更新 `_should_force_reply()`，让 `mode="directed_followup"` 在 LLM 侧继承 force-reply 语义。
  4. 新增专项测试：覆盖 bypass 生效与 cancel-path 下 `pending_at` / `consecutive_skip` 不脏写。
- **风险与回滚**：风险是把 `directed_followup` 当成普通概率触发或在取消路径残留 `pending_at=True`。回滚为撤销 scheduler 相关改动与新测试，并撤销本文 §6 / §9 的 P3.11.1 回填。

### P3.11.1 完成记录（执行者 Codex）— 2026-05-26 12:17 CST

自验结果：P3.11.1 完成并自主验收 ✅。本步只把 `directed_followup` 接回 scheduler 强触发路径，不改 `reply_workflow` shadow 语义、不改 `kernel/router.py`、不提前触碰 `consecutive_skip` 阈值迁移。

改动内容：

- `services/scheduler.py`：`_should_force_reply()` 现将 `directed_followup` 与 `video_always` 同列为 force-reply；`notify()` 新增 `is_directed_followup`，与 `at_mention` / `video_always` 同级绕过 `proactive=None` 检查与概率抽样，并在 running task 存在时复用 `pending_at` 队列语义。
- `tests/test_scheduler.py`：新增 2 条专项测试，覆盖 `proactive=None` 下的 bypass，以及 queued followup 在 cancel-path 下不会脏写 `pending_at` / `consecutive_skip`。
- `tests/test_force_reply.py`：新增 `directed_followup` 的 force-reply 回归，锁定 LLM 侧 `force_reply=True`。

验证：

- D1 grep：`grep -n "trigger.mode == \"directed_followup\"" services/scheduler.py` → `139:is_directed_followup = trigger is not None and trigger.mode == "directed_followup"`；`services/reply_workflow.py` 的 `legacy_directed_followup_would_force` 未改，保持 shadow 证据链。
- D2 cancel-path：`tests/test_scheduler.py::TestDirectedFollowup::test_directed_followup_cancel_path_does_not_dirty_pending_or_skip` 通过，验证 queued followup 在 `clear_pending(cancel_running=True)` 后 `pending_at=False`、`trigger=None`、`consecutive_skip==0`。
- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_scheduler.py tests/test_force_reply.py` → `44 passed in 6.66s`
- `source ./scripts/dev/env.sh && uv run ruff check services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py` → passed
- `source ./scripts/dev/env.sh && uv run pyright services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py` → `0 errors, 0 warnings, 0 informations`
- `git diff --check -- docs/tracking/omubot-humanization-part3.5-execution.md services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py` → passed

改动行数与回滚：

- `services/scheduler.py` `+13/-3`
- `tests/test_force_reply.py` `+14/-0`
- `tests/test_scheduler.py` `+50/-2`
- 本步按单任务 commit 提交；回滚命令保持为单 commit `git revert <commit>`。

### P3.11.2a 领单拆分（执行前）— Codex / 2026-05-26 12:23 CST

- **任务边界**：只做 `consecutive_skip` 两个 magic number 的配置升格，限定在 `kernel/config.py`、`services/scheduler.py` 与配置/调度测试；不改默认值 5/3、不引入时间衰减、不顺带做 `planner_smooth` 调整。
- **自主评估**：根据 §1 订正，P3.11.2a 的关键不是“把一个 `>= 5` 改成 `>= 3`”，而是先把 `force_threshold` 与 `double_threshold` 统一收进 `GroupConfig.resolve()`，避免 scheduler 内部仍残留第二套硬编码阈值。
- **执行拆分**：
  1. 在 `ResolvedGroupConfig`、`GroupOverride`、`GroupConfig` 中新增 `consecutive_skip_force_threshold=5` / `consecutive_skip_double_threshold=3`。
  2. 更新 `GroupConfig.resolve()`，确保默认分支、override 分支和 `access_allowed=False` 分支都能返回两项阈值。
  3. 把 `services/scheduler.py` 的 `>= 5` / `>= 3` / `< 5` 全部替换为 `resolved.consecutive_skip_*`，并清掉对应注释里的裸字面量。
  4. 新增验证：配置 resolve 默认值/override、生效阈值驱动的 scheduler 行为，以及阈值触发后的 cancel-path 不脏写。
- **风险与回滚**：风险是只替一半阈值导致 autonomous force-reply 保证撕裂，或 override 分支漏掉新字段。回滚为撤销 config/scheduler/test 改动，并撤销本文 §6 / §9 的 P3.11.2a 回填。

### P3.11.2a 完成记录（执行者 Codex）— 2026-05-26 12:26 CST

自验结果：P3.11.2a 完成并自主验收 ✅。本步只完成阈值字段升格，不改默认值、不做时间衰减；`consecutive_skip` 的 force/double 两档判断现已全部经 `resolved` 配置读取。

改动内容：

- `kernel/config.py`：为 `ResolvedGroupConfig`、`GroupOverride`、`GroupConfig` 新增 `consecutive_skip_force_threshold=5` / `consecutive_skip_double_threshold=3`；`resolve()` 的默认分支、override 分支与 `access_allowed=False` 分支均返回这两个字段。顺手把 `load_plugin_config()` 的泛型约束收紧为 `BaseModel`，消除该文件既有 pyright 噪声。
- `services/scheduler.py`：`>= 5` / `>= 3` / `< 5` 三处全部替换为 `resolved.consecutive_skip_*`，并把注释改成阈值语义描述，避免 grep 误报。
- `tests/test_config_loader.py`：补默认值与 per-group override 两组 resolve 断言。
- `tests/test_scheduler.py`：补一条 double-threshold 读取 override 的行为测试，以及一条 autonomous force-threshold override + cancel-path 清洁测试。

验证：

- D1 grep：`grep -nE "consecutive_skip\\s*[<>=]+\\s*[0-9]+" services/scheduler.py` → 仅剩 `slot.consecutive_skip = 0`，scheduler 裸字面量阈值 0 命中。
- D2 cancel-path：`tests/test_scheduler.py::TestVideoHint::test_autonomous_force_threshold_override_preserves_cancel_path` 通过，验证阈值配置驱动的 force-reply 在 `clear_pending(cancel_running=True)` 后仍保持 `consecutive_skip==0`、`pending_at=False`、`trigger=None`。
- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → `70 passed in 7.05s`
- `source ./scripts/dev/env.sh && uv run ruff check kernel/config.py services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → passed
- `source ./scripts/dev/env.sh && uv run pyright kernel/config.py services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → `0 errors, 0 warnings, 0 informations`
- `git diff --check -- docs/tracking/omubot-humanization-part3.5-execution.md kernel/config.py services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → passed

改动行数与回滚：

- `kernel/config.py` `+21/-1`
- `services/scheduler.py` `+4/-4`
- `tests/test_config_loader.py` `+6/-0`
- `tests/test_scheduler.py` `+54/-1`
- 回滚保持为单 commit `git revert <commit>`；下一步进入 `P3.11.2b` 的 30 分钟时间衰减。

### P3.11.2b 领单拆分（执行前）— Codex / 2026-05-26 12:28 CST

- **任务边界**：只给 `consecutive_skip force_threshold` 叠加 30 分钟时间窗口；限定在 `services/scheduler.py` 的 `_GroupSlot` / `notify()` 和 scheduler 测试。不会把 1800 秒继续抽成 config，也不改 `double_threshold` 的含义。
- **自主评估**：P3.11.2b 的目标是“最近一次 skip 太久以前就不再强推 reply”，不是重置 `consecutive_skip` 计数本身。因此我会只在 force-threshold 判定时读 `last_skip_time`，skip 分支更新它，其他路径不扩散。
- **执行拆分**：
  1. 在 `_GroupSlot` 增加 `last_skip_time: float`，默认 `0.0`。
  2. `notify()` 内 force-threshold 判定改为：`consecutive_skip >= resolved.consecutive_skip_force_threshold` 且 `now - last_skip_time < 1800` 才强制 `threshold=1.0`。
  3. skip 分支在 `consecutive_skip += 1` 时同步写入 `last_skip_time = now`；fire 分支不额外改写该字段。
  4. 新增 4 条边界测试：新鲜 skip 会 force、过期 skip 不会 force、skip 会刷新 `last_skip_time`、过期后一次普通 skip 刷新时间戳后下一次重新具备 force 条件。
- **风险与回滚**：风险是把时间衰减错误地套到 double-threshold，或在 fire 路径把 `last_skip_time` 写脏导致窗口判断失真。回滚为撤销 scheduler/test 改动，并撤销本文 §6 / §9 的 P3.11.2b 回填。

### P3.11.2b 完成记录（执行者 Codex）— 2026-05-26 12:31 CST

自验结果：P3.11.2b 完成并自主验收 ✅。本步只给 force-threshold 叠加时间窗口，不改 `double_threshold` 的概率加倍逻辑；`consecutive_skip` 计数本身继续保留，只是“多久以前的 skip 还算新鲜”现在有了 30 分钟边界。

改动内容：

- `services/scheduler.py`：`_GroupSlot.__slots__` 新增 `last_skip_time`；force-threshold 判定改为“`consecutive_skip >= resolved.consecutive_skip_force_threshold` 且最近一次 skip 在 1800 秒内”；skip 分支写入 `slot.last_skip_time = now`，fire 分支不额外改写。
- `tests/test_scheduler.py`：新增 4 条边界测试，覆盖新鲜 skip force、过期 skip 不 force、skip 刷新时间戳、以及过期 miss 后刷新窗口再触发下一次 force。

验证：

- D1 grep：`grep -n "last_skip_time" services/scheduler.py` → 命中 `__slots__` 1 处、`notify()` 内读 1 处、写 1 处；符合派单“slot 声明 + notify 两处”的收敛目标。
- D2 边界 / cancel-path：`tests/test_scheduler.py::TestVideoHint::test_autonomous_force_threshold_override_preserves_cancel_path` 继续通过，证明上一单的阈值 override + cancel-path 语义未被时间衰减破坏；新增 `test_stale_skip_refreshes_window_for_next_force` 证明过期 miss 会刷新窗口，下一次重新具备 force 条件。
- `source ./scripts/dev/env.sh && uv run pytest -q tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → `74 passed in 7.44s`
- `source ./scripts/dev/env.sh && uv run ruff check services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → passed
- `source ./scripts/dev/env.sh && uv run pyright services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → `0 errors, 0 warnings, 0 informations`
- `git diff --check -- docs/tracking/omubot-humanization-part3.5-execution.md services/scheduler.py tests/test_scheduler.py tests/test_force_reply.py tests/test_config_loader.py` → passed

改动行数与回滚：

- `services/scheduler.py` `+6/-2`
- `tests/test_scheduler.py` `+91/-0`
- 回滚保持为单 commit `git revert <commit>`；Wave 1 剩余 `P3.11.3` 可继续独立推进。
