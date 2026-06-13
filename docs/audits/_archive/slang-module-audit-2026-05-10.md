# Omubot 黑话模块专项审计报告

审计日期：2026-05-10  
审计范围：`plugins/slang/`、`services/slang/`、`tests/test_slang_plugin.py`、`tests/test_slang_store.py`、`docs/wiki/Slang.md`、运行中 `storage/slang.db` 与最近 24 小时容器日志。

## 一、结论摘要

这轮审计确认，用户已经观察到的两个现象都成立，而且属于两类不同问题：

1. `daily_ai_auto_approve_enabled` 已开启，但“AI 自动通过没有触发”并不是单纯配置问题，而是每日 AI 复核调度存在“先记今日已跑，再真正执行”的状态写入缺陷。只要当天任务卡住、超时或异常，就会直接吃掉当天重试机会。
2. `pjsk` 重复出现并不只是展示层问题。当前黑话去重逻辑只检查主词条 `term`，不检查传入 `aliases` 是否与已有 `term/aliases` 冲突，因此同群内“一个词条的 term”和“另一个词条的 alias”可以合法共存。
3. 除了这两个用户可见问题，黑话模块还存在一组已经属实的次级缺陷：消息命中链路每条消息都查库并做全量文本扫描、tick job 将 daily review 与 manual extract 绑在同一个 50 秒超时里、短词子串匹配过宽、source attribution 过宽、以及 `PluginContext` 动态挂载和 `SlangStore` 职责过重等可维护性问题。

其中前两项属于调度与数据一致性问题，第三项覆盖性能、质量和维护性风险；都能用当前代码和线上数据库直接证明。

## 二、线上证据

### 1. 自动通过开关并未关闭

运行中 `storage/slang.db` 的 `slang_settings` 中，当前配置包含：

- `daily_ai_review_enabled = true`
- `daily_ai_review_search_enabled = true`
- `daily_ai_auto_approve_enabled = true`
- `daily_ai_auto_approve_min_confidence = 0.82`
- `daily_ai_review_time = "04:30"`

说明“自动通过没触发”不是因为设置没开。

### 2. 每日 AI 复核在今天留下了卡死中的运行记录

运行中 `slang_extraction_runs` 最近几次 `daily_ai_review` 记录：

- `2026-05-10T04:30:29+08:00`：`status=running`
- `2026-05-09T04:30:40+08:00`：`status=success`
- `2026-05-08T04:30:39+08:00`：`status=success`

同时 `slang_settings` 中的 meta 记录为：

- `meta:last_daily_ai_review_date = "2026-05-10"`
- `meta:last_daily_ai_review_at = "2026-05-09T04:30:41+08:00"`

这说明今天这次复核已经被标记为“今天跑过”，但成功完成时间仍停留在昨天。

### 3. 当前库内确实存在 `pjsk` 重复碰撞

运行中 `slang_terms` 至少有以下相关记录：

- 群 `984198159`：`term = pjsk`
- 群 `993065015`：`term = pjsk`
- 群 `993065015`：`term = ptt`，但 `aliases_json` 中包含 `pjsk`

其中跨群出现 `pjsk` 本身不一定是 bug，因为黑话系统设计是按群隔离。  
但在同一个群 `993065015` 中，同时存在：

- 一个主词条 `pjsk`
- 另一个主词条 `ptt`，其 alias 包含 `pjsk`

这就不是展示重复，而是数据层允许冲突写入。

### 4. 最近容器日志缺少足够的 daily review 可观测信息

最近 24 小时 `docker compose logs --since=24h bot` 中，与黑话相关的显式日志几乎只有：

- `slang store initialized | db=storage/slang.db`

没有看到可用于值班判断的：

- daily review 开始
- daily review 成功
- daily review 失败
- auto approve 命中数量
- 因 stale running / already_ran 被跳过

这使得调度异常只能靠数据库倒查，而不是靠运行日志快速定位。

### 5. 黑话命中链路每条消息都会读设置、查词条并可能继续写 observation

`SlangPlugin.on_message()` 当前流程是：

1. `load_settings()`
2. `find_matching_terms()`
3. 对每个命中词条 `record_hit()`

对应代码位置：

- [plugins/slang/plugin.py:153](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:153)
- [services/slang/store.py:538](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:538)
- [services/slang/store.py:1958](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1958)
- [services/slang/store.py:1252](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1252)

这意味着在高频群里，黑话模块不是“偶尔扫一次库”，而是每条群消息都至少会：

- 查一次 `slang_settings`
- 查一次当前群全部 approved/candidate 词条
- 在 Python 层逐条做字符串匹配
- 命中后再写 term usage 与 observation

这属于真实存在的运行时开销。

## 三、代码级问题定位

### P0：每日 AI 复核在成功前就写入 `last_daily_ai_review_date`

`SlangPlugin.run_daily_ai_review_if_due()` 在真正执行 review 之前，就先写入当天日期：

- [plugins/slang/plugin.py:223](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:223)
- [plugins/slang/plugin.py:237](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:237)
- [plugins/slang/plugin.py:241](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:241)
- [plugins/slang/plugin.py:242](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:242)

逻辑现状：

1. 判断到点且今天未跑；
2. 立即 `set_meta("last_daily_ai_review_date", today)`；
3. 然后才 `run_daily_ai_review(...)`。

风险：

- review 中途异常，当天后续 tick 会直接落到 `already_ran`；
- `daily_ai_auto_approve_enabled=true` 也无法补救，因为任务入口已经被短路；
- 线上出现 `status=running` 且 `last_daily_ai_review_date=今天`、`last_daily_ai_review_at=昨天`，与这个缺陷完全一致。

### P0：黑话去重只覆盖主词条，不覆盖别名碰撞

`find_existing()` 只检查“新 term 是否撞已有 term 或已有 alias”：

- [services/slang/store.py:598](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:598)
- [services/slang/store.py:603](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:603)
- [services/slang/store.py:611](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:611)
- [services/slang/store.py:617](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:617)

但它不检查：

- 新 aliases 是否撞已有 term
- 新 aliases 是否撞已有 aliases

而三个关键写入入口都依赖这个单向查重：

- `upsert_candidate()`：[services/slang/store.py:775](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:775) 至 [services/slang/store.py:921](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:921)
- `create_term()`：[services/slang/store.py:923](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:923) 至 [services/slang/store.py:1014](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1014)
- `upsert_ai_approved_term()`：[services/slang/store.py:1016](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1016) 至 [services/slang/store.py:1168](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1168)

因此当前系统会接受这样的冲突：

- 已有词条 `term = pjsk`
- 新词条 `term = ptt, aliases = ["pjsk"]`

这正是线上同群 `pjsk` 重复的直接根因。

### P1：pending 候选阶段同样缺失 alias 冲突校验

pending 缓冲并没有额外的全量碰撞保护：

- `_upsert_pending_candidate()` 只按 `term_key` 聚合：[services/slang/store.py:622](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:622) 至 [services/slang/store.py:724](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:724)
- `_promote_pending_candidate()` 晋升前也只检查 `pending.term`：[services/slang/store.py:726](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:726) 至 [services/slang/store.py:773](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:773)

结果是：

- alias 冲突可以先在 pending 阶段积累；
- 达到阈值后再被 promote 成正式 candidate；
- 之后继续污染 prompt 注入、lookup 和管理端列表。

### P1：运行记录可能永久停留在 `running`，缺少 stale run 恢复

`SlangDailyReviewer.run()` 会先调用 `start_extraction_run()`，再在 `try/except` 里尝试 `finish_extraction_run()`：

- [services/slang/daily_reviewer.py:116](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:116)
- [services/slang/daily_reviewer.py:232](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:232)
- [services/slang/daily_reviewer.py:259](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:259)

插件层虽然给 tick job 加了 `wait_for(timeout=50s)`：

- [plugins/slang/plugin.py:193](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:193)
- [plugins/slang/plugin.py:195](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:195)
- [plugins/slang/plugin.py:199](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:199)

但如果发生：

- 进程重启
- 任务被中断
- finish 之前崩溃

就会留下永久 `running` 记录。今天线上已经有一条这样的 run。  
它未必直接阻断功能，但会严重误导排障，而且会和前面的“提前写 today”缺陷叠加。

### P1：daily review 与 manual extract 共用 50 秒 tick 超时，且中间写入不是事务式完成

`SlangPlugin._run_tick_jobs()` 把整个 `_run_tick_jobs_inner()` 包进单个 `wait_for(timeout=50s)`：

- [plugins/slang/plugin.py:193](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:193)
- [plugins/slang/plugin.py:195](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:195)

而 `_run_tick_jobs_inner()` 是先跑 daily review，再决定是否跑 manual extract：

- [plugins/slang/plugin.py:206](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:206)
- [plugins/slang/plugin.py:207](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:207)
- [plugins/slang/plugin.py:213](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:213)

daily review 本身又会逐群执行：

- LLM 抽取
- 搜索调用（单次最多 8 秒）
- AI 评估
- 词条入库

对应位置：

- [services/slang/daily_reviewer.py:130](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:130)
- [services/slang/daily_reviewer.py:147](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:147)
- [services/slang/daily_reviewer.py:346](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:346)

同时，入库路径是多次 `commit()` 的，不是“整轮 review 成功才一次性提交”：

- [services/slang/store.py:911](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:911)
- [services/slang/store.py:1147](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1147)

所以这一层的风险不是单纯“50 秒可能不够”，而是：

- daily review 可能被超时打断；
- 已写入的 term / observation 会保留；
- 但 run / meta 状态又可能停在不一致状态。

这与当前线上 `running` run 和记 today 的问题能相互增强。

### P1：短词与 alias 的子串匹配过宽，可能放大命中数和 observed_count

当前黑话模块多处使用“归一化后子串包含”作为匹配逻辑：

- message 命中：`find_matching_terms()` 使用 `key in normalized_text`
  - [services/slang/store.py:1976](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1976)
  - [services/slang/store.py:1983](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1983)
- daily review 估算出现次数：`_estimate_occurrences()` 使用 `any(key in text_key for key in keys)`
  - [services/slang/daily_reviewer.py:395](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:395)
  - [services/slang/daily_reviewer.py:403](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:403)
- prompt 注入排序时也会做同类直接命中判断
  - [services/slang/store.py:2009](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:2009)
  - [services/slang/store.py:2011](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:2011)

虽然有 `len(key) >= 2` 的下限，但两字、三字短词仍然容易误匹配。这会带来两类后果：

- `observed_count` 被高估，短词更容易越过 `candidate_min_count`
- message hit / prompt direct hit 被放大，污染 usage_count 和注入排序

这属于真实的数据质量问题，不只是“模糊一点也没关系”。

### P2：LLM 调用入口不一致，slang 子任务缺少统一的 task 归口和 usage 记账链路

`SlangExtractor` 会优先走 `_call_slang()`：

- [services/slang/extractor.py:79](/Users/kragcola/OmubotWorkspace/omubot/services/slang/extractor.py:79)

`LLMClient` 也确实为 slang 定义了专门 task wrapper：

- [services/llm/client.py:1045](/Users/kragcola/OmubotWorkspace/omubot/services/llm/client.py:1045)
- [services/llm/client.py:1055](/Users/kragcola/OmubotWorkspace/omubot/services/llm/client.py:1055)

但 `SlangDailyReviewer._assess()` 却直接调用 `_call()`：

- [services/slang/daily_reviewer.py:304](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:304)

这带来两个问题：

1. slang extract 和 slang review 的 task 路由不一致；
2. slang 这类后台子任务没有像主聊天链路那样统一走 usage 记账出口。

这不是最急的线上 bug，但属于需要收口的架构欠账。

### P2：搜索结果“非空即视为可用”的判定过宽，daily auto approve 存在误批风险

`_search()` 当前只要结果非空，且不包含“搜索失败”“未找到”，就将其视为有效搜索证据：

- [services/slang/daily_reviewer.py:337](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:337)
- [services/slang/daily_reviewer.py:352](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:352)

而 auto approve 的判断条件是：

- 有搜索结果
- `assessment.approved = true`
- `confidence >= threshold`

对应位置：

- [services/slang/daily_reviewer.py:182](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:182)
- [services/slang/daily_reviewer.py:188](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:188)

这意味着搜索结果只起到了“存在性门槛”的作用，没有额外判断它是否真的是目标梗的公网证据。  
如果搜索结果主题无关，但 LLM 仍然给出高置信 `approved`，就可能产生错误的 auto approve。

### P2：source attribution 规则过宽，短 evidence 可能绑到错误消息

`_pick_source_row()` 当前使用双向子串包含：

- [services/slang/daily_reviewer.py:377](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:377)
- [services/slang/daily_reviewer.py:383](/Users/kragcola/OmubotWorkspace/omubot/services/slang/daily_reviewer.py:383)

即：

- `evidence in text`
- 或 `text in evidence`

当 evidence 很短时，容易误绑到相似消息行。  
这会影响：

- observation / raw_text 的来源准确性
- AI review 结果的追溯性
- 管理端证据链的可信度

### P3：`PluginContext` 动态挂载黑话对象，`SlangStore` 职责也偏重

黑话插件启动时会动态挂载：

- `ctx.slang_store = self.store`
- `ctx.slang_plugin = self`

见：

- [plugins/slang/plugin.py:138](/Users/kragcola/OmubotWorkspace/omubot/plugins/slang/plugin.py:138)

但 `PluginContext` 类型定义中并没有显式声明这两个字段：

- [kernel/types.py:138](/Users/kragcola/OmubotWorkspace/omubot/kernel/types.py:138)

同时，`SlangStore` 当前单文件约 2265 行，除 CRUD 外还承担：

- prompt block 生成
- lookup
- global candidate scan
- drift review 数据治理

例如：

- [services/slang/store.py:1988](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:1988)
- [services/slang/store.py:2075](/Users/kragcola/OmubotWorkspace/omubot/services/slang/store.py:2075)

这类问题不直接导致当前线上故障，但已经影响长期可维护性。

## 四、文档与实现不一致处

`docs/wiki/Slang.md` 对每日 AI 复核的描述是：

- 搜索失败时只允许入候选，不自动通过；
- 高置信时写为 `approved + source=ai_auto_review`；
- AI 自动通过后可待人工复核。

对应文档位置：

- [docs/wiki/Slang.md:66](/Users/kragcola/OmubotWorkspace/omubot/docs/wiki/Slang.md:66)
- [docs/wiki/Slang.md:74](/Users/kragcola/OmubotWorkspace/omubot/docs/wiki/Slang.md:74)
- [docs/wiki/Slang.md:75](/Users/kragcola/OmubotWorkspace/omubot/docs/wiki/Slang.md:75)

这些描述单看业务语义没有错，但它们默认假设“每日 AI 复核任务能稳定完成”。  
当前实现里，调度状态写入和 stale run 缺失会让“理论上可自动通过”的流程在运行层先失效。

## 五、测试覆盖缺口

### 1. 缺少“daily review 失败后当天还能否重试”的测试

当前只有成功路径测试：

- [tests/test_slang_plugin.py:207](/Users/kragcola/OmubotWorkspace/omubot/tests/test_slang_plugin.py:207)

它验证“成功后当天再次调用会 `already_ran`”，但没有验证：

- review 失败时是否仍会错误写入 `last_daily_ai_review_date`
- stale `running` 是否会影响第二次重试

### 2. 缺少“alias 撞 term / alias”的反向碰撞测试

当前测试能覆盖一部分“term 撞已有 alias”：

- [tests/test_slang_store.py:221](/Users/kragcola/OmubotWorkspace/omubot/tests/test_slang_store.py:221)

但没有覆盖：

- 新 alias 撞已有 term
- 新 alias 撞已有 alias
- pending promote 时 alias 冲突
- `upsert_ai_approved_term()` 的 alias 冲突

这也是为什么 `pjsk` 这种重复能穿过测试上线。

### 3. 缺少性能与质量边界测试

当前测试没有覆盖以下已经属实的边界：

- `on_message()` 高频命中场景下的查库/命中行为
- short term / alias 子串误匹配
- `_estimate_occurrences()` 对短词的膨胀风险
- `_pick_source_row()` 对短 evidence 的误绑风险
- daily review 超时或中断后 run/meta 的状态行为

因此性能和数据质量问题虽然已经在实现里存在，但还没有自动化回归保护。

## 六、风险分级

| 优先级 | 问题 | 影响 |
| --- | --- | --- |
| P0 | `last_daily_ai_review_date` 提前写入 | 当天任务异常后无法重试，直接表现为“AI 自动通过没触发” |
| P0 | term/alias 单向去重 | 同群内允许重复写法并存，污染审核、注入、检索与展示 |
| P1 | daily review 与 manual extract 共用 50 秒超时 | 中断后可能留下部分已提交数据，并放大调度状态不一致 |
| P1 | 每条消息读设置 + 全量词条扫描 + 命中后再写 observation | 高频群中会形成可见的 SQLite 读写压力 |
| P1 | pending 阶段缺少 alias 冲突保护 | 重复项会在缓冲区累积后正式晋升 |
| P1 | 短词子串匹配过宽 | observed_count、usage_count 与 prompt direct hit 可能被放大 |
| P1 | stale `running` 记录无恢复 | 值班排障困难，且与 P0 问题叠加 |
| P2 | 搜索结果存在性判断过宽 | `auto_approve=true` 时存在误批公网梗/同名词条的风险 |
| P2 | slang 子任务 LLM 调用入口不一致 | task 路由、可观测性和 usage 记账不统一 |
| P2 | source attribution 过宽 | evidence 可能绑定到错误消息，削弱审计可追溯性 |
| P2 | 运行日志缺少结构化可观测信息 | 只能查库定位，不利于维护 |
| P3 | `PluginContext` 动态挂载与 `SlangStore` 职责过重 | 长期维护成本高，类型边界模糊 |

## 七、建议修复顺序

### Step 1：修每日 AI 复核调度状态

建议：

1. 只在 `run_daily_ai_review()` 成功完成后再写 `last_daily_ai_review_date`；
2. 或者单独引入 `in_progress` 状态，而不是把“已启动”当成“已完成”；
3. 对超过阈值的 `running` 记录做 stale recovery。

目标：

- 当天 04:30 的 review 如果失败，后续 tick 仍可补跑；
- 管理端和日志能区分“已完成”“正在跑”“异常残留”。

### Step 2：把去重升级为 term + aliases 全量碰撞校验

建议：

1. 写统一的 normalized key set，覆盖 `{term} ∪ aliases`；
2. 创建、候选写入、AI 自动通过、pending promote 统一走同一套 collision 检查；
3. 明确冲突策略：合并、转 drift、拒绝写入、或退回人工审核。

目标：

- 同群内不再允许 `pjsk` 既是一个主词条，又是另一个主词条的 alias；
- 管理端搜索和 prompt 注入不再看到这种重复冲突。

### Step 3：补观测日志与回归测试

建议：

1. 补 daily review 的开始、完成、失败、skip reason、auto approve 数量日志；
2. 补以下测试：
   - review 失败不应消耗当天机会；
   - stale run 可被识别；
   - alias 撞 term / alias；
   - pending promote 的 alias 冲突；
   - AI auto approve 路径的 alias 冲突。

### Step 4：收紧匹配与来源判定，避免短词放大

建议：

1. 为 `find_matching_terms()` 和 `_estimate_occurrences()` 引入更严格的 term boundary / token 规则；
2. 对过短 term/alias 提高阈值或降级为“仅候选观察”；
3. 将 `_pick_source_row()` 改为优先 exact line match、再 fallback 到更保守的相似度匹配。

目标：

- 两字、三字短词不再轻易通过子串误匹配放大 usage 和 observed_count；
- 证据行绑定更稳定，管理端追溯更可信。

### Step 5：降低热路径读写压力，统一 slang 子任务的 LLM 入口

建议：

1. 为 settings 和 approved/candidate term 列表加轻量内存缓存与失效策略；
2. `on_message()` 尽量避免“每条消息一次全量 DB 拉取”；
3. 让 slang extract / review 都显式走统一的 slang task 调用入口；
4. 为 slang 后台任务补 usage / latency 观测项。

目标：

- 高频群下 SQLite 压力可控；
- slang 相关 LLM 调用在 profile、usage 和日志上可统一观测。

## 八、总体判断

黑话模块目前不是“功能没有做出来”，而是：

- 调度层对异常不够稳；
- 存储层对别名碰撞过于宽松；
- 消息命中与短词匹配策略偏粗；
- hot path 读写和后台调用入口还不够收敛；
- 运行可观测性不足。

也就是说，问题的本质不是模型不聪明，而是工作流状态机和数据约束还没收紧。  
这份审计确认，用户观察到的“自动通过没触发”和“pjsk 重复”都不是偶发现象，而是当前实现下可重复出现的结构性问题。
