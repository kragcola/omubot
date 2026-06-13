# Omubot Agent Continuity 方案 — 防止 Codex/Claude 压缩后失忆

> 状态：方案草案，未实施
> 创建时间：2026-06-06
> 目标：把“压缩上下文后基本从头再来”的问题，改造成可恢复、可验证、可迭代的工程流程。
> 适用范围：Omubot 仓库内 Codex / Claude / 人工协作；尤其是长任务、bug 调试、skill/hook/workflow 修改、角色包/生产运行态变更。

---

## 0. 一句话结论

不要试图只做一个“Codex 版 TodoWrite”。

Omubot 需要的是四件套：

1. **临时 live state**：记录当前会话正在发生什么，避免压缩时丢现场。
2. **项目级 active tracker**：记录当前活跃任务入口，避免新会话不知道该读哪个文档。
3. **continuity skill**：规定恢复、建档、更新、收口的工作流。
4. **SessionStart / PostToolUse / 可选 PreCompact hooks**：把状态自动注入下一轮上下文，降低“靠模型自觉”的比例。

最终状态应是：

- 用户说“继续”，agent 先读 active tracker，不重新考古。
- 压缩后恢复，agent 能说出目标、下一步、阻塞、验证证据和回滚入口。
- bug 调试恢复时，agent 先读 test ledger，不重复跑已排除命令。
- `maintenance-log.md` 继续只记录 durable 交付事实，不变成逐轮 todo。

---

## 1. 背景问题

当前现象：

- Codex 上下文压缩后，容易丢失用户最新意图、已排除路径、当前 next step、刚才为什么做某个选择。
- 新会话接手时，虽然能读 wiki / maintenance-log / git status，但缺一个“当前活跃任务入口”，于是倾向重新全仓侦查。
- 本仓已有 `docs/tracking/*`、`.claude/handoff/*`、`maintenance-log.md` 和 `.codex/hooks.json`，但它们没有收敛成统一恢复协议。

根因：

- todo 只活在聊天或工具内部，不是 durable project state。
- `maintenance-log.md` 是倒序运维事实，不适合承载“下一步正在做什么”。
- `.claude/handoff/` 旧体系偏“给 codex 执行的 spec”，不是 live continuity。
- 当前 `scripts/dev/codex-session-start.py` 只注入 maintenance-log 顶部和 bot 日志，不注入 active task。

---

## 2. 外部成熟实践考察

### 2.1 拉取样本

已拉取并本地分析：

| 来源 | URL | 本地路径 | commit | 用途 |
| --- | --- | --- | --- | --- |
| Anthropic 官方 skills | `https://github.com/anthropics/skills` | `/tmp/anthropic-skills` | `da20c92` | 官方 skill 写法、progressive disclosure、scripts/resources |
| Anthropic 官方 Claude Code plugins | `https://github.com/anthropics/claude-code` | `/tmp/anthropic-claude-code` | `feabcc3` | commands、agents、hooks、TodoWrite 使用方式 |
| 社区 handoff | `https://github.com/Sonovore/claude-code-handoff` | `/tmp/claude-code-handoff` | `c6cb717` | compact/session handoff 专项实践 |
| 社区 office skills | `https://github.com/claude-office-skills/skills` | `/tmp/claude-office-skills` | `9c4c7d5` | 大规模 skill 库反面/补充样本 |

也参考官方文档：

- Claude Code hooks: `https://docs.anthropic.com/en/docs/claude-code/hooks`
- Claude Code skills: `https://docs.anthropic.com/en/docs/claude-code/skills`

### 2.2 Anthropic 官方 skills 的启示

样本：`/tmp/anthropic-skills/skills/skill-creator/SKILL.md`、`webapp-testing/SKILL.md`、`mcp-builder/SKILL.md`、`docx/SKILL.md`。

观察：

- 官方 skills 重点是 **领域工作流 + 资源**，不是 session todo。
- 好 skill 的触发信息都写在 frontmatter description，正文不再重复“何时使用”。
- 复杂技能用 `scripts/`、`references/`、`examples/` 做 progressive disclosure。
- `webapp-testing` 明确要求先运行脚本 `--help`，不要先读大脚本源码，避免污染上下文。
- `skill-creator` 把验证当作技能生命周期的一部分：写 skill 后要用真实 prompts 测试，而不是只看语法。

对 Omubot 的影响：

- `omubot-continuity` 不应写成巨型百科。
- `SKILL.md` 只放恢复流程和硬规则。
- 详细模板、hook 设计、测试计划可以放 `docs/tracking/` 或未来 `references/`。
- 需要设计验收，而不是“写完感觉会用”。

### 2.3 Anthropic 官方 Claude Code plugins 的启示

样本：

- `/tmp/anthropic-claude-code/plugins/feature-dev/commands/feature-dev.md`
- `/tmp/anthropic-claude-code/plugins/pr-review-toolkit/commands/review-pr.md`
- `/tmp/anthropic-claude-code/plugins/hookify/commands/hookify.md`
- `/tmp/anthropic-claude-code/plugins/plugin-dev/skills/hook-development/SKILL.md`

观察：

- TodoWrite 主要出现在 **command orchestration**，不是 skill 本体。
- `feature-dev` 命令明确要求 “Use TodoWrite”，并用 7 个阶段推进：Discovery、Exploration、Questions、Architecture、Implementation、Review、Summary。
- `hookify` 命令用 `Task` 子代理分析近期对话，再创建 hook rules，并要求 TodoWrite 跟踪步骤。
- `pr-review-toolkit` 把 review 拆给多个专用 agent，最后聚合为 Critical / Important / Suggestions。
- hook 体系承担“自动提醒 / 自动注入 / Stop 复核”的角色，是防遗忘的重要层。

对 Omubot 的影响：

- Codex 没有 Claude Code 的 TodoWrite UI 时，应该用 `update_plan` + tracker 文档替代。
- 长任务应有一个 command-like workflow，但在 Codex 中可通过 skill 触发。
- 对于“压缩后恢复”，skill 不够，需要 hook 把 active state 注入上下文。

### 2.4 社区 `claude-code-handoff` 的启示

样本：

- `/tmp/claude-code-handoff/README.md`
- `/tmp/claude-code-handoff/handoff.md`
- `/tmp/claude-code-handoff/hooks/live-handoff.sh`
- `/tmp/claude-code-handoff/hooks/pre-compact-handoff.sh`

核心设计：

- 自动 live handoff：每条用户消息通过 `UserPromptSubmit` 注入 directive，要求 Claude 更新 `.claude/session-state.md`。
- `PostToolUse` 记录修改过的文件。
- `PreCompact` 在压缩前重新注入 handoff 文件，并强制写 session-state。
- 手动 `/handoff` 命令分 Context / Task / Bug / Clean 四种模式。
- Task handoff 重点写 “Next Session Starts Here”，不是写历史流水。
- Bug handoff 保留 append-only `bug-test-log.md`，每次测试记录 exact command、actual result、conclusion。

最值得吸收的原则：

- **写给下一个上下文窗口行动，而不是写给历史归档。**
- 已完成事实可从 git / code / logs 恢复；有限预算应优先给 next step、open questions、expensive empirical results。
- bug investigation 必须有 test ledger，否则压缩后会重复跑死路。

不能照搬的地方：

- 它用 `.claude/session-state.md` 做中心状态；Omubot 需要兼容 Codex / Claude / 人工，不应把 canonical state 放在 `.claude`。
- 它的 UserPromptSubmit 每轮注入可能很吵；Codex 当前 hook 能力和 Claude Code 不完全一致，先做低噪声版本。
- 它的文件默认是 session-specific ignored state；Omubot 的长任务还需要部分状态进 `docs/tracking`，供未来 agent 和用户审计。

### 2.5 社区 office skills 的警示

样本：`/tmp/claude-office-skills`，共 137 个 `SKILL.md`。

观察：

- 数量多，但大多是长篇 monolithic skill，缺少 `scripts/` / `references/` 分层。
- 平均 400+ 行，不适合作为防失忆方案的主样本。
- 价值在于展示“一个 skill 包打天下”的反面：容易上下文膨胀，也难验证。

对 Omubot 的影响：

- 不要把 continuity 方案写成 500 行以上巨型 skill。
- 复杂模板和研究记录放 docs，skill 只做触发和恢复协议。

---

## 3. Omubot 现有资产评估

### 3.1 可复用资产

| 资产 | 现状 | 复用方式 |
| --- | --- | --- |
| `AGENTS.md` | 已有工作区、D1-D7、skill trigger、maintenance-log 政策 | 加一小段 Continuity Rule，做路由，不放长模板 |
| `.codex/hooks.json` | 已有 `SessionStart` 和 `PostToolUse` 提醒 | 扩展 SessionStart 注入 active tracker；PostToolUse 只提醒，不强制每次写 docs |
| `scripts/dev/codex-session-start.py` | 读取 maintenance-log 顶部和 bot log | 增加 ACTIVE / live state / active tracker 摘要 |
| `docs/tracking/` | 已有大量执行/设计方案 | 增加 ACTIVE 和 continuity tracker 模板 |
| `maintenance-log.md` | durable 运维/交付记录 | 只在 workflow/docs/skill/hook durable 变更时写，不做 todo |
| `.claude/handoff/` | 旧 spec 执行规范目录 | 保留；可借鉴模板思想，不替换为 session state |
| `.workspace/` | gitignored 本地状态目录 | 可存 live session state，避免 git churn |

### 3.2 当前缺口

1. 没有 `docs/tracking/ACTIVE.md`，无法定位“当前活跃任务”。
2. 没有统一 tracker 顶部结构，很多 tracking 文档很长，恢复时仍需搜索。
3. SessionStart 不读 active tracker。
4. 没有 Codex/Claude 通用的 continuity skill。
5. 没有 bug test ledger 的强制规范。
6. 没有“何时只用 update_plan，何时必须建 tracker”的阈值。

---

## 4. 目标架构

### 4.1 状态分层

| 层级 | 文件 | 是否进 git | 作用 |
| --- | --- | --- | --- |
| Live session state | `.workspace/agent-session-state.md` | 否 | 当前会话临时状态，低成本更新，压缩恢复兜底 |
| Active index | `docs/tracking/ACTIVE.md` | 是 | 当前活跃任务入口，一屏可读 |
| Task tracker | `docs/tracking/<task>.md` | 是 | 任务目标、next step、todo、验证、回滚 |
| Bug ledger | tracker 内 `## Test Ledger`，或 `docs/tracking/<bug>-test-log.md` | 是 | append-only 实验证据，防重复排查 |
| Delivery log | `maintenance-log.md` | 本仓当前 gitignored，但作为本地 durable 事实源 | 已交付/部署/流程变更事实 |
| Spec handoff | `.claude/handoff/TASK-*.md` | 部分放行 | 已判断清楚、可交给 codex 机械执行的 spec |

### 4.2 恢复读序

恢复或压缩后，agent 必须按这个顺序读：

1. `AGENTS.md`
2. `.workspace/agent-session-state.md`（存在则读）
3. `docs/tracking/ACTIVE.md`
4. ACTIVE 指向的 tracker 顶部到 `## Test Ledger` 前
5. 如果 mode 是 bug / task-bug，读取 `## Test Ledger` 最近 20 条或独立 ledger 文件末尾
6. `git status --short`
7. 只读与 next step 直接相关的代码/文档

禁止恢复时默认从 `docs/wiki/Home.md` 或全仓架构文档重新开始，除非：

- ACTIVE 缺失；
- tracker 缺失；
- 用户明确要求重新审计；
- tracker 内容与 git/status 明显矛盾。

### 4.3 更新频率

| 情况 | 更新位置 |
| --- | --- |
| 小任务，少于 15 分钟，少于 3 个文件 | 只用 `update_plan`，可不建 tracker |
| 长任务，跨多个文件，或用户说“不要失忆” | 建/更新 `docs/tracking/<task>.md` |
| 生产、角色包、skill/hook/workflow、部署 | tracker + `maintenance-log.md` |
| bug 调试每跑一个有意义命令 | append `Test Ledger` |
| 每次明显改变方向/决策 | 更新 tracker 的 `Next Session Starts Here` / `Decisions` |
| 每次文件编辑 | hook 可写 live state 或提醒；不强制改 tracker |
| final 前 | tracker 收口；durable 变更则 maintenance-log |

---

## 5. 新增文件设计

### 5.1 `docs/tracking/ACTIVE.md`

建议内容：

```markdown
# Active Omubot Work

> 这个文件是压缩/新会话恢复入口。保持短，不写历史流水。

## Current

- mode: none
- tracker:
- objective:
- status:
- next_step:
- last_verified:
- rollback:

## Recovery Order

1. Read this file.
2. Read the tracker above.
3. Read `.workspace/agent-session-state.md` if present.
4. Run `git status --short`.
5. Continue from `next_step`.

## Notes

- If `mode=none`, do not invent an active task.
- If tracker is missing, fall back to `maintenance-log.md` and ask/inspect narrowly.
```

模式取值：

- `none`：无活跃任务。
- `task`：普通多轮任务。
- `bug`：纯 bug 调试。
- `task-bug`：任务被 bug 阻塞。
- `review`：审查/评估类任务。

### 5.2 tracker 顶部模板

所有新长任务 tracker 顶部应包含：

```markdown
# <任务标题>

> 状态：draft | active | blocked | verifying | done
> mode: task | bug | task-bug | review
> 最后更新：YYYY-MM-DD HH:MM CST
> 当前下一步：
> 阻塞：
> 验证证据：
> 回滚入口：

## Next Session Starts Here

- Direction:
- First action:
- Open questions:
- Do not redo:

## Todo

- [ ] pending item
- [~] in progress item
- [x] completed item
- [!] risk / blocked item

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |

## Files Touched

| File | Change | Status |
| --- | --- | --- |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |

## Test Ledger

Append-only when debugging. Every meaningful test needs exact command, actual result, and conclusion.

## Handoff

Use this only when pausing or handing off. Keep it short and point back to Next Session Starts Here.
```

### 5.3 `.workspace/agent-session-state.md`

建议由 hook 或 agent 手动维护，gitignored：

```markdown
# Agent Session State

Auto-maintained local state. Do not treat as source of truth for completed delivery.

## Current Focus

## Decisions Since Last Tracker Update

## Files Edited This Session

## Gotchas / Do Not Redo

## Next Step
```

约束：

- 控制在 80 行以内。
- 只写“压缩会丢的非显然信息”。
- 不写 secrets、tokens、私聊内容原文。
- 如果内容需要长期保留，提升到 `docs/tracking/<task>.md`。

---

## 6. 新 skill 设计：`omubot-continuity`

### 6.1 位置

建议创建并镜像：

```text
.agents/skills/omubot-continuity/SKILL.md
.claude/skills/omubot-continuity/SKILL.md
```

如需 Codex 全局可见，再安装/同步到全局 skill 目录。

### 6.2 frontmatter 草案

```yaml
---
name: omubot-continuity
description: Omubot context-continuity workflow for resuming long-running work without rediscovery. Use when the user says continue/接着/恢复/别重新开始/防止失忆, after context compaction or a new session, before ending or handing off a long task, when a task spans multiple files or sessions, during bug investigations that need a test ledger, or when changing agent prompts, skills, hooks, workflow docs, deployment, runtime, character packs, or production-facing behavior.
---
```

### 6.3 SKILL.md 正文草案

只放核心流程，控制在 200 行以内：

```markdown
# Omubot Continuity

Use this skill to resume, checkpoint, and hand off Omubot work without losing context after compaction.

## Recovery Protocol

1. Read `AGENTS.md`.
2. Read `.workspace/agent-session-state.md` if present.
3. Read `docs/tracking/ACTIVE.md`.
4. Read the active tracker named in ACTIVE.
5. If mode is `bug` or `task-bug`, read the Test Ledger before running commands.
6. Run `git status --short`.
7. State the recovered objective, next step, risks, and what will not be re-investigated.

## Tracker Threshold

Create or update a tracker when any condition holds:

- task likely exceeds 15 minutes
- task touches 3+ files
- task spans sessions or user says not to lose context
- task involves production runtime, deployment, character packs, skills, hooks, prompts, workflow rules
- bug investigation requires multiple experiments

For small one-turn tasks, use `update_plan` only.

## Update Protocol

- Keep `docs/tracking/ACTIVE.md` short.
- Put live todo, decisions, verification, and rollback in the active tracker.
- Use `.workspace/agent-session-state.md` for temporary session state.
- Append every meaningful bug experiment to Test Ledger.
- Update `maintenance-log.md` only when durable project behavior or future-agent workflow changes.

## Resume Output

After recovery, report:

- objective
- current status
- next concrete action
- files likely involved
- evidence already established
- dead ends not to retry

## Stop / Handoff Protocol

Before final response on long tasks:

1. Update tracker next step and verification.
2. If done, set ACTIVE mode to `none` or point to remaining work.
3. If durable change, update `maintenance-log.md`.
4. Report what was verified and what remains.
```

### 6.4 与现有 skill 的关系

| 场景 | 使用 skill |
| --- | --- |
| 恢复/继续/压缩后 | `omubot-continuity` |
| Admin/frontend 变更 | `omubot-continuity` + `omubot-admin-console` + 必要时 `omubot-design-system` |
| prompt/skill/hook/workflow 变更 | `omubot-continuity` + `omubot-deep-delivery` |
| 角色包/数据录入 | `omubot-continuity` + `omubot-deep-delivery` |
| 小修小补 | 不强制 continuity；用 `update_plan` 即可 |

---

## 7. Hook / Script 设计

### 7.1 Codex 当前可落地的低噪声版

当前 `.codex/hooks.json` 已有：

- `SessionStart`
- `PostToolUse`

建议先做：

1. 扩展 `scripts/dev/codex-session-start.py`：
   - 读取 `.workspace/agent-session-state.md`。
   - 读取 `docs/tracking/ACTIVE.md`。
   - 如果 ACTIVE 有 tracker，读取 tracker 顶部约 80 行。
   - 输出 “请从 tracker next_step 继续，不要重新审计全仓”。

2. 保留现有 `PostToolUse` maintenance-log 提醒。

3. 可选增加一个轻量 PostToolUse 提醒：
   - 当编辑 `services/`、`plugins/`、`kernel/`、`admin/`、`.agents/`、`.codex/`、`.claude/`、`docs/tracking/` 时，如果 `docs/tracking/ACTIVE.md` 存在 active tracker，则提醒“如本次改动改变 next step / decisions / verification，请更新 tracker”。
   - 不要每次自动写 tracker，避免噪音和误写。

### 7.2 Claude Code 强化版

如果要给 Claude Code 做强 continuity，可借鉴 `claude-code-handoff`：

- `SessionStart`：读取 `.workspace/agent-session-state.md`、ACTIVE、active tracker。
- `PreCompact`：压缩前重新输出 live state + ACTIVE + tracker 顶部，并提醒写 state。
- `PostToolUse`：记录编辑过的文件到 live state。
- `Stop`：final 前提醒检查 tracker、ACTIVE 和 `maintenance-log.md` 是否需要收口。
- 不建议一开始启用 `UserPromptSubmit` 每轮注入；先看噪音。

### 7.3 为什么不直接 submodule `claude-code-handoff`

不直接引入的原因：

- 它以 `.claude/` 为中心，Omubot 需要 Codex/Claude 双兼容。
- 它的状态文件是 session-specific，Omubot 的长期任务需要 `docs/tracking` 进入项目文档。
- 它的脚本/模板需要改造成 D1-D7、NapCat 红线、maintenance-log 规则。

可借鉴而不照搬。

---

## 8. AGENTS.md 修改建议

只加短规则，不放长模板：

```markdown
## Continuity Rule

For long-running, resumed, or compaction-sensitive work, use the
`omubot-continuity` skill. First read `.workspace/agent-session-state.md` if it
exists, then `docs/tracking/ACTIVE.md`, then the active tracker named there, then
`git status --short`. Continue from the tracker `next_step` instead of
rediscovering the repository from scratch.

Create or update an active tracker for work that spans sessions, touches 3+
files, involves production/runtime/skills/hooks/prompts, or requires a bug test
ledger. Keep `maintenance-log.md` for durable completed changes, not live todo.
```

---

## 9. Rollout Plan

### Phase 0 — 本方案建档

交付：

- `docs/tracking/agent-continuity-omubot-plan-2026-06-06.md`
- `maintenance-log.md` 顶部记录方案建档

验证：

```bash
git diff --check -- docs/tracking/agent-continuity-omubot-plan-2026-06-06.md maintenance-log.md
```

状态：本文件即 Phase 0。

### Phase 1 — Active Tracker MVP

交付：

- 新增 `docs/tracking/ACTIVE.md`
- 新增 `docs/tracking/agent-continuity-template.md`
- 更新 `AGENTS.md` Continuity Rule

验收：

```bash
test -f docs/tracking/ACTIVE.md
test -f docs/tracking/agent-continuity-template.md
rg -n "Continuity Rule|omubot-continuity|ACTIVE.md" AGENTS.md docs/tracking/ACTIVE.md
```

回滚：

- 删除新增文件；
- 从 `AGENTS.md` 移除 Continuity Rule。

### Phase 2 — SessionStart 注入

交付：

- 扩展 `scripts/dev/codex-session-start.py`
- 可选新增脚本函数，限制 tracker 注入行数

验收：

```bash
python3 scripts/dev/codex-session-start.py | python3 -m json.tool >/tmp/omubot-session-start.json
rg -n "ACTIVE|Next Session|agent-session-state|tracking" /tmp/omubot-session-start.json
```

语义验收：

- ACTIVE 指向 tracker 时，输出包含 tracker 标题和 next step。
- ACTIVE 为 none 时，不输出虚假活跃任务。
- tracker 不存在时，输出明确 warning，不报错。

回滚：

- 恢复 `scripts/dev/codex-session-start.py`。

### Phase 3 — `omubot-continuity` skill

交付：

- `.agents/skills/omubot-continuity/SKILL.md`
- `.claude/skills/omubot-continuity/SKILL.md`
- 必要时同步全局 Codex skill

验收：

```bash
test -f .agents/skills/omubot-continuity/SKILL.md
test -f .claude/skills/omubot-continuity/SKILL.md
rg -n "continue|接着|压缩|ACTIVE.md|Test Ledger" .agents/skills/omubot-continuity/SKILL.md
```

行为验收：

- 新会话只说“继续”，agent 应读取 ACTIVE/tracker，而不是重新读 wiki。
- 用户说“防止失忆”，agent 应建/更新 tracker，而不是只聊天总结。

回滚：

- 删除 skill 文件；
- 移除 AGENTS 引用。

### Phase 4 — PostToolUse 轻提醒

交付：

- `.codex/hooks.json` 增加 active tracker 更新提醒，或扩展现有提醒文本。

验收：

```bash
python3 -m json.tool .codex/hooks.json >/tmp/hooks.json
rg -n "ACTIVE|tracker|continuity|maintenance-log" .codex/hooks.json
```

风险：

- hook 过吵会扰乱对话。

回滚：

- 删除新增 hook 或恢复旧 `.codex/hooks.json`。

### Phase 5 — Claude Code PreCompact / Stop 强化（可选）

交付：

- `.claude/hooks/session-start.sh`
- `.claude/hooks/pre-compact.sh`
- `.claude/hooks/stop.sh`
- `.claude/settings.json` 或插件化 hook 配置

注意：

- 当前 `.gitignore` 忽略 `.claude/*`，需要明确哪些 hook/commands 要纳入版本控制。
- 这一步只在用户确认 Claude Code 侧也要同等支持后做。

---

## 10. 验收基准

### 10.1 冷恢复基准

准备：

1. ACTIVE 指向一个测试 tracker。
2. tracker 里写清 next step、files touched、verification。
3. 清空聊天上下文或开新会话。

输入：

```text
继续
```

合格输出：

- 说明从 ACTIVE/tracker 恢复。
- 复述 objective / status / next step。
- 指出需要检查的文件。
- 不默认从 wiki 全量开始。

失败表现：

- 先读 `docs/wiki/Home.md`、`docs/architecture.md` 一长串。
- 不知道当前任务是什么。
- 重复已经在 tracker 标记为 Do not redo 的实验。

### 10.2 Bug Ledger 基准

准备：

tracker 中 `mode=bug`，Test Ledger 有：

```markdown
### T1 — reproduce visual truncation — FAIL
- Command: `...`
- Result: exact key line
- Conclusion: ruled out router cap
```

输入：

```text
继续查这个 bug
```

合格输出：

- 先读 Test Ledger。
- 明确 T1 已排除，不重复跑。
- 提出下一条未验证实验。

### 10.3 Hook 注入基准

运行：

```bash
python3 scripts/dev/codex-session-start.py | python3 -m json.tool
```

合格：

- JSON 可解析。
- active tracker 存在时输出摘要。
- 缺文件时输出 warning 而不是 traceback。

### 10.4 低噪声基准

在一个小任务中只改一行文档：

- 不应强制创建 tracker。
- 不应要求写 maintenance-log。
- 不应在每轮输出过多 continuity 旁白。

---

## 11. 风险与防护

| 风险 | 表现 | 防护 |
| --- | --- | --- |
| 过度记录 | 每句话都改 docs，git 噪音巨大 | live state 放 `.workspace/`；docs/tracking 只在重大节点更新 |
| maintenance-log 滥用 | 变成 todo 和流水账 | 只记 durable completed changes |
| tracker 过长 | 恢复时仍要读 1000 行 | 顶部 `Next Session Starts Here` 必须自洽；历史下沉 |
| secrets 泄漏 | 把 token、群消息原文写入 docs | 明确禁止 secrets；敏感内容只写摘要 |
| hook 误导 agent | 旧 ACTIVE 指向已完成任务 | `mode=none` 或 done 时必须清 active；SessionStart 对 stale tracker warning |
| Codex/Claude 分歧 | `.claude` 可用，Codex 不读 | canonical 放 `docs/tracking` + `.workspace`，`.claude` 只做增强 |
| 用户不想写文件 | 小任务被流程绑架 | 设 tracker threshold，小任务只用 `update_plan` |

---

## 12. 不做项

短期不做：

- 不把外部 `claude-code-handoff` 作为 submodule 直接引入。
- 不把所有状态写进 `AGENTS.md`。
- 不在每个用户 prompt 都强制写 git-tracked tracker。
- 不把 `maintenance-log.md` 改成 active todo。
- 不强制所有任务都使用 tracker。
- 不为了 continuity 引入 Python 以外新依赖。

---

## 13. 未来可选增强

1. 增加 `scripts/dev/agent-continuity-check.py`
   - 检查 ACTIVE 指向文件是否存在。
   - 检查 tracker 是否有 next step。
   - 检查 done tracker 是否还被 ACTIVE 引用。

2. 增加 `/handoff` 类 command（Claude Code 专用）
   - Context / Task / Bug / Clean 四模式。
   - 写入 `docs/tracking`，不是 `.claude`。

3. 增加 forward-test
   - 用三个真实 Omubot 历史任务模拟“新 agent 只收到继续”。
   - 比较 baseline vs continuity skill 的恢复准确度。

4. 增加 hook 噪音评分
   - 连续 20 轮普通任务中，hook 提醒不超过 3 次。

---

## 14. 给下一位 agent 的执行提示

如果你在压缩后或新会话读到本文：

1. 不要从头重新评估“是否需要 continuity”。这个方案已经基于外部官方 skills、Claude Code plugins、社区 handoff 和 Omubot 当前资产做过一次分析。
2. 先执行 Phase 1：`ACTIVE.md` + tracker 模板 + AGENTS 短规则。
3. 再执行 Phase 2：扩展 `codex-session-start.py`。
4. 除非用户明确要求，不要直接上 Claude Code `PreCompact/UserPromptSubmit` 强 hook。
5. 如果要实现 skill，保持 `SKILL.md` 短小，勿把本文复制进去。
6. 每个 phase 都要写验证和回滚，流程变更完成后更新 `maintenance-log.md`。

---

## 15. 本方案引用的本仓文件

- `AGENTS.md`
- `.codex/hooks.json`
- `scripts/dev/codex-session-start.py`
- `.claude/handoff/README.md`
- `.claude/handoff/TEMPLATE.md`
- `docs/tracking/`
- `maintenance-log.md`
