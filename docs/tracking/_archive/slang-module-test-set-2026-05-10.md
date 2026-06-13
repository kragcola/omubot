# Omubot 黑话模块测试集

日期：2026-05-10  
对应范围：`services/slang/`、`plugins/slang/plugin.py`、`services/llm/client.py`

## 当前自动化验收结果

2026-05-11 已执行：

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_acceptance_check.py
```

结果：
- `sqlite integrity_check`：通过
- `sqlite quick_check`：通过
- 黑话 pytest suite：`103 passed`
- 黑话 ruff suite：通过
- live semantic smoke：`0 fail, 0 warn`
- 总结：`5 passed, 0 failed`

## 0. 最短验收路径

如果你现在只想快速看结果，先走这条一键验收：

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_acceptance_check.py
```

它会自动跑：
- `sqlite3` 的 `integrity_check` / `quick_check`
- 黑话相关 pytest 回归
- ruff 规则检查
- live semantic smoke

如果你只想先做离线检查，跳过 bot / admin live smoke：

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_acceptance_check.py --skip-live
```

如果你现在只想快速看结果，先走这条：

1. 先跑一键烟雾检查：

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_semantic_smoke.py
```

这条命令会自动：
- 登录 admin API
- 必要时临时种一条 pending
- 必要时临时种一条 live 群消息，让日审真的进入 `user_rows`
- 强制跑一次 daily review
- 自动清理临时 pending 和临时消息

2. 再打开管理员页 `http://localhost:8081/admin/slang`
3. 左侧菜单也可以直接点 `群内黑话`
4. 页面首屏中间偏上有一个卡片，标题是 `最近抽取记录`
5. 点右上角 `手动抽取`
6. 看这个卡片里的最新一行是否更新
7. 再查日志：

```bash
docker compose logs bot --since=10m | rg 'task=slang_(extract|review)|slang manual extract|daily slang AI review'
```

识别口径：
- `抽取` = 手动抽取
- `AI 复核` = 每日 AI 复核
- `running / success / failed / abandoned` = 运行状态

## 一、自动化回归

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_acceptance_check.py
```

离线最小集：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py tests/test_slang_db_integrity.py -q
uv run ruff check services/storage/sqlite.py services/slang/store.py services/slang/semantic_reviewer.py services/slang/daily_reviewer.py plugins/slang/plugin.py services/health.py admin/routes/api/slang.py scripts/dev/slang_db_repair.py scripts/dev/slang_semantic_smoke.py scripts/dev/slang_acceptance_check.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py tests/test_slang_db_integrity.py
```

## 二、人工验收

### 1. 页面入口和运行记录

入口：
- `http://localhost:8081/admin/slang`
- 左侧菜单 `群内黑话`

页面上你要找的是：
- 顶部按钮 `手动抽取`
- 卡片 `最近抽取记录`

期望：
- 页面首屏就能看到 `最近抽取记录`
- 点 `手动抽取` 后，记录卡片会新增一条
- 新记录的类型会显示为 `抽取`

### 2. daily review 日志

输入：触发一次 `run_daily_ai_review_if_due()`

现场做法：
1. 把 `每日 AI 识别` 打开
2. 把 `每日 AI 识别时间` 暂时设成当前时间后的 1-2 分钟
3. 保存设置
4. 等下一轮 tick / 后台任务跑完
5. 看日志和 `最近抽取记录`

期望：
- 日志出现 `task=slang_review`
- 日志出现 `latency_ms`
- 成功时有 `slang review finished`
- 被跳过时有 `skip_reason=disabled|not_due|already_ran`
- 运行记录里类型显示为 `AI 复核`

### 3. manual extract 日志

输入：触发一次 `run_manual_extract()`

现场做法：
1. 进入 `群内黑话`
2. 点击 `手动抽取`
3. 等 toast 提示完成
4. 看 `最近抽取记录`
5. 再查 `docker compose logs bot --since=10m`

期望：
- 日志出现 `task=slang_extract`
- 日志出现 `latency_ms`
- 成功时有 `slang manual extract finished`
- 运行记录里类型显示为 `抽取`

### 4. 短 ASCII 词边界

数据：
- term: `abc`
- text: `zabcx`

期望：
- `find_matching_terms()` 不命中

再测：
- text: `abc! 继续`

期望：
- `find_matching_terms()` 命中

### 5. source row 选择

数据：
- evidence: `abc`
- rows:
  - `zabcx`
  - `abc!`
  - `abc`

期望：
- `_pick_source_row()` 选择最后一个精确行

### 6. observed_count 保守化

数据：
- term: `abc`
- aliases: `[]`
- rows: `zabcx`, `abc!`, `abc`

期望：
- `_estimate_occurrences()` 结果为 `2`

### 7. settings 缓存刷新

步骤：
1. 读一次 settings
2. 另一个实例保存新 settings
3. 立即再读

期望：
- 立即看到新值

### 8. batch hit

步骤：
1. 创建两个 approved term
2. 调用 `record_hits([term_a, term_b, term_a])`

期望：
- 返回 `2`
- duplicate term 只记一次

### 9. daily review task 入口

输入：
- 复核走 `slang_review`
- 抽取走 `slang`

期望：
- 两者能分别调用
- `slang_review` 不再直接走主聊天裸调用

### 10. 单测兜底

如果你不想等后台 tick，直接跑这条就够了：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_slang_plugin.py -q
```

这条线覆盖：
- `run_daily_ai_review_if_due()` 的状态机
- stale run 回收
- `run_manual_extract()` 的运行记录与日志
- `slang_review` wrapper 是否还在走独立入口

### 11. 语义复核验收

这次黑话语义复核不是只看“有没有调用 LLM”，而是要同时对上三处：
- `最近抽取记录` 有 `AI 复核` run
- 日志里有 `semantic_reviewed/semantic_approved/semantic_rejected/semantic_kept`
- pending 或 term/revision 里写入了 `semantic_*` meta

自动化入口：

```bash
source ./scripts/dev/env.sh
uv run python scripts/dev/slang_semantic_smoke.py
```

这条命令会自动做：
- admin 登录
- 读取 slang settings
- 拉取最近 daily review run
- 扫 pending 是否有到阈值的候选
- 必要时临时种一条 live 群消息，让 `semantic_reviewed` 真的不再是 0
- 扫 term meta 里的 `semantic_*`
- 对账 `docker compose logs bot`
- 默认把日志缺失当成诊断警告；如果你想强制日志也必须命中，可以加 `--strict-logs`
- 默认不会因为“当前没有到阈值 pending”失败；如果要强制确认本轮至少复核了 1 条，可以加 `--require-semantic-reviewed 1`

准备：
1. 打开 `http://localhost:8081/admin/slang`
2. 展开右侧设置，确认：
   - `每日 AI 识别` 开启
   - `每日 AI 识别时间` 设为当前时间后的 1-2 分钟
   - 如果配置了群白名单，目标群必须在白名单内
   - 初次验收建议先关闭 `AI 自动通过`，避免直接 approved 后不好区分路径
3. 在 `观察中` / pending 区确认至少有 1 条 `count >= 2` 的 pending。语义复核阈值是 `[2, 4, 8, 12, 24, 60, 100]`，未达到阈值不会调用 LLM。

触发：
1. 保存设置
2. 等下一轮后台 tick 触发 daily review
3. 同时看日志：

```bash
docker compose logs bot --since=15m | rg 'task=slang_review|daily slang AI review|semantic_reviewed|semantic_approved|semantic_rejected|semantic_kept|semantic_no_info|semantic_failed'
```

期望：
- 出现 `daily slang AI review start`
- 出现 `slang review start | task=slang_review`
- 结束日志包含 `semantic_reviewed=...`
- 如果 `semantic_reviewed=0`，优先检查 pending 是否未到阈值、今天是否已经 `already_ran`、目标群是否被白名单排除、`daily_ai_max_terms_per_group` 是否太小

页面确认：
1. `最近抽取记录` 最新一行应显示 `AI 复核`
2. 状态应为 `success`
3. 如果语义判定为群内黑话且自动通过关闭，pending 会被转成 `待审核` 词条，详情抽屉的修订记录应出现 `semantic_review_candidate`
4. 如果语义判定接近普通含义，会转为静音/否决路径，修订记录应出现 `ai_reject_pending`
5. 如果开启 `AI 自动通过` 且置信度足够，会进入 `AI 审核` 队列，详情里应出现 `AI 通过复核`
6. 如果是 `no_info`、解析失败或超时，pending 会保留，这是 fail closed 的正确行为

API 深查：

```bash
curl -s 'http://localhost:8081/api/admin/slang/extract/runs?limit=5' | jq '.runs[0]'
curl -s 'http://localhost:8081/api/admin/slang/pending?page_size=20' | jq '.pending[] | {pending_id, term, count, meta}'
curl -s 'http://localhost:8081/api/admin/slang/terms/TERM_ID/revisions' | jq '.revisions[0]'
curl -s 'http://localhost:8081/api/admin/slang/terms/TERM_ID' | jq '.term.meta'
```

`TERM_ID` 替换成页面详情或 API 返回的词条 ID。

通过口径：
- pending meta 或 term meta 中存在 `semantic_review=true`
- 达到阈值的样本有 `last_semantic_inference_count`
- 完整三段推断成功时有 `semantic_context_meaning`、`semantic_literal_meaning`、`semantic_is_similar`、`semantic_compare_reason`
- run meta 或日志统计能看到 `semantic_reviewed > 0`
- 同一阈值重复跑不会再次烧模型；只有 count 跨到下一个阈值才会再次复核

## 三、建议执行顺序

1. 先看页面里的 `最近抽取记录`
2. 再点 `手动抽取`
3. 再看 `docker compose logs bot --since=10m`
4. 最后跑自动化回归
5. 如果还要核 daily review，再跑单测兜底
