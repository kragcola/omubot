# Omubot 黑话模块修复方案报告

日期：2026-05-10  
对应审计：`docs/audits/slang-module-audit-2026-05-10.md`

## 一、目标

这份方案的目标不是重写黑话模块，而是在保持现有功能边界不扩张的前提下，优先修复已经确认的：

1. 调度与状态一致性问题
2. term / alias 去重缺陷
3. 高频消息热路径的读写压力
4. 短词与 evidence 匹配过宽导致的数据质量问题
5. daily review 的可观测性与可恢复性

本轮不做的事：

- 不重做 Admin 页面
- 不引入新数据库
- 不改 NapCat / router 主流程
- 不把黑话系统升级成 embedding-first 或重型 RAG

## 二、修复原则

1. **先修一致性，再修性能。**  
   如果状态机会错，再快也会错得更快。

2. **先收紧数据约束，再调模型效果。**  
   `pjsk` 这类问题本质是约束缺失，不是 prompt 不够聪明。

3. **先加观测，再做更细的优化。**  
   daily review、message hit、auto approve 的日志要先能对账。

4. **保持现有业务语义。**  
   这轮修复不改变“候选 -> 审核 -> approved 注入”的主流程。

## 三、分阶段方案

### Phase 1：修 daily review 状态机

#### 目标

解决：

- `last_daily_ai_review_date` 提前写入
- stale `running` run 无恢复
- daily review 与 manual extract 共用单个 50 秒超时

#### 改动建议

1. 调整 `run_daily_ai_review_if_due()`：
   - 不要在执行前写 `last_daily_ai_review_date`
   - 仅在 `run_daily_ai_review()` 成功完成后写入

2. 新增运行态 meta：
   - `last_daily_ai_review_started_at`
   - `last_daily_ai_review_run_id`
   - 可选 `last_daily_ai_review_status`

3. 在 daily review 入口增加 stale recovery：
   - 以 `slang_extraction_runs` 为准，不只依赖 meta key
   - 查找 `kind=daily_ai_review`、`status=running`、且 `started_at` 超出阈值（如 10 分钟）的记录
   - 先把旧 run 标记为 `failed` 或 `abandoned`
   - 再允许今天重新执行

4. 拆分 tick 超时：
   - daily review 单独超时
   - manual extract 单独超时
   - 不再共享一个 50 秒包裹

5. 时间判断改为数值比较：
   - 将 `HH:MM` 解析成分钟数
   - 避免继续依赖字符串字典序，尽管当前零填充格式下逻辑可工作

#### 验收条件

- daily review 失败后，同一天后续 tick 仍可补跑
- 中断后不会永久卡 `already_ran`
- `slang_extraction_runs` 不再长期残留无解释的 `running`

### Phase 2：修 term + alias 全量碰撞

#### 目标

解决：

- 新 term 撞旧 term / alias
- 新 alias 撞旧 term / alias
- pending promote 时 alias 冲突漏检

#### 改动建议

1. 引入统一 collision helper，例如：

```text
normalized_keys = {normalize(term)} U {normalize(alias_i)}
```

边界要求：

- 这里只做 `normalize` 后的精确 key 碰撞
- 不在写入路径引入语义相似度判断
- 语义上的“可能同义”继续留给 drift review / daily review 处理

2. 为以下入口统一使用 collision 检查：

- `create_term()`
- `upsert_candidate()`
- `upsert_ai_approved_term()`
- `_promote_pending_candidate()`

3. 明确冲突策略：

- 主 key 精确碰撞：合并到 existing 或更新 existing
- alias key 撞 existing term / alias：优先并入 existing；若语义字段冲突，则转人工审核或 drift 风格队列
- 无精确碰撞但语义上疑似相关：本阶段不在写入路径处理
- 明显冲突：拒绝写入并记录原因

4. 新增冲突返回结构，而不是只返回 `term_id | None`

建议形态：

```text
{ action: "created" | "merged" | "rejected" | "unchanged", term_id: "...", reason: "..." }
```

目的：

- 调用方能区分“新建成功 / 合并已有 / 拒绝写入 / 无变化”
- 便于日志、测试和后台 run 统计

5. 覆盖 pending promote 的 existing 命中路径：
   - `_promote_pending_candidate()` 命中已有 term 时
   - 不能只删除 pending 并返回 term_id
   - 需要把 pending 的 aliases、必要 meta 和观察结果合并到 existing

#### 验收条件

- 同群内不再允许 `pjsk` 既是 term 又是另一个 term 的 alias
- pending 到 candidate 的晋升路径也能拦住 alias 冲突
- 管理端查询和 prompt 注入不再出现这类重复词条

### Phase 3：降低热路径 DB 压力

#### 目标

解决：

- `on_message()` 每条消息都 `load_settings()`
- 每条消息都拉当前群全部 term 再做 Python 层匹配
- 命中后写 observation 带来的写放大

#### 改动建议

1. 为 `SlangSettings` 加内存缓存：
   - 插件启动加载
   - `save_settings()` 后主动失效/刷新
   - 不必每条消息再查 DB

   失效要求：
   - admin 端保存设置后必须触发 plugin 可见的缓存刷新
   - 不允许出现“后台设置已改，但 `on_message()` 继续长期使用旧 settings”

2. 为 message match 增加轻量群级缓存：
   - 按 `(group_id, include_candidates)` 缓存当前 term 列表
   - term 更新、状态变更、合并、人工审核后使缓存失效

3. 将 `find_matching_terms()` 分成：
   - DB 层：按 group 取 term snapshot
   - 纯内存层：对消息做匹配

4. 对 `record_hit()` 增加更细的去重与采样策略评估：
   - 保留 message_id 去重
   - 视情况减少重复 observation 写入
   - 明确评估“同一条消息命中多个 term 时，是否改成单事务批量写入”

#### 验收条件

- 高频群里不再出现“每条消息都查设置 + 查全部词条”的固定成本
- term 状态变化后缓存不会长期脏读
- 现有功能行为保持一致

### Phase 4：收紧短词匹配与 source attribution

#### 目标

解决：

- `_estimate_occurrences()` 对短词的子串膨胀
- `find_matching_terms()` 对短 alias 的误命中
- `_pick_source_row()` 对短 evidence 的误绑

#### 改动建议

1. 引入更保守的 match policy：
   - 对长度 2-3 的 term/alias 提高匹配门槛
   - 要求更明确的边界，或限定为整 token / 强上下文

   实施要求：
   - 不依赖英文空格边界作为中文主方案
   - 规则必须基于当前 `normalize_term()` / 中文群聊文本实际形态设计

2. 将 observed_count 逻辑与 message hit 逻辑拆开：
   - message hit 可适度宽松
   - observed_count 要更保守，因为它直接影响 candidate 晋升

3. 重写 `_pick_source_row()`：
   - 优先 exact evidence line match
   - 再尝试 normalized equality / high-overlap match
   - 最后才 fallback 到最近一条

4. 为短词增加保护策略：
   - 过短词条默认不走 auto approve
   - 或要求更高 `observed_count` / `confidence`

#### 验收条件

- 两字、三字短词不再轻易因为子串匹配膨胀
- evidence 与 source row 的绑定更稳定
- 回归样本中短词不再异常高频晋升

### Phase 5：统一 slang LLM 调用入口与观测

#### 目标

解决：

- slang extract / review task 路由不一致
- usage / latency 观测分散
- daily review 运行过程缺少结构化日志

#### 改动建议

1. 统一 slang 子任务入口：
   - extract 走 `task="slang_extract"` 或沿用 `slang`
   - review 走 `task="slang_review"` 或显式同属 slang task family

   边界说明：
   - 不要求 slang 后台任务直接走 `LLMClient.chat()` 的完整聊天公共路径
   - 仍应保留“轻量子任务调用”形态
   - 但 task 路由、usage 观测和日志口径要统一

2. 为 slang 子任务补统一日志字段：
   - `run_id`
   - `group_id`
   - `task`
   - `latency_ms`
   - `search_used`
   - `auto_approve`
   - `skip_reason`

3. 让 daily review 至少输出：
   - start
   - success
   - failed
   - timeout
   - abandoned stale run recovered

4. 视现有 LLMClient 能力补齐后台任务 usage / cost 观测：
   - 至少做到 extract / review 的调用次数、latency、profile 可统计
   - 明确评估当前 slang 子任务对 prompt caching 的利用情况，而不是默认继承主聊天链路的缓存收益

#### 验收条件

- 可以从日志直接判断 daily review 是没跑、跑挂、被跳过还是成功
- slang extract / review 的 task profile 能独立观测

### Phase 6：清理维护性欠账

#### 目标

处理：

- `PluginContext` 动态挂载未声明属性
- `SlangStore` 单文件职责过重

#### 改动建议

1. 在 `PluginContext` 中显式声明可选字段：
   - `slang_store`
   - `slang_plugin`

2. 逐步拆分 `SlangStore`，但不做大重构：
   - `store_terms.py`：term CRUD / collisions
   - `store_runs.py`：run/meta
   - `prompt_adapter.py`：prompt block / lookup formatting

3. 这一步排在后面，只在前几步稳定后再做

#### 验收条件

- 类型边界更明确
- 黑话相关逻辑更容易维护和继续审计

## 四、推荐实施顺序

建议按下面顺序推进：

1. `Phase 1`：daily review 状态机
2. `Phase 5` 中与 Phase 1/2 直接相关的日志增强
3. `Phase 2`：term + alias collision
4. `Phase 4`：短词匹配和 evidence/source 绑定
5. `Phase 3`：热路径缓存
6. `Phase 6`：维护性清理

说明：

- Phase 1 后先补关键日志，是为了让 Phase 2 的 collision 改动可对账
- Phase 3 的缓存放在后面，避免先把错误行为缓存起来

原因：

- Phase 1 和 Phase 2 直接对应当前已发生的线上问题
- 日志增强要尽早做，否则后续修复难对账
- 缓存优化应放在语义和一致性修正之后，避免缓存住错误行为

## 五、测试计划

### 新增单元测试

1. daily review 失败不应消耗当天机会
2. stale `running` run 可被回收并允许重试
3. 新 alias 撞已有 term -> 应拒绝或合并
4. 新 alias 撞已有 alias -> 应拒绝或合并
5. pending promote 命中 existing 时 aliases 不丢失，应被合并
6. pending promote 时 alias 冲突 -> 不得新建重复 candidate
7. `_estimate_occurrences()` 对短词不应异常膨胀
8. `_pick_source_row()` 短 evidence 不应优先绑定错误消息
9. settings/term 缓存失效后能读取最新状态
10. stale `daily_ai_review` run 会按 `started_at` 被识别并回收

### 回归测试

1. AI auto approve 成功路径仍能产生 `approved + ai_auto_review`
2. 搜索失败仍只入 candidate
3. approved term 仍能正常进入 prompt block
4. 群隔离仍然成立
5. lookup tool 行为不变

### 手工验收

1. 触发一次 daily review，中途人为打断，确认：
   - 不会永久 `already_ran`
   - run 状态能解释

2. 手工创建：
   - `term = pjsk`
   - `term = ptt, aliases = ["pjsk"]`
   应被拦住或合并

3. 构造短词样本，确认：
   - observed_count 不再异常放大
   - 命中和 source attribution 更稳定

## 六、总体判断

黑话模块当前最需要的不是“换一个更聪明的模型”，而是把：

- 调度状态
- 数据碰撞约束
- 短词匹配边界
- 日志与恢复机制

先收紧。

做到这一步之后，再讨论 embedding、语义检索或更复杂的公网梗判断，才是稳的。
