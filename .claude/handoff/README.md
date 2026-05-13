# .claude/handoff/ — codex 执行规范目录

本目录存放交给 codex（或任何外部 AI/人工）执行的任务规范（spec）。spec 是"判断已经做完、只剩机械执行"的任务说明书。

## 命名规范

`TASK-YYYYMMDD-NN-slug.md`

- `YYYYMMDD`：创建日期
- `NN`：当日序号 01 开始
- `slug`：短 kebab-case 描述

示例：

- `TASK-20260514-01-remove-redundant-important.md`
- `TASK-20260514-02-split-slang-view.md`

## 生命周期

1. **起草** — Claude 写 spec，提交到主分支
2. **分支** — 用户为每个 task 建独立 git 分支 `task-YYYYMMDD-NN`
3. **执行** — codex 在该分支上按 spec 实现
4. **验收** — 用户把 `git diff main` 贴给 Claude，Claude 审查
5. **合并** — 通过后 merge 到主分支，spec 前言加 `## 状态：✅ 已合并 @ <commit>`

## 编写要求

- **明确交付物**：具体到文件路径（动的 / 不准动的）
- **明确约束**：哪些 API 形状 / 信号不能变
- **可验收**：给一组能在命令行跑的验证命令
- **不含判断**：不要写"优雅地处理"这种需要判断的词；要写"保持现有 try/except 行为"
- **长度**：一个 spec 不要超过 300 行。超了说明任务该拆

## spec 模板见 [TEMPLATE.md](TEMPLATE.md)

## 什么任务适合扔给 codex

看 `docs/agent-collaboration.md`（如果存在）或与 Claude 讨论。基本原则：

✅ 机械转换 / 照表执行 / 规则明确的样板代码
❌ 视觉设计 / 信息架构 / 跨层贯穿改动 / 调试 / 安全相关

## 审查流程（用户操作手册）

每个 spec 底部都带「复制到终端」命令段，照做即可。流程永远是：

```bash
# 1. stash 未提交改动 + 起分支
git stash push -u -m "pre-task-...";  git checkout -b task-YYYYMMDD-NN

# 2. codex 执行（命令在 spec 里）
codex "..."

# 3. 验证（命令在 spec 里）
...

# 4. 把 diff 贴给 Claude 审查 —— 用 HEAD 不用 main！
git diff HEAD | pbcopy

# 5. Claude 确认后提交 + 恢复 stash
git commit -m "..."
git stash pop
```

### 为什么不用 `git diff main`

工作区常常有一批未提交的改动（整轮重构等）。main 可能严重落后，`git diff main` 会把所有改动都列出来，无法验证 codex 是否只动了 spec 允许的文件。**`git diff HEAD`** 对比的是分支创建时的快照，精确反映 codex 的改动。
