# TASK-YYYYMMDD-NN {短标题}

> 删掉这段开头说明再提交。保留本模板所有 H2 标题，填充内容。

## 状态

- [ ] 起草
- [ ] 执行中 @ 分支 task-YYYYMMDD-NN
- [ ] 已合并 @ commit XXXXXXX

## 目标

用 2-4 句话讲清楚「改完之后是什么样」。不要讲「为什么改」。

## 约束

- 这些信号 / API / 文件结构 **不准变**
- 这些命名 / 风格 **必须保持**
- 禁止引入新依赖（除非额外说明）

## 动的文件

精确到路径。prefer 动几个小文件，不要"views 目录下所有 .vue"。

- 修改：`path/to/file.ts`
- 新建：`path/to/new-file.vue`
- 删除：`path/to/legacy.py`

## 不准动

明确列出。**"仅涉及 admin/frontend"** 这种模糊说法不算数。

- 任何 `services/**/*.py`
- 任何 `config/**`
- `admin/frontend/src/stores/`

## 实施步骤

**可选**。如果你想给 codex 一步一步的路子，写在这里。留空 = 让它自己想。

1. Step A
2. Step B

## 验收

**每条都要能在命令行跑，能 0/非 0 判断通过**。

```bash
# 示例
cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit
cd admin/frontend && ./node_modules/.bin/vite build
grep -c "!important" admin/frontend/src/styles/global.css  # 期望 ≤ 20
```

## 用户复制命令段

每块独立可复制。**用户不应该需要改任何字符**。

### 1. 建分支（含 dirty-worktree 保护）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# stash 未提交改动，让 codex 从干净状态出发（已干净会 no-op）
git stash push -u -m "pre-task-YYYYMMDD-NN" 2>&1

# 用当前 HEAD 做基，别切 main（main 可能严重落后）
git checkout -b task-YYYYMMDD-NN

echo "branch ready; HEAD=$(git rev-parse --short HEAD)"
```

### 2. 交给 codex 执行

```bash
codex 'cd /Users/kragcola/OmubotWorkspace/omubot && 严格按照 .claude/handoff/TASK-YYYYMMDD-NN-slug.md 执行，只动该 spec 里「动的文件」列出的路径。完成后 git status 报告改了哪些文件。'
```

### 3. 本地验证

```bash
cd /Users/kragcola/OmubotWorkspace/omubot/admin/frontend
./node_modules/.bin/vue-tsc --noEmit && ./node_modules/.bin/vite build
# 以及 spec 验收段里的其他命令
```

### 4. 把 diff 给 Claude 审查

> **注意**：永远用 `git diff HEAD`（对比分支创建时快照），不要用 `git diff main`。main 可能严重落后。

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git diff HEAD | pbcopy
# 贴给 Claude 说 "审 TASK-YYYYMMDD-NN"
```

### 5. Claude 审核通过后合并 + 恢复 stash

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 提交到 task 分支
git add <spec 列出的动的文件>
git commit -m "<简短说明>"

# 如果 main 严重落后,不 merge 回 main
# 让 task 分支直接成为新工作分支，或稍后开大 PR 统一归整

# 恢复开工前 stash 的其他改动
git stash pop
```

### 6. 如果 codex 搞砸了要重来

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git checkout -
git branch -D task-YYYYMMDD-NN
git stash pop  # 恢复原本 stash 的改动
```

## 审查要点（给 Claude 看）

用户把 diff 贴来后，Claude 重点核对：

- [ ] "动的文件" 列表外的文件**没有**被动
- [ ] "不准动" 列表的文件 0 diff
- [ ] 验收命令全部通过
- [ ] 没有偷偷引入新依赖（`package.json` / `pyproject.toml` / `requirements.txt` 的 diff）
- [ ] 没有 `TODO` / `FIXME` / `// XXX` / `console.log` 残留

## 备注

可选。执行过程中发现的坑、为什么这么约束、参考链接等。
