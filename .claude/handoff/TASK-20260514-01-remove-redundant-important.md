# TASK-20260514-01 删除 global.css 里冗余的 !important 块

## 状态

- [x] 起草
- [x] 已通过 Claude 干跑验证（2026-05-14）
- [ ] 执行中 @ 分支 task-20260514-01
- [ ] 已合并 @ commit

## 背景

前一轮重构中，[admin/frontend/src/stores/app.ts](../../admin/frontend/src/stores/app.ts) `buildThemeOverrides()` 已覆盖 Tag / DataTable / placeholder / icon 等 Naive UI 内部 token，[admin/frontend/src/styles/global.css](../../admin/frontend/src/styles/global.css) 里对应的 `.dark .n-*` 深度选择器变成冗余。冗余块用 `@audit redundant` 注释标出，可一把删除。

playground 已由用户视觉验收通过（2026-05-13 阶段 0-2 交接），现在可以清理。

**干跑结果**：Claude 本地模拟执行通过——删除后 `!important` 从 51 降至 **31**（不是 spec 原本预期的 ≤ 20），`vue-tsc` 0 error，`vite build` 4.72s 通过。

## 目标

把 [global.css](../../admin/frontend/src/styles/global.css) 里**唯一一个**以 `/* @audit redundant` 开头的标注注释**以及它后面所有 CSS 规则，直到下一个以 `/* @audit` 开头的注释之前**全部删除。

具体：删除文件中当前的 **line 199–244**（含）。

保留所有 `@audit keep` 标注块（line 143 和 line 160 两块）。

改完后：

- `!important` 数从 51 → **31**
- 标注计数 `@audit redundant`（排除文件头说明注释）从 1 → 0
- `@audit keep` 保持 2

## 约束

- **不改** [app.ts](../../admin/frontend/src/stores/app.ts) 的 `themeOverrides` 结构
- **不改** `@audit keep` 块的任何内容
- **不改** 文件开头（line 127–141）的文档注释块 —— 它里面**出现 `@audit redundant` 字符串**但那是说明文字，不是标注
- **不新增**任何 CSS 规则

## 动的文件

- 修改：`admin/frontend/src/styles/global.css`

## 不准动

- 任何 `admin/frontend/src/*.ts` / `*.vue`
- 任何 `admin/frontend/src/stores/`
- 任何 `admin/frontend/src/components/`
- 任何 `*.py`
- 任何 `docs/` / `.claude/`
- `package.json` / `pnpm-lock.yaml` / `uno.config.ts`

## 实施步骤

1. 读 `admin/frontend/src/styles/global.css`
2. 找到**标注注释**（不是 header 说明）：以 `/* @audit redundant` 开头且独占一行的那一处。当前位于 line 199
3. 从该行起**连续删除**，直到（但不包括）下一个以 `/* @audit keep` 开头的注释 —— 当前这意味着删除 line 199 到当前 file 末尾前最后一块 `.dark .n-data-table` 的结束（含 `}` 以及紧随的空行）。具体是 line 199–244
4. **不要删**文件末尾原有的其他内容（如果有）—— 实际上 line 244 后就是文件末尾空行，删到那里即可
5. 删完后用下面"验收"段的 6 条命令核查

## 验收

**每条在命令行必须通过，期望输出全部以 `OK-` 开头**：

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 1. !important 数已降到 31
count=$(grep -c '!important' admin/frontend/src/styles/global.css)
[ "$count" = "31" ] && echo "OK-important-count=$count"

# 2. @audit redundant 标注（独占一行的注释开头）已清零
#    排除文件头说明里引用 `@audit redundant` 字符串的那一行
count=$(grep -c '^/\* @audit redundant' admin/frontend/src/styles/global.css)
[ "$count" = "0" ] && echo "OK-redundant-marker-cleared"

# 3. @audit keep 保持 2
count=$(grep -c '^/\* @audit keep' admin/frontend/src/styles/global.css)
[ "$count" = "2" ] && echo "OK-keep-retained=$count"

# 4. vue-tsc 0 error
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit && echo "OK-tsc"

# 5. vite build 成功
./node_modules/.bin/vite build >/dev/null 2>&1 && echo "OK-build"

# 6. 本次分支只动了这一个文件
#    注意：对比 base = 分支创建时的 HEAD，不是 main
cd /Users/kragcola/OmubotWorkspace/omubot
changed=$(git diff --name-only HEAD | awk '{print $1}' | sort -u | tr '\n' ' ')
[ "$changed" = "admin/frontend/src/styles/global.css " ] && echo "OK-only-global-css-changed"
```

六行 `OK-*` 全输出才算通过。

> **注意**：spec **使用 `git diff HEAD`**（相对分支创建时的快照）而不是 `git diff main`，因为工作区可能有其他未提交改动（比如整轮 admin 重构）。`HEAD` 指的是 codex 开始工作前的提交点。

## 用户复制命令段

### 1. 建分支（含 dirty-worktree 保护）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 先把当前未提交的改动 stash 起来,让 codex 从干净状态出发
# 如果已经干净会 no-op
git stash push -u -m "pre-task-20260514-01" 2>&1

# 用当前 HEAD 做基，不要切到 main (main 落后严重)
git checkout -b task-20260514-01

echo "branch ready; current HEAD: $(git rev-parse --short HEAD)"
```

### 2. 让 codex 执行

```bash
codex 'cd /Users/kragcola/OmubotWorkspace/omubot && 严格按照 .claude/handoff/TASK-20260514-01-remove-redundant-important.md 执行。只动 admin/frontend/src/styles/global.css 一个文件。删除以 "/* @audit redundant" 开头（独占一行）的标注注释以及它到下一个 @audit 注释之前的所有 CSS 规则（当前位于 line 199-244）。保留所有 @audit keep 块和文件头部 /* ==== */ 文档注释。删完后跑 spec 验收段里 6 条命令，全部输出 OK-* 才算完成。'
```

### 3. 本地验证（期望输出 6 行 OK-*）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

count=$(grep -c '!important' admin/frontend/src/styles/global.css)
[ "$count" = "31" ] && echo "OK-important-count=$count"

count=$(grep -c '^/\* @audit redundant' admin/frontend/src/styles/global.css)
[ "$count" = "0" ] && echo "OK-redundant-marker-cleared"

count=$(grep -c '^/\* @audit keep' admin/frontend/src/styles/global.css)
[ "$count" = "2" ] && echo "OK-keep-retained=$count"

cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit && echo "OK-tsc"
./node_modules/.bin/vite build >/dev/null 2>&1 && echo "OK-build"

cd /Users/kragcola/OmubotWorkspace/omubot
changed=$(git diff --name-only HEAD | awk '{print $1}' | sort -u | tr '\n' ' ')
[ "$changed" = "admin/frontend/src/styles/global.css " ] && echo "OK-only-global-css-changed"
```

### 4. 视觉抽验（重建 bot，不动 napcat）

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
dot_clean . 2>/dev/null
docker compose up bot -d --build 2>&1 | tail -5
```

浏览器打开 http://localhost:8081/admin/，切浅 / 深主题，Dashboard / Config / Logs 看一遍。NCard / NInput / NSelect / NTag / NDrawer / NModal / NDataTable 在深色模式下颜色、边框正常即可。

### 5. 把 diff 贴给 Claude 审查

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
git diff HEAD | pbcopy
```

Claude 聊天里发："**审 TASK-20260514-01**" + 粘贴。

### 6. Claude 通过后合并 + 恢复 stash

```bash
cd /Users/kragcola/OmubotWorkspace/omubot

# 提交到 task 分支
git add admin/frontend/src/styles/global.css
git commit -m "$(cat <<'EOF'
chore(admin-ui): remove redundant !important blocks from global.css

依据 .claude/handoff/TASK-20260514-01-remove-redundant-important.md 执行。
Naive UI themeOverrides 已覆盖的 .dark .n-* 深度选择器全部移除，
!important 计数从 51 降至 31。
EOF
)"

# 注意:由于 main 严重落后,这里不 merge 回 main
# 改为让 task 分支直接成为新的 working 分支
# 或者如果你之后想规整,手动在新日子开个大 PR 统一合并

# 恢复开工前 stash 的其他改动
git stash pop
```

### 7. 要全部丢弃重来

```bash
cd /Users/kragcola/OmubotWorkspace/omubot
# 回到开工前的 HEAD
git checkout -
git branch -D task-20260514-01
git stash pop  # 恢复原本 stash 的改动
```

## 审查要点（给 Claude 看 diff 时过一遍）

- [ ] diff 只涉及 `admin/frontend/src/styles/global.css` 一个文件
- [ ] 删除行数 ≈ 46 行（line 199 到 244 含尾部空行）
- [ ] 文件头部 line 127-141 的 `/* ==== Naive UI deep-selector overrides ==== */` 文档注释**完整保留**（里面那句 `@audit redundant` 字样是说明文字）
- [ ] 两个 `@audit keep` 块（line 143、160）完整保留，内容一字不动
- [ ] 没有新增任何 CSS 规则
- [ ] `grep -c "!important" global.css` 刚好等于 31（不是 ≤ 31，也不是 20）
- [ ] 没有 `package.json` / `uno.config.ts` / `stores/app.ts` 的 diff

## 备注

### 干跑验证结果（2026-05-14 Claude 本地执行）

- 删除 line 199-244 后：`!important` 从 51 → 31、`vue-tsc` 0 error、`vite build` 4.72s 通过
- 起初 spec 预期 `!important` ≤ 20，实际只能到 31，因为两个 `@audit keep` 块（NButton + NMenu）本身就占了约 30 处 `!important`。spec 已改为精确等于 31
- `@audit redundant` 在文件里出现 2 次：1 次是独占一行的标注（line 199），1 次是文件头部说明（line 134 的说明性文字）。**验收命令已改为只 grep 行首 `/\*`** 避免误匹配说明

### 为什么这是适合 codex 的任务

- 目标文件精确到 line 199-244
- 验收命令全部是 0/非 0 判断
- 纯删除，不需要发明任何新内容
- 风险极低：改错也只是样式问题，Claude 审查能立刻发现

