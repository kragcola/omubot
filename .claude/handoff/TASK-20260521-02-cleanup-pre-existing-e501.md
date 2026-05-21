# TASK-20260521-02 清理 26 条 pre-existing E501 长行（HEAD baseline tech debt）

## 状态

- [x] 起草（2026-05-21）
- [ ] 执行中 @ 分支 task-20260521-02
- [ ] 已合并 @ commit

## 背景

2026-05-21 stash 全量恢复（commit `3477163`）后，`uv run ruff check` 还剩 **26 条 E501**。
经过 `git archive HEAD` 取 HEAD 干净副本独立 ruff 扫描，确认这 26 条**全部是恢复前就存在
的 pre-existing 长行**（HEAD baseline 78 条，恢复合入后净改善 52 条）。

剩余 26 条不阻断 ruff 工作流（其他规则 0 error），但累积已久，应在低风险窗口清掉。

**特征**：
- 13 条集中在 `services/plugin_index.py` 的长中文 governance hint 字符串（runtime 用户可见
  提示，会显示在 admin「插件总览」页里）
- 其他散落在 9 个文件，多是 httpx/aiohttp 长 URL、长字典字面量、长日志行

## 目标

`uv run ruff check` 输出 `All checks passed!`（0 errors）。

修复策略按字符串语义分两类：
1. **代码长行**（log 调用、httpx 客户端调用、字典字面量等）：按现有 ruff 风格手工换行
2. **services/plugin_index.py 的 governance hint 长中文字符串**：考虑单独走
   `[tool.ruff.lint.per-file-ignores]` 加 `services/plugin_index.py = ["E501"]`，因为这些
   是 admin UI 直接显示的提示文案，硬拆字符串会破坏阅读体验（同
   `plugins/schedule/generator.py` 与 `services/slang/shared_prefix.py` 的先例）

## 约束

- **不改任何 runtime 行为**：所有改动必须语义等价
- **字符串内容不变**：长中文 hint 只能整段加 `# noqa: E501` 或文件级 ignore，**不能改动
  实际显示给用户的文字**
- **不引入新依赖**
- **不动 `pyproject.toml` 已有 per-file-ignores 配置**（保持 `plugins/schedule/generator.py`
  和 `services/slang/shared_prefix.py` 现状）

## 动的文件

按 ruff 报告精确到 9 个文件：

| 文件 | 长行数 | 修复策略 |
|---|---|---|
| `services/plugin_index.py` | 13 | 走 per-file-ignores（中文 hint 文案，不换行） |
| `admin/routes/api/providers.py` | 4 | 手工换行 |
| `services/llm/providers/deepseek.py` | 2 | 手工换行 |
| `services/health.py` | 2 | 手工换行 |
| `services/tools/web_fetch.py` | 1 | 手工换行 |
| `services/llm/usage.py` | 1 | 手工换行 |
| `plugins/context/plugin.py` | 1 | 手工换行 |
| `kernel/bus.py` | 1 | 手工换行 |
| `admin/routes/api/plugins.py` | 1 | 手工换行 |

具体行号（ruff 报告）：

```
services/plugin_index.py: 177, 178, 230, 243, 244, 421, 431, 434, 436, 475, 492, 499, 515
admin/routes/api/providers.py: 226, 227, 258, 407
services/llm/providers/deepseek.py: 181, 191
services/health.py: 323, 327
services/tools/web_fetch.py: 98
services/llm/usage.py: 220
plugins/context/plugin.py: 91
kernel/bus.py: 148
admin/routes/api/plugins.py: 293
```

需要修改的文件：

- 修改：`pyproject.toml`（追加 `services/plugin_index.py = ["E501"]`，理由注释同先例）
- 修改：`admin/routes/api/providers.py`
- 修改：`admin/routes/api/plugins.py`
- 修改：`services/llm/providers/deepseek.py`
- 修改：`services/health.py`
- 修改：`services/tools/web_fetch.py`
- 修改：`services/llm/usage.py`
- 修改：`plugins/context/plugin.py`
- 修改：`kernel/bus.py`

## 不准动

- `services/plugin_index.py` 的字符串内容（仅 `pyproject.toml` 加 ignore，**不改 .py**）
- 任何 `tests/`
- 任何 `admin/frontend/`
- 任何 `config/` / `storage/` / `docs/` / `.claude/`
- `services/slang/shared_prefix.py` 与 `plugins/schedule/generator.py` 的现有 ignore（保留）
- `uv.lock` 必然 0 diff

## 实施步骤

1. 跑 `uv run ruff check 2>&1 > /tmp/ruff-baseline.txt`，确认 26 errors
2. `pyproject.toml`：在 `[tool.ruff.lint.per-file-ignores]` 块追加：
   ```toml
   "services/plugin_index.py" = ["E501"]  # 插件治理面板长中文 hint 文案
   ```
3. 9 个 `.py` 文件中，**不属于 plugin_index.py** 的 13 条长行逐条手工换行：
   - 长 log/print：参数拆多行、用 `f"..."` 拼接
   - 长 httpx 调用：把 kwargs 拆到独立行
   - 长字典字面量：每个 key 一行
   - 不要用 `# noqa: E501`，先尝试换行；只在换行后语义明显变差时才考虑 ignore（spec 不
     预期出现这种情况）
4. 每改完一个文件就跑一次 `uv run ruff check <file>` 确认该文件 0 error
5. 最后跑完整 `uv run ruff check` 确认全局 `All checks passed!`
6. `uv run pytest -q` 确认 1216 passed 不退步
7. `uv run pyright` 确认 0 errors 不退步

## 验收

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1. ruff 全绿
uv run ruff check 2>&1 | tail -1 | grep -q 'All checks passed' && echo "OK-ruff-clean"

# 2. pyright 不退步
uv run pyright 2>&1 | tail -1 | grep -qE '0 errors' && echo "OK-pyright"

# 3. pytest 不退步
uv run pytest -q 2>&1 | tail -3 | grep -qE '1216 passed|passed' && echo "OK-pytest"

# 4. pyproject.toml 含新增 ignore（且只加这一条）
grep -q '"services/plugin_index.py" = \["E501"\]' pyproject.toml && echo "OK-ignore-added"

# 5. plugin_index.py 字符串未改（diff 只允许 0 行）
git diff HEAD -- services/plugin_index.py | wc -l | xargs -I{} test {} -eq 0 && echo "OK-plugin-index-untouched"

# 6. 没动 frontend / tests / docs
git diff --name-only HEAD | grep -qE '^(admin/frontend|tests/|docs/|config/|storage/)' && echo "FAIL-out-of-scope" || echo "OK-scope-clean"

# 7. 没动 napcat / docker-compose
git diff --name-only HEAD | grep -qE '^(docker-compose|napcat/)' && echo "FAIL-touched-infra" || echo "OK-infra-untouched"
```

7 行 `OK-*` 全输出才算通过。

## 用户复制命令段

### 1. 建分支

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

git stash push -u -m "pre-task-20260521-02" 2>&1
git checkout -b task-20260521-02

uv run ruff check 2>&1 | tail -1
# 期望: Found 26 errors.
echo "branch ready; HEAD=$(git rev-parse --short HEAD)"
```

### 2. 交给 codex 执行

```bash
codex 'cd /Users/kragcola/OmubotWorkspace/omubot && 严格按照 .claude/handoff/TASK-20260521-02-cleanup-pre-existing-e501.md 执行。修 26 条 E501 长行：services/plugin_index.py 的 13 条走 pyproject.toml per-file-ignore（理由：插件治理面板长中文 hint 文案，同 services/slang/shared_prefix.py 先例），其余 9 个文件的 13 条手工换行。改完后跑 spec 验收 7 条命令，全部输出 OK-*。'
```

### 3. 本地验证

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 验收命令见 ## 验收
```

### 4. 把 diff 给 Claude 审查

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git diff HEAD | pbcopy
# 贴给 Claude 说 "审 TASK-20260521-02"
```

### 5. Claude 通过后提交

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

git add pyproject.toml \
        admin/routes/api/providers.py admin/routes/api/plugins.py \
        services/llm/providers/deepseek.py services/llm/usage.py \
        services/health.py services/tools/web_fetch.py \
        plugins/context/plugin.py kernel/bus.py

git commit -m "$(cat <<'EOF'
chore(lint): clear 26 pre-existing E501 long-line warnings

13 条 services/plugin_index.py 走 per-file-ignore（长中文 hint 文案，
同 services/slang/shared_prefix.py 与 plugins/schedule/generator.py 先例）。
其余 9 个文件 13 条手工换行，无 runtime 行为变化。

ruff 0 errors / pyright 0 errors / pytest 1216 passed 不退步。
EOF
)"

git stash pop
```

### 6. 失败回滚

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git checkout -
git branch -D task-20260521-02
git stash pop
```

## 审查要点（给 Claude 看 diff 时过一遍）

- [ ] `pyproject.toml` 只追加一条 ignore（services/plugin_index.py），位置在
      `[tool.ruff.lint.per-file-ignores]` 段，带说明注释
- [ ] 已有的两条 per-file ignore（schedule/generator.py、slang/shared_prefix.py）0 diff
- [ ] `services/plugin_index.py` **0 diff**（只通过 ignore 解决）
- [ ] 其余 9 个文件的换行修复**不改变字符串内容**（grep 检查关键词仍存在）
- [ ] 没动 frontend / tests / docs / config / storage / napcat / docker-compose
- [ ] 没引入 `# noqa: E501`（除非换行后语义明显变差且 spec 已 explicit 允许）
- [ ] ruff `All checks passed!`，pyright 0 errors，pytest 1216 passed

## 备注

### 为什么 plugin_index.py 走 ignore 而不是换行

13 条长行全部是这种结构：

```python
return ("attention", "签名校验异常", "修复 plugin.sig 的指纹或来源声明，避免继续装载来源不明或已漂移的插件包。")
```

这个第三段中文 hint 直接显示在 admin 「插件总览」页面对应行的「建议」列里，硬拆成多行
拼接（`"...，" "..."`）会让维护者读源码时丢失"这是一句完整提示"的语义。同先例：

- `plugins/schedule/generator.py` — 系统提示词含长中文行
- `services/slang/shared_prefix.py` — 共享缓存前缀含长中文系统提示

把 plugin_index.py 列入同名单是一致选择。

### 为什么不在 pyproject.toml 全局 ignore E501

E501 在其他文件（特别是 admin/routes/api/*）抓到的多是 httpx 长 URL、长字典字面量，这些
**应该**换行。全局 ignore 等于放弃整个 codebase 的行宽治理。per-file 是正确粒度。

### 与 stash 恢复的关系

stash 恢复期（commit `3477163`）已修了 6 条 stash 引入的 E501（plugins/calendar_context/
service.py 4 条 + services/slang/shared_prefix.py 11 条走 ignore），HEAD 净改善 52 条。
本 spec 处理的是恢复前就存在、与本次 stash 无关的 26 条历史债。
