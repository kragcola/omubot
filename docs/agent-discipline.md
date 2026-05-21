# Agent 工作纪律

> 本文件汇总从历次 incident 复盘中提炼的工作纪律。CLAUDE.md 给出精炼指针，
> 这里给出每条纪律的**起源 incident、动作要求、可执行的检查清单**。
>
> 新条款采用"起源 → 规则 → 动作 → 反例"四段格式，按时间倒序追加。
> 改写既有条款时务必保留起源 incident 链接，否则 5-Why 失忆。

---

## D1 — 修 bug 必做"同模式扫描"（Same-Pattern Sweep）

**起源**：[2026-05-15 黑话 daily AI review 锁全天 incident](../maintenance-log.md)。
昨晚专项修了"slang run 卡 running、counters 全 0"——加了 try/finally + asyncio.shield
+ stale-sweep，自认闭环。但**没扫同代码库的同模式位点**：
`run_daily_ai_review_if_due` 里的 `set_meta(last_daily_ai_review_date)` 被写在
`await run_daily_ai_review()` 之前。当 wait_for 一次 cancel，date 已经被标成
"今天已跑"，但 review 实际半路死掉——下次 tick 看到 date==today 就直接
`skipped="already_ran"`，**全天再不重试**。24 小时内同模式第二刀炸出来。

**规则**：修任何 bug 时，必须 grep 同代码库找"同模式位点"，并在维护日志里列出。

**动作**：

1. 定位到根因后，**先停手**。
2. 根据根因抽出"模式签名"——例如：
   - `await store.set_meta(...)` 写在 `await some_long_running_task(...)` **之前**？
   - `try: ... except Exception:` 是否漏掉 `BaseException` 子类（CancelledError/KeyboardInterrupt）？
   - `await self.store.X(...)` 是否在 try 块**之外**，cancel 后会被跳过？
3. 用 grep 搜整个模块（不只搜出问题那个文件）。例如：
   ```bash
   rg -n 'await\s+self\.store\.set_meta' plugins/ services/
   rg -n 'except Exception:' plugins/slang/ services/slang/
   ```
4. 把命中点逐一过一遍：是不是同样的洞？
5. 修复时同步处理（或显式记录"此处不影响因为 X，无需改动"）。
6. 维护日志的"改动文件"段下面，必须有一行：
   `**同模式扫描**：grep ... 命中 N 处，已处理 X 处、白名单 Y 处（理由：...）`

**反例**：
- ❌ "改了 A 函数 → pytest 通过 → 收工"——同文件 B 函数同模式没看。
- ❌ "搜了 plugin.py 没看 services/slang/daily_reviewer.py"。
- ❌ 维护日志只写"修了 A"，没写扫了哪些位点。

---

## D2 — 关键 async 任务必有 cancel-path 测试

**起源**：D1 同 incident。
原代码所有路径都假设"任务正常返回"，但 `asyncio.wait_for(timeout)`、
`task.cancel()`、shutdown 都会让被等待的协程抛 `CancelledError`，
而 `except Exception` 不抓 `BaseException` 子类。这是 Python async 的
"沉默杀手"——单测用 `await foo()` 跑一次绿了不代表线上绿。

**规则**：任何被外层 `wait_for` / `gather(timeout=)` / 长跑后台任务，
必须有对应的 cancel-path 测试。

**动作**：

1. 找到所有"被外层超时包裹"或"会被 shutdown 取消"的协程。
2. 为每个写至少一条测试：
   ```python
   @pytest.mark.asyncio
   async def test_X_does_not_corrupt_state_when_cancelled(...):
       with pytest.raises(asyncio.TimeoutError):
           await asyncio.wait_for(plugin.X(...), timeout=0.05)
       # 验证：1) DB 状态不卡 running；2) 任何 in-flight 旗标已释放；
       #       3) 任何"今日已跑"meta 不会被错误写入
   ```
3. 测试断言必须覆盖**外部可观察状态**——不能只断言"函数抛了异常"。
4. 推荐用 `_SlowLLM` 之类的 stub（`await asyncio.sleep(60); raise AssertionError`）
   保证内层 await 一定挂住，让 wait_for 必然 fire。

**反例**：
- ❌ 只测 `await foo()` happy path，不测 `await asyncio.wait_for(foo(), 0.05)`。
- ❌ 测试断言只看返回值，不看 sqlite 行的 `status`、不看 plugin 的
  `_in_flight` 旗标、不看 meta 的"今天已跑"标记。
- ❌ 用 `time.sleep` 而不是 `asyncio.sleep`——前者卡事件循环，cancel 不会传进去。

---

## D3 — 大重构必带"逐项回归清单"（Migration Checklist）

**起源**：[2026-05-15 admin 表达方式页面消失 incident](../maintenance-log.md)。
v1.4.0 admin SPA 迁移时，`StyleView.vue` 从老前端被复制到新 SPA 的
`admin/frontend/src/views/style/`，但 router/index.ts 没注册 `/style` 路由、
SideMenu 没加菜单项。后端 `/api/admin/style/*` 还是好的。**前端文件存在 ≠ 用户能访问**——
三件事（文件、路由、菜单）任意一件缺失就完全失踪。

**规则**：批量重构（框架迁移、kernel 重构、API 重整）必须带逐项回归清单，
按"旧 → 新"四列对照走完所有项。

**动作**：

1. 重构前先做盘点 spreadsheet。例如 admin SPA 迁移：
   | 旧页面 | 新视图文件 | 新路由 | 新菜单项 | 后端 API | 验证状态 |
   | ------ | ---------- | ------ | -------- | -------- | -------- |
   | 表达方式 | StyleView.vue | `/style` | SideMenu「日常」 | `/api/admin/style/*` | ✅ HTTP 200 |
2. 每行的"验证状态"列必须由 `curl -o /dev/null -w '%{http_code}'`
   或浏览器实测确认，不能只靠 grep 文件名。
3. 清单存进 `docs/migrations/<feature>-<date>.md`，**和重构 PR 一起提交**。
4. PR 描述里贴清单截图/链接，未来同问题再爆可追溯。

**反例**：
- ❌ "我把所有 .vue 文件复制过来就完事了"——三件事缺两。
- ❌ "看了下大家都迁过来了"——没有清单 = 没有验证。
- ❌ 重构 PR 不附迁移清单。

---

## D4 — 完成声明必须含"已扫同模式位点"和"外部可观察证据"

**规则**：声称 "fix 完成 / 任务交付" 时，必须在维护日志同时给出：

1. **同模式扫描结果**（D1）——grep 命中数 + 处理结果 + 白名单理由。
2. **外部可观察证据**——sqlite SELECT 输出、HTTP 状态码、容器日志片段、测试输出，
   不能只是"测试通过"。
3. **回滚路径**——一行 git revert / 一条 SQL backfill / 容器重启命令。

**反例**：
- ❌ "改完了，pytest 通过"——没有可观察状态证据。
- ❌ "走查了一遍代码"——没扫 grep。
- ❌ 没写回滚方案。

---

## D5 — 跑全量 pytest 前先清理孤儿进程

**起源**：[2026-05-15 全量 pytest 卡 5 分钟 incident](../maintenance-log.md)。
今天连续两次跑 `uv run pytest` 卡 5 分钟没输出。`ps -ef | grep pytest` 显示 11 个
PPID=1 的孤儿 pytest 进程从凌晨 12:01 起就在内存里没退出，跟 IDE 测试 explorer 启的
新 pytest 抢同一个 sqlite 文件锁导致互锁。本次改动本身无锅，环境问题污染了验证。

**规则**：跑全量 pytest 前先 `pkill -9 -f pytest`，或显式接受可能死锁。

**动作**（按需挑一）：

```bash
# 推荐：先清孤儿
pkill -9 -f pytest && sleep 1 && uv run pytest -q --tb=short

# 或：限定到 tmp_path 测试避免抢真实 storage/*.db 锁
uv run pytest tests/test_slang_plugin.py tests/test_slang_store.py -q

# 或：整个 IDE 测试 explorer 暂停后再跑
```

**反例**：
- ❌ 看到 pytest 卡了，第二次重跑同条命令——只会再加一个孤儿。
- ❌ 不查 `ps -ef | grep pytest` 就归因到"测试本身慢"。
- ❌ 用 `--timeout=N` 但项目里没装 pytest-timeout 插件。

---

## D6 — admin SPA 静态资源同步路径

**起源**：D3 同 incident 的解决步骤。
`docker-compose.yml` 把 `./admin/static:/app/admin/static:ro` 做了 bind mount，
意味着前端 `npm run build` 一旦输出到 `admin/static/assets/`，**容器内立即可见**，
不需要 rebuild bot 镜像、也不一定要 restart。但 `Dockerfile` 同时有
`COPY admin/static ./admin/static/`，新建镜像时会再 freeze 一次。

**规则**：

- 只改前端 → `cd admin/frontend && npm run build` → 浏览器强刷即可，不需要 docker rebuild。
- 改前端+后端 → `npm run build` 完再 `docker compose up bot -d --build`。
- 验证镜像内代码：`docker compose exec bot grep -c <symbol> <path>`，
  不能只看 host 文件。

**反例**：
- ❌ 改了 .vue 没 build，只 docker rebuild——容器里跑的还是旧 JS。
- ❌ 改了 .py 只 build 前端没 rebuild bot——后端逻辑没更新。

---

## D7 — 部署 / build 前必跑 git hygiene

**起源**：[2026-05-21 stash 全量恢复 incident](../maintenance-log.md)。
Phase 2 部署当天没人查 stash，5 天 in-progress 工作（admin 重构 + knowledge_graph store +
slang 子组件化 + CachePipelinePanel 重写）就这么被静默回溯到 2 天前的状态。根因有两条：

1. `git stash apply` 遇到 tracked-file 上下文冲突时**静默跳过 hunks，不报错也不 reject**。
   stash@{0} 自 b41631a 起累计经过 12 次手动 push/restart 与 Phase 1+2 在 close_with_checkpoint /
   journal_mode=DELETE 等关键文件冲突，apply 跑过去之后看似无事，实际整段 frontend 改动
   完全没落盘。
2. 恢复期间 `git add -A` 把 3 个 `storage/slang.db.bak-pre-a1-merge*` 备份文件自动 stage，
   靠人眼在 git status 里看到才 `git reset HEAD` 捞回来——只动 spec 没护栏会复发。

**规则**：

- 任何 deploy / build / merge 之前，必跑 `git stash list && git status -uno`，确认没有
  未恢复 stash、没有未提交修改被遗漏。
- `storage/*.db*` / `storage/*.bak*` / `*.db-shm` / `*.db-wal` 永不进 commit——靠 .gitignore
  物理护栏，不靠"我记得避开"。
- 恢复 stash 后必须 `git diff` 抽查关键文件，确认 hunks 真的应用上了；不要相信 stash apply
  的 exit code 0。

**动作**：

```bash
# 1. 部署 / build / merge 前
git stash list                        # 期望：empty 或解释清楚每条 stash 用途
git status -uno                       # uno = 不显示 untracked，专看追踪文件改动
git diff --cached HEAD                # 确认 staged 改动是预期的

# 2. stash apply / pop 后必抽查
git stash apply stash@{0}
git status                            # 看到 modified 文件
git diff -- <key-file>                # 抽 1-2 个关键文件确认 hunks 真落地
# 如果 stash 跨多次 commit 冲突严重：用 git apply --3way 逐文件应用，不要靠 stash apply

# 3. commit 前
git status                            # untracked 里看到 storage/*.bak* / *.db-* 立即停手
git diff --cached --stat              # 检查 staged 文件清单符合预期
```

**反例**：

- ❌ 部署前跳过 `git stash list`，假设"stash 早就 pop 过了"——5 天工作消失。
- ❌ `git stash apply` exit 0 就当成功，不抽查 diff——hunks 静默跳过没察觉。
- ❌ 用 `git add -A` / `git add .` 而不审 staged 列表——把 storage 备份带进 commit。
- ❌ `.gitignore` 没覆盖 `*.bak*` / `*.db-shm`，靠每次提醒自己别 add。
