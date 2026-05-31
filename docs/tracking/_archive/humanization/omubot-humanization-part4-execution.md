# Omubot 拟人 Part 4 — 记忆与关系系统 Execution

> 状态：2026-05-29 修订版派单。基于 v2-修订版计划，经代码库审计修正 7 处过时假设后生成。
> 前置依赖：Part 3.5 v3 RWS scaffolding 已落盘（`services/scheduler_rws/rws.py` 含 `compute_rws` + `RWSExplanation`）。
> 执行者引导：本文件是**逐步执行指南**，每个子任务包含触点文件、改动描述、验证条件、回滚路径。按编号顺序执行，每完成一个子任务立即跑验证，不要跳步。

---

## 审计修正记录（执行前必读）

| # | 原计划假设 | 审计发现 | 修正 |
|---|---|---|---|
| 1 | CardStore 有 `cross_group_visible` | CardStore 无此列；该列在 episodic/slang/style/graph/normalizer 5 store | 触点改为 5 store |
| 2 | `search_episodes()` 方法存在 | 不存在；只有 `list_episodes()` / `list_for_recall()` | 新建方法或改 `list_for_recall()` |
| 3 | Dream agent 在 `services/dream_agent.py` | 实际在 `plugins/dream/plugin.py` | 路径修正 |
| 4 | `state_board.py` 提供 mood 信号 | state_board 无 mood；mood 来自 affection engine → `plugins/schedule/mood.py` | 数据源修正 |
| 5 | RWS 新增 `w_mood` | RWS 已有 `mood_mult` / `mood_residual` | 合并为 `mood_residual` 增强，不新增独立项 |
| 6 | `services/scheduler/memory_signals.py` | 无此目录；RWS 在 `services/scheduler_rws/` | 路径改为 `services/scheduler_rws/memory_signals.py` |
| 7 | Promoter 是 provenance 统一写入点 | Promoter 只创建 episode；card 创建分散在 dream plugin / food plugin / 手动 | 需 grep 所有 `add_card` 调用点 |

## 2026-05-29 执行回填（已完成）

### 状态表

| Wave | 子项 | 状态 | 结果 |
|---|---|---|---|
| Wave 1 | P4.11.1 provenance 三列 | 已完成 | `services/memory/card_store.py` 已加 `source_msg_id` / `captured_at` / `captured_by`，并回填到 `food` / `memo` / `llm client` / `memory migrate` 等 `add_card` 调用点 |
| Wave 1 | P4.11.2 include_decayed | 已完成 | `services/episodic/store.py:list_for_recall(..., include_decayed=False)` 落地，默认行为保持不变 |
| Wave 1 | P4.11.3 cross_group_visibility | 已完成 | `services/cross_group.py` + episodic/slang/style/graph/normalizer 五套 store 全部切到 enum 映射 |
| Wave 1 | P4.11.4 CER willingness episodic 注入 | 已完成 | `services/persona/willingness.py` 新增 `recent_outcomes` 与 `episodic_situation_lookup()`，在 `plugins/chat/plugin.py:on_message()` 接线 |
| Wave 2 | P4.12.1 memory_signals readers | 已完成 | 新增 `services/scheduler_rws/memory_signals.py`，含 `recent_outcome_ratio` / `familiarity_score` / `willingness_phase_score` / `mood_trend` |
| Wave 2 | P4.12.2 RWS 扩展 3 项 | 已完成 | `RWSFeatures` / `RWSWeights` / `compute_rws()` 已纳入 `outcome_ratio` / `familiarity` / `willingness_phase`，`RWS_MEMORY_COUPLING=false` 可一键归零 |
| Wave 2 | P4.12.3 EBR 2 信号触发 | 已完成 | 新增 `EventBoundaryDetector`，`plugins/dream/plugin.py` 已接 silence + mood reversal 两类触发，并保留 `EBR_ENABLED=false` kill switch |

### 实施订正

- `cross_group_visible` 的兼容映射以旧行为优先，最终落地为 `0 -> none`、`1 -> opt_out`、`2 -> opt_in`；本文下方原示例已同步修正。
- willingness 运行时写入没有放进 scheduler 异步链路，而是放在 `plugins/chat/plugin.py:on_message()` 同步缓存到 `ctx.memory_relation_signals`，scheduler 只消费缓存。
- `RWS_MEMORY_COUPLING` 落在 scheduler 层做 3 项权重归零，`compute_rws()` 仍保持纯函数。
- `mood_trend` 没有新增独立 mood 权重，而是并入既有 `mood_residual` 路径；`DreamPlugin.on_tick()` 先看 EBR，再走周期 gate。

### 验证证据

- `source ./scripts/dev/env.sh && uv run ruff check plugins/chat/plugin.py services/cross_group.py services/persona/willingness.py services/slang/types.py services/memory_consolidator/__init__.py tests/test_chat_plugin_humanization_wire.py tests/test_scheduler_rws.py kernel/types.py`
  - 结果：`All checks passed!`
- `source ./scripts/dev/env.sh && uv run pyright plugins/chat/plugin.py tests/test_scheduler_rws.py kernel/types.py`
  - 结果：`0 errors, 0 warnings, 0 informations`
- `source ./scripts/dev/env.sh && uv run pytest tests/test_card_store.py tests/test_episode.py tests/test_cross_group.py tests/test_willingness.py tests/test_memory_signals.py tests/test_chat_plugin_humanization_wire.py tests/test_rws.py tests/test_scheduler_rws.py tests/test_event_boundary.py tests/test_graph_writer.py tests/test_dream.py -q`
  - 结果：`155 passed`
  - 备注：`tests/test_dream.py` 伴随 4 条既有 `aiosqlite` 线程关闭 warning，不影响断言通过
- `source ./scripts/dev/env.sh && uv run pytest -q`
  - 结果：`7 failed, 2111 passed, 8 skipped`
  - 失败集：`tests/test_llm_pipelines.py`、`tests/test_llm_request.py`、`tests/test_llm_task_admin_sync.py`
  - 判定：均为 `episode_review` / `fact_review` / `style_review` 未同步进 LLM pipeline / admin task 列表的既有漂移，和 Part 4 触点无交集

---

## Wave 1 — P4.11 系列：低风险止血

### P4.11.1 CardStore provenance 三列

**问题**：memo card 没记录来源消息、抽取时间、抽取者——debug 无法追溯。

**触点文件**：
- `services/memory/card_store.py` — schema 加三列
- 所有 `add_card` / `create_card` 调用点（执行前先 grep）

**改动**：

1. `card_store.py` 的 `_CREATE_TABLE` 加：
   ```sql
   source_msg_id TEXT DEFAULT NULL,
   captured_at TEXT DEFAULT NULL,
   captured_by TEXT NOT NULL DEFAULT 'unknown'
   ```
2. `add_card()` 方法签名加三个可选参数，写入对应列。
3. grep 所有 `add_card` 调用点，能传 source 信息的传入（dream plugin 的 `_execute_tool` 有 msg context）；无法传的保持 default。
4. 旧数据不迁移（NULL = legacy）。

**验证**：
```bash
uv run pytest tests/test_card_store.py -v  # 新 card 三列非空
sqlite3 storage/memory_cards.db "PRAGMA table_info(memory_cards)" | grep source_msg_id
```

**回滚**：单 commit revert；三列 NULL-allowed 不破坏现有查询。

---

### P4.11.2 EpisodeStore include_decayed 召回

**问题**：decay 后的 episode 无法被长程召回（"上个月那个事"查不到）。

**触点文件**：
- `services/episodic/store.py` — `list_for_recall()` 方法

**改动**：

1. `list_for_recall()` 加参数 `include_decayed: bool = False`。
2. 当 `include_decayed=True` 时，WHERE 条件不排除 `episode_state = 'disabled'`（decay 后的状态）。
3. 所有现有调用点不传此参数 → 行为不变。

**注意**：计划原文引用 `search_episodes()` 方法——该方法不存在。实际改动目标是 `list_for_recall()`。如需独立的语义搜索方法，在 Wave 2 CER 注入时再建。

**验证**：
```python
# tests/test_episode_include_decayed.py
# 构造 1 条 episode_state='disabled' 的 episode
# 断言 list_for_recall(include_decayed=False) 返回 0 条
# 断言 list_for_recall(include_decayed=True) 返回 1 条
```

**回滚**：单参数 revert，默认值保证无行为变化。

---

### P4.11.3 cross_group_visibility enum 三值化

**问题**：`cross_group_visible: bool` 二值——开了全暴露，关了全屏蔽。

**触点文件**（5 个 store，不是 CardStore）：
- `services/episodic/store.py`
- `services/slang/store.py`
- `services/style/store.py`
- `services/knowledge_graph/store.py`
- `services/learning_normalizer/store.py`

**改动**：

1. 每个 store 的 schema 保持 `cross_group_visible INTEGER` 不变（SQLite 无 enum）。
2. 新建 `services/cross_group.py`（或 `kernel/cross_group.py`）定义：
   ```python
   CrossGroupVisibility = Literal["none", "opt_out", "opt_in"]
   # none = 0, opt_out = 1, opt_in = 2
   ```
3. 各 store 的读写方法改为使用 enum 映射（0→none, 1→opt_out[旧 true], 2→opt_in[新]）。
4. 旧数据：`1 → opt_out`（原来 visible=true 的保持最宽松）；`0 → none`。
5. 同模式扫描（D1）：`grep -rn "cross_group_visible" --include="*.py"` 所有位点确认全部迁移。

**验证**：
```bash
grep -rn "cross_group_visible" --include="*.py" | grep -v "__pycache__"
# 确认所有位点使用新 enum 映射
uv run pytest tests/ -k "cross_group" -v
```

**回滚**：enum 映射层独立于 schema；revert 代码即回退到 bool 语义。

---

### P4.11.4 CER willingness episodic 注入

**问题**：willingness 5-stage 决策不用历史 outcome——bot 被冷处理后还硬聊。

**触点文件**：
- `services/persona/willingness.py` — 当前纯规则，无 LLM / 无 episodic

**改动**：

1. 新建 helper `episodic_situation_lookup(episode_store, group_id, situation_text) → list[Episode]`：
   - 从 EpisodeStore 按 group_id 查最近 50 条 enabled_for_prompt episode
   - 按 situation 文本相似度（简单 token overlap 或 embedding）取 top-3（相似度 > 0.5 且 outcome_signal 非空）
2. `willingness_stage()` 扩展：接收可选 `recent_outcomes: list[str]` 参数。
   - 如果 recent_outcomes 中 negative 占比 > 60%，stage 向 withdraw 方向偏移一档。
   - 如果 positive 占比 > 60%，stage 向 close 方向偏移一档。
3. 调用点（`plugins/chat/plugin.py` 或 scheduler）传入 episodic lookup 结果。

**注意**：当前 `willingness_stage()` 是纯函数（4 个数值输入）。改动后仍保持可测试性——新参数可选，不传时行为不变。

**验证**：
```python
# tests/test_willingness_cer.py
# mock 3 条 outcome=negative episode → 断言 stage 偏移
# mock 3 条 outcome=positive episode → 断言 stage 偏移
# 不传 recent_outcomes → 断言行为与旧版一致
```

**回滚**：参数可选，不传即回退。

---

### Wave 1 完成判据

```bash
uv run pytest -q          # 全绿
uv run ruff check         # 全绿
uv run pyright            # 全绿（或 0 new errors）
```

维护日志一条："Wave 1 上线：provenance + include_decayed + visibility enum + CER 注入"。
同模式扫描（D1）：grep `cross_group_visible` / `add_card` 所有位点确认。
回滚：4 条 commit 各自独立 revert。

---

## Wave 2 — P4.12 系列：RWS 跨层联动 + EBR 信号触发

> 前置：Wave 1 全部落地 + Part 3.5 v3 P3.12 RWS scaffolding 已在 `services/scheduler_rws/rws.py`。

### P4.12.1 memory_signals reader helpers

**触点文件**：
- 新建 `services/scheduler_rws/memory_signals.py`

**改动**：

4 个 reader 函数，全部读现成数据，不新建表：

```python
async def recent_outcome_ratio(episode_store, group_id: str, hours: int = 24) -> float:
    """最近 N 小时 episode outcome positive 占比，[0, 1]。"""

async def familiarity_score(card_store, target_id: str, cap: int = 50) -> float:
    """target 相关 card 数 / cap，[0, 1]。"""

async def willingness_phase_score(stage: str) -> float:
    """5-stage → [0, 1] 映射。stranger=0.2, acquaint=0.4, familiar=0.6, close=0.8, withdraw=0.1。"""

async def mood_trend(mood_engine, group_id: str) -> float:
    """最近 30min mood valence 变化趋势，[-1, 1] 归一化到 [0, 1]。"""
```

**注意**：
- `mood_trend` 读 `MoodEngine`（`plugins/schedule/mood.py`），不是 `state_board.py`。
- 不新增独立的 `w_mood` RWS 项——合并到已有的 `mood_residual` 权重中（避免重叠）。

**验证**：每函数 2-3 case mock 测试，断言返回值 ∈ [0, 1]。

---

### P4.12.2 RWS 公式扩展 3 项

**触点文件**：
- `services/scheduler_rws/rws.py` — `RWSFeatures` + `RWSWeights` + `compute_rws()`
- `services/scheduler_rws/weights.py` — 默认权重

**改动**：

1. `RWSFeatures` 加 3 字段：`outcome_ratio`, `familiarity`, `willingness_phase`。
2. `RWSWeights` 加 3 字段：`outcome`, `familiarity`, `willingness`（默认 0.05-0.1）。
3. `compute_rws()` 累加 3 项。
4. `RWSExplanation.terms` 自然包含新项（已有的 dict 结构）。
5. **不新增 mood 项**——已有 `mood_residual` 覆盖；`mood_trend` reader 的输出喂给现有 `mood_residual` 计算。
6. env flag：`RWS_MEMORY_COUPLING=false` 让 3 项权重 = 0。

**验证**：
```bash
# shadow log 含新 3 项加权值
sqlite3 storage/usage.db "SELECT ... FROM rws_decisions ORDER BY ts DESC LIMIT 5"
# env flag 测试：设 false 后 3 项 = 0
```

**回滚**：env flag 瞬时回退。

---

### P4.12.3 EBR 2 信号触发 dream cycle

**触点文件**：
- 新建 `services/memory_consolidator/event_boundary.py`
- `plugins/dream/plugin.py` — DreamPlugin 的 `_loop()` / `on_tick()`

**改动**：

1. `event_boundary.py`：
   ```python
   class EventBoundaryDetector:
       async def check_silence(group_id, message_log, threshold_min=30) -> bool
       async def check_mood_reversal(mood_engine, group_id, variance_threshold=0.4) -> bool
   ```
   - cooldown 30min：同一 group 30min 内不重复触发。

2. `plugins/dream/plugin.py` 的 `on_tick()`：
   - 在时钟触发之外，额外检查 `EventBoundaryDetector` 的 2 信号。
   - 命中时立即触发 consolidate（不等下个时钟 tick）。

3. env flag：`EBR_ENABLED=false` 禁用信号触发（保留时钟兜底）。

**验证**：
```python
# tests/test_event_boundary.py
# silence > 30min + 队列非空 → 触发
# 平稳序列 → 不触发
# cooldown 内重复信号 → 不触发
```

**回滚**：env flag 独立禁用；时钟兜底不受影响。

---

### Wave 2 完成判据

```bash
uv run pytest -q && uv run ruff check && uv run pyright
```

- RWS 决策日志可见 3 项新加权值。
- EBR 触发：手工构造 silence / mood reversal 场景各 1，确认触发。
- dream cycle 总耗时增长 ≤ 10%。
- 维护日志记录 Wave 2 上线。
- 回滚：`RWS_MEMORY_COUPLING=false` / `EBR_ENABLED=false` 各自独立瞬时回退。

---

## 执行者注意事项

1. **同模式扫描（D1）**：每个子任务完成后，grep 同代码库找"同模式位点"。例如 P4.11.3 改 `cross_group_visible` 时，必须扫描所有 5 个 store + admin API + 前端引用。

2. **cancel-path 测试（D2）**：P4.12.3 EBR 的 `on_tick` 被 `wait_for` 包裹，必须有 `pytest.raises(asyncio.CancelledError)` 回归测试。

3. **不要过度工程**：本版已砍掉 8 项 ROI 负向改动。如果执行中发现某项改动需要 > 200 行新代码或新建 > 2 个文件，停下来重新评估 ROI。

4. **mood 数据源**：mood 来自 `plugins/schedule/mood.py` 的 `MoodEngine`，不是 `services/memory/state_board.py`。state_board 只有 active_users / topics / frequency。

5. **RWS 扩展不要重复 mood**：已有 `mood_mult` + `mood_residual`。新增的 `mood_trend` reader 输出应喂给现有 `mood_residual` 计算路径，不要新建第三个 mood 权重项。

6. **Provenance 写入点分散**：CardStore 的 `add_card` 被多处调用。执行 P4.11.1 前先跑：
   ```bash
   grep -rn "add_card\|create_card" --include="*.py" | grep -v __pycache__ | grep -v test
   ```
   确认所有调用点都传入 provenance 参数。

7. **Wave 2 依赖 Part 3.5 v3**：如果 `services/scheduler_rws/rws.py` 的 `RWSFeatures` / `RWSWeights` 结构有变，先同步再改。

---

## 不做的事（明示）

以下 8 项在 v2-修订版中已明确砍除，执行者不要自行恢复：

- HEG 超图 + entity_vertices + DWP 检索
- entity resolution 跨群同人识别
- EBR generative counterfactual replay
- ε-budget + scope_transfer_ledger
- MEXTRA 红队 panel
- Chronos SVO 子表
- BDI 子表 + isotonic 校准 + 标注队列
- 5 信号 EBR（只保留 2 信号）
